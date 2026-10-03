// v14 — 시장 시계 · Truth Center · 완성 기준 · 왜 BUY/SELL/NO TRADE · 내 보유 · 종목별 AI 적중률 · 차트 매매 구간선 ·
// 오늘 할 일 · 시장 한눈에 · 종목 비교 · 관심종목 별표 · 키보드 단축키 · 최근 검색
/* global price, $, S, api, esc, card, empty, badge, kv, R, P, num, asOf, pill, render, safeGet, safeSet, toast, ICONS, lineChart, CAL_ICON */

// ------------------------------------------------------------ 상단 시장 시계 (장중·장외·휴장 · 다음 개장/폐장까지 남은 시간)
const MKT = { data: null, fetchedAt: 0 };
window.MKT_CLOCK_ON = true;
const PHASE_KO = { open: "장중", pre_open: "장전", closed: "장외" };

function dur(sec) {
  if (sec < 0) sec = 0;
  const d = Math.floor(sec / 86400), h = Math.floor(sec % 86400 / 3600), m = Math.floor(sec % 3600 / 60), s = Math.floor(sec % 60);
  const p = (n) => String(n).padStart(2, "0");
  return d ? `${d}일 ${h}시간` : h ? `${h}:${p(m)}:${p(s)}` : `${m}:${p(s)}`;
}

async function loadClock() {
  try { MKT.data = await api("/api/clock"); MKT.fetchedAt = Date.now(); } catch { /* 다음 틱에 다시 */ }
  tickMarketClock();
}

function tickMarketClock() {
  const box = $("#market-badges");
  const d = MKT.data;
  if (!box || !d) return;
  let refetch = false;
  box.innerHTML = Object.entries(d.markets).map(([k, m]) => {
    const target = Date.parse(m.next_event === "폐장" ? m.next_close : m.next_open);
    const left = (target - Date.now()) / 1000;
    if (left <= 0) refetch = true;
    const state = m.trading_day ? m.session_label || PHASE_KO[m.phase] || m.phase : `휴장${m.holiday && m.holiday !== "주말" ? "·" + m.holiday : ""}`;
    const cls = m.phase === "open" ? "open" : m.trading_day ? (["pre_market", "after_hours", "pre_auction"].includes(m.session_code) ? "ext" : "") : "holiday";
    const tip = `${m.name} · 현지 ${m.local_time} · 다음 개장 ${m.next_open_kst} · 다음 폐장 ${m.next_close_kst}${m.session?.note ? " · " + m.session.note : ""}${m.dst == null ? "" : m.dst ? " · 서머타임 (정규장 22:30~05:00 한국시간)" : " · 표준시 (정규장 23:30~06:00 한국시간)"}`;
    const word = m.phase === "open" ? "장중" : m.trading_day ? (m.session_label || "장마감") : "휴장";
    return `<a class="pill mktc ${cls}" href="#truth" title="${esc(tip)}"><b>${esc(m.short || k)}</b> ${m.light || ""} ${esc(word === state ? word : state)} <span class="mono xs">· ${m.next_event === "폐장" ? "마감까지" : "개장까지"} ${dur(left)}</span></a>`;
  }).join(" ");
  const notes = Object.values(d.markets).map((m) => m.notice).filter(Boolean);
  const nb = $("#market-notice");
  if (nb) nb.innerHTML = notes.length ? notes.map((n) => `<div class="mkt-notice">🛑 ${esc(n)}</div>`).join("") : "";
  if (refetch && Date.now() - MKT.fetchedAt > 20e3) loadClock();
}

function initMarketClock() {
  loadClock();
  setInterval(tickMarketClock, 1000);
  setInterval(loadClock, 60e3);  // 세부 세션(동시호가·시간외·프리/애프터마켓)이 바뀌는 시점을 놓치지 않게
}

