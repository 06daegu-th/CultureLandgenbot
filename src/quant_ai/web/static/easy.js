/* v19: 쉬운 화면 — 메뉴 6개(나머지는 '고급'으로 접힘) · 처음 쓰는 사람 안내 · 홈 맨 위(오늘 할 일 3개 · AI 믿을 만한가) · 서버 연결 실패 화면 */
"use strict";

// 쉬운 화면에서 보이는 메뉴 6개 (나머지는 '고급 메뉴'에 접혀 있다)
// v25: 토스식 메뉴 — 홈 · 관심종목 · 포트폴리오 · 시장 · 뉴스/공시 · 일정 · AI 분석 · 알림 · 더보기 (종목은 검색·목록에서 바로)
const NAV_EASY = [["dashboard", "home", "홈"], ["watch", "star", "관심종목"], ["pos", "portfolio", "포트폴리오"], ["market", "market", "시장"],
  ["news", "news", "뉴스 · 공시"], ["calendar", "calendar", "일정"], ["picks", "ai", "AI 추천"], ["report", "ai", "AI 분석"], ["alerts", "bell", "알림 설정"], ["more", "grid", "더보기"]];

function uiMode() { return S.uiMode || safeGet("qa_ui") || "easy"; }
function setUiMode(m) {
  S.uiMode = m; safeSet("qa_ui", m);
  post("/api/prefs", { ui: { mode: m } }).then(() => { if (S.prefs) S.prefs.ui = { ...(S.prefs.ui || {}), mode: m }; }).catch(() => {});
  document.body.classList.toggle("ui-easy", m === "easy");
  buildNav(); render();
}

// ------------------------------------------------------------ 처음 쓰는 사람 안내 (① 데이터 ② 투자 한도 ③ 관심종목 3개 ④ 오늘 할 일)
function guideAct(x) {
  if (x.key === "data") return `<div class="sg-cmd"><code>./run.sh</code><button class="btn-sm" data-copy="./run.sh">복사</button><span class="xs muted">터미널에 붙여 넣고 Enter · 다 받으면 이 화면이 저절로 바뀝니다</span></div>`;
  if (x.key === "budget") return `<a class="btn-sm primary" href="#budget">투자 한도 정하기 →</a>`;
  if (x.key === "watch") return `<button class="btn-sm primary" data-focus-search>종목 검색하기 ( / )</button>`;
  return `<a class="btn-sm primary" href="#action">오늘 할 일 보기 →</a>`;
}
function guideCard(g) {
  const step = (x) => {
    const cur = x.key === g.next;
    const act = !x.done && cur ? guideAct(x) : "";
    return `<div class="sg-step ${x.done ? "done" : cur ? "cur" : ""}"><span class="sg-n">${x.done ? "✓" : x.n}</span>
      <div class="sg-body"><b>${esc(x.title)}</b>${x.detail ? ` <span class="xs muted">${esc(x.detail)}</span>` : ""}
      ${cur || !x.done ? `<div class="small muted">${esc(x.how)}</div>` : ""}${act}</div></div>`;
  };
  const opt = (g.optional || []).filter((o) => !o.done).map((o) => `<div class="xs muted">선택: <a href="${esc(o.link)}">${esc(o.title)}</a> — ${esc(o.how)}</div>`).join("");
  // v21: 화면 한가득 차지하던 4단계 안내 → '다음 할 것' 한 줄 + 진행 막대 (단계 전체는 펼쳐서)
  const cur = g.steps.find((x) => x.key === g.next) || g.steps.find((x) => !x.done) || g.steps[0];
  return `<div class="card start-guide sg-compact"><div class="sg-line">
      <span class="sg-n cur">${cur.n}</span><div class="sg-body"><span class="xs muted">시작하기 ${g.progress}/3 · 다음 할 것</span><b>${esc(cur.title)}</b><span class="small muted">${esc(cur.how)}</span>${cur.done ? "" : guideAct(cur)}</div>
      <div class="sg-side"><div class="sg-bar"><i style="width:${Math.round(g.progress / 3 * 100)}%"></i></div>
        <details class="sg-more"><summary class="xs">단계 전체 보기</summary><div class="sg-steps">${g.steps.map(step).join("")}</div>${opt}</details>
        <button class="btn-sm" id="sg-hide" title="다시 보려면 설정 → 화면">숨기기</button></div></div></div>`;
}

