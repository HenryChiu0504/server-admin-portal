#!/usr/bin/env bash
set -e
[[ -x /usr/local/libexec/nvidia-fanctl ]] || { echo "請先由管理者執行 fan_install.sh"; exit 1; }
exec /usr/local/libexec/nvidia-fanctl auto
