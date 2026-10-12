"""v38 Quant AI 모델 라인업 — '어떤 일에 어떤 AI 를 쓸지'를 우리가 정한다.

사용자는 공급자 이름(Gemini·Groq…) 대신 일의 종류로 고른다:
  · QA 리서치  — 웹 검색으로 최신 소식·발표·실적 일정까지 찾아 출처와 함께 (Gemini 검색 근거 → Groq compound → 아무 모델 + 공용 검색 도구)
  · QA 심층    — 오래 생각하는 분석 (Claude → Gemini Pro → NVIDIA → Groq)
  · QA 빠름    — 짧고 빠른 답 (Groq → Gemini Flash → Cloudflare)
  · QA 로컬    — 내 PC 의 LLM (키·인터넷 없이)
  · 규칙       — AI 키 없이 플랫폼 데이터만 요약
  · 자동       — 질문과 '웹 검색' 스위치를 보고 위에서 고른다
웹 검색 방식: native(모델이 직접 검색 · 출처 받음) · tool(우리 검색 도구를 모델이 불러 씀) · rules(검색 결과 목록만).
역할별 지정(뉴스 AI·경제 AI·Risk AI·공시 AI → 공급자)은 ai_model_prefs 상태로 저장 → assign_roles 가 따른다.
"""

from __future__ import annotations

from . import ops

PREFS = "ai_model_prefs"
PRESETS: dict[str, dict] = {
    "auto": {"label": "자동", "desc": "질문과 '웹 검색' 스위치를 보고 알맞은 모델을 골라요", "chain": ()},
    "research": {"label": "QA 리서치", "desc": "웹에서 최신 소식·발표·실적 일정까지 찾아 출처와 함께 답해요",
                 "chain": (("gemini", ("gemini-flash-latest", "gemini-pro-latest")), ("groq", ("groq/compound", "groq/compound-mini")),
                           ("claude", ()), ("nvidia", ()), ("cloudflare", ()), ("local", ())), "web": True},
    "deep": {"label": "QA 심층", "desc": "근거를 많이 보고 오래 생각하는 분석 (느리지만 꼼꼼)",
             "chain": (("claude", ()), ("gemini", ("gemini-pro-latest", "gemini-flash-latest")), ("nvidia", ()), ("groq", ()))},
    "fast": {"label": "QA 빠름", "desc": "짧고 빠른 답 (무료 한도를 아껴요)",
             "chain": (("groq", ("openai/gpt-oss-120b", "llama-3.3-70b-versatile")), ("gemini", ("gemini-flash-lite-latest", "gemini-flash-latest")),
                       ("cloudflare", ()), ("nvidia", ()))},
    "local": {"label": "QA 로컬", "desc": "내 PC 에서 도는 AI — 키·인터넷 없이 (Ollama 등)", "chain": (("local", ()),)},
    "rules": {"label": "규칙", "desc": "AI 키 없이 플랫폼 데이터만 요약해요", "chain": ()},
}
NATIVE_WEB = {"gemini": "gemini_grounding", "groq": "groq_compound"}
ROLE_LABEL = {"primary": "뉴스 AI", "nvidia": "경제·시장 AI", "panel": "공시·실적 AI", "risk": "Risk AI"}
PROVIDER_LABEL = {"gemini": "Google Gemini", "groq": "Groq", "nvidia": "NVIDIA", "cloudflare": "Cloudflare", "local": "내 PC LLM",
                  "claude": "Anthropic Claude"}


def available(settings) -> set[str]:
    return set(settings.llm_providers) | ({"claude"} if getattr(settings, "anthropic_enabled", False) else set())


def prefs(engine) -> dict:
    p = ops.get_state(engine, PREFS) or {}
    return {"chat": p.get("chat") if p.get("chat") in PRESETS else "auto", "web": bool(p.get("web")),
            "roles": {k: v for k, v in (p.get("roles") or {}).items() if k in ROLE_LABEL}}


