#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo "請使用 sudo $0"; exit 1; }

CONTAINER="${APACHE_CONTAINER:-k8s-webserver}"
PATH_PREFIX="${PORTAL_PATH:-/tool}"
UPSTREAM_PORT="${PORTAL_UPSTREAM_PORT:-8088}"

command -v docker >/dev/null || { echo "找不到 docker"; exit 1; }
docker inspect "$CONTAINER" >/dev/null 2>&1 || { echo "找不到 container: $CONTAINER"; exit 1; }

GATEWAY="$(docker inspect "$CONTAINER" --format '{{range $name,$net := .NetworkSettings.Networks}}{{$net.Gateway}}{{println}}{{end}}' | head -n1 | tr -d '[:space:]')"
[[ -n "$GATEWAY" ]] || { echo "無法偵測 Docker network gateway"; exit 1; }

VHOST_DIR="$(docker inspect "$CONTAINER" --format '{{range .Mounts}}{{if eq .Destination "/etc/apache2/sites-enabled"}}{{.Source}}{{end}}{{end}}')"
[[ -n "$VHOST_DIR" && -d "$VHOST_DIR" ]] || { echo "找不到 /etc/apache2/sites-enabled 的 bind mount"; exit 1; }

CONF="$VHOST_DIR/default.conf"
[[ -f "$CONF" ]] || { echo "找不到 $CONF"; exit 1; }
cp -a "$CONF" "$CONF.bak.$(date +%Y%m%d-%H%M%S)"

docker exec "$CONTAINER" a2enmod proxy proxy_http >/dev/null

python3 - "$CONF" "$PATH_PREFIX" "$GATEWAY" "$UPSTREAM_PORT" <<'PY'
from pathlib import Path
import re, sys
conf=Path(sys.argv[1]); path=sys.argv[2].rstrip('/'); gateway=sys.argv[3]; port=sys.argv[4]
text=conf.read_text()
start='# BEGIN SERVER-ADMIN-PORTAL'
end='# END SERVER-ADMIN-PORTAL'
block=f'''{start}\n    RedirectMatch 302 ^{re.escape(path)}$ {path}/\n    ProxyPreserveHost On\n    ProxyPass        {path}/ http://{gateway}:{port}/\n    ProxyPassReverse {path}/ http://{gateway}:{port}/\n{end}'''
if start in text and end in text:
    text=re.sub(re.escape(start)+r'.*?'+re.escape(end), block, text, flags=re.S)
else:
    pos=text.rfind('</VirtualHost>')
    if pos < 0:
        raise SystemExit('default.conf 找不到 </VirtualHost>')
    text=text[:pos]+'\n    '+block.replace('\n','\n    ')+'\n'+text[pos:]
conf.write_text(text)
PY

docker exec "$CONTAINER" apache2ctl configtest
docker restart "$CONTAINER" >/dev/null

echo "完成：${PATH_PREFIX}/ -> http://${GATEWAY}:${UPSTREAM_PORT}/"
echo "請測試：http://<SERVER_IP>${PATH_PREFIX}/"
echo "注意：若未來 docker compose 重新建立 container，Apache proxy modules 可能需要重新執行此腳本啟用。"
