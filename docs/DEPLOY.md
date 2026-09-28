# Deploy: GitHub App on your Azure VM

Goal: one public HTTPS URL that serves the dashboard, the REST API **and** the
GitHub App webhook — which is what GitHub requires before it will let you
register the App.

---

## 1. How this is architected

Today, in development, the app is two processes: the Angular dev server on
`:4200` and FastAPI on `:8000`, connected by a dev proxy. That is fine locally
and wrong for a server.

In production this project collapses to **a single service**:

```
                    https://pr-decomposer.example.com
                                  |
                    nginx :443  (TLS, certbot)      <- the only public listener
                                  |
                    uvicorn :8000 inside the container
                                  |
             +--------------------+--------------------+
             |                    |                    |
        /  (dashboard)     /api/*  (REST API)    /webhook  (App events)
```

`api.py` serves the compiled Angular bundle itself, so the container image is
one process. Consequences that matter:

- One port to expose, one process to restart, one thing to health-check.
- The dashboard, the API and the webhook share a single origin, so **no CORS
  configuration is needed in production**.
- The dev proxy (`dashboard/proxy.conf.json`) is only for `ng serve`.

### One URL for both Webhook URL and Setup URL

GitHub App registration asks for two things, and this service answers both on
the same path, separated by HTTP method:

| GitHub setting | What GitHub does | Handler |
|---|---|---|
| **Webhook URL** | `POST /webhook` with an event body + HMAC signature | `POST /webhook` |
| **Setup URL** | sends the installer's *browser* to `GET /webhook?setup_action=install&installation_id=…` | `GET /webhook` → 302 to the dashboard |

So set both to `https://<your-domain>/webhook`.

> **The Setup URL is optional** in GitHub's form, but set it anyway: it is what
> makes the dashboard's "Connect" button resolve instantly after install. If you
> leave it blank, users land on the dashboard and must click Connect again.

---

## 2. The TLS requirement (read this first)

GitHub **rejects** a webhook whose certificate is not publicly trusted. Three
consequences:

- `http://` will not work. It must be `https://`.
- A raw VM IP with a self-signed certificate will not work. The full hostname
  must be in the certificate.
- Therefore a **domain name is required** (unless you use a tunnel that issues a
  trusted certificate, e.g. Cloudflare Tunnel).

Checklist before registering the App:

- [ ] A DNS `A` record for your domain points at the VM's public IP
- [ ] `dig +short <your-domain>` returns that IP **from outside the VM**
- [ ] `curl -I https://<your-domain>/webhook` returns `302` over TLS
- [ ] Azure NSG allows 443

If the VM has no domain yet, the fastest unblock is
[Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
(it issues a publicly trusted certificate), or temporarily
`ngrok http 8000` for a smoke test — but do not use a tunnel URL as your final
App URL, because it changes every time you restart it.

---

## 3. Create the fresh app-only repo

This workspace's git history is a research repo (`results_summary/`, `slurm/`,
`deploy/` for Vercel, `*_vLLM.py`). You decided the new repo should contain
**only the app**. The old repo keeps its history untouched; nothing was lost.

```powershell
cd C:\Users\azerc\Desktop\MyReviewer\Do-AI-generated-PR-summary-improve-code-review
```

1. Create an empty repo on GitHub (no README, no `.gitignore`, no license —
   otherwise the first push conflicts). Name suggestion:
   `pr-decomposer`. **Do not** initialise it with a license or gitignore.
2. Point this workspace at it, on a clean `main` branch, so the 199 deletions
   and 15 modifications land as one intentional app-only snapshot:

```powershell
git checkout --orphan main
git rm -rf --cached .
git add -A
git status --short          # review carefully before committing
git commit -m "PR Decomposer: GitHub App service (dashboard + webhook)"
git remote add app-origin https://github.com/<you>/<new-repo>.git
git push -u app-origin main
```

`git checkout --orphan` creates a branch with no parent, so the new repo's
history contains exactly one commit and none of the research data.

> The `origin` remote still points at the old research repo. `app-origin` is the
> new one. Push with `git push app-origin main`; do not overwrite `origin`.

---

## 4. Bootstrap the VM

