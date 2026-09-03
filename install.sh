#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo "請使用 sudo bash install.sh"; exit 1; }
SRC="$(cd "$(dirname "$0")" && pwd)"
DST=/opt/server-admin-portal

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip nginx curl

# 支援直接從 /opt/server-admin-portal 原地安裝，也支援從其他目錄部署。
# 不可在 SRC == DST 時刪除目的目錄，否則會把安裝檔本身一起刪掉。
if [[ "$SRC" != "$DST" ]]; then
  mkdir -p "$DST"
  cp -a "$SRC"/. "$DST"/
fi
rm -rf "$DST/.venv"
python3 -m venv "$DST/.venv"
"$DST/.venv/bin/pip" install --upgrade pip
"$DST/.venv/bin/pip" install -r "$DST/requirements.txt"
chmod +x "$DST/tools"/*.sh

if [[ ! -f /etc/server-admin-portal.env ]]; then
  PASS="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(12))
PY
)"
  SECRET="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
)"
  cat >/etc/server-admin-portal.env <<EOF
ADMIN_PASSWORD=$PASS
SESSION_SECRET=$SECRET
PORT=8787
BASE_PATH=/tool
DEFAULT_LINUX_PASSWORD=
EOF
  chmod 600 /etc/server-admin-portal.env
  echo
  echo "=============================================="
  echo "首次管理密碼：$PASS"
  echo "請登入後立即修改 /etc/server-admin-portal.env"
  echo "=============================================="
fi

# Add newer optional settings to an existing environment file without overwriting secrets.
if ! grep -q '^BASE_PATH=' /etc/server-admin-portal.env; then
  echo 'BASE_PATH=/tool' >> /etc/server-admin-portal.env
fi
if ! grep -q '^DEFAULT_LINUX_PASSWORD=' /etc/server-admin-portal.env; then
  echo 'DEFAULT_LINUX_PASSWORD=' >> /etc/server-admin-portal.env
fi

cp "$DST/server-admin-portal.service" /etc/systemd/system/server-admin-portal.service
cp "$DST/nginx-server-admin-portal.conf" /etc/nginx/sites-available/server-admin-portal
ln -sf /etc/nginx/sites-available/server-admin-portal /etc/nginx/sites-enabled/server-admin-portal
# Portal 僅使用 8088；移除 Ubuntu nginx 預設的 port 80 site，避免與既有 Docker Web Server 衝突。
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl daemon-reload
systemctl enable --now server-admin-portal
systemctl enable --now nginx
systemctl reload nginx

echo
echo "部署完成"
echo "後端入口：http://<伺服器IP>:8088"
echo "若使用 Docker Apache 整合，可再執行：sudo $DST/tools/setup-docker-apache-tool.sh"
echo "狀態：systemctl status server-admin-portal --no-pager"
