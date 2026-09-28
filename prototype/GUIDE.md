# PR Decomposer — localhost guide

Everything in this file runs on your own machine (`http://localhost:8000` for the
API, `http://localhost:4200` for the dashboard). No deployment, no tunnels, no
GitHub hooks required.

The product works **real by default**: when `NIM_API_KEY` is set, every analysis
(CLI, API, dashboard) calls the NVIDIA NIM LLM. Mock responses are an explicit
dev/test opt-in and are never used silently.

---

## 1. Prerequisites

| What | Where | Notes |
|------|-------|-------|
| Python 3.11+ venv | `/home/azer/CodeReviewQA/.venv` | `openai`, `fastapi`, `uvicorn`, `requests` (see `requirements.txt`) |
| Node 18+/npm | `/home/azer/.local/bin` | for the Angular dashboard |
| NVIDIA NIM key | `prototype/.env` → `NIM_API_KEY` | free at https://build.nvidia.com → "API keys" (`nvapi-…`) |
| GitHub token | `prototype/.env` → `GITHUB_TOKEN` | fine-grained with **Pull requests: Read** (+ **Contents: Read** for private repos), or classic `public_repo` |

### 1.1 Configure `.env`

```bash
cd /home/azer/CodeReviewQA/prototype
cp .env.example .env
# edit .env and fill:
#   NIM_API_KEY=nvapi-xxxxxxxx
#   GITHUB_TOKEN=github_pat_xxxxxxxx
#   NIM_MODEL=nvidia/nemotron-3-super-120b-a12b   # default; add others via NIM_MODELS
```

> **Adding a model.** The picker starts with a single model
> (`nvidia/nemotron-3-super-120b-a12b`). To add another from the NVIDIA NIM
> catalog, hardcode it in `.env` as `NIM_MODELS=id1,id2` (comma-separated) — the
> app only ever lists the default plus whatever you add explicitly. Verify a
> candidate callable on your account first:
>
> ```bash
> curl https://integrate.api.nvidia.com/v1/chat/completions \
>   -H "Authorization: Bearer $NIM_API_KEY" -H "Content-Type: application/json" \
>   -d '{"model":"nvidia/nemotron-3-super-120b-a12b",
>        "messages":[{"role":"user","content":"say OK"}],"max_tokens":8}'
> ```
>
> A `200` with a JSON body means the model works on your account. Most catalog
> ids are not callable from a free key — many return HTTP 404, some hang (e.g.
> `google/gemma-4-31b-it`, `meta/llama-3.2-90b-vision-instruct`) and older ids
> return 500/410 (e.g. `meta/llama-3.2-11b-vision-instruct`, `openai/gpt-oss-20b`
> as of 2026-09). Check the response before adding them.

---

## 2. Start the local stack

Two terminals, from `prototype/`:

```bash
# Terminal 1 — API (port 8000)
./run_api.sh

# Terminal 2 — dashboard (port 4200, proxies /api → :8000)
./run_dashboard.sh
```

Sanity check:

```bash
curl -s http://localhost:8000/api/health
# {"status":"ok","model":"nvidia/nemotron-3-super-120b-a12b","has_api_key":true,"github_configured":true,"reports":N}
```

Open http://localhost:4200 — the health badges should show **LLM ready** and
**GitHub token set**.

---

## 3. How to use it (localhost)

### 3.1 Dashboard

1. Paste a PR address into the single field — `owner/repo#123` or
   `https://github.com/owner/repo/pull/123`.
2. Watch the live “resolved” preview appear.
3. Click **Analyze PR**. The box **Dev: use mock LLM responses** is off by
   default, so this runs the real NIM LLM plus a real GitHub diff fetch.
4. The report opens with: overall summary → concerns → mixed highlights →
   flags → **AI change summary (old → new)** → **Review of the new code** (with
   verdict) → suggested decomposition.
5. Reports persist under `prototype/data/reports/` and appear in the table.

Works best on PRs with 2+ independent concerns (the example demo report uses
one). One-off demo data can be reseeded with:

```bash
./seed_report.sh        # real LLM analysis of the bundled example diff
./seed_report.sh --mock # deterministic offline demo instead
```

### 3.2 CLI

```bash
# Analyze a local unified diff with the real LLM (no GitHub needed)
.venv/bin/python cli.py --diff example/mixed_concern.diff --title "Mixed PR"

# Analyze a real GitHub PR (needs GITHUB_TOKEN)
.venv/bin/python cli.py --owner octocat --repo Hello-World --pr 1

# Analysis over a local git range (any repo, no GitHub token)
.venv/bin/python cli.py --repo /path/to/repo --base main --head feature-x

# JSON output, and the dev/test mock mode
.venv/bin/python cli.py --diff example/mixed_concern.diff --format json
.venv/bin/python cli.py --diff example/mixed_concern.diff --mock
```

### 3.3 API