def set_prefs(engine, body: dict, settings=None) -> dict:
    cur = prefs(engine)
    if body.get("chat") in PRESETS:
        cur["chat"] = body["chat"]
    if "web" in body:
        cur["web"] = bool(body["web"])
    if isinstance(body.get("roles"), dict):
        avail = available(settings) if settings is not None else None
        for role, prov in body["roles"].items():
            if role not in ROLE_LABEL:
                continue
            if not prov:
                cur["roles"].pop(role, None)
            elif avail is None or prov in avail:
                cur["roles"][role] = prov
            else:
                raise ValueError(f"{PROVIDER_LABEL.get(prov, prov)} 키가 없어요 — 먼저 설정 → 키에서 넣어 주세요")
    ops.set_state(engine, PREFS, cur)
    return cur


def plan(settings, preset: str = "auto", web: bool = False, text: str = "") -> dict:
    """채팅 한 번을 어떤 모델로 · 웹 검색은 어떻게. 키가 없는 공급자는 건너뛴다."""
    avail = available(settings)
    preset = preset if preset in PRESETS else "auto"
    asked = preset
    if preset == "auto":
        if web:
            preset = "research"
        elif len(text) > 280 or any(k in text for k in ("자세히", "깊게", "심층", "분석해", "비교해")):
            preset = "deep"
        else:
            preset = "fast"
        if not avail:
            preset = "rules"
    if preset == "rules":
        return {"preset": "rules", "asked": asked, "provider": None, "models": (), "web": "rules" if web else None,
                "label": PRESETS["rules"]["label"]}
    for prov, models in PRESETS[preset]["chain"]:
        if prov in avail:
            w = None
            if web:
                w = NATIVE_WEB.get(prov) or "tool"
                if prov == "groq" and not any(m.startswith("groq/compound") for m in models):
                    w = "tool"
            return {"preset": preset, "asked": asked, "provider": prov, "models": tuple(models), "web": w,
                    "label": PRESETS[preset]["label"]}
    # 이 묶음에 쓸 수 있는 공급자가 없으면: 다른 아무 공급자(웹은 도구로) → 그래도 없으면 규칙
    for prov in ("gemini", "groq", "claude", "nvidia", "cloudflare", "local"):
        if prov in avail:
            return {"preset": preset, "asked": asked, "provider": prov, "models": (), "web": ("tool" if web else None),
                    "label": PRESETS[preset]["label"], "fallback": True}
    return {"preset": "rules", "asked": asked, "provider": None, "models": (), "web": "rules" if web else None,
            "label": PRESETS["rules"]["label"], "fallback": preset != "rules"}


def catalog(settings, engine=None) -> dict:
    """화면용: 모델마다 쓸 수 있나 · 지금 무엇으로 연결되나 · 웹 검색 방식."""
    avail = available(settings)
    rows = []
    for key, p in PRESETS.items():
        pl = plan(settings, key, web=bool(p.get("web")))
        chain = [{"provider": prov, "label": PROVIDER_LABEL.get(prov, prov), "ready": prov in avail,
                  "web": (NATIVE_WEB.get(prov) if prov in NATIVE_WEB else "tool") if p.get("web") else None}
                 for prov, _ in p.get("chain") or ()]
        ready = key in ("auto", "rules") or any(c["ready"] for c in chain)
        rows.append({"key": key, "label": p["label"], "desc": p["desc"], "ready": ready, "chain": chain,
                     "now": None if not pl.get("provider") else PROVIDER_LABEL.get(pl["provider"], pl["provider"]),
                     "web": bool(p.get("web")), "web_mode": pl.get("web")})
    try:
        from .analysts.analysts import assign_roles
        roles = assign_roles(settings, prefs(engine)["roles"] if engine is not None else None)
    except Exception:  # noqa: BLE001
        roles = {}
    role_rows = [{"role": r, "label": lab, "provider": roles.get(r), "provider_label": PROVIDER_LABEL.get(roles.get(r), "규칙 (키 없음)"),
                  "fixed": bool(engine is not None and prefs(engine)["roles"].get(r))} for r, lab in ROLE_LABEL.items()]
    return {"presets": rows, "roles": role_rows, "providers": [{"key": k, "label": PROVIDER_LABEL[k], "ready": k in avail}
                                                               for k in PROVIDER_LABEL],
            "prefs": prefs(engine) if engine is not None else None,
            "selftest": (ops.get_state(engine, "ai_selftest") or None) if engine is not None else None,
            "note": "키가 있는 공급자만 고를 수 있어요 · 웹 검색은 키 없이도 '규칙' 으로 결과 목록을 보여 줘요"}


