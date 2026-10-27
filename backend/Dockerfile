# slim base, the system libs it needs are added back below
FROM python:3.12.15-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# tesseract-ocr + poppler-utils   ocr fallback for scanned cvs
# libpango / libharfbuzz / libffi weasyprint, renders the pdf report
# build-essential                 wheels that have no prebuilt binary
RUN apt-get update && apt-get install --no-install-recommends -y \
        build-essential \
        curl \
        tesseract-ocr \
        poppler-utils \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz0b \
        libffi8 \
        shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

# deps before source so editing code doesn't bust the pip layer
COPY requirements.txt requirements-dev.txt ./
RUN pip install -r requirements.txt

# opencv's face models, outside /app so the dev bind mount doesn't hide them.
# yunet (mit) finds faces, sface (apache 2.0) turns one into a 128-number template
ARG HF=https://huggingface.co/opencv
RUN mkdir -p /opt/face-models \
    && curl -fsSL -o /opt/face-models/yunet.onnx \
        $HF/face_detection_yunet/resolve/3cc26e7f1014a5ee5d74a42acee58bafc9d0a310/face_detection_yunet_2023mar.onnx \
    && curl -fsSL -o /opt/face-models/sface.onnx \
        $HF/face_recognition_sface/resolve/3d7082438a6e4551e840c9b2bb60b71e8da4b524/face_recognition_sface_2021dec.onnx \
    && echo "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4  /opt/face-models/yunet.onnx" | sha256sum -c - \
    && echo "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79  /opt/face-models/sface.onnx" | sha256sum -c -

COPY . .

# don't run as root
RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /data/storage \
    && chown -R appuser:appuser /app /data
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD curl --fail --silent http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]


# dev target: test/lint tools + reload, used by docker-compose.yml
FROM base AS dev

USER root
RUN pip install -r requirements-dev.txt
USER appuser

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
