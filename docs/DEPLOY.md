# Deploy: GitHub App on your Azure VM

Goal: one public HTTPS URL serving the dashboard, the REST API **and** the
GitHub App webhook — which is what GitHub requires before it will let you
register the App.

Images are **pushed to Docker Hub by CI** and **pulled by the VM**. There is no
SSH key in GitHub Secrets, and CI never touches the machine.

---

## 1. Architecture

In development the app is two processes: the Angular dev server on `:4200` and
FastAPI on `:8000`, joined by a dev proxy. In production it is **one service**:

```
     push                          pull
GitHub Actions  ──▶ Docker Hub ──▶ Azure VM
 (build+test)      azerchakir/     (docker compose)
                   pr-decomposer

                    https://pr-decomposer.example.com
                              |
              nginx :443  (TLS, certbot)      <- the only public listener
                              |
              uvicorn :8000 inside the container
                              |
       +----------------------+---------------------+
       |                |                |
   /  (dashboard)   /api/*  (REST)   /webhook  (App events)
```

`api.py` serves the compiled Angular bundle itself, so it is one process and one
port. The dashboard, API and webhook share an origin, so **no CORS is needed in
production**. The dev proxy is only for `ng serve`.

### One URL for both Webhook URL and Setup URL

| GitHub setting | What GitHub does | Handler |
|---|---|---|
| **Webhook URL** | `POST /webhook` + HMAC signature | `POST /webhook` |
| **Setup URL** | sends the installer's *browser* to `GET /webhook?setup_action=install&installation_id=…` | `GET /webhook` → 302 |

Set both to `https://<your-domain>/webhook`.

---

## 2. The TLS requirement (read this first)

GitHub **rejects** a webhook whose certificate is not publicly trusted:

- `http://` will not work.
- A raw VM IP with a self-signed cert will not work.
- You therefore need a **domain** (unless you use a tunnel that issues a
  trusted cert, e.g. Cloudflare Tunnel).

Before registering the App:

- [ ] DNS `A` record → the VM's public IP
- [ ] `dig +short <your-domain>` returns it **from outside the VM**
- [ ] `curl -I https://<your-domain>/webhook` returns `302` over TLS
- [ ] Azure NSG allows 443

---

## 3. Connecting to the VM (SSH)

You need the VM's **public IP or FQDN** and the **username** Azure created
(the default is the local admin user, commonly `azureuser`).

Find them in the portal: **Virtual machines → your VM → Overview**, or:

```powershell
winget install --id Microsoft.AzureCLI     # once
az login
az vm list -d                              # shows NAME, RESOURCE GROUP, IP ADDRESS
az vm show -d <vm-name> -g <rg> --query "publicIps,osProfile.adminUsername" -o tsv
```

### 3a. Allow SSH through the network security group

The NSG blocks port 22 by default for many images:

```powershell
az network nsg rule create -g <rg> --nsg <nsg-name> \
  --name allow-ssh -priority 1000 \
  --source-address-prefixes "<your-public-ip>/32" \
  --source-port-ranges "*" --destination-port-ranges 22 --protocol Tcp
```

Then also open 80/443 for the webhook:

```powershell
foreach ($p in 80,443) {
  az network nsg rule create -g <rg> --nsg <nsg-name> --name "allow-$p" `
    --priority 1010 --source-address-prefixes "*" `
    --source-port-ranges "*" --destination-port-ranges $p --protocol Tcp
}
```

Restricting SSH to your own IP (`<your-public-ip>/32`) rather than `*` is
strongly recommended.

### 3b. Test the connection

```powershell
ssh <user>@<vm-public-ip>
```

First contact asks you to confirm the host key — verify the fingerprint, then
type `yes`. Choose the key explicitly if you have more than one:

```powershell
ssh -i $HOME\.ssh\id_ed25519 <user>@<vm-public-ip>
```

Useful extras:

