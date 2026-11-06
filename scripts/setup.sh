#!/usr/bin/env bash

set -e

export DEBIAN_FRONTEND=noninteractive

echo "[setup] Updating apt..."
apt-get update -qq

echo "[setup] Installing system packages..."
apt-get install -y \
  postgresql postgresql-contrib \
  redis-server \
  nginx \
  certbot python3-certbot-nginx \
  python3 python3-venv python3-dev python3-pip \
  libpq-dev \
  git

echo "[setup] Enabling Redis (CHANNEL_LAYERS / campanita)..."
systemctl enable --now redis-server

echo "[setup] Ensuring user git and directory..."
id git 2>/dev/null || useradd -m -s /bin/bash git
mkdir -p /home/git/backend/media /home/git/backend/data /home/git/backend/logs
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
LOCAL_BACKEND="/Users/jcgiler/BlackCubeTech/Ubintrack/backend"
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
  --exclude 'config/settings/local_settings.production.py' \
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
2) Secretos de SO + Django producción (desde tu máquina)
──────────────────────────────────────────────────────────────────────────────
# SO / servicios (Postgres, Zebra, Redis) → .env del servidor
# Debe incluir REDIS_URL=redis://127.0.0.1:6379/1 (Channels; /1 evita choque con PortPax /0).
# Partir de .env.production.template si aún no tienes .env.production local.
scp "$LOCAL_BACKEND/.env.production" "${REMOTE_HOST}:${REMOTE_PATH}/.env"

# Django producción → local_settings.py del servidor
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
ssh "${REMOTE_HOST}" "sudo -u git bash -lc 'cd ${REMOTE_PATH} && .venv/bin/python manage.py migrate --noinput && .venv/bin/python manage.py seed_platform_data && .venv/bin/python manage.py ensure_default_workspace && .venv/bin/python manage.py collectstatic --noinput'"

ssh "${REMOTE_HOST}" "sudo chown -R git:www-data ${REMOTE_PATH}/staticfiles && sudo find ${REMOTE_PATH}/staticfiles -type d -exec chmod 2775 {} \\; && sudo find ${REMOTE_PATH}/staticfiles -type f -exec chmod 664 {} \\;"

──────────────────────────────────────────────────────────────────────────────
6) Nginx + systemd (una sola vez; DNS de api.ubintrack.com debe apuntar al host)
──────────────────────────────────────────────────────────────────────────────
scp "$LOCAL_BACKEND/scripts/nginx/api.ubintrack.com.conf" \
  "${REMOTE_HOST}:/tmp/api.ubintrack.com.conf"

ssh "${REMOTE_HOST}" "sudo cp /tmp/api.ubintrack.com.conf /etc/nginx/sites-available/api.ubintrack.com.conf && \
  sudo ln -sf /etc/nginx/sites-available/api.ubintrack.com.conf /etc/nginx/sites-enabled/api.ubintrack.com.conf && \
  sudo nginx -t && sudo systemctl reload nginx"

scp "$LOCAL_BACKEND/scripts/systemd/ubintrack-api.service" \
  "${REMOTE_HOST}:/tmp/ubintrack-api.service"
scp "$LOCAL_BACKEND/scripts/systemd/ubintrack-daphne.service" \
  "${REMOTE_HOST}:/tmp/ubintrack-daphne.service"

ssh "${REMOTE_HOST}" "sudo touch /var/log/daphne.log && sudo chown git:git /var/log/daphne.log && \
  sudo cp /tmp/ubintrack-api.service /etc/systemd/system/ubintrack-api.service && \
  sudo cp /tmp/ubintrack-daphne.service /etc/systemd/system/ubintrack-daphne.service && \
  sudo systemctl daemon-reload && \
  sudo systemctl enable --now ubintrack-api ubintrack-daphne"

# TLS (solo cuando el vhost responde en HTTP y el DNS está propagado)
ssh "${REMOTE_HOST}" "sudo certbot --nginx -d api.ubintrack.com"

──────────────────────────────────────────────────────────────────────────────
7) Cron de tareas (refresh dashboard; una sola vez, o al cambiar el crontab)
──────────────────────────────────────────────────────────────────────────────
scp "$LOCAL_BACKEND/scripts/crond/ubintrack-api.crontab" \
  "${REMOTE_HOST}:/tmp/ubintrack-api.crontab"

ssh "${REMOTE_HOST}" "sudo -u git mkdir -p ${REMOTE_PATH}/logs && \
  sudo cp /tmp/ubintrack-api.crontab /etc/cron.d/ubintrack-api && \
  sudo chmod 644 /etc/cron.d/ubintrack-api"

──────────────────────────────────────────────────────────────────────────────
Deploys siguientes (solo código y reinicio de API; ver scripts/deploy.sh)
──────────────────────────────────────────────────────────────────────────────
cd "$LOCAL_BACKEND" && ./scripts/deploy.sh

Nota: deploy.sh sincroniza el código Django, instala deps, migra, collectstatic y
      reinicia ubintrack-api (y pregunta si reiniciar ubintrack-daphne y ubintrack-celery). No toca nginx,
      systemd units, cron, fail2ban ni tmux. Si cambias esos archivos, vuelve a
      copiarlos aquí y ejecuta daemon-reload / reload nginx según corresponda.

──────────────────────────────────────────────────────────────────────────────
Ops (tmux / btop / fail2ban / nginx hardening) — una sola vez en el API host
──────────────────────────────────────────────────────────────────────────────
# Paquetes + fail2ban jail:
ssh "${REMOTE_HOST}" "apt-get update -qq && apt-get install -y -qq tmux btop ccze fail2ban"
ssh "${REMOTE_HOST}" "cd ${REMOTE_PATH} && sudo ./scripts/fail2ban/install.sh"

# tmux ops dashboard (gunicorn arriba; celery | daphne | btop abajo):
scp "$LOCAL_BACKEND/scripts/systemd/tmux.service" "${REMOTE_HOST}:/tmp/tmux.service"
ssh "${REMOTE_HOST}" "sudo cp /tmp/tmux.service /etc/systemd/system/tmux.service && \
  sudo systemctl daemon-reload && sudo systemctl enable --now tmux.service"
# Tras cambiar tmux_ops.sh o el unit: sudo systemctl restart tmux.service

# Nginx maps + vhost endurecido (444 / rate limit / UA drop + /ws/ → Daphne):
scp "$LOCAL_BACKEND/scripts/nginx/conf.d/ubintrack-api-maps.conf" \
  "${REMOTE_HOST}:/tmp/ubintrack-api-maps.conf"
scp "$LOCAL_BACKEND/scripts/nginx/api.ubintrack.com.conf" \
  "${REMOTE_HOST}:/tmp/api.ubintrack.com.conf"
ssh "${REMOTE_HOST}" "sudo cp /tmp/ubintrack-api-maps.conf /etc/nginx/conf.d/ubintrack-api-maps.conf && \
  sudo cp /tmp/api.ubintrack.com.conf /etc/nginx/sites-available/api.ubintrack.com.conf && \
  sudo nginx -t && sudo systemctl reload nginx"
# Si el vhost ya tiene bloques SSL de Certbot, no pises a ciegas: fusiona
# location /ws/ en el server listen 443 (como en el template del repo).

# Attach: ssh -t ubintrack-api 'sudo -u git tmux attach -t ubintrack-ops'

EOF
