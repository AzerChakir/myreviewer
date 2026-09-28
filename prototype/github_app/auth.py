"""GitHub App authentication — CodeRabbit-style.

The server proves it IS the GitHub App by signing a short-lived JWT with the
App's private key (`GITHUB_APP_ID` + `GITHUB_PRIVATE_KEY_PATH`), then exchanges
it for per-installation access tokens (`POST /app/installations/{id}/access_tokens`).
Those tokens are scoped to the repos the user granted the App, so the server
never needs a user PAT — the "connect your GitHub" step is simply *installing
the App* on the user's account.

Installations are recorded from `installation`/`installation_repositories`
webhook events into `data/installations.json`, and access tokens are cached
(with `expires_at`) so we only mint a token when the old one is near expiry.

`AuthProvider.resolve_auth_provider()` picks the nicest configured provider:
a static `GITHUB_TOKEN` (CLI/live tests) falls back to the App.
"""

from __future__ import annotations

import json
import os
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path

import jwt
import requests

GITHUB_API = "https://api.github.com"
GITHUB_APP_INSTALL_URL = "https://github.com/apps/{slug}/installations/new"

JWT_MAX_AGE = 9 * 60  # GitHub caps App JWTs at 10 minutes
TOKEN_LEEWAY = 5 * 60  # refresh when an install token is this close to expiry


def _data_root() -> Path:
    env_dir = os.environ.get("DATA_DIR")
    if env_dir:
        return Path(env_dir)
    if os.environ.get("VERCEL"):
        return Path("/tmp/prd-data")
    return Path(__file__).resolve().parent.parent / "data"


DATA_ROOT = _data_root()
INSTALLATIONS_PATH = DATA_ROOT / "installations.json"
APP_TOKENS_PATH = DATA_ROOT / "app_tokens.json"
APP_SLUG_PATH = DATA_ROOT / "app_slug.json"


class NotConfigured(RuntimeError):
    """The GitHub App is not configured (missing key/id/etc.)."""


class GithubAppError(RuntimeError):
    """A GitHub App API call failed."""


# ── persistence helpers ──────────────────────────────────────────────────────

def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _write_json(path: Path, data: dict) -> None:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _load_private_key(private_key_path: str) -> str:
    if not private_key_path:
        raise NotConfigured(
            "GITHUB_PRIVATE_KEY_PATH is not set — generate a private key on the "
            "GitHub App settings page and save it to prototype/.env."
        )
    path = Path(private_key_path)
    if not path.exists():
        raise NotConfigured(
            f"GITHUB_PRIVATE_KEY_PATH points to a missing file: {path}"
        )
    return path.read_text(encoding="utf-8")


# ── App JWT ──────────────────────────────────────────────────────────────────

def create_app_jwt(config) -> str:
    """Sign an RS256 JWT asserting this server is the GitHub App."""
    app_id = getattr(config, "github_app_id", "") or ""
    if not app_id:
        raise NotConfigured(
            "GITHUB_APP_ID is not set — create a GitHub App at "
            "github.com/settings/apps/new and add its id to .env."
        )
    private_key = _load_private_key(getattr(config, "github_private_key_path", ""))
    now = int(time.time())
    payload = {"iat": now, "exp": now + JWT_MAX_AGE, "iss": app_id}
    return jwt.encode(payload, private_key, algorithm="RS256")


# ── installation access tokens ───────────────────────────────────────────────

_token_cache: dict[str, dict] = {}


def request_app_info(config) -> dict:
    """GET /app with the App JWT — public_name/slug/etc. for install URLs."""
    headers = {"Authorization": f"Bearer {create_app_jwt(config)}",
               "Accept": "application/vnd.github+json"}
    resp = requests.get(f"{GITHUB_API}/app", headers=headers, timeout=60)
    if resp.status_code != 200:
        raise GithubAppError(f"GET /app failed: HTTP {resp.status_code} {resp.text[:200]}")
    return resp.json()


def app_slug(config) -> str:
    """The App's URL slug, used for the install page link.

    Prefers `GITHUB_APP_SLUG` when set, otherwise queries `GET /app` once and
    caches the result on disk (the slug is stable).
    """
    configured_slug = getattr(config, "github_app_slug", "") or ""
    if configured_slug:
        return configured_slug
    cached = _read_json(APP_SLUG_PATH)
    if cached.get("slug"):
        return cached["slug"]
    info = request_app_info(config)
    slug = info.get("slug")
    if not slug:
        app_id = getattr(config, "github_app_id", "") or ""
        raise GithubAppError(
            f"GET /app returned no slug (app id {app_id}) — cannot build the "
            "install URL."
        )
    _write_json(APP_SLUG_PATH, {"slug": slug})
    return slug


def install_url(config) -> str:
    """The 'Install this App' page the dashboard Connect button opens."""
    return GITHUB_APP_INSTALL_URL.format(slug=app_slug(config))


def _exchange_install_token(config, installation_id: int | str) -> dict:
    headers = {"Authorization": f"Bearer {create_app_jwt(config)}",
               "Accept": "application/vnd.github+json"}
    resp = requests.post(
        f"{GITHUB_API}/app/installations/{installation_id}/access_tokens",
        headers=headers,
        timeout=60,
    )
    if resp.status_code != 201:
        raise GithubAppError(
            f"create installation token failed: HTTP {resp.status_code} {resp.text[:200]}"
        )
    return resp.json()


