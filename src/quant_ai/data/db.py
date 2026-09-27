"""DB 연결과 저장 헬퍼."""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import contextmanager
from datetime import UTC

import pandas as pd
from sqlalchemy import create_engine, insert, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base, PriceBar


def make_engine(url: str, connect_timeout: int | None = None) -> Engine:
    # psycopg3 드라이버를 기본으로 사용
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    args = {"connect_timeout": connect_timeout} if connect_timeout and url.startswith("postgresql") else {}
    return create_engine(url, future=True, pool_pre_ping=True, connect_args=args)


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)


@contextmanager
def session_scope(engine: Engine):
    session = sessionmaker(engine, expire_on_commit=False)()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def to_utc_index(idx: pd.Index) -> pd.DatetimeIndex:
    """모든 시각은 UTC 로 통일한다 (SQLite 는 tz 정보를 잃으므로 naive=UTC 로 간주)."""
    idx = pd.DatetimeIndex(idx)
    return idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")


def _naive_utc(ts) -> object:
    return ts.astimezone(UTC).replace(tzinfo=None) if ts.tzinfo else ts


def upsert_bars(session: Session, symbol: str, bars: pd.DataFrame, interval: str, source: str) -> int:
    """(symbol, interval, ts) 기준으로 없는 봉만 추가하고, 있으면 값을 갱신한다."""
    if bars.empty:
        return 0
    bars = bars.set_axis(to_utc_index(bars.index))
    existing = {
        _naive_utc(b.ts): b
        for b in session.scalars(
            select(PriceBar).where(
                PriceBar.symbol == symbol,
                PriceBar.interval == interval,
                PriceBar.ts >= bars.index.min().to_pydatetime(),
                PriceBar.ts <= bars.index.max().to_pydatetime(),
            )
        )
    }
    new_rows = []
    for ts, o, h, lo, c, v in zip(bars.index, bars["open"], bars["high"], bars["low"], bars["close"],
                                  bars["volume"], strict=True):
        py_ts = ts.to_pydatetime()
        values = dict(open=float(o), high=float(h), low=float(lo), close=float(c), volume=float(v))
        bar = existing.get(_naive_utc(py_ts))
        if bar is None:
            new_rows.append(dict(symbol=symbol, interval=interval, ts=py_ts, source=source, **values))
        else:  # 수정주가는 새 분할이 생기면 과거 값이 바뀐다 → 갱신
            for k, val in values.items():
                setattr(bar, k, val)
    if new_rows:
        session.execute(insert(PriceBar), new_rows)  # 대량 삽입
    return len(new_rows)


def load_bars(session: Session, symbols: Iterable[str], interval: str = "1d") -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        rows = session.scalars(
            select(PriceBar)
            .where(PriceBar.symbol == symbol, PriceBar.interval == interval)
            .order_by(PriceBar.ts)
        ).all()
        if not rows:
            continue
        df = pd.DataFrame(
            [(r.ts, r.open, r.high, r.low, r.close, r.volume) for r in rows],
            columns=["ts", "open", "high", "low", "close", "volume"],
        ).set_index("ts")
        df.index = to_utc_index(df.index)
        out[symbol] = df
    return out