function bindGuide(root) {
  root.querySelectorAll("[data-copy]").forEach((b) => b.onclick = () => {
    navigator.clipboard?.writeText(b.dataset.copy).then(() => { b.textContent = "복사됨"; }).catch(() => { b.textContent = "직접 입력하세요"; });
  });
  root.querySelectorAll("[data-focus-search]").forEach((b) => b.onclick = () => { const i = $("#search"); i.focus(); i.scrollIntoView({ block: "center" }); });
  const h = root.querySelector("#sg-hide");
  if (h) h.onclick = async () => {
    await post("/api/prefs", { ui: { guide_hidden: true } }).catch(() => {});
    if (S.prefs) S.prefs.ui = { ...(S.prefs.ui || {}), guide_hidden: true };
    root.querySelector(".start-guide")?.remove();
  };
}

// ------------------------------------------------------------ 홈 맨 위: 오늘 내가 할 일 3개 · AI 믿을 만한가 · 시장 · 자산 · 뉴스
async function homeTop(root) {
  if (!root) return;
  const box = document.createElement("div");
  box.className = "home5";
  box.innerHTML = `<div class="h5-grid">${'<div class="h5 card"><div class="skel" style="height:90px"></div></div>'.repeat(3)}</div>`;
  root.prepend(box);
  const q = typeof pfModeQ === "function" ? pfModeQ() : "";
  const [h, g, p] = await Promise.all([api(`/api/home5?${q}`).catch((e) => ({ error: e.message })), api("/api/start-guide").catch(() => null),
    loadPrefs().catch(() => ({}))]);
  if (h.error) { box.innerHTML = card("홈", `<div class="veto">${esc(h.error)}</div>`); return; }
  const LV = { ok: "good", good: "good", warn: "warn", bad: "bad" };
  const err = (x) => x?.error ? `<div class="xs dim">불러오지 못함 — ${esc(x.error)}</div>` : "";
  const m = h.market || {}, a = h.assets || {}, ai = h.ai || {}, nw = h.news || {}, td = h.todo || {};
  // v21: 할 일 아이콘은 이모지 대신 같은 선 아이콘 세트로
  const TD_IC = { halt: "stop", kill: "stop", ops_scheduler: "ops", ops_data: "data", ops_news: "news", ops_sector: "data", thesis: "risk",
    event: "calendar", ai: "ai", signal: "ai", disclosure: "news", macro: "market", market: "market", move: "pulse", start: "check" };
  const todo = td.error ? err(td) : (td.items || []).length
    ? td.items.map((t) => `<a class="td-it" href="${esc(t.link)}">${t.symbol ? stockLogo(t.symbol, "", 28) : `<span class="td-ic">${ICONS[TD_IC[t.kind] || "bell"] || ""}</span>`}
        <span class="td-tx"><b>${esc(koText(t.text))}</b>${t.why ? `<span class="xs muted">${esc(t.why)}</span>` : ""}</span><span class="td-go">›</span></a>`).join("")
      + (td.more ? `<a class="xs" href="#action">${td.more}개 더 보기 →</a>` : "")
    : g && !g.done ? `<div class="td-empty"><div class="small">시작하기를 마치면 여기에 매일 할 일이 나옵니다</div></div>`
    : td.empty_hint ? `<div class="td-empty"><div class="small">${esc(td.empty_hint)}</div><button class="btn-sm primary" data-focus-search>종목 검색 ( / )</button></div>`
      : `<div class="td-empty"><div class="small">${esc(td.calm || "오늘 꼭 할 일은 없습니다")}</div></div>`;
  const ez = ai.easy || {};
  const AI_LV = { verified: "good", checking: "warn", banned: "bad" };
  const aiB = ai.error ? err(ai) : `
    <div class="h5-big">${lvDot(AI_LV[ai.key])}${esc(ai.label || "")}</div>
    <div class="xs muted">${esc(ai.why || "")}</div>
    <div class="h5-kv">
      ${ez.n ? `<div><span>방향 적중</span><b class="num">${ez.hits}/${ez.n}</b><em>그냥 '오른다'면 ${ez.base_hits}</em></div>` : '<div><span>채점된 기록</span><b>아직 부족</b></div>'}
      ${ez.excess != null ? `<div><span>그대로 샀다면</span><b class="num ${ez.excess >= 0 ? "up" : "down"}">지수 ${ez.excess >= 0 ? "+" : ""}${(ez.excess * 100).toFixed(1)}%p</b><em>비용 뺀 뒤</em></div>` : ""}
      ${ez.alpha ? `<div><span>시장 대비 적중</span><b class="num ${ez.alpha.proven ? "up" : ez.alpha.worse ? "down" : ""}">${Math.round(ez.alpha.hit * 100)}%</b><em>기준 ${Math.round(Math.max(0.5, ez.alpha.base) * 100)}%</em></div>` : ""}
    </div>
    <a class="xs" href="#aitrust">AI 신뢰 센터 →</a>`;
  const mk = m.error ? err(m) : `
    ${(m.markets || []).map((x) => `<div class="small">${esc(x.name)} ${dotText(`${x.light || ""} ${x.state || ""}`)}</div>`).join("")}
    ${m.index ? `<div class="small">${esc(m.index.name)} <b class="num ${m.index.chg >= 0 ? "up" : "down"}">${P(m.index.chg, 2)}</b> <span class="xs dim">${esc(m.index.date)}</span></div>` : ""}
    ${m.mood ? `<div class="small">${lvDot(LV[m.mood.level])}${esc(koText(m.mood.text))}</div>` : ""}
    ${m.event ? `<a class="small" href="${esc(m.event.link || "#calendar")}">${lvDot(LV[m.event.level] || "idle")}${esc(koText(m.event.text))}</a>` : ""}`;
  const MODE_KO = { paper: "모의투자", live: "실계좌", shadow: "그림자 매매" };
  const hero = a.error ? `<div class="card home-hero">${err(a)}</div>` : `<div class="card home-hero">
    <div class="hh-main"><div class="xs muted">내 자산 · ${esc(MODE_KO[a.mode] || a.mode || "")}</div>
      <div class="hh-big num">${moneyShort(a.equity, "KRW")}</div>
      <div class="small">${a.pnl_pct == null ? "" : `<b class="num ${a.pnl_pct >= 0 ? "up" : "down"}">${P(a.pnl_pct, 1)}</b> <span class="muted">${a.paid_in ? "낸 돈 대비" : "시작 대비"}</span> · `}${a.n}종목 · 현금 ${moneyShort(a.cash, "KRW")}</div>
      ${sparkSvg(a.spark)}
      ${a.risk?.headline ? `<div class="xs">${lvDot(LV[a.risk.level])}${esc(a.risk.headline)}</div>` : ""}
      <a class="xs" href="#pos">자산 자세히 →</a></div>
    <a class="hh-goal" href="#goal">${(h.goal || {}).set ? `<div class="xs muted">내 목표</div>${goalBar(h.goal)}`
      : `<div class="xs muted">내 목표</div><div class="b">아직 없음</div><div class="small muted">목표 금액과 매달 넣을 돈을 정하면 '몇 년 안에 몇 % 확률'로 보여 드립니다</div><span class="btn-sm primary" style="align-self:flex-start;margin-top:6px">목표 정하기</span>`}</a></div>`;
  const nwB = nw.error ? err(nw) : (nw.items || []).length ? nw.items.map((n) => `<div class="h5-news" data-news="${esc(n.id)}" role="button" tabindex="0">
      ${dotText(n.level?.icon || "⚪")} <span class="small">${esc(n.title)}</span>
      <div class="xs dim">${(n.symbols || []).map((x) => `${stockLogo(x.symbol, x.name, 14)} ${esc(x.name)}`).join(" · ")}${n.first ? ` · ${esc(n.first)}` : ""}</div></div>`).join("")
      + `<a class="xs" href="#news">뉴스 ${nw.n}건 모두 →</a>` : `<div class="small muted">최근 2일 저장된 뉴스가 없습니다</div><div class="xs dim">뉴스는 24시간 운영이 켜져 있을 때 모입니다</div>`;
  const showGuide = g && !g.done && !(p?.ui?.guide_hidden);
  // v23 홈 순서: 시장 → 오늘 확인할 것 · 주의할 것 → 내 포트폴리오 · AI 상태 → 시장 핵심 · 관심종목 → 오늘의 결론 → 뉴스
  const sys = Array.isArray(h.system) ? h.system : [];
  const sysLine = sys.length ? `<details class="sys-line"><summary>${lvDot(sys.some((x) => x.level === "bad") ? "bad" : "warn")}시스템 점검 필요 ${sys.length}건 <span class="xs muted">— 투자 판단과는 별개 · 눌러서 해결 방법</span></summary>
      ${sys.map((x) => `<div class="small">${esc(x.text)}<div class="xs muted">${esc(x.fix)}</div></div>`).join("")}</details>` : "";
  const cd = (sec) => { if (sec == null) return ""; const d = Math.floor(sec / 86400), hh = Math.floor((sec % 86400) / 3600), mm = Math.floor((sec % 3600) / 60);
    return d ? `${d}일 ${hh}시간` : `${hh}:${String(mm).padStart(2, "0")}`; };
  const mkt = m.error ? err(m) : `<div class="mkt-strip">${(m.markets || []).map((x) => `<div class="mkt-it"><span class="mkt-n">${esc(x.name)}</span>
      <span class="mkt-s ${x.state === "장중" ? "open" : ""}">${x.state === "장중" ? '<i class="live-dot"></i>' : ""}${esc(x.state || "")}${x.holiday ? ` · ${esc(x.holiday)}` : ""}</span>
      ${x.next_kst ? `<span class="xs dim" data-cd="${x.seconds_to_next ?? ""}">${esc(x.next_event === "개장" ? "개장까지" : "마감까지")} <b class="num">${cd(x.seconds_to_next)}</b> · ${esc(x.next_kst)}</span>` : ""}
      ${x.dst != null ? `<span class="xs dim">${x.dst ? "서머타임" : "표준시"}</span>` : ""}</div>`).join("")}
    ${m.index ? `<div class="mkt-it"><span class="mkt-n">${esc(m.index.name)}</span><span class="num ${m.index.chg >= 0 ? "up" : "down"}">${P(m.index.chg, 2)}</span><span class="xs dim">${esc(m.index.date)}</span></div>` : ""}</div>`;
  const dh = h.data || {};
  const dataLine = dh.overall == null ? "" : `<a class="data-line" href="#datahealth"><span class="xs muted">데이터 상태</span> <b class="num">${Math.round(dh.overall)}</b><span class="xs dim">/100</span>
      ${(dh.items || []).filter((x) => x.status !== "na").map((x) => `<span class="xs">${lvDot({ ok: "good", warn: "warn", bad: "bad" }[x.status] || "idle")}${esc(x.name)} ${esc(x.label)}</span>`).join("")}</a>`;
  const cau = Array.isArray(h.caution) ? h.caution : [];
  const cauB = h.caution?.error ? err(h.caution) : cau.length ? cau.map((c) => `<a class="td-it" href="${esc(c.link)}"><span class="td-ic">${lvDot(c.level === "bad" ? "bad" : "warn")}</span>
      <span class="td-tx"><b>${esc(c.text)}</b>${c.why ? `<span class="xs muted">${esc(c.why)}</span>` : ""}</span><span class="td-go">›</span></a>`).join("")
    : '<div class="td-empty"><div class="small">특별히 조심할 것은 없습니다</div></div>';
  const ACT_TXT = (r) => r.ai ? `${koAct(r.ai === "NO_TRADE" ? "NO TRADE" : r.ai)}${r.prob_up != null && r.ai !== "NO_TRADE" ? ` ${Math.round(r.prob_up * 100)}%` : ""}` : "판단 없음";
  const wl = Array.isArray(h.watch) ? h.watch : [];
  // v24 토스식 목록 한 줄: [로고 · 이름 / AI 판단]  ······  [가격 / 등락]
  const wlB = h.watch?.error ? err(h.watch) : wl.length ? `<div class="wl-mini">${wl.map((r) => `<a class="wl-row t-row" href="#analysis/${esc(r.symbol)}">
      ${coId(r.symbol, r.name, { size: 36, link: false, tail: (r.held ? " · 보유" : "") + ` · <span class="act-t act-${esc(r.ai || "none")}">${esc(ACT_TXT(r))}</span>` })}
      <span class="wl-r"><b class="num t-px">${r.last == null ? "-" : esc(price(r.last, r.symbol))}</b>${r.chg_pct != null ? `<span class="num t-chg ${r.chg_pct >= 0 ? "up" : "down"}">${P(r.chg_pct, 2)}</span>` : ""}</span></a>`).join("")}</div>
      <a class="xs" href="#watch">관심종목 전체 →</a>`
    : '<div class="td-empty"><div class="small">관심종목이 없습니다 — 종목을 검색해 ☆ 를 누르세요</div><button class="btn-sm" data-focus-search>종목 검색 ( / )</button></div>';
  const core = h.core || {};
  const coreB = core.error ? err(core) : (core.lines || []).length ? `<ul class="core-l">${core.lines.map((x) => `<li><b class="${x.dir > 0 ? "up" : "down"}">${x.dir > 0 ? "▲" : "▼"}</b> <b>${esc(x.text)}</b><span class="xs muted">${esc(x.detail)}</span></li>`).join("")}</ul>
      ${core.as_of ? `<div class="xs dim">경제지표 기준 ${esc(core.as_of)}</div>` : ""}` : `<div class="small muted">${esc(core.hint || "")}</div>`;
  const STAGE_KO = { BACKTEST: "과거 시험만", SHADOW: "그림자 기록 (주문 없음)", PAPER: "모의투자", LIVE: "소액 실전" };
  const aiTop = ai.error ? "" : `<div class="ai-stage xs"><span class="stage-tag">${esc(ai.stage || "")}</span> ${esc(STAGE_KO[ai.stage] || "")}</div>`;
  const up = h.upcoming || {};
  const KIND_DOT = { earnings: "earn", ex_div: "div", div_pay: "div", holiday: "off", half_day: "off", fomc: "macro", cpi: "macro", nfp: "macro", pce: "macro", bok: "macro" };
  const upB = (up.rows || []).length ? `<div class="up-strip">${up.rows.map((e) => `<a class="up-it" href="${e.symbol ? `#analysis/${esc(e.symbol)}` : "#calendar"}">
      <b class="up-d ${e.d_day <= 1 ? "soon" : ""}">${esc(e.d_label)}</b>${calDot(e.kind)}<span class="up-t">${e.symbol ? stockLogo(e.symbol, "", 16) + " " : e.market ? `<span class="flag cc">${esc(e.market)}</span>` : ""}${esc(e.title)}${e.estimated ? '<span class="xs dim"> 추정</span>' : ""}</span></a>`).join("")}
      ${up.more ? `<a class="up-it more" href="#calendar">+${up.more}</a>` : ""}</div>` : "";
  const cc = h.conclusion || {};
  box.innerHTML = `${showGuide ? guideCard(g) : ""}
  <div class="card h5-mkt"><div class="h5-h">시장</div>${mkt}${dataLine}${sysLine}</div>
  ${upB ? `<div class="card h5-up"><div class="h5-h">다가오는 일정 <a class="xs" href="#calendar">전체 일정 →</a></div>${upB}</div>` : ""}
  <div class="h5-grid two">
    <div class="h5 card h5-todo"><div class="h5-h">오늘 확인할 것 <span class="xs dim">${esc(td.as_of || "")}</span></div>${todo}</div>
    <div class="h5 card h5-todo"><div class="h5-h">오늘 주의할 것</div>${cauB}</div>
  </div>
  ${hero}
  <div class="h5-grid two">
    <div class="h5 card"><div class="h5-h">관심종목 — AI 판단</div>${wlB}</div>
    <div class="h5 card h5-ai ai-${esc(ai.key || "")}"><div class="h5-h">AI 상태 — 지금 믿을 만한가</div>${aiTop}${aiB}</div>
  </div>
  <div class="h5-grid two">
    <div class="h5 card"><div class="h5-h">시장 핵심 <span class="xs dim">최근 5거래일</span></div>${coreB}</div>
    <div class="h5 card"><div class="h5-h">중요한 뉴스</div>${nwB}</div>
  </div>
  ${cc.text ? `<div class="card concl concl-${esc(cc.level || "ok")}"><div class="h5-h">오늘의 결론</div><div class="concl-t">${esc(cc.text)}</div></div>` : ""}`;
  bindGuide(box);
  box.querySelectorAll("[data-focus-search]").forEach((b) => b.onclick = () => $("#search").focus());
}

