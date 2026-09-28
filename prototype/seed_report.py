"""Seed a demo report into the dashboard store from the example diff.

By default this runs the full pipeline against the real configured LLM (NIM)
so the dashboard demo shows a genuine analysis. Pass `--mock` to instead use
the deterministic mock model (offline/dev only).
"""

import argparse
import sys
from datetime import datetime, timezone

from github_client import GitDiff, parse_unified_diff
from pr_decomposer import run_pipeline
from pr_decomposer.config import PROTOTYPE_ROOT, load_config
from pr_decomposer.store import save_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mock", action="store_true",
                        help="dev/testing only: use the deterministic mock model")
    parser.add_argument("--model", default=None, help="override the configured NIM model")
    parser.add_argument("--effort", choices=["standard", "deep"], default="deep")
    args = parser.parse_args()

    config = load_config(PROTOTYPE_ROOT / ".env")

    diff_text = (PROTOTYPE_ROOT / "example" / "mixed_concern.diff").read_text(encoding="utf-8")
    files = parse_unified_diff(diff_text)
    diff = GitDiff(
        repo="example/demo",
        title="Mixed PR: avatars, orders speedup, refactor",
        pr_number=17,
        files=files,
    )
    report = run_pipeline(diff, config=config, mock=args.mock,
                          model=args.model, effort=args.effort)

    payload = report.to_dict()
    payload["report_id"] = "demo-mixed-concerns"
    payload["repo"] = "example/demo"
    payload["pr_number"] = 17
    payload["analyzed_at_iso"] = datetime.now(timezone.utc).isoformat()
    payload["requested_mock"] = args.mock

    report_id = save_report(payload, report_id="demo-mixed-concerns")
    mode = "mock" if args.mock else f"real ({report.model})"
    print(f"{report_id} — seeded with {mode}")
    return 0


if __name__ == "__main__":
    sys.exit(main())