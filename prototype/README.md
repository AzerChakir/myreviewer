# PR Decomposer — Prototype

## What is it?

A research-grounded prototype that **analyzes large pull requests**, **suggests
decomposition strategies**, **highlights mixed concerns**, and **recommends
reviewer-friendly PR structure**. It is the application spin-off of the
CodeReviewQA experiments at the repository root: those showed that
summary-augmented prompting improves model comprehension on code-review
reasoning tasks (change type recognition, change localisation, solution
identification, and automated code refinement).

The tool reuses that same idea at pull-request scale: summarize the change's
intent first, then cluster the diff into distinct concerns, flag mixed or
oversized changes, and plan a reviewer-friendly split.

```
GitDiff (any source)                     LLM backend (NVIDIA NIM free tier)
  - local diff file / git range      ─▶  - summarize (intent)
  - GitHub PR (via REST/.diff API)       - cluster (files -> concerns)
        │                                - diagnose (mixed concerns, size flags)
        │                                - plan (markdown split plan)
        ▼
   Markdown report + JSON  ◀───────── (CLI prints it, GitHub App posts it)
```

## Quick start (local, no GitHub required)

```bash
cd prototype
python -m venv .venv && source .venv/bin/activate   # or reuse repo venv
pip install -r requirements.txt

cp .env.example .env          # then paste your NVIDIA key into .env
```

Run on the bundled example diff with the **mock** model (no API key needed)
to see the full pipeline:

```bash
python cli.py --diff example/mixed_concern.diff --title "Mixed PR: avatars, orders speedup, refactor" --mock
```

Or with the real model (requires `NIM_API_KEY` in `.env`):

```bash
python cli.py --diff example/mixed_concern.diff --title "Mixed PR: avatars, orders speedup, refactor"
```

You can also analyze any local repository range, or a GitHub PR:

```bash
python cli.py --repo /path/to/repo --base main --head feature-branch
python cli.py --owner <org> --repo <name> --pr 42            # needs GITHUB_TOKEN
python cli.py --diff example/mixed_concern.diff --format json # machine-readable
```

Compare a run against `example/expected_report.md` (generated with the mock
model) to confirm the pipeline behaves as documented.

## Web dashboard (Angular)

An Angular 20 single-page app (CodeRabbit-style) for analyzing GitHub PRs and
browsing past reports. The backend exposes a small REST API on `:8000`; the
Angular dev server proxies `/api` to it, so no CORS config is needed locally.

```bash
# terminal 1 — REST API + GitHub App webhook (FastAPI)
./run_api.sh

# terminal 2 — Angular dev server (http://localhost:4200)
./run_dashboard.sh
```

Requirements: the repo `.venv` for the backend; a local Node.js ≥ 20.19 for the
frontend (this machine uses a user-local install at `~/.local/bin`).

To see the dashboard populated without GitHub access, seed the bundled example:

```bash
./seed_report.sh     # writes a demo report into data/reports; reload the page
```