Copy `deploy/bootstrap_vm.sh` and `deploy/nginx/pr-decomposer.conf` to the VM,
then:

```bash
scp deploy/bootstrap_vm.sh deploy/nginx/pr-decomposer.conf azureuser@<vm-ip>:/tmp/
ssh azureuser@<vm-ip>
sudo bash /tmp/bootstrap_vm.sh pr-decomposer.example.com
```

It installs Docker, nginx, certbot, 2 GB of swap (a 1–2 GB VM will otherwise be
OOM-killed mid-analysis), the nginx vhost, and opens 22/80/443. It is
idempotent.

Once DNS resolves:

```bash
sudo certbot --nginx -d pr-decomposer.example.com --redirect
```

---

## 5. First deploy

```bash
sudo mkdir -p /opt/pr-decomposer && sudo chown $USER /opt/pr-decomposer
cd /opt/pr-decomposer
git clone https://github.com/<you>/<new-repo>.git .
cd prototype
cp .env.example .env
$EDITOR .env          # NIM_API_KEY + the GitHub App values from step 6
chmod 600 .env
docker compose up -d --build
docker compose ps
curl -fsS https://pr-decomposer.example.com/api/health
```

`prototype/.env` needs, at minimum:

| Key | Value |
|---|---|
| `NIM_API_KEY` | `nvapi-…` from https://build.nvidia.com |
| `GITHUB_APP_ENABLED` | `1` — **required**, or the post-install redirect never records the installation |
| `GITHUB_APP_ID` | the numeric App ID |
| `GITHUB_WEBHOOK_SECRET` | the secret you chose in GitHub's form |
| `GITHUB_PRIVATE_KEY_PATH` | absolute path to the downloaded `.pem` |
| `APP_URL` | `https://pr-decomposer.example.com` (public URL, **not** localhost) |
| `DATA_DIR` | `/app/data` (already set by `docker-compose.yml`) |

Two mistakes that are easy to make:

- `GITHUB_PRIVATE_KEY_PATH` must be an **absolute** path (`/opt/pr-decomposer/prototype/private-key.pem`),
  not the relative `prototype/private-key.pem` from the example — the container's
  working directory is `/app`, so a relative path resolves somewhere else.
- Mount the `.pem` read-only if you keep it outside the app directory, and keep
  it out of git (`.gitignore` already excludes `*.pem` and `private-key*`).

---

## 6. Register the GitHub App

Now that you have a reachable URL, register the App at
**https://github.com/settings/apps/new**:

| Field | Value |
|---|---|
| App name | e.g. `pr-decomposer` |
| Homepage / App URL | `https://pr-decomposer.example.com` |
| **Webhook URL** | `https://pr-decomposer.example.com/webhook` |
| **Setup URL** | `https://pr-decomposer.example.com/webhook` |
| Webhook secret | any long random string → put the same value in `GITHUB_WEBHOOK_SECRET` |

**Repository permissions**

| Permission | Access | Why |
|---|---|---|
| Metadata | Read-only | mandatory, auto-granted |
| Contents | Read-only | read the PR diff |
| Pull requests | **Read & write** | post/update review comments |
| Issues | **Read & write** | read `/review`, `/ask` comments |

**Events**: `Pull request`, `Issue comment`, `Installation`,
`Installation repositories`.

Then:

1. **Generate a private key** on the App's page → downloads a `.pem`. Copy it to
   the VM and point `GITHUB_PRIVATE_KEY_PATH` at it. This key is the one GitHub
   issues; a locally generated RSA key will not work.
2. Restart: `docker compose up -d --force-recreate app`
3. Verify the App is seen as configured:

```bash
curl -fsS https://pr-decomposer.example.com/api/health
#   "app_configured": true
```

4. Click **Install App** on your repo, then open a PR. The bot comments with
   the review; `/review`, `/ask` and `/help` work in PR comments.

If the App registers but deliveries fail, check
**Settings → Developer settings → your App → Advanced → Recent deliveries**;
a `401` means the webhook secret mismatches, a `404` means nginx is not
proxying `/webhook`.

---

## 7. CI/CD

Two workflows, both already written:

