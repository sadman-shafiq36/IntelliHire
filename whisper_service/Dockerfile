# faster-whisper on cpu, separate image so the model and runtime stay out of the api
FROM python:3.12.15-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_HOME=/models

WORKDIR /app

# curl is only for the healthcheck. no ffmpeg package, PyAV bundles its own
RUN apt-get update && apt-get install --no-install-recommends -y \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY server.py .

# weights download once into the /models volume
RUN useradd --create-home --uid 1000 whisper \
    && mkdir -p /models \
    && chown -R whisper:whisper /app /models
USER whisper

EXPOSE 9000

HEALTHCHECK --interval=15s --timeout=5s --start-period=180s --retries=10 \
    CMD curl --fail --silent http://localhost:9000/health || exit 1

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "9000"]
