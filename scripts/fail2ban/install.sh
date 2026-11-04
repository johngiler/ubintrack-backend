#!/usr/bin/env bash
#
# Install Ubintrack fail2ban jail on the API host (production).
#
# Run ON the server (as root), from backend/:
#   sudo ./scripts/fail2ban/install.sh
#
# Or from your laptop:
#   ssh ubintrack-api 'cd /home/git/backend && sudo ./scripts/fail2ban/install.sh'
#
# What it does:
#   1. apt install fail2ban (if missing)
#   2. install filter + jail
#   3. enable/restart fail2ban
#   4. print jail status
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "ERROR: run as root (sudo $0)" >&2
  exit 1
fi

echo "[fail2ban] Installing package (if needed)…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq fail2ban

echo "[fail2ban] Installing filter + jail…"
install -d -m 0755 /etc/fail2ban/filter.d /etc/fail2ban/jail.d
install -m 0644 "$SCRIPT_DIR/filter.d/ubintrack-nginx.conf" \
  /etc/fail2ban/filter.d/ubintrack-nginx.conf
install -m 0644 "$SCRIPT_DIR/jail.d/ubintrack-api.local" \
  /etc/fail2ban/jail.d/ubintrack-api.local
install -m 0644 "$SCRIPT_DIR/jail.d/sshd-port.local" \
  /etc/fail2ban/jail.d/sshd-port.local

# Ensure sshd jail stays available; do not disable default jails globally.
if [[ ! -f /etc/fail2ban/jail.local ]]; then
  cat >/etc/fail2ban/jail.local <<'EOF'
[DEFAULT]
bantime  = 1h
findtime = 10m
maxretry = 5
backend  = auto
banaction = nftables-multiport
banaction_allports = nftables-allports
EOF
fi

echo "[fail2ban] Enabling service…"
systemctl enable fail2ban
systemctl restart fail2ban
sleep 1

echo "[fail2ban] Status:"
fail2ban-client status || true
echo
fail2ban-client status ubintrack-nginx || true

echo
echo "[fail2ban] Done."
echo "Useful:"
echo "  fail2ban-client status ubintrack-nginx"
echo "  fail2ban-client set ubintrack-nginx unbanip <IP>"
echo "  fail2ban-regex /var/log/nginx/access.log /etc/fail2ban/filter.d/ubintrack-nginx.conf"
