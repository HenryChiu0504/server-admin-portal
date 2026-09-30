#!/usr/bin/env bash
# 從 GitHub 拉新版並重建容器。可手動執行，或交給 NAS 排程每天跑。
#   ./update.sh           有新版才更新
#   ./update.sh --force   沒新版也重建
# 新版啟動失敗（/health 沒回應）會自動退回上一版，而且之後不會再裝同一個壞掉的版本。
set -Eeuo pipefail

BRANCH="${BRANCH:-main}"          # 要追蹤的分支，例如 BRANCH=main ./update.sh
PORT="${PORT:-13999}"
cd "$(dirname "$(readlink -f "$0")")"
BOT_DIR="$PWD"
LOG="$BOT_DIR/data/update.log"
mkdir -p "$BOT_DIR/data"
log() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

if docker compose version >/dev/null 2>&1; then DC=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then DC=(docker-compose)
else log "找不到 docker compose"; exit 1; fi

git config --global --get-all safe.directory 2>/dev/null | grep -qxF "$(git rev-parse --show-toplevel)" \
  || git config --global --add safe.directory "$(git rev-parse --show-toplevel)"

git fetch --quiet origin "$BRANCH"
OLD=$(git rev-parse HEAD)
NEW=$(git rev-parse "origin/$BRANCH")
BAD=$(cat "$BOT_DIR/data/.bad_commit" 2>/dev/null || true)

if [[ "$OLD" == "$NEW" && "${1:-}" != "--force" ]]; then
  echo "已是最新版 ${OLD:0:7}"; exit 0
fi
if [[ "$NEW" == "$BAD" && "${1:-}" != "--force" ]]; then
  log "跳過 ${NEW:0:7}：這個版本之前啟動失敗過"; exit 0
fi

deploy() {
  "${DC[@]}" up -d --build --remove-orphans bot
  for _ in $(seq 1 30); do
    sleep 2
    curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && return 0
  done
  return 1
}

if ! git diff --quiet HEAD -- . ; then
  log "stock-gift-bot 內有本機修改（$(git diff --name-only HEAD -- . | tr '\n' ' ')），為避免覆蓋已停止更新。"
  log "客製化請改寫在 .env 或 docker-compose.override.yml，或用 git checkout -- <檔案> 還原後再更新。"
  exit 1
fi

log "更新 ${OLD:0:7} -> ${NEW:0:7} ($BRANCH)"
git checkout --quiet "$BRANCH" 2>/dev/null || git checkout --quiet -b "$BRANCH" "origin/$BRANCH"
git reset --quiet --hard "$NEW"
if deploy; then
  echo "$(git log -1 --format='%h %cd %s' --date=format:'%F %R')" > "$BOT_DIR/data/version.txt"
  rm -f "$BOT_DIR/data/.bad_commit"
  docker image prune -f >/dev/null 2>&1 || true
  log "更新完成：$(cat "$BOT_DIR/data/version.txt")"
else
  log "新版啟動失敗，退回 ${OLD:0:7}"
  echo "$NEW" > "$BOT_DIR/data/.bad_commit"
  git reset --quiet --hard "$OLD"
  deploy && log "已退回舊版" || log "舊版也啟動失敗，請看 docker logs stock-gift-bot"
  exit 1
fi
