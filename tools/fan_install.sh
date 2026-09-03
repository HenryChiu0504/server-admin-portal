#!/usr/bin/env bash
set -Eeuo pipefail
if [[ $EUID -ne 0 ]]; then echo "請用 sudo 執行：sudo ./fan_install.sh"; exit 1; fi
command -v nvidia-smi >/dev/null || { echo "ERROR: 找不到 nvidia-smi"; exit 1; }
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y nvidia-settings xserver-xorg-core x11-xserver-utils
CONF=/etc/X11/xorg-nvidia-fan.conf
[[ -f "$CONF" ]] && cp -a "$CONF" "${CONF}.bak.$(date +%Y%m%d-%H%M%S)"
nvidia-xconfig --output-xconfig="$CONF" --enable-all-gpus --allow-empty-initial-configuration --cool-bits=4
mkdir -p /usr/local/libexec
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
ExecStart=/usr/local/sbin/nvidia-fan-x-start
Restart=on-failure
RestartSec=3
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
  (( MODE >= 50 && MODE <= 95 )) || { echo "ERROR: 手動風扇轉速只允許 50-95%"; exit 2; }
fi
nvidia-settings -c "$D" -q gpus >/dev/null 2>&1 || { echo "ERROR: 無法連線到 NVIDIA fan-control X server ($D)"; exit 1; }
mapfile -t GPUS < <(nvidia-settings -c "$D" -q gpus 2>/dev/null | sed -n 's/.*\[gpu:\([0-9]\+\)\].*/\1/p' | sort -nu)
mapfile -t FANS < <(nvidia-settings -c "$D" -q fans 2>/dev/null | sed -n 's/.*\[fan:\([0-9]\+\)\].*/\1/p' | sort -nu)
[[ ${#GPUS[@]} -gt 0 ]] || { echo "ERROR: 看不到 GPU"; exit 1; }
[[ ${#FANS[@]} -gt 0 ]] || { echo "ERROR: 沒有可控制 fan target"; exit 1; }
if [[ "$MODE" != "auto" ]]; then
  for g in "${GPUS[@]}"; do nvidia-settings -c "$D" -a "[gpu:${g}]/GPUFanControlState=1" >/dev/null; done
  for f in "${FANS[@]}"; do nvidia-settings -c "$D" -a "[fan:${f}]/GPUTargetFanSpeed=${MODE}" >/dev/null; done
else
  for g in "${GPUS[@]}"; do nvidia-settings -c "$D" -a "[gpu:${g}]/GPUFanControlState=0" >/dev/null; done
fi
nvidia-smi --query-gpu=index,name,temperature.gpu,fan.speed --format=csv,noheader 2>/dev/null || true
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
systemctl daemon-reload
systemctl enable --now nvidia-fan-x.service
sleep 3
echo "安裝完成。所有使用者可直接執行：fan_95.sh / fan_init.sh"
