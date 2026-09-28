#!/usr/bin/env bash
# One-click apt package update. Started by the Portal through systemd-run so
# it keeps running even if the browser or Portal backend disconnects.
#
#   KEEP_NVIDIA=1 (default)  hold NVIDIA driver packages during the upgrade.
#   Upgrading the driver while it is loaded causes "Driver/library version
#   mismatch" in nvidia-smi / nvidia-settings (and breaks fan control) until
#   the next reboot.
set -Euo pipefail
[[ $EUID -eq 0 ]] || { echo "ERROR: must run as root"; exit 1; }

LOG="${PKG_UPGRADE_LOG:-/var/log/server-admin-portal/pkg-upgrade.log}"
mkdir -p "$(dirname "$LOG")"
exec >>"$LOG" 2>&1

export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a
APT_OPTS=(-y -o DPkg::Lock::Timeout=120 -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold)
HELD=()

finish() {
  local code=$?
  if ((${#HELD[@]})); then
    apt-mark unhold "${HELD[@]}" >/dev/null 2>&1 || true
    echo "[INFO] Released temporary hold on ${#HELD[@]} NVIDIA package(s)"
  fi
  if [[ -f /var/run/reboot-required ]]; then
    echo "[WARN] 系統需要重新開機才能套用部分更新 (/var/run/reboot-required)"
  fi
  echo "__EXIT__ $code"
}
trap finish EXIT

echo "[INFO] $(date '+%F %T') 開始更新套件"

echo "[1/3] apt-get update"
apt-get update -o DPkg::Lock::Timeout=120 || exit $?

if [[ "${KEEP_NVIDIA:-1}" == "1" ]]; then
  mapfile -t NV < <(dpkg-query -W -f='${db:Status-Abbrev} ${Package}\n' 2>/dev/null \
    | awk '$1 ~ /^ii/ {print $2}' \
    | grep -E '^(nvidia-|libnvidia-|xserver-xorg-video-nvidia|linux-modules-nvidia-|linux-objects-nvidia-|linux-signatures-nvidia-)' || true)
  mapfile -t ALREADY < <(apt-mark showhold 2>/dev/null)
  for p in "${NV[@]}"; do
    [[ " ${ALREADY[*]} " == *" $p "* ]] && continue
    HELD+=("$p")
  done
  if ((${#HELD[@]})); then
    apt-mark hold "${HELD[@]}" >/dev/null
    echo "[INFO] 暫時保留 ${#HELD[@]} 個 NVIDIA 驅動套件不更新"
  fi
fi

echo "[2/3] 可更新的套件："
apt list --upgradable 2>/dev/null | sed -n '2,$p'

echo "[3/3] apt-get upgrade"
apt-get "${APT_OPTS[@]}" upgrade || exit $?
apt-get "${APT_OPTS[@]}" autoremove || true

echo "[OK] $(date '+%F %T') 套件更新完成"