```bash
# Trigger a real analysis and store the report
curl -X POST http://localhost:8000/api/analyze \
  -H 'Content-Type: application/json' \
  -d '{"owner":"octocat","repo":"Hello-World","pr_number":1,"mock":false}'

# List / fetch / delete reports
curl http://localhost:8000/api/reports
curl http://localhost:8000/api/reports/<report_id>
curl -X DELETE http://localhost:8000/api/reports/<report_id>

# Download a stored report as HTML, Markdown, or PDF
curl -OJ "http://localhost:8000/api/reports/<report_id>/export?format=html"
curl -OJ "http://localhost:8000/api/reports/<report_id>/export?format=md"    # or format=markdown
curl -OJ "http://localhost:8000/api/reports/<report_id>/export?format=pdf"
```

### 3.4 GitHub comment preview

`github_client.prepare_comment_body(report)` renders exactly the comment a
GitHub App webhook would post (change summary → post-review → split plan):

```bash
.venv/bin/python -c "import sys; sys.path.insert(0,'.')
from github_client import GithubApiDiffSource, prepare_comment_body
from pr_decomposer import run_pipeline
from pr_decomposer.config import load_config
cfg = load_config()
d = GithubApiDiffSource(cfg.github_token, 'octocat', 'Hello-World', 1).fetch()
print(prepare_comment_body(run_pipeline(d, config=cfg)))"
```

---

## 4. How to test it (localhost)

### 4.1 Offline unit tests — no keys, < 1s

Uses the deterministic mock model; never touches the network.

```bash
cd /home/azer/CodeReviewQA/prototype
.venv/bin/python -m unittest tests.test_pipeline -v
```

Asserts: change log entries (old/new/impact) non-empty, review findings
severity-sorted, verdict present, concerns still parse, `to_dict()` carries all
new keys, unified-diff parsing works.

### 4.2 Live integration tests — real NIM + real GitHub

Exercises the **whole product** for real: fetches `octocat/Hello-World#1` from
the GitHub API and runs all six stages on the configured NIM model. Skips
automatically (in <1s) if `NIM_API_KEY` or `GITHUB_TOKEN` are missing.

```bash
.venv/bin/python -m unittest tests.test_live -v
```

Expected: ~20–30 s, 3 tests OK (`diff_fetched_from_github`,
`real_pipeline_runs`, `comment_body_renders`).

### 4.3 End-to-end against the running stack

With the API (`run_api.sh`) and dashboard (`run_dashboard.sh`) up:

```bash
# Real analysis through the dashboard's proxy, then clean up
curl -X POST http://localhost:4200/api/analyze \
  -H 'Content-Type: application/json' \
  -d '{"owner":"octocat","repo":"Hello-World","pr_number":1,"mock":false}'
curl http://localhost:4200/api/reports
curl -X DELETE http://localhost:4200/api/reports/octocat-Hello-World-pr1
```

`e2e_check.sh` runs a fuller smoke (health, report list, report-detail fields
through the :4200 proxy).

---

## 5. What “real” means here

- Every LLM stage (summarize, cluster, diagnose, change log, post-review, plan)
  hits `NIM_BASE_URL` with the configured `NIM_MODEL`.
- The GitHub diff is fetched via `https://api.github.com` using `GITHUB_TOKEN`.
- No silent fallback: a missing key aborts with a clear error
  (`cli.py`, dashboard analyze, `/api/analyze`). Mock is only reachable via the
  explicit switches listed above and is reserved for dev/test.

---

## 6. Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `/api/health` shows **no LLM key** | `NIM_API_KEY` missing/empty in `.env`; analysis will error until set. |
| ```400 no GITHUB_TOKEN configured``` | set `GITHUB_TOKEN` in `.env` (dashboard analyzes real PRs through the API). |
| ```llm error: HTTP 410``` | `NIM_MODEL` retired (EOL). Switch to a verified model — see §1.1. |
| LLM call hangs forever | model is in the catalogue but not callable for your account; pick a working one (§1.1 curl check). |
| ```llm error: HTTP 401``` | `NIM_API_KEY` invalid/expired. |
| ```HTTP 429``` | NIM free-tier rate limit (~25–40 req/min); the client retries with backoff, or wait and retry. |
| Port 8000/4200 already in use | an old `uvicorn`/`ng serve` is still running; stop it or use a different port. |
| Dashboard shows mock for a real run | demo data seeded with `seed_report.py --mock`, or `mock:true` was sent — reseed with `./seed_report.sh`. |

---

## 7. Cost / latency notes (free tier)

- One PR analysis = 6 LLM calls (one per stage). A small PR (a few files) runs
  in ~20–40 s on the free NIM endpoint; large diffs are truncated to ~40k chars
  (`git_diff.to_compact`) and take longer.
- The free tier is rate-limited; heavy bursts trigger 429s which are retried
  with exponential backoff (up to 60 s each).
- Nothing is posted to GitHub from this local setup — commenting only happens
  through the GitHub App webhook (`github_app/`), which is disabled by default.