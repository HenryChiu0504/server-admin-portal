#!/usr/bin/env bash
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo "ERROR: installer must run as root"; exit 1; }
command -v nvidia-smi >/dev/null 2>&1 || { echo "ERROR: nvidia-smi not found"; exit 1; }
. /etc/os-release
CODENAME="${VERSION_CODENAME:-}"
ENV_FILE=/etc/server-admin-portal.env
CONF=/etc/X11/xorg-nvidia-fan.conf

echo "[INFO] Detected OS: ${PRETTY_NAME:-unknown}"
echo "[1/6] Updating package lists..."
apt-get update

echo "[2/6] Installing NVIDIA settings and X11 dependencies..."
if command -v nvidia-settings >/dev/null 2>&1; then
  echo "[OK] nvidia-settings already installed"
else
  case "$CODENAME" in
    focal) DEBIAN_FRONTEND=noninteractive apt-get install -y nvidia-settings/focal-updates ;;
    jammy) DEBIAN_FRONTEND=noninteractive apt-get install -y nvidia-settings/jammy-updates ;;
    noble) DEBIAN_FRONTEND=noninteractive apt-get install -y nvidia-settings/noble-updates || DEBIAN_FRONTEND=noninteractive apt-get install -y nvidia-settings ;;
    resolute) DEBIAN_FRONTEND=noninteractive apt-get install -y nvidia-settings ;;
    *) DEBIAN_FRONTEND=noninteractive apt-get install -y nvidia-settings ;;
  esac
fi
DEBIAN_FRONTEND=noninteractive apt-get install -y xserver-xorg-core x11-xserver-utils
command -v nvidia-xconfig >/dev/null 2>&1 || { echo "ERROR: nvidia-xconfig not found"; exit 1; }

echo "[3/6] Creating Xorg fan-control configuration..."
[[ -f "$CONF" ]] && cp -a "$CONF" "${CONF}.bak.$(date +%Y%m%d-%H%M%S)"
nvidia-xconfig --output-xconfig="$CONF" --enable-all-gpus --allow-empty-initial-configuration --cool-bits=4
mkdir -p /usr/local/libexec /usr/local/sbin

# A display is only fan-control ready when the control attribute is actually
# available. Merely listing GPU/Fan targets is not sufficient.
fan_display_ready() {
  local d="$1"
  nvidia-settings -c "$d" -q gpus >/dev/null 2>&1 || return 1
  nvidia-settings -c "$d" -q fans >/dev/null 2>&1 || return 1
  nvidia-settings -c "$d" -q '[gpu:0]/GPUFanControlState' -t >/dev/null 2>&1 || return 1
}

display_in_use() {
  local n="${1#:}"
  [[ -S "/tmp/.X11-unix/X${n}" || -e "/tmp/.X${n}-lock" ]] && return 0
  pgrep -af "(^|/)(Xorg|X)[[:space:]]+:${n}([[:space:]]|$)" >/dev/null 2>&1
}

# Prefer an already functional display. Otherwise choose a free private X
# display without terminating any existing X server.
DISPLAY_NUM=""
for d in :99 :98 :97 :96 :95; do
  if fan_display_ready "$d"; then
    DISPLAY_NUM="$d"
    echo "[OK] Existing $d already provides working NVIDIA fan-control attributes; leaving it untouched."
    break
  fi
done
if [[ -z "$DISPLAY_NUM" ]]; then
  for d in :99 :98 :97 :96 :95; do
    if ! display_in_use "$d"; then DISPLAY_NUM="$d"; break; fi
  done
fi
[[ -n "$DISPLAY_NUM" ]] || { echo "ERROR: no free X display available in :95-:99"; exit 1; }

echo "[INFO] Fan-control display: $DISPLAY_NUM"

# Persist the selected display for the Portal backend and command-line helper.
touch "$ENV_FILE"
if grep -q '^NVIDIA_FAN_DISPLAY=' "$ENV_FILE"; then
  sed -i "s/^NVIDIA_FAN_DISPLAY=.*/NVIDIA_FAN_DISPLAY=${DISPLAY_NUM}/" "$ENV_FILE"
else
  printf '\nNVIDIA_FAN_DISPLAY=%s\n' "$DISPLAY_NUM" >>"$ENV_FILE"
fi
chmod 600 "$ENV_FILE"

echo "[4/6] Installing fan-control service and commands..."
cat >/usr/local/sbin/nvidia-fan-x-start <<'EOS'
#!/usr/bin/env bash
set -Eeuo pipefail
ENV_FILE=/etc/server-admin-portal.env
[[ -r "$ENV_FILE" ]] && . "$ENV_FILE"
D="${NVIDIA_FAN_DISPLAY:-:99}"
XORG="$(command -v Xorg || true)"
[[ -n "$XORG" ]] || XORG=/usr/lib/xorg/Xorg
exec "$XORG" "$D" -config /etc/X11/xorg-nvidia-fan.conf -noreset -nolisten tcp -ac -logfile /var/log/Xorg.nvidia-fan.log
EOS
chmod 755 /usr/local/sbin/nvidia-fan-x-start

