#!/usr/bin/env bash
# Recover NVIDIA fan control when the private fan-control X server is missing.
#
# Known failure: plymouth-quit-wait.service never finishes, multi-user.target
# stays "waiting", and an nvidia-fan-x.service ordered After=multi-user.target
# sits in "start waiting" forever, so :99 never comes up and nvidia-fanctl
# reports "no GPU targets detected" / "Connection refused".
set -Euo pipefail
[[ $EUID -eq 0 ]] || { echo "ERROR: recovery must run as root"; exit 1; }

ENV_FILE=/etc/server-admin-portal.env
UNIT=nvidia-fan-x.service
UNIT_FILE=/etc/systemd/system/$UNIT
FANCTL=/usr/local/libexec/nvidia-fanctl

[[ -x "$FANCTL" ]] || { echo "ERROR: $FANCTL not found; install fan control first"; exit 1; }
[[ -r "$ENV_FILE" ]] && . "$ENV_FILE"
D="${NVIDIA_FAN_DISPLAY:-:99}"
N="${D#:}"

fan_display_ready() {
  nvidia-settings -c "$D" -q gpus >/dev/null 2>&1 || return 1
  nvidia-settings -c "$D" -q fans >/dev/null 2>&1 || return 1
  nvidia-settings -c "$D" -q '[gpu:0]/GPUFanControlState' -t >/dev/null 2>&1 || return 1
}

report_targets() {
  local gpus fans state
  gpus="$(nvidia-settings -c "$D" -q gpus 2>/dev/null | grep -c '\[gpu:[0-9]\+\]' || true)"
  fans="$(nvidia-settings -c "$D" -q fans 2>/dev/null | grep -c '\[fan:[0-9]\+\]' || true)"
  state="$(nvidia-settings -c "$D" -q '[gpu:0]/GPUFanControlState' -t 2>/dev/null | tail -n1 || true)"
  echo "[INFO] $D: GPU targets=${gpus:-0}, fan targets=${fans:-0}, GPUFanControlState=${state:-N/A}"
}

echo "[1/6] Checking fan-control display $D..."
if fan_display_ready; then
  report_targets
  echo "[OK] Fan control is already working on $D; nothing to recover."
  exit 0
fi
echo "[WARN] $D is not providing NVIDIA fan-control attributes."
[[ -S "/tmp/.X11-unix/X${N}" ]] && echo "[INFO] X socket /tmp/.X11-unix/X${N} exists" || echo "[INFO] X socket /tmp/.X11-unix/X${N} is missing"
echo "[INFO] $UNIT: $(systemctl is-active "$UNIT" 2>/dev/null || true)"

echo "[2/6] Checking boot targets and pending systemd jobs..."
systemctl list-jobs --no-pager 2>/dev/null | sed 's/^/    /' || true
for u in plymouth-quit-wait.service plymouth-quit.service; do
  if [[ "$(systemctl is-active "$u" 2>/dev/null || true)" == "activating" ]]; then
    echo "[FIX] $u is stuck in 'activating'; telling Plymouth to quit and stopping the unit."
    plymouth quit >/dev/null 2>&1 || true
    systemctl stop --no-block "$u" >/dev/null 2>&1 || true
  fi
done

echo "[3/6] Repairing $UNIT ordering..."
if [[ -f "$UNIT_FILE" ]] && grep -q '^After=.*multi-user\.target' "$UNIT_FILE"; then
  cp -a "$UNIT_FILE" "${UNIT_FILE}.bak.$(date +%Y%m%d-%H%M%S)"
  # Being WantedBy=multi-user.target *and* After=multi-user.target means the
  # service can only start once boot fully finishes; any stuck boot job blocks it.
  sed -i 's/^After=.*multi-user\.target.*$/After=systemd-user-sessions.service systemd-modules-load.service nvidia-persistenced.service/' "$UNIT_FILE"
  echo "[FIX] Removed After=multi-user.target from $UNIT_FILE"
else
  echo "[OK] Unit ordering does not depend on multi-user.target"
fi
systemctl daemon-reload

# A start job queued with the old ordering can keep waiting; cancel it.
while read -r job _ ; do
  [[ "$job" =~ ^[0-9]+$ ]] || continue
  echo "[FIX] Cancelling waiting systemd job $job for $UNIT"
  systemctl cancel "$job" >/dev/null 2>&1 || true
done < <(systemctl list-jobs --no-legend --no-pager 2>/dev/null | awk -v u="$UNIT" '$2==u')

echo "[4/6] Cleaning stale X lock for $D..."
if [[ -e "/tmp/.X${N}-lock" ]]; then
  pid="$(tr -dc '0-9' <"/tmp/.X${N}-lock" 2>/dev/null || true)"
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
    echo "[INFO] /tmp/.X${N}-lock belongs to running PID $pid; leaving it."
  else
    echo "[FIX] Removing stale /tmp/.X${N}-lock (PID ${pid:-?} not running)"
    rm -f "/tmp/.X${N}-lock" "/tmp/.X11-unix/X${N}"
  fi
else
  echo "[OK] No lock file"
fi

echo "[5/6] (Re)starting $UNIT..."
systemctl reset-failed "$UNIT" >/dev/null 2>&1 || true
if [[ "$(systemctl is-active "$UNIT" 2>/dev/null || true)" == "active" ]]; then
  timeout 30 systemctl restart "$UNIT" || echo "[WARN] systemctl restart returned non-zero"
else
  timeout 30 systemctl start "$UNIT" || echo "[WARN] systemctl start returned non-zero"
fi
READY=0
for _ in $(seq 1 30); do
  if fan_display_ready; then READY=1; break; fi
  sleep 1
done
if [[ "$READY" -ne 1 ]]; then
  echo "ERROR: $D still does not expose GPUFanControlState"
  systemctl status "$UNIT" --no-pager 2>&1 | tail -n 20 || true
  tail -n 40 /var/log/Xorg.nvidia-fan.log 2>/dev/null || true
  echo "[HINT] If the unit file is missing or broken, use 修復安裝 (re-run fan_install.sh)."
  exit 1
fi

echo "[6/6] Verifying fan control..."
report_targets
"$FANCTL" auto
echo "[OK] Fan control recovered on $D and switched to Auto."
