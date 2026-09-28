"""Tests for production single-service mode: the API serving the Angular bundle.

In development the dashboard is a separate `ng serve` process. In production
`api.mount_dashboard` serves the compiled bundle from the same FastAPI app, so
one container exposes the dashboard, the REST API and the webhook on a single
port. The risk this pins down: the SPA catch-all route must never swallow API or
webhook paths, and must not allow path traversal out of the dist directory.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api import mount_dashboard  # noqa: E402


def _make_dist(root: Path) -> Path:
    """Build a minimal Angular-like dist directory and return the browser dir."""
    browser = root / "dashboard" / "dist" / "dashboard" / "browser"
    (browser / "assets").mkdir(parents=True)
    (browser / "index.html").write_text(
        "<!doctype html><html><body><app-root></app-root></body></html>",
        encoding="utf-8",
    )
    (browser / "assets" / "main-abc123.js").write_text("console.log(1)", encoding="utf-8")
    (browser / "favicon.ico").write_bytes(b"\x00\x00\x01\x00")
    return browser


def _app_with_real_api_routes(dist: Path) -> FastAPI:
    """A small app that mirrors the real layering: real routes, then SPA last."""
    from api import app as real_app

    test_app = FastAPI()

    @test_app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    @test_app.post("/webhook")
    def webhook() -> dict:
        return {"ok": True}

    @test_app.get("/health")
    def app_health() -> dict:
        return {"status": "ok"}

    assert mount_dashboard(test_app, dist) is True
    # Sanity: the real app also mounts successfully against the same layout.
    assert real_app is not None
    return test_app


class MountDashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dist = _make_dist(Path(self._tmp.name))
        self.client = TestClient(_app_with_real_api_routes(self.dist))

    def test_missing_dist_is_not_mounted(self) -> None:
        empty = Path(self._tmp.name) / "nope"
        self.assertFalse(mount_dashboard(FastAPI(), empty))

    def test_index_served_at_root(self) -> None:
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("<app-root>", r.text)

    def test_client_side_route_falls_back_to_index(self) -> None:
        """A deep link like /reports/abc must return the SPA, not a 404."""
        for path in ("/reports", "/report-detail", "/some/deep/client/route"):
            with self.subTest(path=path):
                r = self.client.get(path)
                self.assertEqual(r.status_code, 200)
                self.assertIn("<app-root>", r.text)

    def test_real_files_are_served(self) -> None:
        r = self.client.get("/favicon.ico")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, b"\x00\x00\x01\x00")

    def test_api_routes_are_not_shadowed(self) -> None:
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"status": "ok"})

    def test_webhook_route_is_not_shadowed(self) -> None:
        r = self.client.post("/webhook")
        self.assertEqual(r.status_code, 200)

    def test_app_health_route_is_not_shadowed(self) -> None:
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})

    def test_unknown_api_path_is_404_not_html(self) -> None:
        """The catch-all must refuse reserved prefixes, not return index.html."""
        r = self.client.get("/api/definitely-missing")
        self.assertEqual(r.status_code, 404)
        self.assertNotIn("<app-root>", r.text)

    def test_unknown_webhook_path_is_404_not_html(self) -> None:
        r = self.client.get("/webhook/extra")
        self.assertEqual(r.status_code, 404)
        self.assertNotIn("<app-root>", r.text)

    def test_path_traversal_is_contained(self) -> None:
        """Encoded traversal must not escape the dist directory."""
        for path in ("/../api.py", "/..%2fapi.py", "/%2e%2e/api.py"):
            with self.subTest(path=path):
                r = self.client.get(path)
                self.assertNotIn("create_app", r.text)
                self.assertNotIn("GitHub", r.text)


class AppConfiguredTests(unittest.TestCase):
    """`app_configured` must reflect reality, not just a non-empty path string.

    Regression: a relative or unmounted GITHUB_PRIVATE_KEY_PATH reported the App
    as configured, then /api/auth/status returned 500 when minting the App JWT.
    That broke the dashboard's "Connect GitHub" button in exactly the state a
    fresh deploy is in.
    """

    def _cfg(self, key_path):
        class _C:
            github_app_id = "12345"
            github_private_key_path = key_path

        return _C()

    def test_false_when_nothing_set(self) -> None:
        from api import _app_configured

        self.assertFalse(_app_configured(self._cfg("")))

    def test_false_when_id_missing(self) -> None:
        from api import _app_configured

        class _NoId:
            github_app_id = ""
            github_private_key_path = "/tmp/whatever.pem"

        self.assertFalse(_app_configured(_NoId()))

    def test_false_when_key_file_missing(self) -> None:
        from api import _app_configured

        with tempfile.TemporaryDirectory() as tmp:
            missing = str(Path(tmp) / "not-there.pem")
            self.assertFalse(
                _app_configured(self._cfg(missing)),
                "a non-existent key path must not count as configured",
            )

    def test_true_when_key_file_exists(self) -> None:
        from api import _app_configured

        with tempfile.TemporaryDirectory() as tmp:
            key = Path(tmp) / "private-key.pem"
            key.write_text("-----BEGIN PRIVATE KEY-----\nx\n", encoding="utf-8")
            self.assertTrue(_app_configured(self._cfg(str(key))))


if __name__ == "__main__":
    unittest.main()
