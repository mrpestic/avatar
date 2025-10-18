#!/usr/bin/env bash
set -euo pipefail

# Use /runpod-volume for persistent cache (shared across workers in endpoint)
CACHE_DIR="/runpod-volume/models"
mkdir -p "$CACHE_DIR"/{diffusion_models,loras,vae,text_encoders,clip_vision}

# Setup symlinks to cache
for dir in diffusion_models loras vae text_encoders clip_vision; do
  rm -rf "/ComfyUI/models/$dir" 2>/dev/null || true
  ln -sf "$CACHE_DIR/$dir" "/ComfyUI/models/$dir"
done
echo "[entrypoint] Model cache setup: /ComfyUI/models -> $CACHE_DIR"

# Download models if not cached (only first time per endpoint)
download_if_missing() {
  local url="$1"
  local target="$2"
  if [ ! -f "$target" ]; then
    echo "[entrypoint] Downloading $(basename $target)..."
    wget -q --show-progress "$url" -O "$target" || echo "Warning: failed to download $target"
  fi
}

# Check if we need to download (marker file approach)
if [ ! -f "$CACHE_DIR/.models_ready" ]; then
  echo "[entrypoint] First run detected, downloading models to cache..."
  
  download_if_missing "https://huggingface.co/Kijai/WanVideo_comfy_GGUF/resolve/main/InfiniteTalk/Wan2_1-InfiniteTalk_Single_Q8.gguf" "$CACHE_DIR/diffusion_models/Wan2_1-InfiniteTalk_Single_Q8.gguf"
  download_if_missing "https://huggingface.co/Kijai/WanVideo_comfy_GGUF/resolve/main/InfiniteTalk/Wan2_1-InfiniteTalk_Multi_Q8.gguf" "$CACHE_DIR/diffusion_models/Wan2_1-InfiniteTalk_Multi_Q8.gguf"
  download_if_missing "https://huggingface.co/city96/Wan2.1-I2V-14B-480P-gguf/resolve/main/wan2.1-i2v-14b-480p-Q8_0.gguf" "$CACHE_DIR/diffusion_models/wan2.1-i2v-14b-480p-Q8_0.gguf"
  download_if_missing "https://huggingface.co/vrgamedevgirl84/Wan14BT2VFusioniX/resolve/main/FusionX_LoRa/Wan2.1_I2V_14B_FusionX_LoRA.safetensors" "$CACHE_DIR/loras/Wan2.1_I2V_14B_FusionX_LoRA.safetensors"
  download_if_missing "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Wan2_1_VAE_bf16.safetensors" "$CACHE_DIR/vae/Wan2_1_VAE_bf16.safetensors"
  download_if_missing "https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/umt5-xxl-enc-bf16.safetensors" "$CACHE_DIR/text_encoders/umt5-xxl-enc-bf16.safetensors"
  download_if_missing "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/clip_vision/clip_vision_h.safetensors" "$CACHE_DIR/clip_vision/clip_vision_h.safetensors"
  download_if_missing "https://huggingface.co/Kijai/MelBandRoFormer_comfy/resolve/main/MelBandRoformer_fp16.safetensors" "$CACHE_DIR/diffusion_models/MelBandRoformer_fp16.safetensors"
  
  touch "$CACHE_DIR/.models_ready"
  echo "[entrypoint] Models cached successfully!"
else
  echo "[entrypoint] Using cached models from $CACHE_DIR"
fi

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