// ------------------------------------------------------------ Truth Center · 완성 기준
const TR_ICON = { ok: "🟢", warn: "🟡", bad: "🔴", na: "⚪" };
const TR_LABEL = { ok: "정상", warn: "주의", bad: "문제" };
const CK_LABEL = { live: "실시간 확인", impl: "구현·테스트", setup: "설정 필요", warn: "주의", bad: "문제" };
const CK_CLS = { live: "ok", impl: "ok", setup: "none", warn: "warn", bad: "bad" };

async function viewTruth(el) {
  const [t, c] = await Promise.all([api(`/api/truth${S._truthRefresh ? "?refresh=1" : ""}`), api("/api/checklist")]);
  S._truthRefresh = false;
  const secs = (t.sections || []).map((s) => `<div class="card tr-sec tr-${esc(s.status)}"><div class="card-h"><h3>${TR_ICON[s.status] || ""} ${esc(s.title)}</h3><div class="right">${pill({ ok: "ok", warn: "warn", bad: "bad" }[s.status], TR_LABEL[s.status] || s.status)}</div></div>
    <div class="tr-rows">${s.checks.map((x) => `<div class="tr-row"><span class="tr-ic">${TR_ICON[x.status] || ""}</span><div><div class="small b">${esc(x.label)}</div><div class="xs muted">${esc(x.detail)}</div></div></div>`).join("")}</div></div>`).join("");
  const areas = Object.entries(c.areas || {});
  const f = S.ckFilter || "all";
  const items = (c.items || []).filter((r) => f === "all" || (f === "problem" ? ["warn", "bad"].includes(r.status) : f === "setup" ? r.status === "setup" : r.status === "live"));
  let lastArea = null;
  const rows = items.map((r) => {
    const head = r.area !== lastArea ? `<tr class="ck-area"><td colspan="4">${esc(r.area)}</td></tr>` : "";
    lastArea = r.area;
    return `${head}<tr><td><b>${esc(r.item)}</b><div class="xs dim">${esc(r.what)}</div></td><td>${pill(CK_CLS[r.status], CK_LABEL[r.status] || r.status)}</td><td class="xs" style="white-space:normal">${esc(r.detail || "")}</td><td class="xs mono dim" style="white-space:normal">${esc(r.where)}<br>${esc(r.test)}</td></tr>`;
  }).join("");
  const tot = c.total || {};
  el.innerHTML = `
  <div class="card rd-hero ${{ ok: "rd-ready", warn: "rd-caution", bad: "rd-not" }[t.status] || ""}">
    <div class="rd-top"><div><div class="xs muted">Truth Center — AI 보다 먼저: 시장 시계 · 데이터 · 이벤트 · 증권사 · 포트폴리오 위험 · 체결이 사실인가</div>
      <h2>${esc({ ok: "모두 사실 확인", warn: "주의 항목 있음", bad: "문제 있음 — 해당 매매 관문 빨강" }[t.status] || "-")}</h2>
      <div class="small muted">${asOf(t.as_of || t.at)} · ${esc(t.mode || "")} 장부 기준</div>
      ${(t.bad || []).length ? `<div class="veto" style="margin-top:8px">⛔ ${t.bad.slice(0, 6).map(esc).join("<br>⛔ ")}</div>` : ""}</div>
      <div class="vf-act"><button class="btn-sm primary" id="truth-refresh">지금 다시 확인</button><a class="btn-sm" href="#readiness">매매 준비</a></div></div>
    <div class="xs dim" style="margin-top:8px">문제(🔴) 항목은 매매 준비의 해당 관문(DATA·EVENT·BROKER·RISK)을 빨강으로 만들어 신규 매수를 막습니다 (fail-closed).</div>
  </div>
  <div class="tr-grid">${secs}</div>
  ${card(`완성 기준 체크리스트 <span class="small dim">${tot.n || 0}개 항목 · 확인 ${tot.done || 0} · 설정 필요 ${tot.setup || 0} · 문제 ${tot.problems || 0}</span>`, `
    <div class="ck-areas">${areas.map(([a, v]) => `<div class="ck-a"><b>${esc(a)}</b><div class="ck-bar">${["live", "impl", "setup", "warn", "bad"].map((k) => v[k] ? `<span class="ck-${k}" style="flex:${v[k]}" title="${CK_LABEL[k]} ${v[k]}"></span>` : "").join("")}</div><span class="xs dim">${v.live + v.impl}/${v.n}</span></div>`).join("")}</div>
    <div class="tabs" id="ck-tabs" style="margin:10px 0">${[["all", "전체"], ["live", "실시간 확인"], ["problem", "문제·주의"], ["setup", "설정 필요"]].map(([k, l]) => `<button data-k="${k}" class="${f === k ? "on" : ""}">${l}</button>`).join("")}</div>
    <div class="scroll" style="max-height:640px"><table class="tight ck-table"><thead><tr><th>항목</th><th>상태</th><th>지금 확인된 것</th><th>구현 · 검증 테스트</th></tr></thead><tbody>${rows || `<tr><td colspan="4">${empty("해당 항목 없음")}</td></tr>`}</tbody></table></div>
    <div class="xs dim" style="margin-top:8px">'구현·테스트' = 코드와 자동 테스트로 확인, 지금 관찰할 데이터가 없는 항목 (예: 실주문이 없어 부분체결 0건). 문서: docs/FINAL_CHECKLIST.md</div>`)}`;
  $("#truth-refresh").onclick = () => { S._truthRefresh = true; render(); };
  document.querySelectorAll("#ck-tabs button").forEach((b) => b.onclick = () => { S.ckFilter = b.dataset.k; render(); });
}

