#!/usr/bin/env bash
#
# Update the running app on the Azure VM by pulling the latest image from
# Docker Hub. This is the counterpart to .github/workflows/deploy.yml: CI
# pushes the image, this pulls it.
#
# Usage:
#   ./deploy_vm.sh              # pull :latest, restart, health check
#   ./deploy_vm.sh <tag>        # pull a specific tag, e.g. v1.0.0 or a sha
#
# Safe to re-run. Keeps the previous image around so a rollback is one command.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_DIR="${APP_DIR:-/opt/pr-decomposer/prototype}"
IMAGE_REPO="${DOCKERHUB_REPO:-azerchakir/pr-decomposer}"
TAG="${1:-latest}"
IMAGE="${IMAGE_REPO}:${TAG}"

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$1"; }
die() { printf '\033[1;31m[fatal] %s\033[0m\n' "$1" >&2; exit 1; }

[ -f "$APP_DIR/docker-compose.yml" ] \
    || die "no docker-compose.yml in $APP_DIR - is the repo cloned there?"

cd "$APP_DIR"

# ── remember the current image so rollback is possible ──────────────────────
PREVIOUS="$(docker inspect pr-decomposer \
    --format '{{.Config.Image}}' 2>/dev/null || echo '<none>')"
say "Currently running: $PREVIOUS"

# ── pull ────────────────────────────────────────────────────────────────────
say "Pulling $IMAGE"
if ! docker pull "$IMAGE"; then
    die "could not pull $IMAGE - check DOCKERHUB_REPO / tag, and that 'docker login' has been done"
fi

# ── restart ─────────────────────────────────────────────────────────────────
say "Restarting with $IMAGE"
PR_DECOMPOSER_IMAGE="$IMAGE" docker compose up -d --no-deps app

# ── wait for health ─────────────────────────────────────────────────────────
say "Health check"
ok=0
for i in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
        ok=1
        break
    fi
    printf '    waiting (%d/30)\r' "$i"
    sleep 5
done

if [ "$ok" -eq 1 ]; then
    echo
    curl -fsS http://127.0.0.1:8000/api/health; echo
    say "Deployed $IMAGE successfully"
    exit 0
fi

# ── failed: roll back ───────────────────────────────────────────────────────
echo
docker compose logs --tail 60 app || true
if [ "$PREVIOUS" != "<none>" ]; then
    say "Rolling back to $PREVIOUS"
    PR_DECOMPOSER_IMAGE="$PREVIOUS" docker compose up -d --no-deps app
else
    warn="No previous image to roll back to (first deploy)."
    printf '\033[1;33m[warn] %s\033[0m\n' "$warn"
fi
die "deployment failed"
