# the mcp server the scheduling agent calls for gmail, google calendar and smtp
FROM python:3.12.15-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# curl is only for the healthcheck
RUN apt-get update && apt-get install --no-install-recommends -y curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-dev.txt ./
# test tools are small, and having them in the image lets the tests run in compose
RUN pip install -r requirements-dev.txt

COPY . .

RUN useradd --create-home --uid 1000 mcp && chown -R mcp:mcp /app
USER mcp

EXPOSE 8100

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD curl --fail --silent http://localhost:8100/health || exit 1

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8100"]
