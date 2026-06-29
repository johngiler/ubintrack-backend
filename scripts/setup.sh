#!/usr/bin/env bash

set -e

export DEBIAN_FRONTEND=noninteractive

echo "[setup] Updating apt..."
apt-get update -qq

echo "[setup] Installing system packages..."
apt-get install -y \
  postgresql postgresql-contrib \
  nginx \
  certbot python3-certbot-nginx \
  python3 python3-venv python3-dev python3-pip \
  libpq-dev \
  git

echo "[setup] Ensuring user git and directory..."
id git 2>/dev/null || useradd -m -s /bin/bash git
mkdir -p /home/git/backend /home/git/backend/media
mkdir -p /home/git/backend /home/git/backend/data
mkdir -p /home/git/backend/logs
chown -R git:git /home/git
chmod 755 /home/git /home/git/backend
chown git:www-data /home/git/backend/media
chown git:www-data /home/git/backend/data
chmod 2775 /home/git/backend/media /home/git/backend/data
usermod -a -G www-data git 2>/dev/null || true

echo "[setup] Allowing nginx to read letsencrypt challenges..."
mkdir -p /var/www/letsencrypt
chown -R www-data:www-data /var/www/letsencrypt

cat <<'EOF'

[setup] Done. Siguientes pasos (ejecutar desde tu Mac / máquina de desarrollo).

Requisito: en ~/.ssh/config debe existir el host «ubintrack-api» apuntando al servidor.
Ajusta LOCAL_BACKEND si tu repo no está en la ruta por defecto.

──────────────────────────────────────────────────────────────────────────────
0) Variables (opcional, para pegar bloques siguientes)
──────────────────────────────────────────────────────────────────────────────
LOCAL_BACKEND="/Users/jcgiler/BlackCubeTech/Publivalla/backend"
REMOTE_HOST="ubintrack-api"
REMOTE_PATH="/home/git/backend"

──────────────────────────────────────────────────────────────────────────────
1) Rsync inicial del código (sin --delete; no pisa .env ni local_settings del servidor)
──────────────────────────────────────────────────────────────────────────────
cd "$LOCAL_BACKEND"

rsync -avz \
  --exclude '.venv' \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.env' \
  --exclude '.env.local' \
  --exclude '.env.production' \
  --exclude 'config/settings/local_settings.py' \
  --exclude 'db.sqlite3' \
  --exclude 'staticfiles' \
  --exclude 'static' \
  --exclude 'media' \
  --exclude 'data' \
  --exclude '.git' \
  --exclude '.DS_Store' \
  --exclude '*.log' \
  -e ssh ./ "${REMOTE_HOST}:${REMOTE_PATH}/"

ssh "${REMOTE_HOST}" "sudo chown -R git:git ${REMOTE_PATH} && sudo chmod 755 /home/git /home/git/backend && sudo chown git:www-data ${REMOTE_PATH}/media ${REMOTE_PATH}/data 2>/dev/null || true && sudo chmod 2775 ${REMOTE_PATH}/media ${REMOTE_PATH}/data 2>/dev/null || true"

──────────────────────────────────────────────────────────────────────────────
2) Secretos y settings de producción (desde tu máquina)
──────────────────────────────────────────────────────────────────────────────
# .env de producción (revisa valores antes de subir)
scp "$LOCAL_BACKEND/.env.production" "${REMOTE_HOST}:${REMOTE_PATH}/.env"

# Overrides Django (plantilla versionada en el repo)
scp "$LOCAL_BACKEND/config/settings/local_settings.production.py" \
  "${REMOTE_HOST}:${REMOTE_PATH}/config/settings/local_settings.py"

ssh "${REMOTE_HOST}" "sudo chown git:git ${REMOTE_PATH}/.env ${REMOTE_PATH}/config/settings/local_settings.py && sudo chmod 640 ${REMOTE_PATH}/.env"

──────────────────────────────────────────────────────────────────────────────
3) Crear usuario y base Postgres (en el servidor; lee POSTGRES_* del .env)
──────────────────────────────────────────────────────────────────────────────
ssh "${REMOTE_HOST}" "sudo bash ${REMOTE_PATH}/scripts/init_db.sh"

──────────────────────────────────────────────────────────────────────────────
4) Venv e dependencias Python (usuario git en el servidor)
──────────────────────────────────────────────────────────────────────────────
ssh "${REMOTE_HOST}" "sudo -u git bash -lc 'cd ${REMOTE_PATH} && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt'"

──────────────────────────────────────────────────────────────────────────────
5) Migraciones, workspace por defecto y estáticos del admin
──────────────────────────────────────────────────────────────────────────────
ssh "${REMOTE_HOST}" "sudo -u git bash -lc 'cd ${REMOTE_PATH} && .venv/bin/python manage.py migrate --noinput && .venv/bin/python manage.py ensure_default_workspace && .venv/bin/python manage.py collectstatic --noinput'"

ssh "${REMOTE_HOST}" "sudo chown -R git:www-data ${REMOTE_PATH}/staticfiles && sudo find ${REMOTE_PATH}/staticfiles -type d -exec chmod 2775 {} \\; && sudo find ${REMOTE_PATH}/staticfiles -type f -exec chmod 664 {} \\;"

──────────────────────────────────────────────────────────────────────────────
6) Nginx + systemd (en el servidor; DNS de api.ubintrack.com debe apuntar al host)
──────────────────────────────────────────────────────────────────────────────
ssh "${REMOTE_HOST}" "sudo cp ${REMOTE_PATH}/scripts/nginx/api.ubintrack.com.conf /etc/nginx/sites-available/api.ubintrack.com.conf && sudo ln -sf /etc/nginx/sites-available/api.ubintrack.com.conf /etc/nginx/sites-enabled/api.ubintrack.com.conf && sudo nginx -t && sudo systemctl reload nginx"

ssh "${REMOTE_HOST}" "sudo cp ${REMOTE_PATH}/scripts/systemd/ubintrack-api.service /etc/systemd/system/ && sudo cp ${REMOTE_PATH}/scripts/systemd/ubintrack-celery.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now ubintrack-api ubintrack-celery"

# TLS (solo cuando el vhost responde en HTTP y el DNS está propagado)
ssh "${REMOTE_HOST}" "sudo certbot --nginx -d api.ubintrack.com"

──────────────────────────────────────────────────────────────────────────────
7) Cron de tareas (hold 72h, vencimiento de contratos, etc.)
──────────────────────────────────────────────────────────────────────────────
ssh "${REMOTE_HOST}" "sudo -u git mkdir -p ${REMOTE_PATH}/logs && sudo cp ${REMOTE_PATH}/scripts/crond/ubintrack-backend.crontab /etc/cron.d/ubintrack-backend && sudo chmod 644 /etc/cron.d/ubintrack-backend"

──────────────────────────────────────────────────────────────────────────────
Deploys siguientes (solo código; usa --delete; ver scripts/deploy.sh)
──────────────────────────────────────────────────────────────────────────────
cd "$LOCAL_BACKEND" && ./scripts/deploy.sh

EOF
