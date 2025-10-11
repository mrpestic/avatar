# Use specific version of nvidia cuda image
FROM wlsdml1114/multitalk-base:1.4 as runtime

# Лёгкая сборка по умолчанию (для тестов): 1 — пропускаем ComfyUI и кастомные ноды на этапе билда
ARG LIGHT_BUILD=1
ENV PIP_NO_CACHE_DIR=1

# wget 설치 (URL 다운로드를 위해)
RUN apt-get update && apt-get install -y wget && rm -rf /var/lib/apt/lists/*

RUN pip install -U --no-cache-dir "huggingface_hub[hf_transfer]"
RUN pip install --no-cache-dir runpod websocket-client librosa

WORKDIR /

RUN if [ "$LIGHT_BUILD" = "0" ]; then \
    git clone https://github.com/comfyanonymous/ComfyUI.git && \
    cd /ComfyUI && \
    pip install --no-cache-dir -r requirements.txt; \
    else echo "[build] LIGHT_BUILD=1: skipping ComfyUI clone+deps"; fi

RUN if [ "$LIGHT_BUILD" = "0" ]; then \
    cd /ComfyUI/custom_nodes && \
    git clone --depth 1 https://github.com/Comfy-Org/ComfyUI-Manager.git && \
    cd ComfyUI-Manager && \
    pip install --no-cache-dir -r requirements.txt; \
    else echo "[build] skip ComfyUI-Manager"; fi

RUN if [ "$LIGHT_BUILD" = "0" ]; then \
    cd /ComfyUI/custom_nodes && \
    git clone --depth 1 https://github.com/city96/ComfyUI-GGUF && \
    cd ComfyUI-GGUF && \
    pip install --no-cache-dir -r requirements.txt; \
    else echo "[build] skip ComfyUI-GGUF"; fi

RUN if [ "$LIGHT_BUILD" = "0" ]; then \
    cd /ComfyUI/custom_nodes && \
    git clone --depth 1 https://github.com/kijai/ComfyUI-KJNodes && \
    cd ComfyUI-KJNodes && \
    pip install --no-cache-dir -r requirements.txt; \
    else echo "[build] skip KJNodes"; fi

RUN if [ "$LIGHT_BUILD" = "0" ]; then \
    cd /ComfyUI/custom_nodes && \
    git clone --depth 1 https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite && \
    cd ComfyUI-VideoHelperSuite && \
    pip install --no-cache-dir -r requirements.txt; \
    else echo "[build] skip VideoHelperSuite"; fi
    
RUN if [ "$LIGHT_BUILD" = "0" ]; then \
    cd /ComfyUI/custom_nodes && \
    git clone --depth 1 https://github.com/orssorbit/ComfyUI-wanBlockswap; \
    else echo "[build] skip wanBlockswap"; fi

RUN if [ "$LIGHT_BUILD" = "0" ]; then \
    cd /ComfyUI/custom_nodes && \
    git clone --depth 1 https://github.com/kijai/ComfyUI-MelBandRoFormer && \
    cd ComfyUI-MelBandRoFormer && \
    pip install --no-cache-dir -r requirements.txt; \
    else echo "[build] skip MelBandRoFormer"; fi

RUN if [ "$LIGHT_BUILD" = "0" ]; then \
    cd /ComfyUI/custom_nodes && \
    git clone --depth 1 https://github.com/kijai/ComfyUI-WanVideoWrapper && \
    cd ComfyUI-WanVideoWrapper && \
    pip install --no-cache-dir -r requirements.txt; \
    else echo "[build] skip WanVideoWrapper"; fi


# Условительная загрузка весов: по умолчанию ИГНОРИРУЕМ на этапе билда (для тестов),
# а сами веса докачаем в runtime из entrypoint.sh, когда есть GPU.
ARG INCLUDE_WEIGHTS=0
RUN if [ "$INCLUDE_WEIGHTS" = "1" ]; then \
    wget -q --show-progress https://huggingface.co/Kijai/WanVideo_comfy_GGUF/resolve/main/InfiniteTalk/Wan2_1-InfiniteTalk_Single_Q8.gguf -O /ComfyUI/models/diffusion_models/Wan2_1-InfiniteTalk_Single_Q8.gguf && \
    wget -q --show-progress https://huggingface.co/Kijai/WanVideo_comfy_GGUF/resolve/main/InfiniteTalk/Wan2_1-InfiniteTalk_Multi_Q8.gguf -O /ComfyUI/models/diffusion_models/Wan2_1-InfiniteTalk_Multi_Q8.gguf && \
    wget -q --show-progress https://huggingface.co/city96/Wan2.1-I2V-14B-480P-gguf/resolve/main/wan2.1-i2v-14b-480p-Q8_0.gguf -O /ComfyUI/models/diffusion_models/wan2.1-i2v-14b-480p-Q8_0.gguf && \
    wget -q --show-progress https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Lightx2v/lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors -O /ComfyUI/models/loras/lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors && \
    wget -q --show-progress https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Wan2_1_VAE_bf16.safetensors -O /ComfyUI/models/vae/Wan2_1_VAE_bf16.safetensors && \
    wget -q --show-progress https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/umt5-xxl-enc-bf16.safetensors -O /ComfyUI/models/text_encoders/umt5-xxl-enc-bf16.safetensors && \
    wget -q --show-progress https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/clip_vision/clip_vision_h.safetensors -O /ComfyUI/models/clip_vision/clip_vision_h.safetensors && \
    wget -q --show-progress https://huggingface.co/Kijai/MelBandRoFormer_comfy/resolve/main/MelBandRoformer_fp16.safetensors -O /ComfyUI/models/diffusion_models/MelBandRoformer_fp16.safetensors; \
    else echo "[build] Skipping heavy weights download (INCLUDE_WEIGHTS=0)"; fi


COPY . .
RUN chmod +x /entrypoint.sh

CMD ["/entrypoint.sh"]