Analyzing a real PR from the UI requires the **GitHub App** configured in `.env`
(create it at github.com/settings/apps/new, install it from the dashboard's
Connect button, and the server uses the App's own installation tokens — no PAT).
Reports are stored as JSON under `data/reports/` and appear on the dashboard
list automatically. The "mock model" toggle analyzes without an NVIDIA key.

API endpoints:

| Endpoint | Description |
|----------|-------------|
| `GET /api/health` | model, App/key status, report count |
| `POST /api/analyze` | body `{owner, repo, pr_number, mock}` → stores + returns a report |
| `GET /api/auth/login` | redirects to the GitHub App install page |
| `POST /webhook` | GitHub App webhook (PR review, `/review` `/ask` `/help`) |
| `GET /api/reports` | metadata list (title, verdict, counts) |
| `GET/DELETE /api/reports/{report_id}` | full report / delete |
| `GET /api/reports/{report_id}/export?format=html&#124;md&#124;pdf` | download the report as a standalone HTML, Markdown, or PDF file |

## Configuration (`.env`)

All settings live in `prototype/.env` (copy of `.env.example`, gitignored).

| Key | Meaning | Default |
|-----|---------|---------|
| `NIM_API_KEY` | Free NVIDIA build key (`nvapi-...`) at build.nvidia.com | — |
| `NIM_BASE_URL` | OpenAI-compatible endpoint | `https://integrate.api.nvidia.com/v1` |
| `NIM_MODEL` | default model id (starts as `nvidia/nemotron-3-super-120b-a12b`) | `nvidia/nemotron-3-super-120b-a12b` |
| `NIM_MODELS` | extra models for the picker, comma-separated (hardcode ids from the NIM catalog) | — |
| `LLM_TEMPERATURE` | sampling temperature | `0.0` |
| `LLM_MAX_TOKENS` | max completion tokens | `8192` |
| `GITHUB_APP_ENABLED` | switch the GitHub App webhook + dashboard on | `0` |
| `GITHUB_APP_ID` | numeric App id from your GitHub App settings | — |
| `GITHUB_APP_SLUG` | App slug (optional; auto-fetched when unset) | — |
| `GITHUB_WEBHOOK_SECRET` | Webhook secret configured on the App | — |
| `GITHUB_PRIVATE_KEY_PATH` | path to the App's `.pem` private key | — |
| `GITHUB_BOT_USERNAME` | bot login used to find/update its own comments | `pr-decomposer[bot]` |
| `GITHUB_TOKEN` | optional legacy PAT — only for `cli.py --owner/--repo/--pr` | — |

The client retries rate limits (25-40 req/min on the free tier) with
exponential backoff + jitter.

## Architecture

```
prototype/
├── api.py                     # dashboard REST API (FastAPI) — analyze + report store
├── cli.py                     # local harness: prints markdown or JSON
├── github_client.py           # the diff-source seam + GitHub REST helpers
├── seed_report.py             # seeds the demo report (real LLM, or --mock)
├── GUIDE.md                   # localhost use + test walkthrough
├── tests/                     # unit tests (mock) + live tests (real NIM + GitHub)
├── pr_decomposer/             # pure analysis core (model-agnostic)
│   ├── config.py              # .env loading
│   ├── store.py               # JSON report persistence (data/reports)
│   ├── llm_client.py          # OpenAI-compatible client (+ MockLLMClient)
│   ├── prompts.py             # 6 stage prompts (summary-augmented style)
│   ├── summarizer.py          # stage 1 — overall intent
│   ├── clusterer.py           # stage 2 — files -> concerns
│   ├── diagnoser.py           # stage 3 — flags + verdict (heuristics + LLM)
│   ├── change_log.py          # stage 4 — AI change summary (old → new)
│   ├── post_review.py         # stage 5 — review of the new code + verdict
│   └── planner.py             # stage 6 — markdown split plan
├── github_app/                # GitHub App integration (CodeRabbit-style)
│   ├── app.py                 # FastAPI webhook router — events + slash commands
│   └── auth.py                # JWT signing, install tokens, installation registry
├── dashboard/                 # Angular 20 web app (proxy.conf.json → :8000)
├── example/                   # sample mixed-concern diff + expected report
└── requirements.txt
```

### The six stages (research mapping)

| Stage | Module | CodeReviewQA analogue |
|-------|--------|-----------------------|
| 1. Summarize intent | `summarizer.py` | summary-augmented prompting (`[SUMMARY]` block in `utils.py`) |
| 2. Cluster concerns | `clusterer.py` | change localisation (CL) |
| 3. Diagnose flags | `diagnoser.py` | change type recognition (CTR) + size heuristics |
| 4. Change summary | `change_log.py` | old → new code summary (per concern) |
| 5. Post-review | `post_review.py` | review findings on the new code + verdict |
| 6. Plan the split | `planner.py` | solution identification (SI) — what the author should do |

The change summary and post-review findings are rendered into the GitHub PR
comment (`github_client.prepare_comment_body`) and shown as cards in the
dashboard when a report contains them.

## Production status

- **GitHub App (done):** CodeRabbit-style. `github_app/auth.py` signs the App JWT
  (RS256, ≤10-min TTL), mints per-installation access tokens, and caches them to
  `data/`; `github_app/app.py` verifies webhook signatures and reacts to
  `installation`, `pull_request` (opened/reopened/synchronize) and
  `issue_comment` events — auto-posting or updating a review and answering the
  `/help`, `/review` and `/ask <question>` slash commands. Users install the App
  via the dashboard's Connect button (no OAuth, no PAT). Inert unless
  `GITHUB_APP_ENABLED=1` and the App secrets/key are configured.
- **Multi-tenant accounts (designed):** `auth.resolve_auth_provider` keeps a
  seam so per-user access (e.g. private repos on organic installs) can slot in
  behind `GithubApiDiffSource` without touching the analysis core.
- **Deployment (ready):** `Dockerfile` builds the Angular bundle and serves it
  from the same FastAPI process, so one container exposes the dashboard, the REST
  API and the webhook on a single port. `docker-compose.yml` runs it behind
  host nginx for TLS; `.github/workflows/ci.yml` tests + builds the image and
  `.github/workflows/deploy.yml` pushes to GHCR and deploys to the VM over SSH.

## Deploying

See **[`../docs/DEPLOY.md`](../docs/DEPLOY.md)** for the full runbook: Azure VM
bootstrap, TLS, registering the GitHub App (Webhook URL **and** Setup URL are
both `https://<your-domain>/webhook`), and the CI/CD secrets.

> GitHub requires a publicly trusted HTTPS certificate to deliver webhooks, so
> you need a real domain on the VM — a bare IP with a self-signed cert is
> rejected.

## Notes

- This prototype is intentionally independent from the experiment scripts in
  the repository root — reuse `.venv` but nothing else is shared.
- NIM chat endpoints do not guarantee JSON mode, so stages emit/parse
  structured text blocks and degrade gracefully.
- Real analysis is the default everywhere: the dashboard, `cli.py` and the API
  call the configured LLM whenever `NIM_API_KEY` is set, and a missing key fails
  fast with a clear message instead of silently switching to mocks. Mock
  responses are an explicit dev/test opt-in only (dashboard checkbox,
  `cli.py --mock`, `seed_report.py --mock`, `tests/test_pipeline.py`).
- See `GUIDE.md` for a hands-on localhost walkthrough (setup, use, testing).