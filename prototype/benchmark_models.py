"""Multi-model benchmark for the PR Decomposer review pipeline.

Runs a matrix of (model x sample) through the full pipeline and produces a
comparison report so you can pick the best model for the SWE-grade review.
Models default to the configured default plus `--models`; samples come from
the synthetic fixtures under `fixtures/` (real, known bugs → precision/recall)
and, optionally, real GitHub PRs via `--prs owner/repo#n`.

Runs the matrix sequentially, each (model x sample) in a **subprocess** so a
hung LLM request is hard-killed by `--timeout` instead of leaking a stuck
thread inside the parent.

Examples (run from prototype/):

    ../.venv/bin/python benchmark_models.py --effort standard
    ../.venv/bin/python benchmark_models.py --models nvidia/nemotron-3-super-120b-a12b
    ../.venv/bin/python benchmark_models.py --prs octocat/Hello-World#1

Output: data/benchmarks/<timestamp>/report.md (human) + results.json (machine).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _bench_worker import WORKER_PATH  # noqa: E402
from pr_decomposer.config import PROTOTYPE_ROOT, load_config  # noqa: E402
from pr_decomposer.models import available_models  # noqa: E402
from pr_decomposer.repo_context import compose_requirements, fetch_repo_requirements  # noqa: E402


def _load_fixture_samples(fixtures_dir: Path) -> list[dict]:
    samples = []
    for path in sorted(fixtures_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        samples.append({
            "kind": "fixture",
            "label": f"[fixture] {data['name']}",
            "title": data["title"],
            "diff_text": data["diff"],
            "requirements": "\n".join(data.get("requirements", [])) or "",
            "expected_bugs": data.get("expected_bugs", []),
        })
    return samples


def _load_real_pr_samples(prs: list[str], config) -> list[dict]:
    samples = []
    for ref in prs:
        parts = ref.split("#")
        if len(parts) != 2 or not parts[1].isdigit() or "/" not in parts[0]:
            print(f"skip malformed PR ref: {ref} (expected owner/repo#n)", file=sys.stderr)
            continue
        owner, repo = parts[0].rsplit("/", 1)
        source = GithubApiDiffSource(config.github_token, owner, repo, int(parts[1]))
        diff = source.fetch()
        repo_block = fetch_repo_requirements(owner, repo, config.github_token)
        samples.append({
            "kind": "real",
            "label": f"[real]   {owner}/{repo}#{parts[1]}",
            "title": diff.title or ref,
            "diff_text": diff.to_compact(),
            "requirements": compose_requirements(repo_block=repo_block, manual=""),
            "expected_bugs": [],
        })
    return samples


def _run_one(sample: dict, model: str, effort: str, timeout: int) -> dict:
    """Run one (sample, model) in a subprocess; killed if it exceeds `timeout`."""
    import time

    started = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, WORKER_PATH, json.dumps(sample), model, effort],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False, "seconds": timeout, "error": "killed after timeout",
            "stages": {}, "scores": {"expected": len(sample["expected_bugs"]),
                                     "detected": 0, "total": 0,
                                     "recall": 0.0, "precision": 0.0},
            "report": None,
        }
    elapsed = round(time.monotonic() - started, 2)
    if proc.returncode != 0:
        return {
            "ok": False, "seconds": elapsed, "error": "worker crash",
            "stderr": (proc.stderr or "").strip()[-500:],
            "stages": {}, "scores": {"expected": len(sample["expected_bugs"]),
                                     "detected": 0, "total": 0,
                                     "recall": 0.0, "precision": 0.0},
            "report": None,
        }
    try:
        outcome = json.loads(proc.stdout or "")
    except json.JSONDecodeError:
        return {
            "ok": False, "seconds": elapsed, "error": "worker output not JSON",
            "stderr": (proc.stderr or "").strip()[-500:],
            "stages": {}, "scores": {"expected": len(sample["expected_bugs"]),
                                     "detected": 0, "total": 0,
                                     "recall": 0.0, "precision": 0.0},
            "report": None,
        }
    outcome["seconds"] = max(outcome.get("seconds", elapsed), 0.0)
    return outcome


def _main_loop(models, samples, effort, timeout) -> dict[str, list[dict]]:
    results: dict[str, list[dict]] = {}
    for model in models:
        print(f"model {model}:", flush=True)
        per_model = []
        for sample in samples:
            outcome = _run_one(sample, model, effort, timeout)
            status = "ok" if outcome["ok"] else f"FAIL {(outcome.get('error', ''))[:80]}"
            print(f"  {sample['label'].ljust(34)} {status.ljust(40)} {outcome['seconds']}s",
                  flush=True)
            per_model.append({"sample": sample["label"], **outcome})
        results[model] = per_model
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", default="",
                        help="comma-separated model ids (default: configured NIM_MODEL)")
    parser.add_argument("--fixtures-dir", default="fixtures")
    parser.add_argument("--prs", default="", help="comma-separated owner/repo#pr real PRs")
    parser.add_argument("--effort", choices=["standard", "deep"], default="deep")
    parser.add_argument("--out", default="", help="output dir (default data/benchmarks/<ts>)")
    parser.add_argument("--timeout", type=int, default=240,
                        help="per (model, sample) timeout in seconds")
    args = parser.parse_args(argv)

    config = load_config(PROTOTYPE_ROOT / ".env")
    if not config.has_api_key:
        print("error: NIM_API_KEY required for the benchmark", file=sys.stderr)
        return 1

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    if not models:
        known = available_models(config)
        models = [known[0]["id"]] if known else [config.model]

    samples = _load_fixture_samples(Path(args.fixtures_dir))
    if args.prs.strip():
        if not config.github_token:
            print("error: --prs needs GITHUB_TOKEN", file=sys.stderr)
            return 1
        samples.extend(_load_real_pr_samples(
            [p.strip() for p in args.prs.split(",") if p.strip()], config))

    if not samples:
        print("error: no samples found; run scripts/build_fixtures.py first "
              "or pass --prs", file=sys.stderr)
        return 1

    out_dir = Path(args.out) if args.out else (
        PROTOTYPE_ROOT / "data" / "benchmarks" /
        datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"))
    out_dir.mkdir(parents=True, exist_ok=True)

    results = _main_loop(models, samples, args.effort, args.timeout)

    _write_report(out_dir, results, models, samples, args.effort)
    (out_dir / "results.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nwrote report -> {out_dir / 'report.md'}")
    return 0


def _aggregate(model: str, results: dict[str, list[dict]]) -> dict:
    runs = results.get(model, [])
    total = len(runs)
    ok = [r for r in runs if r["ok"]]
    times = [r["seconds"] for r in runs]
    # Bug metrics only make sense on labeled (synthetic) samples.
    labeled = [r for r in ok if r["scores"]["expected"] > 0]
    recall = (sum(r["scores"]["recall"] for r in labeled) / (len(labeled) or 1)
              if labeled else None)
    precision = (sum(r["scores"]["precision"] for r in labeled) / (len(labeled) or 1)
                 if labeled else None)
    detected = sum(r["scores"]["detected"] for r in labeled)
    expected = sum(r["scores"]["expected"] for r in labeled)
    stage_success = {name: 0 for name in ("summary", "concerns", "change_log",
                                          "post_review", "bug_parse", "readiness",
                                          "req_checks")}
    for r in ok:
        for name, value in r["stages"].items():
            if value:
                stage_success[name] += 1
    return {
        "model": model,
        "runs": total,
        "ok": len(ok),
        "avg_s": round(sum(times) / (len(times) or 1), 2),
        "max_s": round(max(times) if times else 0, 2),
        "stage_success": stage_success,
        "bug_recall": None if recall is None else round(recall, 3),
        "bug_precision": None if precision is None else round(precision, 3),
        "bugs_detected": detected,
        "bugs_expected": expected,
    }


def _write_report(out_dir, results, models, samples, effort) -> None:
    rows = [_aggregate(m, results) for m in models]
    lines = [
        "# Model benchmark — PR Decomposer",
        f"\nRan **{len(samples)}** samples "
        f"(fixtures+real) across **{len(models)}** model(s), effort=`{effort}`.",
        f"Generated {datetime.now(timezone.utc).isoformat()}.\n",
        "| model | ok | avg s | max s | summary | concerns | change_log | post_review | bug_parse | readiness | req_checks | bug recall | bug precision | detected/expected |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    stage_cols = ("summary", "concerns", "change_log", "post_review", "bug_parse", "readiness", "req_checks")
    for row in sorted(rows, key=lambda r: (-(r["bug_recall"] if r["bug_recall"] is not None else -1.0),
                                        -(r["bug_precision"] if r["bug_precision"] is not None else -1.0),
                                        r["avg_s"])):
        cells = [
            f"`{row['model']}`",
            f"{row['ok']}/{row['runs']}",
            str(row["avg_s"]), str(row["max_s"]),
        ] + [f"{row['stage_success'][c]}/{row['runs']}" for c in stage_cols] + [
            f"{row['bug_recall']:.2f}" if row["bug_recall"] is not None else "n/a",
            f"{row['bug_precision']:.2f}" if row["bug_precision"] is not None else "n/a",
            f"{row['bugs_detected']}/{row['bugs_expected']}",
        ]
        lines.append("| " + " | ".join(cells) + " |")

    lines.append("\n## Notes\n")
    lines.append("- **bug recall** = injected/synthetic bugs the model found; "
                 "**bug precision** = share of its findings that match an injected bug "
                 "(real PR samples have no labels and are excluded from bug metrics).")
    lines.append("- Synthetic fixtures: cart-discount-rounding, auth-token-rotation, "
                 "webhook-delivery-retry — each contains known bugs.")
    lines.append("- Same-structured prompts, temperature 0.0. NIM free tier rate-limits "
                 "(client retries); sequential runs keep the benchmark gentle.")

    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())