cat >/etc/systemd/system/nvidia-fan-x.service <<'EOS'
[Unit]
Description=Local NVIDIA X server for fan control
After=multi-user.target
[Service]
Type=simple
EnvironmentFile=-/etc/server-admin-portal.env
ExecStart=/usr/local/sbin/nvidia-fan-x-start
Restart=on-failure
RestartSec=3
TimeoutStartSec=25
TimeoutStopSec=10
KillMode=mixed
[Install]
WantedBy=multi-user.target
EOS

cat >/usr/local/libexec/nvidia-fanctl <<'EOS'
#!/usr/bin/env bash
set -Eeuo pipefail
MODE="${1:-}"
ENV_FILE=/etc/server-admin-portal.env
[[ -r "$ENV_FILE" ]] && . "$ENV_FILE"
D="${NVIDIA_FAN_DISPLAY:-:99}"
if [[ "$MODE" != "auto" ]]; then
  [[ "$MODE" =~ ^[0-9]+$ ]] || { echo "Usage: nvidia-fanctl {auto|50..95}"; exit 2; }
  (( MODE >= 50 && MODE <= 95 )) || { echo "ERROR: manual fan speed must be 50-95%"; exit 2; }
fi
nvidia-settings -c "$D" -q gpus >/dev/null 2>&1 || { echo "ERROR: cannot connect to NVIDIA fan-control X server ($D)"; exit 1; }
mapfile -t GPUS < <(nvidia-settings -c "$D" -q gpus 2>/dev/null | sed -n 's/.*\[gpu:\([0-9]\+\)\].*/\1/p' | sort -nu)
mapfile -t FANS < <(nvidia-settings -c "$D" -q fans 2>/dev/null | sed -n 's/.*\[fan:\([0-9]\+\)\].*/\1/p' | sort -nu)
[[ ${#GPUS[@]} -gt 0 ]] || { echo "ERROR: no GPU targets detected"; exit 1; }
[[ ${#FANS[@]} -gt 0 ]] || { echo "ERROR: no fan targets detected"; exit 1; }
nvidia-settings -c "$D" -q "[gpu:${GPUS[0]}]/GPUFanControlState" -t >/dev/null 2>&1 || {
  echo "ERROR: GPUFanControlState is unavailable on $D; CoolBits fan control is not active"; exit 1;
}
if [[ "$MODE" == "auto" ]]; then
  for g in "${GPUS[@]}"; do nvidia-settings -c "$D" -a "[gpu:${g}]/GPUFanControlState=0" >/dev/null; done
else
  for g in "${GPUS[@]}"; do nvidia-settings -c "$D" -a "[gpu:${g}]/GPUFanControlState=1" >/dev/null; done
  for f in "${FANS[@]}"; do nvidia-settings -c "$D" -a "[fan:${f}]/GPUTargetFanSpeed=${MODE}" >/dev/null; done
fi
EOS
chmod 755 /usr/local/libexec/nvidia-fanctl

cat >/usr/local/bin/fan_95.sh <<'EOS'
#!/usr/bin/env bash
exec /usr/local/libexec/nvidia-fanctl 95
EOS
cat >/usr/local/bin/fan_init.sh <<'EOS'
#!/usr/bin/env bash
exec /usr/local/libexec/nvidia-fanctl auto
EOS
chmod 755 /usr/local/bin/fan_95.sh /usr/local/bin/fan_init.sh

echo "[5/6] Starting/checking fan-control X server..."
systemctl daemon-reload
systemctl enable nvidia-fan-x.service >/dev/null
if ! fan_display_ready "$DISPLAY_NUM"; then
  echo "[INFO] $DISPLAY_NUM is reserved for this installer and is not ready yet; starting nvidia-fan-x.service."
  systemctl restart nvidia-fan-x.service
  READY=0
  for _ in $(seq 1 30); do
    if fan_display_ready "$DISPLAY_NUM"; then READY=1; break; fi
    sleep 1
  done
  if [[ "${READY:-0}" -ne 1 ]]; then
    echo "ERROR: fan-control X server did not expose GPUFanControlState on $DISPLAY_NUM"
    systemctl status nvidia-fan-x.service --no-pager || true
    tail -n 100 /var/log/Xorg.nvidia-fan.log 2>/dev/null || true
    exit 1
  fi
fi

echo "[6/6] Verifying fan control..."
/usr/local/libexec/nvidia-fanctl auto >/dev/null
# Reload Portal environment so its backend uses the selected display.
systemctl try-restart server-admin-portal.service >/dev/null 2>&1 || true

echo "[OK] NVIDIA Fan Control installation completed on $DISPLAY_NUM."