// ------------------------------------------------------------ 종목 페이지: 왜 BUY · 왜 SELL · 왜 NO TRADE
async function whyCard(sym) {
  const box = $("#pf-why");
  if (!box) return;
  let e;
  try { e = await api(`/api/explain?symbol=${encodeURIComponent(sym)}`); } catch { box.innerHTML = ""; return; }
  if (!e || e.error) { box.innerHTML = ""; return; }
  const bar = (x, side) => `<div class="why-it"><div class="why-h"><b>${esc(x.ai)}</b><span class="num ${side}">${(x.prob_up * 100).toFixed(0)}%</span></div>
    <div class="why-bar"><span class="${side}" style="width:${Math.min(100, x.influence * 1000).toFixed(0)}%"></span></div>
    <div class="xs muted">${esc(x.summary || "")}${x.track ? ` · <span class="dim">${esc(x.track)}</span>` : ""}</div></div>`;
  const upSide = e.action === "SELL" ? "down" : "up", dnSide = e.action === "SELL" ? "up" : "down";
  box.innerHTML = card(`왜 ${esc(e.action === "NO_TRADE" ? "NO TRADE" : e.action)}? <span class="small dim">봉인된 판단 기록 그대로 · 새로 계산하지 않음</span>`, `
    <div class="why-head">${badge(e.action)} <b>${esc(e.headline)}</b></div>
    ${(e.blocks || []).length ? `<div class="veto" style="margin:8px 0">⛔ 막은 것: ${e.blocks.map(esc).join(" · ")}</div>` : ""}
    <div class="grid g-2" style="margin-top:8px">
      <div><div class="small muted" style="margin-bottom:6px">찬성 근거 <span class="xs dim">(영향 = 가중치 × 확률의 치우침)</span></div>${(e.for || []).map((x) => bar(x, upSide)).join("") || '<div class="xs dim">없음</div>'}</div>
      <div><div class="small muted" style="margin-bottom:6px">반대 근거</div>${(e.against || []).map((x) => bar(x, dnSide)).join("") || '<div class="xs dim">반대 의견 없음</div>'}</div>
    </div>
    <div class="small" style="margin-top:10px"><b>무엇이 바뀌면 판단이 바뀌나</b></div>
    <ul class="plain small">${(e.what_changes || []).map((x) => `<li>→ ${esc(x)}</li>`).join("")}</ul>
    ${(e.reasons || []).length || (e.risks || []).length ? `<div class="xs muted" style="margin-top:8px">${(e.reasons || []).map((x) => "✓ " + esc(x)).join(" · ")}${(e.risks || []).length ? " · " + e.risks.map((x) => "⚠ " + esc(x)).join(" · ") : ""}</div>` : ""}
    <div class="xs dim" style="margin-top:8px">기준: 매수 P(상승) ≥ ${R(e.thresholds.buy_prob, 0)} · 신뢰도 ≥ ${e.thresholds.min_confidence} / 매도 P(상승) ≤ ${R(e.thresholds.sell_prob, 0)}${e.sealed ? " · 🔒 해시 봉인" : ""}</div>`, asOf(e.as_of));
}

