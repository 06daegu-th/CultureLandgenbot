/* v27 토스식 화면 2 — 메뉴의 나머지 화면(AI · 포트폴리오 · 기록 · 시스템)을 화면마다 새로 그린다.
   원칙: 맨 위 한 줄 결론(큰 글씨) → 숫자 몇 개 → 목록. 표 대신 행, 영어 대신 우리말, 어려운 말은 눌러서 풀이.
   예전(전문가용) 화면은 그대로 두고 각 화면 아래 '전문가용 전체 화면'(#화면//full)으로 언제든 넘어간다. */
"use strict";

const T2 = { aiWin: "100", ordMode: "all", ledF: "all", jrF: "all", ctlTab: "stage", setTab: "screen" };
const TV = {};  // 화면 이름 → 그리는 함수 (toss.js 의 tossOwns / tossRender 가 본다)

// ------------------------------------------------------------ 공통 부품
const LV_GOOD = new Set(["ok", "good", "green", "pass", "passed", "live", "verified", "stable", "healthy", "done", "ready", "better", "impl", "running", "alive", "pass_", "ok_", "on", "filled", "proven", "intact"]);
const LV_WARN = new Set(["warn", "yellow", "watch", "partial", "checking", "setup", "pending", "insufficient", "continue", "shadow", "caution", "degraded", "stale", "slow", "manual", "orange", "inconclusive", "observe"]);
const LV_BAD = new Set(["bad", "red", "fail", "failed", "worse", "banned", "error", "blocked", "drift", "decaying", "halted", "not_ready", "rejected", "down", "critical", "tampered", "broken", "stop", "dead"]);
function tLv(s) {
  const k = String(s ?? "").toLowerCase().replace(/[^a-z_]/g, "");
  if (LV_GOOD.has(k)) return "good";
  if (LV_WARN.has(k)) return "warn";
  if (LV_BAD.has(k)) return "bad";
  return "idle";
}
const LV_KO = { good: "정상", warn: "주의", bad: "문제", idle: "기록 없음" };
const tSt = (s, label) => { const lv = ["good", "warn", "bad", "idle"].includes(s) ? s : tLv(s); return `<span class="t-st st-${lv}">${esc(label || LV_KO[lv])}</span>`; };
const tPr = (v, d = 0) => v == null || Number.isNaN(v) ? "-" : `${(v * 100).toFixed(d)}%`;       // 0.42 → 42%
const tPp = (v, d = 1) => v == null || Number.isNaN(v) ? "-" : `${v > 0 ? "+" : ""}${(v * 100).toFixed(d)}%p`;
const tN = (v, d = 0) => v == null || Number.isNaN(v) ? "-" : num(v, d);
const tAgo = (iso) => {
  if (!iso) return "";
  const t = new Date(/Z|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`).getTime();
  if (!t) return String(iso);
  const m = Math.round((Date.now() - t) / 60000);
  return m < 1 ? "방금" : m < 60 ? `${m}분 전` : m < 1440 ? `${Math.round(m / 60)}시간 전` : m < 1440 * 30 ? `${Math.round(m / 1440)}일 전` : tDate(iso);
};
// 머리: ← 제목 (메뉴 이름) · 오른쪽 단추
function tHead(title, right = "") {
  return `<div class="t2-head"><button class="t-back" aria-label="뒤로">${TI.back}</button><h1 class="t-h1">${title}</h1><div class="t2-head-r">${right}</div></div>`;
}
function tBindHead(root) {
  const b = root.querySelector(".t2-head .t-back");
  if (b) b.onclick = () => { if (T_NAV.length) history.back(); else location.hash = uiMode() === "easy" ? "#more" : "#dashboard"; };
}
// 큰 결론 칸
function tHero(o) {
  return `<section class="t-hero2 lv-${o.lv || "idle"}">${o.k ? `<span class="t-k">${o.k}</span>` : ""}<b class="t-big ${o.bigCls || ""}">${o.big}</b>
    ${o.sub ? `<div class="t-hero2-sub">${o.sub}</div>` : ""}${o.body || ""}${o.foot ? `<div class="t-foot">${o.foot}</div>` : ""}</section>`;
}
// 숫자 칸 묶음: [[이름, 값, 색 class, 작은 설명]]
const tKV = (xs) => `<div class="t2-kv">${xs.filter(Boolean).map(([k, v, c, s]) => `<div><span class="t-k">${k}</span><b class="num ${c || ""}">${v}</b>${s ? `<em>${s}</em>` : ""}</div>`).join("")}</div>`;
// 진행 막대
const tProg = (f, lv = "", label = "") => `<div class="t-prog ${lv ? `lv-${lv}` : ""}" role="progressbar" aria-valuenow="${Math.round((f || 0) * 100)}" aria-valuemin="0" aria-valuemax="100"${label ? ` aria-label="${esc(label)}"` : ""}><i style="width:${Math.max(0, Math.min(100, (f || 0) * 100)).toFixed(1)}%"></i></div>`;
// 막대 비교: [[이름, 값 0~1, 색]] — 'AI 42% vs 기준 54%' 를 눈으로
function tVs(rows, o = {}) {
  const mx = o.max || Math.max(0.0001, ...rows.map((r) => Math.abs(r[1] || 0)));
  return `<div class="t-vs">${rows.map(([k, v, c, s]) => `<div class="t-vs-r"><span class="t-vs-k">${k}</span><span class="t-vs-b"><i class="${c || ""}" style="width:${v == null ? 0 : Math.max(1.5, Math.abs(v) / mx * 100).toFixed(1)}%"></i></span><b class="num ${c || ""}">${s ?? (o.fmt ? o.fmt(v) : tPr(v))}</b></div>`).join("")}</div>`;
}
// 상태 행: 점 · 제목 · 설명 / 오른쪽 상태말
function tStRow(title, detail, status, o = {}) {
  const lv = ["good", "warn", "bad", "idle"].includes(status) ? status : tLv(status);
  const tt = title && typeof title === "object" ? title.html : esc(koText(String(title ?? "")));  // {html: ...} = 이미 만든 HTML (용어 풀이 단추 등)
  const inner = `<span class="t-co"><span class="t-dot-ic">${lvDot(lv)}</span><span class="t-co-t"><b class="t2-wrap">${tt}</b>${detail ? `<span class="t2-wrap">${esc(koText(String(detail)))}</span>` : ""}</span></span><span class="t-li-r">${o.right ?? tSt(lv, o.label)}</span>`;
  return o.href ? `<a class="t-li" href="${esc(o.href)}">${inner}</a>` : `<div class="t-li">${inner}</div>`;
}
const tList = (rows, emptyT = "자료가 없어요", emptyS = "") => rows.length ? `<div class="t-list">${rows.join("")}</div>` : tEmpty(emptyT, emptyS);
const tCard = (title, body, right = "") => tSec(title, body, right, "t-card");
function tFull(v, extra = "") {
  return `<div class="t-pro">${extra}<a class="t-btn ghost" href="#${v}//full">전문가용 전체 화면</a></div>`;
}
const tSym = (sym, name, sub = "", right = "", o = {}) => tRow(`#analysis/${encodeURIComponent(sym)}`, tName(sym, name, sub, o.size || 36), right, o);
async function tLoad(el, view, title, cls, fetcher) {
  el.innerHTML = `<div class="ts t2 ${cls}">${tHead(title)}${tSkel(4)}</div>`;
  const root = el.querySelector(".ts");
  tBindHead(root);
  let d;
  try { d = await fetcher(); }
  catch (e) { if (root.isConnected) { root.innerHTML = `${tHead(title)}${tErr(e)}${tFull(view)}`; tBindHead(root); } return null; }
  return root.isConnected ? { root, d } : null;
}
const tSafe = (p) => p.catch((e) => ({ error: e.message || String(e) }));
function tPaint(root, title, html, right = "") { root.innerHTML = tHead(title, right) + html; tBindHead(root); glossify(root); }
const tActKo = (a) => koAct(a === "NO TRADE" ? "NO_TRADE" : a);
const tOk = (c) => c == null ? '<span class="t-tag">채점 전</span>' : c ? '<span class="t-tag up">맞음</span>' : '<span class="t-tag down">틀림</span>';

// ------------------------------------------------------------ 어려운 말 풀이 (눌러서 보기)
const GLOSSARY = {
  "Sharpe": ["샤프 지수", "벌어들인 수익을 '흔들린 정도(변동성)'로 나눈 값. 같은 수익이면 덜 흔들린 쪽이 높다. 1 이상이면 괜찮은 편, 0 아래면 예금보다 못했다는 뜻."],
  "VaR": ["VaR (최대 예상 손실)", "평소 같은 날 100일 중 95일은 이보다 덜 잃는다는 하루 손실선. 예: VaR 3% = 100일에 5일쯤은 3% 넘게 잃을 수 있음."],
  "ES": ["ES (꼬리 평균 손실)", "VaR 선을 넘어간 나쁜 날들만 모아 평균 낸 손실. 정말 나쁜 날 얼마나 아픈지 보여 준다."],
  "MDD": ["MDD (최대 낙폭)", "가장 높았던 때에서 가장 낮았던 때까지 떨어진 크기. -20% 면 한때 고점보다 20% 빠졌었다는 뜻."],
  "Brier": ["Brier 점수", "확률 예측의 오차. 0 이 완벽, 0.25 는 늘 '반반'이라고 말한 수준. 낮을수록 좋다."],
  "Brier Skill": ["Brier Skill (확률 실력)", "AI 확률이 '늘 평균만 말하기'보다 얼마나 나은지. 0 보다 크면 낫고, 0 아래면 차라리 평균을 말하는 게 낫다는 뜻."],
  "ECE": ["ECE (확률 어긋남)", "AI 가 '70%'라고 할 때 실제로 70% 가까이 맞았는지. 0 에 가까울수록 확률을 믿을 수 있다."],
  "PSI": ["PSI (시장 변화 크기)", "AI 가 배운 시기의 시장과 지금 시장이 얼마나 달라졌는지. 0.1 아래 안정, 0.25 넘으면 크게 달라짐."],
  "IC": ["IC (순위 예측력)", "AI 가 매긴 종목 순위와 실제 수익 순위가 얼마나 맞는지. 0.02~0.05 만 꾸준해도 쓸모 있다고 본다."],
  "DSR": ["DSR (보정된 샤프)", "시험을 여러 번 할수록 우연히 좋아 보이는 결과가 늘어나는 것을 깎아 낸 샤프 지수. 0.95 이상이어야 '우연이 아니다'."],
  "PSR": ["PSR", "샤프 지수가 0 보다 클 확률. 0.95 이상이면 꽤 믿을 만하다."],
  "HHI": ["HHI (쏠림 정도)", "돈이 몇 종목에 몰려 있는지. 0 에 가까우면 고르게, 1 에 가까우면 한 종목에 몰림."],
  "베타": ["베타", "시장이 1% 움직일 때 이 종목·포트폴리오가 몇 % 움직이는지. 1.3 이면 시장보다 30% 더 크게 출렁인다."],
  "Beta": ["베타", "시장이 1% 움직일 때 이 종목·포트폴리오가 몇 % 움직이는지. 1.3 이면 시장보다 30% 더 크게 출렁인다."],
  "알파": ["알파 (초과 수익)", "같은 기간 시장(지수)보다 더 번 부분. 시장이 다 같이 오른 덕을 뺀 진짜 실력."],
  "Alpha": ["알파 (초과 수익)", "같은 기간 시장(지수)보다 더 번 부분. 시장이 다 같이 오른 덕을 뺀 진짜 실력."],
  "슬리피지": ["슬리피지 (체결 차이)", "주문할 때 본 가격과 실제로 사고판 가격의 차이. bp 는 0.01% — 10bp = 0.1%."],
  "bp": ["bp (베이시스 포인트)", "0.01%. 10bp = 0.1%, 100bp = 1%."],
  "적중률": ["적중률", "AI 가 '오른다/내린다'고 한 방향이 실제와 맞은 비율. 그냥 '늘 오른다'고 말해도 50% 넘게 맞는 시기가 있어 기준선과 같이 봐야 한다."],
  "기준선": ["기준선", "아무 실력 없이도 얻는 점수: 동전 던지기(50%), '늘 오른다'고 말하기 등. AI 는 이것보다 나아야 쓸모 있다."],
  "확률 보정": ["확률 보정", "AI 가 말한 확률을 지난 성적에 맞춰 고쳐 쓰는 것. 70% 라고 했는데 실제 55% 만 맞았다면 낮춰 준다."],
  "드리프트": ["드리프트 (시장 변화)", "AI 가 배운 시기와 지금 시장 모습이 달라지는 것. 크면 AI 를 다시 학습해야 한다."],
  "봉인": ["봉인", "예측을 적는 순간 지문(해시)을 찍어 두어, 나중에 결과를 보고 몰래 고칠 수 없게 하는 것."],
  "VKOSPI": ["VKOSPI (공포 지수)", "앞으로 한 달 코스피가 얼마나 출렁일지 옵션 가격으로 본 값. 높을수록 시장이 불안하다는 뜻."],
  "NO TRADE": ["거래 안 함", "AI 의견이 약하거나 위험 점검에 걸려 이번엔 사지 않기로 한 판단."],
  "PEAD": ["실적 발표 뒤 흐름 (PEAD)", "실적이 예상보다 좋으면(나쁘면) 발표 뒤 며칠~몇 주 더 오르는(내리는) 경향. 그 경향을 쓰는 전략."],
  "SPRT": ["순차 검정", "결과가 쌓일 때마다 '실력 있음/없음'을 미리 정한 기준으로 판정하는 통계 방법. 너무 일찍 결론 내지 않게 막아 준다."],
  "Fail-Closed": ["멈춤 우선", "자료가 끊기거나 이상하면 '일단 산다'가 아니라 '일단 멈춘다'로 정해 둔 안전 규칙."],
  "킬스위치": ["긴급 정지", "누르면 새 주문을 모두 멈춘다. 손실 한도·데이터 이상 때 자동으로 켜지기도 한다."],
  "HHI·실효 종목 수": ["실효 종목 수", "쏠림을 감안하면 실제로 몇 종목에 나눠 담은 것과 같은지. 10종목을 담아도 한 종목이 80%면 실효 1~2개."],
};
const GL_ALIAS = { "Brier Skill Score": "Brier Skill", "BSS": "Brier Skill", "샤프": "Sharpe", "Sharpe Ratio": "Sharpe", "VaR95": "VaR", "VaR 95%": "VaR", "ES95": "ES", "최대 낙폭": "MDD", "낙폭": "MDD", "Calibration": "확률 보정", "ECE (확률 어긋남)": "ECE", "Drift": "드리프트", "NO_TRADE": "NO TRADE" };
const tG = (key, label) => { const k = GL_ALIAS[key] || key; return GLOSSARY[k] ? `<button type="button" class="t-gl" data-gl="${esc(k)}">${label ?? esc(key)}<i aria-hidden="true">?</i></button>` : (label ?? esc(key)); };
function tGlSheet(k) {
  const g = GLOSSARY[GL_ALIAS[k] || k];
  if (!g) return;
  tSheet(`<h3 class="t-sheet-h">${esc(g[0])}</h3><p class="t-sheet-p">${esc(g[1])}</p><button class="t-btn primary t-sheet-ok">알겠어요</button>`, (b, close) => { b.querySelector(".t-sheet-ok").onclick = close; });
}
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-gl]");
  if (!b) return;
  e.preventDefault(); e.stopPropagation();
  tGlSheet(b.dataset.gl);
});
// 예전 화면(표 머리·이름 칸)에도 같은 풀이 단추를 붙인다 — 글자가 정확히 용어일 때만
const GL_RE = new RegExp(`^(${Object.keys({ ...GLOSSARY, ...GL_ALIAS }).sort((a, b) => b.length - a.length).map((k) => k.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})(\\s*\\(.*\\))?$`);
function glossify(root) {
  if (!root) return;
  root.querySelectorAll("th, .t-k, .t-stat > span, .kvs .muted, .kv .k, dt, .t2-kv .t-k, .t-vs-k").forEach((n) => {
    if (n.dataset.gl || n.querySelector("[data-gl], button, a, input")) return;
    const t = n.textContent.trim();
    const m = GL_RE.exec(t);
    if (!m) return;
    n.dataset.gl = GL_ALIAS[m[1]] || m[1];
    n.classList.add("t-gl-host");
    n.insertAdjacentHTML("beforeend", '<i class="t-gl-q" aria-hidden="true">?</i>');
    n.setAttribute("role", "button");
    n.setAttribute("tabindex", "0");
    n.title = "눌러서 쉬운 설명 보기";
  });
}

