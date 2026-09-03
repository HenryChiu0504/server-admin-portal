#!/usr/bin/env bash
set -Eeuo pipefail
if [[ $EUID -ne 0 ]]; then echo "ERROR: installer must run as root"; exit 1; fi
command -v nvidia-smi >/dev/null 2>&1 || { echo "ERROR: nvidia-smi not found"; exit 1; }
. /etc/os-release
CODENAME="${VERSION_CODENAME:-}"
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
    *) DEBIAN_FRONTEND=noninteractive apt-get install -y nvidia-settings ;;
  esac
fi
DEBIAN_FRONTEND=noninteractive apt-get install -y xserver-xorg-core x11-xserver-utils
command -v nvidia-xconfig >/dev/null 2>&1 || { echo "ERROR: nvidia-xconfig not found"; exit 1; }

echo "[3/6] Creating Xorg fan-control configuration..."
CONF=/etc/X11/xorg-nvidia-fan.conf
[[ -f "$CONF" ]] && cp -a "$CONF" "${CONF}.bak.$(date +%Y%m%d-%H%M%S)"
nvidia-xconfig --output-xconfig="$CONF" --enable-all-gpus --allow-empty-initial-configuration --cool-bits=4
mkdir -p /usr/local/libexec

echo "[4/6] Installing fan-control service and commands..."
cat >/usr/local/sbin/nvidia-fan-x-start <<'EOF'
#!/usr/bin/env bash
set -e
XORG="$(command -v Xorg || true)"
[[ -n "$XORG" ]] || XORG=/usr/lib/xorg/Xorg
exec "$XORG" :99 -config /etc/X11/xorg-nvidia-fan.conf -noreset -nolisten tcp -ac -logfile /var/log/Xorg.nvidia-fan.log
EOF
chmod 755 /usr/local/sbin/nvidia-fan-x-start

cat >/etc/systemd/system/nvidia-fan-x.service <<'EOF'
[Unit]
Description=Local NVIDIA X server for fan control
After=multi-user.target
[Service]
Type=simple
ExecStart=/usr/local/sbin/nvidia-fan-x-start
Restart=on-failure
RestartSec=3
TimeoutStartSec=25
TimeoutStopSec=10
KillMode=mixed
[Install]
WantedBy=multi-user.target
EOF

cat >/usr/local/libexec/nvidia-fanctl <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
MODE="${1:-}"
D="${NVIDIA_FAN_DISPLAY:-:99}"
if [[ "$MODE" != "auto" ]]; then
  [[ "$MODE" =~ ^[0-9]+$ ]] || { echo "Usage: nvidia-fanctl {auto|50..95}"; exit 2; }
  (( MODE >= 50 && MODE <= 95 )) || { echo "ERROR: manual fan speed must be 50-95%"; exit 2; }
fi
nvidia-settings -c "$D" -q gpus >/dev/null 2>&1 || { echo "ERROR: cannot connect to NVIDIA fan-control X server ($D)"; exit 1; }
mapfile -t GPUS < <(nvidia-settings -c "$D" -q gpus 2>/dev/null | sed -n 's/.*\[gpu:\([0-9]\+\)\].*/\1/p' | sort -nu)
mapfile -t FANS < <(nvidia-settings -c "$D" -q fans 2>/dev/null | sed -n 's/.*\[fan:\([0-9]\+\)\].*/\1/p' | sort -nu)
[[ ${#GPUS[@]} -gt 0 ]] || { echo "ERROR: no GPU targets detected"; exit 1; }
[[ ${#FANS[@]} -gt 0 ]] || { echo "ERROR: no controllable fan targets detected"; exit 1; }
if [[ "$MODE" == "auto" ]]; then
  for g in "${GPUS[@]}"; do nvidia-settings -c "$D" -a "[gpu:${g}]/GPUFanControlState=0" >/dev/null; done
else
  for g in "${GPUS[@]}"; do nvidia-settings -c "$D" -a "[gpu:${g}]/GPUFanControlState=1" >/dev/null; done
  for f in "${FANS[@]}"; do nvidia-settings -c "$D" -a "[fan:${f}]/GPUTargetFanSpeed=${MODE}" >/dev/null; done
fi
EOF
chmod 755 /usr/local/libexec/nvidia-fanctl

cat >/usr/local/bin/fan_95.sh <<'EOF'
#!/usr/bin/env bash
exec /usr/local/libexec/nvidia-fanctl 95
EOF
cat >/usr/local/bin/fan_init.sh <<'EOF'
#!/usr/bin/env bash
exec /usr/local/libexec/nvidia-fanctl auto
EOF
chmod 755 /usr/local/bin/fan_95.sh /usr/local/bin/fan_init.sh

echo "[5/6] Checking fan-control X server..."
systemctl daemon-reload
systemctl enable nvidia-fan-x.service >/dev/null
READY=0
if nvidia-settings -c :99 -q gpus >/dev/null 2>&1 && nvidia-settings -c :99 -q fans >/dev/null 2>&1; then
  echo "[OK] Existing :99 X server already exposes NVIDIA GPUs and fans; leaving it untouched."
  READY=1
else
  echo "[INFO] :99 is not ready; starting nvidia-fan-x.service without terminating existing X processes."
  systemctl start --no-block nvidia-fan-x.service || true
  for _ in $(seq 1 30); do
    if nvidia-settings -c :99 -q gpus >/dev/null 2>&1 && nvidia-settings -c :99 -q fans >/dev/null 2>&1; then READY=1; break; fi
    sleep 1
  done
fi
if [[ "$READY" -ne 1 ]]; then
  echo "ERROR: fan-control X server did not become ready"
  systemctl status nvidia-fan-x.service --no-pager || true
  tail -n 80 /var/log/Xorg.nvidia-fan.log 2>/dev/null || true
  exit 1
fi
echo "[6/6] Verifying fan control..."
/usr/local/libexec/nvidia-fanctl auto >/dev/null
test -x /usr/local/libexec/nvidia-fanctl
echo "[OK] NVIDIA Fan Control installation completed."
