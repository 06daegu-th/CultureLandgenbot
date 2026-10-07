"""v32 소액 현실 검증 — '200만원 + 매달 100만원' 같은 작은 계좌로 실제로 할 수 있는 매매만 했다면?

research_lab.small_capital_study 를 16년 KRX 자료(상장폐지 포함)로 돌린 결과를 봉인해 두고,
AI 자동매매의 실제 계좌 관문으로 쓴다: **같은 돈을 지수 ETF 에 적립한 것보다 나았던 규칙이 있을 때만** 실제 돈.

  · 원 단위 · 1주 단위 (그날 실제 가격으로 1주도 못 사면 건너뜀) · 매달 적립 · 수수료·미끄러짐·연도별 거래세
  · 고르는 건 개발 구간(2012~2020)만 · 검증 구간(2021~)은 고른 뒤 한 번 계산 · 시도 횟수로 DSR 보정
결과가 없는 PC 에서는 개발자가 계산해 넣어 둔 결과(assets/smallcap_study.json)를 '참고'로 보여 준다.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import UTC, datetime
from pathlib import Path

from . import ops

STATE = "smallcap_study"
BUNDLED = Path(__file__).parent / "assets" / "smallcap_study.json"
_LOCK = threading.Lock()


def _h(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def compact(res: dict, data_info: dict | None = None) -> dict:
    """화면·봉인용 요약 (그래프용 시계열 없이)."""
    rows = [{k: r[k] for k in ("name", "top_k", "trend_ma", "dev", "holdout", "dev_mix", "holdout_mix", "dev_dsr") if k in r}
            for r in res.get("results", [])]
    out = {"principal": res.get("principal"), "monthly": res.get("monthly"), "periods": res.get("periods"), "etf": res.get("etf"),
           "results": rows, "chosen": res.get("chosen"), "verdict": res.get("verdict"), "n_trials": res.get("n_trials"),
           "data": data_info or {}, "computed_at": datetime.now(UTC).isoformat(timespec="seconds")}
    out["hash"] = _h({k: v for k, v in out.items() if k != "hash"})
    return out


def run(app, marcap_dir: str | None = None, principal: float = 2_000_000, monthly: float = 1_000_000, say=print) -> dict:
    """사용자 PC 의 marcap 자료로 다시 계산해 봉인 저장 (약 1~2분)."""
    from .data.collectors.marcap import build_krx_dataset, default_dir
    from .research_lab import small_capital_study
    d = marcap_dir or default_dir()
    if not d:
        raise ValueError("KRX 16년 자료(marcap)가 없어요 — ./run.sh data 로 받거나 --marcap-dir 로 위치를 알려 주세요")
    years = sorted(int(p.stem.split("-")[-1]) for p in Path(d).glob("marcap-*.parquet"))
    if len(years) < 10:
        raise ValueError(f"자료가 {len(years)}년치뿐이에요 — 개발(2012~2020)·검증(2021~) 구간을 나누려면 2010년부터 필요해요")
    ds = build_krx_dataset(d, max(years[0], 2010), years[-1], top_n=100)
    res = small_capital_study(ds, principal=principal, monthly=monthly, say=say)
    last = max(b.index.max() for b in ds.bars.values())
    out = compact(res, {"source": "KRX marcap (상장폐지 포함)", "from": str(max(years[0], 2010)), "to": str(last.date()), "universe": "월말 시가총액 상위 100"})
    out["origin"] = "local"
    ops.set_state(app.engine, STATE, out)
    return out


def run_background(app, marcap_dir: str | None = None) -> dict:
    if not _LOCK.acquire(blocking=False):
        return {"running": True}

    def job():
        try:
            run(app, marcap_dir, say=lambda *a: None)
        except Exception as e:  # noqa: BLE001 - 화면에 이유를 보여 준다
            ops.set_state(app.engine, STATE + "_error", {"error": f"{type(e).__name__}: {e}"[:300], "at": datetime.now(UTC).isoformat()})
        finally:
            _LOCK.release()
    threading.Thread(target=job, name="smallcap", daemon=True).start()
    return {"started": True}


def load(app) -> dict:
    """내 PC 에서 계산한 결과 → 없으면 함께 들어 있는 참고 결과."""
    st = ops.get_state(app.engine, STATE)
    if st.get("results"):
        st = dict(st)
        st["sealed_ok"] = st.get("hash") == _h({k: v for k, v in st.items() if k not in ("hash", "origin", "sealed_ok")})
        return st
    if BUNDLED.exists():
        b = json.loads(BUNDLED.read_text(encoding="utf-8"))
        b["origin"] = "bundled"
        b["sealed_ok"] = b.get("hash") == _h({k: v for k, v in b.items() if k not in ("hash", "origin", "sealed_ok")})
        return b
    return {}


def status(app) -> dict:
    s = load(app)
    err = ops.get_state(app.engine, STATE + "_error")
    s = s | {"running": _LOCK.locked(), "error": err.get("error") if err and (not s.get("computed_at") or err.get("at", "") > s.get("computed_at", "")) else None}
    if s.get("etf"):
        e = s["etf"]["holdout"]
        best = max(s["results"], key=lambda r: r["holdout"]["final"]) if s.get("results") else None
        s["headline"] = (f"{s['periods']['holdout'][0][:4]}년부터 매달 {s['monthly'] / 1e4:,.0f}만원: 지수 ETF 적립 {e['final'] / 1e8:,.2f}억 "
                         f"(넣은 돈 {e['paid'] / 1e8:,.2f}억)" + (f" · 가장 나은 종목 고르기 규칙 {best['holdout']['final'] / 1e8:,.2f}억" if best else ""))
    return s


def gate(app) -> dict:
    """AI 자동매매 실제 계좌 관문: 소액 검증에서 지수 ETF 적립을 이긴 규칙이 있나."""
    s = load(app)
    if not s.get("verdict"):
        return {"ok": False, "detail": "소액 검증 결과가 없어요 — ./run.sh smallcap (KRX 16년 자료 필요)"}
    v = s["verdict"]
    ok = bool(v.get("beat_etf_holdout")) and bool(s.get("sealed_ok"))
    return {"ok": ok, "detail": v.get("text", "") + (" · 개발자 PC 결과(참고)" if s.get("origin") == "bundled" else "")}


__all__ = ["run", "run_background", "load", "status", "gate", "compact", "STATE"]