```powershell
ssh -v <user>@<host>          # verbose, for auth/protocol problems
ssh -p 2222 <user>@<host>     # non-standard port
scp deploy\bootstrap_vm.sh <user>@<host>:/tmp/   # copy a file over
```

### 3c. Save a host alias (optional but handy)

Create `C:\Users\azerc\.ssh\config`:

```
Host pr-decomposer
    HostName  <vm-public-ip>
    User      <vm-user>
    IdentityFile ~/.ssh/id_ed25519
    ServerAliveInterval 60
```

After that you can just type `ssh pr-decomposer`.

---

## 4. Bootstrap the VM

```powershell
scp deploy\bootstrap_vm.sh deploy\nginx\pr-decomposer.conf <user>@<vm-ip>:/tmp/
ssh <user>@<vm-ip>
sudo bash /tmp/bootstrap_vm.sh pr-decomposer.example.com
```

Installs Docker, nginx, certbot, **2 GB of swap** (a 1–2 GB VM is otherwise
OOM-killed mid-analysis), the nginx vhost, and opens 22/80/443. Idempotent.

Once DNS resolves:

```bash
sudo certbot --nginx -d pr-decomposer.example.com --redirect
```

---

## 5. Docker Hub setup

### 5a. Create the repository

Docker Hub must already have a repo named exactly `pr-decomposer` under your
account: <https://hub.docker.com/repositories/create>.

### 5b. Create an access token

Not your password. Go to
<https://hub.docker.com/settings/tokens> → **Generate new token** → scope
**read & write** → copy it. You only see it once.

### 5c. Give CI the credentials

```powershell
winget install --id GitHub.cli      # once
gh auth login
cd C:\Users\azerc\Desktop\MyReviewer\Do-AI-generated-PR-summary-improve-code-review
gh secret set DOCKERHUB_USERNAME
gh secret set DOCKERHUB_TOKEN
```

`DOCKERHUB_USERNAME` must match the namespace in the image name. If your Docker
Hub username differs from `azerchakir`, also edit `deploy/deploy_vm.sh`
(`DOCKERHUB_REPO`) and `prototype/docker-compose.yml` (the `image:` default).

### 5d. Publish

Push to `main` and the `Publish to Docker Hub` workflow runs: it requires CI to
pass, then tags and pushes `latest`, the full commit SHA, and a 7-char short
SHA.

```powershell
git push origin main
gh run watch        # or open the repo's Actions tab
```

Confirm the image exists: <https://hub.docker.com/r/azerchakir/pr-decomposer/tags>

---

## 6. First deploy on the VM

```bash
sudo mkdir -p /opt/pr-decomposer && sudo chown $USER /opt/pr-decomposer
cd /opt/pr-decomposer
git clone https://github.com/AzerChakir/myreviewer.git .
cd prototype

cp .env.example .env
$EDITOR .env
chmod 600 .env
```

`prototype/.env` needs:

| Key | Value |
|---|---|
| `NIM_API_KEY` | `nvapi-…` from <https://build.nvidia.com> |
| `GITHUB_APP_ENABLED` | `1` — **required**, or the post-install redirect never records the installation |
| `GITHUB_APP_ID` | the numeric App ID |
| `GITHUB_WEBHOOK_SECRET` | the secret you chose in GitHub's form |
| `GITHUB_PRIVATE_KEY_PATH` | **absolute** path to the downloaded `.pem` |
| `APP_URL` | `https://pr-decomposer.example.com` |
| `DATA_DIR` | `/app/data` (already set by `docker-compose.yml`) |

Then log in once and start:

```bash
docker login                        # Docker Hub username + access token
./../deploy/deploy_vm.sh            # pulls :latest, restarts, health-checks
```

Common mistakes:

- `GITHUB_PRIVATE_KEY_PATH` must be **absolute**
  (`/opt/pr-decomposer/prototype/private-key.pem`), not the relative
  `prototype/private-key.pem` from the example — the container's working
  directory is `/app`, so a relative path resolves elsewhere.
- Verify: `curl -fsS https://<domain>/api/health` → `"app_configured": true`.