// ============================================================ AI
// 확률 보정 막대: 'AI 가 말한 확률' vs '실제로 오른 비율'
function tCalib(rows) {
  rows = (rows || []).filter((c) => c && c.n);
  if (!rows.length) return tEmpty("채점된 판단이 아직 적어요");
  return `<div class="t-cal">${rows.map((c) => {
    const pr = c.predicted ?? c.pred, ac = c.actual;
    const gap = ac != null && pr != null ? ac - pr : null;
    return `<div class="t-cal-r"><span class="t-k">${esc(c.range || `${Math.round((c.lo ?? 0) * 100)}~${Math.round((c.hi ?? 0) * 100)}%`)}<em>${tN(c.n)}번</em></span>
      ${tVs([["AI 가 말한 확률", pr, "acc"], ["실제로 오른 비율", ac, ac >= pr ? "up" : "down"]], { max: 1 })}
      <span class="t-sub">${gap == null ? "" : Math.abs(gap) < 0.05 ? "잘 맞음" : gap > 0 ? `실제가 ${tPr(Math.abs(gap))} 더 많이 올랐어요 (AI 가 겁이 많음)` : `실제가 ${tPr(Math.abs(gap))} 덜 올랐어요 (AI 가 자신 과잉)`}</span></div>`;
  }).join("")}</div>`;
}
TV.scorecard = async (el) => {
  const L = await tLoad(el, "scorecard", "AI 성적표", "t2-score", () => Promise.all([api("/api/ai-public"), tSafe(api("/api/ai-card"))]));
  if (!L) return;
  const [p, c] = L.d;
  const draw = () => {
    const w = (c.windows || {})[T2.aiWin] || c.main || {};
    const worse = p.direction_accuracy != null && p.base_up != null && p.direction_accuracy < p.base_up;
    tPaint(L.root, "AI 성적표", `
      ${tHero({ k: `최근 ${tN(p.n)}번 · ${esc(p.from || "")} ~ ${esc(p.to || "")}`, lv: worse ? "bad" : "good",
        big: `${tPr(p.direction_accuracy)} 맞혔어요`, bigCls: worse ? "down" : "up",
        sub: `그냥 '늘 오른다'고만 했으면 ${tPr(p.base_up)} — ${worse ? "AI 가 이것보다 못했어요" : "AI 가 이것보다 나았어요"}`,
        body: tVs([["AI", p.direction_accuracy, worse ? "down" : "up"], ["늘 오른다", p.base_up, "dim"]], { max: 1 }),
        foot: esc(koText(p.verdict || "")) })}
      ${tKV([[tG("Brier Skill", "확률 실력"), p.brier_skill == null ? "-" : p.brier_skill.toFixed(3), p.brier_skill > 0 ? "up" : "down", "0 보다 커야 쓸모"], [tG("ECE", "확률 어긋남"), tPr(p.ece, 1), "", "작을수록 좋음"],
        [tG("알파", "시장보다 더 번 것"), tPct(p.alpha_cum, 1), tCls(p.alpha_cum), "누적"], [tG("MDD", "최대 낙폭"), tPct(p.mdd, 1), "down", "가장 크게 빠진 때"]])}
      ${p.stability_msg ? `<div class="t-note${/관찰|저하|약/.test(p.stability_msg) ? " warn" : ""}">${esc(koText(p.stability_msg))}</div>` : ""}
      ${tCard("최근 몇 번으로 볼까", `${tChips("sc-win", [["50", "50번"], ["100", "100번"], ["300", "300번"]], T2.aiWin)}
        <div class="t-gap"></div>${tKV([["맞힌 비율", tPr(w.hit), w.hit < w.base_up ? "down" : "up", w.ci95 ? `범위 ${tPr(w.ci95[0])}~${tPr(w.ci95[1])}` : ""], ["늘 오른다", tPr(w.base_up), ""], ["확률 실력", w.brier_skill == null ? "-" : w.brier_skill.toFixed(3), w.brier_skill > 0 ? "up" : "down"], ["AI 가 고른 종목 − 전체", tPct(w.ai_alpha, 2), tCls(w.ai_alpha)]])}
        <div class="t-sub">${esc(koText(w.verdict || ""))}</div>`)}
      ${tCard(`${tG("확률 보정", "AI 확률을 믿어도 될까")}`, tCalib(p.calibration))}
      ${tCard("크게 틀린 판단", tList((p.failures || []).map((f) => tSym(f.symbol, f.name, `${esc(f.at)} · ${esc(tActKo(f.action))} · 오를 확률 ${tPr(f.prob_up)}`, `<b class="num ${tCls(f.realized)}">${tPct(f.realized, 1)}</b><span class="t-sub">5일 뒤 실제</span>`))), '<a href="#ailab">왜 틀렸나</a>')}
      <div class="t-foot">${esc(p.method || "")} · ${esc(c.note || "")}</div>
      ${tFull("scorecard", '<a class="t-btn ghost" href="#aitrust">AI 신뢰 센터</a><a class="t-btn ghost" href="#verify">예측 기록장</a>')}`);
    tBind(L.root, "sc-win", (k) => { T2.aiWin = k; draw(); });
  };
  draw();
};
TV.aitrust = async (el) => {
  const L = await tLoad(el, "aitrust", "AI 신뢰 센터", "t2-trust", () => api("/api/ai-trust"));
  if (!L) return;
  const t = L.d, s = t.state || {}, e = s.easy || {}, ev = t.evaluation || {}, lg = t.ledger || {};
  const lv = { verified: "good", checking: "warn", banned: "bad" }[s.key] || "idle";
  const STAGE = { backtest: "과거 시험", shadow: "그림자 채점", paper: "모의 투자", small_live: "소액 실전", live: "실전" };
  const ctx = t.context || {};
  tPaint(L.root, "AI 신뢰 센터", `
    ${tHero({ k: "지금 AI 를 믿어도 될까", lv, big: `${lvDot(lv)} ${esc(s.label || "-")}`, sub: esc(koText(s.why || "")),
      body: e.headline ? `<div class="t-hero2-line">${esc(koText(e.headline))}</div>${e.n ? tVs([["AI", e.hits / e.n, e.hits >= e.base_hits ? "up" : "down", `${e.hits}번`], ["늘 오른다", e.base_hits / e.n, "dim", `${e.base_hits}번`]], { max: 1 }) : ""}` : "",
      foot: lv === "bad" ? "이 상태에서는 AI 판단이 주문에 쓰이지 않아요 (참고만)" : "" })}
    ${tCard("쉽게 말하면", `<div class="t-list">
      ${e.money ? tStRow("AI 말대로 샀다면", e.money, e.earned ? "good" : "bad", { label: e.earned ? "벌었음" : "못 벌었음" }) : ""}
      ${e.alpha?.text ? tStRow("시장 대비로 채점하면", e.alpha.text, e.alpha.worse ? "bad" : e.alpha.proven ? "good" : "warn", { label: e.alpha.worse ? "기준 미달" : e.alpha.proven ? "증명됨" : "아직 몰라요" }) : ""}
      ${e.weak ? tStRow("약한 곳", e.weak, "warn", { label: "약함" }) : ""}
      ${e.strong ? tStRow("강한 곳", e.strong, "good", { label: "강함" }) : ""}</div>`)}
    ${tCard("왜 이렇게 판정했나", tList((s.reasons || []).map((r) => tStRow(r, "", "bad", { label: "" }))))}
    <div class="t-grid2">
      ${tCard("지금 단계", `<div class="t2-steps">${["backtest", "shadow", "paper", "small_live", "live"].map((k, i, a) => `<span class="${k === t.ladder?.stage ? "on" : a.indexOf(t.ladder?.stage) > i ? "done" : ""}">${STAGE[k]}</span>`).join("")}</div>
        <div class="t-sub">${esc(koText((t.ladder?.reasons || []).join(" · ")))}</div>`, '<a href="#control">자세히</a>')}
      ${tCard("기록이 진짜인가", `${tStRow({ html: `예측 ${tN(lg.sealed)}건 ${tG("봉인", "봉인")}` }, lg.ok ? "적은 뒤 고쳐진 예측 없음" : "고쳐진 흔적이 있어요", lg.ok ? "good" : "bad", { label: lg.ok ? "무결" : "문제" })}
        ${tStRow("독립 채점", koText(ev.verdict || ""), ev.status, { label: ev.status === "worse" ? "기준 미달" : ev.status === "better" ? "기준 통과" : "판정 중" })}`, '<a href="#verify">기록장</a>')}
    </div>
    ${(t.failures?.findings || []).length ? tCard("자주 틀리는 상황", tList(t.failures.findings.map((f) => tStRow(f.text, f.action, "warn", { label: "약점" }))), '<a href="#ailab">연구</a>') : ""}
    ${(ctx.symbol || []).length ? tCard("종목별 성적", tList(ctx.symbol.map((x) => `<div class="t-li${x.enough ? "" : " dim"}"><span class="t-co-t"><b>${esc(x.key)}</b><span>${tN(x.n)}번 채점</span></span><span class="t-li-r">${tProg(x.hit, x.hit >= 0.5 ? "good" : "bad")}<b class="num">${tPr(x.hit)}</b></span></div>`)), "", ) + `<div class="t-foot">${esc(ctx.note || "")}</div>` : ""}
    ${tFull("aitrust", '<a class="t-btn ghost" href="#scorecard">AI 성적표</a><a class="t-btn ghost" href="#aihealth">AI 상태 점검</a>')}`);
};
TV.ailab = async (el) => {
  const L = await tLoad(el, "ailab", "AI 가 틀린 이유", "t2-lab", () => Promise.all([api("/api/failure-lab"), tSafe(api("/api/ai-lab"))]));
  if (!L) return;
  const [f, a] = L.d;
  const top = (f.findings || [])[0];
  const tags = (f.by_tag || []).slice().sort((x, y) => Math.abs((y.hit_with ?? 0) - (y.hit_without ?? 0)) - Math.abs((x.hit_with ?? 0) - (x.hit_without ?? 0)));
  tPaint(L.root, "AI 가 틀린 이유", `
    ${tHero({ k: `최근 ${tN(f.days)}일 · 채점 ${tN(f.n)}번 · 전체 적중 ${tPr(f.hit)}`, lv: top ? "warn" : "idle", big: top ? "가장 큰 약점" : "뚜렷한 약점이 아직 없어요",
      sub: top ? esc(koText(top.text)) : "", body: top?.action ? `<div class="t-concl"><span class="t-k">이렇게 고치는 중</span><b>${esc(koText(top.action))}</b></div>` : "" })}
    ${tCard("상황별로 보면", tags.length ? `<div class="t-list">${tags.map((t) => `<div class="t-li t2-tag"><span class="t-co-t"><b>${esc(t.name)}</b><span>${tN(t.n)}번 (${tPr(t.share)}) 이런 상황이었어요</span></span>
      <span class="t2-tag-v">${tVs([["있을 때", t.hit_with, t.hit_with >= 0.5 ? "up" : "down"], ["없을 때", t.hit_without, t.hit_without >= 0.5 ? "up" : "down"]], { max: 1 })}</span></div>`).join("")}</div>` : tEmpty("상황 표시가 붙은 판단이 없어요"))}
    ${tCard("크게 틀린 판단", tList((f.losses || []).map((x) => tSym(x.symbol, x.name, `${esc(x.at)} · ${x.predicted === "DOWN" ? "내린다" : "오른다"}고 봄 (${tPr(x.prob)}) · ${esc((x.why || []).join(", "))}`, `<b class="num ${tCls(x.actual)}">${tPct(x.actual, 1)}</b>`))))}
    ${a && !a.error ? tCard("AI 층별 상태", tList((a.layers || []).map((x) => tStRow(x.name, x.detail || (x.hit != null ? `적중 ${tPr(x.hit)} · ${tN(x.n_scored)}번` : ""), x.status, { label: x.hit != null ? tPr(x.hit) : undefined }))
      .concat([tStRow("합의", a.ensemble?.detail, a.ensemble?.status), tStRow("위험 점검", a.risk_gate?.detail, "idle", { label: `거부 ${tN(a.risk_gate?.vetoes_30d)}건` }), tStRow("최종 모델", a.final?.detail, a.final?.champion ? "good" : "warn", { label: a.final?.champion ? "있음" : "없음" })]))) : ""}
    <div class="t-foot">${esc(f.note || "")}</div>
    ${tFull("ailab", '<a class="t-btn ghost" href="#scorecard">AI 성적표</a><a class="t-btn ghost" href="#lab">모델 실험</a>')}`);
};
TV.ai = async (el) => {
  const L = await tLoad(el, "ai", "AI 별 성적", "t2-ai", () => Promise.all([api("/api/ai-scoreboard"), tSafe(api("/api/calibration"))]));
  if (!L) return;
  const [s, c] = L.d;
  const cons = c.consensus || {};
  const rows = (s.rows || []).slice().sort((a, b) => (b.accuracy ?? 0) - (a.accuracy ?? 0));
  const AL = { primary: "뉴스 AI", nvidia: "경제·시장 AI", panel: "공시·실적 AI", quant: "차트·Quant AI", regime: "시장 국면", risk: "Risk AI", challenger: "도전자 모델" };
  tPaint(L.root, "AI 별 성적", `
    ${tHero({ k: `AI 들의 합의 · 최근 ${tN(c.window_days)}일 · ${tN(cons.n)}번`, lv: cons.brier_skill > 0 ? "good" : "bad", big: `${tPr(cons.accuracy)} 맞혔어요`,
      sub: `기본 상승 비율 ${tPr(cons.base_rate)} · ${tG("Brier Skill", "확률 실력")} ${cons.brier_skill == null ? "-" : cons.brier_skill.toFixed(3)} · ${tG("ECE", "확률 어긋남")} ${tPr(cons.ece, 1)}` })}
    ${tCard("AI 마다", tList(rows.map((r) => `<div class="t-li"><span class="t-co-t"><b>${esc(r.provider)}</b><span>${esc(r.label || "")} · ${tN(r.n)}번 · ${tG("Brier", "오차")} ${r.brier == null ? "-" : r.brier.toFixed(3)}</span></span>
      <span class="t-li-r t2-w">${tProg(r.accuracy, r.accuracy >= 0.5 ? "good" : "bad")}<b class="num">${tPr(r.accuracy, 1)}</b></span></div>`)))}
    ${Object.keys(c.analysts || {}).length ? tCard(`${tG("확률 보정", "확률 보정")} 전후`, tList(Object.entries(c.analysts).map(([k, v]) => `<div class="t-li"><span class="t-co-t"><b>${esc(AL[k] || k)}</b><span>${tN(v.raw?.n)}번 · 맞힌 비율 ${tPr(v.raw?.accuracy)}</span></span>
      <span class="t-li-r"><span class="t-sub">확률 어긋남</span><b class="num">${tPr(v.calibrator?.before?.ece ?? v.raw?.ece, 1)} → ${tPr(v.calibrator?.after?.ece ?? v.used?.ece, 1)}</b></span></div>`))) : ""}
    ${tCard("합의 확률 vs 실제", tCalib((cons.curve || []).map((x) => ({ ...x, predicted: x.pred }))))}
    ${tCard("AI 역할", tList((s.roles || []).map((r) => `<div class="t-li"><span class="t-co-t"><b>${esc(r.label)}</b><span>${esc(r.desc || "")}</span></span><span class="t-li-r"><span class="t-sub">${esc(r.provider || "")}</span>${r.free ? '<span class="t-tag">무료</span>' : ""}</span></div>`)))}
    ${tFull("ai", '<a class="t-btn ghost" href="#scorecard">AI 성적표</a>')}`);
};
TV.aihealth = async (el) => {
  const L = await tLoad(el, "aihealth", "AI 상태 점검", "t2-aih", () => api("/api/health/ai"));
  if (!L) return;
  const h = L.d, m = h.model || {};
  const lv = tLv(h.status);
  tPaint(L.root, "AI 상태 점검", `
    ${tHero({ k: `점검 ${esc(h.as_of || "")}`, lv, big: lv === "good" ? "AI 상태 좋아요" : lv === "warn" ? "지켜봐야 할 게 있어요" : `고칠 것 ${(h.problems || []).length}개`,
      body: (h.problems || []).length ? `<ol class="t-ol">${h.problems.slice(0, 5).map((p, i) => `<li><span class="t-num">${i + 1}</span>${esc(koText(p))}</li>`).join("")}</ol>` : "" })}
    ${tCard("부분별", tList((h.tiles || []).map((t) => tStRow(t.title, t.detail, t.status))))}
    ${tCard("AI 마다 최근 성적", tList((h.analysts || []).map((a) => `<div class="t-li"><span class="t-co"><span class="t-dot-ic">${lvDot(tLv(a.decay))}</span><span class="t-co-t"><b>${esc(a.label)}</b><span class="t2-wrap">${esc(koText(a.decay_msg || ""))}</span></span></span>
      <span class="t-li-r"><span class="t-sub">처음 ${tPr(a.hit_ref)} → 최근</span><b class="num ${a.hit_recent < a.hit_ref ? "down" : "up"}">${tPr(a.hit_recent)}</b></span></div>`)))}
    <div class="t-grid2">
      ${tCard("예측력 검정", `${tStRow(m.power?.label || "-", m.power?.bottom_line, m.power?.decision, { label: m.power?.more_needed ? `${tN(m.power.more_needed)}건 더` : undefined })}`, '<a href="#power">자세히</a>')}
      ${tCard(`시장 변화 (${tG("드리프트", "드리프트")})`, tStRow(m.drift?.status === "drift" ? "시장이 크게 달라졌어요" : m.drift?.status === "stable" ? "안정" : "변화 있음", m.drift?.message, m.drift?.status), '<a href="#verify">자세히</a>')}
    </div>
    ${(m.checks || []).length ? tCard("모델 점검", tList(m.checks.map((c) => tStRow(c.title, c.detail, c.status)))) : ""}
    ${!h.has_llm ? `<a class="t-note" href="#datahealth">LLM 키가 없어 뉴스·공시 AI 는 규칙 기반으로 돌아요 — 로컬 LLM(Ollama)이나 키를 넣으면 바뀌어요</a>` : ""}
    ${tFull("aihealth", '<a class="t-btn ghost" href="#ai">AI 별 성적</a>')}`);
};
TV.power = async (el) => {
  const L = await tLoad(el, "power", "AI 예측력 검정", "t2-power", () => api("/api/power"));
  if (!L) return;
  const p = L.d, f = p.forward || {}, pr = p.prereg || {}, need = pr.required_n_fixed || (f.n || 0) + (f.more_needed || 0);
  const dec = { continue: ["warn", "판정 중"], accept_h1: ["good", "예측력 있음"], accept_h0: ["bad", "예측력 없음"], h1: ["good", "예측력 있음"], h0: ["bad", "예측력 없음"] }[f.decision] || ["idle", f.decision || "-"];
  tPaint(L.root, "AI 예측력 검정", `
    ${tHero({ k: "진짜 시장에서 맞히는가 (미리 정한 규칙으로 검정)", lv: dec[0], big: esc(dec[1]), sub: esc(koText(p.bottom_line || f.label || "")),
      body: `<div class="t-gap"></div>${tProg(need ? (f.n || 0) / need : 0, dec[0], "모은 결과")}<div class="t-sub">${tN(f.n)} / ${tN(need)}건 모음${f.eta ? ` · 예상 ${esc(f.eta)}` : ""}${f.hit_rate != null ? ` · 지금까지 ${tPr(f.hit_rate)} 맞힘` : ""}</div>` })}
    ${tCard("미리 정해 둔 규칙", `<div class="t-list">
      ${tStRow("기준 (실력 없음)", pr.h0, "idle", { label: tPr(pr.p0, 1) })}
      ${tStRow("목표 (실력 있음)", pr.h1, "idle", { label: tPr(pr.p1, 1) })}
      ${tStRow("재는 것", pr.metric, "idle", { label: "" })}
      ${tStRow({ html: tG("SPRT", "검정 방법") }, pr.rule, "idle", { label: "" })}</div>
      <div class="t-foot">등록 ${esc(tDate(pr.registered_at))} · 지문 <span class="mono">${esc((pr.hash || "").slice(0, 16))}</span> — 결과를 본 뒤 기준을 바꿀 수 없게 고정</div>`)}
    ${tCard("검증 단계", tList((p.layers || []).map((x) => tStRow(x.title, x.detail, x.status))))}
    ${(p.power_table || []).length ? tCard("실력이 이만큼이면 몇 건이 필요할까", tList(p.power_table.map((r) => `<div class="t-li"><span class="t-co-t"><b>기준보다 ${r.edge_pp}%p 더 맞히면</b></span><span class="t-li-r"><b class="num">${tN(r.n)}건</b></span></div>`))) : ""}
    ${p.decay?.consensus ? tCard("최근 성적 흐름", `${tStRow(p.decay.consensus.label || "AI 합의", p.decay.consensus.message, p.decay.consensus.status)}${(p.decay.consensus.rolling || []).length > 2 ? `<div class="t-gap"></div>${tSpark(p.decay.consensus.rolling, { h: 70, w: 600, color: "var(--accent)" })}<div class="t-foot">채점된 날마다 적중률 (이동 평균)</div>` : ""}`) : ""}
    ${tFull("power", '<a class="t-btn ghost" href="#verify">예측 기록장</a>')}`);
};
TV.pead = async (el) => {
  const L = await tLoad(el, "pead", "실적 발표 전략", "t2-pead", () => api("/api/pead"));
  if (!L) return;
  const p = L.d, f = p.forward || {};
  tPaint(L.root, `실적 발표 전략 <span class="t-h1s">${tG("PEAD", "PEAD")}</span>`, `
    ${tHero({ k: "실전 기록 (주문에는 쓰지 않아요)", lv: f.n >= 30 && f.months >= 6 ? "good" : "warn", big: esc(koText(p.decision || "-")),
      body: `<div class="t-gap"></div><div class="t-grid2 tight"><div><span class="t-k">판단 건수</span>${tProg((f.n || 0) / 30, "", "건수")}<span class="t-sub">${tN(f.n)} / 30건</span></div><div><span class="t-k">기간</span>${tProg((f.months || 0) / 6, "", "개월")}<span class="t-sub">${tN(f.months)} / 6개월</span></div></div>` })}
    ${tCard("규칙", `<div class="t-text"><p>${esc(p.rule || "")}</p></div>${tKV([["비용 가정", tPr(p.cost, 2)], ["찾은 실적 발표", `${tN(p.n_events)}건`], ["신호", `${tN(p.n_signals)}건`], ["고쳐진 기록", `${tN(p.tampered)}건`, p.tampered ? "down" : ""]])}`)}
    ${tCard("최근 신호", tList((p.recent || []).map((r) => tSym(r.symbol, r.name || r.symbol, `${esc(r.date || r.at || "")} · 서프라이즈 ${tPct(r.surprise, 1)}`, `<b class="num ${tCls(r.ret ?? r.realized)}">${tPct(r.ret ?? r.realized, 1)}</b>`)), "아직 신호가 없어요", "실적 발표에서 예상보다 5% 이상 차이 나면 생겨요"))}
    ${(p.pending || []).length ? tCard("결과 기다리는 중", tList(p.pending.map((r) => tSym(r.symbol, r.name || r.symbol, esc(r.date || ""), `<span class="t-sub">${esc(r.until || "")}</span>`)))) : ""}
    ${tStRow("외부 공증", p.external?.text, p.external?.status)}
    <div class="t-foot">${esc(p.note || "")} · ${esc(p.as_of || "")}</div>
    ${tFull("pead", '<a class="t-btn ghost" href="#calendar">실적 일정</a>')}`);
};
TV.verify = async (el) => {
  const L = await tLoad(el, "verify", "예측 기록장", "t2-verify", () => api("/api/verify"));
  if (!L) return;
  const v = L.d, e = v.evaluation || {}, lg = v.ledger || {}, dr = v.drift || {}, lk = e.leakage || {};
  const bl = e.baselines || {};
  const BL = { coin: "동전 던지기", always_up: "늘 오른다", momentum: "최근 오른 쪽" };
  tPaint(L.root, "예측 기록장", `
    ${tHero({ k: `${tG("봉인", "봉인")}된 예측 ${tN(lg.sealed)}건`, lv: lg.ok ? "good" : "bad", big: lg.ok ? "기록이 고쳐지지 않았어요" : "고쳐진 기록이 있어요",
      sub: esc(koText(lg.message || "")), foot: lg.last_anchor_at ? `마지막 지문 ${esc(tAgo(lg.last_anchor_at))} · #${tN(lg.last_upto_id)}까지` : "" })}
    ${tCard("독립 채점 (다시 계산해서 맞춰 봄)", `${tVs([["AI", e.hit_rate, e.hit_rate >= e.best_baseline ? "up" : "down"], ...Object.entries(bl).map(([k, x]) => [BL[k] || k, x, "dim"])], { max: 1 })}
      <div class="t-concl ${e.status === "worse" ? "t-concl-bad" : ""}"><b>${esc(koText(e.verdict || ""))}</b><span class="t-sub">${tN(e.n)}건 · 다시 계산 ${tN(e.recomputed)}건 · 불일치 ${tN((e.mismatches || []).length)}건</span></div>`)}
    ${(e.errors?.miss_reasons || []).length ? tCard("틀린 이유", tList(e.errors.miss_reasons.map((r) => `<div class="t-li"><span class="t-co-t"><b>${esc(koText(r.reason || r.name || r[0] || ""))}</b></span><span class="t-li-r"><b class="num">${tN(r.n ?? r[1])}건</b></span></div>`))) : ""}
    <div class="t-grid2">
      ${tCard("미래 정보 새어 들어옴", tStRow(lk.message || "-", `${tN(lk.checked)}건 검사`, lk.ok ? "good" : "bad", { label: lk.ok ? "없음" : "발견" }))}
      ${tCard(tG("드리프트", "시장 변화"), `${tStRow(dr.worst?.label || "-", dr.message, dr.status, { right: dr.worst ? `<span class="t-st st-${tLv(dr.status)}">PSI ${dr.worst.psi?.toFixed(2)}</span>` : undefined })}`)}
    </div>
    ${(dr.features || []).length ? tCard("어떤 지표가 달라졌나", tList(dr.features.map((x) => `<div class="t-li"><span class="t-co-t"><b>${esc(x.label)}</b><span>예전 평균 ${tN(x.ref_mean, 4)} → 지금 ${tN(x.cur_mean, 4)}</span></span><span class="t-li-r">${tSt(x.status, x.status === "stable" ? "안정" : x.status === "drift" ? "크게 바뀜" : "조금 바뀜")}</span></div>`))) : ""}
    ${tFull("verify", '<a class="t-btn ghost" href="#ledger">예측 장부</a><a class="t-btn ghost" href="#power">예측력 검정</a>')}`);
};
TV.alpha = async (el) => {
  const L = await tLoad(el, "alpha", "AI 가 돈을 벌었나", "t2-alpha", () => Promise.all([api("/api/net-alpha"), tSafe(api("/api/an/counterfactual")), tSafe(api("/api/an/execution"))]));
  if (!L) return;
  const [n, cf, ex] = L.d, hd = n.headline || {}, vd = n.verdict || {};
  const lv = tLv(vd.status);
  tPaint(L.root, "AI 가 돈을 벌었나", `
    ${tHero({ k: `${esc(n.bench_name || "지수")} 대비 · ${esc(n.mode || "")}`, lv, big: hd.excess != null ? `시장보다 ${tPct(hd.excess, 1)}` : esc(koText(vd.title || "아직 몰라요")), bigCls: tCls(hd.excess),
      sub: esc(koText(vd.message || "")), foot: `${tN(n.passed)} / ${(n.steps || []).length} 단계 통과` })}
    ${tCard("단계별 확인", tList((n.steps || []).map((s) => tStRow(s.title, `${s.headline || ""}${s.detail ? ` — ${s.detail}` : ""}`, s.status))))}
    ${cf?.veto ? tCard("위험 점검이 막은 매수", `${tKV([["막은 횟수", `${tN(cf.veto.n)}번`], ["샀다면 평균", tPct(cf.veto.avg_return_if_bought, 2), tCls(cf.veto.avg_return_if_bought)], ["손실을 피한 비율", tPr(cf.veto.avoided_loss_rate)]])}<div class="t-sub">${esc(koText(cf.veto.verdict || ""))}</div>`) : ""}
    ${ex && !ex.error ? tCard(`체결 비용 (${tG("슬리피지", "슬리피지")})`, `${tKV([["주문", `${tN(ex.orders)}건`], ["체결률", tPr(ex.fill_rate)], ["평균 차이", `${tN(ex.slippage_bps?.mean, 1)}bp`, "", `가정 ${tN(ex.assumed_slippage_bps, 1)}bp`], ["수수료", tWon(ex.fees_krw)]])}
      ${tList((ex.worst || []).slice(0, 5).map((w) => tSym(w.symbol, w.name, `${w.side === "buy" ? "매수" : "매도"} ${tN(w.qty)}주 · ${esc(tDate(w.ts))}`, `<b class="num">${tN(w.slippage_bps, 1)}bp</b>`)))}`, '<a href="#execution">자세히</a>') : ""}
    ${tFull("alpha", '<a class="t-btn ghost" href="#scorecard">AI 성적표</a>')}`);
};

// ============================================================ 기록 · 연구
TV.notrade = async (el) => {
  const days = T2.ntDays || 30;
  const L = await tLoad(el, "notrade", "거래 안 한 이유", "t2-nt", () => api(`/api/notrade?${typeof pfModeQ === "function" ? pfModeQ() : "mode=paper"}&days=${days}`));
  if (!L) return;
  const r = L.d, lab = Object.fromEntries((r.by_category || []).map((c) => [c.key, c.label]));
  const mx = Math.max(1, ...(r.by_category || []).map((c) => c.n));
  tPaint(L.root, "거래 안 한 이유", `
    ${tChips("nt-days", [["7", "1주"], ["30", "1개월"], ["90", "3개월"]], String(days))}<div class="t-gap"></div>
    ${tHero({ k: `최근 ${r.days}일 · ${esc(r.mode)} 장부`, lv: r.n ? "warn" : "good", big: r.n ? `${tN(r.n)}번 사지 않았어요` : "막힌 매수가 없어요", sub: esc(koText(r.headline || "")) })}
    ${(r.by_category || []).length ? tCard("이유별", tVs(r.by_category.map((c) => [esc(c.label), c.n, "acc", `${c.n}번`]), { max: mx })) : ""}
    ${tCard("하나씩 보기", tList((r.rows || []).map((x) => {
      const left = x.symbol ? tName(x.symbol, x.name, `${esc(tAgo(x.ts))} · ${esc(lab[x.category] || x.category || "")}`, 36) : `<span class="t-co-t"><b>${esc(x.name || "-")}</b><span>${esc(tAgo(x.ts))}</span></span>`;
      const body = `${left}<span class="t-li-r"><span class="t-st ${x.blocked ? "st-bad" : "st-warn"}">${esc(x.outcome || (x.blocked ? "막음" : "줄임"))}</span></span>`;
      const why = `<div class="t2-why">${(x.reasons || []).map((w) => esc(koText(w))).join(" · ")}${x.plan ? `<br><em>계획: ${esc(x.plan)}</em>` : ""}</div>`;
      return `<div class="t2-item">${x.symbol ? `<a class="t-li" href="#analysis/${encodeURIComponent(x.symbol)}">${body}</a>` : `<div class="t-li">${body}</div>`}${why}</div>`;
    }), "막히거나 줄어든 매수가 없어요", "위험 한도·일정·데이터 문제로 사지 않으면 여기에 남아요"))}
    <div class="t-foot">분류: 위험 한도 · 일정 · 데이터 부족 · 포트폴리오 한도 · 거래량 부족 · 긴급 정지 · 매매 준비 · AI 판단${r.ai_on ? "" : " · AI 보조 판단이 꺼져 있어 'AI 매수 미실행'은 세지 않아요"}</div>
    ${tFull("notrade", '<a class="t-btn ghost" href="#risk">위험 관리</a><a class="t-btn ghost" href="#budget">투자 한도</a>')}`);
  tBind(L.root, "nt-days", (k) => { T2.ntDays = +k; TV.notrade(el); });
};
TV.journal = async (el) => {
  const L = await tLoad(el, "journal", "AI 판단 일지", "t2-jr", () => api("/api/journal"));
  if (!L) return;
  const rows = L.d.rows || [];
  const scored = rows.filter((x) => x.correct != null), hits = scored.filter((x) => x.correct).length;
  const draw = () => {
    const xs = rows.filter((x) => T2.jrF === "all" || (T2.jrF === "hit" ? x.correct === true : T2.jrF === "miss" ? x.correct === false : x.correct == null));
    tPaint(L.root, "AI 판단 일지", `
      ${tHero({ k: `최근 ${rows.length}건`, lv: scored.length ? (hits / scored.length >= 0.5 ? "good" : "bad") : "idle", big: scored.length ? `${scored.length}건 중 ${hits}건 맞음` : "아직 채점 전이에요", sub: scored.length ? `맞힌 비율 ${tPr(hits / scored.length)} · 결과 기다리는 판단 ${rows.length - scored.length}건` : "판단 뒤 5거래일이 지나면 자동으로 채점돼요" })}
      ${tChips("jr-f", [["all", "전체"], ["hit", "맞음"], ["miss", "틀림"], ["wait", "채점 전"]], T2.jrF)}<div class="t-gap"></div>
      ${tCard("", tList(xs.map((x) => `<a class="t-li t2-jr" href="#evidence/${esc(x.id)}">${tName(x.symbol, x.name, `${esc(tDate(x.as_of))} · ${esc(koText(x.expected || ""))}${(x.no_trade || []).length ? ` · ${esc(koText(x.no_trade[0]))}` : ""}`, 36)}
        <span class="t-li-r">${tPill(x.action, x.prob_up, { short: true })}${x.actual != null ? `<span class="num t-sub ${tCls(x.actual)}">${tPct(x.actual, 1)}</span>` : ""}${tOk(x.correct)}</span><span class="t-chev">${TI.chev}</span></a>`), "조건에 맞는 판단이 없어요"))}
      <div class="t-foot">눌러서 그때 AI 가 본 재료(뉴스·지표·의견)부터 결과까지 볼 수 있어요</div>
      ${tFull("journal", '<a class="t-btn ghost" href="#ledger">예측 장부</a><a class="t-btn ghost" href="#myjournal">내 투자일지</a>')}`);
    tBind(L.root, "jr-f", (k) => { T2.jrF = k; draw(); });
  };
  draw();
};
TV.ledger = async (el) => {
  const q = new URLSearchParams();
  if (T2.ledBefore) q.set("before", T2.ledBefore);
  if (T2.ledF !== "all") q.set("result", T2.ledF);
  if (T2.ledSym) q.set("symbol", T2.ledSym);
  const L = await tLoad(el, "ledger", "예측 장부", "t2-led", () => api(`/api/ledger?${q}`));
  if (!L) return;
  const rows = L.d.rows || [];
  const RES = { SUCCESS: ["맞음", "up"], FAIL: ["틀림", "down"], PENDING: ["대기", ""] };
  tPaint(L.root, "예측 장부", `
    <div class="t-note">AI 판단은 적는 순간 ${tG("봉인", "봉인")}돼요. 결과는 나중에 붙고, 판단 자체는 한 글자도 바꿀 수 없어요.</div>
    <div class="t2-filter"><input id="led-sym" class="t-in" placeholder="종목 코드 (예: 005930)" value="${esc(T2.ledSym || "")}" inputmode="text" aria-label="종목 코드">
      ${tChips("led-f", [["all", "전체"], ["success", "맞음"], ["fail", "틀림"]], T2.ledF)}</div>
    ${tCard("", tList(rows.map((x) => { const r = RES[x.result] || ["-", ""]; return `<a class="t-li" href="#evidence/${esc(x.id)}">${tName(x.symbol, x.name, `#${x.id} · ${esc(tDate(x.as_of))} · ${x.prediction === "UP" ? "오른다" : "내린다"} ${tPr(x.prob_up)} · 예상 ${tPct(x.expected, 1)}`, 36)}
      <span class="t-li-r">${x.realized == null ? '<span class="t-sub">결과 대기</span>' : `<b class="num ${tCls(x.realized)}">${tPct(x.realized, 1)}</b>`}${r[1] ? `<span class="t-tag ${r[1]}">${r[0]}</span>` : `<span class="t-tag">${r[0]}</span>`}</span></a>`; }), "조건에 맞는 예측이 없어요"))}
    <div class="t-pro">${T2.ledBefore ? '<button class="t-btn ghost" id="led-new">처음으로</button>' : ""}${rows.length >= 60 ? '<button class="t-btn ghost" id="led-more">이전 60개</button>' : ""}</div>
    ${tFull("ledger", '<a class="t-btn ghost" href="#verify">기록장 검증</a>')}`);
  tBind(L.root, "led-f", (k) => { T2.ledF = k; T2.ledBefore = null; TV.ledger(el); });
  const inp = L.root.querySelector("#led-sym");
  inp.onkeydown = (e) => { if (e.key === "Enter") { T2.ledSym = inp.value.trim().slice(0, 12); T2.ledBefore = null; TV.ledger(el); } };
  const mo = L.root.querySelector("#led-more"); if (mo) mo.onclick = () => { T2.ledBefore = rows[rows.length - 1]?.id; TV.ledger(el); };
  const nw = L.root.querySelector("#led-new"); if (nw) nw.onclick = () => { T2.ledBefore = null; TV.ledger(el); };
};
TV.evidence = async (el) => {
  const id = S.param;
  if (!id) { location.hash = "#journal"; return; }
  const L = await tLoad(el, "evidence", "판단 근거", "t2-ev", () => api(`/api/evidence?id=${encodeURIComponent(id)}`));
  if (!L) return;
  const e = L.d;
  if (e.error) { tPaint(L.root, "판단 근거", tEmpty("이 판단을 찾을 수 없어요", "예측 장부에서 다시 골라 주세요", '<a class="t-btn" href="#ledger">예측 장부</a>')); return; }
  const PL = { last_close: "종가", last_bar: "기준 봉", ret_1: "1일 수익률", ret_5: "5일 수익률", ret_20: "20일 수익률", vol_20: "20일 변동성", rsi_14: "RSI(14)", dist_52w: "52주 고점 거리", ma_20_gap: "20일선 대비", ma_60_gap: "60일선 대비", vol_ratio: "변동성 비율", jump_sigma: "당일 급변(σ)" };
  const body = (st) => {
    const it = st.items;
    if (st.stage === "news" || st.stage === "disclosure") return (it || []).length ? `<div class="t-list">${it.map((n) => `<div class="t-li"><span class="t-co-t"><b class="t2-wrap">${esc(n.title)}</b><span>${n.n_articles > 1 ? `기사 ${n.n_articles}건을 하나로 묶음` : ""}${n.sentiment != null ? ` · 분위기 ${n.sentiment > 0.1 ? "좋음" : n.sentiment < -0.1 ? "나쁨" : "보통"}` : ""}</span></span></div>`).join("")}</div>` : '<span class="t-sub">없음</span>';
    if (st.stage === "price") { const ks = Object.entries(it || {}); return ks.length ? tKV(ks.slice(0, 8).map(([k, v]) => [esc(PL[k] || k), typeof v !== "number" ? esc(String(v)).slice(0, 10) : /ret|gap|dist|vol_20/.test(k) ? tPct(v, 1) : k === "last_close" ? num(v) : v.toFixed(2)])) : '<span class="t-sub">이 판단은 근거 저장 전 기록이라 그때 지표가 없어요</span>'; }
    if (st.stage === "market") { const mk = it?.market || {}; return tKV([["시장 분위기", `${esc(mk.label || "-")}`], ["국면", esc(({ bull: "상승장", bear: "하락장", sideways: "횡보" })[it?.regime?.regime] || it?.regime?.regime || "-")], ...((it?.cross_asset || []).slice(0, 2).map((c) => [`${esc(c.name)} 함께 움직임`, c.corr.toFixed(2)]))]); }
    if (st.stage === "ai") return `<div class="t-list">${(it || []).map((a) => { const sc = Object.values(a.scores || {})[0] || {}; return `<div class="t-li"><span class="t-co-t"><b>${esc(a.label)}</b><span class="t2-wrap">${esc(koText(a.summary || (a.reasons || [])[0] || ""))}</span></span><span class="t-li-r">${sc.prob_up != null ? `<b class="num">${tPr(sc.prob_up)}</b>` : ""}${sc.correct == null ? "" : tOk(sc.correct)}${a.veto ? '<span class="t-st st-bad">거부</span>' : ""}</span></div>`; }).join("")}</div>`;
    if (st.stage === "ensemble") return `<div class="t2-ens">${tPill(it?.action, it?.prob_up)}<span class="t-sub">확신 ${Math.round(it?.confidence || 0)} · 의견 충돌 ${esc({ low: "적음", medium: "보통", high: "큼" }[it?.conflict] || it?.conflict || "-")}</span></div>${(it?.reasons || []).slice(0, 4).map((r) => `<div class="t-sub">· ${esc(koText(r))}</div>`).join("")}`;
    if (st.stage === "risk") { const xs = [...(it?.vetoes || []).map((v) => tStRow(v, "", "bad", { label: "막음" })), ...(it?.no_trade || []).map((v) => tStRow(v, "", "warn", { label: "보류" }))]; return xs.length ? `<div class="t-list">${xs.join("")}</div>` : '<span class="t-sub">통과 — 막은 이유 없음</span>'; }
    if (st.stage === "order") return (it || []).length ? `<div class="t-list">${it.map((o) => `<div class="t-li"><span class="t-co-t"><b class="${o.side === "buy" ? "up" : "down"}">${o.side === "buy" ? "매수" : "매도"} ${num(o.qty)}주</b><span>${esc(o.mode)} · ${esc(o.status)}</span></span><span class="t-li-r"><b class="num">${num(o.avg_price || o.ref_price)}</b>${o.slippage_bps != null ? `<span class="t-sub">차이 ${o.slippage_bps.toFixed(1)}bp</span>` : ""}</span></div>`).join("")}</div>` : '<span class="t-sub">이 판단으로 나간 주문 없음</span>';
    if (st.stage === "outcome") return it?.correct == null ? `<span class="t-sub">${it?.horizon || 5}거래일 지나면 자동 채점</span>` : `<div class="t2-ens"><b class="num t-big ${tCls(it.realized_return)}">${tPct(it.realized_return, 1)}</b>${tOk(it.correct)}</div>${it.cause ? `<div class="t-sub">원인: ${esc(koText(it.cause))}</div>` : ""}`;
    return "";
  };
  tPaint(L.root, "판단 근거", `
    <section class="t-hero2"><div class="t2-evh">${tName(e.symbol, e.name, `${esc(e.symbol)} · ${esc(tDate(e.as_of))} 판단`, 48)}${tPill(e.action, e.prob_up)}</div>
      <div class="t-sub">그때 AI 가 본 재료부터 실제 결과까지 순서대로예요. 판단과 함께 저장해 두어 나중에 봐도 똑같이 보여요.</div></section>
    <ol class="t-tl">${(e.chain || []).map((st, i) => `<li><span class="t-tl-n">${i + 1}</span><div class="t-tl-b"><h3>${esc(koText(st.title))}</h3>${body(st)}</div></li>`).join("")}</ol>
    ${tFull(`evidence/${encodeURIComponent(id)}`, `<a class="t-btn ghost" href="#analysis/${encodeURIComponent(e.symbol)}">종목 화면</a><a class="t-btn ghost" href="#journal">판단 일지</a>`)}`);
};
TV.review = async (el) => {
  const L = await tLoad(el, "review", "복기 리포트", "t2-rv", () => api("/api/reviews"));
  if (!L) return;
  const rs = Array.isArray(L.d) ? L.d : L.d.reviews || [];
  const r = rs[0];
  if (!r) { tPaint(L.root, "복기 리포트", `${tEmpty("아직 복기가 없어요", "매일 장 마감 뒤 지난 판단을 돌아보는 복기가 자동으로 만들어져요")}${tFull("review")}`); return; }
  const s = r.summary || {};
  const REG = { bull: "상승장", bear: "하락장", sideways: "횡보", high_vol: "변동 큼" };
  tPaint(L.root, "복기 리포트", `
    ${tHero({ k: `${esc(r.date)} · 최근 ${tN(s.window_days)}일 · 결과 나온 판단 ${tN(s.n_resolved)}건`, lv: s.consensus_accuracy >= 0.5 ? "good" : "bad", big: `${tPr(s.consensus_accuracy)} 맞혔어요`, sub: s.traded_accuracy != null ? `실제로 사고판 것만 보면 ${tPr(s.traded_accuracy)}` : "" })}
    ${tCard("배운 점", (r.lessons || []).length ? `<ol class="t-ol big">${r.lessons.map((t, i) => `<li><span class="t-num">${i + 1}</span>${esc(koText(t))}</li>`).join("")}</ol>` : tEmpty("특별한 교훈이 없어요"))}
    <div class="t-grid2">
      ${tCard("시장 상황별", tVs((s.by_regime || []).map((x) => [esc(REG[x.regime] || x.regime || x.key || "-"), x.accuracy ?? x.hit, (x.accuracy ?? x.hit) >= 0.5 ? "up" : "down", `${tPr(x.accuracy ?? x.hit)} · ${tN(x.n)}건`]), { max: 1 }))}
      ${tCard("확신도별", tVs((s.by_confidence || []).map((x) => [esc(x.bucket || x.key || x.label || "-"), x.accuracy ?? x.hit, (x.accuracy ?? x.hit) >= 0.5 ? "up" : "down", `${tPr(x.accuracy ?? x.hit)} · ${tN(x.n)}건`]), { max: 1 }))}
    </div>
    ${tCard("가장 크게 틀린 판단", tList((s.worst_misses || []).map((x) => tSym(x.symbol, x.name || x.symbol, `${esc(tDate(x.as_of || x.date))} · ${esc(tActKo(x.action))}${x.prob_up != null ? ` ${tPr(x.prob_up)}` : ""}${x.cause ? ` · ${esc(koText(x.cause))}` : ""}`, `<b class="num ${tCls(x.realized ?? x.actual)}">${tPct(x.realized ?? x.actual, 1)}</b>`))))}
    ${rs.length > 1 ? tCard("지난 복기", tList(rs.slice(1, 10).map((x) => `<div class="t-li"><span class="t-co-t"><b>${esc(x.date)}</b><span>${tN(x.summary?.n_resolved)}건</span></span><span class="t-li-r"><b class="num">${tPr(x.summary?.consensus_accuracy)}</b></span></div>`))) : ""}
    ${tFull("review", '<a class="t-btn ghost" href="#reports">리포트 · 브리핑</a>')}`);
};
TV.reports = async (el) => {
  const L = await tLoad(el, "reports", "리포트 · 브리핑", "t2-rp", () => api("/api/reports"));
  if (!L) return;
  const list = L.d.reports || [];
  const cur = S.param ? decodeURIComponent(S.param) : null;
  const KIND = (k) => k === "morning" ? "아침 브리핑" : "일일 리포트";
  let detail = "";
  if (cur) {
    let r = null;
    try { r = await api(`/api/report?file=${encodeURIComponent(cur)}`); } catch { r = null; }
    if (!L.root.isConnected) return;
    detail = r ? tCard(`${KIND(r.kind)} <span class="t-h1s">${esc(r.date)}</span>`, `<div class="t-text">${(r.text || "").split("\n").slice(1).filter((l) => l.trim()).map((l) => `<p>${esc(koText(l))}</p>`).join("")}</div>
      ${(r.predictions || []).length ? `<div class="t-list">${r.predictions.map((p) => tSym(p.symbol, p.name, `예상 ${tPct(p.expected, 1)}`, tPill(p.action, p.prob_up))).join("")}</div>` : ""}
      ${(r.recent || []).length ? `<div class="t-list">${r.recent.map((x) => tSym(x.symbol, x.name, `${esc(tActKo(x.action))} · 예상 ${tPct(x.expected, 1)}${x.hit ? "" : ` · ${esc(koText(x.miss_reason || ""))}`}`, `<b class="num ${tCls(x.actual)}">${tPct(x.actual, 1)}</b>${tOk(x.hit)}`)).join("")}</div>` : ""}
      ${r.ledger ? `<div class="t-foot">예측 장부 #${tN(r.ledger.upto_id)}까지 봉인 · <span class="mono">${esc((r.ledger.digest || "").slice(0, 16))}</span></div>` : ""}`) : tErr("리포트를 열지 못했어요");
  }
  tPaint(L.root, "리포트 · 브리핑", `
    <div class="t-pro" style="margin-top:0"><button class="t-btn primary" id="rp-m">아침 브리핑 지금 만들기</button><button class="t-btn ghost" id="rp-d">일일 리포트 지금 만들기</button></div><div class="t-gap"></div>
    ${detail}
    ${tCard("지난 리포트", tList(list.map((x) => `<a class="t-li${x.file === cur ? " on" : ""}" href="#reports/${encodeURIComponent(x.file)}"><span class="t-co"><span class="t-ev-ic">${x.kind === "morning" ? TI.cal : TI.doc}</span><span class="t-co-t"><b>${KIND(x.kind)}</b><span>${esc(x.date)}</span></span></span><span class="t-chev">${TI.chev}</span></a>`), "리포트가 아직 없어요", "평일 08:30 아침 브리핑 · 16:10 일일 리포트가 자동으로 만들어져요"))}
    ${tFull("reports")}`);
  const mk = (id, name) => { const b = L.root.querySelector(id); b.onclick = async () => { b.disabled = true; b.textContent = "만드는 중…"; const x = await runAction(name); toast({ title: x.error ? "만들지 못했어요" : "리포트를 만들었어요", body: x.error || "", level: x.error ? "warn" : "good" }); location.hash = x.result?.file ? `#reports/${encodeURIComponent(x.result.file)}` : "#reports"; if (!x.result?.file) TV.reports(el); }; };
  mk("#rp-m", "morning_brief"); mk("#rp-d", "daily_report");
};
TV.lab = async (el) => {
  const L = await tLoad(el, "lab", "모델 실험 · 승격", "t2-lab2", () => Promise.all([api("/api/an/experiments"), tSafe(api("/api/an/ai_verdict")), tSafe(api("/api/an/events"))]));
  if (!L) return;
  const [x, v, ev] = L.d;
  const ST = { champion: ["good", "사용 중"], shadow: ["warn", "그림자 시험"], candidate: ["warn", "후보"], rejected: ["bad", "탈락"], retired: ["idle", "은퇴"] };
  tPaint(L.root, "모델 실험 · 승격", `
    ${tHero({ k: `지금까지 시험한 모델 ${tN(x.n_trials_total)}개`, lv: (x.models || []).some((m) => m.status === "champion") ? "good" : "warn", big: (x.models || []).some((m) => m.status === "champion") ? "검증을 통과한 모델이 있어요" : "아직 통과한 모델이 없어요", sub: esc(koText(x.note || "")) })}
    ${tCard("모델", tList((x.models || []).map((m) => { const s = ST[m.status] || ["idle", m.status]; const mt = m.metrics || {}; return `<div class="t2-item"><div class="t-li"><span class="t-co-t"><b>${esc(m.name)}</b><span>${esc(tDate(m.created_at))} · ${tG("Sharpe", "샤프")} ${tN(mt.sharpe, 2)} · ${tG("DSR", "DSR")} ${tN(mt.dsr, 2)} · ${tG("MDD", "MDD")} ${tPct(mt.max_drawdown, 1)}</span></span><span class="t-li-r">${tSt(s[0], s[1])}</span></div>${m.notes ? `<div class="t2-why">${esc(koText(m.notes))}</div>` : ""}</div>`; }), "시험한 모델이 없어요"))}
    ${v && !v.error ? tCard("AI 보조 판단을 켤까", `${tStRow(v.title, v.message, v.status === "no_data" ? "idle" : v.status)}${v.min_days ? `<div class="t-gap"></div>${tProg((v.days || 0) / v.min_days, "", "기록 일수")}<div class="t-sub">기록 ${tN(v.days)} / ${tN(v.min_days)}일 · 채점 ${tN(v.scored)} / ${tN(v.min_scored)}건</div>` : ""}`) : ""}
    ${ev && !ev.error && (ev.recent || []).length ? tCard("최근 이벤트", tList(ev.recent.slice(0, 10).map((e) => `<div class="t-li"><span class="t-co-t"><b>${esc(e.title || e.type)}</b><span>${esc(tDate(e.date || e.ts))}</span></span></div>`))) : ""}
    ${tCard("새 모델이 거치는 단계", `<div class="t2-steps">${["가설 미리 적기", "과거로 시험", "그림자 채점", "모의 투자", "사용"].map((t) => `<span>${t}</span>`).join("")}</div><div class="t-sub">단계를 건너뛰지 않아요 · 탈락한 모델도 기록에 남겨요</div>`)}
    ${tFull("lab", '<a class="t-btn ghost" href="#models">모델 목록</a><a class="t-btn ghost" href="#research">백테스트</a>')}`);
};
TV.models = async (el) => {
  const ms = (S.data?.models || []);
  el.innerHTML = `<div class="ts t2 t2-models"></div>`;
  const root = el.querySelector(".ts");
  const ST = { champion: ["good", "사용 중"], shadow: ["warn", "그림자 시험"], candidate: ["warn", "후보"], rejected: ["bad", "탈락"], retired: ["idle", "은퇴"] };
  const champ = ms.find((m) => m.status === "champion");
  const chk = (ok, label, val) => tStRow(label, val, ok == null ? "idle" : ok ? "good" : "bad", { label: ok == null ? "-" : ok ? "통과" : "미달" });
  tPaint(root, "모델 목록 · 검증", `
    ${tHero({ k: `등록된 모델 ${ms.length}개`, lv: champ ? "good" : "warn", big: champ ? `${esc(champ.name)} 사용 중` : "사용 중인 모델이 없어요", sub: champ ? "" : "검증 기준을 넘은 모델이 없어 차트·Quant AI 는 쉬고, 다른 AI 의견만으로 판단해요" })}
    ${ms.map((m) => { const s = ST[m.status] || ["idle", m.status]; return tCard(`${esc(m.name)} <span class="t-h1s">${esc(tDate(m.created_at))}</span>`, `<div class="t2-cardtop">${tSt(s[0], s[1])}</div>
      ${tKV([[tG("Sharpe", "샤프"), tN(m.sharpe, 2), m.sharpe > 0 ? "up" : "down"], ["수익률", tPct(m.total_return, 1), tCls(m.total_return), m.benchmark_return != null ? `지수 ${tPct(m.benchmark_return, 0)}` : ""], [tG("MDD", "최대 낙폭"), tPct(m.mdd, 1), "down"], [tG("IC", "순위 예측력"), tN(m.ic, 3)]])}
      <div class="t-list">${chk(m.psr == null ? null : m.psr >= 0.9, "샤프가 우연이 아닐 확률 ≥ 90%", `PSR ${tN(m.psr, 2)}`)}${chk(m.dsr == null ? null : m.dsr >= 0.5, "여러 번 시험한 것을 깎아도 ≥ 50%", `DSR ${tN(m.dsr, 2)}`)}
        ${chk(m.stress_sharpe == null ? null : m.stress_sharpe >= 0, "비용이 2배여도 손해 아님", `샤프 ${tN(m.stress_sharpe, 2)}`)}${chk(m.ic == null ? null : m.ic >= 0.02, "순위 예측력 ≥ 0.02", `IC ${tN(m.ic, 3)}`)}${chk(m.mdd == null ? null : m.mdd >= -0.25, "최대 낙폭 -25% 이내", tPct(m.mdd, 1))}</div>
      ${m.notes ? `<div class="t-foot">${esc(koText(m.notes))}</div>` : ""}`); }).join("") || tEmpty("등록된 모델이 없어요", "quant-ai train 으로 학습하면 여기에 생겨요")}
    ${tFull("models", '<a class="t-btn ghost" href="#lab">모델 실험</a>')}`);
};

// ============================================================ 포트폴리오 · 위험 · 주문
const tField = (label, input, hint = "") => `<label class="t2-field"><span class="t-k">${label}</span>${input}${hint ? `<em>${hint}</em>` : ""}</label>`;
const PF_KO = { paper: "모의투자", shadow: "그림자", live: "실계좌" };
function tModeChips(id, cur) { return tChips(id, Object.entries(PF_KO), cur); }
TV.budget = async (el) => {
  const L = await tLoad(el, "budget", "내 투자 한도", "t2-bg", () => api("/api/budget"));
  if (!L) return;
  const b = L.d, sv = b.saved, u = b.usage;
  const LBL = { live_max_capital: "실전에 쓸 최대 돈", live_small_capital: "실전 첫 단계 (소액)", max_daily_loss_pct: "하루 최대 손실", max_position_weight: "한 종목 최대 비중", max_order_value: "한 번 주문 최대", max_var95: `하루 ${tG("VaR", "최대 예상 손실")}`, max_sector_weight: "한 업종 최대 비중" };
  const fmt = (k, v) => (k.includes("capital") || k === "max_order_value") ? tMoney(v) : tPr(v, 1);
  const plan = (p) => `${(p.plain || []).length ? `<div class="t-concl"><b>${p.plain.map((x) => esc(koText(x))).join("<br>")}</b></div>` : ""}
    ${(p.warnings || []).map((w) => `<div class="t-note warn">${esc(koText(w))}</div>`).join("")}
    <div class="t-list">${Object.entries(p.limits || {}).map(([k, v]) => `<div class="t-li"><span class="t-co-t"><b>${LBL[k] || esc(k)}</b><span class="t2-wrap">${esc(koText(p.why?.[k] || ""))}</span></span><span class="t-li-r"><b class="num">${fmt(k, v)}</b></span></div>`).join("")}</div>`;
  const replay = (r) => !r ? "" : !r.available ? `<div class="t-foot">${esc(r.message || "")}</div>` : tCard("이 한도였으면 과거에 몇 번 걸렸을까", `${tKV([["신규 매수 멈춤", `연 ${r.stop_per_year}번`, "", `하루 ${tPr(r.limit, 1)} 손실 때`], ["자동 정지", `연 ${r.kill_per_year}번`, r.kill_per_year > 1 ? "down" : "", `하루 ${tPr(r.kill_at, 1)} 손실 때`], ["최대 손실선에 닿은 해", tPr(r.hit_share), r.hit_share > 0.3 ? "down" : ""], ["같은 기간 최대 낙폭", tPct(r.max_drawdown, 0), "down"]])}<div class="t-sub">${(r.plain || []).map((x) => esc(koText(x))).join("<br>")}</div><div class="t-foot">${esc(r.source || "")} · ${esc(r.from || "")} ~ ${esc(r.to || "")}</div>`);
  const pol = sv?.on_stop || "hold";
  const POL_T = { hold: "그대로 두기", reduce: "절반 줄이기", liquidate: "전부 정리" };
  tPaint(L.root, "내 투자 한도", `
    ${u && u.equity != null ? tHero({ k: "최대 손실 한도 중 지금까지 쓴 만큼", lv: u.used >= 0.8 ? "bad" : u.used >= 0.5 ? "warn" : "good", big: `${tPr(u.used)} 썼어요`, body: `${tProg(u.used, u.used >= 0.8 ? "bad" : u.used >= 0.5 ? "warn" : "good", "한도 사용")}${tKV([["넣은 돈", tMoney(u.invested ?? u.base)], ["지금 평가", tMoney(u.equity)], ["손실", tMoney(u.loss), "down"], ["한도", tMoney(u.limit)]])}`, foot: "80% 에서 알림 · 100% 에서 신규 매수 정지 · 입출금은 기록해야 손실로 잘못 보이지 않아요" })
      : tHero({ k: "원금과 '최대로 감당할 손실'만 정하면", lv: sv ? "good" : "warn", big: sv ? `원금 ${tMoney(sv.principal)} · 최대 손실 ${tMoney(sv.max_loss)}` : "아직 한도를 정하지 않았어요", sub: sv ? "모든 한도가 이 두 숫자에서 계산돼요" : "지금은 기본 한도로 움직여요. 아래에서 두 숫자만 넣어 보세요" })}
    ${tCard("한도 정하기", `<div class="t-form">
      ${tField("원금", `<input id="bg-p" class="t-in" type="number" inputmode="numeric" step="100000" value="${sv?.principal ?? ""}" placeholder="예: 10000000">`, "투자에 쓰는 전체 돈 (원)")}
      ${tField("최대로 감당할 손실", `<input id="bg-l" class="t-in" type="number" inputmode="numeric" step="10000" value="${sv?.max_loss ?? ""}" placeholder="예: 1000000">`, "이만큼 잃으면 멈춰요 (원)")}
      ${tField("실전 첫 단계 비율", `<input id="bg-f" class="t-in" type="number" step="0.01" min="0.01" max="0.5" value="${sv?.first_stage ?? 0.1}">`, "처음엔 원금의 이만큼만 실전에 (0.1 = 10%)")}</div>
      <div class="t-k" style="margin:14px 0 8px">한도에 닿아 멈췄을 때 가진 주식은?</div>
      ${tChips("bg-pol", Object.keys(b.policies || {}).map((k) => [k, POL_T[k] || k]), pol)}
      <div class="t-sub" id="bg-pol-d" style="margin-top:8px">${esc(koText((b.policies || {})[pol] || ""))}</div>
      <div class="t-pro"><button class="t-btn ghost" id="bg-prev">미리 보기 (과거로 재생)</button><button class="t-btn primary" id="bg-save">저장하고 바로 적용</button></div>
      <div id="bg-out" style="margin-top:14px">${sv ? plan(sv) : ""}</div>`)}
    ${replay(b.replay)}
    ${b.stop_sheet?.rows?.length ? tCard("멈춘 뒤 팔 주문표", `<div class="t-note warn">${esc(koText(b.stop_sheet.text || ""))} — 자동으로 팔지 않았어요. 확인 후 직접 주문하세요</div>${tList(b.stop_sheet.rows.map((r) => tSym(r.symbol, r.name || r.symbol, `보유 ${num(r.held)}주`, `<b class="num down">${num(r.sell_qty)}주 매도</b>`)))}`) : ""}
    ${tCard("지금 적용 중인 한도", tList(Object.entries(b.current || {}).map(([k, v]) => `<div class="t-li"><span class="t-co-t"><b>${LBL[k] || esc(k)}</b></span><span class="t-li-r"><b class="num">${fmt(k, v)}</b></span></div>`)))}
    ${tFull("budget", '<a class="t-btn ghost" href="#risk">위험 관리</a><a class="t-btn ghost" href="#goal">내 목표</a>')}`);
  let curPol = pol;
  tBind(L.root, "bg-pol", (k) => { curPol = k; L.root.querySelectorAll("#bg-pol button").forEach((x) => x.classList.toggle("on", x.dataset.k === k)); L.root.querySelector("#bg-pol-d").textContent = koText((b.policies || {})[k] || ""); });
  const vals = () => ({ principal: L.root.querySelector("#bg-p").value, max_loss: L.root.querySelector("#bg-l").value, first_stage: L.root.querySelector("#bg-f").value, on_stop: curPol });
  const out = L.root.querySelector("#bg-out");
  L.root.querySelector("#bg-prev").onclick = async () => {
    const v = vals();
    out.innerHTML = tSkel(3);
    const r = await api(`/api/budget?principal=${encodeURIComponent(v.principal)}&max_loss=${encodeURIComponent(v.max_loss)}&on_stop=${v.on_stop}`).catch((e) => ({ error: e.message }));
    out.innerHTML = r.error ? tErr(r.error) : `${plan(r.preview)}${replay(r.replay)}<div class="t-foot">미리 보기예요 — 저장해야 적용돼요</div>`;
  };
  L.root.querySelector("#bg-save").onclick = async () => {
    const r = await post("/api/budget", vals()).catch((e) => ({ error: e.message }));
    if (r.error) { out.innerHTML = tErr(r.error); return; }
    toast({ title: "투자 한도를 저장했어요", body: `원금 ${tMoney(r.principal)} · 최대 손실 ${tMoney(r.max_loss)}`, level: "good" });
    TV.budget(el);
  };
};
TV.portfolio = async (el) => {
  const d = S.data || {};
  const mode = T2.pfMode || S.pfMode || "paper";
  const pf = (d.portfolios || {})[mode] || {};
  el.innerHTML = `<div class="ts t2 t2-pfb"></div>`;
  const root = el.querySelector(".ts");
  const pos = (pf.positions || []).slice().sort((a, b) => (b.value ?? b.qty * (b.price || 0)) - (a.value ?? a.qty * (a.price || 0)));
  const curve = (pf.curve || []).map((x) => Array.isArray(x) ? x[1] : x);
  tPaint(root, "장부별 포트폴리오", `
    ${tModeChips("pb-mode", mode)}<div class="t-gap"></div>
    ${pf.equity == null ? tEmpty(`${PF_KO[mode]} 장부가 비어 있어요`, mode === "live" ? "실계좌(KIS)를 연결하고 주문이 나가면 생겨요" : "전략이 한 번 돌면 생겨요") : `
    ${tHero({ k: `${PF_KO[mode]} 장부 · 평가금액`, lv: "idle", big: `<span class="num">${tWon(pf.equity)}</span>`, sub: `<span class="num ${tCls(pf.return_pct)}">${tPct(pf.return_pct)}</span> · 시작 ${tMoney(pf.start_equity)} · 현금 ${tMoney(pf.cash)}`, body: curve.length > 1 ? tSpark(curve, { h: 80, w: 600 }) : "", foot: curve.length > 1 ? `최근 ${curve.length}번 평가` : "자산 흐름은 하루 이상 운용하면 나와요" })}
    ${tCard(`보유 ${pos.length}종목`, tList(pos.map((p) => tSym(p.symbol, p.name, `${num(p.qty)}주 · 평균 ${tPx(p.avg_price, p.symbol)}`, `<b class="num t-pv">${tPx(p.price ?? p.last, p.symbol)}</b><span class="num t-pc ${tCls(p.pnl_pct)}">${tPct(p.pnl_pct)}</span>`)), "보유 종목이 없어요"))}`}
    ${tFull("portfolio", '<a class="t-btn ghost" href="#pos">내 자산 한눈에</a><a class="t-btn ghost" href="#orders">주문 내역</a>')}`);
  tBind(root, "pb-mode", (k) => { T2.pfMode = k; TV.portfolio(el); });
};
TV.risk = async (el) => {
  const mode = T2.riskMode || (typeof homeMode === "function" && S.data ? homeMode(S.data) : "paper");
  const L = await tLoad(el, "risk", "위험 관리", "t2-risk", () => api(`/api/risk?mode=${mode}`));
  if (!L) return;
  const r = L.d;
  if (r.error || !r.n_positions) {
    tPaint(L.root, "위험 관리", `${tModeChips("rk-mode", mode)}<div class="t-gap"></div>${tEmpty(`${PF_KO[mode]} 장부에 보유 종목이 없어요`, r.error || "주식을 담으면 위험을 계산해요")}${tFull("risk")}`);
    tBind(L.root, "rk-mode", (k) => { T2.riskMode = k; TV.risk(el); });
    return;
  }
  const c = r.concentration || {}, lim = r.limits || {}, v = r.var || {};
  const over = r.var95 > (lim.max_var95 ?? 1);
  const lv = over || (r.warnings || []).length > 1 ? "bad" : (r.warnings || []).length ? "warn" : "good";
  tPaint(L.root, "위험 관리", `
    ${tModeChips("rk-mode", mode)}<div class="t-gap"></div>
    ${tHero({ k: `${PF_KO[mode]} 장부 · ${r.n_positions}종목 · 최근 ${r.days}거래일로 계산`, lv, big: `나쁜 날 하루 ${tPr(r.var95, 1)} 정도 잃을 수 있어요`,
      sub: `100일 중 5일쯤은 이보다 더 잃어요 · 그런 날 평균 ${tPr(r.es95, 1)} · 지금까지 가장 나빴던 날 ${tPct(r.worst_day, 1)} (${esc(tDate(r.worst_day_date))})`,
      body: `${tVs([[tG("VaR", "하루 최대 예상 손실"), r.var95, over ? "down" : "acc"], ["한도", lim.max_var95, "dim"]], { max: Math.max(r.var95 || 0, lim.max_var95 || 0) * 1.2, fmt: (x) => tPr(x, 1) })}` })}
    ${(r.warnings || []).map((w) => `<div class="t-note warn">${esc(koText(w))}</div>`).join("")}
    ${tKV([[tG("베타", "시장 민감도"), tN(r.beta, 2), "", "시장 1% → 이만큼"], ["시장 -10% 일 때", tPct(r["stress_market_-10pct"], 1), "down"], [tG("HHI·실효 종목 수", "실제로 나눠 담은 수"), `${tN(c.effective_n, 1)}개`, c.effective_n < (lim.min_effective_n ?? 5) ? "down" : ""], ["1년 변동성", tPr(r.vol, 0)]])}
    <div class="t-grid2">
      ${tCard("가장 큰 종목 비중", tVs([["1등 종목", c.top1, c.top1 > (lim.max_name_weight ?? 1) ? "down" : "acc"], ["상위 5종목", c.top5, "acc"]], { max: 1 }))}
      ${tCard("업종 쏠림", tVs((r.sectors || []).slice(0, 6).map((s) => [esc(s.sector), s.weight, s.weight > (lim.max_sector_weight ?? 1) ? "down" : "acc", `${tPr(s.weight)} · ${s.n}종목`]), { max: 1 }))}
    </div>
    ${tCard("위험을 많이 만드는 종목", tList((r.risk_contrib || []).slice(0, 8).map((x) => tSym(x.symbol, x.name, `비중 ${tPr(x.weight, 1)}`, `<span class="t-li-r t2-w">${tProg(x.share * 4, x.share > x.weight * 1.5 ? "bad" : "")}<b class="num">${tPr(x.share, 1)}</b></span>`))), "<span class='t-sub'>전체 위험 중 몫</span>")}
    ${(r.clusters || []).length ? tCard("함께 움직이는 묶음", tList(r.clusters.map((g) => `<div class="t-li"><span class="t-co-t"><b class="t2-wrap">${g.members.map(esc).join(", ")}</b><span>같이 오르내리는 경향이 강해요</span></span><span class="t-li-r"><b class="num ${g.weight > (lim.max_cluster_weight ?? 0.4) ? "down" : ""}">${tPr(g.weight)}</b></span></div>`))) : ""}
    ${tCard("계산 방법별 하루 최대 손실", tKV([["과거 그대로", tPr(v.hist95, 2)], ["정규분포 가정", tPr(v.normal95, 2)], ["꼬리 보정", tPr(v.cornish_fisher95, 2)], ["최근 가중", tPr(v.ewma95, 2)], ["10일 기준", tPr(v.hist95_10d, 1)], ["나눠 담은 효과", tPr(r.diversification_benefit, 0), "up"]]))}
    ${tFull("risk", '<a class="t-btn ghost" href="#budget">투자 한도</a><a class="t-btn ghost" href="#safety">안전 센터</a>')}`);
  tBind(L.root, "rk-mode", (k) => { T2.riskMode = k; TV.risk(el); });
};
TV.accounts = async (el) => {
  const L = await tLoad(el, "accounts", "계좌 · 세금 · 배당", "t2-acct", () => api("/api/accounts"));
  if (!L) return;
  const a = L.d, t = a.total || {}, ov = a.overseas || {};
  const form = (acct) => {
    const types = Object.entries(a.types || {}).map(([k, v]) => `<option value="${k}" ${acct?.type === k ? "selected" : ""}>${esc(v)}</option>`).join("");
    const hs = (acct?.holdings || []).map((h) => `${h.symbol},${h.qty},${h.avg_price}`).join("\n");
    tSheet(`<h3>${acct ? "계좌 고치기" : "계좌 추가"}</h3><form class="t-form" id="ac-f">
      ${tField("이름", `<input class="t-in" name="name" required maxlength="40" placeholder="예: 키움 ISA" value="${esc(acct?.name || "")}">`)}
      ${tField("종류", `<select class="t-in" name="type">${types}</select>`)}
      ${tField("증권사", `<input class="t-in" name="broker" maxlength="30" value="${esc(acct?.broker || "")}">`)}
      ${tField("현금 (원)", `<input class="t-in" name="cash" type="number" step="any" value="${acct?.cash ?? ""}">`)}
      ${tField("올해 실현 손익 (원)", `<input class="t-in" name="realized_ytd" type="number" step="any" value="${acct?.realized_ytd ?? ""}">`)}
      ${tField("올해 받은 배당 (원)", `<input class="t-in" name="dividends_ytd" type="number" step="any" value="${acct?.dividends_ytd ?? ""}">`)}
      <label class="t2-check"><input type="checkbox" name="low_income" ${acct?.low_income ? "checked" : ""}> ISA 서민형 (400만원까지 비과세)</label>
      ${tField("보유 종목", `<textarea class="t-in" name="holdings" rows="4" placeholder="종목,수량,평균단가 (한 줄에 하나)&#10;005930,10,71000">${esc(hs)}</textarea>`)}
      <div class="t-sheet-act"><button type="button" class="t-btn" id="ac-x">취소</button><button class="t-btn primary">저장</button></div></form>`, (box, close) => {
      box.querySelector("#ac-x").onclick = close;
      const f = box.querySelector("#ac-f");
      f.onsubmit = async (e) => {
        e.preventDefault();
        const fd = Object.fromEntries(new FormData(f).entries());
        const holdings = (fd.holdings || "").split("\n").map((l) => l.split(",").map((x) => x.trim())).filter((x) => x[0]).map(([symbol, qty, avg]) => ({ symbol, qty: Number(qty), avg_price: Number(avg) }));
        try { await post("/api/accounts", { ...fd, id: acct?.id, low_income: !!fd.low_income, holdings }); toast({ title: "계좌를 저장했어요", body: fd.name, level: "good" }); close(); TV.accounts(el); }
        catch (err) { toast({ title: "저장하지 못했어요", body: err.message, level: "bad" }); }
      };
    });
  };
  tPaint(L.root, "계좌 · 세금 · 배당", `
    ${tHero({ k: `계좌 ${(a.accounts || []).length}개 합계`, lv: "idle", big: `<span class="num">${tWon(t.value)}</span>`, sub: `<span class="num ${tCls(t.gain)}">${tSigned(t.gain)}</span> · 12개월 배당 ${tMoney(t.div_12m)} · 올해 세금 추정 ${tMoney(t.tax_now)}` })}
    <div class="t-pro" style="margin-top:0"><button class="t-btn primary" id="ac-new">계좌 추가</button></div><div class="t-gap"></div>
    ${(a.accounts || []).map((x) => tCard(`${esc(x.name)} <span class="t-tag">${esc(x.type_label)}</span>`, `
      ${tKV([["평가 + 현금", tWon(x.total)], ["손익", `${tSigned(x.gain)}`, tCls(x.gain), tPct(x.ret, 1)], ["현금", tMoney(x.cash)], ["세금 추정", tMoney(x.tax_now)]])}
      ${tList((x.holdings || []).map((h) => tSym(h.symbol, h.name || h.symbol, `${num(h.qty)}주 · 평균 ${tPx(h.avg_price, h.symbol)}`, `<b class="num t-pv">${tWon(h.value_krw ?? h.value)}</b><span class="num t-pc ${tCls(h.ret ?? h.pnl_pct)}">${tPct(h.ret ?? h.pnl_pct)}</span>`)), "보유 종목을 넣지 않았어요")}
      ${x.note ? `<div class="t-foot">${esc(koText(x.note))}</div>` : ""}`, x.readonly ? "" : `<button class="t-link" data-ac-edit="${esc(x.id)}">고치기</button>`)).join("") || tEmpty("아직 계좌가 없어요", "'계좌 추가'로 ISA·연금저축·일반 계좌를 넣으면 세금·배당을 계산해요")}
    <div class="t-grid2">
      ${tCard("해외 주식 양도세 (올해 추정)", tKV([["이익", tMoney(ov.gain_ytd_est)], ["공제", tMoney(ov.deduction)], ["세금", tMoney(ov.tax), ov.tax ? "down" : ""]]))}
      ${tCard("금융소득 (이자·배당)", `${tKV([["올해 추정", tMoney(a.financial_income_est)], ["종합과세 기준", tMoney(a.rules?.financial_income_threshold)]])}${tProg((a.financial_income_est || 0) / (a.rules?.financial_income_threshold || 1), "")}`)}
    </div>
    ${(a.dividend_calendar || []).length ? tCard("배당 일정", tList(a.dividend_calendar.map((d) => tSym(d.symbol, d.name || d.symbol, `${esc(tDate(d.ex_date))} 배당락 · ${esc(d.account || "")}`, `<b class="num">${tWon(d.div)}</b>`)))) : ""}
    ${(a.tips || []).length ? tCard("절세 팁", `<ol class="t-ol">${a.tips.map((x, i) => `<li><span class="t-num">${i + 1}</span>${esc(koText(x))}</li>`).join("")}</ol>`) : ""}
    <div class="t-foot">환율 ${num(a.fx, 1)}원 (${esc(a.fx_source || "")}) · 세율 기준 ${esc(String(a.rules?.year || ""))}년 · 참고용 추정 — 실제 신고는 증권사·세무 안내를 따르세요</div>
    ${tFull("accounts", '<a class="t-btn ghost" href="#goal">내 목표</a>')}`);
  L.root.querySelector("#ac-new").onclick = () => form(null);
  L.root.querySelectorAll("[data-ac-edit]").forEach((b) => b.onclick = () => form((a.accounts || []).find((x) => x.id === b.dataset.acEdit)));
};
TV.manual = async (el) => {
  const us = T2.manUS === "us";
  const L = await tLoad(el, "manual", "수동 모의 장부", "t2-man", () => api(`/api/ticket/book${us ? "?mode=us-manual" : ""}`));
  if (!L) return;
  const b = L.d, money = (v) => us ? `$${num(v, 2)}` : tWon(v);
  tPaint(L.root, "수동 모의 장부", `
    ${tChips("mn-mk", [["kr", "국내"], ["us", "미국"]], us ? "us" : "kr")}<div class="t-gap"></div>
    ${tHero({ k: `종목 화면의 '모의 매수'로 담은 것 · 실제 돈 아님${us ? " · 달러 장부" : ""}`, lv: "idle", big: `<span class="num">${money(b.equity)}</span>`,
      sub: `<span class="num ${tCls(b.return)}">${tPct(b.return)}</span> · 현금 ${us ? money(b.cash) : tMoney(b.cash)}${us && b.equity_krw ? ` · 약 ${tMoney(b.equity_krw)} (환율 ${num(b.fx, 1)}원)` : ""}` })}
    ${tCard(`보유 ${(b.positions || []).length}종목`, tList((b.positions || []).map((p) => tSym(p.symbol, p.name || p.symbol, `${num(p.qty)}주 · 평균 ${tPx(p.avg_price, p.symbol)}`, `<b class="num t-pv">${tPx(p.price, p.symbol)}</b><span class="num t-pc ${tCls(p.pnl_pct)}">${tPct(p.pnl_pct)}</span>`)), "아직 모의 주문이 없어요", us ? "미국 종목(예: NVDA)을 검색해서 '모의 매수'를 눌러 보세요" : "종목을 검색해서 '모의 매수'를 눌러 보세요"))}
    ${us && b.fx_source ? `<div class="t-foot">환율: ${esc(b.fx_source)}</div>` : ""}
    <div class="t-pro"><button class="t-btn primary" data-t-search>종목 검색</button><a class="t-btn ghost" href="#orders">주문 내역</a></div>
    ${tFull("manual")}`);
  tBindBar(L.root);
  tBind(L.root, "mn-mk", (k) => { T2.manUS = k; TV.manual(el); });
};
TV.core = async (el) => {
  const L = await tLoad(el, "core", "자동매매 설정", "t2-core", () => api("/api/core-satellite"));
  if (!L) return;
  const c = L.d, p = c.plan || {}, cfg = p.config || {}, tr = p.trend || {}, h = c.health || {}, nm = p.names || {};
  const core = (p.core || []);
  tPaint(L.root, "자동매매 설정", `
    ${tHero({ k: `${esc(PF_KO[p.mode] || p.mode || "")} · 기준 ${esc(tDate(p.as_of))}`, lv: tLv(h.status) === "idle" ? "warn" : tLv(h.status), big: `핵심 ${core.length}종목 + 위성 ${(p.satellite || []).length}종목`,
      sub: `돈의 ${tPr(cfg.core_weight)}는 점수 상위 ${cfg.core_top_k}종목에 똑같이 나눠 담고, ${cfg.core_rebalance_days}거래일마다 다시 맞춰요`, foot: esc(koText(h.action || "")) })}
    ${tKV([["시장 추세", tr.below ? "약함 (줄여 담기)" : "정상", tr.below ? "down" : "", tr.index ? `지수 ${num(tr.index, 1)} · 200일선 ${num(tr.ma, 1)}` : ""], ["후보 종목", `${tN(p.universe_size)}개`], ["다시 맞춘 횟수", `${tN(p.rebalances)}번`, "", tDate(p.last_rebalance)], ["AI 보조", cfg.use_ai ? "켜짐" : "꺼짐"]])}
    ${(p.notes || []).length ? `<div class="t-note">${p.notes.map((x) => esc(koText(x))).join("<br>")}</div>` : ""}
    ${tCard("지금 담는 종목 (점수 순)", tList(core.map((s, i) => tSym(s, nm[s] || s, `점수 ${tN(p.scores?.[s], 2)}`, `<span class="t-rank">${i + 1}</span><b class="num">${tPr(p.weights?.[s], 1)}</b>`))))}
    ${(p.satellite || []).length ? tCard("위성 (AI 확신 높은 종목)", tList(p.satellite.map((s) => { const sym = s.symbol || s; return tSym(sym, nm[sym] || s.name || sym, s.reason ? esc(koText(s.reason)) : "", `<b class="num">${tPr(p.weights?.[sym] ?? s.weight, 1)}</b>`); }))) : ""}
    ${(h.checks || []).length ? tCard("전략 건강 점검", tList(h.checks.map((x) => tStRow(x.label || x.name, x.detail || x.message, x.status)))) : ""}
    ${tCard("규칙", tList([["점수 매기기", Object.entries(cfg.factor_weights || {}).map(([k, w]) => `${{ mom_12_1: "12개월 오름세", vol_60: "60일 출렁임", dist_52w: "52주 고점 근처" }[k] || k}${w < 0 ? "(낮을수록 좋음)" : ""}`).join(" · ")], ["다시 맞추기", `${cfg.core_rebalance_days}거래일마다 · 상위 ${cfg.core_buffer_k}위 안이면 그대로 둠 (잦은 교체 방지)`], ["시장이 약할 때", `지수가 ${cfg.trend_ma}일선 아래면 ${tPr(cfg.trend_off_scale)}만 담기`], ["위성 크기", `확신 ${cfg.satellite_min_confidence} 이상 · ${cfg.satellite_k}종목까지`]].map(([k, v]) => `<div class="t-li"><span class="t-co-t"><b>${k}</b><span class="t2-wrap">${esc(v)}</span></span></div>`)))}
    ${tFull("core", '<a class="t-btn ghost" href="#sheet">국내 주문표</a><a class="t-btn ghost" href="#portfolio">장부</a>')}`);
};
TV.orders = async (el) => {
  const L = await tLoad(el, "orders", "주문 내역", "t2-ord", () => api("/api/orders"));
  if (!L) return;
  const o = L.d, rows = o.rows || [];
  const ST = { filled: ["체결", "good"], partial: ["일부 체결", "warn"], submitted: ["접수", "warn"], rejected: ["거부", "bad"], cancelled: ["취소", "idle"], error: ["오류", "bad"], unknown: ["확인 필요", "warn"], blocked: ["막힘", "bad"], simulated: ["가상 체결", "good"] };
  const modes = ["all", ...new Set(rows.map((r) => r.mode))];
  const draw = () => {
    const xs = rows.filter((r) => T2.ordMode === "all" || r.mode === T2.ordMode);
    const sl = (o.slippage || {}).paper || {};
    tPaint(L.root, "주문 내역", `
      ${tKV(Object.entries(o.counts || {}).map(([k, n]) => [ST[k]?.[0] || esc(k), `${n}건`, k === "rejected" || k === "error" ? "down" : ""]))}
      ${sl.n ? `<div class="t-note${/나쁨/.test(sl.verdict || "") ? " warn" : ""}">모의 체결 ${tN(sl.n)}건 평균 ${tG("슬리피지", "체결 차이")} ${tN(sl.mean_bps, 1)}bp (가정 ${tN(sl.assumed_bps, 1)}bp) — ${esc(koText(sl.verdict || ""))}</div>` : ""}
      ${tChips("ord-m", modes.map((m) => [m, m === "all" ? "전체" : (PF_KO[m] || { manual: "수동 모의", "us-paper": "미국 모의" }[m] || m)]), T2.ordMode)}<div class="t-gap"></div>
      ${tCard("", tList(xs.map((r) => { const s = ST[r.status] || [r.status, "idle"]; return `<div class="t2-item">${tSym(r.symbol, r.name, `${esc(tAgo(r.ts))} · ${esc(PF_KO[r.mode] || r.mode)}${r.avg_price ? ` · 체결 ${tPx(r.avg_price, r.symbol)}` : r.ref_price ? ` · 기준 ${tPx(r.ref_price, r.symbol)}` : ""}`, `<b class="${r.side === "buy" ? "up" : "down"}">${r.side === "buy" ? "매수" : "매도"} ${num(r.qty)}주</b>${tSt(s[1], s[0])}`)}${r.status === "rejected" && r.reason ? `<div class="t2-why">${esc(koText(r.reason))}</div>` : ""}</div>`; }), "주문 기록이 없어요"))}
      ${tFull("orders", '<a class="t-btn ghost" href="#execution">체결 품질</a>')}`);
    tBind(L.root, "ord-m", (k) => { T2.ordMode = k; draw(); });
  };
  draw();
};
TV.execution = async (el) => {
  const L = await tLoad(el, "execution", "체결 품질 · 증권사 점검", "t2-exe", () => Promise.all([api("/api/execution"), tSafe(api("/api/an/execution"))]));
  if (!L) return;
  const [x, ae] = L.d, k = x.kis || {}, sl = x.slippage || {}, ca = x.costs?.assumed || {};
  tPaint(L.root, "체결 품질 · 증권사 점검", `
    ${tHero({ k: `증권사 연결 · ${esc(x.kis_env === "demo" ? "모의투자 서버" : x.kis_env || "")}`, lv: k.ok ? "good" : "warn", big: k.ok ? "한국투자증권 연결됨" : "증권사가 연결되지 않았어요", sub: esc(koText(k.message || "")), foot: k.at ? `마지막 확인 ${esc(tAgo(k.at))}` : "" })}
    ${(k.steps || []).length ? tCard("연결 점검 단계", tList(k.steps.map((s) => tStRow(s.name || s.title, s.detail || s.message, s.ok === true ? "good" : s.ok === false ? "bad" : s.status)))) : ""}
    ${ae && !ae.error ? tCard(`최근 ${tN(ae.days)}일 체결`, tKV([["주문", `${tN(ae.orders)}건`], ["체결률", tPr(ae.fill_rate)], [tG("슬리피지", "체결 차이"), `${tN(ae.slippage_bps?.mean, 1)}bp`, "", `가운데 ${tN(ae.slippage_bps?.median, 1)} · 나쁜 10% ${tN(ae.slippage_bps?.p90, 1)}`], ["숨은 비용", tWon(ae.implementation_shortfall_krw)]])) : ""}
    ${tCard("비용 가정 (백테스트·모의에 쓰는 값)", tKV([["수수료", `${tN(ca.commission_bps, 1)}bp`], ["체결 차이", `${tN(ca.slippage_bps, 1)}bp`], ["매도세", `${tN(ca.sell_tax_bps, 1)}bp`], ["큰 주문 충격", `계수 ${tN(ca.impact_coef, 2)}`]]) + `<div class="t-sub">${esc(koText(sl.live?.verdict || ""))} · 실제 체결이 쌓이면 자동으로 보정해요${sl.autocal ? " (자동 보정 켜짐)" : ""}</div>`)}
    ${tFull("execution", '<a class="t-btn ghost" href="#orders">주문 내역</a><a class="t-btn ghost" href="#validation">실전 검증 진행표</a>')}`);
};
TV.sheet = async (el) => {
  el.innerHTML = `<div class="ts t2 t2-sheet"></div>`;
  const root = el.querySelector(".ts");
  const saved = { cash: safeGet("qa_os_cash") || "", holdings: safeGet("qa_os_holdings") || "" };
  const SK = { BUY: ["매수", "up"], SELL: ["매도", "down"], HOLD: ["유지", ""] };
  tPaint(root, "국내 주문표", `
    <div class="t-note">자동매매 없이 이 전략을 따라 하는 표예요. 주문은 내지 않아요 — 증권사 앱에서 보고 그대로 넣으세요.</div>
    ${tCard("내 계좌 넣기", `<div class="t-form">
      ${tField("주문 가능 현금 (원)", `<input id="os-cash" class="t-in" inputmode="numeric" placeholder="10000000" value="${esc(saved.cash)}">`)}
      ${tField("보유 종목", `<textarea id="os-h" class="t-in" rows="5" placeholder="종목코드,수량 (한 줄에 하나)&#10;005930,10&#10;000660,3">${esc(saved.holdings)}</textarea>`, "입력한 값은 이 브라우저에만 저장돼요")}</div>
      <label class="t2-check"><input type="checkbox" id="os-ai"> AI 보조 판단도 쓰기 (LLM 키가 있으면 비용이 들어요)</label>
      <div class="t-pro"><button class="t-btn primary" id="os-run">주문표 만들기</button></div>`)}
    <div id="os-out"></div>
    ${tCard("이렇게 따라 하세요", `<ol class="t-ol">${["한 달에 한 번(약 20거래일마다)만 실행하고 중간엔 손대지 않기", "파는 주문 먼저 → 체결 확인 → 사는 주문 (09:10 이후 지정가)", "지정가를 넘는 급변이면 그날은 건너뛰고 다음 날 다시"].map((t, i) => `<li><span class="t-num">${i + 1}</span>${t}</li>`).join("")}</ol>`)}
    ${tFull("sheet", '<a class="t-btn ghost" href="#usorder">미국 주문표</a>')}`);
  root.querySelector("#os-run").onclick = async () => {
    const cash = root.querySelector("#os-cash").value.replace(/[, 원]/g, ""), holdings = root.querySelector("#os-h").value;
    safeSet("qa_os_cash", cash); safeSet("qa_os_holdings", holdings);
    const out = root.querySelector("#os-out");
    out.innerHTML = tSkel(4);
    let r;
    try { r = await post("/api/order-sheet", { cash, holdings, use_ai: root.querySelector("#os-ai").checked }); } catch (e) { out.innerHTML = tErr(e); return; }
    if (r.error) { out.innerHTML = tErr(r.error); return; }
    const t = r.totals || {}, tr = r.trend || {};
    const lines = (r.lines || []).filter((l) => l.side !== "HOLD"), holds = (r.lines || []).filter((l) => l.side === "HOLD");
    out.innerHTML = `${tKV([["평가금액", tMoney(r.equity), "", `${esc(r.as_of)} 종가`], ["팔기 / 사기", `${t.n_sell} / ${t.n_buy}건`, "", `${tMoney(t.sell_value)} / ${tMoney(t.buy_value)}`], ["예상 비용", tWon(t.cost), "", `회전율 ${tPr(t.turnover)}`], ["시장 추세", tr.below ? "약함 (줄여 담기)" : "정상", tr.below ? "down" : ""]])}
      ${tCard("순서대로 주문하세요", tList(lines.map((l, i) => `<div class="t2-item">${tSym(l.symbol, l.name, `${i + 1}번 · ${num(l.current_qty)} → ${num(l.target_qty)}주 · 목표 ${tPr(l.target_weight, 1)}`, `<b class="${SK[l.side][1]}">${SK[l.side][0]} ${num(l.qty)}주</b><span class="t-sub num">지정가 ${l.limit_price ? num(l.limit_price) : "-"}</span>`)}${l.reason ? `<div class="t2-why">${esc(koText(l.reason))}</div>` : ""}</div>`), "바꿀 게 없어요", "지금 보유가 목표와 같아요"), '<button class="t-link" id="os-csv">CSV 받기</button>')}
      ${holds.length ? tCard(`그대로 둘 종목 ${holds.length}개`, tList(holds.map((l) => tSym(l.symbol, l.name, `${num(l.current_qty)}주`, `<span class="t-sub">유지</span>`)))) : ""}
      ${(r.notes || []).length ? `<div class="t-foot">${r.notes.map((x) => esc(koText(x))).join("<br>")}</div>` : ""}
      <div class="t-foot">주문 뒤 현금 ${tWon(r.cash_after)} · 투자 비중 ${tPr(t.invested_after)}</div>`;
    out.querySelector("#os-csv").onclick = () => { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob(["﻿" + r.csv], { type: "text/csv;charset=utf-8" })); a.download = `orders_${r.as_of}.csv`; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 1000); };
  };
};
TV.usorder = async (el) => {
  el.innerHTML = `<div class="ts t2 t2-us"></div>`;
  const root = el.querySelector(".ts");
  S.us = S.us || { holdings: "", targets: "", avg: "", prices: "", cash_usd: "", cash_krw: "", ytd: "" };
  const u = S.us;
  const ta = (id, v, ph, rows = 4) => `<textarea id="${id}" class="t-in" rows="${rows}" placeholder="${ph}">${esc(v)}</textarea>`;
  tPaint(root, "미국 주식 주문표", `
    <div class="t-note">증권사 앱에서 그대로 따라 넣는 표예요 — 환전·수수료·양도세 추정까지 계산해요. 해외 실주문은 아직 열지 않았어요 (지정가 권장).</div>
    ${tCard("내 계좌 넣기", `<div class="t-form">
      ${tField("보유", ta("us-h", u.holdings, "AAPL,10&#10;MSFT,3"), "종목,수량")}
      ${tField("목표 비중 (비우면 시스템 미국 모의 장부를 따라가요)", ta("us-t", u.targets, "AAPL,0.3&#10;NVDA,0.4"), "종목,비중")}
      ${tField("평균 단가 (매도 이익·세금 계산용)", ta("us-a", u.avg, "AAPL,150,1310", 3), "종목,평단 달러,산 날 환율")}
      ${tField("지금 가격 (선택)", ta("us-p", u.prices, "AAPL,195.3", 2), "비우면 마지막 종가")}
      ${tField("달러 현금 (USD)", `<input id="us-cu" class="t-in" inputmode="decimal" value="${esc(u.cash_usd)}" placeholder="0">`)}
      ${tField("환전할 원화 (원)", `<input id="us-ck" class="t-in" inputmode="decimal" value="${esc(u.cash_krw)}" placeholder="0">`)}
      ${tField("올해 이미 낸 해외주식 이익 (원)", `<input id="us-y" class="t-in" inputmode="decimal" value="${esc(u.ytd)}" placeholder="0">`, "양도세 250만원 공제 계산용")}</div>
      <div class="t-pro"><button class="t-btn primary" id="us-go">주문표 만들기</button></div>`)}
    <div id="us-out"></div>
    ${tFull("usorder", '<a class="t-btn ghost" href="#sheet">국내 주문표</a>')}`);
  const pairs = (s) => Object.fromEntries(s.split(/\n/).map((x) => x.split(",").map((y) => y.trim())).filter((x) => x.length === 2 && x[0] && x[1]));
  root.querySelector("#us-go").onclick = async () => {
    const g = (id) => root.querySelector(id).value;
    Object.assign(u, { holdings: g("#us-h"), targets: g("#us-t"), avg: g("#us-a"), prices: g("#us-p"), cash_usd: g("#us-cu"), cash_krw: g("#us-ck"), ytd: g("#us-y") });
    const box = root.querySelector("#us-out");
    box.innerHTML = tSkel(4);
    let r;
    try { r = await post("/api/us-sheet", { holdings: pairs(u.holdings), targets: u.targets.trim() ? pairs(u.targets) : null, avg_cost: u.avg.trim() || null, prices: u.prices.trim() ? pairs(u.prices) : null, cash_usd: u.cash_usd, cash_krw: u.cash_krw, ytd_gain_krw: u.ytd }); } catch (e) { r = { error: e.message }; }
    if (r.error) { box.innerHTML = tErr(r.error); return; }
    const s = r.summary || {}, t = s.tax;
    box.innerHTML = `${tKV([["팔 금액", `$${num(s.sell_usd, 2)}`], ["살 금액", `$${num(s.buy_usd, 2)}`, "", "수수료 포함"], ["새로 환전", `$${num(s.need_usd, 2)}`, "", `≈ ${tMoney(s.need_krw)} · 환전비 ${tMoney(s.fx_cost_krw)}`], ["양도세 추정", t ? tWon(t.tax_krw) : "-", t?.tax_krw ? "down" : "", t ? `이익 ${tMoney(t.gain_krw)} · 남은 공제 ${tMoney(t.deduction_left)}` : "평단을 넣으면 계산"]])}
      ${tCard("순서대로 (파는 것 먼저)", tList((r.rows || []).map((x) => tSym(x.symbol, x.name || x.symbol, `${x.held} → ${x.target_qty}주 · 목표 ${tPr(x.target_weight, 1)} · $${num(x.price, 2)}${x.gain_krw != null ? ` · 이익 ${tMoney(x.gain_krw)}` : ""}`, `<b class="${x.order_qty > 0 ? "up" : x.order_qty < 0 ? "down" : ""}">${x.order_qty > 0 ? "매수" : x.order_qty < 0 ? "매도" : "유지"} ${Math.abs(x.order_qty)}주</b><span class="t-sub num">$${num(x.amount_usd, 2)}</span>`)), "주문할 게 없어요"), '<button class="t-link" id="us-csv">CSV 받기</button>')}
      ${(r.missing || []).length ? `<div class="t-note warn">가격이 없어 뺀 종목: ${r.missing.map(esc).join(", ")} — ${esc(r.missing_hint || "")}</div>` : ""}
      ${(r.tax_warnings || []).map((w) => `<div class="t-note warn">${esc(koText(w))}</div>`).join("")}
      <div class="t-foot">환율 ${num(r.fx, 1)}원 (${esc(r.fx_source || "")}) · 가격 ${esc(r.price_source || "")} · 세금 기준 ${esc(r.tax_basis || "")} · ${esc(r.note || "")}</div>`;
    box.querySelector("#us-csv").onclick = () => { const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob(["﻿" + r.csv], { type: "text/csv" })); a.download = `us-orders-${new Date().toISOString().slice(0, 10)}.csv`; a.click(); };
  };
};

// ============================================================ 시장 · 일정 · 도구
TV.action = async (el) => {
  const L = await tLoad(el, "action", "오늘 할 일", "t2-act", () => Promise.all([api("/api/action-center"), tSafe(api("/api/briefing"))]));
  if (!L) return;
  const [a, b] = L.d, rk = a.risk || {};
  const n = (a.check || []).length + (a.events || []).length + (a.signal_changes || []).length;
  const KIND = { event: TI.cal, news: TI.news, disc: TI.doc, signal: ICONS.ai || TI.doc, risk: ICONS.risk || TI.doc };
  tPaint(L.root, "오늘 할 일", `
    ${tHero({ k: esc(a.as_of || ""), lv: rk.level === "bad" ? "bad" : n ? "warn" : "good", big: n ? `확인할 것 ${n}개` : "오늘은 조용해요", sub: n ? "위에서부터 하나씩 보면 돼요" : esc(koText(a.empty_hint || "관심·보유 종목에 큰 움직임·일정·신호 변화가 없어요")) })}
    ${(b?.items || []).length ? tCard("한 줄 브리핑", tList(b.items.map((x) => tRow(x.link || "#dashboard", `<span class="t-co"><span class="t-ev-ic">${KIND[x.kind] || TI.doc}</span><span class="t-co-t"><b class="t2-wrap">${esc(koText(x.text))}</b></span></span>`, "", { chev: true })))) : ""}
    ${(a.check || []).length ? tCard("먼저 볼 종목", tList(a.check.map((c) => `<div class="t2-item">${tSym(c.symbol, c.name, c.held ? "보유 중" : "관심", `<span class="t-tag ${c.score >= 3 ? "warn" : ""}">중요 ${c.score}</span>`, { chev: true })}<div class="t2-why">${(c.why || []).map((w) => esc(koText(w))).join(" · ")}</div></div>`))) : ""}
    ${(a.events || []).length ? tCard("다가오는 일정", tList(a.events.map((e) => tRow(e.symbol ? `#analysis/${encodeURIComponent(e.symbol)}` : "#calendar", `<span class="t-co">${e.symbol ? stockLogo(e.symbol, "", 36) : `<span class="t-ev-ic">${TI.cal}</span>`}<span class="t-co-t"><b class="t2-wrap">${esc(koText(e.title))}</b><span>${e.held ? "보유 종목" : ""}${e.estimated ? " · 추정일" : ""}</span></span></span>`, tDday(e)))), '<a href="#calendar">전체</a>') : ""}
    ${(a.signal_changes || []).length ? tCard("AI 판단이 바뀐 종목", tList(a.signal_changes.map((s) => tSym(s.symbol, s.name, `${esc(tActKo(s.from || s.prev))} → ${esc(tActKo(s.to || s.action))}`, tPill(s.to || s.action, s.prob_up))))) : ""}
    ${(a.disclosures || []).length ? tCard("새 공시", tList(a.disclosures.map((d) => `<div class="t2-item">${tSym(d.symbol, d.name, `${esc(tDate(d.date))}${d.held ? " · 보유" : ""}`, "", { chev: true })}<div class="t2-why"><b>${esc(d.title)}</b>${d.summary ? `<br>${esc(koText(d.summary))}` : ""}</div></div>`))) : ""}
    ${rk.headline ? tCard("위험", `${tStRow(rk.headline, (rk.rising || []).join(" · "), rk.level)}`, '<a href="#risk">자세히</a>') : ""}
    ${tFull("action", '<a class="t-btn ghost" href="#calendar">일정</a><a class="t-btn ghost" href="#news">뉴스 · 공시</a>')}`);
};
TV.map = async (el) => {
  const L = await tLoad(el, "map", "증시 지도", "t2-map", () => api("/api/market-map"));
  if (!L) return;
  const m = L.d, br = m.breadth || {}, ix = m.index || {};
  const tot = (br.up || 0) + (br.down || 0) + (br.flat || 0) || 1;
  const heat = (c) => { const a = Math.min(1, Math.abs(c || 0) / 0.06); return c > 0 ? `color-mix(in srgb, var(--up) ${Math.round(18 + a * 70)}%, var(--t-card))` : c < 0 ? `color-mix(in srgb, var(--down) ${Math.round(18 + a * 70)}%, var(--t-card))` : "var(--t-soft)"; };
  const secs = (m.sectors || []).slice().sort((a, b) => b.weight - a.weight);
  const draw = () => {
    const sec = T2.mapSec || "all";
    const tiles = (m.tiles || []).filter((t) => sec === "all" || t.sector === sec).sort((a, b) => b.value - a.value).slice(0, 60);
    const mx = Math.max(...tiles.map((t) => t.weight || 0), 0.0001);
    tPaint(L.root, "증시 지도", `
      ${tHero({ k: `${esc(m.date || "")} 일봉 · ${esc(ix.name || "지수")}`, lv: br.up > br.down ? "good" : "warn", big: `${esc(br.mood || "")} <span class="num ${tCls(ix.chg)}" style="font-size:20px">${ix.last ? num(ix.last, 2) : ""} ${tPct(ix.chg)}</span>`,
        body: `<div class="t2-breadth"><i style="flex:${br.up};background:var(--up)"></i><i style="flex:${br.flat};background:var(--dim)"></i><i style="flex:${br.down};background:var(--down)"></i></div><div class="t-sub">오른 종목 <b class="up">${br.up}</b> · 내린 종목 <b class="down">${br.down}</b> · 보합 ${br.flat} · 20일선 위 ${tPr(br.above20)}</div>` })}
      ${tChips("mp-sec", [["all", "전체"], ...secs.map((s) => [s.sector, `${s.sector} <span class="num ${tCls(s.chg)}">${tPct(s.chg, 1)}</span>`])], sec)}<div class="t-gap"></div>
      ${tCard("", `<div class="t2-heat">${tiles.map((t) => `<a href="#analysis/${encodeURIComponent(t.symbol)}" class="t2-tile" style="flex-grow:${Math.max(1, Math.round((t.weight / mx) * 12))};background:${heat(t.chg)}" title="${esc(t.name)} ${tPct(t.chg)}"><b>${esc(t.name)}</b><span class="num">${tPct(t.chg, 1)}</span></a>`).join("")}</div>
        <div class="t-foot">칸 크기 = 거래대금 · 색 = 그날 등락 (빨강 오름 · 파랑 내림)</div>`)}
      <div class="t-grid2">
        ${tCard("많이 오른", tList((m.gainers || []).map((r) => tSym(r.symbol, r.name, esc(r.sector || ""), tPrice(r.last, r.chg, r.symbol)))))}
        ${tCard("많이 내린", tList((m.losers || []).map((r) => tSym(r.symbol, r.name, esc(r.sector || ""), tPrice(r.last, r.chg, r.symbol)))))}
      </div>
      ${m.sector_note ? `<div class="t-note">${esc(koText(m.sector_note))}</div>` : ""}
      <div class="t-foot">${esc(koText(m.note || ""))}${m.n_stale ? ` · 오래된 시세 ${m.n_stale}종목 제외` : ""}</div>
      ${tFull("map", '<a class="t-btn ghost" href="#market">시장</a><a class="t-btn ghost" href="#graph">업종 · 관계도</a>')}`);
    tBind(L.root, "mp-sec", (k) => { T2.mapSec = k; draw(); });
  };
  draw();
};
TV.calendar = async (el) => {
  const L = await tLoad(el, "calendar", "일정", "t2-cal", () => api("/api/calendar"));
  if (!L) return;
  const c = L.d, vk = c.vkospi || {};
  const evs = (c.events || []).filter((e) => e.d_day >= 0).sort((a, b) => a.d_day - b.d_day);
  const past = (c.events || []).filter((e) => e.d_day < 0).sort((a, b) => b.d_day - a.d_day).slice(0, 8);
  const kinds = [...new Set(evs.map((e) => e.kind))];
  const draw = () => {
    const k = T2.calK || "all";
    const xs = evs.filter((e) => k === "all" || e.kind === k);
    const byDate = xs.reduce((acc, e) => { (acc[e.date] = acc[e.date] || []).push(e); return acc; }, {});
    tPaint(L.root, "일정", `
      ${tHero({ k: `오늘 ${esc(tDate(c.today))}`, lv: evs[0]?.d_day <= 1 ? "warn" : "idle", big: evs.length ? `다가오는 일정 ${evs.length}개` : "다가오는 일정이 없어요", sub: evs[0] ? `가장 가까운 것: ${esc(evs[0].title)} (${esc(evs[0].d_label)})` : "" })}
      ${tChips("cl-k", [["all", "전체"], ...kinds.map((x) => [x, EV_KO[x] || ({ export: "수출입", index_rebalance: "지수 변경", disclosure: "공시", ipo: "상장", split: "분할", meeting: "주총" }[x] || x)])], k)}<div class="t-gap"></div>
      ${Object.keys(byDate).length ? Object.entries(byDate).map(([d, es]) => tCard(`${esc(tDate(d))} <span class="t-h1s">${esc(es[0].d_label)}</span>`, tList(es.map(tEventRow)))).join("") : tCard("", tEmpty("이 종류의 일정이 없어요"))}
      ${vk.available ? tCard(`시장 불안 정도 (${tG("VKOSPI", "VKOSPI")})`, `${tKV([["지금", tN(vk.level, 1), vk.fear ? "down" : "", vk.proxy ? "대용 값" : ""], ["1년 중 위치", tPr(vk.percentile_1y), "", "높을수록 불안"], ["하루 예상 움직임", `±${tPr(vk.daily_move, 1)}`], ["한 달 예상 움직임", `±${tPr(vk.move_20d, 1)}`]])}${(vk.series || []).length > 2 ? tSpark(vk.series, { h: 60, w: 600, color: "var(--warn)" }) : ""}<div class="t-foot">${esc(vk.source || "")}</div>`) : ""}
      ${(c.risk || []).length ? tCard("일정 때문에 조심할 종목", tList(c.risk.filter((r) => r.level !== "low").slice(0, 10).map((r) => tSym(r.symbol, r.name, esc(koText(r.reason || (r.drivers || []).map((d) => d.label || d.name || "").filter(Boolean).join(" · ") || "")), tSt(r.level === "high" ? "bad" : "warn", r.level === "high" ? "높음" : "보통"))), "조심할 종목이 없어요")) : ""}
      ${(c.impact?.market_kr || []).length ? tCard("이런 날 시장은 얼마나 움직였나", tList(c.impact.market_kr.map((x) => `<div class="t-li"><span class="t-co-t"><b>${esc(EV_KO[x.kind] || x.kind)}</b><span>${tN(x.n)}번 · 가장 나빴던 날 ${tPct(x.worst, 1)}</span></span><span class="t-li-r"><b class="num">평소의 ${tN(x.vs_normal, 1)}배</b><span class="t-sub">보통 ±${tPr(x.abs_median, 1)}</span></span></div>`))) : ""}
      ${past.length ? tCard("지난 일정", tList(past.map(tEventRow))) : ""}
      ${c.bok?.rate?.message ? `<div class="t-foot">금리: ${esc(c.bok.rate.message)}</div>` : ""}
      <div class="t-foot">휴장일 출처 ${esc(c.calendar_source || "")} · ${esc(c.as_of || "")}</div>
      ${tFull("calendar", '<a class="t-btn ghost" href="#pead">실적 발표 전략</a><a class="t-btn ghost" href="#replay">그날 다시 보기</a>')}`);
    tBind(L.root, "cl-k", (x) => { T2.calK = x; draw(); });
  };
  draw();
};
TV.replay = async (el) => {
  const d0 = S.param && /^\d{4}-\d{2}-\d{2}$/.test(S.param) ? S.param : (S.replayDate || "");
  const L = await tLoad(el, "replay", "그날 다시 보기", "t2-rp", () => api(`/api/replay?date=${encodeURIComponent(d0)}`));
  if (!L) return;
  const r = L.d, b = r.breadth || {};
  S.replayDate = r.date;
  const mv = (x) => tSym(x.symbol, x.name, esc(x.symbol), tPrice(x.close ?? x.last, x.chg, x.symbol));
  tPaint(L.root, "그날 다시 보기", `
    <div class="t2-datebar"><button class="t-btn ghost" id="rp-prev">← ${esc(tDate(r.prev))}</button><input type="date" class="t-in" id="rp-d" value="${esc(r.date)}" aria-label="날짜"><button class="t-btn ghost" id="rp-next">${esc(tDate(r.next))} →</button></div>
    ${tHero({ k: `${esc(r.date)} (${esc(r.weekday)})`, lv: r.trading ? "idle" : "warn", big: r.trading ? (r.index ? `코스피 <span class="num ${tCls(r.index.chg)}">${tPct(r.index.chg)}</span>` : "거래일") : `휴장${r.holiday ? ` · ${esc(r.holiday)}` : ""}`,
      body: b.n ? `<div class="t2-breadth"><i style="flex:${b.up};background:var(--up)"></i><i style="flex:${b.n - b.up - b.down};background:var(--dim)"></i><i style="flex:${b.down};background:var(--down)"></i></div><div class="t-sub">오름 ${b.up} · 내림 ${b.down} / ${b.n}종목</div>` : "",
      foot: r.book ? `그때 모의 장부 ${tMoney(r.book.equity)} · ${r.book.n}종목 (${esc(r.book.at)})` : "" })}
    ${r.trading ? `<div class="t-grid2">${tCard("그날 많이 오른", tList((r.gainers || []).map(mv)))}${tCard("그날 많이 내린", tList((r.losers || []).map(mv)))}</div>` : ""}
    ${r.us?.trading ? tCard(`미국 장 (한국 시간 그날 밤) · 오름 ${r.us.breadth.up} · 내림 ${r.us.breadth.down}`, `<div class="t-grid2">${tList((r.us.gainers || []).map(mv))}${tList((r.us.losers || []).map(mv))}</div>`) : r.us?.holiday ? `<div class="t-note">미국 휴장 · ${esc(r.us.holiday)}</div>` : ""}
    ${tCard(`그날 AI 판단 ${r.ai_n || 0}건${r.ai_hit_later != null ? ` · 나중에 맞은 비율 ${tPr(r.ai_hit_later)}` : ""}`, tList((r.ai || []).map((a) => tSym(a.symbol, a.name, `확신 ${a.confidence}`, `${tPill(a.action, a.prob_up, { short: true })}${a.later ? `<span class="num t-sub ${tCls(a.later.ret)}">${tPct(a.later.ret, 1)}</span>${tOk(a.later.correct)}` : '<span class="t-sub">결과 전</span>'}`)), "그날 AI 판단이 없어요"))}
    ${tCard(`그날 뉴스 ${(r.news || []).length}건`, tList((r.news || []).map((n) => `<a class="t-li" href="${n.id ? `#n/${esc(n.id)}` : esc(n.url || "#")}"><span class="t-co-t"><b class="t2-wrap">${esc(n.title)}</b><span>${esc(n.source || "")} · ${esc(n.at || "")}${(n.symbols || []).length ? ` · ${n.symbols.map(esc).join(", ")}` : ""}</span></span></a>`), "그날 저장된 뉴스가 없어요"))}
    ${(r.events || []).length ? tCard("그날 일정", tList(r.events.map((e) => `<div class="t-li"><span class="t-co"><span class="t-ev-ic">${TI.cal}</span><span class="t-co-t"><b>${esc(e.title)}</b><span>${esc(e.market || "")}${e.estimated ? " · 추정" : ""}</span></span></span></div>`))) : ""}
    ${(r.alerts || []).length ? tCard("그날 알림", tList(r.alerts.map((a) => `<div class="t-li"><span class="t-co-t"><b>${esc(a.title)}</b><span>${esc(a.at)}</span></span></div>`))) : ""}
    <div class="t-foot">${esc(koText(r.note || ""))}</div>
    ${tFull(`replay/${esc(r.date)}`)}`);
  const go = (d) => { if (d) { S.replayDate = d; location.hash = `#replay/${d}`; } };
  L.root.querySelector("#rp-prev").onclick = () => go(r.prev);
  L.root.querySelector("#rp-next").onclick = () => go(r.next);
  L.root.querySelector("#rp-d").onchange = (e) => go(e.target.value);
};
TV.compare = async (el) => {
  let syms = (S.param ? decodeURIComponent(S.param).split(",") : []).filter(Boolean);
  if (syms.length < 2) {
    const star = (await api("/api/star").catch(() => ({ starred: [] }))).starred || [];
    const pool = [...syms, ...star, ...(S.data?.watchlist || []).map((w) => w.symbol)];
    syms = [...new Set(pool)].slice(0, Math.max(2, Math.min(3, pool.length)));
  }
  const L = await tLoad(el, "compare", "종목 비교", "t2-cmp", () => syms.length >= 2 ? api(`/api/compare?symbols=${encodeURIComponent(syms.join(","))}`) : Promise.resolve({ error: "비교할 종목을 2개 이상 고르세요" }));
  if (!L) return;
  const r = L.d, COLORS = ["#3182f6", "#f04452", "#12b886", "#f59f00"];
  const pick = `<div class="t2-cmp-pick">${syms.map((s, i) => `<span class="t-chipa" style="border-color:${COLORS[i]}">${stockLogo(s, "", 20)} ${esc((r.rows || []).find((x) => x.symbol === s)?.name || s)} <button class="t-link" data-rm="${esc(s)}" aria-label="빼기">✕</button></span>`).join("")}
    ${syms.length < 4 ? `<input id="cmp-add" class="t-in" placeholder="종목 코드 추가 (예: 000660, AAPL)" maxlength="12"><button class="t-btn" id="cmp-go">추가</button>` : ""}</div>`;
  const go = (xs) => { location.hash = `#compare/${encodeURIComponent(xs.join(","))}`; };
  const bind = () => {
    L.root.querySelectorAll("[data-rm]").forEach((b) => b.onclick = () => go(syms.filter((s) => s !== b.dataset.rm)));
    const add = () => { const v = (L.root.querySelector("#cmp-add")?.value || "").trim().toUpperCase(); if (v) go([...syms, v]); };
    const g = L.root.querySelector("#cmp-go"); if (g) g.onclick = add;
    const i = L.root.querySelector("#cmp-add"); if (i) i.onkeydown = (e) => { if (e.key === "Enter") add(); };
  };
  if (r.error) { tPaint(L.root, "종목 비교", `${pick}${tEmpty(r.error)}${tFull("compare")}`); bind(); return; }
  const rows = r.rows || [];
  const line = (label, f, cls) => `<tr><th>${label}</th>${rows.map((x) => `<td class="r num ${cls ? cls(x) : ""}">${f(x)}</td>`).join("")}</tr>`;
  const sg = (k) => (x) => tCls(x[k]);
  tPaint(L.root, "종목 비교", `${pick}
    ${tCard(`같은 날 100 으로 맞춘 흐름 <span class="t-h1s">${esc(r.from)} ~ ${esc(r.to)}</span>`, `<div id="cmp-chart" class="t2-chart"></div>`)}
    ${tCard("숫자로 비교", `<div class="t2-scroll"><table class="t-table t2-cmpt"><thead><tr><th></th>${rows.map((x, i) => `<th class="r"><a href="#analysis/${encodeURIComponent(x.symbol)}" style="color:${COLORS[i]}">${esc(x.name)}</a></th>`).join("")}</tr></thead><tbody>
      ${line("현재가", (x) => tPx(x.last, x.symbol))}${line("1개월", (x) => tPct(x.ret_1m, 1), sg("ret_1m"))}${line("3개월", (x) => tPct(x.ret_3m, 1), sg("ret_3m"))}${line("1년", (x) => tPct(x.ret_1y, 1), sg("ret_1y"))}
      ${line("1년 출렁임", (x) => tPr(x.vol))}${line("베타", (x) => tN(x.beta, 2))}${line("최대 낙폭", (x) => tPct(x.mdd, 0), () => "down")}
      ${line("AI 판단", (x) => x.ai ? tPill(x.ai.action, x.ai.prob_up, { short: true }) : "-")}${line("AI 과거 적중", (x) => x.n_scored ? `${tPr(x.hit)} <span class="t-sub">${x.n_scored}번</span>` : "-")}
      ${line("PER", (x) => x.per ? num(x.per, 1) : "-")}${line("PBR", (x) => x.pbr ? num(x.pbr, 2) : "-")}${line("배당", (x) => x.div_yield ? tPr(x.div_yield, 2) : "-")}</tbody></table></div>`)}
    ${tCard("얼마나 같이 움직이나", `<div class="t-list">${r.symbols.flatMap((a, i) => r.symbols.slice(i + 1).map((b) => { const v = r.corr[a][b]; return `<div class="t-li"><span class="t-co-t"><b>${esc(rows.find((x) => x.symbol === a)?.name || a)} · ${esc(rows.find((x) => x.symbol === b)?.name || b)}</b><span>${v >= 0.7 ? "거의 같이 움직여요 — 함께 사도 나눠 담는 효과가 작아요" : v >= 0.4 ? "어느 정도 같이 움직여요" : "따로 움직이는 편이에요"}</span></span><span class="t-li-r"><b class="num">${v.toFixed(2)}</b></span></div>`; })).join("")}</div>`)}
    ${(r.missing || []).length ? `<div class="t-note warn">일봉이 없어 뺀 종목: ${r.missing.map(esc).join(", ")}</div>` : ""}
    ${tFull(`compare/${encodeURIComponent(syms.join(","))}`)}`);
  bind();
  const ch = L.root.querySelector("#cmp-chart");
  if (ch && typeof lineChart === "function") lineChart(ch, r.symbols.map((s, i) => ({ data: r.series[s], color: COLORS[i], title: rows.find((x) => x.symbol === s)?.name || s })));
};
TV.graph = async (el) => {
  const L = await tLoad(el, "graph", "업종 · 종목 관계도", "t2-graph", () => Promise.all([api(`/api/graph${S.graphSym ? `?symbol=${encodeURIComponent(S.graphSym)}` : ""}`), tSafe(api("/api/agents"))]));
  if (!L) return;
  const [g, ag] = L.d, sv = ag?.sector_view || {}, mb = ag?.macro_brief || {}, nd = ag?.news_digest || {};
  const nodes = Object.entries(g.nodes || {}).sort((a, b) => (a[1].name || "").localeCompare(b[1].name || ""));
  const cen = S.graphSym ? g.nodes?.[S.graphSym] : null;
  tPaint(L.root, "업종 · 종목 관계도", `
    ${tHero({ k: g.stats ? `${tN(g.stats.nodes)}종목 · 관계 ${tN(g.stats.edges)}개` : "", lv: "idle", big: cen ? `${esc(cen.name)}와 같이 움직이는 종목` : "같이 움직이는 종목 묶음", sub: "가격이 비슷하게 오르내리거나(상관 0.55+) 뉴스에 같이 나온 종목을 선으로 이었어요" })}
    <div class="t2-filter"><select id="gr-sel" class="t-in" aria-label="중심 종목"><option value="">종목 골라서 중심으로 보기…</option>${nodes.map(([s, v]) => `<option value="${esc(s)}" ${s === S.graphSym ? "selected" : ""}>${esc(v.name || s)}</option>`).join("")}</select>${S.graphSym ? '<button class="t-btn ghost" id="gr-all">전체 보기</button>' : ""}</div>
    ${tCard("", `<div class="t2-graphbox">${typeof graphSvg === "function" ? graphSvg(g, 900, S.graphSym ? 520 : 600, S.graphSym) : ""}</div><div class="t-foot">선 굵기 = 관계 강도 · 점을 누르면 그 종목 중심으로</div>`)}
    ${S.graphSym ? tCard("연관 종목", tList((g.neighbors || []).map((n) => tSym(n.symbol, n.name, [n.corr ? `같이 움직임 ${n.corr}` : "", n.co_mention ? `뉴스 ${n.co_mention}번 같이` : "", n.same_sector ? "같은 업종" : ""].filter(Boolean).join(" · "), "", { chev: true })), "연결된 종목이 없어요")) : ""}
    ${(sv.table || []).length ? tCard("업종 강약 (지수보다 강한 순)", tList(sv.table.map((r) => `<div class="t2-item"><div class="t-li"><span class="t-co-t"><b>${r.rank}. ${esc(r.sector)}</b><span>${r.n}종목 · 20일선 위 ${tPr(r.breadth)}</span></span><span class="t-li-r"><b class="num ${tCls(r.rs_20)}">${tPct(r.rs_20, 1)}</b><span class="t-sub">5일 ${tPct(r.ret_5, 1)}</span></span></div>${(r.leaders || []).length ? `<div class="t2-why">앞선 종목: ${r.leaders.map((m) => `<a href="#analysis/${encodeURIComponent(m.symbol)}">${esc(m.name)}</a> <span class="${tCls(m.ret_5)}">${tPct(m.ret_5, 1)}</span>`).join(" · ")}</div>` : ""}</div>`))) : tCard("업종 강약", tEmpty("업종 정보가 아직 없어요", "업종(WICS) 수집이 되면 보여요", '<button class="t-btn" id="gr-build">지금 만들기</button>'))}
    <div class="t-grid2">${mb.view ? tCard("경제 흐름 요약", `<div class="t-text"><p>${esc(koText(mb.view))}</p></div>${(mb.watch || []).length ? `<div class="t-sub">오늘 볼 것: ${mb.watch.map(esc).join(" · ")}</div>` : ""}`) : ""}${nd.summary ? tCard("뉴스 요약", `<div class="t-text"><p>${esc(koText(nd.summary))}</p></div>`) : ""}</div>
    ${tFull("graph", '<a class="t-btn ghost" href="#map">증시 지도</a>')}`);
  L.root.querySelector("#gr-sel").onchange = (e) => { S.graphSym = e.target.value || null; TV.graph(el); };
  const all = L.root.querySelector("#gr-all"); if (all) all.onclick = () => { S.graphSym = null; TV.graph(el); };
  const bld = L.root.querySelector("#gr-build"); if (bld) bld.onclick = async () => { bld.disabled = true; bld.textContent = "만드는 중…"; const x = await runAction("graph"); toast({ title: x.error ? "만들지 못했어요" : "관계도를 새로 만들었어요", body: x.error || "", level: x.error ? "warn" : "good" }); TV.graph(el); };
  L.root.querySelectorAll(".t2-graphbox [data-sym]").forEach((n) => n.addEventListener("click", () => { S.graphSym = n.dataset.sym; TV.graph(el); }));
};
TV.chat = async (el) => {
  el.innerHTML = `<div class="ts t2 t2-chat">${tHead("AI 에게 묻기")}<div class="t2-chatbox">${chatShell("chat-full", true)}</div></div>`;
  const root = el.querySelector(".ts");
  tBindHead(root);
  mountChat(root.querySelector("#chat-full"));
};
TV.newslist = async (el) => {
  const q = T2.nq || "";
  const L = await tLoad(el, "newslist", "뉴스 원문 검색", "t2-nl", () => q ? api(`/api/news-search?q=${encodeURIComponent(q)}&days=${T2.nd || 30}`) : api("/api/t/feed?tab=news&region=all&topic=all"));
  if (!L) return;
  const items = q ? (L.d.results || []) : (L.d.items || []);
  tPaint(L.root, "뉴스 원문 검색", `
    <div class="t2-filter"><input id="nl-q" class="t-in" placeholder="종목·단어로 찾기 (예: 삼성, HBM, 금리)" value="${esc(q)}" aria-label="검색어">${tChips("nl-d", [["7", "1주"], ["30", "1개월"], ["90", "3개월"]], String(T2.nd || 30))}</div>
    ${tCard(q ? `'${esc(q)}' 검색 결과 ${items.length}건` : "최근 뉴스", tList(items.map((n) => {
      const sym = (n.symbols || [])[0];
      return `<a class="t-li" href="#n/${esc(n.id)}">${sym ? stockLogo(sym, (n.names || [])[0] || "", 40) : `<span class="t-ev-ic">${TI.news}</span>`}<span class="t-co-t"><b class="t2-wrap">${esc(n.title)}</b><span>${esc(n.source || "")} · ${esc(n.time || n.ago || "")}${(n.names || n.symbols || []).length ? ` · ${esc((n.names || n.symbols).slice(0, 3).join(", "))}` : ""}</span></span>${n.tone != null ? `<span class="t-li-r"><span class="t-tone ${n.tone > 0.2 ? "up" : n.tone < -0.2 ? "down" : ""}">${n.tone > 0.2 ? "좋음" : n.tone < -0.2 ? "나쁨" : "보통"}</span></span>` : ""}</a>`;
    }), q ? "찾는 뉴스가 없어요" : "최근 뉴스가 없어요", "뉴스는 24시간 운영(./run.sh)이 켜져 있을 때 모여요"))}
    ${tFull("newslist", '<a class="t-btn ghost" href="#news">뉴스 · 공시</a>')}`);
  const inp = L.root.querySelector("#nl-q");
  inp.onkeydown = (e) => { if (e.key === "Enter") { T2.nq = inp.value.trim().slice(0, 100); TV.newslist(el); } };
  tBind(L.root, "nl-d", (k) => { T2.nd = +k; if (T2.nq) TV.newslist(el); else L.root.querySelectorAll("#nl-d button").forEach((b) => b.classList.toggle("on", b.dataset.k === k)); });
};

// ============================================================ 신뢰 · 시스템 · 나
// 버튼이 많은 예전 카드(키 진단·연결 점검·설정 등)는 기능 그대로 토스식 카드 안에 넣는다
const tLegacy = (id) => `<div class="tl t2-legacy" id="${id}"></div>`;
TV.datahealth = async (el) => {
  const L = await tLoad(el, "datahealth", "데이터 상태", "t2-dh", () => Promise.all([api("/api/data-health"), tSafe(api("/api/sentinel")), tSafe(api("/api/governance"))]));
  if (!L) return;
  const [h, sn, g] = L.d, blocked = h.trading === "BLOCKED";
  tPaint(L.root, "데이터 상태", `
    ${tHero({ k: `데이터 믿을 만한 정도 · ${esc(h.as_of || "")}`, lv: blocked ? "bad" : h.overall >= 80 ? "good" : "warn", big: `${tN(h.overall, 0)}점 · ${blocked ? "새로 사지 않아요" : "거래해도 돼요"}`, sub: esc(koText(h.block_reason || h.price_delay?.detail || "")),
      body: tProg((h.overall || 0) / 100, blocked ? "bad" : h.overall >= 80 ? "good" : "warn", "데이터 점수"), foot: "주가 점수 50% 미만 · 장중 실시간 가격 15분 이상 늦음 → 신규 매수를 막아요 (팔기·위험 줄이기는 가능)" })}
    <div class="t-pro" style="margin-top:0"><button class="t-btn ghost" id="dh-re">다시 점검</button><button class="t-btn ghost" data-logo-up="">종목 로고 직접 넣기</button></div><div class="t-gap"></div>
    ${tCard("분야별", tList((h.rows || []).map((r) => `<div class="t2-item"><div class="t-li"><span class="t-co"><span class="t-dot-ic">${lvDot(tLv(r.status))}</span><span class="t-co-t"><b>${esc(r.name)}</b><span class="t2-wrap">${esc(koText(r.detail || ""))}</span></span></span><span class="t-li-r t2-w">${r.pct == null ? '<span class="t-sub">해당 없음</span>' : `${tProg(r.pct / 100, tLv(r.status))}<b class="num">${r.pct}%</b>`}</span></div><div class="t2-why">출처: ${esc(r.source || "-")}</div></div>`)))}
    ${sn && !sn.error ? tCard("5분마다 자동 감시", tList((sn.checks || []).map((c) => tStRow(c.title, c.detail, c.status)))) : ""}
    ${g && !g.error && (g.failmode || []).length ? tCard("장애가 나도 돈은 이렇게 지켜요", tList(g.failmode.map((m) => `<div class="t-li"><span class="t-co-t"><b>${esc(m.part)} 이 멈추면</b><span class="t2-wrap">가진 주식 ${esc(m.positions)} · 새로 사기 ${esc(m.new_buys)} · 팔기 ${esc(m.sells)}</span></span></div>`))) : ""}
    ${tLegacy("dh-legacy")}
    ${tFull("datahealth", '<a class="t-btn ghost" href="#readiness">매매해도 되나</a><a class="t-btn ghost" href="#truth">데이터 사실 확인</a>')}`);
  L.root.querySelector("#dh-re").onclick = async (e) => { e.target.disabled = true; e.target.textContent = "점검 중…"; await api("/api/data-health?refresh=1").catch(() => null); TV.datahealth(el); };
  const box = L.root.querySelector("#dh-legacy");
  collectCard(box); keysCard(box); netCard(box); conflictCard(box);
};
TV.readiness = async (el) => {
  const L = await tLoad(el, "readiness", "매매해도 되나", "t2-rd", () => Promise.all([api("/api/readiness"), tSafe(api("/api/pipeline"))]));
  if (!L) return;
  const [r, p] = L.d, ok = r.status === "READY";
  const reds = (r.checks || []).filter((c) => tLv(c.status) === "bad").length;
  const fr = Object.values(r.freshness?.items || {});
  tPaint(L.root, "매매해도 되나", `
    ${tHero({ k: `7가지 점검 · ${esc(r.as_of || "")} · ${esc(PF_KO[r.mode] || r.mode || "")}`, lv: ok ? "good" : "bad", big: ok ? "매매해도 돼요" : `아직 안 돼요 — 막힌 곳 ${reds}개`, sub: r.real_money ? "실제 돈 장부 기준" : "실제 돈은 쓰지 않는 장부 기준이에요",
      body: (r.blockers || []).length ? `<ol class="t-ol">${r.blockers.map((b, i) => `<li><span class="t-num">${i + 1}</span>${esc(koText(b))}</li>`).join("")}</ol>` : "" })}
    ${tCard("점검 항목", tList((r.checks || []).map((c) => `<div class="t2-item">${tStRow(c.title, c.detail, c.status)}${(c.items || []).length > 1 ? `<div class="t2-why">${c.items.map((x) => esc(koText(x))).join(" · ")}</div>` : ""}</div>`)))}
    ${fr.length ? tCard("자료가 얼마나 새것인가", tList(fr.map((f) => tStRow(f.name, `${f.label || "-"}${f.source ? ` · ${f.source}` : ""}`, { fresh: "good", stale: "warn", old: "bad", none: "idle" }[f.status] || "idle", { label: f.age }))), `<span class="t-sub">${esc(r.freshness?.now_label || "")}</span>`) : ""}
    ${(r.history || []).length ? tCard("최근 점검 기록", `<div class="t2-hist">${r.history.slice(-40).map((x) => `<i class="${x.status === "READY" ? "ok" : "no"}" title="${esc(tDate(x.at))} ${x.status === "READY" ? "가능" : "불가"}${(x.reds || []).length ? ` · ${x.reds.join(", ")}` : ""}"></i>`).join("")}</div><div class="t-foot">칸 하나 = 점검 한 번 · 회색 = 가능 · 빨강 = 불가</div>`) : ""}
    ${p && !p.error && (p.sources || []).length ? tCard("자료 받아오는 곳", tList(p.sources.map((s) => tStRow(s.source, s.last_error ? `실패: ${s.last_error}` : s.last_ok ? `마지막 성공 ${tAgo(s.last_ok)}` : "아직 안 돌았어요", s.status, { label: s.runs ? `${s.runs - s.fails}/${s.runs} 성공` : "기록 없음" })))) : ""}
    ${tFull("readiness", '<a class="t-btn ghost" href="#datahealth">데이터 상태</a><a class="t-btn ghost" href="#control">실시간 감시실</a>')}`);
};
TV.truth = async (el) => {
  const L = await tLoad(el, "truth", "데이터 사실 확인", "t2-tr", () => Promise.all([api("/api/truth"), tSafe(api("/api/checklist"))]));
  if (!L) return;
  const [t, c] = L.d;
  const SECK = { clock: "장 시계", data: "데이터", ai: "AI 판단", orders: "주문", portfolio: "포트폴리오", risk: "위험" };
  const AREA = { DATA: "데이터", MARKET: "시장", EVENT: "일정", AI: "AI", MODEL: "모델", PORTFOLIO: "포트폴리오", TRADING: "매매", SAFETY: "안전", OPERATIONS: "운영", UX: "화면" };
  tPaint(L.root, "데이터 사실 확인", `
    ${tHero({ k: esc(t.as_of || ""), lv: tLv(t.status), big: (t.bad || []).length ? `틀린 곳 ${(t.bad || []).length}개` : "화면 숫자가 원본과 맞아요", sub: "화면에 보이는 숫자를 DB 원본과 다시 맞춰 봤어요",
      body: (t.bad || []).length ? `<ol class="t-ol">${t.bad.map((b, i) => `<li><span class="t-num">${i + 1}</span>${esc(koText(b))}</li>`).join("")}</ol>` : "" })}
    ${(t.sections || []).map((s) => tCard(`${esc(SECK[s.key] || koText(s.title))} ${tSt(s.status, s.n_bad ? `문제 ${s.n_bad}` : s.n_warn ? `주의 ${s.n_warn}` : "정상")}`, `<details class="t2-det"${s.n_bad || s.n_warn ? " open" : ""}><summary>${(s.checks || []).length}개 확인</summary>${tList((s.checks || []).map((x) => tStRow(x.title || x.name, x.detail, x.status)))}</details>`)).join("")}
    ${c && !c.error && c.total ? tCard(`구현 점검표 · ${c.total.n}개 중 ${c.total.done}개 완료`, `${tProg(c.total.done / c.total.n, "good")}<div class="t-gap"></div>${tList(Object.entries(c.areas || {}).map(([k, a]) => `<div class="t-li"><span class="t-co-t"><b>${esc(AREA[k] || k)}</b><span>실제 동작 ${a.live} · 구현 ${a.impl}${a.setup ? ` · 설정 필요 ${a.setup}` : ""}</span></span><span class="t-li-r">${a.bad ? tSt("bad", `문제 ${a.bad}`) : a.warn ? tSt("warn", `주의 ${a.warn}`) : tSt("good", `${a.n}개`)}</span></div>`))}`) : ""}
    ${tFull("truth", '<a class="t-btn ghost" href="#datahealth">데이터 상태</a>')}`);
};
TV.validation = async (el) => {
  const L = await tLoad(el, "validation", "실전 검증 진행표", "t2-val", () => Promise.all([api("/api/validation"), tSafe(api("/api/exec-costs"))]));
  if (!L) return;
  const [v, ec] = L.d;
  tPaint(L.root, "실전 검증 진행표", `
    ${tHero({ k: esc(v.as_of || ""), lv: v.overall >= 0.8 ? "good" : "warn", big: `${tPr(v.overall)} 진행`, sub: esc(koText(v.headline || "")), body: tProg(v.overall, v.overall >= 0.8 ? "good" : "warn", "전체 진행") })}
    ${(v.items || []).map((x) => tCard(`${esc(koText(x.title))} ${tSt(x.status, { setup: "설정 필요", running: "진행 중", done: "완료", ok: "완료" }[x.status])}`, `${tProg(x.progress, tLv(x.status))}<div class="t-sub" style="margin:8px 0">${esc(koText(x.detail || ""))}</div>
      ${(x.steps || []).length ? `<ol class="t-ol">${x.steps.map((s, i) => `<li><span class="t-num">${i + 1}</span>${esc(koText(s))}</li>`).join("")}</ol>` : ""}${x.next ? `<div class="t-note">다음: <span class="mono">${esc(x.next)}</span></div>` : ""}`)).join("")}
    ${ec && !ec.error ? tCard("실제 비용 vs 가정", `${tKV([["가정 왕복 비용", `${tN(ec.assumed?.roundtrip_bps, 1)}bp`], ...(ec.rows || []).map((r) => [`${PF_KO[r.mode] || r.mode} 실측`, `${tN(r.roundtrip_bps, 1)}bp`, r.roundtrip_bps > (ec.assumed?.roundtrip_bps || 0) ? "down" : "", `${tN(r.n_filled)}건 체결${r.measured ? "" : " · 모의"}`])])}<div class="t-sub">${esc(koText(ec.verdict || ""))}</div>`) : ""}
    ${tFull("validation", '<a class="t-btn ghost" href="#execution">체결 품질</a>')}`);
};
TV.control = async (el) => {
  const L = await tLoad(el, "control", "실시간 감시실", "t2-ctl", () => Promise.all([api("/api/control"), tSafe(api("/api/ladder"))]));
  if (!L) return;
  const [c, ld] = L.d, cnt = c.counts || {};
  const STAGE = { backtest: "과거 시험", shadow: "그림자 채점", paper: "모의 투자", live_small: "소액 실전", live: "실전" };
  const draw = () => {
    const tab = T2.ctlTab;
    let body = "";
    if (tab === "stage") body = tList((c.stages || []).map((s) => tStRow(s.label, `${s.detail || ""}${s.last ? ` · 마지막 ${tAgo(s.last)}` : ""}`, { ok: "good", run: "good", wait: "idle", fail: "bad", warn: "warn" }[s.status] || s.status, { label: { ok: "정상", run: "도는 중", wait: "대기", fail: "실패", warn: "주의" }[s.status] })));
    else if (tab === "jobs") body = tList((c.jobs || []).map((j) => tStRow(j.label, `${j.cadence || ""}${j.last ? ` · 마지막 ${tAgo(j.last)}` : " · 아직 안 돎"}${j.error ? ` · ${j.error}` : ""}`, j.ok === true ? "good" : j.ok === false ? "bad" : "idle", { label: j.runs ? `${j.runs - j.fails}/${j.runs}` : "-" })));
    else body = tList((c.feed || []).map((f) => tSym(f.symbol, f.name, `${esc(tDate(f.as_of))} · 예상 ${tPct(f.expected, 1)} (${f.horizon}일)`, `${tPill(f.action, f.prob_up, { short: true })}${tOk(f.correct)}`)));
    tPaint(L.root, "실시간 감시실", `
      ${tKV([["AI 예측 기록", `${tN(cnt.predictions)}건`], ["결과 기다림", `${tN(cnt.pending)}건`], ["24시간 알림", `${tN(cnt.alerts_24h)}건`], ["돌고 있는 작업", `${(c.jobs || []).filter((j) => j.runs).length} / ${(c.jobs || []).length}`]])}
      ${ld && !ld.error ? tCard(`단계 사다리 · 지금 ${esc(STAGE[ld.stage] || ld.stage)}`, `<div class="t2-steps">${Object.keys(STAGE).map((k, i, a) => `<span class="${k === ld.stage ? "on" : a.indexOf(ld.stage) > i ? "done" : ""}">${STAGE[k]}</span>`).join("")}</div>
        ${tList((ld.conditions || []).map((x) => tStRow(x.label, `지금 ${x.value} · 필요 ${x.need}`, x.status, { label: x.status === "pass" ? "통과" : "미달" })))}
        ${ld.would ? `<div class="t-note">다음 점검에서 ${ld.would_kind === "promote" ? "올라갈" : "내려갈"} 단계: ${esc(STAGE[ld.would] || ld.would)}</div>` : ""}`) : ""}
      ${tTabs("ct-tab", [["stage", "단계"], ["jobs", `작업 ${(c.jobs || []).length}`], ["feed", "최근 AI 판단"]], tab, "t-tabs-line")}
      ${tCard("", body)}
      ${tFull("control", '<a class="t-btn ghost" href="#readiness">매매해도 되나</a><a class="t-btn ghost" href="#ops">운영 기록</a>')}`);
    tBind(L.root, "ct-tab", (k) => { T2.ctlTab = k; draw(); });
  };
  draw();
};
TV.safety = async (el) => {
  const L = await tLoad(el, "safety", "안전 센터", "t2-safe", () => Promise.all([api("/api/guardian"), tSafe(api("/api/an/stress")), tSafe(api("/api/an/confidence")), tSafe(api("/api/an/champion"))]));
  if (!L) return;
  const [g, st, cf, ch] = L.d, halted = g.state === "HALTED";
  tPaint(L.root, "안전 센터", `
    ${tHero({ k: `자동 감시 ${(g.conditions || []).length}가지 · ${esc(tAgo(g.checked_at))} 점검`, lv: halted ? "bad" : g.state === "WARN" ? "warn" : "good", big: halted ? "새 주문을 멈췄어요" : "정상 운영 중", sub: halted ? "아래 '문제' 조건 때문에 자동으로 멈췄어요. 원인을 확인하고 해제하세요" : "조건이 나빠지면 자동으로 새 주문을 멈춰요" })}
    <div class="t-pro" style="margin-top:0"><button class="t-btn ghost" id="sf-check">지금 다시 점검</button>${halted ? '<button class="t-btn primary" id="sf-release">정지 해제</button>' : ""}</div><div class="t-gap"></div>
    ${tCard("감시 조건", tList((g.conditions || []).map((c) => tStRow(c.label, c.detail, c.status === "na" ? "idle" : c.status, { label: { ok: "정상", warn: "주의", bad: "문제", critical: "심각", na: "해당 없음" }[c.status] }))))}
    ${st && !st.error && (st.scenarios || []).length ? tCard(`과거 위기가 오면 (${tMoney(st.equity)} 기준)`, tList(st.scenarios.map((s) => `<div class="t-li"><span class="t-co-t"><b>${esc(s.label)}</b><span>${esc(s.period || "")} · 코스피 ${tPct(s.kospi, 0)}</span></span><span class="t-li-r"><b class="num down">${tMoney(s.loss_krw)}</b><span class="t-sub">${tPct(s.portfolio, 1)}</span></span></div>`)) + `<div class="t-foot">${esc(koText(st.note || ""))}</div>`) : ""}
    ${cf && !cf.error ? tCard(`데이터 믿을 만한 정도 · ${tN(cf.score)}점 (${esc(cf.label || "")})`, tList((cf.items || []).map((x) => tStRow(x.label, `${x.last ? `마지막 ${x.last.slice(0, 10)}` : "기록 없음"}${x.age != null ? ` · ${x.age}${x.unit || ""} 지남` : ""}${x.hint ? ` · ${x.hint}` : ""}`, x.status === "ok" ? "good" : x.status === "stale" ? "warn" : x.status)))) : ""}
    ${ch && !ch.error ? tCard("모델 자동 되돌리기", (ch.events || []).length ? tList(ch.events.map((e) => tStRow(e.title || e.action, e.detail || e.reason, e.status || "warn"))) : `<div class="t-sub">${ch.status === "none" ? "사용 중인 모델이 없어 되돌릴 일이 없어요" : "되돌린 기록이 없어요"}</div>`) : ""}
    ${tFull("safety", '<a class="t-btn ghost" href="#risk">위험 관리</a><a class="t-btn ghost" href="#budget">투자 한도</a>')}`);
  L.root.querySelector("#sf-check").onclick = async (e) => { e.target.disabled = true; e.target.textContent = "점검 중…"; await runAction("guardian"); TV.safety(el); };
  const rb = L.root.querySelector("#sf-release");
  if (rb) rb.onclick = async () => {
    if (!confirm("정지를 해제할까요?\n원인을 확인하셨나요? 조건이 여전히 '심각'이면 다음 점검에서 다시 멈춥니다.")) return;
    await post("/api/killswitch", { on: false, reason: "안전 센터에서 해제" }); if (typeof refresh === "function") await refresh(); TV.safety(el);
  };
};
TV.governance = async (el) => {
  const L = await tLoad(el, "governance", "규제 · 보안 · 라이선스", "t2-gov", () => api("/api/governance"));
  if (!L) return;
  const g = L.d, sv = g.service || {}, lc = g.licenses || {}, se = g.security || {};
  const RISK = { high: ["bad", "높음"], medium: ["warn", "보통"], low: ["good", "낮음"] };
  tPaint(L.root, "규제 · 보안 · 라이선스", `
    ${tHero({ k: "지금 쓰는 방식", lv: "idle", big: esc(sv.current_label || "-"), sub: esc(koText(sv.note || "")) })}
    ${tCard("다른 사람에게 제공하면 필요한 것", tList((sv.levels || []).map((l) => `<div class="t2-item"><div class="t-li"><span class="t-co-t"><b>${esc(l.name)}</b><span class="t2-wrap">${esc(l.desc)}</span></span>${l.key === sv.current ? '<span class="t-li-r"><span class="t-st st-good">지금</span></span>' : ""}</div><div class="t2-why">${esc(l.legal)}</div></div>`)))}
    ${tCard(`보안 점검 · ${tN(se.score)}점`, `${tProg((se.score || 0) / 100, se.score >= 80 ? "good" : "warn")}<div class="t-gap"></div>${tList((se.items || []).map((x) => tStRow(x.item, x.detail, x.status === "partial" ? "warn" : x.status, { label: { ok: "됨", partial: "일부", missing: "없음", bad: "없음" }[x.status] })))}<div class="t-foot">${esc(koText(se.note || ""))}</div>`)}
    ${tCard("데이터 사용 조건", tList((lc.rows || []).map((r) => `<div class="t2-item"><div class="t-li"><span class="t-co-t"><b>${esc(r.source)}</b><span class="t2-wrap">${esc(r.used)}</span></span><span class="t-li-r">${tSt(...(RISK[r.risk] || ["idle", r.risk]))}</span></div><div class="t2-why">개인: ${esc(r.personal)}<br>상업: ${esc(r.commercial)}</div></div>`)) + `<div class="t-foot">${esc(koText(lc.note || ""))}</div>`)}
    ${tCard("최근 기록 (누가 무엇을)", tList((g.audit || []).slice(0, 15).map((a) => `<div class="t-li"><span class="t-co-t"><b>${esc(a.action)}${a.detail ? ` · ${esc(a.detail)}` : ""}</b><span>${esc(a.as_of || "")} · ${esc(a.actor || "")}</span></span></div>`)))}
    ${tFull("governance")}`);
};
TV.server = async (el) => {
  const L = await tLoad(el, "server", "서버 · DB", "t2-srv", () => Promise.all([api("/api/server"), tSafe(api("/api/db"))]));
  if (!L) return;
  const [s, db] = L.d, sc = s.scheduler || {};
  const LBL = { price_bars: "주가 일봉", consensus_signals: "AI 판단", llm_calls: "LLM 호출", news_articles: "뉴스", portfolio_snapshots: "장부 기록", job_runs: "작업 기록", instruments: "종목" };
  tPaint(L.root, "서버 · DB", `
    ${tHero({ k: `${esc(s.os || "")} · 파이썬 ${esc(s.python || "")}`, lv: s.db?.ok && sc.alive ? "good" : "warn", big: `켜진 지 ${s.uptime_min >= 60 ? `${Math.floor(s.uptime_min / 60)}시간 ${Math.round(s.uptime_min % 60)}분` : `${Math.round(s.uptime_min)}분`}`, sub: sc.alive ? "자동 작업(스케줄러)이 돌고 있어요" : "자동 작업(스케줄러)이 꺼져 있어요 — ./run.sh 로 켜면 24시간 수집·판단이 돌아요" })}
    ${tKV([["DB", s.db?.ok ? "정상" : "문제", s.db?.ok ? "" : "down", `${tN(s.db?.latency_ms, 1)}ms · ${tN((s.db?.size_bytes || 0) / 1048576, 0)}MB`], ["디스크 남음", `${tN(s.disk?.free_gb, 1)}GB`, "", `전체 ${tN(s.disk?.total_gb, 0)}GB`], ["데이터 점수", `${tN(s.data_confidence)}점`], ["안전 감시", s.guardian?.state === "HALTED" ? "멈춤" : "정상", s.guardian?.state === "HALTED" ? "down" : ""]])}
    ${(sc.failing || []).length ? tCard("실패 중인 작업", tList(sc.failing.map((j) => tStRow(j.job || j, j.error || "", "bad")))) : ""}
    ${db && !db.error ? tCard("저장된 기록", `${tList(Object.entries(db.tables || {}).map(([k, n]) => `<div class="t-li"><span class="t-co-t"><b>${esc(LBL[k] || k)}</b></span><span class="t-li-r"><b class="num">${tN(n)}</b></span></div>`))}
      <div class="t-note">정리하면 지울 수 있는 것 ${tN(db.total)}줄 — ${esc(db.kept || "")}</div><div class="t-pro"><button class="t-btn ghost" id="sv-clean">오래된 로그 정리</button><button class="t-btn ghost" id="sv-backup">지금 백업</button></div>`) : ""}
    <div class="t-foot">모드 ${esc(PF_KO[s.mode] || s.mode)} · 증권사 ${esc(s.broker === "none" ? "연결 안 됨" : s.broker)} · ${esc(s.kis_env === "demo" ? "KIS 모의 서버" : s.kis_env || "")}</div>
    ${tFull("server", '<a class="t-btn ghost" href="#ops">운영 기록</a>')}`);
  const act = (id, name, ask) => { const b = L.root.querySelector(id); if (b) b.onclick = async () => { if (ask && !confirm(ask)) return; b.disabled = true; b.textContent = "하는 중…"; const r = await runAction(name); toast({ title: r.error ? "실패" : "완료", body: r.error || "", level: r.error ? "warn" : "good" }); TV.server(el); }; };
  act("#sv-clean", "db_clean", "오래된 로그만 지워요 (주문·판단 기록은 그대로). 진행할까요?"); act("#sv-backup", "backup");
};
TV.trades = async (el) => {
  const d = S.data || {};
  el.innerHTML = `<div class="ts t2 t2-trd"></div>`;
  const root = el.querySelector(".ts");
  const tr = d.trades || [], rl = d.risk_log || [];
  tPaint(root, "거래 · 위험 기록", `
    ${tCard(`체결 ${tr.length}건`, tList(tr.map((x) => tSym(x.symbol, x.name, `${esc(tAgo(x.ts))} · ${esc(PF_KO[x.mode] || x.mode)}${x.reason ? ` · ${esc(koText(x.reason))}` : ""}`, `<b class="${x.side === "buy" ? "up" : "down"}">${x.side === "buy" ? "매수" : "매도"} ${num(x.qty)}주</b><span class="t-sub num">${tPx(x.price, x.symbol)} · 비용 ${tWon(x.fee)}</span>`)), "체결 기록이 없어요"))}
    ${tCard(`위험 점검에 걸린 주문 ${rl.length}건`, tList(rl.map((x) => `<div class="t2-item">${tSym(x.symbol, x.name, `${esc(tAgo(x.ts))} · ${esc(PF_KO[x.mode] || x.mode)}`, tSt("bad", x.status === "rejected" ? "거부" : x.status))}<div class="t2-why">${(x.reasons || []).map((r) => esc(koText(r))).join(" · ")}</div></div>`), "막힌 주문이 없어요"))}
    ${tFull("trades", '<a class="t-btn ghost" href="#orders">주문 내역</a><a class="t-btn ghost" href="#notrade">거래 안 한 이유</a>')}`);
};
TV.ops = async (el) => {
  const L = await tLoad(el, "ops", "운영 · 작업 기록", "t2-ops", () => api("/api/ops"));
  if (!L) return;
  const o = L.d, dq = Object.entries(o.data_quality || {}).filter(([, v]) => (v.warnings || []).length);
  const llm = o.llm || {};
  tPaint(L.root, "운영 · 작업 기록", `
    ${tKV([["오늘 LLM 비용", `$${tN(llm.today_cost, 2)}`, "", `한 달 한도 $${tN(o.llm_budget_usd, 0)}`], ["최근 작업", `${(o.jobs || []).length}개`], ["데이터 경고 종목", `${dq.length}개`, dq.length ? "down" : ""]])}
    ${tCard("최근 작업", tList((o.jobs || []).slice(0, 40).map((j) => tStRow(j.label || j.job, `${j.at || j.started_at ? tAgo(j.at || j.started_at) : ""}${j.error ? ` · ${j.error}` : j.detail ? ` · ${j.detail}` : ""}`, j.ok === false || j.status === "failed" ? "bad" : j.ok === true || j.status === "ok" ? "good" : "idle", { label: j.duration_s != null ? `${tN(j.duration_s, 1)}초` : undefined })), "작업 기록이 없어요", "./run.sh 로 24시간 운영을 켜면 쌓여요"))}
    ${(llm.by_model || []).length ? tCard("LLM 사용량", tList(llm.by_model.map((m) => `<div class="t-li"><span class="t-co-t"><b>${esc(m.model)}</b><span>${tN(m.calls)}번</span></span><span class="t-li-r"><b class="num">$${tN(m.cost, 3)}</b></span></div>`))) : ""}
    ${dq.length ? tCard("데이터 이상 (주가)", tList(dq.slice(0, 30).map(([sym, v]) => `<div class="t2-item">${tSym(sym, (S.data?.all_symbols || []).find((x) => x.symbol === sym)?.name || sym, `${tN(v.rows_out)}일치`, "")}<div class="t2-why">${v.warnings.map((w) => esc(koText(w))).join("<br>")}</div></div>`))) : ""}
    ${tFull("ops", '<a class="t-btn ghost" href="#server">서버 · DB</a>')}`);
};
TV.settings = async (el) => {
  el.innerHTML = `<div class="ts t2 t2-set"></div>`;
  const root = el.querySelector(".ts");
  const theme = document.documentElement.dataset.theme;
  tPaint(root, "설정", `
    ${tCard("", `<div class="t-list">
      <div class="t-li"><span class="t-co-t"><b>화면 모드</b><span>쉬운 화면 = 토스식 메뉴 / 전체 = 모든 메뉴</span></span><span class="t-li-r">${tChips("st-ui", [["easy", "쉬운"], ["pro", "전체"]], uiMode())}</span></div>
      <div class="t-li"><span class="t-co-t"><b>테마</b><span>기기 설정을 따르다가 직접 바꾸면 기억해요</span></span><span class="t-li-r">${tChips("st-th", [["light", "라이트"], ["dark", "다크"]], theme)}</span></div></div>`)}
    ${tCard("바로 가기", tList([["#alerts", "알림 설정", "가격 · 뉴스 · 일정 알림 켜고 끄기"], ["#budget", "내 투자 한도", "원금 · 최대 손실"], ["#profile", "내 투자 성향", "성장 · 배당 · 관심 업종"], ["#datahealth", "데이터 상태 · 키 진단", "API 키가 잘 들어갔는지"], ["#safety", "안전 센터", "자동 정지 조건"], ["#governance", "보안 · 라이선스", "데이터 사용 조건"]].map(([h, t, d]) => tRow(h, `<span class="t-co-t"><b>${t}</b><span>${d}</span></span>`, "", { chev: true }))))}
    <h2 class="t2-sech">자세한 설정</h2>
    ${tLegacy("st-legacy")}
    ${tFull("settings")}`);
  tBind(root, "st-ui", (k) => { if (k !== uiMode()) setUiMode(k); });
  tBind(root, "st-th", (k) => { if (k !== document.documentElement.dataset.theme) { $("#theme-btn").click(); TV.settings(el); } });
  const box = root.querySelector("#st-legacy");
  box.innerHTML = uiSettingsCard() + alertSettingsCard() + extraSettingsCard() + viewSettings(S.data || {});
  bindUiSettings(); bindAlertSettings(); bindExtraSettings(); notifyCard(box);
};
TV.goal = async (el) => {
  const q = S.goalQ || {};
  const qs = new URLSearchParams(Object.entries(q).filter(([, v]) => v !== "" && v != null)).toString();
  const L = await tLoad(el, "goal", "내 목표", "t2-goal", () => api(`/api/goal${qs ? `?${qs}` : ""}`));
  if (!L) return;
  const g = L.d, i = g.inputs, sim = g.sim || {}, pr = g.progress || {}, d = (g.saved || {}).dca || {};
  const p = g.p_target ?? 0;
  const yrs = (y) => y ? `${y}년` : "30년 넘게";
  tPaint(L.root, "내 목표", `
    ${tHero({ k: `${tMoney(i.principal)}에서 매달 ${tMoney(i.monthly)}씩 · ${esc(g.assumption?.name || "")}`, lv: p >= 0.7 ? "good" : p >= 0.4 ? "warn" : "bad", big: `${i.target_years}년 안에 ${tMoney(i.goal)}, 될 확률 ${tPr(p)}`,
      body: `${tProg(p, p >= 0.7 ? "good" : p >= 0.4 ? "warn" : "bad", "달성 확률")}${tKV([["보통이면", yrs(sim.median_years), "", "절반의 미래에서 이 안에"], ["확률 80% 가 되려면", `매달 ${tMoney(g.need_monthly?.p80)}`], ["중간에 크게 빠질 때", tPct(sim.mdd_p50, 0), "down", `30% 넘게 빠질 확률 ${tPr(sim.p_mdd30)}`]])}`,
      foot: esc(g.assumption?.source || "") })}
    ${pr.set ? tCard("지금까지", typeof goalBar === "function" ? goalBar(pr) : "") : ""}
    ${(g.honest || []).length ? `<div class="t-note warn">${g.honest.map((x) => esc(koText(x))).join("<br>")}</div>` : ""}
    ${tCard("목표 바꿔 보기", `<div class="t-form">
      ${tField("지금 원금 (원)", `<input id="g-principal" class="t-in" inputmode="numeric" value="${num(i.principal)}">`)}
      ${tField("매달 적립 (원)", `<input id="g-monthly" class="t-in" inputmode="numeric" value="${num(i.monthly)}">`)}
      ${tField("목표 금액 (원)", `<input id="g-goal" class="t-in" inputmode="numeric" value="${num(i.goal)}">`)}
      ${tField("목표 기간 (년)", `<input id="g-years" class="t-in" inputmode="numeric" value="${i.target_years}">`)}
      ${tField("투자 방식", `<select id="g-strategy" class="t-in">${Object.entries(g.presets || {}).map(([k, x]) => `<option value="${k}" ${k === i.strategy ? "selected" : ""}>${esc(x.name)} (연 ${(x.mu * 100).toFixed(1)}% 가정)</option>`).join("")}</select>`)}
      ${tField("해마다 적립액 늘리기 (%)", `<input id="g-raise" class="t-in" inputmode="decimal" value="${(i.raise_pct * 100).toFixed(0)}">`)}</div>
      <div class="t-pro"><button class="t-btn ghost" id="g-calc">다시 계산</button><button class="t-btn primary" id="g-save">이 목표로 저장</button><span class="t-sub" id="g-msg"></span></div>`)}
    <div class="t-grid2">
      ${tCard("방식별 확률", tVs((g.compare || []).map((c) => [esc(c.name), c.p_target, c.key === i.strategy ? "acc" : "dim", `${tPr(c.p_target)} · ${yrs(c.median_years)}`]), { max: 1 }))}
      ${tCard("매달 얼마씩이면", tVs((g.what_if || []).map((w) => [`매달 ${tMoney(w.monthly)}`, w.p_target, w.monthly === i.monthly ? "acc" : "dim", `${tPr(w.p_target)}`]), { max: 1 }))}
    </div>
    ${(g.tax || []).length ? tCard("계좌 종류별 (세금 반영)", tList(g.tax.map((t) => `<div class="t-li${t.ok ? "" : " dim"}"><span class="t-co-t"><b>${esc(t.name)}</b><span class="t2-wrap">${esc(koText(t.note || ""))}</span></span><span class="t-li-r"><b class="num">${tPr(t.p_target)}</b><span class="t-sub">${yrs(t.median_years)}</span></span></div>`))) : ""}
    ${tCard("매달 자동 적립", `<div class="t-form">
      <label class="t2-check" style="margin:0"><input type="checkbox" id="d-on" ${d.on ? "checked" : ""}> 자동 적립 켜기</label>
      ${tField("매달 며칠 (1~28)", `<input id="d-day" class="t-in" inputmode="numeric" value="${d.day || 25}">`)}
      ${tField("금액 (원)", `<input id="d-amount" class="t-in" inputmode="numeric" value="${num(d.amount || i.monthly)}">`)}
      ${tField("장부", `<select id="d-mode" class="t-in"><option value="paper" ${d.mode !== "live" ? "selected" : ""}>모의 (자동으로 넣고 삼)</option><option value="live" ${d.mode === "live" ? "selected" : ""}>실계좌 (알림 + 주문표만)</option></select>`)}
      ${tField("무엇을 살까", `<select id="d-target" class="t-in"><option value="core" ${d.target !== "etf" ? "selected" : ""}>코어 전략 (시스템이 종목 선택)</option><option value="etf" ${d.target === "etf" ? "selected" : ""}>지수 ETF 하나만</option></select>`)}
      ${tField("ETF", `<select id="d-etf" class="t-in">${Object.entries(g.etfs || {}).map(([k, v]) => `<option value="${k}" ${(d.etf || "069500") === k ? "selected" : ""}>${esc(v)}</option>`).join("")}</select>`)}</div>
      <div class="t-sub" style="margin-top:10px">휴장이면 다음 거래일 · 한 달에 한 번 · 실계좌는 돈을 옮길 수 없어서 알림과 주문표만 보내요</div>
      <div class="t-pro"><button class="t-btn ghost" id="d-save">적립 설정 저장</button></div>`)}
    <div id="g-base"></div>
    <div class="t-foot">${esc(koText(g.note || ""))}</div>
    ${tFull("goal", '<a class="t-btn ghost" href="#budget">투자 한도</a><a class="t-btn ghost" href="#accounts">계좌 · 세금</a>')}`);
  if (typeof baselineCard === "function") { const gb = L.root.querySelector("#g-base"); gb.classList.add("tl", "t2-legacy"); baselineCard(gb); }
  const val = (id) => L.root.querySelector(id).value.replace(/[^\d.]/g, "");
  const read = () => ({ principal: val("#g-principal"), monthly: val("#g-monthly"), goal: val("#g-goal"), target_years: val("#g-years"), strategy: L.root.querySelector("#g-strategy").value, raise_pct: String((Number(val("#g-raise")) || 0) / 100) });
  L.root.querySelector("#g-calc").onclick = () => { S.goalQ = read(); TV.goal(el); };
  const save = async (extra = {}) => {
    const r = await post("/api/goal", { ...read(), ...extra }).catch((e) => ({ error: e.message }));
    L.root.querySelector("#g-msg").textContent = r.error ? `저장하지 못했어요: ${r.error}` : "저장했어요 — 홈에 진행률이 나와요";
    if (!r.error) { S.goalQ = null; setTimeout(() => TV.goal(el), 600); }
  };
  L.root.querySelector("#g-save").onclick = () => save();
  L.root.querySelector("#d-save").onclick = () => save({ dca: { on: L.root.querySelector("#d-on").checked, day: Number(val("#d-day")), amount: Number(val("#d-amount")), mode: L.root.querySelector("#d-mode").value, target: L.root.querySelector("#d-target").value, etf: L.root.querySelector("#d-etf").value } });
};
TV.myjournal = async (el) => {
  const L = await tLoad(el, "myjournal", "내 투자일지", "t2-mj", () => api("/api/myjournal"));
  if (!L) return;
  const j = L.d, pr = j.pairs || {}, dg = j.disagree || {};
  tPaint(L.root, "내 투자일지", `
    ${tHero({ k: `기록 ${tN(j.n)}개 · 채점 ${tN(j.scored)}개`, lv: "idle", big: j.me?.hit_rate != null ? `내 판단 ${tPr(j.me.hit_rate)} 맞음` : "채점 전이에요", sub: pr.n ? `AI 와 같은 종목·같은 날 ${pr.n}번 — 의견 일치 ${tPr(pr.agree_rate)} · 나 ${tPr(pr.me_hit)} vs AI ${tPr(pr.ai_hit)}` : "적는 순간 봉인돼서 나중에 고칠 수 없어요 — 솔직한 기록이 쌓여요" })}
    ${tCard("새로 적기", `<form class="t-form" id="mj-f">
      ${tField("종목 코드", `<input class="t-in" name="symbol" required maxlength="12" placeholder="예: 005930, NVDA">`)}
      ${tField("내 판단", `<select class="t-in" name="action"><option value="BUY">오른다 (산다)</option><option value="SELL">내린다 (판다)</option><option value="HOLD">지켜본다</option></select>`)}
      ${tField("확신", `<select class="t-in" name="conviction">${[1, 2, 3, 4, 5].map((k) => `<option value="${k}" ${k === 3 ? "selected" : ""}>${k}점</option>`).join("")}</select>`)}
      ${tField("기간", `<select class="t-in" name="horizon">${[1, 5, 20].map((k) => `<option value="${k}" ${k === 5 ? "selected" : ""}>${k}거래일</option>`).join("")}</select>`)}
      ${tField("이유", `<input class="t-in" name="reason" maxlength="500" placeholder="왜 그렇게 생각하나요?">`)}
      <div class="t2-field" style="justify-content:flex-end"><button class="t-btn primary">기록 (봉인)</button></div></form>`)}
    ${dg.n ? tCard(`AI 와 의견이 달랐던 ${dg.n}번`, `${tVs([["내가 맞음", dg.me_right / dg.n, "acc", `${dg.me_right}번`], ["AI 가 맞음", dg.ai_right / dg.n, "dim", `${dg.ai_right}번`]], { max: 1 })}`) : ""}
    ${(j.biases || []).length ? tCard("내 습관", tList(j.biases.map((b) => tStRow(b.title || b, b.detail || "", "warn", { label: "" })))) : ""}
    ${tCard("내 기록", tList((j.rows || []).map((r) => `<div class="t2-item">${tSym(r.symbol, r.name, `${esc(tDate(r.at))} · ${r.horizon}거래일 · 확신 ${r.conviction}${r.sealed ? " · 봉인" : ""}`, `${tPill(r.action, null, { short: true })}${r.ret != null ? `<span class="num t-sub ${tCls(r.ret)}">${tPct(r.ret, 1)}</span>` : ""}${tOk(r.correct)}`)}${r.reason ? `<div class="t2-why">${esc(r.reason)}</div>` : ""}</div>`), "아직 기록이 없어요"))}
    ${tFull("myjournal", '<a class="t-btn ghost" href="#journal">AI 판단 일지</a><a class="t-btn ghost" href="#profile">내 투자 성향</a>')}`);
  const f = L.root.querySelector("#mj-f");
  f.onsubmit = async (e) => {
    e.preventDefault();
    const body = Object.fromEntries(new FormData(f).entries());
    body.symbol = body.symbol.trim().toUpperCase(); body.conviction = Number(body.conviction); body.horizon = Number(body.horizon);
    try { await post("/api/myjournal", body); toast({ title: "기록했어요 (봉인)", body: `${body.symbol} ${koAct(body.action)}`, level: "good" }); TV.myjournal(el); }
    catch (err) { toast({ title: "기록하지 못했어요", body: err.message, level: "bad" }); }
  };
};
TV.profile = async (el) => {
  const L = await tLoad(el, "profile", "내 투자 성향", "t2-prof", () => Promise.all([api("/api/user-profile"), tSafe(api("/api/mistakes")), tSafe(api("/api/discover"))]));
  if (!L) return;
  const [u, m, dv] = L.d, pr = u.profile || {};
  let style = pr.style || "balanced", markets = new Set(pr.markets || ["KR"]);
  tPaint(L.root, "내 투자 성향", `
    ${tCard("나는 이런 투자를 해요", `<div class="t-k" style="margin-bottom:8px">스타일</div>${tChips("pf-style", Object.entries(u.styles || {}), style)}
      <div class="t-k" style="margin:14px 0 8px">관심 시장</div>${tChips("pf-mkt", [["KR", "한국"], ["US", "미국"]], [...markets])}
      <div class="t-form" style="margin-top:14px">${tField("관심 업종 (쉼표로)", `<input id="pf-sec" class="t-in" value="${esc((pr.sectors || []).join(", "))}" placeholder="반도체, 2차전지">`)}
      ${tField("목표", `<input id="pf-goal" class="t-in" value="${esc(pr.goal || "")}" placeholder="예: 5년 뒤 배당 월 50만원">`)}
      ${tField("하루 최대 손실 (%)", `<input id="pf-mdl" class="t-in" type="number" step="0.1" value="${pr.max_daily_loss ?? ""}" placeholder="예: 2">`)}</div>
      <div class="t-pro"><button class="t-btn primary" id="pf-save">저장</button></div>`)}
    ${m && !m.error ? tCard("내가 자주 하는 실수", (m.items || []).length ? tList(m.items.map((x) => tStRow(x.title || x.text || x, x.detail || x.advice || "", "warn", { label: x.n ? `${x.n}번` : "" }))) : `<div class="t-sub">${esc(koText(m.message || ""))}</div>`, '<a href="#myjournal">투자일지</a>') : ""}
    ${dv && !dv.error ? tCard(`성향에 맞는 종목 (${esc(dv.style || "")})`, tList((dv.rows || []).map((r) => tSym(r.symbol, r.name, esc(koText(r.why || r.reason || "")), r.last != null ? tPrice(r.last, r.chg_pct ?? r.chg, r.symbol) : ""))) + `<div class="t-foot">${esc(koText(dv.note || ""))}</div>`) : ""}
    ${tFull("profile")}`);
  tBind(L.root, "pf-style", (k) => { style = k; L.root.querySelectorAll("#pf-style button").forEach((b) => b.classList.toggle("on", b.dataset.k === k)); });
  tBind(L.root, "pf-mkt", (k, b) => { if (markets.has(k)) markets.delete(k); else markets.add(k); b.classList.toggle("on", markets.has(k)); });
  L.root.querySelector("#pf-save").onclick = async () => {
    const r = await post("/api/user-profile", { style, markets: [...markets], sectors: L.root.querySelector("#pf-sec").value.split(",").map((x) => x.trim()).filter(Boolean), goal: L.root.querySelector("#pf-goal").value, max_daily_loss: L.root.querySelector("#pf-mdl").value }).catch((e) => ({ error: e.message }));
    toast({ title: r.error ? "저장하지 못했어요" : "성향을 저장했어요", body: r.error || "", level: r.error ? "warn" : "good" });
    if (!r.error) TV.profile(el);
  };
};
TV.research = async (el) => {
  const L = await tLoad(el, "research", "과거로 시험하기", "t2-res", () => api("/api/research"));
  if (!L) return;
  const r = L.d;
  if (!r.trials && r.kind !== "lab") {
    tPaint(L.root, "과거로 시험하기", `${tHero({ k: "백테스트", lv: "idle", big: "아직 시험 결과가 없어요", sub: "실제 KRX 16년치 주가로 전략을 시험해 볼 수 있어요 (수십 분 걸려요)" })}
      ${tCard("시작하는 법", `<ol class="t-ol"><li><span class="t-num">1</span>터미널에서 <span class="mono">./run.sh research krx</span> 실행</li><li><span class="t-num">2</span>끝나면 이 화면에 결과가 자동으로 나와요</li></ol>`)}
      ${tFull("research", '<a class="t-btn ghost" href="#lab">모델 실험</a>')}`);
    return;
  }
  tPaint(L.root, "과거로 시험하기", `<div class="t-note">실제 KRX 과거 주가로 시험한 결과예요 — 비용·세금을 넣었고, 과거 성적이 미래를 보장하지 않아요.</div>${tLegacy("rs-legacy")}${tFull("research", '<a class="t-btn ghost" href="#lab">모델 실험</a>')}`);
  const box = L.root.querySelector("#rs-legacy");
  await (r.kind === "lab" ? viewLabResult(box, r) : viewResearch(box));
};

// ============================================================ v27 로고 직접 넣기 (국내 종목 로고 보강)
function tLogoSheet(sym0 = "", name = "") {
  tSheet(`<h3>로고 직접 넣기</h3>
    <p class="t-sheet-p" style="font-size:14px">공개 로고를 못 찾은 종목(특히 국내)은 회사 홈페이지 등에서 받은 그림을 넣으면 모든 화면에 바로 보여요. 이 컴퓨터에만 저장돼요.</p>
    <div class="t-form">
      ${tField("종목 코드", `<input id="lg-sym" class="t-in" maxlength="12" placeholder="예: 005930" value="${esc(sym0)}">`, esc(name))}
      ${tField("그림 파일", `<input id="lg-file" class="t-in" type="file" accept="image/png,image/jpeg,image/webp,image/gif,image/svg+xml">`, "PNG · JPG · WEBP · SVG · 140KB 이하 · 정사각형이 예뻐요")}</div>
    <div class="t2-logo-prev" id="lg-prev">${sym0 ? stockLogo(sym0, name, 64) : ""}</div>
    <div class="t-sheet-act"><button class="t-btn" id="lg-del">넣은 로고 지우기</button><button class="t-btn primary" id="lg-save" disabled>저장</button></div>
    <div class="t-foot">자동으로 받고 싶다면 .env 에 QUANT_LOGO_DEV_TOKEN (logo.dev 무료 키)을 넣으면 국내·미국 로고를 먼저 그곳에서 받아요</div>`, (b, close) => {
    let data = "";
    const symIn = b.querySelector("#lg-sym"), save = b.querySelector("#lg-save"), prev = b.querySelector("#lg-prev");
    b.querySelector("#lg-file").onchange = (e) => {
      const f = e.target.files[0];
      if (!f) return;
      if (f.size > 140000) { toast({ title: "그림이 너무 커요", body: `${Math.round(f.size / 1000)}KB — 140KB 이하로 줄여 주세요`, level: "warn" }); return; }
      const r = new FileReader();
      r.onload = () => { data = String(r.result); prev.innerHTML = `<img src="${esc(data)}" width="64" height="64" alt="미리 보기" style="border-radius:50%;object-fit:contain;background:var(--t-soft)">`; save.disabled = !symIn.value.trim(); };
      r.readAsDataURL(f);
    };
    symIn.oninput = () => { save.disabled = !data || !symIn.value.trim(); };
    const send = async (d) => {
      const sym = symIn.value.trim().toUpperCase();
      if (!sym) return;
      try { await post("/api/logo-upload", { symbol: sym, data: d }); toast({ title: d ? "로고를 넣었어요" : "넣은 로고를 지웠어요", body: `${sym} — 화면을 새로 열면 보여요`, level: "good" }); close(); }
      catch (e) { toast({ title: "저장하지 못했어요", body: e.message, level: "warn" }); }
    };
    save.onclick = () => send(data);
    b.querySelector("#lg-del").onclick = () => send("");
  });
}
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-logo-up]");
  if (!b) return;
  e.preventDefault();
  tLogoSheet(b.dataset.logoUp || "", b.dataset.logoName || "");
});

// ============================================================ v28 AI 추천 (신호 엔진 2.0)
// 가운데가 0, 오른쪽 빨강(+) · 왼쪽 파랑(-) 막대 — 점수 -3 ~ +3
const tDiv = (v, max = 3) => { const f = Math.min(1, Math.abs(v || 0) / max) * 50; return `<span class="t2-div" aria-hidden="true"><i class="${v >= 0 ? "up" : "down"}" style="${v >= 0 ? "left:50%" : `left:${(50 - f).toFixed(1)}%`};width:${f.toFixed(1)}%"></i></span>`; };
const tScore = (v) => `<b class="num t2-score ${v >= 0.5 ? "up" : v <= -0.5 ? "down" : ""}">${v > 0 ? "+" : ""}${Number(v).toFixed(1)}</b>`;
function tPickCard(r, side, money) {
  const ev = r.evidence || {};
  const sigs = (r.signals || []).slice().sort((a, b) => (b.verified - a.verified) || (Math.abs(b.score) * (b.weight || 0.01) - Math.abs(a.score) * (a.weight || 0.01)));
  const tag = (s) => s.flip ? '<span class="t-tag warn">반대로 작동</span>' : s.verified ? '<span class="t-tag">검증됨</span>' : s.verdict === "효과 확인 안 됨" ? '<span class="t-tag">효과 없음 · 0</span>' : '<span class="t-tag">검증 전</span>';
  return `<details class="t2-pick"${side === "buy" ? "" : ""}>
    <summary>${tName(r.symbol, r.name, esc([r.sector, r.held ? "보유 중" : r.focus ? "관심" : ""].filter(Boolean).join(" · ")), 44)}
      <span class="t2-pick-r">${tScore(r.score)}${tDiv(r.score)}<span class="num t-sub">${tPx(r.last, r.symbol)} <span class="${tCls(r.chg)}">${tPct(r.chg)}</span></span></span></summary>
    <div class="t2-pick-b">
      ${ev.n ? `<div class="t2-ev"><span class="t-k">과거 비슷한 점수 (${esc(ev.label || "")}) — 보지 않은 기간</span><b>${tN(ev.n)}번 중 ${tPr(ev.hit)} 시장보다 ${side === "buy" ? "올랐음" : "올랐음"}</b><em>20거래일 뒤 평균 ${tPct(ev.mean, 1)} (시장 대비)</em></div>` : ""}
      <div class="t2-sigs">${sigs.map((s) => `<div class="t2-sig${!s.verified && !s.flip ? " dim" : ""}"><span class="t2-sig-k">${esc(s.label)} ${tag(s)}</span>${tDiv(s.flip ? -s.score : s.score)}<span class="t2-sig-t">${esc(s.text || "")}</span></div>`).join("")}</div>
      <div class="t2-stop">${side === "buy" && r.stop ? `이 가격 아래로 내려가면 판단이 틀린 것으로 봐요 <b class="num down">${tPx(r.stop, r.symbol)}</b>` : r.invalid_above ? `이 가격 위로 올라가면 판단이 틀린 것으로 봐요 <b class="num up">${tPx(r.invalid_above, r.symbol)}</b>` : ""}<span class="t-sub">하루 평균 움직임의 2배</span></div>
      <div class="t-pro no-logo" style="margin-top:10px"><a class="t-btn ghost" href="#analysis/${encodeURIComponent(r.symbol)}">종목 화면</a>${side === "buy" ? `<button class="t-btn primary" data-pick-buy="${esc(r.symbol)}" data-name="${esc(r.name)}" data-last="${r.last}">모의 매수</button>` : ""}</div>
    </div></details>`;
}
TV.picks = async (el) => {
  const mk = T2.picksMk || "KR";
  const L = await tLoad(el, "picks", "AI 추천", "t2-picks", () => api(`/api/signals2?market=${mk}`));
  if (!L) return;
  const d = L.d;
  if (d.error) { tPaint(L.root, "AI 추천", `${tChips("pk-mk", [["KR", "국내"], ["US", "미국"]], mk)}<div class="t-gap"></div>${tEmpty("아직 계산할 수 없어요", d.error)}`); tBind(L.root, "pk-mk", (k) => { T2.picksMk = k; TV.picks(el); }); return; }
  const tier = d.calibration?.tier || {};
  const tab = T2.picksTab || "buy";
  const list = d[tab] || [];
  const W = d.weights || {}, st = W.stats || {};
  const PL = { trend: "추세", mom: "12개월 오름세", high52: "52주 고점 근처", volsurge: "거래대금 급증", reversal: "단기 과열·급락", lowvol: "덜 출렁임", sector_rs: "업종 안 강도" };
  const fw = d.forward || {};
  const EMPTY = { buy: ["오늘은 강한 매수 후보가 없어요", "점수 +0.5 이상인 종목이 없을 땐 억지로 고르지 않아요"], sell: ["줄일 만한 보유·관심 종목이 없어요", "보유·관심 종목 중 점수 -0.5 이하가 없어요"], avoid: ["피할 종목이 없어요", ""] };
  tPaint(L.root, "AI 추천", `
    ${tChips("pk-mk", [["KR", "국내"], ["US", "미국"]], mk)}<div class="t-gap"></div>
    ${tHero({ k: `${esc(d.as_of)} 일봉 기준 · ${tN(d.eligible)}종목 중 (거래 적은 종목 제외)`, lv: tier.key, big: `${lvDot(tier.key)} ${esc(tier.label || "-")}`, sub: esc(tier.why || ""),
      foot: `${esc(d.regime?.text || "")} · 자동매매에는 아직 쓰지 않아요 — 아래 '실제로 지나 본 결과'가 기준을 넘으면 연결해요` })}
    ${tTabs("pk-tab", [["buy", `매수 후보 ${(d.buy || []).length}`], ["sell", `비중 축소 ${(d.sell || []).length}`], ["avoid", `피할 종목 ${(d.avoid || []).length}`]], tab, "t-tabs-line")}
    <div class="t-sub" style="margin:8px 4px 12px">${tab === "buy" ? "여러 신호를 합친 점수가 높은 순 · 눌러서 근거 보기" : tab === "sell" ? "내가 가진·관심 종목 중 점수가 낮은 순 — 팔거나 줄일지 검토" : "전체에서 점수가 가장 낮은 종목 — 새로 사지 않기"}</div>
    ${list.length ? `<div class="t2-picks">${list.map((r) => tPickCard(r, tab)).join("")}</div>` : tCard("", tEmpty(...EMPTY[tab]))}
    ${tCard("점수를 이렇게 만들어요", `<div class="t-sub" style="margin-bottom:10px">${W.basis === "past" ? `과거 ${esc(W.train_from || "")} ~ ${esc(W.train_to || "")} 동안 신호마다 '20거래일 뒤 시장보다 올랐나'를 재서, 꾸준히 맞은 신호만 반영해요` : "과거 자료가 부족해 검증 전 기본 가중치를 써요"}</div>
      ${tList(Object.keys(PL).map((k) => { const s = st[k] || {}; const w = (W.weights || {})[k] || 0; return `<div class="t-li"><span class="t-co-t"><b>${PL[k]}</b><span>${esc(s.verdict || "-")}${s.ic != null ? ` · ${tG("IC", "순위 예측력")} ${s.ic.toFixed(3)} (t ${s.t ?? "-"}, ${s.n_dates}개 날짜)` : ""}</span></span><span class="t-li-r t2-w">${tProg(Math.abs(w), w < 0 ? "warn" : w ? "good" : "idle")}<b class="num">${Math.round(Math.abs(w) * 100)}%</b></span></div>`; }))}
      <div class="t-foot">공시 · 뉴스 · 외국인/기관 수급 · 실적 서프라이즈 · 커뮤니티는 아직 과거 기록이 짧아 '검증 전'으로 작게(최대 30%) 반영 · 지금 자료가 있는 종목: 공시 ${tN(d.live_coverage?.disclosure)} · 뉴스 ${tN(d.live_coverage?.news)} · 수급 ${tN(d.live_coverage?.flow)} · 실적 ${tN(d.live_coverage?.earnings)} · 커뮤니티 ${tN(d.live_coverage?.community)}</div>`)}
    ${tCard(`점수대별 실제 결과 (보지 않은 기간 ${esc(d.calibration?.from || "")} ~ ${esc(d.calibration?.to || "")})`, `${tVs((d.calibration?.bins || []).map((b) => [esc(b.label), b.hit, b.hit >= 0.5 ? "up" : "down", b.n ? `${tPr(b.hit)} · 평균 ${tPct(b.mean, 1)}` : "-"]), { max: 1 })}
      <div class="t-foot">막대 = 20거래일 뒤 시장보다 오른 종목 비율 · 평균 = 시장 대비 평균 수익 · 가중치를 정할 때 쓰지 않은 기간이라 '처음 보는 시험'에 가까워요</div>`)}
    ${tCard("실제로 지나 본 결과 (매일 봉인한 후보)", fw.buy?.n ? tKV([["매수 후보", tPr(fw.buy.hit), fw.buy.hit >= 0.5 ? "up" : "down", `${tN(fw.buy.n)}건 · 평균 ${tPct(fw.buy.mean_excess, 1)}`], ["비중 축소", fw.sell?.n ? tPr(fw.sell.hit) : "-", "", `${tN(fw.sell?.n)}건 · 내려간 비율`], ["피할 종목", fw.avoid?.n ? tPr(fw.avoid.hit) : "-", "", `${tN(fw.avoid?.n)}건`]])
      : tEmpty(`기록 ${tN(fw.days)}일 · 채점 기다리는 중 ${tN(fw.pending_days)}일`, `후보는 매일 그날 처음 계산한 그대로 저장돼요 (나중에 못 바꿈) · ${fw.horizon || 20}거래일이 지나면 여기에 결과가 나와요`))}
    <div class="t-foot">${esc(d.rule || "")} · ${esc(d.note || "")}</div>
    <div class="t-pro"><a class="t-btn ghost" href="#aitrust">AI 신뢰 센터</a><a class="t-btn ghost" href="#scorecard">AI 성적표</a></div>`);
  tBind(L.root, "pk-mk", (k) => { T2.picksMk = k; TV.picks(el); });
  tBind(L.root, "pk-tab", (k) => { T2.picksTab = k; TV.picks(el); });
  L.root.querySelectorAll("[data-pick-buy]").forEach((b) => b.onclick = () => tOrderSheet(b.dataset.pickBuy, b.dataset.name, Number(b.dataset.last)));
};
// 홈 카드 · 종목 화면 카드
async function tPicksHome(box) {
  let d;
  try { d = await api("/api/signals2?market=KR"); } catch { box.remove(); return; }
  if (!box.isConnected || d.error) { box.innerHTML = ""; return; }
  const t = d.calibration?.tier || {};
  const xs = (d.buy || []).slice(0, 3);
  box.innerHTML = tSec("오늘의 매수 후보", `<a class="t-trust t-trust-${t.key === "bad" ? "banned" : t.key === "good" ? "verified" : "checking"}" href="#picks">${lvDot(t.key)}<span><b>신호 엔진 · ${esc(t.label || "")}</b>${esc(t.why || "")}</span>${TI.chev}</a>
    ${xs.length ? `<div class="t-list">${xs.map((r) => tRow(`#picks`, tName(r.symbol, r.name, esc((r.signals || []).filter((s) => s.verified).slice(0, 2).map((s) => s.text).join(" · ")), 40), `${tScore(r.score)}<span class="t-sub num">${tPx(r.last, r.symbol)}</span>`)).join("")}</div>`
      : tEmpty("오늘은 강한 매수 후보가 없어요", "억지로 고르지 않아요")}`, '<a href="#picks">전체 · 비중 축소</a>', "t-card");
}
async function tStockSignal(root, sym) {
  const box = root.querySelector("#tsk-sum-sig");
  if (!box) return;
  let d;
  try { d = await api(`/api/signals2/stock?symbol=${encodeURIComponent(sym)}`); } catch { box.innerHTML = ""; return; }
  if (!box.isConnected) return;
  if (!d.row) { box.innerHTML = tSec("신호 점수", tEmpty("신호 점수가 없어요", d.why || ""), "", "t-card"); return; }
  const r = d.row, side = d.side;
  box.innerHTML = tSec(`신호 점수 ${tScore(r.score)}`, `<div class="t-sub" style="margin-bottom:8px">${side === "buy" ? "오늘의 매수 후보예요" : side === "sell" ? "비중 축소 후보예요" : side === "avoid" ? "피할 종목 목록에 있어요" : "후보는 아니에요"} · 신호 엔진 ${esc(d.tier?.label || "")} · ${esc(d.as_of || "")}</div>
    <div class="t2-sigs">${(r.signals || []).map((s) => `<div class="t2-sig${!s.verified && !s.flip ? " dim" : ""}"><span class="t2-sig-k">${esc(s.label)}</span>${tDiv(s.flip ? -s.score : s.score)}<span class="t2-sig-t">${esc(s.text || "")}</span></div>`).join("")}</div>
    ${r.evidence?.n ? `<div class="t-foot">과거 비슷한 점수 ${tN(r.evidence.n)}번 중 ${tPr(r.evidence.hit)} 시장보다 올랐음 · 평균 ${tPct(r.evidence.mean, 1)} (20거래일)</div>` : ""}`, '<a href="#picks">AI 추천</a>', "t-card");
}
