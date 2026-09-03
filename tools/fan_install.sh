#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "請用 root 執行"
    exit 1
fi

command -v nvidia-smi >/dev/null || {
    echo "ERROR: 找不到 nvidia-smi"
    exit 1
}

. /etc/os-release

echo "[INFO] Detected OS: ${PRETTY_NAME:-unknown}"

apt-get update

if command -v nvidia-settings >/dev/null 2>&1; then
    echo "[OK] nvidia-settings already installed"
else
    case "${VERSION_CODENAME:-}" in
        focal)
            echo "[INFO] Installing Ubuntu 20.04 compatible nvidia-settings..."
            DEBIAN_FRONTEND=noninteractive apt-get install -y \
                nvidia-settings/focal-updates
            ;;
        jammy)
            echo "[INFO] Installing Ubuntu 22.04 compatible nvidia-settings..."
            DEBIAN_FRONTEND=noninteractive apt-get install -y \
                nvidia-settings/jammy-updates
            ;;
        *)
            echo "[INFO] Installing nvidia-settings..."
            DEBIAN_FRONTEND=noninteractive apt-get install -y \
                nvidia-settings
            ;;
    esac
fi

DEBIAN_FRONTEND=noninteractive apt-get install -y \
    xserver-xorg-core \
    x11-xserver-utils
