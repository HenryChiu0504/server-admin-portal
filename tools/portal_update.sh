#!/usr/bin/env bash
# Self-update the Portal from GitHub. Started by the Portal through systemd-run,
# because update.sh restarts server-admin-portal.service (and with it the
# backend that launched this script).
set -Euo pipefail

main() {
  [[ $EUID -eq 0 ]] || { echo "ERROR: must run as root"; exit 1; }
  local dst log
  dst="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  log="${PORTAL_UPDATE_LOG:-/var/log/server-admin-portal/portal-update.log}"
  mkdir -p "$(dirname "$log")"
  exec >>"$log" 2>&1
  trap 'echo "__EXIT__ $?"' EXIT

  echo "[INFO] $(date '+%F %T') 開始更新 Server Admin Portal ($dst)"
  local git=(git -C "$dst" -c "safe.directory=$dst")
  # install.sh / update.sh chmod the scripts; do not treat that as local edits.
  "${git[@]}" config core.fileMode false
  echo "[1/2] git pull --ff-only"
  "${git[@]}" pull --ff-only || exit $?
  "${git[@]}" log --oneline -1

  # Run the freshly pulled update.sh (its own git pull is then a no-op).
  echo "[2/2] update.sh"
  bash "$dst/update.sh" || exit $?
  echo "[OK] $(date '+%F %T') Portal 更新完成"
}

main "$@"
exit
