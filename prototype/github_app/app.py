"""FastAPI webhook handler for the PR decomposer GitHub App.

The App listens for GitHub webhooks and does the CodeRabbit-style work:

- `installation` / `installation_repositories`: track which accounts installed
  the App, so the dashboard's Connect button resolves to "already installed".
- `pull_request` (opened / reopened / synchronize): analyze the PR and
  idempotently post (or update) the bot's review comment.
- `issue_comment` (created, on a PR): slash commands — `/review` re-runs the
  analysis, `/ask <question>` answers a question about the diff, `/help` lists
  commands.

Every GitHub call uses the *App's own* installation access token (from the
`installation.id` in the payload) — no PAT, no per-user OAuth.

You can also mount this app inside `api.py` by including its routes / app.

Run standalone:
    uvicorn github_app.app:app --reload --port 8000
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, FastAPI, Request, Response, status
from fastapi.responses import RedirectResponse

from pr_decomposer import ConfigError, answer_question, run_pipeline
from pr_decomposer.config import PROTOTYPE_ROOT, load_config
from github_client import (
    GithubApiDiffSource,
    GithubApiError,
    find_bot_comment,
    list_installation_repos,
    post_pr_comment,
    prepare_comment_body,
    update_pr_comment,
)
from github_app.auth import (
    GithubAppError,
    NotConfigured,
    get_installation_token,
    record_installation,
    remove_installation,
    set_installation_repos,
)

log = logging.getLogger("pr-decomposer")
logging.basicConfig(level=logging.INFO)

router = APIRouter()
app = FastAPI(title="PR Decomposer GitHub App")

COMMANDS = ("review", "ask", "help")


def _verify_signature(payload: bytes, signature_header: str, secret: str) -> bool:
    if not signature_header or not secret:
        return False
    digest = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature_header)


def _webhook_response(config) -> Response:
    """Inert-mode reply when the GitHub App is not enabled."""
    log.info("github app disabled (GITHUB_APP_ENABLED=0); event ignored")
    return Response(status_code=200, content="{}")


def _installation_id(payload: dict) -> int | None:
    return (payload.get("installation") or {}).get("id")


def _repo_meta(payload: dict):
    repo = payload.get("repository") or {}
    owner = (repo.get("owner") or {}).get("login") or ""
    name = repo.get("name") or ""
    full_name = repo.get("full_name") or f"{owner}/{name}"
    return owner, name, full_name


# ── event handlers ───────────────────────────────────────────────────────────

def _handle_installation_event(payload: dict, config) -> dict:
    action = payload.get("action", "")
    installation = payload.get("installation") or {}
    installation_id = installation.get("id")
    if installation_id is None:
        return {"handled": False, "reason": "missing installation.id"}
    account = installation.get("account") or {}
    repositories = payload.get("repositories") or []

    if action == "deleted":
        remove_installation(installation_id)
        return {"handled": True, "action": "deleted", "installation": installation_id}

    record_installation(installation_id, account, repositories)
    return {"handled": True, "action": action, "installation": installation_id}


def _handle_repositories_event(payload: dict, config) -> dict:
    action = payload.get("action", "")
    installation_id = _installation_id(payload)
    if installation_id is None:
        return {"handled": False, "reason": "missing installation.id"}
    add = action == "added"
    set_installation_repos(installation_id, (payload.get("repositories") or []), add=add)
    return {"handled": True, "action": action, "installation": installation_id}


def _handle_pull_request(payload: dict, config) -> dict:
    action = payload.get("action")
    if action not in ("opened", "reopened", "synchronize"):
        return {"handled": False, "reason": f"action '{action}' ignored"}
    installation_id = _installation_id(payload)
    if installation_id is None:
        return {"handled": False, "reason": "missing installation.id"}

    pr = payload.get("pull_request") or {}
    number = pr.get("number")
    owner, name, _ = _repo_meta(payload)
    if not number or not owner or not name:
        return {"handled": False, "reason": "missing pull_request/repository metadata"}
    if pr.get("draft"):
        return {"handled": False, "reason": "draft PR ignored"}

    try:
        token = get_installation_token(config, installation_id)
        diff_source = GithubApiDiffSource(token, owner, name, number)
        diff = diff_source.fetch()
        report = run_pipeline(diff, config=config)
        body = prepare_comment_body(report)

        existing = find_bot_comment(token, owner, name, number,
                                    config.github_bot_username)
        if existing:
            update_pr_comment(token, owner, name, existing, body)
            route = "updated"
        else:
            post_pr_comment(token, owner, name, number, body)
            route = "posted"
        return {"handled": True, "route": route, "pr": number}
    except (GithubApiError, GithubAppError, NotConfigured, ConfigError) as exc:
        log.exception("failed to process pull_request event")
        return {"handled": False, "error": str(exc)}


def _handle_issue_comment(payload: dict, config) -> dict:
    action = payload.get("action")
    if action != "created":
        return {"handled": False, "reason": f"action '{action}' ignored"}
    is_pr = bool(payload.get("pull_request"))
    if not is_pr:
        return {"handled": False, "reason": "not a pull request"}
    comment = payload.get("comment") or {}
    body = (comment.get("body") or "").strip()
    if not body.startswith("/"):
        return {"handled": False, "reason": "not a slash command"}
    command = body.split(maxsplit=1)[0].lstrip("/").lower().split("?")[0]
    arg = body.split(maxsplit=1)[1] if len(body.split(maxsplit=1)) > 1 else ""

    installation_id = _installation_id(payload)
    if installation_id is None:
        return {"handled": False, "reason": "missing installation.id"}

    issue = payload.get("issue") or {}
    number = issue.get("number") or (payload.get("pull_request") or {}).get("number")
    owner, name, _ = _repo_meta(payload)
    if not number or not owner or not name:
        return {"handled": False, "reason": "missing issue/repository metadata"}

    try:
        token = get_installation_token(config, installation_id)
        if command == "help":
            reply = (_help_text())
            post_pr_comment(token, owner, name, number, reply)
            return {"handled": True, "command": "help"}

        diff_source = GithubApiDiffSource(token, owner, name, number)
        diff = diff_source.fetch()

        if command == "ask":
            question = arg or "(no question)"
            try:
                answer = answer_question(diff, question, config=config)
            except ConfigError as exc:
                reply = f"⚠️ {exc}"
            else:
                reply = _answer_markdown(question, answer, number)
            post_pr_comment(token, owner, name, number, reply)
            return {"handled": True, "command": "ask"}

        if command == "review":
            report = run_pipeline(diff, config=config)
            body_md = prepare_comment_body(report)
            existing = find_bot_comment(token, owner, name, number,
                                        config.github_bot_username)
            if existing:
                update_pr_comment(token, owner, name, existing, body_md)
                route = "updated"
            else:
                post_pr_comment(token, owner, name, number, body_md)
                route = "posted"
            return {"handled": True, "command": "review", "route": route, "pr": number}

        return {"handled": False, "reason": f"unknown command '/{command}'"}
    except (GithubApiError, GithubAppError, NotConfigured, ConfigError) as exc:
        log.exception("failed to handle issue_comment command")
        return {"handled": False, "error": str(exc)}


def _help_text() -> str:
    return (
        "**Available commands** (leave a PR comment starting with a slash):\n\n"
        "- `/review` — re-run the AI analysis and update the review comment.\n"
        "- `/ask <question>` — ask anything about this PR's diff.\n"
        "- `/help` — show this help.\n"
    )


def _answer_markdown(question: str, answer: str, pr_number: int) -> str:
    return (f"> **Q:** {question}\n\n"
            f"{answer}\n\n"
            f"· _" + "answer via `/ask` on PR #" + f"{pr_number}_")


@router.post("/webhook")
async def webhook(request: Request) -> Response:
    config = load_config(PROTOTYPE_ROOT / ".env")
    payload = await request.body()
    event = request.headers.get("X-GitHub-Event", "")
    signature = request.headers.get("X-Hub-Signature-256", "")

    if not config.github_app_enabled:
        return _webhook_response(config)

    if not _verify_signature(payload, signature, config.github_webhook_secret):
        return Response(status_code=status.HTTP_401_UNAUTHORIZED,
                        content="invalid signature")

    data = json.loads(payload or b"{}")

    if event == "ping":
        return Response(status_code=200, content='{"ping": "pong"}')

    try:
        if event == "installation":
            result = _handle_installation_event(data, config)
        elif event == "installation_repositories":
            result = _handle_repositories_event(data, config)
        elif event == "pull_request":
            result = _handle_pull_request(data, config)
        elif event == "issue_comment":
            result = _handle_issue_comment(data, config)
        else:
            result = {"handled": False, "reason": f"event '{event}' ignored"}
    except Exception as exc:  # noqa: BLE001
        log.exception("failed to process %s event", event)
        return Response(status_code=500,
                        content=json.dumps({"error": str(exc)}, default=str))
    return Response(status_code=200, content=json.dumps(result, default=str))


@router.get("/webhook")
async def webhook_setup(request: Request) -> RedirectResponse:
    """Browser route GitHub sends the installer to after they click *Install*.

    GitHub redirects the user's browser here (the App's Setup URL, which you
    should point at `{public}/webhook`) with `?setup_action=install` +
    `installation_id`. We seed the installation record and bounce them straight
    back to the dashboard so Connect resolves immediately.
    """
    config = load_config(PROTOTYPE_ROOT / ".env")
    setup_action = request.query_params.get("setup_action") or ""
    installation_id = request.query_params.get("installation_id")
    target = config.frontend_url or "http://localhost:4200"

    if config.github_app_enabled and setup_action == "install" and installation_id:
        try:
            token = get_installation_token(config, installation_id)
            repos = list_installation_repos(token)
            record_installation(
                installation_id,
                {"login": request.query_params.get("login") or "",
                 "name": "", "type": "User"},
            )
            set_installation_repos(installation_id, repos)
            log.info("recorded installation %s after setup (setup_action=install)",
                     installation_id)
        except (GithubApiError, GithubAppError, NotConfigured) as exc:
            log.warning("could not prefetch install %s: %s", installation_id, exc)
        return RedirectResponse(
            url=f"{target}/?installed=1&installation_id={installation_id}",
            status_code=status.HTTP_302_FOUND)
    return RedirectResponse(url=f"{target}/", status_code=status.HTTP_302_FOUND)


@router.get("/health")
async def health() -> dict:
    config = load_config(PROTOTYPE_ROOT / ".env")
    return {"status": "ok", "github_app_enabled": config.github_app_enabled,
            "model": config.model,
            "has_api_key": bool(config.has_api_key)}


app.include_router(router)