def selftest(app, say=None, post=None, search=None, client_factory=None) -> dict:
    """키를 넣은 뒤 '정말 되나' 한 번에: 공급자마다 아주 짧은 요청 1번 + 공용 웹 검색 + (Gemini 키가 있으면) 검색 근거.
    키 값은 어디에도 남기지 않는다 — 결과는 '됨/안 됨 + 쉬운 이유'만 (상태 ai_selftest)."""
    import time
    from datetime import UTC, datetime

    from . import websearch
    from .assistant import chat_client
    from .refresh import friendly
    say = say or (lambda m: None)
    factory = client_factory or chat_client
    rows = []

    def run(name, kind, fn):
        say(f"{name} 확인 중…")
        t0 = time.monotonic()
        try:
            detail = fn()
            rows.append({"name": name, "kind": kind, "ok": True, "detail": detail, "ms": int((time.monotonic() - t0) * 1000)})
        except Exception as e:  # noqa: BLE001 - 결과에 이유로 남긴다
            rows.append({"name": name, "kind": kind, "ok": False, "detail": friendly(e), "raw": f"{type(e).__name__}"[:60],
                         "ms": int((time.monotonic() - t0) * 1000)})
    for prov in sorted(available(app.settings)):
        def ping(prov=prov):
            client, _ = factory(app.settings, {"provider": prov, "models": ()})
            if client is None:
                raise RuntimeError("키를 읽지 못했어요 (.env 확인)")
            out = client._post("/chat/completions", {"model": client.models[0], "max_tokens": 8, "temperature": 0,
                                                      "messages": [{"role": "user", "content": "OK 라고만 답해"}]})
            txt = ((out.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            return f"{client.models[0]} 응답함" + (f" ('{txt.strip()[:12]}')" if txt else "")
        run(PROVIDER_LABEL.get(prov, prov), "model", ping)

    def web():
        r = (search or websearch.search)("NVIDIA earnings date", kind="auto", days=7, n=3)
        if not r.get("results"):
            raise RuntimeError(r.get("error") or "검색 결과 없음")
        return f"검색 결과 {len(r['results'])}건 (예: {r['results'][0]['title'][:40]})"
    run("공용 웹 검색 (Google 뉴스 · DuckDuckGo)", "web", web)
    gem = (app.settings.llm_providers.get("gemini") or {}).get("key")
    if gem:
        def ground():
            g = websearch.gemini_grounded(gem, "gemini-flash-latest", "한 줄로 답한다.", "오늘 미국 증시 뉴스 한 가지만 제목으로", post=post)
            if not g.get("text"):
                raise RuntimeError("빈 답")
            return f"답함 · 웹 출처 {len(g.get('sources') or [])}개"
        run("Gemini Google 검색 근거", "grounding", ground)
    ok = sum(r["ok"] for r in rows)
    st = {"at": datetime.now(UTC).isoformat(), "rows": rows, "ok": ok, "n": len(rows),
          "headline": ("모두 정상" if ok == len(rows) else f"{len(rows) - ok}개 안 됨") + f" · {ok}/{len(rows)}"}
    ops.set_state(app.engine, "ai_selftest", st)
    return st


__all__ = ["PRESETS", "plan", "catalog", "prefs", "set_prefs", "available", "selftest"]
