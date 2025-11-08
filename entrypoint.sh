#!/usr/bin/env bash
set -euo pipefail

if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
  echo "[entrypoint] GPU detected:"
  nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv
  
  # Test CUDA availability with Python
  python -c "import torch; print(f'PyTorch CUDA available: {torch.cuda.is_available()}'); print(f'CUDA version: {torch.version.cuda}'); print(f'GPU count: {torch.cuda.device_count()}')" || echo "Warning: PyTorch CUDA check failed"
  
  echo "[entrypoint] Starting ComfyUI..."
  python -u /ComfyUI/main.py --disable-auto-launch --listen 0.0.0.0 --port 8188 --cuda-device 0 &

  echo "[entrypoint] waiting ComfyUI on 127.0.0.1:8188 ..."
  for i in {1..180}; do
    if wget -qO- http://127.0.0.1:8188/ >/dev/null 2>&1; then
      echo "[entrypoint] ComfyUI is up."
      break
    fi
    sleep 1
  done
else
  echo "[entrypoint] No usable GPU detected, skipping ComfyUI boot."
fi

echo "[entrypoint] starting RunPod handler (callback-enabled)..."
exec python -u /handler_callback.py
