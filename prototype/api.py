"""Dashboard REST API for the PR decomposer GitHub App.

Serves the Angular dashboard: triggers analysis of a GitHub PR, stores the
report, lists past reports, and returns full reports. CORS is enabled for the
Angular dev server (http://localhost:4200).

Authentication is GitHub-App based (CodeRabbit-style): the dashboard's
"Connect" button opens the GitHub **App install page**; once the user installs
the App on their account, the server authenticates with the App's own
installation access tokens — no per-user OAuth, no PAT.

Run:
    uvicorn api:app --reload --port 8000
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from github_client import (
    GithubApiDiffSource,
    GithubApiError,
    list_open_prs_for_install,
    post_comment_from_payload,
    pr_states,
)
from github_app import auth as app_auth
from github_app.app import router as webhook_router
from pr_decomposer import ConfigError, run_pipeline
from pr_decomposer.config import PROTOTYPE_ROOT, load_config
from pr_decomposer.models import available_models
from pr_decomposer.report_export import build_html, build_markdown, build_pdf
from pr_decomposer.repo_context import compose_requirements, fetch_repo_requirements
from pr_decomposer.store import (
    DATA_ROOT,
    ReportNotFoundError,
    delete_report,
    list_reports,
    load_report,
    make_report_id,
    report_owner,
    save_report,
)

CONFIG = load_config(PROTOTYPE_ROOT / ".env")

log = logging.getLogger("api")

app = FastAPI(title="PR Decomposer API")

# The GitHub App webhook (review PRs, slash commands, install tracking) is
# served from the same process/port and shared data dir as the dashboard.
app.include_router(webhook_router)

FRONTEND_URL = CONFIG.frontend_url
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:4200",
        "http://127.0.0.1:4200",
        CONFIG.frontend_url,
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AnalyzeRequest(BaseModel):
    owner: str
    repo: str
    pr_number: int
    mock: bool = False
    model: str | None = None
    effort: str = "deep"
    requirements: str = ""
    post_comment: bool = False


class AnalyzeResponse(BaseModel):
    report_id: str
    analyzed_at_iso: str
    report: dict


def _app_configured(config) -> bool:
    """True only when the App has an ID *and* a readable private key on disk.

    Checking that the path is merely non-empty is not enough: a typo, a
    relative path that resolves elsewhere inside the container, or an unmounted
    volume would otherwise report "configured" and then blow up with a 500 the
    moment an App JWT is minted.
    """
    if not (config.github_app_id and config.github_private_key_path):
        return False
    return Path(config.github_private_key_path).is_file()


def _connected_installations(config) -> list[dict]:
    """Live view of accounts that have installed the App, for the dashboard."""
    if not _app_configured(config):
        return []
    try:
        installs = app_auth.list_installations().values()
    except Exception:  # noqa: BLE001 - disk read must not break /api/health
        installs = []
    result = []
    for inst in installs:
        result.append({
            "installation_id": inst.get("installation_id", ""),
            "username": inst.get("account_login", ""),
            "display_name": inst.get("account_name", ""),
            "account_type": inst.get("account_type", ""),
            "repos": inst.get("repositories", []),
        })
    return result


def _resolve_token(owner: str = "", repo: str = "") -> str:
    try:
        return app_auth.resolve_token_for(CONFIG, owner, repo)
    except app_auth.NotConfigured as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "model": CONFIG.model,
        "default_model": CONFIG.model,
        "models": available_models(CONFIG),
        "has_api_key": CONFIG.has_api_key,
        "app_configured": _app_configured(CONFIG),
        "connected": bool(_connected_installations(CONFIG)),
        "github_configured": bool(CONFIG.github_token) or _app_configured(CONFIG),
        "reports": len(list_reports()),
    }


@app.get("/api/models")
def models() -> list[dict]:
    return available_models(CONFIG)


@app.post("/api/analyze", response_model=AnalyzeResponse)
def analyze(req: AnalyzeRequest, request: Request) -> AnalyzeResponse:
    # Real LLM by default. Mock is an explicit dev/test opt-in only — never a
    # silent fallback, so a missing key surfaces as a clear configuration error.
    analyzer_uses_mock = req.mock

    try:
        token = _resolve_token(req.owner, req.repo)
    except HTTPException:
        raise

    try:
        diff_source = GithubApiDiffSource(
            token=token, owner=req.owner, repo=req.repo,
            pr_number=req.pr_number,
        )
        diff = diff_source.fetch()
    except app_auth.NotConfigured as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        detail = str(exc)
        raise HTTPException(status_code=400, detail=detail)

    requirements = ""
    if not analyzer_uses_mock:
        try:
            repo_block = fetch_repo_requirements(req.owner, req.repo, token)
            requirements = compose_requirements(
                repo_block=repo_block, manual=req.requirements,
            )
        except Exception:
            requirements = req.requirements

    try:
        report = run_pipeline(
            diff, config=CONFIG, mock=analyzer_uses_mock,
            model=req.model, effort=req.effort, requirements=requirements,
        )
    except ConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"LLM analysis failed: {exc}")

    payload = report.to_dict()
    owner_login = req.owner
    report_id = make_report_id(req.owner, req.repo, req.pr_number)
    payload["report_id"] = report_id
    payload["owner"] = owner_login
    payload["repo"] = f"{req.owner}/{req.repo}"
    payload["pr_number"] = req.pr_number
    payload["analyzed_at_iso"] = datetime.now(timezone.utc).isoformat()
    payload["requested_mock"] = analyzer_uses_mock
    payload["effort"] = report.effort

    report_id = save_report(payload, report_id)

    comment_posted = False
    if req.post_comment and not analyzer_uses_mock:
        try:
            post_comment_from_payload(token, req.owner, req.repo,
                                      req.pr_number, payload)
            comment_posted = True
        except GithubApiError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"analysis saved ({report_id}) but posting the PR "
                       f"comment failed: {exc}",
            )

    return AnalyzeResponse(
        report_id=report_id,
        analyzed_at_iso=payload["analyzed_at_iso"],
        report=payload,
    )


# ── GitHub App "Connect / install" ───────────────────────────────────────────


@app.get("/api/auth/status")
def auth_status() -> dict:
    configured = _app_configured(CONFIG)
    connected = _connected_installations(CONFIG)
    first = connected[0] if connected else {}

    # Resolving the slug mints an App JWT and calls GitHub, so it can fail for
    # reasons unrelated to the install state (unreadable key, GitHub outage).
    # A status endpoint must never 500: degrade to "not connected" instead.
    install_url = ""
    if configured:
        try:
            install_url = app_auth.install_url(CONFIG)
        except Exception:  # noqa: BLE001 - see above
            log.warning("could not resolve the App install URL", exc_info=True)
            install_url = ""

    return {
        "connected": configured and bool(connected),
        "username": first.get("username", ""),
        "display_name": first.get("display_name", ""),
        "account_type": first.get("account_type", ""),
        "installations": connected,
        "app_configured": configured,
        "install_url": install_url,
    }


@app.get("/api/auth/login")
def auth_login() -> RedirectResponse:
    if not _app_configured(CONFIG):
        raise HTTPException(
            status_code=400,
            detail="GitHub App not configured — set GITHUB_APP_ID and "
                   "GITHUB_PRIVATE_KEY_PATH in .env.",
        )
    try:
        url = app_auth.install_url(CONFIG)
    except app_auth.GithubAppError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except app_auth.NotConfigured as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return RedirectResponse(url=url)


@app.post("/api/auth/logout")
def auth_logout() -> dict:
    """The App has no per-user session; logout just reports App install state."""
    return {"connected": bool(_connected_installations(CONFIG))}


@app.get("/api/users")
def users() -> dict:
    """Accounts that have installed the GitHub App on this instance."""
    return {"config": CONFIG.raw.get("GITHUB_APP_ENABLED", "0"),
            "installations": _connected_installations(CONFIG)}


@app.post("/api/auth/disconnect")
def auth_disconnect(response: Response) -> dict:
    """Drop local installation records (uninstalling the App happens on GitHub)."""
    for installation_id in list(app_auth.list_installations()):
        app_auth.remove_installation(installation_id)
    return {"connected": False}


@app.get("/api/prs")
def open_prs(request: Request) -> list[dict]:
    if not _app_configured(CONFIG):
        raise HTTPException(status_code=400, detail="GitHub App not configured.")
    installations = list(app_auth.list_installations().values())
    if not installations:
        raise HTTPException(
            status_code=400,
            detail="No installations yet — open the dashboard Connect button to "
                   "install the App on your GitHub account.",
        )

    all_prs: list[dict] = []
    seen: set[str] = set()
    for installation in installations:
        installation_id = installation.get("installation_id")
        if not installation_id:
            continue
        try:
            token = app_auth.get_installation_token(CONFIG, installation_id)
            prs = list_open_prs_for_install(token)
        except (app_auth.GithubAppError, app_auth.NotConfigured, GithubApiError):
            continue
        for pr in prs:
            key = f"{pr.get('repo_full', '')}#{pr.get('pr_number')}"
            if key in seen:
                continue
            seen.add(key)
            all_prs.append(pr)

    reviewed = {
        f"{r.get('repo', '')}#{r.get('pr_number')}"
        for r in list_reports()
        if isinstance(r.get("pr_number"), int) and r.get("repo")
    }
    all_prs.sort(key=lambda p: p.get("updated_at") or "", reverse=True)
    return [
        pr for pr in all_prs
        if f"{pr.get('repo_full', '')}#{pr.get('pr_number')}" not in reviewed
    ]


@app.get("/api/reports")
def reports() -> list[dict]:
    return list_reports()


@app.get("/api/reports/states")
def reports_states() -> dict:
    """Live GitHub state (merged?) for the stored reports."""
    repo_prs = [
        {"owner": r.get("repo", "").split("/", 1)[0] if "/" in r.get("repo", "") else "",
         "repo": r.get("repo", ""), "pr_number": r.get("pr_number")}
        for r in list_reports()
        if isinstance(r.get("pr_number"), int) and r.get("repo")
    ]
    states: dict[str, dict] = {}
    for item in repo_prs:
        owner = item.get("owner", "")
        repo = item.get("repo", "")
        pr_number = item.get("pr_number")
        if not owner or not repo or not isinstance(pr_number, int):
            continue
        try:
            token = _resolve_token(owner, repo)
            states.update(pr_states(token, [item]))
        except (HTTPException, app_auth.GithubAppError, app_auth.NotConfigured,
                GithubApiError):
            continue
    return states


def _require_owner(request: Request, report_id: str) -> str:
    """Simple existence gate — reports are visible to whoever runs the App."""
    return report_owner(report_id)


@app.get("/api/reports/{report_id}")
def report(report_id: str) -> dict:
    try:
        return load_report(report_id)
    except ReportNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"report '{exc}' not found")


@app.get("/api/reports/{report_id}/export")
def export_report(report_id: str, format: str = "html") -> Response:
    """Download a stored report as standalone HTML, Markdown, or a PDF."""
    export_format = (format or "html").lower()
    if export_format == "markdown":
        export_format = "md"
    if export_format not in ("html", "md", "pdf"):
        raise HTTPException(
            status_code=400,
            detail="format must be 'html', 'md', or 'pdf'",
        )
    try:
        payload = load_report(report_id)
    except ReportNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"report '{exc}' not found")

    filename = f"{report_id}.{export_format}"
    export_formats = {
        "html": ("text/html; charset=utf-8", build_html(payload).encode("utf-8")),
        "md": ("text/markdown; charset=utf-8", build_markdown(payload).encode("utf-8")),
        "pdf": ("application/pdf", _build_pdf_bytes(payload)),
    }
    media_type, body = export_formats[export_format]
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _build_pdf_bytes(payload: dict) -> bytes:
    try:
        return bytes(build_pdf(payload))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {exc}")


@app.delete("/api/reports/{report_id}")
def remove(report_id: str) -> dict:
    if not (DATA_ROOT / f"{report_id}.json").exists():
        raise HTTPException(status_code=404, detail=f"report '{report_id}' not found")
    delete_report(report_id)
    return {"deleted": report_id}


# --------------------------------------------------------------------------
# Production dashboard (single-service mode)
#
# In development the Angular dev server runs separately on :4200 and proxies
# /api here. In production we serve the compiled bundle from this same process
# so a single container/VM port serves BOTH the dashboard and the webhook.
#
# The catch-all below must stay LAST: FastAPI matches routes in registration
# order, so it only ever sees paths that no API or webhook route claimed.
# --------------------------------------------------------------------------
FRONTEND_DIST = PROTOTYPE_ROOT / "dashboard" / "dist" / "dashboard" / "browser"

# Paths owned by the API/webhook layer. The SPA fallback refuses to answer these
# so a typo in a client never silently returns HTML instead of JSON.
_RESERVED_PREFIXES = ("api/", "webhook", "health")


def mount_dashboard(target: FastAPI, dist_dir: Path) -> bool:
    """Serve a built Angular bundle from `target`. Returns True if mounted.

    Split out from module scope so tests can point it at a temporary directory
    without reloading the module.
    """
    if not dist_dir.is_dir():
        return False

    assets_dir = dist_dir / "assets"
    if assets_dir.is_dir():
        target.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @target.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> Response:
        """Serve the bundle, falling back to index.html for client-side routes."""
        if full_path.startswith(_RESERVED_PREFIXES):
            raise HTTPException(status_code=404, detail="not found")
        candidate = (dist_dir / full_path).resolve()
        # Guard against ../ traversal escaping the dist directory.
        if candidate.is_file() and dist_dir in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(dist_dir / "index.html")

    return True


mount_dashboard(app, FRONTEND_DIST)