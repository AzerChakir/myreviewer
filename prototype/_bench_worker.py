"""Benchmark worker: run one (sample, model) pipeline and emit a JSON outcome.

Spawned by benchmark_models.py via subprocess so a hung LLM request can be
hard-killed with `timeout` instead of leaking a stuck thread inside the parent.

Usage: python _bench_worker.py '<sample json>' <model> <effort>
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from github_client import GitDiff, parse_unified_diff  # noqa: E402
from pr_decomposer import run_pipeline  # noqa: E402
from pr_decomposer.config import PROTOTYPE_ROOT, load_config  # noqa: E402

WORKER_PATH = str(Path(__file__).resolve())


def _match_bug(finding, expected) -> bool:
    title = (finding.title or "").lower()
    location = (finding.location or "") or ""
    if any(kw in title for kw in expected.get("keywords", [])):
        return True
    if expected.get("path") and expected["path"] in location:
        ftype = (finding.bug_type or "").lower()
        etype = (expected.get("type") or "").lower()
        if etype and (etype in ftype or ftype in etype):
            return True
    return False


def _score_bugs(findings, expected_bugs) -> dict:
    matched = [False] * len(expected_bugs)
    true_positive = 0
    for finding in findings:
        for i, expected in enumerate(expected_bugs):
            if not matched[i] and _match_bug(finding, expected):
                matched[i] = True
                true_positive += 1
                break
    detected = sum(1 for m in matched if m)
    total = len(expected_bugs) or 1
    return {
        "expected": len(expected_bugs),
        "detected": detected,
        "total": len(findings),
        "recall": detected / total,
        "precision": (true_positive / len(findings)) if findings else 1.0,
    }


def main() -> int:
    _, sample_json, model, effort = sys.argv[:4]
    sample = json.loads(sample_json)
    config = load_config(PROTOTYPE_ROOT / ".env")
    started = time.monotonic()
    try:
        diff = GitDiff(
            title=sample["title"] or sample["label"],
            files=parse_unified_diff(sample["diff_text"]),
            raw=sample["diff_text"],
        )
        report = run_pipeline(
            diff, config=config, mock=False, model=model,
            effort=effort, requirements=sample["requirements"],
        )
        payload = report.to_dict()
        outcome = {
            "ok": True,
            "seconds": round(time.monotonic() - started, 2),
            "stages": {
                "summary": bool(payload["summary"]),
                "concerns": len(payload["concerns"]) > 0,
                "change_log": len(payload["change_log"]) > 0,
                "post_review": len(payload["post_review"]) > 0,
                "bug_parse": len(payload["bug_findings"]) > 0 or bool(payload["requirements_checks"]),
                "readiness": bool(payload["merge_readiness"]),
                "req_checks": len(payload["requirements_checks"]) > 0,
            },
            "scores": _score_bugs(report.bug_findings, sample["expected_bugs"]),
            "report": payload,
        }
    except Exception as exc:  # noqa: BLE001
        outcome = {
            "ok": False,
            "seconds": round(time.monotonic() - started, 2),
            "error": str(exc)[:300],
            "stages": {},
            "scores": {"expected": len(sample.get("expected_bugs", [])), "detected": 0,
                       "total": 0, "recall": 0.0, "precision": 0.0},
            "report": None,
        }
    json.dump(outcome, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())