/* v19: 쉬운 화면 — 메뉴 6개(나머지는 '고급'으로 접힘) · 처음 쓰는 사람 안내 · 홈 맨 위(오늘 할 일 3개 · AI 믿을 만한가) · 서버 연결 실패 화면 */
"use strict";

// 쉬운 화면에서 보이는 메뉴 6개 (나머지는 '고급 메뉴'에 접혀 있다)
const NAV_EASY = [["dashboard", "home", "홈"], ["analysis", "ai", "종목"], ["watch", "star", "관심종목"], ["news", "news", "뉴스 · 공시"],
  ["calendar", "calendar", "일정"], ["pos", "portfolio", "내 자산"], ["goal", "target", "내 목표"]];

function uiMode() { return S.uiMode || safeGet("qa_ui") || "easy"; }
function setUiMode(m) {
  S.uiMode = m; safeSet("qa_ui", m);
  post("/api/prefs", { ui: { mode: m } }).then(() => { if (S.prefs) S.prefs.ui = { ...(S.prefs.ui || {}), mode: m }; }).catch(() => {});
  document.body.classList.toggle("ui-easy", m === "easy");
  buildNav(); render();
}

// ------------------------------------------------------------ 처음 쓰는 사람 안내 (① 데이터 ② 투자 한도 ③ 관심종목 3개 ④ 오늘 할 일)
function guideCard(g) {
  const step = (x) => {
    const cur = x.key === g.next;
    let act = "";
    if (!x.done && cur) {
      if (x.key === "data") act = `<div class="sg-cmd"><code>./run.sh</code><button class="btn-sm" data-copy="./run.sh">복사</button><span class="xs muted">터미널에 붙여 넣고 Enter · 다 받으면 이 화면이 저절로 바뀝니다</span></div>`;
      else if (x.key === "budget") act = `<a class="btn-sm primary" href="#budget">투자 한도 정하기 →</a>`;
      else if (x.key === "watch") act = `<button class="btn-sm primary" data-focus-search>종목 검색하기 ( / )</button>`;
      else act = `<a class="btn-sm primary" href="#action">오늘 할 일 보기 →</a>`;
    }
    return `<div class="sg-step ${x.done ? "done" : cur ? "cur" : ""}"><span class="sg-n">${x.done ? "✓" : x.n}</span>
      <div class="sg-body"><b>${esc(x.title)}</b>${x.detail ? ` <span class="xs muted">${esc(x.detail)}</span>` : ""}
      ${cur || !x.done ? `<div class="small muted">${esc(x.how)}</div>` : ""}${act}</div></div>`;
  };
  const opt = (g.optional || []).filter((o) => !o.done).map((o) => `<div class="xs muted">○ <a href="${esc(o.link)}">${esc(o.title)}</a> — ${esc(o.how)}</div>`).join("");
  return `<div class="card start-guide"><div class="card-h"><h3>처음이라면 이 순서로 <span class="small dim">${g.progress}/3 완료</span></h3>
      <div class="right"><button class="btn-sm" id="sg-hide" title="다시 보려면 설정 → 화면">숨기기</button></div></div>
    <div class="sg-bar"><i style="width:${Math.round(g.progress / 3 * 100)}%"></i></div>
    <div class="sg-steps">${g.steps.map(step).join("")}</div>${opt}</div>`;
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
  const IC = { ok: "🟢", good: "🟢", warn: "🟡", bad: "🔴" };
  const err = (x) => x?.error ? `<div class="xs dim">불러오지 못함 — ${esc(x.error)}</div>` : "";
  const m = h.market || {}, a = h.assets || {}, ai = h.ai || {}, nw = h.news || {}, td = h.todo || {};
  const todo = td.error ? err(td) : (td.items || []).length
    ? td.items.map((t) => `<a class="td-it" href="${esc(t.link)}">${t.symbol ? stockLogo(t.symbol, "", 28) : `<span class="td-ic">${esc(t.icon)}</span>`}
        <span class="td-tx"><b>${esc(t.text)}</b>${t.why ? `<span class="xs muted">${esc(t.why)}</span>` : ""}</span><span class="td-go">›</span></a>`).join("")
      + (td.more ? `<a class="xs" href="#action">+ ${td.more}개 더 · 오늘 할 일 전체 →</a>` : "")
    : g && !g.done ? `<div class="td-empty"><div class="small">위 <b>'처음이라면'</b> 순서를 끝내면 여기에 매일 할 일 3개가 나옵니다 (보유·관심 종목 실적·AI 신호 변경·중요 공시·CPI·FOMC 등)</div></div>`
    : td.empty_hint ? `<div class="td-empty"><div class="small">${esc(td.empty_hint)}</div><button class="btn-sm primary" data-focus-search>종목 검색하기 ( / )</button></div>`
      : `<div class="td-empty"><div class="td-calm">☕</div><div class="small">${esc(td.calm || "오늘 꼭 할 일은 없습니다")}</div></div>`;
  const ez = ai.easy || {};
  const aiB = ai.error ? err(ai) : `
    <div class="h5-big">${esc(ai.icon || "")} ${esc(ai.label || "")}</div>
    <div class="small">${esc(ai.why || "")}</div>
    ${ez.n ? `<div class="small" style="margin-top:4px">${esc(ez.headline)}</div>` : '<div class="xs dim">채점된 기록이 아직 부족합니다</div>'}
    ${ez.money ? `<div class="small ${ez.earned ? "up" : ez.earned === false ? "down" : ""}">${esc(ez.money)}</div>` : ""}
    ${ez.alpha ? `<div class="small ${ez.alpha.proven ? "up" : ez.alpha.worse ? "down" : "muted"}">📏 ${esc(ez.alpha.text)}</div>` : ""}
    ${ez.strong ? `<div class="xs muted">👍 ${esc(ez.strong)}</div>` : ""}${ez.weak ? `<div class="xs muted">👎 ${esc(ez.weak)}</div>` : ""}
    <a class="xs" href="#scorecard">AI 성적표 (틀린 사례 포함) →</a>`;
  const mk = m.error ? err(m) : `
    ${(m.markets || []).map((x) => `<div class="small">${esc(x.flag || "")} ${esc(x.name)} <b>${esc(x.light || "")} ${esc(x.state || "")}</b></div>${x.notice ? `<div class="xs warn-t">${esc(x.notice)}</div>` : ""}`).join("")}
    ${m.index ? `<div class="small">${esc(m.index.name)} <b class="num ${m.index.chg >= 0 ? "up" : "down"}">${P(m.index.chg, 2)}</b> <span class="xs dim">${esc(m.index.date)}</span></div>` : ""}
    ${m.mood ? `<div class="small">${IC[m.mood.level] || "•"} ${esc(m.mood.text)}</div>` : ""}
    ${m.event ? `<a class="small" href="${esc(m.event.link || "#calendar")}">${IC[m.event.level] || "📅"} ${esc(m.event.text)}</a>` : ""}`;
  const as = a.error ? err(a) : `
    <div class="h5-big num">${moneyShort(a.equity, "KRW")}</div>
    <div class="small">${a.pnl_pct == null ? "" : `<b class="num ${a.pnl_pct >= 0 ? "up" : "down"}">${P(a.pnl_pct, 1)}</b> ${a.paid_in ? "낸 돈 대비" : "시작 대비"} · `}${a.n}종목 · 현금 ${moneyShort(a.cash, "KRW")}</div>
    ${sparkSvg(a.spark)}
    ${a.risk?.headline ? `<div class="small">${IC[a.risk.level] || "•"} ${esc(a.risk.headline)}</div>` : ""}
    <a class="xs" href="#pos">${a.mode === "live" ? "실계좌" : "모의투자"} 자세히 →</a>`;
  const nwB = nw.error ? err(nw) : (nw.items || []).length ? nw.items.map((n) => `<div class="h5-news" data-news="${esc(n.id)}" role="button" tabindex="0">
      <span style="color:${LV_COLOR[n.level?.icon] || "var(--dim)"}">${esc(n.level?.icon || "⚪")}</span> <span class="small">${esc(n.title)}</span>
      <div class="xs dim">${(n.symbols || []).map((x) => `${stockLogo(x.symbol, x.name, 14)} ${esc(x.name)}`).join(" · ")}${n.first ? ` · ${esc(n.first)}` : ""}</div></div>`).join("")
      + `<a class="xs" href="#news">뉴스 ${nw.n}건 모두 보기 →</a>` : empty("최근 2일 저장된 뉴스 없음");
  const showGuide = g && !g.done && !(p?.ui?.guide_hidden);
  const gp = h.goal || {};
  const goalCard = gp.set ? `<a class="card goal-strip" href="#goal"><div class="h5-h">🎯 내 목표</div>${goalBar(gp)}</a>`
    : `<a class="card goal-strip empty" href="#goal"><span class="h5-h">🎯 내 목표</span> <span class="small">아직 없음 — 목표를 정하면 '몇 년 안에 몇 % 확률'로 보여 드립니다 (예: 500만원 → 1억) →</span></a>`;
  box.innerHTML = `${showGuide ? guideCard(g) : ""}${goalCard}<div class="h5-grid">
    <div class="h5 card h5-todo"><div class="h5-h">오늘 내가 할 일 <span class="xs dim">${esc(td.as_of || "")}</span></div>${todo}</div>
    <div class="h5 card h5-ai ai-${esc(ai.key || "")}"><div class="h5-h">AI 지금 믿을 만한가?</div>${aiB}</div>
    <div class="h5 card"><div class="h5-h">오늘 시장</div>${mk}</div>
    <div class="h5 card"><div class="h5-h">내 자산</div>${as}</div>
    <div class="h5 card"><div class="h5-h">중요한 뉴스</div>${nwB}</div>
  </div>`;
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
const VIEW_CLS = { BUY: "up", SELL: "down", HOLD: "warn-t", "NO TRADE": "dim", 기권: "dim", 통과: "good" };
async function stockTop(sym) {
  const box = $("#pf-top");
  if (!box) return;
  let v;
  try { v = await api(`/api/verdict?symbol=${encodeURIComponent(sym)}`); } catch (e) { box.innerHTML = `<div class="xs dim">AI 판단 불러오기 실패 — ${esc(e.message)}</div>`; return; }
  if ($("#pf-top") !== box) return;  // 그 사이 다른 종목으로 이동
  const m = v.market || {};
  const r = v.range52;
  const mk = `<div class="vt-mkt">${esc(m.flag || "")} <b>${esc(m.name || "")}</b> ${esc(m.light || "")} ${esc(m.state || "")}
    ${m.local_time ? `<span class="xs dim">· 현지 ${esc(String(m.local_time).slice(11, 16) || m.local_time)}</span>` : ""}
    ${m.next_kst ? `<span class="xs dim">· 다음 ${esc(m.next === "폐장" ? "마감" : "개장")} ${esc(m.next_kst)}</span>` : ""}
    ${m.notice ? `<div class="xs warn-t">${esc(m.notice)}</div>` : ""}</div>`;
  const r52 = r && r.pos != null ? `<div class="vt-52"><span class="xs muted">52주</span>
    <span class="xs num">${esc(price(r.low, sym))}</span><span class="r52-bar"><i style="left:${Math.round(r.pos * 100)}%"></i></span><span class="xs num">${esc(price(r.high, sym))}</span>
    <span class="xs ${r.from_high > -0.05 ? "up" : r.from_high < -0.3 ? "down" : ""}">고점 대비 ${P(r.from_high, 1)}</span></div>` : "";
  let ai;
  if (!v.final) {
    ai = `<div class="vt-card vt-none"><div class="xs muted">AI 최종 판단</div><div class="vt-big">⚪ AI 판단 없음</div><div class="small muted">${esc(v.why_none || "")}</div></div>`;
  } else {
    const votes = (v.votes || []).map((x) => `<span class="vt-vote" title="${esc(x.label)} · ${esc(x.summary || "")}"><span class="xs muted">${esc(x.role)}</span> <b class="${VIEW_CLS[x.view] || ""}">${esc(x.view)}</b></span>`).join("");
    const li = (xs, empty) => xs.length ? `<ol class="vt-ol">${xs.map((x) => `<li>${esc(x)}</li>`).join("")}</ol>` : `<div class="xs dim">${empty}</div>`;
    const u = v.used || {};
    ai = `<div class="vt-card fin-${esc(v.final)}">
      <div class="vt-row"><span class="xs muted">AI 최종 판단 · ${v.horizon}거래일 · ${esc(v.as_of)} 판단${u.sealed ? " · 🔒봉인" : ""}</span>
        ${v.trust ? `<a class="xs" href="#scorecard" title="AI 전체 성적">AI 상태 ${esc(v.trust.icon)} ${esc(v.trust.label)}</a>` : ""}</div>
      <div class="vt-big">${esc(v.big)}${v.changed_by_gate ? ` <span class="xs dim">(AI 원래 판단 ${esc(v.raw === "NO_TRADE" ? "NO TRADE" : v.raw)} → 막음)</span>` : ""}
        ${v.expected_return != null && v.final !== "NO_TRADE" ? `<span class="small ${v.expected_return >= 0 ? "up" : "down"}">예상 ${P(v.expected_return, 1)}</span>` : ""}</div>
      <div class="vt-votes">${votes}<span class="vt-arrow">→</span><span class="vt-vote"><span class="xs muted">FINAL</span> <b class="${VIEW_CLS[v.label] || ""}">${esc(v.label)}</b></span></div>
      ${v.warn ? `<div class="vt-warn small">⚠ ${esc(v.warn)}</div>` : ""}
      <div class="vt-why"><div><div class="xs b up">왜 ${v.prob_up >= 0.5 ? "오를 수 있나" : "내릴 수 있나"}</div>${li(v.why_buy || [], "뚜렷한 근거 없음")}</div>
        <div><div class="xs b down">왜 지금 사면 안 되나</div>${li(v.why_not || [], "막는 이유 없음")}</div></div>
      <div class="xs dim vt-used">판단에 쓴 데이터: 가격 ${esc(u.price || "-")} 종가 · 뉴스 ${u.news?.n ?? 0}건${u.news?.last ? ` (마지막 ${esc(String(u.news.last).slice(0, 16))})` : ""} · 공시 ${u.disclosures?.n ?? 0}건 · 경제지표 ${u.macro?.n ? u.macro.n + "개" : "없음"}${u.regime ? ` · 국면 ${esc(REGIME_KO?.[u.regime] || u.regime)}` : ""}
        ${v.plan?.stop ? ` · 틀렸다고 인정하는 가격 ${esc(price(v.plan.stop, sym))}` : ""}</div></div>`;
  }
  box.innerHTML = `<div class="vt">${mk}${r52}</div>${ai}`;
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
    ${m ? `<div class="ps-money ${m.earned ? "up" : m.earned === false ? "down" : ""}">💰 ${esc(m.text)}</div>
      <div class="xs dim">${m.earned ? "지수를 이겼습니다 (비용 뒤)" : m.earned === false ? "지수에 졌습니다 — 그냥 지수를 산 것보다 못했습니다 (주가가 올랐어도 AI 가 돈을 번 것은 아님)" : ""}</div>` : ""}
    ${r.alpha ? `<div class="small ${r.alpha.beat_base ? "up" : "down"}" style="margin-top:6px">📏 ${esc(r.alpha.text)}</div>
      <div class="xs dim">시장이 다 같이 오를 때 '오른다'고 하면 쉽게 맞습니다 — 그래서 '지수보다 더 오를지'로 다시 채점한 것이 진짜 실력입니다</div>` : ""}
    ${fails ? `<div class="small b" style="margin-top:10px">크게 틀린 사례</div><ul class="ps-fail">${fails}</ul>` : ""}
    ${reg ? `<div class="small b" style="margin-top:8px">시장 분위기별 적중</div><div>${reg}</div>` : ""}
    ${r.strong ? `<div class="xs">👍 ${esc(r.strong)}</div>` : ""}${r.weak ? `<div class="xs">👎 ${esc(r.weak)}</div>` : ""}
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
