"""PostgreSQL 스키마 (SQLAlchemy ORM).

운영은 PostgreSQL, 테스트/로컬 실험은 SQLite 로 같은 스키마를 쓴다.
JSON 컬럼은 PostgreSQL 에서 JSONB 로 저장된다.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JSONType = JSON().with_variant(JSONB(), "postgresql")
# SQLite 는 BIGINT 를 자동 증가 PK 로 쓰지 못하므로 Integer 로 대체
BigId = BigInteger().with_variant(Integer(), "sqlite")


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------- 시장 데이터
class Instrument(Base):
    __tablename__ = "instruments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32))
    market: Mapped[str] = mapped_column(String(16))  # KRX / US / ...
    name: Mapped[str | None] = mapped_column(String(128))
    currency: Mapped[str] = mapped_column(String(8), default="KRW")
    sector: Mapped[str | None] = mapped_column(String(64))
    keywords: Mapped[list | None] = mapped_column(JSONType)  # 뉴스 태깅용 별칭
    __table_args__ = (UniqueConstraint("symbol", "market"),)


class PriceBar(Base):
    __tablename__ = "price_bars"
    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32))
    interval: Mapped[str] = mapped_column(String(8))  # 1d, 1m ...
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(32))
    __table_args__ = (UniqueConstraint("symbol", "interval", "ts"),)


class QuoteSnapshot(Base):
    """실시간 호가 스냅샷."""

    __tablename__ = "quote_snapshots"
    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    bids: Mapped[list] = mapped_column(JSONType)  # [[price, qty], ...]
    asks: Mapped[list] = mapped_column(JSONType)
    __table_args__ = (Index("ix_quote_symbol_ts", "symbol", "ts"),)


class Tick(Base):
    """실시간 체결."""

    __tablename__ = "ticks"
    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    price: Mapped[float] = mapped_column(Float)
    qty: Mapped[float] = mapped_column(Float)
    side: Mapped[str | None] = mapped_column(String(4))
    __table_args__ = (Index("ix_tick_symbol_ts", "symbol", "ts"),)


# ------------------------------------------------------- 뉴스/공시/경제지표
class NewsArticle(Base):
    __tablename__ = "news_articles"
    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    source: Mapped[str] = mapped_column(String(64))
    url: Mapped[str] = mapped_column(String(1024), unique=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    title: Mapped[str] = mapped_column(Text)
    body: Mapped[str | None] = mapped_column(Text)
    symbols: Mapped[list | None] = mapped_column(JSONType)
    sentiment: Mapped[float | None] = mapped_column(Float)
    events: Mapped[list | None] = mapped_column(JSONType)
    importance: Mapped[float | None] = mapped_column(Float)


class Disclosure(Base):
    __tablename__ = "disclosures"
    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    source: Mapped[str] = mapped_column(String(32))  # DART, SEC ...
    receipt_no: Mapped[str] = mapped_column(String(32), unique=True)
    corp_code: Mapped[str | None] = mapped_column(String(16))
    corp_name: Mapped[str | None] = mapped_column(String(128))
    symbol: Mapped[str | None] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(Text)
    filed_at: Mapped[date] = mapped_column(Date)
    url: Mapped[str] = mapped_column(String(1024))
    sentiment: Mapped[float | None] = mapped_column(Float)
    events: Mapped[list | None] = mapped_column(JSONType)


class MacroObservation(Base):
    __tablename__ = "macro_observations"
    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    series_id: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(32))
    ts: Mapped[date] = mapped_column(Date)
    value: Mapped[float] = mapped_column(Float)
    __table_args__ = (UniqueConstraint("series_id", "ts"),)


# ------------------------------------------------------ 모델 / 예측 / 시나리오
class ModelRecord(Base):
    __tablename__ = "model_registry"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    version: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))  # candidate/shadow/champion/rejected/retired
    params: Mapped[dict | None] = mapped_column(JSONType)
    metrics: Mapped[dict | None] = mapped_column(JSONType)
    shadow_metrics: Mapped[dict | None] = mapped_column(JSONType)
    artifact_path: Mapped[str | None] = mapped_column(String(512))
    notes: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("name", "version"),)


class PredictionRecord(Base):
    __tablename__ = "predictions"
    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    model_id: Mapped[int | None] = mapped_column(ForeignKey("model_registry.id"))
    symbol: Mapped[str] = mapped_column(String(32))
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # 예측에 사용한 마지막 데이터 시점
    horizon_bars: Mapped[int] = mapped_column(Integer)
    prob_up: Mapped[float] = mapped_column(Float)
    direction: Mapped[str] = mapped_column(String(8))  # up/down/neutral
    regime: Mapped[str | None] = mapped_column(String(32))
    rationale: Mapped[dict | None] = mapped_column(JSONType)
    # 사후 채움 (복기)
    realized_return: Mapped[float | None] = mapped_column(Float)
    correct: Mapped[bool | None] = mapped_column(Boolean)
    __table_args__ = (Index("ix_pred_symbol_asof", "symbol", "as_of"),)


class Scenario(Base):
    __tablename__ = "scenarios"
    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    target_date: Mapped[date] = mapped_column(Date)
    symbol: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSONType)


# ------------------------------------------------------------- 주문 / 체결
class OrderRecord(Base):
    __tablename__ = "orders"
    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    mode: Mapped[str] = mapped_column(String(16))  # paper/shadow/live/backtest
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    symbol: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(4))
    qty: Mapped[float] = mapped_column(Float)
    order_type: Mapped[str] = mapped_column(String(16))
    limit_price: Mapped[float | None] = mapped_column(Float)
    # pending(제출 직전) → submitted(증권사 접수) → filled/partial/unfilled/cancelled · rejected(리스크) · error · unknown
    status: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(Text)
    broker_order_id: Mapped[str | None] = mapped_column(String(64))
    prediction_id: Mapped[int | None] = mapped_column(ForeignKey("predictions.id"))
    # 멱등성 · 복구 · Evidence Chain · 슬리피지 (0002)
    client_order_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    broker_orgno: Mapped[str | None] = mapped_column(String(16))
    consensus_id: Mapped[int | None] = mapped_column(BigInteger)
    ref_price: Mapped[float | None] = mapped_column(Float)
    filled_qty: Mapped[float | None] = mapped_column(Float)
    avg_price: Mapped[float | None] = mapped_column(Float)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FillRecord(Base):
    __tablename__ = "fills"
    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    qty: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    fee: Mapped[float] = mapped_column(Float)


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"
    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    mode: Mapped[str] = mapped_column(String(16))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cash: Mapped[float] = mapped_column(Float)
    equity: Mapped[float] = mapped_column(Float)
    positions: Mapped[dict] = mapped_column(JSONType)


# ------------------------------------------------------------- 매매일지 / 복기
class JournalEntry(Base):
    __tablename__ = "journal_entries"
    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    mode: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(32))  # signal/order/fill/risk_block/note ...
    symbol: Mapped[str | None] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict | None] = mapped_column(JSONType)


class ReviewReport(Base):
    __tablename__ = "review_reports"
    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    review_date: Mapped[date] = mapped_column(Date)
    mode: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    summary: Mapped[dict] = mapped_column(JSONType)
    lessons: Mapped[list | None] = mapped_column(JSONType)


# ---------------------------------------------------------- 멀티 AI / 앙상블
class AnalystOpinionRecord(Base):
    """각 AI 애널리스트의 독립 의견. 사후에 실제 결과로 채점되어 성적표가 된다."""

    __tablename__ = "analyst_opinions"
    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    consensus_id: Mapped[int | None] = mapped_column(ForeignKey("consensus_signals.id"))
    analyst: Mapped[str] = mapped_column(String(32))  # primary / nvidia / quant / regime / risk
    symbol: Mapped[str] = mapped_column(String(32))
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    horizon_bars: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String(32))  # news / macro / direction / trend / risk ...
    prob_up: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    veto: Mapped[bool] = mapped_column(Boolean, default=False)
    payload: Mapped[dict | None] = mapped_column(JSONType)  # 근거, 리스크 요인 등
    # 사후 채점
    realized_return: Mapped[float | None] = mapped_column(Float)
    correct: Mapped[bool | None] = mapped_column(Boolean)
    __table_args__ = (Index("ix_opinion_analyst_cat", "analyst", "category"),)


class ConsensusRecord(Base):
    __tablename__ = "consensus_signals"
    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32))
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    action: Mapped[str] = mapped_column(String(16))  # BUY / SELL / HOLD / NO_TRADE
    prob_up: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)  # 0~100
    conflict: Mapped[str] = mapped_column(String(8))  # low / medium / high
    payload: Mapped[dict] = mapped_column(JSONType)
    realized_return: Mapped[float | None] = mapped_column(Float)
    correct: Mapped[bool | None] = mapped_column(Boolean)


class MemoryDoc(Base):
    """RAG 메모리: 뉴스, 과거 이벤트, 과거 AI 판단과 결과를 임베딩으로 저장."""

    __tablename__ = "memory_docs"
    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))  # news / event / opinion / review
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    symbol: Mapped[str | None] = mapped_column(String(32))
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list] = mapped_column(JSONType)
    embed_model: Mapped[str] = mapped_column(String(64))
    meta: Mapped[dict | None] = mapped_column(JSONType)


# ------------------------------------------------------------------ 운영
class SystemState(Base):
    """여러 프로세스(스케줄러·웹·CLI)가 공유하는 운영 상태 (킬스위치 등)."""

    __tablename__ = "system_state"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONType)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class JobRun(Base):
    __tablename__ = "job_runs"
    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    job: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ok: Mapped[bool | None] = mapped_column(Boolean)
    error: Mapped[str | None] = mapped_column(Text)
    __table_args__ = (Index("ix_jobrun_job_started", "job", "started_at"),)


class LLMCall(Base):
    """모든 LLM 호출 감사 로그 (비용·지연·실패 추적, 응답 캐시, 설명가능성)."""

    __tablename__ = "llm_calls"
    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(128))
    analyst: Mapped[str | None] = mapped_column(String(32))
    prompt_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16))  # ok / error / cached / budget
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    response: Mapped[dict | None] = mapped_column(JSONType)
    error: Mapped[str | None] = mapped_column(Text)