// ------------------------------------------------------------ 종목 페이지: 내 보유 · 관심 별표 · 과거 AI 적중률 · 비교
async function holdCard(sym) {
  const box = $("#pf-hold");
  if (!box) return;
  let h;
  try { h = await api(`/api/holdings?symbol=${encodeURIComponent(sym)}`); } catch { box.innerHTML = ""; return; }
  if (h.error) { box.innerHTML = ""; return; }
  const t = h.track || {};
  const rows = (h.rows || []).map((r) => `<tr><td>${esc(r.book)}</td><td class="r num">${num(r.qty)}</td><td class="r num">${r.avg_price ? num(r.avg_price, 2) : "-"}</td><td class="r num">${r.value ? num(r.value) : "-"}</td><td class="r ${r.pnl_pct >= 0 ? "up" : "down"}">${P(r.pnl_pct)}</td></tr>`).join("");
  const by = Object.entries(t.by_action || {}).map(([a, v]) => `<span class="chip xs">${esc(a)} ${v.n}건 · 적중 ${R(v.hit, 0)}${v.avg_ret != null ? ` · 평균 ${P(v.avg_ret)}` : ""}</span>`).join(" ");
  const dots = (t.recent || []).map((r) => `<span class="hit-dot ${r.correct ? "ok" : "no"}" title="${esc(r.as_of)} ${esc(r.action)} P ${R(r.prob_up, 0)} → ${P(r.ret)}"></span>`).join("");
  box.innerHTML = `<div class="grid g-2">
    ${card(`내 보유 <button class="star-btn ${h.starred ? "on" : ""}" id="star-btn" title="관심종목 (단축키 s) — 매일 AI 판단·알림 대상">${h.starred ? "★ 관심종목" : "☆ 관심종목 추가"}</button>`,
      rows ? `<table class="tight"><thead><tr><th>장부 · 계좌</th><th class="r">수량</th><th class="r">평단</th><th class="r">평가</th><th class="r">손익</th></tr></thead><tbody>${rows}</tbody></table>
        <div class="xs dim" style="margin-top:6px">합계 ${num(h.total_qty)}주${h.value ? ` · ${num(h.value)}` : ""} · 현재가 ${h.last ? num(h.last, 2) : "-"}</div>`
        : `<div class="small muted">이 종목을 보유한 장부·계좌가 없습니다.</div><div class="xs dim" style="margin-top:6px"><a href="#accounts">계좌 · 세금 · 배당</a>에 내 계좌 보유를 넣으면 여기 함께 보입니다.</div>`,
      `<a class="btn-sm" href="#compare/${esc(sym)}">비교</a>`)}
    ${card("이 종목 과거 AI 적중률 <span class='small dim'>결과가 확정된 판단만</span>", t.n_scored ? `
      <div class="kv-grid">${kv("적중률", R(t.hit, 0), t.baseline_up != null && t.hit > t.baseline_up ? "good" : "")}${kv("95% 구간", t.ci95 ? `${R(t.ci95[0], 0)}~${R(t.ci95[1], 0)}` : "-")}${kv("기준 (항상 상승)", R(t.baseline_up, 0))}</div>
      <div class="small" style="margin-top:8px"><b>${esc(t.verdict)}</b> <span class="xs dim">채점 ${t.n_scored} / 전체 ${t.n_total}</span></div>
      <div style="margin-top:6px">${by}</div><div class="hit-dots" style="margin-top:8px">${dots}<span class="xs dim">최근 채점 (초록 = 적중)</span></div>`
      : empty(t.n_total ? `판단 ${t.n_total}건 — 아직 결과(5거래일)가 확정되지 않았습니다` : "이 종목에 대한 AI 판단 기록이 없습니다"))}
  </div>`;
  $("#star-btn").onclick = () => toggleStar(sym, !h.starred).then(() => holdCard(sym));
}

