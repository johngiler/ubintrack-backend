#!/usr/bin/env bash
#
# Deploy Ubintrack backend to api.ubintrack.com (ubintrack-api).
# Requires: rsync, SSH config Host ubintrack-api -> api.ubintrack.com, root on remote.
# Target: /home/git/backend (gunicorn via ubintrack-api.service).
#
# Infraestructura (nginx, systemd, cron, certbot): ver scripts/setup.sh (una sola vez).
#
# Server-only files (never overwritten by rsync):
#   .env, .env.local, .env.production, config/settings/local_settings.py,
#   config/settings/local_settings.production.py, .venv, db.sqlite3,
#   media/, data/, staticfiles/, static/
#
# Usage:
#   ./scripts/deploy.sh              # full deploy (sync + migrate + restart)
#   ./scripts/deploy.sh --sync-only  # rsync + chown only (no migrate / collectstatic / restart)
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REMOTE_HOST="ubintrack-api"
REMOTE_PATH="/home/git/backend"
SYNC_ONLY=0

for arg in "$@"; do
  case "$arg" in
    --sync-only) SYNC_ONLY=1 ;;
    -h|--help)
      echo "Usage: $0 [--sync-only]"
      echo "  --sync-only   rsync + chown only (skip migrate, collectstatic, service restart)"
      exit 0
      ;;
    *)
      echo "Unknown option: $arg" >&2
      echo "Usage: $0 [--sync-only]" >&2
      exit 2
      ;;
  esac
done

RSYNC_EXCLUDE=(
  --exclude ".venv"
  --exclude "__pycache__"
  --exclude "*.pyc"
  --exclude ".env"
  --exclude ".env.local"
  --exclude ".env.production"
  --exclude "config/settings/local_settings.py"
  --exclude "config/settings/local_settings.production.py"
  --exclude "db.sqlite3"
  --exclude "staticfiles"
  --exclude "static"
  --exclude "media"
  --exclude "data"
  --exclude ".git"
  --exclude ".DS_Store"
  --exclude "*.log"
)

cd "$BACKEND_DIR"

echo "[deploy] Syncing backend -> $REMOTE_HOST:$REMOTE_PATH"
rsync -avz --delete "${RSYNC_EXCLUDE[@]}" -e ssh "$BACKEND_DIR/" "$REMOTE_HOST:$REMOTE_PATH/"

echo "[deploy] Fixing ownership on remote..."
# Código bajo git:git; no tocar staticfiles/media aquí (siguen git:www-data entre deploys).
ssh "$REMOTE_HOST" "find $REMOTE_PATH -path $REMOTE_PATH/staticfiles -prune -o -path $REMOTE_PATH/media -prune -o -print0 | xargs -0 -r chown git:git"
# Nginx (www-data) debe poder atravesar el home de git (si no, 403 en /static/ del admin).
ssh "$REMOTE_HOST" "chmod 755 /home/git /home/git/backend"

if [[ "$SYNC_ONLY" -eq 1 ]]; then
  echo "[deploy] Sync-only done (skipped migrate / collectstatic / restart)."
  exit 0
fi

REMOTE_SETUP="
set -e
cd $REMOTE_PATH

if [[ ! -f .env ]]; then
  echo 'ERROR: $REMOTE_PATH/.env missing on the server.' >&2
  exit 1
fi
if [[ ! -f config/settings/local_settings.py ]]; then
  echo 'ERROR: config/settings/local_settings.py missing. Copy local_settings.production.py on the server.' >&2
  exit 1
fi

if [[ ! -d .venv ]]; then
  echo '[deploy] Creating Python venv...'
  sudo -u git python3 -m venv .venv
fi

sudo -u git .venv/bin/pip install -q -r requirements.txt
sudo -u git .venv/bin/python manage.py check
sudo -u git .venv/bin/python manage.py migrate --noinput
sudo -u git .venv/bin/python manage.py collectstatic --noinput --clear 2>/dev/null || true
"

echo "[deploy] Remote: venv, check, migrate, collectstatic..."
ssh "$REMOTE_HOST" "$REMOTE_SETUP"

