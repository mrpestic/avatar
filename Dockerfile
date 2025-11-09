# Use specific version of nvidia cuda image
FROM wlsdml1114/multitalk-base:1.4 AS runtime

ENV PIP_NO_CACHE_DIR=1
ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility
ENV CUDA_VISIBLE_DEVICES=0

# wget 설치 (URL 다운로드를 위해)
RUN apt-get update && apt-get install -y wget && rm -rf /var/lib/apt/lists/*

RUN pip install -U --no-cache-dir "huggingface_hub[hf_transfer]"
RUN pip install --no-cache-dir runpod websocket-client librosa

WORKDIR /

# Clone ComfyUI and install dependencies
RUN git clone https://github.com/comfyanonymous/ComfyUI.git && \
    cd /ComfyUI && \
    pip install --no-cache-dir -r requirements.txt

# Install ComfyUI-Manager
RUN cd /ComfyUI/custom_nodes && \
    git clone --depth 1 https://github.com/Comfy-Org/ComfyUI-Manager.git && \
    cd ComfyUI-Manager && \
    pip install --no-cache-dir -r requirements.txt

# Install ComfyUI-GGUF
RUN cd /ComfyUI/custom_nodes && \
    git clone --depth 1 https://github.com/city96/ComfyUI-GGUF && \
    cd ComfyUI-GGUF && \
    pip install --no-cache-dir -r requirements.txt

# Install ComfyUI-KJNodes
RUN cd /ComfyUI/custom_nodes && \
    git clone --depth 1 https://github.com/kijai/ComfyUI-KJNodes && \
    cd ComfyUI-KJNodes && \
    pip install --no-cache-dir -r requirements.txt

# Install ComfyUI-VideoHelperSuite
RUN cd /ComfyUI/custom_nodes && \
    git clone --depth 1 https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite && \
    cd ComfyUI-VideoHelperSuite && \
    pip install --no-cache-dir -r requirements.txt

# Install ComfyUI-wanBlockswap
RUN cd /ComfyUI/custom_nodes && \
    git clone --depth 1 https://github.com/orssorbit/ComfyUI-wanBlockswap

# Install ComfyUI-MelBandRoFormer
RUN cd /ComfyUI/custom_nodes && \
    git clone --depth 1 https://github.com/kijai/ComfyUI-MelBandRoFormer && \
    cd ComfyUI-MelBandRoFormer && \
    pip install --no-cache-dir -r requirements.txt

# Install ComfyUI-WanVideoWrapper
RUN cd /ComfyUI/custom_nodes && \
    git clone --depth 1 https://github.com/kijai/ComfyUI-WanVideoWrapper && \
    cd ComfyUI-WanVideoWrapper && \
    pip install --no-cache-dir -r requirements.txt


# Model directories (weights will be cached in /runpod-volume at runtime)
RUN mkdir -p /ComfyUI/models/diffusion_models \
    /ComfyUI/models/loras \
    /ComfyUI/models/vae \
    /ComfyUI/models/text_encoders \
    /ComfyUI/models/clip_vision

RUN wget -q --show-progress https://huggingface.co/Kijai/WanVideo_comfy_GGUF/resolve/main/InfiniteTalk/Wan2_1-InfiniteTalk_Single_Q8.gguf -O /ComfyUI/models/diffusion_models/Wan2_1-InfiniteTalk_Single_Q8.gguf
RUN wget -q --show-progress https://huggingface.co/Kijai/WanVideo_comfy_GGUF/resolve/main/InfiniteTalk/Wan2_1-InfiniteTalk_Multi_Q8.gguf -O /ComfyUI/models/diffusion_models/Wan2_1-InfiniteTalk_Multi_Q8.gguf
RUN wget -q --show-progress https://huggingface.co/city96/Wan2.1-I2V-14B-480P-gguf/resolve/main/wan2.1-i2v-14b-480p-Q8_0.gguf -O /ComfyUI/models/diffusion_models/wan2.1-i2v-14b-480p-Q8_0.gguf
RUN wget -q --show-progress https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Lightx2v/lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors -O /ComfyUI/models/loras/lightx2v_I2V_14B_480p_cfg_step_distill_rank64_bf16.safetensors
RUN wget -q --show-progress https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/Wan2_1_VAE_bf16.safetensors -O /ComfyUI/models/vae/Wan2_1_VAE_bf16.safetensors
RUN wget -q --show-progress https://huggingface.co/Kijai/WanVideo_comfy/resolve/main/umt5-xxl-enc-bf16.safetensors -O /ComfyUI/models/text_encoders/umt5-xxl-enc-bf16.safetensors
RUN wget -q --show-progress https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/clip_vision/clip_vision_h.safetensors -O /ComfyUI/models/clip_vision/clip_vision_h.safetensors
RUN wget -q --show-progress https://huggingface.co/Kijai/MelBandRoFormer_comfy/resolve/main/MelBandRoformer_fp16.safetensors -O /ComfyUI/models/diffusion_models/MelBandRoformer_fp16.safetensors


COPY . .
RUN chmod +x /entrypoint.sh

CMD ["/entrypoint.sh"]