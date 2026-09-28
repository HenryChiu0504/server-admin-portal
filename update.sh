#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo "請使用 sudo bash update.sh"; exit 1; }
DST=/opt/server-admin-portal
[[ -d "$DST/.git" ]] || { echo "$DST 不是 Git clone 目錄，請先使用 GitHub clone 部署。"; exit 1; }

cd "$DST"
# Allow root (this script, the Portal service) to use a checkout owned by the
# admin user; older git ignores `-c safe.directory`, so use the system config.
git config --system --get-all safe.directory 2>/dev/null | grep -qxF "$DST" || git config --system --add safe.directory "$DST"
# chmod +x below must not show up as local changes that block the next pull.
git -c safe.directory="$DST" config core.fileMode false
git -c safe.directory="$DST" pull --ff-only

python3 -m venv "$DST/.venv"
"$DST/.venv/bin/pip" install --upgrade pip
"$DST/.venv/bin/pip" install -r "$DST/requirements.txt"
chmod +x "$DST"/*.sh "$DST/tools"/*.sh

cp "$DST/server-admin-portal.service" /etc/systemd/system/server-admin-portal.service
cp "$DST/nginx-server-admin-portal.conf" /etc/nginx/sites-available/server-admin-portal
ln -sf /etc/nginx/sites-available/server-admin-portal /etc/nginx/sites-enabled/server-admin-portal
rm -f /etc/nginx/sites-enabled/default

# Older fan-control installs ordered nvidia-fan-x.service After=multi-user.target,
# so a stuck boot job (e.g. plymouth-quit-wait) kept it from starting. Fix the
# ordering in place; the running fan service is not restarted (applies next boot).
FAN_UNIT=/etc/systemd/system/nvidia-fan-x.service
if [[ -f "$FAN_UNIT" ]] && grep -q '^After=.*multi-user\.target' "$FAN_UNIT"; then
  cp -a "$FAN_UNIT" "${FAN_UNIT}.bak.$(date +%Y%m%d-%H%M%S)"
  sed -i 's/^After=.*multi-user\.target.*$/After=systemd-user-sessions.service systemd-modules-load.service nvidia-persistenced.service/' "$FAN_UNIT"
  echo "[FIX] 已修正 nvidia-fan-x.service 的開機順序（下次開機生效，未重啟風扇服務）"
fi

nginx -t
systemctl daemon-reload
systemctl restart server-admin-portal
systemctl reload nginx

echo "更新完成：$(git -c safe.directory="$DST" log --oneline -1 2>/dev/null || echo unknown)"
