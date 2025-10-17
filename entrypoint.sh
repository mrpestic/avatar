#!/usr/bin/env bash
set -euo pipefail

if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
  echo "[entrypoint] GPU detected, starting ComfyUI..."
  # Download weights at runtime if not present
  if [ ! -f /ComfyUI/models/diffusion_models/Wan2_1-InfiniteTalk_Single_Q8.gguf ]; then
    echo "[entrypoint] downloading model weights..."
    wget -q https://huggingface.co/Kijai/WanVideo_comfy_GGUF/resolve/main/InfiniteTalk/Wan2_1-InfiniteTalk_Single_Q8.gguf -O /ComfyUI/models/diffusion_models/Wan2_1-InfiniteTalk_Single_Q8.gguf || true
    wget -q https://huggingface.co/Kijai/WanVideo_comfy_GGUF/resolve/main/InfiniteTalk/Wan2_1-InfiniteTalk_Multi_Q8.gguf -O /ComfyUI/models/diffusion_models/Wan2_1-InfiniteTalk_Multi_Q8.gguf || true
    wget -q https://huggingface.co/city96/Wan2.1-I2V-14B-480P-gguf/resolve/main/wan2.1-i2v-14b-480p-Q8_0.gguf -O /ComfyUI/models/diffusion_models/wan2.1-i2v-14b-480p-Q8_0.gguf || true
    wget -q https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Lightx2v/lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors -O /ComfyUI/models/loras/lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors || true
    wget -q https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Wan2_1_VAE_bf16.safetensors -O /ComfyUI/models/vae/Wan2_1_VAE_bf16.safetensors || true
    wget -q https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/umt5-xxl-enc-bf16.safetensors -O /ComfyUI/models/text_encoders/umt5-xxl-enc-bf16.safetensors || true
    wget -q https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/clip_vision/clip_vision_h.safetensors -O /ComfyUI/models/clip_vision/clip_vision_h.safetensors || true
    wget -q https://huggingface.co/Kijai/MelBandRoFormer_comfy/resolve/main/MelBandRoformer_fp16.safetensors -O /ComfyUI/models/diffusion_models/MelBandRoformer_fp16.safetensors || true
  fi
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
  echo "[entrypoint] No usable GPU detected, skipping ComfyUI boot."
fi

echo "[entrypoint] starting RunPod handler (callback-enabled)..."
exec python -u /handler_callback.py