async function toggleStar(sym, on) {
  const r = await api("/api/star", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ symbol: sym, on }) });
  if (typeof toast === "function") toast({ title: r.error ? "실패" : on ? "관심종목에 추가" : "관심종목에서 뺌", body: r.error || (on ? "매일 AI 판단·알림 대상입니다" : ""), level: r.error ? "warn" : "good" });
  return r;
}

// ------------------------------------------------------------ 차트: AI 매수 구간 · 손절 · 목표 · 추격 금지 선
async function chartPlanLines(sym, plan) {
  if (!plan) return;
  for (let i = 0; i < 30; i++) {  // 차트가 아직 그려지는 중이면 잠깐 기다림
    const el = $("#an-chart");
    if (!el || el.dataset.sym !== sym) { await new Promise((r) => setTimeout(r, 150)); continue; }
    if (!el._series) { await new Promise((r) => setTimeout(r, 150)); continue; }
    const en = plan.entry || {};
    const L = (price, color, title, style, pri) => chartLine(el, { price, color, title, style, pri });
    L(plan.stop, "#ef4444", "손절", 0, 10);
    L(en.low, "#3b82f6", "진입 하단", 2, 8);
    L(en.high, "#3b82f6", "진입 상단", 2, 7.5);
    L(en.no_chase_above, "#f59e0b", "추격 금지", 2, 6);
    L(plan.target, "#22c55e", "목표", 2, 7);
    const leg = document.createElement("div");
    leg.className = "chart-legend xs";
    leg.innerHTML = `<span style="color:#3b82f6">━ AI 진입 구간</span> <span style="color:#ef4444">━ 손절</span> <span style="color:#22c55e">┅ 목표</span> <span style="color:#f59e0b">┅ 추격 금지</span> <span class="dim">▲▼ 과거 AI BUY/SELL</span>${plan.action && plan.action !== "BUY" ? ` <span class="warn-t">· 지금 신호 ${esc(plan.action)} — 구간은 '산다면' 참고용</span>` : ""}`;
    el.parentElement?.appendChild(leg);
    return;
  }
}