---

## 7. Updating after a new push

```bash
cd /opt/pr-decomposer/prototype
git pull
../deploy/deploy_vm.sh              # or: ../deploy/deploy_vm.sh <tag>
```

`deploy_vm.sh` records the current image first, so a failed rollout rolls back
automatically. To pin a version:

```bash
DOCKERHUB_REPO=azerchakir/pr-decomposer ../deploy/deploy_vm.sh v1.0.0
```

---

## 8. Register the GitHub App

Now that the URL is reachable, register at
<https://github.com/settings/apps/new>:

| Field | Value |
|---|---|
| App name | e.g. `pr-decomposer` |
| Homepage / App URL | `https://pr-decomposer.example.com` |
| **Webhook URL** | `https://pr-decomposer.example.com/webhook` |
| **Setup URL** | `https://pr-decomposer.example.com/webhook` |
| Webhook secret | any long random string → same value in `GITHUB_WEBHOOK_SECRET` |

**Repository permissions**

| Permission | Access | Why |
|---|---|---|
| Metadata | Read-only | mandatory, auto-granted |
| Contents | Read-only | read the PR diff |
| Pull requests | **Read & write** | post/update review comments |
| Issues | **Read & write** | read `/review`, `/ask` comments |

**Events**: `Pull request`, `Issue comment`, `Installation`,
`Installation repositories`.

Then generate a private key on the App page (`.pem` — this is the key GitHub
issues; a locally generated RSA key will not work), copy it to the VM, point
`GITHUB_PRIVATE_KEY_PATH` at it, and:

```bash
cd /opt/pr-decomposer/prototype
docker compose up -d --force-recreate app
```

Click **Install App** on a repo, open a PR, and the bot comments with the
review. `/review`, `/ask` and `/help` work in PR comments.

If deliveries fail, check **App settings → Advanced → Recent deliveries**:
`401` = webhook secret mismatch, `404` = nginx isn't proxying `/webhook`.

---

## 9. Operating

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

## 10. Troubleshooting

| Symptom | Cause |
|---|---|
| `ssh` times out | NSG doesn't allow 22, or wrong IP. Check with `Test-NetConnection <ip> -Port 22` |
| `Permission denied (publickey)` | wrong username, or your public key isn't in `~/.ssh/authorized_keys` on the VM |
| `405` on `GET /webhook` | stale image — `./deploy_vm.sh` |
| `401` on every delivery | `GITHUB_WEBHOOK_SECRET` ≠ the secret in GitHub's App settings |
| `404` on `/webhook` behind the domain | nginx vhost not proxying; `sudo nginx -t && sudo systemctl reload nginx` |
| Redirect goes to `localhost:4200` | `APP_URL` unset/wrong, or `GITHUB_APP_ENABLED=0` |
| `app_configured: false` | `GITHUB_APP_ID` or the `.pem` path is wrong/unreadable in the container |
| Dashboard 404, API fine | the image predates single-service mode — rebuild and republish |
| `docker pull` fails 401 | `docker logout && docker login` on the VM with a valid access token |
| Container OOM-killed | add swap (bootstrap step 4) and/or raise the VM's RAM |
| GitHub: "URL not reachable" | DNS not propagated, NSG blocking 443, or untrusted cert — recheck §2 |

---

## 11. Security notes

- `prototype/.env` and `*.pem` are gitignored; **never** commit them. The image
  copies neither in (verified — no `nvapi-` or `PRIVATE KEY` material in any
  layer).
- The container runs as uid `10001`, not root, and the app port binds to
  `127.0.0.1` so only nginx can reach it.
- Use a **Docker Hub access token** scoped read/write, never your account
  password, and never store either in the repo.
- CI holds only that token. It cannot reach the VM, which is why deployment is a
  pull you trigger yourself.
- A previously used NVIDIA key was pasted into this project's session output.
  Rotate it at <https://build.nvidia.com> and update `.env` on the VM.
