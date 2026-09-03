#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo "請使用 sudo bash uninstall.sh"; exit 1; }

systemctl disable --now server-admin-portal 2>/dev/null || true
rm -f /etc/systemd/system/server-admin-portal.service
rm -f /etc/nginx/sites-enabled/server-admin-portal
rm -f /etc/nginx/sites-available/server-admin-portal
systemctl daemon-reload
nginx -t >/dev/null 2>&1 && systemctl reload nginx || true

echo "Portal service 已移除。"
echo "為避免誤刪資料，以下項目保留："
echo "  /opt/server-admin-portal"
echo "  /etc/server-admin-portal.env"
