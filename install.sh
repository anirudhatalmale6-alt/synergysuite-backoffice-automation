#!/usr/bin/env bash
# Sets up the TSC automation box. Safe to re-run.
set -euo pipefail

APP=/opt/tsc

echo "== timezone =="
timedatectl set-timezone America/Chicago

echo "== packages =="
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip cron unattended-upgrades >/dev/null

echo "== app dir =="
mkdir -p "$APP/logs"
chmod 700 "$APP"

echo "== python =="
if [ ! -d "$APP/venv" ]; then
  python3 -m venv "$APP/venv"
fi
"$APP/venv/bin/pip" install -q --upgrade pip
"$APP/venv/bin/pip" install -q playwright openpyxl
"$APP/venv/bin/playwright" install --with-deps chromium

echo "== permissions =="
chmod 600 "$APP/CREDENTIALS.txt"
chmod 700 "$APP"/*.py

echo "== ssh hardening =="
sed -i 's/^#\?PasswordAuthentication .*/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/^#\?PermitRootLogin .*/PermitRootLogin prohibit-password/' /etc/ssh/sshd_config
systemctl reload ssh || systemctl reload sshd

echo "== cron =="
crontab "$APP/crontab.txt"
crontab -l

echo "== log cleanup (keep 30 days) =="
cat > /etc/cron.daily/tsc-logrotate <<'EOF'
#!/bin/sh
find /opt/tsc/logs -type f -name '*.log' -mtime +30 -delete
EOF
chmod +x /etc/cron.daily/tsc-logrotate

echo
echo "done. next: $APP/venv/bin/python $APP/run_job.py live_sales"
