#!/usr/bin/env bash
# Khởi động bot + dashboard trên Render.
# Dữ liệu (accounts.json, users.json) được lưu trên ổ Disk /var/data
# thông qua symlink, nên Main.py vẫn đọc "accounts.json" theo đường dẫn tương đối như cũ.
set -e
DATA_DIR="${DATA_DIR:-/var/data}"
mkdir -p "$DATA_DIR" 2>/dev/null || true

if [ -w "$DATA_DIR" ]; then
  for f in accounts.json users.json; do
    if [ -f "$f" ] && [ ! -L "$f" ] && [ ! -f "$DATA_DIR/$f" ]; then
      cp "$f" "$DATA_DIR/$f"          # giữ dữ liệu cũ nếu có trong repo
    fi
    [ -f "$DATA_DIR/$f" ] || { [ "$f" = "accounts.json" ] && echo "[]" > "$DATA_DIR/$f" || true; }
    if [ -f "$DATA_DIR/$f" ]; then
      rm -f "$f"
      ln -s "$DATA_DIR/$f" "$f"
    fi
  done
  echo "[+] Dữ liệu lưu tại $DATA_DIR"
else
  echo "[!] Không có Disk ghi được tại $DATA_DIR - dữ liệu sẽ mất khi restart"
fi

exec python -u Main.py