// ------------------------------------------------------------ 홈: 오늘 할 일 · 시장 한눈에
async function todayCard(root) {
  if (!root) return;
  const box = document.createElement("div");
  box.id = "home-today";
  root.prepend(box);
  let t;
  try { t = await api("/api/today"); } catch { box.remove(); return; }
  if (t.error) { box.remove(); return; }
  const g = t.glance || {};
  const mk = Object.entries(g.markets || {}).map(([k, m]) => `<div class="gl-it"><div class="xs muted">${k === "US" ? "미국" : "한국"}</div><b class="${m.phase === "open" ? "good" : ""}">${m.holiday ? "휴장" + (m.holiday !== "주말" ? " · " + esc(m.holiday) : "") : PHASE_KO[m.phase] || m.phase}</b><div class="xs dim">${esc(m.next)}까지 ${dur(m.seconds)}</div></div>`).join("");
  const ICON = { bad: "⛔", warn: "⚠️", info: "•" };
  box.innerHTML = `<div class="grid g-21">
    ${card(`오늘 할 일 <span class="small dim">중요한 순 · ${t.n}건</span>`, t.todo.length ? `<div class="todo">${t.todo.map((x) => `<a class="todo-it td-${esc(x.level)}" href="${esc(x.link || "#")}"><span>${ICON[x.level]}</span><div><b>${esc(x.title)}</b><div class="xs muted">${esc(x.detail || "")}</div></div></a>`).join("")}</div>` : `<div class="small good">✓ 처리할 일 없음 — 안전·데이터·이벤트·신호 모두 조용합니다</div>`, asOf(t.as_of, "fresh", "지금"))}
    ${card("시장 한눈에", `<div class="glance">${mk}
      ${g.kospi ? `<div class="gl-it"><div class="xs muted">${esc(g.kospi.name)}</div><b class="num">${num(g.kospi.last, 2)}</b><div class="xs"><span class="${g.kospi.chg >= 0 ? "up" : "down"}">${P(g.kospi.chg, 2)}</span> <span class="dim">${esc(g.kospi.as_of)}</span></div></div>` : ""}
      ${g.vkospi ? `<div class="gl-it"><div class="xs muted">${g.vkospi.proxy ? "변동성(대용)" : "VKOSPI"}</div><b class="num">${g.vkospi.level ?? "-"}</b><div class="xs dim">1년 ${R(g.vkospi.percentile_1y, 0)} 분위</div></div>` : ""}</div>
      <div class="xs dim" style="margin-top:8px">관심종목 ★ ${t.starred.length}개 · 보유 ${t.held.length}종목 · <a href="#truth">Truth Center</a> · <a href="#compare">종목 비교</a> · 단축키 <kbd>?</kbd></div>`)}
  </div>`;
}

