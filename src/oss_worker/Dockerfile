FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# System deps for OpenCV runtime + mp4 encoding on slim images
RUN apt-get update && \
	apt-get install -y --no-install-recommends \
		ffmpeg \
		libglib2.0-0 \
		libsm6 \
		libxext6 \
		libgl1 \
	&& rm -rf /var/lib/apt/lists/*

# Install deps using the single repo requirements file.
# (Torch wheels are installed separately so GPU/CPU choice is controllable.)
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Optional: heavier thumbnail pipeline deps (YOLO segmentation + rembg + ESRGAN).
# Keep this off by default so the worker image stays build-stable.
ARG INSTALL_ADV_THUMBNAIL_DEPS=false
RUN if [ "$INSTALL_ADV_THUMBNAIL_DEPS" = "true" ]; then \
		pip install --no-cache-dir ultralytics rembg realesrgan onnxruntime-gpu; \
	fi

# Torch install (default: CUDA wheels for typical AKS GPU nodes; override as needed)
# Note: if you want CPU-only worker, set OSS_DIFFUSERS_DEVICE=cpu and use CPU torch wheels.
RUN pip install --no-cache-dir torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121

COPY oss_worker /app/oss_worker

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "oss_worker.app:app", "--host", "0.0.0.0", "--port", "8000"]
