#!/usr/bin/env bash
set -euo pipefail

if command -v nvidia-smi >/dev/null 2>&1; then
  echo "[entrypoint] GPU detected, starting ComfyUI..."
  python -u /ComfyUI/main.py --disable-auto-launch --listen 0.0.0.0 --port 8188 &

  echo "[entrypoint] waiting ComfyUI on 127.0.0.1:8188 ..."
  for i in {1..180}; do
    if wget -qO- http://127.0.0.1:8188/ >/dev/null 2>&1; then
      echo "[entrypoint] ComfyUI is up."
      break
    fi
    sleep 1
  done
else
  echo "[entrypoint] No GPU detected (test phase), skipping ComfyUI boot."
fi

echo "[entrypoint] starting RunPod handler (callback-enabled)..."
exec python -u /handler_callback.py