- `.github/workflows/ci.yml` — backend tests (`unittest`), Angular production
  build, Docker build, and a smoke test that boots the image and asserts
  `/api/health` is 200, `/` serves `<app-root>`, and an unsigned `POST /webhook`
  is rejected with 401.
- `.github/workflows/deploy.yml` — on push to `main`, requires CI to pass, builds
  and pushes the image to GHCR, then over SSH restarts the container on the VM
  and polls `https://<domain>/api/health` for 150 s.

### One-time setup

Install the CLIs (neither is on this machine yet):

```powershell
winget install --id GitHub.cli
winget install --id Microsoft.AzureCLI
gh auth login
az login
```

### Repository secrets

| Secret | Value |
|---|---|
| `VM_SSH_HOST` | VM hostname or public IP |
| `VM_SSH_PORT` | usually `22` |
| `VM_SSH_USER` | e.g. `azureuser` |
| `VM_SSH_PRIVATE_KEY` | contents of a **dedicated deploy key** (see below) |
| `NIM_API_KEY` | only if you want the live LLM test in CI |

```bash
# dedicated deploy key, not your personal one
ssh-keygen -t ed25519 -C "pr-decomposer-deploy" -f ~/.ssh/pr_decomposer_deploy -N ""
cat ~/.ssh/pr_decomposer_deploy.pub          # add as a deploy key on the VM user
gh secret set VM_SSH_PRIVATE_KEY < ~/.ssh/pr_decomposer_deploy
gh secret set VM_SSH_HOST; gh secret set VM_SSH_USER; gh secret set VM_SSH_PORT
```

### Repository variables

| Variable | Value |
|---|---|
| `APP_DOMAIN` | `pr-decomposer.example.com` (used by the post-deploy health check) |
| `VM_APP_DIR` | `/opt/pr-decomposer` |

Enable the `production` environment when prompted. After the first successful
run, protect `main` (Settings → Branches → require PR + status check `CI`) so
deploys only happen from reviewed commits.

---

## 8. Operating it

```bash
cd /opt/pr-decomposer/prototype
docker compose ps                 # health + uptime
docker compose logs -f app        # webhook deliveries, LLM calls, errors
docker compose restart app        # after editing .env
docker compose exec app sh        # shell inside the container
```

The named volume `pr-decomposer-data` holds `installations.json`,
`app_tokens.json` and `reports/`. **Back it up** — it is the only place
installation state lives:

```bash
docker run --rm -v pr-decomposer-data:/data -v $PWD:/backup alpine \
  tar czf /backup/prd-data-$(date +%F).tar.gz -C /data .
```

---

## 9. Troubleshooting

| Symptom | Cause |
|---|---|
| `405` on `GET /webhook` | stale process — the route exists in source; `docker compose up -d --force-recreate` |
| `401` on every delivery | `GITHUB_WEBHOOK_SECRET` ≠ the secret in GitHub's App settings |
| `404` on `/webhook` behind the domain | nginx vhost not proxying; `sudo nginx -t && sudo systemctl reload nginx` |
| Redirect goes to `localhost:4200` | `APP_URL` unset/wrong, or `GITHUB_APP_ENABLED=0` |
| `app_configured: false` | `GITHUB_APP_ID` or the `.pem` path is wrong/unreadable inside the container |
| Dashboard 404, API fine | `dashboard/dist/dashboard/browser` missing from the image — rebuild |
| Container OOM-killed | add swap (bootstrap step 3) and/or raise the VM's RAM |
| GitHub: "URL not reachable" | DNS not propagated, NSG blocking 443, or cert not trusted — recheck §2 |

---

## 10. Security notes

- `prototype/.env` and `*.pem` are gitignored; **never** commit them. The Docker
  build copies neither into the image (verified — no `nvapi-` or `PRIVATE KEY`
  material in any layer).
- The container runs as uid `10001`, not root, and the app port is bound to
  `127.0.0.1` so only nginx can reach it.
- A previously used NVIDIA key was pasted into this session's output. Rotate it
  at https://build.nvidia.com and update `.env` on the VM.
- Use a dedicated SSH deploy key for CI, restricted to the one VM user, rather
  than your personal key.
