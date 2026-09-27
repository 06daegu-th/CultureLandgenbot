# ---- Quant AI 런타임 이미지 (스케줄러 / 웹 공용)
FROM python:3.11-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
# git: 스케줄러가 KRX 데이터(FinanceData/marcap)를 받고 매일 갱신
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 10001 quant
COPY pyproject.toml README.md ./
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
RUN pip install ".[postgres,ai,yahoo]"
# 명명 볼륨은 이미지의 /data 소유권을 물려받는다 → 비루트 사용자가 쓸 수 있게 미리 만든다
RUN mkdir -p /data/artifacts && chown -R quant:quant /data
USER quant
ENV QUANT_ARTIFACTS_DIR=/data/artifacts QUANT_MARCAP_DIR=/data/marcap/data
VOLUME ["/data"]
EXPOSE 8050
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD ["quant-ai", "health"]
CMD ["quant-ai", "run", "--mode", "shadow"]
