#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo "請使用 sudo bash update.sh"; exit 1; }
DST=/opt/server-admin-portal
[[ -d "$DST/.git" ]] || { echo "$DST 不是 Git clone 目錄，請先使用 GitHub clone 部署。"; exit 1; }

cd "$DST"
git pull --ff-only

python3 -m venv "$DST/.venv"
"$DST/.venv/bin/pip" install --upgrade pip
"$DST/.venv/bin/pip" install -r "$DST/requirements.txt"
chmod +x "$DST"/*.sh "$DST/tools"/*.sh

cp "$DST/server-admin-portal.service" /etc/systemd/system/server-admin-portal.service
cp "$DST/nginx-server-admin-portal.conf" /etc/nginx/sites-available/server-admin-portal
ln -sf /etc/nginx/sites-available/server-admin-portal /etc/nginx/sites-enabled/server-admin-portal
rm -f /etc/nginx/sites-enabled/default

nginx -t
systemctl daemon-reload
systemctl restart server-admin-portal
systemctl reload nginx

echo "更新完成：$(cat VERSION 2>/dev/null || echo unknown)"
