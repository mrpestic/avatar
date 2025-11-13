#!/usr/bin/env bash
set -euo pipefail

COMFYUI_PID_FILE="/tmp/comfyui.pid"
COMFYUI_PORT=8188

# Function to get ComfyUI PID
get_comfyui_pid() {
  if [ -f "${COMFYUI_PID_FILE}" ]; then
    cat "${COMFYUI_PID_FILE}" 2>/dev/null || echo ""
  else
    echo ""
  fi
}

# Function to set ComfyUI PID
set_comfyui_pid() {
  echo "$1" > "${COMFYUI_PID_FILE}"
}

# Function to start ComfyUI
start_comfyui() {
  echo "[entrypoint] Starting ComfyUI..."
  python -u /ComfyUI/main.py --disable-auto-launch --listen 0.0.0.0 --port ${COMFYUI_PORT} --cuda-device 0 > /tmp/comfyui.log 2>&1 &
  local pid=$!
  set_comfyui_pid ${pid}
  echo "[entrypoint] ComfyUI started with PID: ${pid}"
  
  # Wait for ComfyUI to be ready
  echo "[entrypoint] waiting ComfyUI on 127.0.0.1:${COMFYUI_PORT} ..."
  for i in {1..180}; do
    if wget -qO- http://127.0.0.1:${COMFYUI_PORT}/ >/dev/null 2>&1; then
      echo "[entrypoint] ComfyUI is up."
      return 0
    fi
    # Check if process is still alive
    if ! kill -0 ${pid} 2>/dev/null; then
      echo "[entrypoint] ComfyUI process died during startup!"
      rm -f "${COMFYUI_PID_FILE}"
      return 1
    fi
    sleep 1
  done
  echo "[entrypoint] ComfyUI failed to start within 180 seconds"
  rm -f "${COMFYUI_PID_FILE}"
  return 1
}

# Function to check if ComfyUI is healthy
check_comfyui_health() {
  local pid=$(get_comfyui_pid)
  if [ -z "${pid}" ] || ! kill -0 ${pid} 2>/dev/null; then
    return 1  # Process is dead
  fi
  if ! wget -qO- http://127.0.0.1:${COMFYUI_PORT}/ >/dev/null 2>&1; then
    return 1  # HTTP check failed
  fi
  return 0  # Healthy
}

# Function to restart ComfyUI
restart_comfyui() {
  echo "[entrypoint] Restarting ComfyUI..."
  local pid=$(get_comfyui_pid)
  if [ -n "${pid}" ]; then
    kill ${pid} 2>/dev/null || true
    wait ${pid} 2>/dev/null || true
  fi
  rm -f "${COMFYUI_PID_FILE}"
  sleep 2
  start_comfyui
}

# Watchdog function to monitor ComfyUI
watchdog_comfyui() {
  while true; do
    sleep 30  # Check every 30 seconds
    if ! check_comfyui_health; then
      echo "[entrypoint] ComfyUI health check failed, restarting..."
      restart_comfyui || echo "[entrypoint] Failed to restart ComfyUI"
    fi
  done
}

if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
  echo "[entrypoint] GPU detected:"
  nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv
  
  # Test CUDA availability with Python
  python -c "import torch; print(f'PyTorch CUDA available: {torch.cuda.is_available()}'); print(f'CUDA version: {torch.version.cuda}'); print(f'GPU count: {torch.cuda.device_count()}')" || echo "Warning: PyTorch CUDA check failed"
  
  # Start ComfyUI
  if ! start_comfyui; then
    echo "[entrypoint] Failed to start ComfyUI initially"
    exit 1
  fi
  
  # Start watchdog in background
  watchdog_comfyui &
  WATCHDOG_PID=$!
  echo "[entrypoint] ComfyUI watchdog started with PID: ${WATCHDOG_PID}"
else
  echo "[entrypoint] No usable GPU detected, skipping ComfyUI boot."
fi

echo "[entrypoint] starting RunPod handler (callback-enabled)..."
exec python -u /handler_callback.py