// 쉬운 화면의 홈: 위 5칸만 · 나머지는 버튼으로
async function homeEasy(el) {
  el.innerHTML = `<div class="home-more"><button class="btn-sm" id="home-more">자세한 홈 화면 보기 (전문가용) ▾</button>
    <span class="xs dim">AI 브리핑 · 매매 준비 · 시장 체크리스트 · 관심종목 표 등</span></div>`;
  $("#home-more", el).onclick = () => { S.homeDetail = true; render(); };
  await homeTop(el);
}

// ------------------------------------------------------------ 서버 연결 실패 화면 (작은 '연결 실패' 표시 대신)
let _offTimer = null;
function isNetErr(e) { return e instanceof TypeError || /Failed to fetch|NetworkError|Load failed| 50[234]$/.test(String(e?.message || e)); }
function showOffline(e) {
  if (document.querySelector(".offline")) return;
  const d = document.createElement("div");
  d.className = "offline";
  d.setAttribute("role", "alertdialog");
  d.innerHTML = `<div class="off-box"><div class="off-ic">🔌</div><h2>서버에 연결할 수 없습니다</h2>
    <p>Quant AI 서버가 꺼져 있거나 다시 시작하는 중입니다. 화면은 서버가 켜져야 채워집니다.</p>
    <ol><li>터미널에서 이 프로그램 폴더로 이동</li><li><code>./run.sh</code> 실행 (대시보드만 볼 때는 <code>./run.sh serve</code>)</li>
      <li>다른 PC·휴대폰이면 주소와 포트(기본 8050)를 확인</li></ol>
    <div class="off-act"><button class="btn-sm primary" id="off-retry">다시 연결</button><span class="small muted" id="off-msg">5초마다 자동으로 다시 시도합니다</span></div>
    <div class="xs dim">${esc(String(e?.message || e || ""))}</div></div>`;
  document.body.appendChild(d);
  let n = 0;
  const tryNow = async () => {
    n += 1;
    $("#off-msg").textContent = `연결 시도 ${n}번째…`;
    try {
      const r = await fetch("/api/health", { cache: "no-store" });
      if (!r.ok) throw new Error(String(r.status));
      clearInterval(_offTimer); _offTimer = null;
      d.remove();
      await refresh().catch(() => {});
      route();
    } catch { $("#off-msg").textContent = `아직 연결 안 됨 (${n}번 시도) — 5초 뒤 다시`; }
  };
  $("#off-retry").onclick = tryNow;
  _offTimer = setInterval(tryNow, 5000);
}

