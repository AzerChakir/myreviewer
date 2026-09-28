#!/usr/bin/env bash
#
# One-shot bootstrap for the Azure VM that hosts the PR Decomposer.
#
# Run as a user with sudo (e.g. `ssh azureuser@<vm-ip> 'bash bootstrap_vm.sh'`).
# Safe to re-run: every step checks before it acts.
#
# After this finishes you still need to:
#   1. point your domain's A record at this VM's public IP (wait for it to resolve)
#   2. run:  sudo certbot --nginx -d <your-domain>
#   3. clone the repo, create prototype/.env, and: docker compose up -d --build
#
set -euo pipefail

DOMAIN="${1:-}"
APP_USER="${APP_USER:-$(id -un)}"
APP_DIR="${APP_DIR:-/opt/pr-decomposer}"

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$1"; }
warn() { printf '\033[1;33m[warn] %s\033[0m\n' "$1"; }
die() { printf '\033[1;31m[fatal] %s\033[0m\n' "$1" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "Run with sudo: sudo bash $0 <your-domain>"

# ── 1. Docker ────────────────────────────────────────────────────────────────
say "Docker"
if command -v docker >/dev/null 2>&1; then
    echo "    already installed: $(docker --version)"
else
    echo "    installing from the official Docker apt repository..."
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc]" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io \
        docker-buildx-plugin docker-compose-plugin
    echo "    installed: $(docker --version)"
fi

# ── 2. nginx + certbot ───────────────────────────────────────────────────────
say "nginx and certbot"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq nginx certbot python3-certbot-nginx curl
echo "    nginx: $(nginx -v 2>&1), certbot: $(certbot --version 2>&1)"

# ── 3. Swap ──────────────────────────────────────────────────────────────────
# Webhook analysis requests can be long-running. Without swap a small VM
# (1-2 GB) gets OOM-killed mid-analysis.
say "Swap"
if [ "$(swapon --show | wc -l)" -eq 0 ]; then
    fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
    swapon /swapfile
    grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo "    2G swap enabled"
else
    echo "    swap already present"
fi

# ── 4. App directory ─────────────────────────────────────────────────────────
say "App directory"
mkdir -p "$APP_DIR"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
echo "    $APP_DIR owned by $APP_USER"

# ── 5. Docker permissions ────────────────────────────────────────────────────
say "Docker group"
if getent group docker >/dev/null; then
    usermod -aG docker "$APP_USER"
    echo "    $APP_USER added to the docker group (re-login for it to take effect)"
else
    warn "no docker group found; the deploy workflow runs as root or via ssh-agent"
fi

# ── 6. nginx site ────────────────────────────────────────────────────────────
if [ -n "$DOMAIN" ]; then
    say "nginx vhost for $DOMAIN"
    SITE_SRC="$(dirname "$0")/nginx/pr-decomposer.conf"
    if [ -f "$SITE_SRC" ]; then
        sed "s/<your-domain>/$DOMAIN/g" "$SITE_SRC" > /etc/nginx/sites-available/pr-decomposer
        ln -sf /etc/nginx/sites-available/pr-decomposer /etc/nginx/sites-enabled/
        rm -f /etc/nginx/sites-enabled/default
        # TLS certs do not exist yet; serve HTTP only until certbot runs.
        if ! nginx -t 2>/dev/null; then
            warn "TLS cert missing, so the :443 server block cannot load yet."
            warn "Run:  sudo certbot --nginx -d $DOMAIN   (after DNS resolves)"
            # Drop the 443 block so nginx can start and certbot can complete.
            python3 - "$DOMAIN" <<'PY'
import re, sys
p = "/etc/nginx/sites-available/pr-decomposer"
s = open(p, encoding="utf-8").read()
s = re.sub(r"server \{[^@]*?listen 443.*?\n\}\n", "", s, flags=re.S)
open(p, "w", encoding="utf-8").write(s)
PY
        fi
        nginx -t && systemctl reload nginx
        echo "    vhost installed (HTTP only for now)"
    else
        warn "config not found next to this script: $SITE_SRC"
    fi
else
    warn "no domain given; skipping the nginx vhost."
    warn "Usage: sudo bash $0 your-domain.com"
fi

# ── 7. Firewall ──────────────────────────────────────────────────────────────
say "UFW"
ufw allow OpenSSH >/dev/null 2>&1 || true
ufw allow 'Nginx Full' >/dev/null 2>&1 || true
ufw --force enable >/dev/null 2>&1 || warn "ufw unavailable; check Azure NSG instead"
echo "    allowed: 22 (ssh), 80 (http), 443 (https)"

say "Done"
cat <<EOF

Next steps on this VM:

  1. DNS: make sure <your-domain> has an A record pointing to this VM's public IP,
     and that it resolves from the internet:
         dig +short <your-domain>

  2. TLS (REQUIRED - GitHub rejects webhooks without a trusted certificate):
         sudo certbot --nginx -d <your-domain> --redirect

  3. Deploy the app:
         cd $APP_DIR
         git clone <your-repo-url> .
         cd prototype
         cp .env.example .env && nano .env     # NIM_API_KEY + GitHub App values
         chmod 600 .env
         docker compose up -d --build

  4. Verify:
         curl -fsS https://<your-domain>/api/health
         curl -I  https://<your-domain>/webhook

EOF
