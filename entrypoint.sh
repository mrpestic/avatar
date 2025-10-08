#!/usr/bin/env bash
set -euo pipefail

echo "[entrypoint] starting ComfyUI..."
python -u /ComfyUI/main.py --disable-auto-launch --listen 0.0.0.0 --port 8188 &

echo "[entrypoint] waiting ComfyUI on 127.0.0.1:8188 ..."
for i in {1..180}; do
  if wget -qO- http://127.0.0.1:8188/ >/dev/null 2>&1; then
    echo "[entrypoint] ComfyUI is up."
    break
  fi
  sleep 1
done

echo "[entrypoint] starting RunPod handler..."
exec python -u /handler.py