// ------------------------------------------------------------ 종목 비교 (2~4개)
async function viewCompare(el) {
  let syms = (S.param ? decodeURIComponent(S.param).split(",") : []).filter(Boolean);
  if (syms.length < 2) {
    const star = (await api("/api/star").catch(() => ({ starred: [] }))).starred || [];
    const pool = [...syms, ...star, ...(S.data?.watchlist || []).map((w) => w.symbol)];
    syms = [...new Set(pool)].slice(0, Math.max(2, Math.min(3, pool.length)));
  }
  const pick = `<div class="cmp-pick">${syms.map((s) => `<span class="chip">${esc(s)} <a data-rm="${esc(s)}" title="빼기">✕</a></span>`).join(" ")}
    <input id="cmp-add" placeholder="코드 추가 (예: 000660, AAPL)" maxlength="12" ${syms.length >= 4 ? "disabled" : ""}><button class="btn-sm" id="cmp-go">추가</button></div>`;
  const r = syms.length >= 2 ? await api(`/api/compare?symbols=${encodeURIComponent(syms.join(","))}`) : { error: "비교할 종목을 2개 이상 고르세요" };
  if (r.error) { el.innerHTML = card("종목 비교", pick + empty(r.error)); bindCmp(syms); return; }
  const COLORS = ["#3b82f6", "#f0474f", "#22c55e", "#f59e0b"];
  const rows = r.rows;
  const line = (label, f, cls) => `<tr><td class="muted">${label}</td>${rows.map((x) => `<td class="r num ${cls ? cls(x) : ""}">${f(x)}</td>`).join("")}</tr>`;
  const sgn = (k) => (x) => x[k] == null ? "" : x[k] >= 0 ? "up" : "down";
  const table = `<table class="tight"><thead><tr><th></th>${rows.map((x, i) => `<th class="r"><a href="#analysis/${esc(x.symbol)}" style="color:${COLORS[i]}">${esc(x.name)}</a><div class="xs dim">${esc(x.symbol)}</div></th>`).join("")}</tr></thead><tbody>
    ${line("현재가", (x) => price(x.last, x.symbol))}${line("1개월", (x) => P(x.ret_1m), sgn("ret_1m"))}${line("3개월", (x) => P(x.ret_3m), sgn("ret_3m"))}${line("1년", (x) => P(x.ret_1y), sgn("ret_1y"))}
    ${line("연 변동성", (x) => R(x.vol))}${line("베타 (시장)", (x) => x.beta ?? "-")}${line("최대 낙폭", (x) => P(x.mdd), () => "down")}
    ${line("AI 신호", (x) => x.ai ? `${badge(x.ai.action)} <span class="xs dim">${R(x.ai.prob_up, 0)}</span>` : "-")}
    ${line("과거 AI 적중", (x) => x.n_scored ? `${R(x.hit, 0)} <span class="xs dim">n=${x.n_scored}</span>` : "-")}
    ${line("PER", (x) => x.per ? num(x.per, 1) : "-")}${line("PBR", (x) => x.pbr ? num(x.pbr, 2) : "-")}${line("배당수익률", (x) => x.div_yield ? R(x.div_yield, 2) : "-")}
    ${line("기준 시각", (x) => `<span class="xs dim">${esc(x.as_of || "")}</span>`)}</tbody></table>`;
  const corr = `<table class="tight"><thead><tr><th></th>${r.symbols.map((s) => `<th class="r">${esc(s)}</th>`).join("")}</tr></thead><tbody>${r.symbols.map((a) => `<tr><td>${esc(a)}</td>${r.symbols.map((b) => { const v = r.corr[a][b]; return `<td class="r num" style="background:rgba(59,130,246,${a === b ? 0 : Math.max(v, 0) * 0.45})">${v.toFixed(2)}</td>`; }).join("")}</tr>`).join("")}</tbody></table>`;
  el.innerHTML = `${card("종목 비교 <span class='small dim'>2~4개 · 같은 기간 100 기준</span>", pick + `<div id="cmp-chart" class="chart" style="margin-top:10px"></div><div class="xs dim">${esc(r.from)} ~ ${esc(r.to)}${r.missing.length ? ` · 일봉 없음: ${r.missing.map(esc).join(", ")}` : ""}</div>`)}
  <div class="grid g-21">${card("지표 · AI", `<div class="scroll">${table}</div>`)}${card("일간 수익률 상관", corr + '<div class="xs dim" style="margin-top:6px">상관이 높으면(0.7+) 같이 사도 분산 효과가 작습니다</div>')}</div>`;
  lineChart($("#cmp-chart"), r.symbols.map((s, i) => ({ data: r.series[s], color: COLORS[i], title: s })));
  bindCmp(syms);
}

function bindCmp(syms) {
  const go = (list) => { location.hash = `#compare/${list.join(",")}`; };
  document.querySelectorAll("[data-rm]").forEach((a) => a.onclick = () => go(syms.filter((s) => s !== a.dataset.rm)));
  const add = () => { const v = ($("#cmp-add").value || "").trim().toUpperCase(); if (v && !syms.includes(v)) go([...syms, v].slice(0, 4)); };
  if ($("#cmp-go")) $("#cmp-go").onclick = add;
  if ($("#cmp-add")) $("#cmp-add").onkeydown = (e) => { if (e.key === "Enter") add(); };
}

