# ---- Quant AI 런타임 이미지 (스케줄러 / 웹 공용)
FROM python:3.11-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN useradd --create-home --uid 10001 quant
COPY pyproject.toml README.md ./
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
RUN pip install ".[postgres,ai,yahoo]" alembic
USER quant
ENV QUANT_ARTIFACTS_DIR=/data/artifacts
VOLUME ["/data"]
EXPOSE 8050
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD ["quant-ai", "health"]
CMD ["quant-ai", "run", "--mode", "shadow"]
