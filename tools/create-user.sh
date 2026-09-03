#!/usr/bin/env bash
set -euo pipefail

echo "======================================"
echo " Linux User Creator"
echo "======================================"

if [[ $EUID -ne 0 ]]; then
    echo "請使用 sudo 執行："
    echo "  sudo ./create-user.sh"
    exit 1
fi

read -rp "請輸入新的使用者帳號: " USERNAME

if [[ -z "$USERNAME" ]]; then
    echo "ERROR: 使用者名稱不能為空"
    exit 1
fi

if ! [[ "$USERNAME" =~ ^[a-z_][a-z0-9_-]*$ ]]; then
    echo "ERROR: 使用者名稱格式不合法"
    echo "建議只使用小寫英文字母、數字、底線與連字號"
    exit 1
fi

if id "$USERNAME" >/dev/null 2>&1; then
    echo "ERROR: 使用者 $USERNAME 已經存在"
    id "$USERNAME"
    exit 1
fi

echo
echo "即將建立使用者：$USERNAME"
echo "Full Name 會自動設定為：$USERNAME"
echo

# 使用 adduser 建立帳號。
# --gecos 自動填入 Full Name，其他欄位留空。
adduser --gecos "$USERNAME,,,," "$USERNAME"

echo
echo "======================================"
echo " 帳號建立完成"
echo "======================================"

echo
echo "家目錄："
ls -ld "/home/$USERNAME"

echo
echo "UID / GID 資訊："
id "$USERNAME"

echo
echo "建立完成：$USERNAME"
