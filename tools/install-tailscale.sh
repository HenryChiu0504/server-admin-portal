#!/usr/bin/env bash
set -euo pipefail

echo "======================================"
echo " Tailscale Installer"
echo "======================================"

if [[ $EUID -ne 0 ]]; then
    echo "請使用 sudo 執行："
    echo "  sudo ./install-tailscale.sh"
    exit 1
fi

# 安裝 Tailscale（若尚未安裝）
if ! command -v tailscale >/dev/null 2>&1; then
    echo "[1/4] 安裝 Tailscale..."
    curl -fsSL https://tailscale.com/install.sh | sh
else
    echo "[1/4] Tailscale 已安裝，略過安裝。"
fi

echo "[2/4] 啟用 tailscaled..."
systemctl enable --now tailscaled

echo "[3/4] Tailscale 版本："
tailscale version

echo
echo "[4/4] 執行 tailscale up..."
echo "如果尚未登入，終端機會顯示登入網址。"
echo

tailscale up

echo
echo "======================================"
echo " Tailscale 設定完成"
echo "======================================"

echo
echo "Tailscale IPv4："
tailscale ip -4 || true

echo
echo "Tailscale 狀態："
tailscale status || true
