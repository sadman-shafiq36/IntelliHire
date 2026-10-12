# slim base, the system libs it needs are added back below
FROM python:3.12-slim AS base

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
