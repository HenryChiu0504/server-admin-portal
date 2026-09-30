#!/usr/bin/env bash
# 從 GitHub Container Registry 下載 GitHub Actions 建好的新版 image 並重啟容器。
# 可手動執行，或交給 NAS 排程每天跑。
#   ./update.sh           有新版才更新
#   ./update.sh --force   沒新版也重啟
# 新版啟動失敗（/health 沒回應）會自動退回上一版，而且之後不會再裝同一個壞掉的 image。
set -Eeuo pipefail

cd "$(dirname "$(readlink -f "$0")")"
BOT_DIR="$PWD"
PORT="${PORT:-13999}"
mkdir -p "$BOT_DIR/data"
LOG="$BOT_DIR/data/update.log"
log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

if docker compose version >/dev/null 2>&1; then DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then DC=(docker-compose)
else log "找不到 docker compose"; exit 1; fi

# Same default as docker-compose.yml; .env may override IMAGE.
IMAGE="ghcr.io/henrychiu0504/stock-gift-bot:latest"
if [[ -f .env ]] && grep -qE '^IMAGE=' .env; then IMAGE=$(grep -E '^IMAGE=' .env | tail -1 | cut -d= -f2- | tr -d '"'"'"); fi
REPO="${IMAGE%:*}"
PREVIOUS="$REPO:previous"

image_id() { docker image inspect -f '{{.Id}}' "$1" 2>/dev/null || true; }

OLD=$(image_id "$IMAGE")
if ! docker pull -q "$IMAGE" >/dev/null; then
  log "下載 $IMAGE 失敗（網路問題？private image 要先 docker login ghcr.io）"; exit 1
fi
NEW=$(image_id "$IMAGE")
BAD=$(cat "$BOT_DIR/data/.bad_image" 2>/dev/null || true)
RUNNING=$(docker inspect -f '{{.Image}}' stock-gift-bot 2>/dev/null || true)

if [[ "$NEW" == "$RUNNING" && "${1:-}" != "--force" ]]; then
  echo "已是最新版 ${NEW:7:12}"; exit 0
fi
if [[ "$NEW" == "$BAD" && "${1:-}" != "--force" ]]; then
  [[ -n "$OLD" ]] && docker tag "$OLD" "$IMAGE"   # keep running the good one
  log "跳過 ${NEW:7:12}：這個版本之前啟動失敗過"; exit 0
fi

deploy() {
  "${DC[@]}" up -d --no-build bot
  for _ in $(seq 1 30); do
    sleep 2
    curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && return 0
  done
  return 1
}

# Remember what was running so we can roll back.
GOOD="${RUNNING:-$OLD}"
[[ -n "$GOOD" ]] && docker tag "$GOOD" "$PREVIOUS"

log "更新 ${GOOD:7:12} -> ${NEW:7:12} ($IMAGE)"
if deploy; then
  VERSION=$(docker image inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$IMAGE" | sed -n 's/^APP_VERSION=//p')
  rm -f "$BOT_DIR/data/.bad_image"
  docker image prune -f >/dev/null 2>&1 || true
  log "更新完成：${VERSION:-${NEW:7:12}}"
elif [[ -n "$GOOD" ]]; then
  log "新版啟動失敗，退回上一版"
  echo "$NEW" > "$BOT_DIR/data/.bad_image"
  docker tag "$PREVIOUS" "$IMAGE"
  deploy && log "已退回上一版" || log "上一版也啟動失敗，請看 docker logs stock-gift-bot"
  exit 1
else
  log "啟動失敗，請看 docker logs stock-gift-bot"; exit 1
fi