// ------------------------------------------------------------ 최근 검색
function recentSearches() { try { return JSON.parse(safeGet("qa_recent") || "[]"); } catch { return []; } }
function pushRecent(sym) {
  if (!sym) return;
  const name = (S.data?.all_symbols || []).find((w) => w.symbol === sym)?.name || sym;
  const list = [{ symbol: sym, name }, ...recentSearches().filter((x) => x.symbol !== sym)].slice(0, 10);
  safeSet("qa_recent", JSON.stringify(list));
}
function initRecentSearch() {
  const input = $("#search"), box = $("#search-results");
  if (!input || !box) return;
  input.addEventListener("focus", () => {
    if (input.value.trim()) return;
    const list = recentSearches();
    if (!list.length) return;
    box.innerHTML = `<div class="xs muted" style="padding:6px 10px">최근 본 종목 <a id="recent-clear" class="xs" style="float:right">지우기</a></div>` + list.map((w) => `<a data-sym="${esc(w.symbol)}"><span>${stockLogo(w.symbol, w.name, 22)} 🕘 ${esc(w.name)} <span class="dim small">${esc(w.symbol)}</span></span></a>`).join("");
    box.classList.add("open");
    box.querySelectorAll("a[data-sym]").forEach((a) => a.onclick = () => { box.classList.remove("open"); location.hash = `#analysis/${a.dataset.sym}`; });
    $("#recent-clear").onclick = (e) => { e.stopPropagation(); safeSet("qa_recent", "[]"); box.classList.remove("open"); };
  });
  window.addEventListener("hashchange", () => { const m = location.hash.match(/^#analysis\/([^/]+)/); if (m) pushRecent(decodeURIComponent(m[1])); });
}

// ------------------------------------------------------------ 키보드 단축키
const KEYS = [["/", "종목 검색"], ["g h", "홈"], ["g o", "오늘 할 일 (Action Center)"], ["g m", "증시 지도"], ["g w", "관심종목"], ["g s", "AI 성적표"], ["g t", "Truth Center"], ["g r", "매매 준비"], ["g p", "Portfolio OS"], ["g k", "리스크"], ["g e", "이벤트 캘린더"], ["g c", "종목 비교"], ["g a", "종목 (AI 분석)"], ["g n", "뉴스 · 공시"], ["g d", "데이터 건강"], ["s", "관심종목 별표 (종목 화면)"], ["d", "다크/라이트 전환"], ["?", "단축키 도움말"], ["Esc", "닫기"]];
const GO = { h: "#dashboard", o: "#action", m: "#map", w: "#watch", s: "#scorecard", t: "#truth", r: "#readiness", p: "#pos", k: "#risk", e: "#calendar", c: "#compare", a: "#analysis", n: "#news", d: "#datahealth" };
function initShortcuts() {
  let g = 0;
  document.addEventListener("keydown", (e) => {
    const tag = (e.target.tagName || "").toLowerCase();
    if (e.key === "Escape") { $("#kbd-help")?.remove(); $("#search-results")?.classList.remove("open"); if (tag === "input") e.target.blur(); return; }
    if (tag === "input" || tag === "textarea" || tag === "select" || e.target.isContentEditable || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === "/") { e.preventDefault(); $("#search")?.focus(); return; }
    if (e.key === "?") { showKeyHelp(); return; }
    if (g && Date.now() - g < 1200 && GO[e.key]) { location.hash = GO[e.key]; g = 0; return; }
    g = e.key === "g" ? Date.now() : 0;
    if (e.key === "d") $("#theme-btn")?.click();
    if (e.key === "s" && S.view === "analysis") { const b = $("#star-btn"); if (b) b.click(); }
  });
}
function showKeyHelp() {
  if ($("#kbd-help")) { $("#kbd-help").remove(); return; }
  const d = document.createElement("div");
  d.id = "kbd-help";
  d.className = "kbd-help";
  d.innerHTML = `<div class="card"><div class="card-h"><h3>키보드 단축키</h3><div class="right"><button class="btn-sm" id="kbd-x">닫기</button></div></div><table class="tight">${KEYS.map(([k, v]) => `<tr><td>${k.split(" ").map((x) => `<kbd>${esc(x)}</kbd>`).join(" ")}</td><td>${esc(v)}</td></tr>`).join("")}</table></div>`;
  d.onclick = (e) => { if (e.target === d || e.target.id === "kbd-x") d.remove(); };
  document.body.appendChild(d);
}

function initV14() {
  initMarketClock();
  initRecentSearch();
  initShortcuts();
}