// ------------------------------------------------------------ 설정 → 화면 (쉬운/전체 · 시작 안내 다시 보기)
function uiSettingsCard() {
  const easy = uiMode() === "easy";
  return card("화면", `<div class="small">지금: <b>${easy ? "쉬운 화면" : "전체 화면"}</b> — ${easy ? "자주 쓰는 메뉴 6개 · 홈은 맨 위 5칸만" : "모든 메뉴 · 자세한 홈"}</div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:8px"><button class="btn-sm ${easy ? "primary" : ""}" data-set-ui="easy">쉬운 화면</button>
    <button class="btn-sm ${easy ? "" : "primary"}" data-set-ui="pro">전체 화면</button><button class="btn-sm" id="guide-show">'처음이라면' 안내 다시 보기</button></div>`);
}
function bindUiSettings() {
  document.querySelectorAll("[data-set-ui]").forEach((b) => b.onclick = () => setUiMode(b.dataset.setUi));
  const g = $("#guide-show");
  if (g) g.onclick = async () => {
    await post("/api/prefs", { ui: { guide_hidden: false } }).catch(() => {});
    if (S.prefs) S.prefs.ui = { ...(S.prefs.ui || {}), guide_hidden: false };
    location.hash = "#dashboard";
  };
}

// ------------------------------------------------------------ 종목 첫 화면 (토스처럼): 장 상태 · 52주 위치 · AI 최종 판단 하나
const VIEW_CLS = { BUY: "up", SELL: "down", HOLD: "", "NO TRADE": "dim", 기권: "dim", 통과: "" };  // v23: 매수 빨강 · 매도 파랑 · 관망 중립
async function stockTop(sym) {
  const box = $("#pf-top");
  if (!box) return;
  let v;
  try { v = await api(`/api/verdict?symbol=${encodeURIComponent(sym)}`); } catch (e) { box.innerHTML = `<div class="xs dim">AI 판단 불러오기 실패 — ${esc(e.message)}</div>`; return; }
  if ($("#pf-top") !== box) return;  // 그 사이 다른 종목으로 이동
  const m = v.market || {};
  const r = v.range52;
  const mk = `<div class="vt-mkt"><b>${esc(m.name || "")}</b> ${dotText(`${m.light || ""} ${m.state || ""}`)}
    ${m.local_time ? `<span class="xs dim">· 현지 ${esc(String(m.local_time).slice(11, 16) || m.local_time)}</span>` : ""}
    ${m.next_kst ? `<span class="xs dim">· 다음 ${esc(m.next === "폐장" ? "마감" : "개장")} ${esc(m.next_kst)}</span>` : ""}
    ${m.notice && !($("#market-notice")?.textContent || "").includes(m.notice.slice(0, 12)) ? `<div class="xs warn-t">${esc(m.notice)}</div>` : ""}</div>`;
  const r52 = r && r.pos != null ? `<div class="vt-52"><span class="xs muted">52주</span>
    <span class="xs num">${esc(price(r.low, sym))}</span><span class="r52-bar"><i style="left:${Math.round(r.pos * 100)}%"></i></span><span class="xs num">${esc(price(r.high, sym))}</span>
    <span class="xs ${r.from_high > -0.05 ? "up" : r.from_high < -0.3 ? "down" : ""}">고점 대비 ${P(r.from_high, 1)}</span></div>` : "";
  let ai;
  if (!v.final) {
    ai = `<div class="vt-card vt-none"><div class="xs muted">AI 의견</div><div class="vt-big">${lvDot("idle")}아직 AI 의견 없음</div><div class="small muted">${esc(v.why_none || "")}</div></div>`;
  } else {
    const votes = (v.votes || []).map((x) => `<span class="vt-vote" title="${esc(x.label)} · ${esc(koText(x.summary || ""))}"><span class="xs muted">${esc(koRole(x.role))}</span> <b class="${VIEW_CLS[x.view] || ""}">${esc(koAct(x.view))}</b></span>`).join("");
    const li = (xs, empty) => xs.length ? `<ol class="vt-ol">${xs.slice(0, 3).map((x) => `<li>${esc(koText(x))}</li>`).join("")}</ol>` : `<div class="xs dim">${empty}</div>`;  // v23: 이유는 3개만
    const u = v.used || {};
    const banned = v.trust?.key === "banned";
    const prob = v.final !== "NO_TRADE" && v.prob_up != null ? `<span class="vt-p">오를 확률 <b>${Math.round(v.prob_up * 100)}%</b></span>` : "";
    ai = `<div class="vt-card fin-${esc(v.final)} ${banned ? "vt-muted" : ""}">
      <div class="vt-row"><span class="xs muted">AI 의견 · ${v.horizon}거래일 뒤 기준 · ${esc(v.as_of)} 판단${u.sealed ? " · 기록 봉인됨" : ""}</span>
        ${v.trust ? `<a class="xs" href="#scorecard" title="AI 전체 성적">AI 성적 ${dotText(v.trust.icon)} ${esc(v.trust.label)}</a>` : ""}</div>
      ${banned ? '<div class="vt-note small">AI 성적이 아직 기준에 못 미칩니다. 아래 의견은 참고만 하세요 — 주문에는 쓰이지 않습니다.</div>' : ""}
      <div class="vt-big">${lvDot(ACT_LV[v.final])}${esc(koAct(v.final))} ${prob}
        ${v.expected_return != null && v.final !== "NO_TRADE" ? `<span class="small ${v.expected_return >= 0 ? "up" : "down"}">예상 ${P(v.expected_return, 1)}</span>` : ""}
        ${v.changed_by_gate ? `<span class="xs dim">원래 의견 ${esc(koAct(v.raw))} → 조건이 안 맞아 쉬어가기</span>` : ""}</div>
      <div class="vt-votes">${votes}<span class="vt-arrow">→</span><span class="vt-vote"><span class="xs muted">최종</span> <b class="${VIEW_CLS[v.label] || ""}">${esc(koAct(v.label))}</b></span></div>
      ${v.warn ? `<div class="vt-warn small">${esc(koText(v.warn))}</div>` : ""}
      <div class="vt-why"><div><div class="xs b up">${v.prob_up >= 0.5 ? "오를 수 있는 이유" : "내릴 수 있는 이유"}</div>${li(v.why_buy || [], "뚜렷한 근거 없음")}</div>
        <div><div class="xs b down">지금 사지 않는 이유</div>${li(v.why_not || [], "막는 이유 없음")}</div></div>
      <div class="xs dim vt-used">근거 데이터 · 가격 ${esc(u.price || "-")} 종가 · 뉴스 ${u.news?.n ?? 0}건 · 공시 ${u.disclosures?.n ?? 0}건${u.macro?.n ? ` · 경제지표 ${u.macro.n}개` : ""}${u.regime ? ` · 시장 ${esc(REGIME_KO?.[u.regime] || u.regime)}` : ""}
        ${v.plan?.stop ? ` · 이 가격 아래로 가면 틀린 것: ${esc(price(v.plan.stop, sym))}` : ""}</div></div>`;
  }
  box.innerHTML = `<div class="vt">${$("#sh-meta") ? "" : mk}${r52}</div>${ai}`;  // v23: 장 상태는 머리 한 줄(sh-meta)에 — 두 번 보이지 않게
  stockMeta(sym, v);
}

