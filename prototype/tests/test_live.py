"""Live integration tests: the full pipeline against the REAL configured
NIM LLM and the REAL GitHub API.

These tests make actual network calls and consume the API key in `.env`, so
they are skipped automatically when either key is missing (run in <1s), and
take ~1-2 minutes otherwise.

Run:

    .venv/bin/python -m unittest tests.test_live -v
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from github_client import GithubApiDiffSource, prepare_comment_body  # noqa: E402
from pr_decomposer import run_pipeline  # noqa: E402
from pr_decomposer.config import load_config  # noqa: E402

REAL_PR = ("octocat", "Hello-World", 1)


class TestLive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_config(Path(ROOT) / ".env")
        if not cls.config.has_api_key or not cls.config.github_token:
            raise unittest.SkipTest(
                "live tests need NIM_API_KEY and GITHUB_TOKEN in prototype/.env")
        owner, repo, pr = REAL_PR
        cls.diff = GithubApiDiffSource(
            cls.config.github_token, owner, repo, pr).fetch()

    def test_diff_fetched_from_github(self):
        self.assertGreater(len(self.diff.files), 0, "expected at least one changed file")
        self.assertGreater(self.diff.added + self.diff.deleted, 0, "expected a non-empty diff")

    def test_real_pipeline_runs(self):
        report = run_pipeline(self.diff, config=self.config, mock=False, effort="standard")
        self.assertNotEqual(report.model, "mock", "model should be the real NIM model")
        self.assertTrue(report.pr_summary.strip(), "summary must be non-empty")
        self.assertTrue(report.verdict.strip(), "verdict must be non-empty")
        self.assertGreater(len(report.concerns), 0, "concerns must be non-empty")
        self.assertGreater(len(report.change_log), 0, "AI change log must be non-empty")
        for entry in report.change_log:
            self.assertTrue(entry.area)
        self.assertGreater(len(report.post_review), 0, "post-review findings must be non-empty")
        self.assertTrue(report.post_review_verdict.strip(),
                        "post-review verdict must be non-empty")

    def test_bug_and_requirement_review_runs(self):
        report = run_pipeline(self.diff, config=self.config, mock=False, effort="standard")
        self.assertIsInstance(report.bug_findings, list)
        self.assertIsInstance(report.requirements_checks, list)
        self.assertIn(report.merge_readiness, ("", "ready", "fix-before-merge", "rework"))
        for finding in report.bug_findings:
            self.assertTrue(finding.title)
            self.assertIn(finding.severity, ("critical", "important", "minor", "nit"))
            self.assertTrue((finding.detail or finding.fix),
                            "findings should carry detail and/or a suggested fix")
        allowed = {"satisfied", "partial", "unmet", "unverified"}
        for check in report.requirements_checks:
            self.assertIn(check.status, allowed)

    def test_comment_body_renders(self):
        report = run_pipeline(self.diff, config=self.config, mock=False, effort="standard")
        body = prepare_comment_body(report)
        self.assertTrue(body.startswith("<!-- PR-DECOMPOSER-V1 -->"))
        self.assertIn("AI change summary", body)
        self.assertIn("Review of the new code", body)
        self.assertIn("Verdict:", body)
        if report.bug_findings:
            self.assertIn("Bugs & risks", body)
        if report.requirements_checks:
            self.assertIn("Requirements compatibility (advisory)", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)