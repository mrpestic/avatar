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
RUN mkdir -p /ComfyUI/models


COPY . .
RUN chmod +x /entrypoint.sh

CMD ["/entrypoint.sh"]