def get_installation_token(config, installation_id: int | str, force: bool = False) -> str:
    """Return a live installation access token, minting/refreshing as needed.

    Tokens live ~1 hour; we cache them in-memory and on disk until they are
    within `TOKEN_LEEWAY` of expiry, then re-mint.
    """
    key = str(installation_id)

    def _cached() -> str | None:
        cached = _token_cache.get(key) or _read_json(APP_TOKENS_PATH).get(key)
        if cached and cached.get("token") and cached.get("expires_at"):
            try:
                expiry = datetime.fromisoformat(cached["expires_at"])
            except (TypeError, ValueError):
                expiry = None
            if expiry and (expiry - datetime.now(timezone.utc)).total_seconds() > TOKEN_LEEWAY:
                return cached["token"]
        return None

    if not force:
        token = _cached()
        if token:
            return token

    payload = _exchange_install_token(config, installation_id)
    token = payload.get("token", "")
    if not token:
        raise GithubAppError("GitHub returned an empty installation token.")
    entry = {
        "installation_id": key,
        "token": token,
        "expires_at": payload.get("expires_at", ""),
    }
    _token_cache[key] = entry
    tokens = _read_json(APP_TOKENS_PATH)
    tokens[key] = entry
    _write_json(APP_TOKENS_PATH, tokens)
    return token


# ── installation registry ────────────────────────────────────────────────────

def record_installation(installation_id: int | str, account: dict,
                        repositories: list[dict] | None = None) -> dict:
    installations = _read_json(INSTALLATIONS_PATH)
    entry = installations.get(str(installation_id)) or {}
    entry.update({
        "installation_id": str(installation_id),
        "account_login": (account or {}).get("login", ""),
        "account_name": (account or {}).get("name") or (account or {}).get("login", ""),
        "account_type": (account or {}).get("type", ""),
        "repositories": [r.get("full_name", "") for r in (repositories or [])],
        "created_at": entry.get("created_at") or datetime.now(timezone.utc).isoformat(),
    })
    installations[str(installation_id)] = entry
    _write_json(INSTALLATIONS_PATH, installations)
    return entry


def remove_installation(installation_id: int | str) -> None:
    installations = _read_json(INSTALLATIONS_PATH)
    installations.pop(str(installation_id), None)
    _write_json(INSTALLATIONS_PATH, installations)
    tokens = _read_json(APP_TOKENS_PATH)
    tokens.pop(str(installation_id), None)
    _write_json(APP_TOKENS_PATH, tokens)
    _token_cache.pop(str(installation_id), None)


def set_installation_repos(installation_id: int | str,
                           repositories: list[dict] | None,
                           add: bool = True) -> None:
    installations = _read_json(INSTALLATIONS_PATH)
    entry = installations.get(str(installation_id))
    if not entry:
        return
    incoming = [r.get("full_name", "") for r in (repositories or [])]
    current = set(entry.get("repositories", []))
    if add:
        current |= set(incoming)
    else:
        current -= set(incoming)
    entry["repositories"] = sorted(current)
    installations[str(installation_id)] = entry
    _write_json(INSTALLATIONS_PATH, installations)


def list_installations() -> dict[str, dict]:
    """{installation_id: entry} in install order."""
    return _read_json(INSTALLATIONS_PATH)


def installation_for_repo(owner: str, repo: str = "") -> dict | None:
    """Best-match installation for a repo: by exact repo, else by account."""
    full = f"{owner}/{repo}" if repo else ""
    entries = list(list_installations().values())
    if full:
        for entry in entries:
            if full in entry.get("repositories", []):
                return entry
        for entry in entries:
            if entry.get("account_login") == owner:
                return entry
    for entry in entries:
        if entry.get("account_login") == owner:
            return entry
    return entries[0] if entries else None


def resolve_token_for(config, owner: str, repo: str = "") -> str:
    """App installation token for a repo, preferring the matching install."""
    installation = installation_for_repo(owner, repo)
    if not installation:
        raise NotConfigured(
            f"No GitHub App installation covers '{owner}/{repo}'. Install the "
            "app on that account in the dashboard first."
        )
    return get_installation_token(config, installation["installation_id"])


# ── auth-provider seam ───────────────────────────────────────────────────────

class AuthProvider(ABC):
    name: str = "base"

    @abstractmethod
    def get_token(self) -> str:
        """Return a usable GitHub access token or raise NotConfigured."""

    def describe(self) -> str:
        try:
            token = self.get_token()
        except NotConfigured:
            return f"{self.name}: not configured"
        return f"{self.name}: {token[:12]}..."


class TokenAuth(AuthProvider):
    """Static token from `.env` — handy for the CLI and live tests."""

    name = "token"

    def __init__(self, token: str = ""):
        self._token = token

    def get_token(self) -> str:
        if not self._token:
            raise NotConfigured("no GITHUB_TOKEN set in .env")
        return self._token


class AppAuth(AuthProvider):
    """GitHub App auth: JWT-signed identity + installation access tokens."""

    name = "github-app"

    def __init__(self, config, installation_id: int | str | None = None):
        self._config = config
        self._installation_id = installation_id
        self._token = ""

    def set_installation(self, installation_id: int | str) -> "AppAuth":
        self._installation_id = installation_id
        self._token = ""
        return self

    def get_token(self) -> str:
        if self._token:
            return self._token
        if self._installation_id is None:
            raise NotConfigured(
                "no installation selected — pick an installation before using "
                "the GitHub App."
            )
        self._token = get_installation_token(self._config, self._installation_id)
        return self._token


def resolve_auth_provider(config) -> AuthProvider:
    """Pick the best configured provider: static token first, else App auth."""
    if getattr(config, "github_token", ""):
        return TokenAuth(config.github_token)
    return AppAuth(config)