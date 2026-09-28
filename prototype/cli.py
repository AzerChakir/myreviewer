"""Local CLI harness for the PR decomposer.

Run a full decomposition over a diff and print the report as markdown or JSON,
with no GitHub access required.

Examples
--------
    python cli.py --diff example/mixed_concern.diff --title "Mixed PR"   # real LLM
    python cli.py --diff example/mixed_concern.diff --mock              # dev/test only
    python cli.py --repo /path/to/repo --base main --head feature-branch
    python cli.py --owner <org> --repo <name> --pr 42          # needs GITHUB_TOKEN
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from openai import APIStatusError

from pr_decomposer import run_pipeline, ConfigError
from pr_decomposer.config import load_config, PROTOTYPE_ROOT
from github_client import (
    DiffSourceError,
    GithubApiDiffSource,
    LocalDiffSource,
)


def _print_api_error(exc: Exception) -> None:
    if isinstance(exc, APIStatusError):
        code = exc.status_code
        hint = ""
        if code == 401:
            hint = " (check NIM_API_KEY)"
        elif code == 410:
            hint = " (model retired on NIM — set NIM_MODEL in .env to a hosted model, see .env.example)"
        elif code == 429:
            hint = " (rate limited — retry in a moment)"
        print(f"llm error: HTTP {code}{hint}\n  detail: {exc}\n"
              "  tip: set NIM_API_KEY in .env for real analysis, or rerun "
              "with --mock to exercise the pipeline without the LLM.",
              file=sys.stderr)
    else:
        print(f"error: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description="Analyze a pull request diff and recommend reviewer-friendly decomposition.",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--diff", metavar="FILE", help="path to a unified diff file")
    source.add_argument("--repo", metavar="DIR", help="local git repository")
    source.add_argument("--owner", metavar="ORG", help="GitHub owner (with --repo as repo name, --pr)")
    source.add_argument("--pr", metavar="N", help="GitHub PR number (requires --owner and --repo)")
    parser.add_argument("--base", default="main", help="base ref for --repo (default: main)")
    parser.add_argument("--head", default="HEAD", help="head ref for --repo (default: HEAD)")
    parser.add_argument("--title", default="", help="override PR title")
    parser.add_argument("--description", default="", help="override PR description")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    parser.add_argument("--model", default=None, help="override NIM_MODEL from .env")
    parser.add_argument("--base-url", default=None, help="override NIM_BASE_URL")
    parser.add_argument("--api-key", default=None, help="override NIM_API_KEY (avoid; use .env)")
    parser.add_argument("--effort", choices=["standard", "deep"], default="deep",
                        help="deep (default) adds a verification pass over the "
                             "bug/requirement review; standard skips it")
    parser.add_argument("--requirements", default="", metavar="TEXT",
                        help="declared project requirements used for compatibility checks")
    parser.add_argument("--requirements-file", default=None, metavar="FILE",
                        help="read declared requirements from a file")
    parser.add_argument("--mock", action="store_true",
                        help="dev/testing only: use the deterministic mock model (no LLM calls)")
    args = parser.parse_args(argv)

    config = load_config(
        PROTOTYPE_ROOT / ".env",
        overrides={"NIM_MODEL": args.model, "NIM_BASE_URL": args.base_url,
                   "NIM_API_KEY": args.api_key},
    )

    requirements = args.requirements.strip()
    if args.requirements_file:
        req_path = Path(args.requirements_file)
        if not req_path.is_absolute():
            req_path = PROTOTYPE_ROOT / req_path
        try:
            requirements = (requirements + "\n" + req_path.read_text(encoding="utf-8")).strip()
        except OSError as exc:
            print(f"error: cannot read requirements file: {exc}", file=sys.stderr)
            return 1

    if args.diff:
        source_impl = LocalDiffSource(diff_file=args.diff, title=args.title,
                                      description=args.description)
    elif args.repo:
        source_impl = LocalDiffSource(repo_dir=args.repo, base=args.base,
                                      head=args.head, title=args.title,
                                      description=args.description)
    elif args.owner and args.pr:
        source_impl = GithubApiDiffSource(
            token=config.github_token or "", owner=args.owner,
            repo=args.repo, pr_number=int(args.pr),
        )
    else:
        parser.error("provide --diff, --repo, or (--owner --repo --pr)")

    try:
        diff = source_impl.fetch()
        report = run_pipeline(diff, config=config, mock=args.mock,
                              model=args.model, effort=args.effort,
                              requirements=requirements)
    except (ConfigError, DiffSourceError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        _print_api_error(exc)
        return 1

    if args.format == "json":
        print(report.to_json())
    else:
        print(report.plan_markdown)
    if args.mock:
        print("\n[note] mock model used (no LLM calls).", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())