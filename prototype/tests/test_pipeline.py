"""Offline unit tests using the deterministic mock model.

Never touches GitHub or the LLM API — runs in a fraction of a second. For the
real end-to-end tests (NIM + GitHub), see tests/test_live.py.
"""

import os
import sys
import unittest
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from github_client import LocalDiffSource, parse_unified_diff  # noqa: E402
from pr_decomposer import run_pipeline  # noqa: E402
from pr_decomposer.bug_review import parse_bug_review  # noqa: E402
from pr_decomposer.config import load_config  # noqa: E402


class TestPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.diff = LocalDiffSource(os.path.join(ROOT, "example", "mixed_concern.diff")).fetch()
        cls.config = load_config(Path(ROOT) / ".env")
        cls.report = run_pipeline(cls.diff, config=cls.config, mock=True)

    def test_change_log_present(self):
        report = self.report
        self.assertGreater(len(report.change_log), 0, "mock change log should not be empty")
        for entry in report.change_log:
            self.assertTrue(entry.area)
            self.assertTrue(entry.old_code)
            self.assertTrue(entry.new_code)
            self.assertTrue(entry.impact)
            self.assertIsInstance(entry.files, list)

    def test_post_review_present(self):
        report = self.report
        self.assertGreater(len(report.post_review), 0, "mock post-review should not be empty")
        severities = {f.severity for f in report.post_review}
        self.assertTrue(severities, "findings must carry a severity")
        self.assertFalse(report.post_review_verdict.strip() == "", "verdict should be non-empty")

    def test_post_review_sorted(self):
        order = {"critical": 0, "important": 1, "minor": 2, "nit": 3}
        report = self.report
        keys = [order.get(f.severity, 9) for f in report.post_review]
        self.assertEqual(keys, sorted(keys), "findings must be severity-sorted")

    def test_concerns_still_parse(self):
        report = self.report
        self.assertGreater(len(report.concerns), 0, "concerns must survive the new stages")
        title = {c.title for c in report.concerns}
        self.assertIn("fix slow orders query", title)

    def test_bug_review_present(self):
        report = self.report
        self.assertEqual(len(report.bug_findings), 2, "mock bug_review should yield 2 findings")
        first = report.bug_findings[0]
        self.assertEqual(first.severity, "critical")
        self.assertEqual(first.bug_type, "error-handling")
        self.assertTrue(first.title)
        self.assertTrue(first.fix, "findings must carry a suggested fix")
        for finding in report.bug_findings:
            self.assertTrue(finding.title)
            self.assertTrue(finding.location)

    def test_requirements_checks_present(self):
        report = self.report
        self.assertEqual(len(report.requirements_checks), 3, "mock requirements checks")
        statuses = {c.status for c in report.requirements_checks}
        self.assertEqual(statuses, {"satisfied", "unverified"},
                         "mock covers satisfied and unverified statuses")
        self.assertTrue(report.requirements_verdict.strip(), "requirements verdict expected")
        self.assertEqual(report.merge_readiness, "fix-before-merge",
                         "mock MERGE_READINESS should parse")

    def test_bug_findings_sorted_by_severity(self):
        order = {"critical": 0, "important": 1, "minor": 2, "nit": 3}
        keys = [order.get(b.severity, 9) for b in self.report.bug_findings]
        self.assertEqual(keys, sorted(keys), "bug findings must be severity-sorted")

    def test_effort_and_model_reported(self):
        report = self.report
        self.assertEqual(report.effort, "deep")
        self.assertEqual(report.model, "mock")
        self.assertIn("Decision for the reviewer", report.plan_markdown)
        self.assertIn("Merge readiness (advisory)", report.plan_markdown)

    def test_to_dict_contains_new_keys(self):
        keys = set(self.report.to_dict().keys())
        for key in ("change_log", "post_review", "post_review_verdict",
                    "bug_findings", "requirements_checks", "requirements_verdict",
                    "merge_readiness", "effort"):
            self.assertIn(key, keys)

    def test_parse_bug_review_packed_fields(self):
        """NIM models often pack several KEY: value fields per line."""
        text = (
            "BUGS:\n"
            "BUG 1: SEVERITY: minor TYPE: edge-case TITLE: Range not validated "
            "LOCATION: cart/pricing.py:8 DETAIL: no bounds check "
            "FIX: raise ValueError\n"
            "REQUIREMENTS:\n"
            "REQ 1: REQUIREMENT: Exact to the cent STATUS: satisfied "
            "EVIDENCE: integer cents\n"
            "REQUIREMENTS_VERDICT: Looks good overall.\n"
            "MERGE_READINESS: ready\n"
        )
        result = parse_bug_review(text)
        self.assertEqual(len(result.findings), 1)
        f = result.findings[0]
        self.assertEqual(f.severity, "minor")
        self.assertEqual(f.bug_type, "edge-case")
        self.assertEqual(f.title, "Range not validated")
        self.assertTrue(f.detail and f.fix)
        self.assertEqual(result.checks[0].status, "satisfied")
        self.assertEqual(result.merge_readiness, "ready")
        self.assertTrue(result.requirements_verdict)

    def test_diff_source_parses(self):
        files = parse_unified_diff(
            (Path(ROOT) / "example" / "mixed_concern.diff").read_text(encoding="utf-8")
        )
        self.assertGreater(len(files), 0)
        self.assertTrue(all(f.display_path for f in files))


if __name__ == "__main__":
    unittest.main(verbosity=2)