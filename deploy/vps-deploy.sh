#!/usr/bin/env bash
# Pull-based CD for MarketMind on the VPS (run by systemd timer or GitHub Actions).
#   - No-op when origin/main is unchanged AND the stack is already running.
#   - --force rebuilds and restarts even when up-to-date.
set -euo pipefail
cd "$(dirname "$0")/.."   # repo root (/opt/marketmind)

FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

git fetch origin main
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)
RUNNING=$(docker compose ps -q marketmind 2>/dev/null || true)

if [ "$LOCAL" = "$REMOTE" ] && [ -n "$RUNNING" ] && [ "$FORCE" -eq 0 ]; then
  echo "[vps-deploy] up-to-date at ${LOCAL:0:7}; nothing to do"
  exit 0
fi

echo "[vps-deploy] deploying ${REMOTE:0:7} (was ${LOCAL:0:7})"
git reset --hard origin/main
docker compose build
docker compose up -d --remove-orphans
docker image prune -f >/dev/null 2>&1 || true

sleep 6
for _ in 1 2 3 4 5; do
  if curl -fsS http://127.0.0.1:8787/healthz >/dev/null 2>&1; then
    echo "[vps-deploy] OK at $(git rev-parse --short HEAD)"
    exit 0
  fi
  sleep 4
done

echo "[vps-deploy] healthcheck FAILED — recent logs:"
docker compose logs --tail=60 marketmind || true
exit 1