// v23 종목 머리 한 줄: 장 상태 · 한국시간/현지시간(1초마다) · 실적 D-day · 내 보유 + 거래소·영문명
let _metaTimer = null;
function stockMeta(sym, v) {
  const box = $("#sh-meta");
  if (!box) return;
  const idt = v.identity || {};
  const co = $("#sh-id .co-s");
  if (co && idt.exchange) co.textContent = [sym, idt.exchange, idt.name_en && idt.name_en !== idt.name ? idt.name_en : ""].filter(Boolean).join(" · ");
  const m = v.market || {};
  const tz = m.tz || (/^\d/.test(sym) ? "Asia/Seoul" : "America/New_York");
  const t = (zone) => new Date().toLocaleTimeString("en-GB", { timeZone: zone, hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
  const e = v.earnings, h = v.holding;
  const html = () => `<span class="sm-it"><b class="${m.state === "장중" ? "open" : ""}">${m.state === "장중" ? '<i class="live-dot"></i>' : ""}${esc(m.state || "")}</b>${m.holiday ? ` · ${esc(m.holiday)}` : ""}</span>
    <span class="sm-it num">${t("Asia/Seoul")} 한국</span>${tz !== "Asia/Seoul" ? `<span class="sm-it num">${t(tz)} 현지${m.dst != null ? (m.dst ? " (서머타임)" : "") : ""}</span>` : ""}
    ${m.next_kst ? `<span class="sm-it xs dim">다음 ${esc(m.next === "폐장" ? "마감" : "개장")} ${esc(m.next_kst)}</span>` : ""}
    ${e && e.d_label ? `<span class="sm-it chip ${e.trading_days != null && e.trading_days <= 3 ? "warn" : ""}">실적 ${esc(e.d_label)}${e.estimated ? " (추정)" : ""}</span>` : ""}
    ${h ? `<span class="sm-it chip">보유 ${num(h.qty)}주 · 평균 ${esc(price(h.avg_price, sym))} · <b class="${h.pnl_pct >= 0 ? "up" : "down"}">${P(h.pnl_pct, 1)}</b></span>` : ""}`;
  box.innerHTML = html();
  clearInterval(_metaTimer);
  _metaTimer = setInterval(() => { if (!document.body.contains(box)) { clearInterval(_metaTimer); return; } box.innerHTML = html(); }, 1000);
}

// ------------------------------------------------------------ 쉬운 말 AI 성적표 (전문 지표 위에)
async function plainScore(box, sym) {
  if (!box) return;
  let r;
  try { r = await api(`/api/ai-plain${sym ? `?symbol=${encodeURIComponent(sym)}` : ""}`); } catch (e) { box.innerHTML = ""; return; }
  if (!r.n) { box.innerHTML = card("AI 성적 — 쉽게", `<div class="small">${esc(r.headline)}</div>`); return; }
  const m = r.money;
  const fails = (r.failures || []).map((f) => `<li><a href="#analysis/${esc(f.symbol)}">${stockLogo(f.symbol, f.name, 16)} ${esc(f.name)}</a> <span class="xs dim">${esc(f.at)}</span> — ${esc(f.text)}</li>`).join("");
  const reg = (r.regimes || []).map((x) => `<span class="chip">${esc(x.regime)} ${R(x.hit, 0)} <span class="xs dim">${x.n}회</span></span>`).join(" ");
  box.innerHTML = card(`AI 성적 — 쉽게 <span class="small dim">${esc(r.from)} ~ ${esc(r.to)}${sym ? ` · ${esc(sym)}` : ""}</span>`, `
    <div class="ps-big ${r.beat_base ? "up" : "down"}">${esc(r.headline)}</div>
    <div class="small muted">${r.beat_base ? "그냥 '오른다'고만 한 것보다 많이 맞혔습니다" : "그냥 '오른다'고만 한 것보다 적게 맞혔습니다 — 아직 AI 를 믿고 거래할 단계가 아닙니다"}</div>
    ${m ? `<div class="ps-money ${m.earned ? "up" : m.earned === false ? "down" : ""}">${esc(koText(m.text))}</div>
      <div class="xs dim">${m.earned ? "지수를 이겼습니다 (비용 뒤)" : m.earned === false ? "지수에 졌습니다 — 그냥 지수를 산 것보다 못했습니다 (주가가 올랐어도 AI 가 돈을 번 것은 아님)" : ""}</div>` : ""}
    ${r.alpha ? `<div class="small ${r.alpha.beat_base ? "up" : "down"}" style="margin-top:6px">${esc(r.alpha.text)}</div>
      <div class="xs dim">시장이 다 같이 오를 때 '오른다'고 하면 쉽게 맞습니다 — 그래서 '지수보다 더 오를지'로 다시 채점한 것이 진짜 실력입니다</div>` : ""}
    ${fails ? `<div class="small b" style="margin-top:10px">크게 틀린 사례</div><ul class="ps-fail">${fails}</ul>` : ""}
    ${reg ? `<div class="small b" style="margin-top:8px">시장 분위기별 적중</div><div>${reg}</div>` : ""}
    ${r.strong ? `<div class="xs">잘하는 곳: ${esc(r.strong)}</div>` : ""}${r.weak ? `<div class="xs">약한 곳: ${esc(r.weak)}</div>` : ""}
    <div class="xs dim" style="margin-top:6px">${esc(r.note)}</div>`);
}

// v20: 홈 '내 자산' 미니 차트 (최근 90일 평가금액) — 오르면 빨강, 내리면 파랑 (국내 관례)
function sparkSvg(pts) {
  if (!pts || pts.length < 2) return '<div class="xs dim">자산 추이는 하루 이상 운용하면 나옵니다</div>';
  const v = pts.map((p) => p[1]), lo = Math.min(...v), hi = Math.max(...v), W = 260, H = 46;
  const x = (i) => (i / (v.length - 1)) * W, y = (a) => hi === lo ? H / 2 : H - 3 - ((a - lo) / (hi - lo)) * (H - 6);
  const d = v.map((a, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(a).toFixed(1)}`).join("");
  const c = v[v.length - 1] >= v[0] ? "var(--up)" : "var(--down)";
  const first = pts[0][0], ch = v[v.length - 1] / v[0] - 1;
  return `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" width="100%" height="${H}" role="img" aria-label="최근 자산 추이">
    <path d="${d}L${W},${H}L0,${H}Z" fill="${c}" opacity=".08"/><path d="${d}" fill="none" stroke="${c}" stroke-width="1.8" vector-effect="non-scaling-stroke"/></svg>
    <div class="xs dim">${esc(first)} 이후 ${P(ch, 1)} (입금 포함 평가금액)</div>`;
}