# Estáticos y media: propietario git, grupo www-data (Nginx), setgid en dirs.
ssh "$REMOTE_HOST" "for d in $REMOTE_PATH/staticfiles $REMOTE_PATH/media; do [ -d \"\$d\" ] || continue; chown -R git:www-data \"\$d\"; find \"\$d\" -type d -exec chmod 2775 {} \\;; find \"\$d\" -type f -exec chmod 664 {} \\;; done"

echo "[deploy] Restarting ubintrack-api.service (gunicorn)..."
if ssh "$REMOTE_HOST" "systemctl is-enabled ubintrack-api.service >/dev/null 2>&1"; then
  ssh "$REMOTE_HOST" "systemctl restart ubintrack-api.service"
  ssh "$REMOTE_HOST" "systemctl is-active --quiet ubintrack-api.service"
  echo "[deploy] ubintrack-api activo"
else
  echo "[deploy] ERROR: falta ubintrack-api.service en el servidor." >&2
  echo "[deploy] Instálalo una vez con backend/scripts/setup.sh" >&2
  exit 1
fi

# Daphne — optional restart (default: no). Needed when consumer/ASGI code changes.
# || true: con stdin no-TTY (pipes/CI), read falla y set -e abortaría el deploy.
RESTART_DAPHNE=""
read -r -p "[deploy] ¿Reiniciar daphne (WebSockets / campanita)? [y/N] " RESTART_DAPHNE || true
if [[ "${RESTART_DAPHNE}" =~ ^[yY]$ ]]; then
  echo "[deploy] Restarting ubintrack-daphne.service..."
  if ssh "$REMOTE_HOST" "systemctl is-enabled ubintrack-daphne.service >/dev/null 2>&1"; then
    ssh "$REMOTE_HOST" "systemctl restart ubintrack-daphne.service"
    ssh "$REMOTE_HOST" "systemctl is-active --quiet ubintrack-daphne.service"
    echo "[deploy] ubintrack-daphne activo"
  else
    echo "[deploy] WARN: ubintrack-daphne.service not installed yet."
    echo "scp scripts/systemd/ubintrack-daphne.service ubintrack-api:/tmp/"
    echo "ssh ubintrack-api 'cp /tmp/ubintrack-daphne.service /etc/systemd/system/ && systemctl daemon-reload && systemctl enable --now ubintrack-daphne'"
  fi
else
  echo "[deploy] Daphne left running (no restart)."
fi

# Celery — optional restart (default: no). Needed when task code changes.
# || true: con stdin no-TTY (pipes/CI), read falla y set -e abortaría el deploy.
RESTART_CELERY=""
read -r -p "[deploy] ¿Reiniciar celery? [y/N] " RESTART_CELERY || true
if [[ "${RESTART_CELERY}" =~ ^[yY]$ ]]; then
  echo "[deploy] Restarting ubintrack-celery.service..."
  if ssh "$REMOTE_HOST" "systemctl is-enabled ubintrack-celery.service >/dev/null 2>&1"; then
    ssh "$REMOTE_HOST" "systemctl restart ubintrack-celery.service"
    ssh "$REMOTE_HOST" "systemctl is-active --quiet ubintrack-celery.service"
    echo "[deploy] ubintrack-celery activo"
  else
    echo "[deploy] WARN: ubintrack-celery.service not installed yet."
    echo "scp scripts/systemd/ubintrack-celery.service ubintrack-api:/tmp/"
    echo "ssh ubintrack-api 'cp /tmp/ubintrack-celery.service /etc/systemd/system/ && systemctl daemon-reload && systemctl enable --now ubintrack-celery'"
  fi
else
  echo "[deploy] Celery left running (no restart)."
fi

if ssh "$REMOTE_HOST" "systemctl is-active nginx >/dev/null 2>&1"; then
  ssh "$REMOTE_HOST" "systemctl reload nginx" || echo "[deploy] WARN: nginx reload failed"
fi

echo "[deploy] Done. https://api.ubintrack.com"
