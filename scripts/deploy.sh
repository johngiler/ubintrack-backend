#!/usr/bin/env bash
# Deploy de aplicación: sincroniza código, deps, migraciones, estáticos y reinicia la API.
# Infraestructura (nginx, systemd, cron, certbot): ver scripts/setup.sh (una sola vez).

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REMOTE_HOST="ubintrack-api"
REMOTE_PATH="/home/git/backend"

RSYNC_EXCLUDE=(
  --exclude ".venv"
  --exclude "__pycache__"
  --exclude "*.pyc"
  --exclude ".env"
  --exclude ".env.local"
  --exclude ".env.production"
  --exclude "config/settings/local_settings.py"
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

echo "[deploy] Permisos, deps, migraciones y collectstatic..."
# Código bajo git:git; no tocar staticfiles/media aquí (siguen git:www-data entre deploys).
ssh "$REMOTE_HOST" "find $REMOTE_PATH -path $REMOTE_PATH/staticfiles -prune -o -path $REMOTE_PATH/media -prune -o -print0 | xargs -0 -r chown git:git"
# Nginx (www-data) debe poder atravesar el home de git (si no, 403 en /static/ del admin).
ssh "$REMOTE_HOST" "chmod 755 /home/git /home/git/backend"
ssh "$REMOTE_HOST" "cd $REMOTE_PATH && sudo -u git .venv/bin/pip install -q -r requirements.txt && sudo -u git .venv/bin/python manage.py migrate --noinput && sudo -u git .venv/bin/python manage.py collectstatic --noinput --clear 2>/dev/null || true"
# Estáticos y media: propietario git, grupo www-data (Nginx), setgid en dirs para que collectstatic deje g+r sin chmod o+rx.
ssh "$REMOTE_HOST" "for d in $REMOTE_PATH/staticfiles $REMOTE_PATH/media; do [ -d \"\$d\" ] || continue; chown -R git:www-data \"\$d\"; find \"\$d\" -type d -exec chmod 2775 {} \\;; find \"\$d\" -type f -exec chmod 664 {} \\;; done"

echo "[deploy] Reiniciando servicio API..."
ssh "$REMOTE_HOST" "set -e
  if ! systemctl list-unit-files ubintrack-api.service --no-legend 2>/dev/null | grep -q ubintrack-api; then
    echo '[deploy] ERROR: falta ubintrack-api.service en el servidor.' >&2
    echo '[deploy] Instálalo una vez con backend/scripts/setup.sh' >&2
    exit 1
  fi
  sudo systemctl restart ubintrack-api
  sudo systemctl is-active --quiet ubintrack-api
  echo '[deploy] ubintrack-api activo'
"

echo "[deploy] Done. https://api.ubintrack.com"
