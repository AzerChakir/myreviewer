"""Offline tests for the GitHub App auth + webhook (CodeRabbit-style).

Uses a throwaway RSA key, a temp DATA_DIR, and mocked GitHub HTTP endpoints —
no real GitHub calls, no LLM calls.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import jwt as pyjwt


def _make_keypair() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    return pem, public


class FakeConfig:
    """Minimal Config-like object with just the fields auth uses."""

    def __init__(self, **kwargs):
        self.github_app_enabled = kwargs.get("github_app_enabled", True)
        self.github_app_id = kwargs.get("github_app_id", "12345")
        self.github_app_slug = kwargs.get("github_app_slug", "")
        self.github_webhook_secret = kwargs.get("github_webhook_secret", "sekret")
        self.github_private_key_path = kwargs.get("github_private_key_path", "")
        self.github_token = kwargs.get("github_token", "")
        self.github_bot_username = kwargs.get("github_bot_username", "bot[bot]")
        self.model = "nvidia/tiny"
        self.has_api_key = False


class TestAppAuth(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.old_env = os.environ.get("DATA_DIR")
        os.environ["DATA_DIR"] = cls.tmp
        cls.pem, cls.public = _make_keypair()
        from github_app import auth
        cls.auth = auth
        # auth resolves DATA_ROOT and the JSON cache paths at IMPORT time, so
        # setting os.environ["DATA_DIR"] above is only effective if this module
        # was not already imported by another test module. Repoint the paths
        # explicitly so these tests neither read nor write the real data/
        # directory regardless of discovery order.
        cls._saved_paths = (
            auth.DATA_ROOT,
            auth.INSTALLATIONS_PATH,
            auth.APP_TOKENS_PATH,
            auth.APP_SLUG_PATH,
        )
        root = Path(cls.tmp)
        auth.DATA_ROOT = root
        auth.INSTALLATIONS_PATH = root / "installations.json"
        auth.APP_TOKENS_PATH = root / "app_tokens.json"
        auth.APP_SLUG_PATH = root / "app_slug.json"

    @classmethod
    def tearDownClass(cls):
        (auth_root, inst_path, tok_path, slug_path) = cls._saved_paths
        cls.auth.DATA_ROOT = auth_root
        cls.auth.INSTALLATIONS_PATH = inst_path
        cls.auth.APP_TOKENS_PATH = tok_path
        cls.auth.APP_SLUG_PATH = slug_path
        cls.auth._token_cache.clear()
        if cls.old_env is None:
            os.environ.pop("DATA_DIR", None)
        else:
            os.environ["DATA_DIR"] = cls.old_env
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        # Start every test from an empty in-memory cache so ordering cannot
        # leak a minted token between tests.
        self.auth._token_cache.clear()
        key_path = Path(self.tmp) / "test-key.pem"
        key_path.write_text(self.pem, encoding="utf-8")
        self.config = FakeConfig(github_private_key_path=str(key_path))

    def setUp(self):
        key_path = Path(self.tmp) / "test-key.pem"
        key_path.write_text(self.pem, encoding="utf-8")
        self.config = FakeConfig(github_private_key_path=str(key_path))

    def test_create_app_jwt_is_rs256_and_self_signed(self):
        token = self.auth.create_app_jwt(self.config)
        claims = pyjwt.decode(token, self.public, algorithms=["RS256"])
        self.assertEqual(claims["iss"], str(self.config.github_app_id))
        # iat ~ now, exp within 10 minutes.
        now = int(time.time())
        self.assertLessEqual(now - claims["iat"], 5)
        self.assertLessEqual(claims["exp"] - now, 10 * 60)

    def test_create_app_jwt_requires_app_id(self):
        bad = FakeConfig(github_app_id="")
        with self.assertRaises(self.auth.NotConfigured):
            self.auth.create_app_jwt(bad)

    def test_app_slug_prefers_config_and_caches(self):
        cfg = FakeConfig(github_app_slug="my-reviewer")
        self.assertEqual(self.auth.app_slug(cfg), "my-reviewer")

    def test_install_url(self):
        cfg = FakeConfig(github_app_slug="my-reviewer")
        self.assertEqual(
            self.auth.install_url(cfg),
            "https://github.com/apps/my-reviewer/installations/new",
        )

    def test_get_installation_token_mints_and_caches(self):
        payload = {"token": "ghs_123", "expires_at": "2099-01-01T00:00:00Z"}
        with mock.patch("github_app.auth.requests.post") as post:
            post.return_value = mock.Mock(
                status_code=201, json=lambda: payload,
            )
            token = self.auth.get_installation_token(self.config, 7)
            self.assertEqual(token, "ghs_123")
            cache = self.auth._token_cache["7"]
            self.assertEqual(cache["token"], "ghs_123")

    def test_get_installation_token_raises_on_failure(self):
        with mock.patch("github_app.auth.requests.post") as post:
            post.return_value = mock.Mock(
                status_code=401, text='{"message":"bad"}',
            )
            with self.assertRaises(self.auth.GithubAppError):
                self.auth.get_installation_token(self.config, 7, force=True)

    def test_record_and_list_installations(self):
        account = {"login": "octocat", "name": "Mona", "type": "User"}
        repos = [{"full_name": "octocat/Hello-World"}, {"full_name": "octocat/Repo2"}]
        self.auth.record_installation(9, account, repos)
        installs = self.auth.list_installations()
        self.assertIn("9", installs)
        self.assertEqual(installs["9"]["account_login"], "octocat")
        self.assertEqual(installs["9"]["repositories"],
                         ["octocat/Hello-World", "octocat/Repo2"])
        self.auth.remove_installation(9)
        self.assertNotIn("9", self.auth.list_installations())

    def test_installation_for_repo_prefers_repo_match(self):
        self.auth.record_installation(1, {"login": "owner-a"}, [{"full_name": "A/repo"}])
        self.auth.record_installation(2, {"login": "owner-b"}, [{"full_name": "B/repo"}])
        found = self.auth.installation_for_repo("B", "repo")
        self.assertEqual(found["installation_id"], "2")
        self.auth.remove_installation(1)
        self.auth.remove_installation(2)

    def test_resolve_token_for_repo(self):
        self.auth.record_installation(3, {"login": "owner-c"}, [{"full_name": "C/app"}])
        payload = {"token": "ghs-install-3", "expires_at": "2099-01-01T00:00:00Z"}
        with mock.patch("github_app.auth.requests.post") as post:
            post.return_value = mock.Mock(status_code=201, json=lambda: payload)
            token = self.auth.resolve_token_for(self.config, "owner-c", "app")
            self.assertEqual(token, "ghs-install-3")
        self.auth.remove_installation(3)

    def test_resolve_token_for_repo_missing_installation(self):
        with self.assertRaises(self.auth.NotConfigured):
            self.auth.resolve_token_for(self.config, "nobody", "repo")


class TestWebhookCommands(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.old_env = os.environ.get("DATA_DIR")
        os.environ["DATA_DIR"] = cls.tmp
        cls.pem, cls.public = _make_keypair()
        from github_app import app as web_app
        cls.web = web_app

    @classmethod
    def tearDownClass(cls):
        if cls.old_env is None:
            os.environ.pop("DATA_DIR", None)
        else:
            os.environ["DATA_DIR"] = cls.old_env

    def setUp(self):
        from github_app import auth
        self.auth = auth
        auth.DATA_ROOT = Path(self.tmp)
        auth.INSTALLATIONS_PATH = Path(self.tmp) / "installations.json"
        auth.APP_TOKENS_PATH = Path(self.tmp) / "app_tokens.json"
        auth.APP_SLUG_PATH = Path(self.tmp) / "app_slug.json"
        auth._token_cache.clear()
        self.config = FakeConfig()

    def payload(self, **overrides):
        base = {
            "action": "created",
            "installation": {"id": 11},
            "repository": {"full_name": "octocat/Hello-World",
                          "name": "Hello-World",
                          "owner": {"login": "octocat"}},
            "issue": {"number": 5},
            "pull_request": {"number": 5, "draft": False},
            "comment": {"body": "/help", "user": {"login": "octocat"}},
        }
        base.update(overrides)
        return base

    def test_verify_signature(self):
        body = b'{"x":1}'
        secret = "s3cr3t"
        good = "sha256=" + __import__("hmac").new(
            secret.encode(), body, __import__("hashlib").sha256
        ).hexdigest()
        self.assertTrue(self.web._verify_signature(body, good, secret))
        self.assertFalse(self.web._verify_signature(body, good, "other"))
        self.assertFalse(self.web._verify_signature(body, "", secret))

    @mock.patch("github_app.app.get_installation_token", return_value="ghs-tok")
    @mock.patch("github_app.app.find_bot_comment", return_value=None)
    @mock.patch("github_app.app.post_pr_comment", return_value=123)
    @mock.patch("github_app.app.GithubApiDiffSource")
    def test_issue_comment_help(self, diff_source, post_comment, find, get_tok):
        diff_source.return_value.fetch.return_value = mock.Mock()
        result = self.web._handle_issue_comment(
            self.payload(action="created", comment={"body": "/help",
                                                   "user": {"login": "octocat"}}),
            self.config,
        )
        self.assertEqual(result["handled"], True)
        self.assertEqual(result["command"], "help")
        post_comment.assert_called_once()
        body = post_comment.call_args[0][4]
        self.assertIn("Available commands", body)

    @mock.patch("github_app.app.get_installation_token", return_value="ghs-tok")
    @mock.patch("github_app.app.post_pr_comment", return_value=123)
    @mock.patch("github_app.app.GithubApiDiffSource")
    @mock.patch("github_app.app.answer_question", return_value="Because of X.")
    def test_issue_comment_ask(self, answer, diff_source, post_comment, get_tok):
        diff_source.return_value.fetch.return_value = mock.Mock(title="PR")
        result = self.web._handle_issue_comment(
            self.payload(action="created",
                         comment={"body": "/ask why is this here?",
                                  "user": {"login": "octocat"}}),
            self.config,
        )
        self.assertEqual(result["handled"], True)
        self.assertEqual(result["command"], "ask")
        answer.assert_called_once()
        self.assertIn("why is this here?", post_comment.call_args[0][4])

    @mock.patch("github_app.app.get_installation_token", return_value="ghs-tok")
    @mock.patch("github_app.app.find_bot_comment", return_value=99)
    @mock.patch("github_app.app.update_pr_comment")
    @mock.patch("github_app.app.GithubApiDiffSource")
    def test_issue_comment_review_updates_existing(self, diff_source,
                                                   update_comment, find, get_tok):
        diff_source.return_value.fetch.return_value = mock.Mock()
        with mock.patch("github_app.app.run_pipeline") as run:
            run.return_value = mock.Mock(
                pr_title="T", pr_summary="S", verdict="v", concerns=[], flags=[],
                plan_markdown="plan", stats_text="stats",
                change_log=[], post_review=[], post_review_verdict="",
                bug_findings=[], requirements_checks=[],
                requirements_verdict="", merge_readiness="ready", commit_messages=[],
                model="mock", effort="deep",
            )
            result = self.web._handle_issue_comment(
                self.payload(action="created",
                             comment={"body": "/review",
                                      "user": {"login": "octocat"}}),
                self.config,
            )
        self.assertEqual(result["route"], "updated")
        update_comment.assert_called_once()

    @mock.patch("github_app.app.get_installation_token", return_value="ghs-tok")
    @mock.patch("github_app.app.post_pr_comment")
    @mock.patch("github_app.app.find_bot_comment", return_value=None)
    @mock.patch("github_app.app.update_pr_comment")
    @mock.patch("github_app.app.GithubApiDiffSource")
    def test_issue_comment_unknown_command(self, diff_source, update,
                                           find, post_comment, get_tok):
        diff_source.return_value.fetch.return_value = mock.Mock()
        result = self.web._handle_issue_comment(
            self.payload(action="created",
                         comment={"body": "/banana",
                                  "user": {"login": "octocat"}}),
            self.config,
        )
        self.assertEqual(result["handled"], False)
        self.assertIn("unknown command", result["reason"])

    @mock.patch("github_app.app.get_installation_token", return_value="ghs-tok")
    @mock.patch("github_app.app.GithubApiDiffSource")
    @mock.patch("github_app.app.find_bot_comment", return_value=None)
    @mock.patch("github_app.app.post_pr_comment", return_value=321)
    def test_pull_request_opened_posts_comment(self, post_comment, find,
                                               diff_source, get_tok):
        diff_source.return_value.fetch.return_value = mock.Mock()
        with mock.patch("github_app.app.run_pipeline") as run:
            run.return_value = mock.Mock(
                pr_title="T", pr_summary="S", verdict="v", concerns=[], flags=[],
                plan_markdown="plan", stats_text="stats",
                change_log=[], post_review=[], post_review_verdict="",
                bug_findings=[], requirements_checks=[],
                requirements_verdict="", merge_readiness="ready", commit_messages=[],
                model="mock", effort="deep",
            )
            result = self.web._handle_pull_request(
                self.payload(action="opened", comment=None), self.config)
        self.assertEqual(result["route"], "posted")
        self.assertEqual(result["pr"], 5)
        post_comment.assert_called_once()

    def test_pull_request_draft_ignored(self):
        result = self.web._handle_pull_request(
            self.payload(action="opened",
                         pull_request={"number": 5, "draft": True}),
            self.config,
        )
        self.assertIn("draft", result["reason"])

    def test_installation_created_deleted(self):
        result = self.web._handle_installation_event(
            {"action": "created", "installation": {"id": 4},
             "repositories": [{"full_name": "o/r"}]},
            self.config,
        )
        self.assertTrue(result["handled"])
        self.assertIn("4", self.auth.list_installations())

        result = self.web._handle_installation_event(
            {"action": "deleted", "installation": {"id": 4}},
            self.config,
        )
        self.assertTrue(result["handled"])
        self.assertNotIn("4", self.auth.list_installations())


if __name__ == "__main__":
    unittest.main(verbosity=2)