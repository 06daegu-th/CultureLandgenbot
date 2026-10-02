/* Quant AI 대시보드 (vanilla JS, 개인용) */
"use strict";

const S = {
  data: null,
  view: "dashboard",
  symbol: null,
  pfMode: "paper",
  newsKind: "all",
  idxTab: "kr", watchTab: "all", feedTab: "all", chartN: 260, chartSym: null, param: null,
  charts: [],
  alphaMarket: null,
  token: new URLSearchParams(location.search).get("token") || safeGet("qa_token") || "",
};

function safeGet(k) { try { return localStorage.getItem(k); } catch { return null; } }
function safeSet(k, v) { try { localStorage.setItem(k, v); } catch { /* 무시 */ } }
if (S.token) safeSet("qa_token", S.token);
S.alphaMarket = safeGet("qa_mkt") || "KR";

// ------------------------------------------------------------ 유틸
const $ = (sel, el = document) => el.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const isKR = (sym) => /^\d{6}$/.test(sym) || /\.K[SQ]$/.test(sym);

function num(v, d = 0) {
  if (v === null || v === undefined || Number.isNaN(v)) return "-";
  return Number(v).toLocaleString("ko-KR", { minimumFractionDigits: d, maximumFractionDigits: d });
}
function price(v, sym) { return num(v, sym && !isKR(sym) && !["KOSPI", "KOSDAQ"].includes(sym) ? 2 : (Math.abs(v) < 1000 ? 2 : 0)); }
function pct(v, d = 2, sign = true) {
  if (v === null || v === undefined) return '<span class="flat">-</span>';
  const cls = v > 0 ? "up" : v < 0 ? "down" : "flat";
  const arrow = v > 0 ? "▲ " : v < 0 ? "▼ " : "";
  return `<span class="${cls}">${arrow}${sign && v > 0 ? "+" : ""}${(v * 100).toFixed(d)}%</span>`;
}
function time(ts, withDate = false) {
  if (!ts) return "-";
  const d = new Date(ts);
  const p = (n) => String(n).padStart(2, "0");
  const t = `${p(d.getHours())}:${p(d.getMinutes())}`;
  return withDate ? `${p(d.getMonth() + 1)}.${p(d.getDate())} ${t}` : t;
}
function date(ts) { if (!ts) return "-"; const d = new Date(ts); return `${d.getFullYear()}.${String(d.getMonth() + 1).padStart(2, "0")}.${String(d.getDate()).padStart(2, "0")}`; }
const badge = (a) => a ? `<span class="badge b-${esc(a)}">${a === "NO_TRADE" ? "NO TRADE" : esc(a)}</span>` : '<span class="badge b-none">-</span>';
const card = (title, body, right = "", cls = "") => `<div class="card ${cls}">${title || right ? `<div class="card-h"><h3>${title}</h3><div class="right">${right}</div></div>` : ""}${body}</div>`;
const empty = (msg = "아직 기록 없음") => `<div class="empty">${esc(msg)}</div>`;

async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  if (S.token) headers["X-Token"] = S.token;
  const r = await fetch(path, { ...opts, headers, credentials: "same-origin" });
  if (r.status === 401 && !path.startsWith("/api/login")) {
    if (await loginDialog()) return api(path, opts);  // v17: 비밀번호 + 2단계 인증 (또는 토큰)
  }
  if (!r.ok) {
    let msg = `${path} ${r.status}`;
    try { const j = await r.json(); if (j.error) msg = j.error; } catch { /* 본문 없음 */ }
    throw new Error(msg);
  }
  return r.json();
}

// v17: 로그인 (비밀번호 · OTP 6자리 · 또는 접속 토큰) — 세션은 HttpOnly 쿠키라 스크립트가 읽을 수 없음
let _loginP = null;
function loginDialog() {
  if (_loginP) return _loginP;
  _loginP = (async () => {
    let info = {};
    try { info = await (await fetch("/api/auth")).json(); } catch { /* 서버 연결 안 됨 */ }
    if (!info.password) {
      const t = prompt("접속 토큰 (QUANT_WEB_TOKEN 또는 읽기 전용 QUANT_WEB_VIEWER_TOKEN)");
      if (t) { S.token = t; safeSet("qa_token", t); return true; }
      return false;
    }
    return await new Promise((resolve) => {
      const d = document.createElement("div");
      d.className = "kbd-help login-dlg";
      d.innerHTML = `<form class="card" style="max-width:360px"><h3>Quant AI 로그인</h3>
        <label class="small muted">비밀번호<input type="password" name="pw" autocomplete="current-password" required></label>
        ${info.mfa ? '<label class="small muted">2단계 인증 코드 (OTP 앱 6자리)<input name="otp" inputmode="numeric" autocomplete="one-time-code" maxlength="6" pattern="[0-9]{6}" required></label>' : ""}
        <div class="xs bad-t" id="lg-err"></div>
        <div style="display:flex;gap:8px;margin-top:10px"><button class="btn-sm primary" type="submit">로그인</button>${info.viewer_token ? '<button class="btn-sm" type="button" id="lg-tok">토큰으로</button>' : ""}</div>
        <div class="xs dim" style="margin-top:8px">로그인 실패 5번이면 15분 잠금 · 세션 12시간</div></form>`;
      const f = d.querySelector("form");
      f.onsubmit = async (e) => {
        e.preventDefault();
        const r = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" }, credentials: "same-origin",
          body: JSON.stringify({ password: f.pw.value, otp: f.otp ? f.otp.value : "" }) });
        if (r.ok) { d.remove(); resolve(true); return; }
        let m = "로그인 실패"; try { m = (await r.json()).error || m; } catch { /* 무시 */ }
        d.querySelector("#lg-err").textContent = m;
      };
      const tb = d.querySelector("#lg-tok");
      if (tb) tb.onclick = () => { const t = prompt("읽기 전용 토큰"); if (t) { S.token = t; safeSet("qa_token", t); d.remove(); resolve(true); } };
      document.body.appendChild(d);
      setTimeout(() => f.pw.focus(), 50);
    });
  })();
  _loginP.finally(() => { _loginP = null; });
  return _loginP;
}
async function logout() {
  await fetch("/api/logout", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}", credentials: "same-origin" });
  S.token = ""; safeSet("qa_token", ""); location.reload();
}

// ------------------------------------------------------------ 작은 시각화 (SVG)
function spark(values, w = 160, h = 44) {
  const v = (values || []).filter((x) => x !== null);
  if (v.length < 2) return "";
  const min = Math.min(...v), max = Math.max(...v), rng = max - min || 1;
  const pts = v.map((x, i) => [(i / (v.length - 1)) * w, h - 4 - ((x - min) / rng) * (h - 8)]);
  const up = v[v.length - 1] >= v[0];
  const col = up ? css("--up") : css("--down");
  const line = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join("");
  const id = "g" + Math.random().toString(36).slice(2, 8);
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><defs><linearGradient id="${id}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="${col}" stop-opacity=".28"/><stop offset="1" stop-color="${col}" stop-opacity="0"/></linearGradient></defs><path d="${line}L${w},${h}L0,${h}Z" fill="url(#${id})"/><path d="${line}" fill="none" stroke="${col}" stroke-width="1.6"/></svg>`;
}

function gauge(score, label) {
  const r = 58, cx = 75, cy = 72, a0 = Math.PI, a1 = a0 + (Math.PI * Math.max(0, Math.min(100, score))) / 100;
  const pt = (a) => [cx + r * Math.cos(a), cy + r * Math.sin(a)];
  const [x0, y0] = pt(a0), [x1, y1] = pt(a1), [xe, ye] = pt(2 * Math.PI);
  const col = score >= 60 ? css("--good") : score < 40 ? css("--bad") : css("--warn");
  return `<svg viewBox="0 0 150 92" width="150"><path d="M${x0},${y0} A${r},${r} 0 0 1 ${xe},${ye}" stroke="${css("--line-2")}" stroke-width="11" fill="none" stroke-linecap="round"/>
  <path d="M${x0},${y0} A${r},${r} 0 0 1 ${x1.toFixed(2)},${y1.toFixed(2)}" stroke="${col}" stroke-width="11" fill="none" stroke-linecap="round"/>
  <text x="75" y="68" text-anchor="middle" font-size="22" font-weight="800" fill="${css("--text")}">${Math.round(score)}%</text>
  <text x="75" y="86" text-anchor="middle" font-size="11" fill="${css("--muted")}">${esc(label)}</text></svg>`;
}

function donut(segs, center) {
  const total = segs.reduce((a, s) => a + s.value, 0) || 1;
  let a = -Math.PI / 2;
  const r = 58, cx = 70, cy = 70;
  const arcs = segs.map((s) => {
    const da = (s.value / total) * Math.PI * 2, a2 = a + da;
    const large = da > Math.PI ? 1 : 0;
    const p = (ang) => `${(cx + r * Math.cos(ang)).toFixed(2)},${(cy + r * Math.sin(ang)).toFixed(2)}`;
    const d = da >= Math.PI * 2 - 1e-6 ? `M${cx},${cy - r} A${r},${r} 0 1 1 ${cx - 0.01},${cy - r}` : `M${p(a)} A${r},${r} 0 ${large} 1 ${p(a2)}`;
    a = a2;
    return `<path d="${d}" stroke="${s.color}" stroke-width="16" fill="none"/>`;
  }).join("");
  return `<svg viewBox="0 0 140 140" width="140">${arcs}<text x="70" y="64" text-anchor="middle" font-size="11" fill="${css("--muted")}">총 자산</text><text x="70" y="84" text-anchor="middle" font-size="15" font-weight="800" fill="${css("--text")}">${esc(center)}</text></svg>`;
}

const REGIME_KO = { bull_quiet: "안정적 상승", bull_volatile: "변동성 상승", sideways: "횡보", bear_quiet: "완만한 하락", bear_volatile: "변동성 하락", crisis: "위기" };
const PALETTE = ["#3b82f6", "#8b5cf6", "#06b6d4", "#22c55e", "#f59e0b", "#ec4899", "#14b8a6", "#f97316", "#a3e635", "#64748b"];

// ------------------------------------------------------------ 차트 (lightweight-charts)
function clearCharts() { S.charts.forEach((c) => { try { c.remove(); } catch { /* */ } }); S.charts = []; }

function chartOpts(el) {
  return {
    width: el.clientWidth, height: el.clientHeight,
    layout: { background: { type: "solid", color: "transparent" }, textColor: css("--muted"), fontFamily: "Pretendard, sans-serif" },
    grid: { vertLines: { color: css("--line") }, horzLines: { color: css("--line") } },
    rightPriceScale: { borderColor: css("--line-2") }, timeScale: { borderColor: css("--line-2") },
    crosshair: { mode: 0 },
  };
}

async function candleChart(el, symbol, n = 260) {
  if (!el) return;
  if (!window.LightweightCharts) { el.innerHTML = empty("차트 라이브러리를 불러오지 못했습니다 (오프라인?)"); return; }
  const d = await api(`/api/chart?symbol=${encodeURIComponent(symbol)}&n=${n}`);
  if (!d.bars.length) { el.innerHTML = empty(); return; }
  const chart = LightweightCharts.createChart(el, chartOpts(el));
  S.charts.push(chart);
  const up = css("--up"), down = css("--down");
  const candles = chart.addCandlestickSeries({ upColor: up, downColor: down, borderUpColor: up, borderDownColor: down, wickUpColor: up, wickDownColor: down });
  const krw = isKR(symbol);
  candles.applyOptions({ priceFormat: { type: "price", precision: krw ? 0 : 2, minMove: krw ? 1 : 0.01 } });
  candles.setData(d.bars);
  el._series = candles; el.dataset.sym = symbol;  // 매매 구간선(truth.js chartPlanLines)
  [["ma5", "#f59e0b"], ["ma20", "#a78bfa"], ["ma60", "#38bdf8"]].forEach(([k, c]) => {
    const s = chart.addLineSeries({ color: c, lineWidth: 1.4, priceLineVisible: false, lastValueVisible: false });
    s.setData(d[k]);
  });
  const vol = chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "vol", lastValueVisible: false, priceLineVisible: false });
  chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
  vol.setData(d.bars.map((b) => ({ time: b.time, value: b.volume, color: (b.close >= b.open ? up : down) + "66" })));
  el._times = d.bars.map((b) => b.time);
  el._markers = d.markers.map((m) => ({
    time: m.time, position: m.action === "BUY" ? "belowBar" : "aboveBar", shape: m.action === "BUY" ? "arrowUp" : "arrowDown",
    color: m.action === "BUY" ? "#22c55e" : "#fb7185", text: `${m.action} ${Math.round(m.confidence)}`,
  }));
  candles.setMarkers(el._markers);  // v16: 뉴스·공시·실적 표시는 os.js 가 여기에 합친다
  chart.timeScale().fitContent();
}

function lineChart(el, series) {
  if (!window.LightweightCharts) { el.innerHTML = empty("차트 라이브러리를 불러오지 못했습니다"); return; }
  const chart = LightweightCharts.createChart(el, chartOpts(el));
  S.charts.push(chart);
  series.forEach(({ data, color, title }) => {
    const s = chart.addAreaSeries({ lineColor: color, topColor: color + "44", bottomColor: color + "00", lineWidth: 2, title });
    const seen = new Set();
    s.setData(data.map(([t, v]) => ({ time: Math.floor(new Date(t).getTime() / 1000), value: v }))
      .filter((p) => !seen.has(p.time) && seen.add(p.time)));
  });
  chart.timeScale().fitContent();
}

window.addEventListener("resize", () => S.charts.forEach((c) => {
  const el = c.chartElement?.()?.parentElement; if (el) c.applyOptions({ width: el.clientWidth });
}));

// ------------------------------------------------------------ 컴포넌트
function consensusBlock(c, labels, compact = false) {
  if (!c) return empty("아직 합의 신호가 없습니다");
  const voters = (c.contributions || []).map((v) => {
    const st = v.prob_up === null ? null : v.stance;
    const left = st === null ? 50 : st >= 0 ? 50 : 50 + st * 50, width = st === null ? 0 : Math.abs(st) * 50;
    const col = st === null ? "transparent" : st >= 0 ? css("--up") : css("--down");
    const acc = v.accuracy === null || v.accuracy === undefined ? "신규" : `${Math.round(v.accuracy * 100)}%`;
    return `<div class="voter" title="${esc(v.summary)}">${aiIcon(providerOf(v.backend), v.analyst, v.backend, 26)}
      <div class="name">${esc(labels[v.analyst] || v.analyst)}${v.veto ? ' <span class="chip neg">VETO</span>' : ""}<small>${esc(v.backend)}</small></div>
      <div class="stance"><div style="left:${left}%;width:${width}%;background:${col}"></div></div>
      <div class="mono r ${st === null ? "flat" : st >= 0 ? "up" : "down"}" style="text-align:right">${v.analyst === "risk" && st === null ? (v.veto ? '<span class="down">VETO</span>' : '<span style="color:var(--good)">통과</span>') : st === null ? "기권" : (st >= 0 ? "+" : "") + st.toFixed(2)}</div>
      <div class="small muted" style="text-align:right" title="과거 적중률 (n=${v.n_scored})">${acc}</div></div>`;
  }).join("");
  const vetoes = (c.vetoes || []).map((v) => `<div class="veto">⛔ ${esc(v)}</div>`).join("");
  const expl = (c.explanation || []).map((e) => `<div class="small muted">• ${esc(e)}</div>`).join("");
  return `<div class="consensus">
    <div class="cons-top"><div class="cons-action ${esc(c.action)}">${c.action === "NO_TRADE" ? "NO TRADE" : esc(c.action)}</div>
      <div style="flex:1"><div class="small muted" style="display:flex;justify-content:space-between"><span>신뢰도</span><b class="mono" style="color:var(--text)">${Math.round(c.confidence)}</b></div>
      <div class="conf-bar"><div style="width:${Math.min(100, c.confidence)}%"></div></div>
      <div class="small muted" style="margin-top:4px">P(상승) <b class="mono" style="color:var(--text)">${(c.prob_up * 100).toFixed(1)}%</b> · 의견 충돌 <b class="conflict-${esc(c.conflict)}">${{ low: "낮음", medium: "보통", high: "높음" }[c.conflict] || c.conflict}</b></div></div></div>
    <div style="display:grid;gap:8px">${voters}</div>
    ${vetoes}${compact ? "" : expl}
  </div>`;
}

// ------------------------------------------------------------ 공통 (새 UI)
const ROLE_LABEL = { primary: "뉴스 AI", nvidia: "경제·시장 AI", panel: "공시·실적 AI", quant: "차트·Quant AI", regime: "시장 국면", risk: "Risk AI", challenger: "도전자 모델" };
const NT_LABEL = { veto: "리스크 거부권", few_responders: "의견 부족", high_conflict: "AI 충돌", weak_signal: "신호 약함" };
const CAT_LABEL = { direction: "단기 방향", news: "뉴스 해석", macro: "거시경제", trend: "추세", risk: "위험 경고" };
function homeMode(d) {
  if (["paper", "shadow", "live"].includes(d.mode)) return d.mode;
  return ["live", "paper", "shadow"].find((m) => d.portfolios[m]?.equity) || "paper";
}
function roleProvider(d, role) {
  const r = (d.system.ai_roles || []).find((x) => x.role === role);
  return r && r.models.length ? providerOf(r.models[0]) || r.provider : null;
}
function aiRoleIcon(d, role, size = 26) { return aiIcon(roleProvider(d, role), role, null, size); }
const krw = (v) => v == null ? "-" : "₩" + num(v);
const pctRaw = (v, dgt = 1) => v == null ? "-" : `${(v * 100).toFixed(dgt)}%`;

// ------------------------------------------------------------ 뷰: 홈 (대시보드)
function viewDashboard(d) {
  const mode = homeMode(d);
  const m = d.market || {};
  // 1) 주요 지수 (국내 / 해외 / 거시)
  const glob = Object.fromEntries((d.macro || []).map((x) => [x.id, x]));
  const tabsIdx = { kr: "국내", us: "해외", macro: "금리·환율" };
  const idxCards = (() => {
    if (S.idxTab === "us") return ["SP500", "NASDAQCOM", "VIXCLS", "DTWEXBGS"].map((k) => glob[k]).filter(Boolean)
      .map((x) => ({ name: x.label, last: x.last, chg: x.chg, chg_pct: x.chg_pct, spark: x.spark }));
    if (S.idxTab === "macro") return ["DGS10", "DGS2", "DEXKOUS", "DCOILWTICO"].map((k) => glob[k]).filter(Boolean)
      .map((x) => ({ name: x.label, last: x.last, chg: x.chg, chg_pct: x.rate ? null : x.chg_pct, bp: x.rate ? x.chg : null, spark: x.spark }));
    // 지수(KOSPI·KOSDAQ 대용)가 4개보다 적으면 거래대금 상위 대표 종목으로 채운다
    const w = d.watchlist.filter((x) => x.market !== "GLOBAL").slice(0, Math.max(0, 4 - d.indices.length));
    return [...d.indices.map((i) => ({ name: { KOSPI: "KOSPI (대용)", KOSDAQ: "KOSDAQ (대용)" }[i.symbol] || i.name, last: i.last, chg: i.chg, chg_pct: i.chg_pct, spark: i.spark })),
      ...w.map((x) => ({ name: x.name, last: x.last, chg_pct: x.chg_pct, spark: null, sym: x.symbol, tag: "대표 종목" }))];
  })();
  const idx = idxCards.length ? `<div class="idx-row">${idxCards.map((i) => `<div class="idx ${i.sym ? "click" : ""}" ${i.sym ? `data-go="#analysis/${esc(i.sym)}"` : ""}><div class="n">${esc(i.name)}${i.tag ? ` <span class="xs dim">${i.tag}</span>` : ""}</div>
      <div class="v num">${i.sym ? price(i.last, i.sym) : num(i.last, 2)}</div><div class="small">${i.bp != null ? `<span class="${i.bp >= 0 ? "up" : "down"}">${i.bp >= 0 ? "▲ +" : "▼ "}${(i.bp * 100).toFixed(1)}bp</span>` : pct(i.chg_pct)}</div>${spark(i.spark)}</div>`).join("")}</div>`
    : empty(S.idxTab === "kr" ? "지수 데이터 없음" : "FRED_API_KEY 설정 후 자동 수집됩니다");
  // 2) 시장 상태
  const hasMacroKV = ["VIXCLS", "DTWEXBGS", "DGS10", "DCOILWTICO", "DEXKOUS"].some((k) => glob[k]);
  const mk = m.score != null ? `<div class="${hasMacroKV ? "mkt" : "mkt-col"}"><div><div class="gauge-l" style="color:${m.score >= 60 ? "var(--good)" : m.score <= 40 ? "var(--bad)" : "var(--warn)"}">${esc(m.risk_label)}</div>${gauge(m.score, "시장 심리")}<div class="gauge-sub">${esc(m.type || "")} · ${esc(m.regime_label || "")}</div></div>
    ${(() => {
      const kv = ["VIXCLS", "DTWEXBGS", "DGS10", "DCOILWTICO", "DEXKOUS"].map((k) => glob[k]).filter(Boolean).map((x) => `<div class="muted">${esc(x.label)}</div><div class="num">${num(x.last, 2)}</div><div class="small">${x.rate ? `<span class="${x.chg >= 0 ? "up" : "down"}">${x.chg >= 0 ? "+" : ""}${(x.chg * 100).toFixed(0)}bp</span>` : pct(x.chg_pct)}</div>`).join("");
      // 거시 지표가 있으면 지표, 없으면 점수 구성 요소 (추세·폭·모멘텀·변동성·낙폭)
      return kv ? `<div class="kv">${kv}</div>` : `<div style="display:grid;gap:9px;min-width:0">${(m.components || []).map((c) => `<div class="comp"><span class="muted">${esc(c.name)}</span><div class="bar"><div style="width:${c.score * 100}%"></div></div><span class="num r small">${esc(c.value)}</span></div>`).join("")}</div>`;
    })()}</div>`
    : empty("시장 상태 계산에 필요한 지수 데이터 부족");
  // 3) 뉴스 이벤트
  const news = (d.news_events || []).slice(0, 6).map((n) => `<div class="news-it">${catIcon(n.category)}<span class="cat-chip">${esc(n.category || "시장")}</span>
    <span class="t" title="${esc(n.title)}">${esc(n.title)}</span><span class="dim xs mono">${n.n_articles > 1 ? `<span class="cnt-chip">${n.n_articles}건</span> ` : ""}${time(n.ts)}</span></div>`).join("");
  // 4) 관심 종목
  const held = new Set((d.portfolios[mode]?.positions || []).map((p) => p.symbol));
  const wl = d.watchlist.filter((w) => S.watchTab === "held" ? held.has(w.symbol) : S.watchTab === "buy" ? w.action === "BUY" : S.watchTab === "star" ? w.starred : true);
  const watch = wl.length ? `<div class="scroll" style="max-height:430px"><table class="tight watch-table"><thead><tr><th>종목명</th><th class="r">현재가</th><th class="r">등락률</th><th class="r">AI 신호</th></tr></thead><tbody>
    ${wl.map((w) => `<tr class="click" data-sym="${esc(w.symbol)}"><td>${w.starred ? '<span class="star-on" title="관심종목">★</span> ' : ""}<b>${esc(w.name)}</b>${held.has(w.symbol) ? ' <span class="chip ok">보유</span>' : w.tier === "core" ? ' <span class="chip">코어</span>' : w.tier === "watched" ? ' <span class="chip">관심</span>' : ""}${w.market === "GLOBAL" ? ' <span class="chip xs">해외</span>' : ""}</td>
      <td class="r num" data-live-sym="${esc(w.symbol)}">${price(w.last, w.symbol)}</td><td class="r">${pct(w.chg_pct)}</td><td class="r">${badge(w.action)}</td></tr>`).join("")}</tbody></table></div>` : empty();
  // 5) 포트폴리오
  const pf = d.portfolios[mode] || {};
  const curve = pf.curve || [];
  const dayPnl = curve.length >= 2 ? curve[curve.length - 1][1] - curve[curve.length - 2][1] : null;
  const stock = (pf.positions || []).reduce((a, p) => a + (p.value || 0), 0);
  const eq = pf.equity || 0;
  const segs = eq ? [{ label: "주식", value: stock, color: "#22c55e" }, { label: "현금", value: Math.max(pf.cash, 0), color: "#3b82f6" }] : [];
  const pfBody = eq ? `<div class="donut-wrap">${donut(segs, krw(eq))}<div class="legend">${segs.map((s) => `<div><span class="sw" style="background:${s.color}"></span>${s.label}<b class="num">${pctRaw(s.value / eq)}</b></div>`).join("")}
      <div class="small muted" style="margin-top:6px">누적 ${pct(pf.return_pct)}</div></div></div>
    <div class="stat-row" style="grid-template-columns:repeat(3,1fr);margin-top:12px">
      <div class="stat"><div class="l">일일 손익</div><div class="num ${dayPnl >= 0 ? "up" : "down"}"><b>${dayPnl == null ? "-" : (dayPnl >= 0 ? "+" : "") + num(dayPnl)}</b></div></div>
      <div class="stat"><div class="l">보유 종목</div><div class="num"><b>${(pf.positions || []).length}</b></div></div>
      <div class="stat"><div class="l">현금 비중</div><div class="num"><b>${pctRaw(pf.cash / eq)}</b></div></div></div>
    <div style="height:46px;margin-top:8px">${spark(curve.slice(-120).map((x) => x[1]), 400, 46)}</div>` : empty(`${mode.toUpperCase()} 장부 기록 없음 — ./run.sh 가 첫 사이클을 실행합니다`);
  // 6) AI 실시간 분석
  const feed = (d.ai_feed || []).filter((f) => S.feedTab === "buy" ? f.action === "BUY" : S.feedTab === "nt" ? ["NO_TRADE", "HOLD"].includes(f.action) : true)
    .slice(0, 8).map((f) => `<div class="feed-it" data-ev="${f.id}"><span class="dim xs mono">${time(f.ts, true)}</span><span class="t"><b>${esc(f.name)}</b> <span class="muted">${esc(f.text || f.action)}</span></span><span class="tone ${f.tone}">${f.tone}</span></div>`).join("");
  // 7) 매매 신호
  const sigs = (d.signals || []).slice(0, 6).map((s) => `<tr class="click" data-sym="${esc(s.symbol)}"><td><b>${esc(s.name)}</b></td><td>${badge(s.action)}</td><td class="r num">${pctRaw(s.prob_up, 0)}</td><td class="r dim small">${time(s.ts, true)}</td></tr>`).join("");
  // 8) 이벤트 · 글로벌
  const evs = (d.events || []).slice(0, 6).map((e) => `<div class="ev-it ${e.d_label ? "click" : ""}" ${e.d_label ? `data-go="#analysis/${esc(e.symbol)}"` : ""}>${flag(e.country)}<span><span class="imp ${e.importance >= 0.8 ? "hi" : e.importance < 0.5 ? "lo" : ""}"></span>${esc(e.name)}</span><span class="dim xs mono">${e.d_label ? `<b style="color:var(--accent-3)">${esc(e.d_label)}</b> ${date(e.ts).slice(5)}` : `${date(e.ts).slice(5)} ${time(e.ts)}`}</span></div>`).join("");
  const globRows = ["SP500", "NASDAQCOM", "VIXCLS", "DTWEXBGS", "DGS10", "DCOILWTICO"].map((k) => glob[k]).filter(Boolean)
    .map((x) => `<tr><td>${esc(x.label)}</td><td class="r num">${num(x.last, 2)}</td><td class="r">${x.rate ? `<span class="${x.chg >= 0 ? "up" : "down"}">${(x.chg * 100).toFixed(0)}bp</span>` : pct(x.chg_pct)}</td></tr>`).join("");
  S.symbol = S.symbol || d.watchlist.find((w) => w.action === "BUY")?.symbol || d.watchlist[0]?.symbol;
  // 홈 'AI 종합 분석' 은 AI 합의가 있는 국내 종목만 — 해외 종목을 보고 왔으면 국내 종목으로 대신
  S.homeSym = /^\d{6}$/.test(S.symbol || "") ? S.symbol : (d.watchlist.find((w) => w.action && /^\d{6}$/.test(w.symbol)) || d.watchlist.find((w) => /^\d{6}$/.test(w.symbol)))?.symbol;
  const chartSym = S.chartSym || (d.indices[0]?.symbol) || S.symbol;
  const tabs = (id, obj, cur) => `<div class="tabs" id="${id}">${Object.entries(obj).map(([k, v]) => `<button data-k="${k}" class="${cur === k ? "on" : ""}">${v}</button>`).join("")}</div>`;
  const lessons = (d.lessons || []).slice(0, 5);
  return `
  ${typeof readinessStrip === "function" ? readinessStrip(d.readiness) : ""}
  <div class="asof-line xs dim">${typeof asOf === "function" ? `${asOf(d.asof?.now, "fresh", "화면 기준")} ${asOf(d.asof?.last_bar, "fresh", "일봉")} ${d.asof?.summary ? asOf(d.asof.summary, "fresh", "AI 판단") : ""}` : ""}</div>
  <div id="live-strip"></div>
  <div id="setup-box"></div>
  <div class="grid h0">
    <div id="alpha-box">${card("Net Alpha", empty("계산 중…"))}</div>
    ${card("매매 안전 상태", `<div id="safe-box">${empty("점검 중…")}</div>`, `<a class="link" href="#safety">안전 센터 ${ICONS.arrow}</a>`)}
  </div>
  <div class="grid h1">
    ${card("주요 지수", idx, tabs("idx-tabs", tabsIdx, S.idxTab))}
    ${card("시장 상태", mk, `<a class="link" href="#market">상세 ${ICONS.arrow}</a>`)}
    ${card("오늘의 주요 뉴스", news ? `<div class="news-list">${news}</div>` : empty("뉴스 수집 대기 중 — 기본 뉴스 피드에서 자동 수집됩니다"), `<a class="link" href="#news">더보기 ${ICONS.arrow}</a>`)}
  </div>
  <div class="grid h2">
    ${card(`<span id="chart-title">${esc(chartSym === "KOSPI" ? "KOSPI (시총가중 대용)" : nameOf(chartSym))}</span>`, `<div id="main-chart" class="chart tall"></div>`,
      tabs("range-tabs", { 60: "3M", 130: "6M", 260: "1Y", 750: "3Y" }, String(S.chartN)) + (chartSym !== "KOSPI" && d.indices[0] ? ` <button class="btn-sm" id="chart-kospi">KOSPI</button>` : ""))}
    ${card("관심 종목", watch, tabs("watch-tabs", { all: "전체", star: "★", held: "보유", buy: "BUY" }, S.watchTab))}
    ${card("AI 종합 분석", `<div id="ai-box">${empty("불러오는 중…")}</div>`)}
  </div>
  <div class="grid h3">
    ${card(`포트폴리오 현황 <span class="chip">${mode.toUpperCase()}</span>`, pfBody, `<a class="link" href="#portfolio">상세 ${ICONS.arrow}</a>`)}
    ${card("AI 실시간 분석", feed || empty("아직 분석 없음"), tabs("feed-tabs", { all: "전체", buy: "BUY", nt: "보류" }, S.feedTab))}
    <div class="stack">
      ${card("최근 매매 신호", sigs ? `<table class="tight"><tbody>${sigs}</tbody></table>` : empty(), `<a class="link" href="#journal">저널 ${ICONS.arrow}</a>`)}
      ${card("포지션 비중 <span class='small dim'>같이 움직이는 묶음</span>", `<div id="pos-box">${empty("계산 중…")}</div>`, `<a class="link" href="#risk">리스크 ${ICONS.arrow}</a>`)}
    </div>
    <div class="stack">
      ${card("주요 경제 이벤트", evs || empty("등록된 일정 없음 — 종목을 열어 보면 실적 발표일이 여기에 모입니다"))}
      ${card("글로벌 시장 현황", globRows ? `<table class="tight"><tbody>${globRows}</tbody></table>` : empty("FRED_API_KEY 설정 시 표시"))}
    </div>
  </div>
  <div class="grid h4">
    ${card("시스템 상태 & 진행 현황", `<div id="pipe-box">${empty("불러오는 중…")}</div>`, `<a class="link" href="#ops">운영 ${ICONS.arrow}</a>`)}
    ${card("최근 복기가 찾은 교훈", lessons.length ? `<ul class="plain">${lessons.map((l) => `<li><span class="chk">✓</span><span class="small">${esc(l)}</span></li>`).join("")}</ul>` : empty("복기가 쌓이면 표시됩니다 (장 마감 후 자동)"), "", "notice")}
    ${card("바로가기", `<div class="shortcuts">${[["#portfolio", "포트폴리오"], ["#risk", "리스크 관리"], ["#journal", "판단 저널"], ["#ai", "AI 성적 · 보정"], ["#sheet", "리밸런싱 주문표"], ["#research", "백테스트"]].map(([h, t]) => `<a href="${h}">${t}${ICONS.arrow}</a>`).join("")}</div>`)}
  </div>`;
}

async function fillHome(d) {
  const chartSym = S.chartSym || d.indices[0]?.symbol || S.symbol;
  if (chartSym) candleChart($("#main-chart"), chartSym, S.chartN);
  if (S.homeSym) fillAI(d, S.homeSym);
  api("/api/ops").then((o) => { const b = $("#pipe-box"); if (b) b.innerHTML = pipeline(d, o); }).catch(() => {});
  fillLiveStrip();
  api("/api/setup").then((st) => {
    const b = $("#setup-box"); if (b) { b.innerHTML = setupBanner(st); bindSetup(); }
    // 화면을 연 뒤에 뉴스·판단이 쌓였으면(시작 직후 채우기) 기다리지 말고 바로 다시 그린다
    const done = (k) => (st.steps || []).some((x) => x.key === k && x.done);
    if ((done("news") && !(d.news_events || []).length) || (done("decisions") && !(d.signals || []).length)) {
      if (!S._homeRetry || Date.now() - S._homeRetry > 30e3) { S._homeRetry = Date.now(); refresh().then(() => { if (S.view === "dashboard") render(); }).catch(() => {}); }
    }
  }).catch(() => {});
  api(`/api/net-alpha?market=${S.alphaMarket || "KR"}`).then((na) => { const b = $("#alpha-box"); if (b) { b.innerHTML = alphaHero(na); bindMarketTabs(); } }).catch((e) => { const b = $("#alpha-box"); if (b) b.innerHTML = card("증명 체인", empty(e.message)); });
  api("/api/guardian").then((g) => { const b = $("#safe-box"); if (b) b.innerHTML = safetyMini(g); }).catch(() => {});
  api(`/api/risk?mode=${homeMode(d)}`).then((r) => { const b = $("#pos-box"); if (b) b.innerHTML = posWeights(r); }).catch(() => {});
}

async function fillAI(d, sym) {
  const a = await api(`/api/analysis?symbol=${encodeURIComponent(sym)}`);
  const box = $("#ai-box");
  if (!box) return;
  const c = a.consensus;
  const w = d.watchlist.find((x) => x.symbol === sym) || {};
  if (!c) {
    const kr = /^\d{6}$/.test(sym);
    box.innerHTML = kr ? analyzeCard(sym, a.name || sym, false) : empty("해외 종목은 AI 합의 대상이 아닙니다");
    if (kr) bindAnalyze(sym, false, () => fillAI(d, sym));
    return;
  }
  const roles = (c.contributions || []).filter((v) => ["primary", "nvidia", "panel", "risk", "quant"].includes(v.analyst));
  const checks = (a.checklist || []).map((x) => `<div class="check-it ${x.ok ? "ok" : "no"}">${x.ok ? ICONS.check : ICONS.x}<b>${esc(x.key)}</b><span class="muted">${esc(x.text)}</span></div>`).join("");
  const rg = a.range;
  box.innerHTML = `<div class="ai-head">${aiRoleIcon(d, "primary", 40)}<div><div class="nm">${esc(a.name)} <span class="dim small">${esc(a.symbol)}</span></div>
      <div class="sub2">현재가 <b class="num" style="color:var(--text)">${price(w.last ?? rg?.last, sym)}</b> ${pct(w.chg_pct)}</div></div>
      <span class="badge big-badge b-${esc(c.action)}">${c.action === "NO_TRADE" ? "NO TRADE" : esc(c.action)}</span></div>
    <div style="margin-top:12px" class="small muted">AI 신뢰도 <b class="num" style="color:var(--text)">${Math.round(c.confidence)}</b> · P(상승) <b class="num" style="color:var(--text)">${pctRaw(c.prob_up)}</b> · 충돌 <b class="conflict-${esc(c.conflict)}">${{ low: "낮음", medium: "보통", high: "높음" }[c.conflict] || c.conflict}</b></div>
    <div class="conf-bar" style="margin-top:6px"><div style="width:${Math.min(100, c.confidence)}%"></div></div>
    <div class="small muted" style="margin-top:12px"><b style="color:var(--text)">분석 요약</b></div><div class="checks">${checks || empty()}</div>
    ${rg ? `<div class="rng"><span class="muted">${rg.horizon}거래일 예상 범위 (1σ)</span><span class="num">${num(rg.lower)} ~ ${num(rg.upper)}</span>
      <span class="muted">변동성 손절선 (2σ)</span><span class="num down">${num(rg.stop)} (${pctRaw(rg.stop / rg.last - 1)})</span></div>` : ""}
    <div class="ai-roles" style="margin-top:12px">${roles.map((v) => `<span class="ai-role" title="${esc(v.summary || "")}">${aiIcon(providerOf(v.backend), v.analyst, v.backend, 20)}${esc(ROLE_LABEL[v.analyst] || v.analyst)} <b class="num ${v.prob_up == null ? "flat" : v.prob_up >= 0.5 ? "up" : "down"}">${v.prob_up == null ? (v.veto ? "VETO" : "통과") : pctRaw(v.prob_up, 0)}</b></span>`).join("")}</div>
    <button class="btn-wide" data-go="#evidence/${c.id}">근거 추적 (Evidence Chain) 보기 ${ICONS.arrow}</button>`;
}

function pipeline(d, o) {
  const job = (n) => (o.jobs || []).find((j) => j.job === n);
  const st = (j, okText = "정상") => !j ? ["대기", "var(--dim)"] : j.ok === false ? ["실패", "var(--bad)"] : j.ok ? [okText, "var(--good)"] : ["실행 중", "var(--warn)"];
  const roles = (d.system.ai_roles || []).filter((r) => r.models.length);
  const stages = [
    ["데이터 수집", ICONS.data, "#0ea5e9", st(job("krx_data") || job("krx_bootstrap")), `마지막 ${date(d.system.last_bar)}`],
    ["뉴스 분석", ICONS.news, "#8b5cf6", st(job("news") || job("news_offhours"), "수집 중"), (d.news_events || []).length ? `이벤트 ${(d.news_events || []).length}` : job("news") ? "수집 결과 반영 중" : "수집 대기"],
    ["AI 모델", ICONS.ai, "#6366f1", [roles.length ? `${roles.length}개 연결` : "휴리스틱", roles.length ? "var(--good)" : "var(--warn)"], `오늘 $${(o.llm?.today_cost || 0).toFixed(2)}`],
    ["주문 엔진", ICONS.engine, "#22c55e", [o.kill_switch?.on ? "매수 정지" : "대기", o.kill_switch?.on ? "var(--bad)" : "var(--good)"], o.broker === "kis" ? (o.kis_env === "real" ? "KIS 실전" : "KIS 모의") : "가상매매"],
    ["포트폴리오 감시", ICONS.portfolio, "#f59e0b", st(job("strategy_health") || job("core_satellite")), "건강검진 · 리스크"],
    ["학습 / 복기", ICONS.learn, "#ec4899", st(job("review"), "완료"), "보정 · 교훈"],
  ];
  return `<div class="pipe">${stages.map(([n, ic, col, [s, sc], sub]) => `<div class="pipe-it"><span class="pi" style="background:${col}">${ic}</span><div style="min-width:0"><b>${n}</b><span style="color:${sc}">${s}</span> <span class="dim">· ${esc(sub)}</span></div></div>`).join("")}</div>`;
}

function posWeights(r) {
  if (!r || r.error || !r.n_positions) return empty("보유 종목 없음");
  const inCluster = new Set((r.clusters || []).flatMap((c) => c.symbols));
  const rows = [...(r.clusters || []).map((c) => ({ label: c.members.slice(0, 2).join("·") + (c.members.length > 2 ? ` 외 ${c.members.length - 2}` : ""), w: c.weight })),
    ...(r.risk_contrib || []).filter((x) => !inCluster.has(x.symbol)).map((x) => ({ label: x.name, w: x.weight }))].sort((a, b) => b.w - a.w).slice(0, 5);
  return rows.map((x) => `<div class="wbar"><span class="small" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(x.label)}</span><div class="bar"><div style="width:${Math.min(100, x.w * 100 / Math.max(r.gross, 0.01))}%"></div></div><span class="num small r">${pctRaw(x.w, 0)}</span></div>`).join("")
    + `<div class="small dim" style="margin-top:8px">1일 VaR95 ${pctRaw(r.var95)} · 베타 ${r.beta ?? "-"} · 현금 ${pctRaw(r.cash_weight, 0)}</div>`;
}

// ------------------------------------------------------------ 뷰: AI 성적 · 확률 보정
async function viewAIScore(el) {
  const [sb, cal] = await Promise.all([api("/api/ai-scoreboard"), api("/api/calibration")]);
  const roles = sb.roles.map((r) => `<div class="prov-card"><div class="hd">${aiIcon(r.models.length ? providerOf(r.models[0]) || r.provider : null, r.role, null, 34)}<div><b>${esc(r.label)}</b><div class="small muted">${esc(r.desc)}</div></div>${r.free && r.models.length ? '<span class="chip ok" style="margin-left:auto">무료</span>' : ""}</div>
    <div class="small">${esc(r.provider)}</div><div class="xs mono dim" style="white-space:normal">${r.models.map(esc).join(" → ") || "-"}</div>${r.daily ? `<div class="xs dim">하루 한도 ${r.daily}회</div>` : ""}</div>`).join("");
  const byProv = {};
  sb.rows.forEach((r) => { (byProv[r.provider] ||= []).push(r); });
  const prov = Object.entries(byProv).map(([p, rows]) => `<div class="prov-card"><div class="hd">${aiIcon(p, null, rows[0].model, 32)}<div><b>${esc(p)}</b><div class="xs mono dim">${esc([...new Set(rows.map((r) => r.model))].join(", "))}</div></div></div>
    ${rows.map((r) => `<div class="skill"><span>${esc(r.label)}</span><div class="bar"><div style="width:${(r.accuracy ?? 0) * 100}%;background:${heatColor(r.accuracy)}"></div></div><span class="num r"><b>${pctRaw(r.accuracy, 0)}</b><span class="xs dim"> ${r.n}</span></span></div>`).join("")}</div>`).join("");
  const an = Object.entries(cal.analysts || {});
  const calRows = an.map(([a, v]) => `<tr><td><b>${esc(ROLE_LABEL[a] || a)}</b></td><td class="r num">${v.raw.n}</td><td class="r num">${pctRaw(v.raw.accuracy)}</td><td class="r num">${v.raw.brier?.toFixed(4) ?? "-"}</td><td class="r num">${v.raw.logloss?.toFixed(4) ?? "-"}</td>
    <td class="r num ${v.raw.ece >= 0.08 ? "bad-t" : "good"}">${pctRaw(v.raw.ece)}</td><td class="r num">${v.calibrator ? `a=${v.calibrator.a.toFixed(2)} b=${v.calibrator.b.toFixed(2)}<span class="sub">ECE ${pctRaw(v.calibrator.before.ece)} → ${pctRaw(v.calibrator.after.ece)}</span>` : '<span class="dim">표본 50개 전</span>'}</td></tr>`).join("");
  const curves = an.filter(([, v]) => v.raw.curve?.length).map(([a, v]) => `<div class="prov-card"><div class="hd"><b>${esc(ROLE_LABEL[a] || a)}</b><span class="small dim" style="margin-left:auto">n=${v.raw.n}</span></div>${reliabilitySvg(v.raw.curve)}</div>`).join("");
  const cons = cal.consensus || {};
  el.innerHTML = `
  ${card("AI 역할 배정 <span class='small dim'>역할마다 다른 회사 모델 → 오류가 겹치지 않게</span>", `<div class="op-grid">${roles}</div>`)}
  ${card("AI 별 성적표 <span class='small dim'>실제 결과로 자동 채점 · 최근 1년</span>", prov ? `<div class="op-grid">${prov}</div>` : empty("채점된 의견이 아직 없습니다 (horizon 경과 후 자동 채점)"))}
  <div class="grid g-2">
    ${card("확률 보정 지표 <span class='small dim'>AI 가 말한 확률 vs 실제 적중</span>", calRows ? `<div class="scroll"><table><thead><tr><th>AI</th><th class="r">표본</th><th class="r">적중률</th><th class="r">Brier</th><th class="r">LogLoss</th><th class="r">ECE</th><th class="r">보정 (Platt)</th></tr></thead><tbody>${calRows}</tbody></table></div>
      <div class="small dim" style="margin-top:8px">ECE = 확률이 실제 적중률과 평균 몇 %p 어긋나는지. 8%p 이상이면 과신/소심. 표본 50개부터 AI 별 자동 보정 → 앙상블에는 보정된 확률이 들어갑니다.</div>` : empty())}
    ${card("합의 신호 보정", cons.n ? `<div class="stat-row" style="grid-template-columns:repeat(3,1fr)"><div class="stat"><div class="l">표본</div><div class="big num">${cons.n}</div></div><div class="stat"><div class="l">Brier</div><div class="big num">${cons.brier?.toFixed(3)}</div></div><div class="stat"><div class="l">ECE</div><div class="big num">${pctRaw(cons.ece)}</div></div></div>${reliabilitySvg(cons.curve)}` : empty("채점된 합의 신호 없음"))}
  </div>
  ${curves ? card("신뢰도 곡선 (Reliability) <span class='small dim'>대각선에 가까울수록 정직한 확률</span>", `<div class="op-grid">${curves}</div>`) : ""}`;
}

function reliabilitySvg(curve) {
  if (!curve || !curve.length) return empty();
  const W = 260, H = 190, P = 26;
  const x = (v) => P + v * (W - P - 8), y = (v) => H - P - v * (H - P - 8);
  const pts = curve.map((b) => `${x(b.pred).toFixed(1)},${y(b.actual).toFixed(1)}`).join(" ");
  const dots = curve.map((b) => `<circle cx="${x(b.pred)}" cy="${y(b.actual)}" r="${Math.min(7, 2 + Math.sqrt(b.n) / 3)}" fill="#60a5fa" fill-opacity=".85"><title>예측 ${pctRaw(b.pred)} · 실제 ${pctRaw(b.actual)} · n=${b.n}</title></circle>`).join("");
  return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;max-height:220px"><rect x="${P}" y="8" width="${W - P - 8}" height="${H - P - 8}" fill="none" stroke="${css("--line-2")}"/>
    <line x1="${x(0)}" y1="${y(0)}" x2="${x(1)}" y2="${y(1)}" stroke="${css("--dim")}" stroke-dasharray="4 4"/>
    <polyline points="${pts}" fill="none" stroke="#60a5fa" stroke-width="2"/>${dots}
    <text x="${W / 2}" y="${H - 6}" font-size="10" fill="${css("--muted")}" text-anchor="middle">AI 가 말한 확률</text>
    <text x="10" y="${H / 2}" font-size="10" fill="${css("--muted")}" text-anchor="middle" transform="rotate(-90 10 ${H / 2})">실제 적중</text></svg>`;
}

// ------------------------------------------------------------ 뷰: 리스크 관리
async function viewRisk(el) {
  const d = S.data;
  const mode = S.riskMode || homeMode(d);
  const r = await api(`/api/risk?mode=${mode}`);
  const tabs = `<div class="tabs" id="risk-tabs">${["paper", "shadow", "live"].map((m) => `<button data-k="${m}" class="${mode === m ? "on" : ""}">${m.toUpperCase()}</button>`).join("")}</div>`;
  if (r.error || !r.n_positions) { el.innerHTML = card("리스크 관리", empty(r.error || `${mode.toUpperCase()} 장부에 보유 종목이 없습니다`), tabs); bindRiskTabs(); return; }
  const warn = (r.warnings || []).map((w) => `<div class="veto" style="margin-bottom:6px">⚠ ${esc(w)}</div>`).join("");
  const contrib = (r.risk_contrib || []).slice(0, 10).map((x) => `<div class="wbar"><span class="small">${esc(x.name)}</span><div class="bar"><div style="width:${Math.max(0, x.share) * 100 * 3}%;background:linear-gradient(90deg,#f59e0b,#ef4444)"></div></div><span class="num small r">${pctRaw(x.share, 0)}</span></div>`).join("");
  const clusters = (r.clusters || []).map((c) => `<tr><td style="white-space:normal">${c.members.map(esc).join(", ")}</td><td class="r num ${c.weight > (r.limits?.max_cluster_weight ?? 0.4) ? "bad-t" : ""}">${pctRaw(c.weight)}</td></tr>`).join("");
  const liq = (r.liquidity || []).slice(0, 12).map((x) => `<tr><td>${esc(x.name)}</td><td class="r num">${num(x.adv / 1e8, 1)}억</td><td class="r num ${x.days_to_liquidate > 3 ? "bad-t" : ""}">${x.days_to_liquidate == null ? "-" : x.days_to_liquidate < 0.01 ? "<0.01일 (즉시)" : x.days_to_liquidate.toFixed(2) + "일"}</td></tr>`).join("");
  el.innerHTML = `
  <div class="card"><div class="card-h"><h3>포트폴리오 리스크 <span class="small dim">최근 ${r.days}거래일 실제 수익률 × 현재 비중 (과거 시뮬레이션)</span></h3><div class="right">${tabs}</div></div>
    <div class="stat-row">
      <div class="stat"><div class="l">1일 VaR95</div><div class="big num">${pctRaw(r.var95)}</div><div class="small muted">${krw(r.var95_krw)} · 한도 ${pctRaw(r.limits?.max_var95, 0)}</div></div>
      <div class="stat"><div class="l">1일 ES95 (최악 5% 평균)</div><div class="big num">${pctRaw(r.es95)}</div><div class="small muted">${krw(r.es95_krw)}</div></div>
      <div class="stat"><div class="l">KOSPI 베타 · 상관</div><div class="big num">${r.beta ?? "-"}</div><div class="small muted">상관 ${r.corr_to_market ?? "-"} · 연변동성 ${pctRaw(r.vol)}</div></div>
      <div class="stat"><div class="l">시장 -10% 시나리오</div><div class="big num down">${pctRaw(r["stress_market_-10pct"])}</div><div class="small muted">최악의 날 ${pctRaw(r.worst_day)} (${esc(r.worst_day_date || "")})</div></div>
    </div>${warn ? `<div style="margin-top:12px">${warn}</div>` : ""}</div>
  <div class="grid g-3">
    ${card("손실 꼬리 기여도 <span class='small dim'>최악 5% 날의 손실 몫</span>", contrib || empty())}
    ${card("같이 움직이는 묶음 <span class='small dim'>상관 ≥ 0.6 · 섹터 대용</span>", clusters ? `<table><thead><tr><th>종목</th><th class="r">합산 비중</th></tr></thead><tbody>${clusters}</tbody></table>` : empty("강하게 묶인 종목 없음 (분산 양호)"))}
    ${card("노출 · 통화", `<table><tbody><tr><td>주식 비중</td><td class="r num">${pctRaw(r.gross)}</td></tr><tr><td>현금</td><td class="r num">${pctRaw(r.cash_weight)}</td></tr><tr><td>보유 종목</td><td class="r num">${r.n_positions}</td></tr><tr><td>종목 간 평균 상관</td><td class="r num">${r.avg_corr ?? "-"}</td></tr>
      ${Object.entries(r.currency || {}).map(([k, v]) => `<tr><td>통화 ${esc(k)}</td><td class="r num">${pctRaw(v)}</td></tr>`).join("")}</tbody></table>`)}
  </div>
  ${card("유동성 <span class='small dim'>20일 평균 거래대금 · 참여율 10% 로 청산에 걸리는 일수 · 주문은 거래대금의 5% 이내로 자동 제한</span>", liq ? `<div class="scroll"><table><thead><tr><th>종목</th><th class="r">일 거래대금</th><th class="r">청산 일수</th></tr></thead><tbody>${liq}</tbody></table></div>` : empty())}
  ${typeof riskV13 === "function" ? riskV13(r) : ""}`;
  bindRiskTabs();
}
function bindRiskTabs() { document.querySelectorAll("#risk-tabs button").forEach((b) => b.onclick = () => { S.riskMode = b.dataset.k; render(); }); }

// ------------------------------------------------------------ 뷰: 판단 저널 (AI Journal)
async function viewJournal(el) {
  const act = { all: "", buy: "BUY", sell: "SELL", nt: "NO_TRADE,HOLD" }[S.journalTab || "all"];
  S.journalLimit = S.journalLimit || 30;
  const j = await api(`/api/journal?limit=${S.journalLimit}${act ? "&action=" + act : ""}`);
  const tabs = `<div class="tabs" id="journal-tabs">${Object.entries({ all: "전체", buy: "BUY", sell: "SELL", nt: "보류 (NO TRADE)" }).map(([k, v]) => `<button data-k="${k}" class="${(S.journalTab || "all") === k ? "on" : ""}">${v}</button>`).join("")}</div>`;
  const rows = j.rows.map((r) => `<div class="jr" data-ev="${r.id}">
    <div><span class="lbl">시각</span><span class="mono small">${date(r.as_of).slice(2)}</span><div>${badge(r.action)}</div></div>
    <div><span class="lbl">종목</span><b>${esc(r.name)}</b><div class="xs dim">${esc(r.symbol)} · 신뢰도 ${Math.round(r.confidence)}</div></div>
    <div><span class="lbl">왜</span>${r.why.slice(0, 3).map((w) => `<div class="small">${esc(w)}</div>`).join("")}</div>
    <div><span class="lbl">예상</span><span class="small">${esc(r.expected)}</span></div>
    <div><span class="lbl">실제</span>${r.actual == null ? '<span class="dim small">채점 대기</span>' : `${pct(r.actual)} ${r.correct ? '<span class="chip ok">적중</span>' : '<span class="chip bad">빗나감</span>'}`}${r.order_status ? `<div class="xs dim">주문: ${esc(r.order_status)}</div>` : ""}</div>
    <div><span class="lbl">왜 틀렸나</span><span class="small muted">${esc(r.cause || (r.correct ? "—" : ""))}</span></div></div>`).join("");
  const nt = (j.no_trade || []).map((x) => `<tr><td>${esc(x.label)}</td><td class="r num">${x.n}</td><td class="r num">${pctRaw(x.avoided_loss_rate, 0)}</td><td class="r">${pct(x.avg_return_if_entered)}</td></tr>`).join("");
  el.innerHTML = `
  ${card("AI 판단 저널 <span class='small dim'>왜 샀나 → 무엇을 예상했나 → 실제 → 왜 틀렸나 · 행을 누르면 근거 추적</span>",
    rows ? `<div class="jr head"><div>시각 · 신호</div><div>종목</div><div>왜 (상위 AI · 근거)</div><div>예상</div><div>실제</div><div>왜 틀렸나</div></div>${rows}
      ${j.rows.length >= S.journalLimit ? `<button class="btn-wide" id="journal-more">더 보기 (${S.journalLimit}건 표시 중)</button>` : ""}` : empty("아직 판단 기록이 없습니다"), tabs)}
  ${card("NO TRADE 판단의 가치 <span class='small dim'>진입을 보류한 사유별로 — 실제로 손실을 피했나</span>", nt ? `<div class="scroll"><table><thead><tr><th>사유</th><th class="r">건수</th><th class="r">하락 회피율</th><th class="r">진입했다면 평균</th></tr></thead><tbody>${nt}</tbody></table></div>` : empty("채점된 보류 판단이 아직 없습니다"))}`;
  document.querySelectorAll("#journal-tabs button").forEach((b) => b.onclick = () => { S.journalTab = b.dataset.k; S.journalLimit = 30; render(); });
  const more = $("#journal-more");
  if (more) more.onclick = () => { S.journalLimit += 30; render(); };
  document.querySelectorAll("[data-ev]").forEach((x) => x.onclick = () => { location.hash = `#evidence/${x.dataset.ev}`; });
}

// ------------------------------------------------------------ 뷰: Evidence Chain
async function viewEvidence(el, id) {
  const e = await api(`/api/evidence?id=${encodeURIComponent(id)}`);
  if (e.error) { el.innerHTML = card("근거 추적", empty("해당 판단을 찾을 수 없습니다")); return; }
  const icon = { news: "📰", disclosure: "📄", price: "📈", market: "🌐", ai: "🤖", ensemble: "⚖️", risk: "🛡️", order: "🧾", outcome: "🎯" };
  const body = (st) => {
    const it = st.items;
    if (st.stage === "news" || st.stage === "disclosure") return it.length ? it.map((n) => `<div class="news-it" style="grid-template-columns:30px 1fr auto">${catIcon(n.category || (st.stage === "disclosure" ? "공시" : null))}<span class="t" style="white-space:normal">${esc(n.title)}</span><span class="xs dim">${n.n_articles > 1 ? `<span class="cnt-chip">${n.n_articles}건 → 1 이벤트</span> ` : ""}${n.sentiment == null ? "" : sentChip(n.sentiment)}</span></div>`).join("") : '<span class="dim small">없음</span>';
    if (st.stage === "price" && !Object.keys(it || {}).length) return '<span class="dim small">이 판단은 근거 저장 기능 이전 기록이라 당시 지표가 없습니다</span>';
    if (st.stage === "price") {
      const L = { last_close: ["종가", "n"], last_bar: ["기준 봉", "s"], ret_1: ["1일 수익률", "p"], ret_5: ["5일 수익률", "p"], ret_20: ["20일 수익률", "p"],
        vol_20: ["20일 변동성 (일)", "p"], vol_ratio: ["변동성 비율 (5/60일)", "x"], jump_sigma: ["당일 급변 (σ)", "x"], rsi_14: ["RSI (14)", "r"],
        dist_52w: ["52주 고점 거리", "p"], ma_20_gap: ["20일선 대비", "p"], ma_60_gap: ["60일선 대비", "p"] };
      const fmt = (t, v) => typeof v !== "number" ? esc(String(v ?? "-")).slice(0, 10) : t === "p" ? pct(v) : t === "r" ? (v <= 1 ? v * 100 : v).toFixed(0) : t === "x" ? v.toFixed(2) : num(v, 0);
      return `<div class="kvs">${Object.entries(it || {}).map(([k, v]) => { const [lb, t] = L[k] || [k, "x"]; return `<div><span class="muted">${esc(lb)}</span><b class="num">${fmt(t, v)}</b></div>`; }).join("")}</div>`;
    }
    if (st.stage === "market") {
      const mk = it.market || {};
      return `<div class="kvs"><div><span class="muted">시장 상태</span><b>${esc(mk.label || "-")} ${mk.score ?? ""}</b></div><div><span class="muted">유형</span><b>${esc(mk.type || "-")}</b></div><div><span class="muted">국면</span><b>${esc(REGIME_KO[it.regime?.regime] || it.regime?.regime || "-")}</b></div>
        ${(it.cross_asset || []).map((c) => `<div><span class="muted">${esc(c.name)} 상관</span><b class="num">${c.corr.toFixed(2)}</b></div>`).join("")}</div>
        ${(it.events || []).length ? `<div class="small muted" style="margin-top:8px">예정 이벤트: ${it.events.map((x) => esc(x.name)).join(", ")}</div>` : ""}`;
    }
    if (st.stage === "ai") return `<div class="op-grid">${it.map((a) => `<div class="op"><h4><span style="display:flex;gap:8px;align-items:center">${aiIcon(a.provider, a.analyst, a.model, 26)}${esc(a.label)}</span><span class="small dim">${a.weight == null ? "" : "가중 " + pctRaw(a.weight, 0)}</span></h4>
      <div class="xs dim">${esc(a.provider || "")} · ${esc(a.model || "")}${a.track_accuracy != null ? ` · 과거 적중 ${pctRaw(a.track_accuracy, 0)} (n=${a.n_scored})` : ""}</div>
      ${Object.values(a.scores || {}).map((s) => `<div class="small" style="margin-top:4px">${esc(s.label)} <b class="num">${pctRaw(s.prob_up, 0)}</b>${s.prob_cal != null ? ` <span class="dim">→ 보정 ${pctRaw(s.prob_cal, 0)}</span>` : ""}${s.correct == null ? "" : s.correct ? ' <span class="chip ok">적중</span>' : ' <span class="chip bad">빗나감</span>'}</div>`).join("")}
      <div class="small" style="margin-top:6px">${esc(a.summary || "")}</div>${a.veto ? `<div class="veto" style="margin-top:6px">⛔ ${esc(a.veto_reason || "VETO")}</div>` : ""}
      ${(a.reasons || []).length ? `<ul>${a.reasons.slice(0, 4).map((x) => `<li>${esc(x)}</li>`).join("")}</ul>` : ""}</div>`).join("")}</div>`;
    if (st.stage === "ensemble") return `<div class="cons-top"><div class="cons-action ${esc(it.action)}">${it.action === "NO_TRADE" ? "NO TRADE" : esc(it.action)}</div><div class="small muted">P(상승) <b class="num" style="color:var(--text)">${pctRaw(it.prob_up)}</b> · 신뢰도 ${Math.round(it.confidence)} · 충돌 <b class="conflict-${esc(it.conflict)}">${esc(it.conflict)}</b></div></div>
      ${(it.reasons || []).slice(0, 5).map((r) => `<div class="small">• ${esc(r)}</div>`).join("")}`;
    if (st.stage === "risk") return `${(it.vetoes || []).map((v) => `<div class="veto" style="margin-bottom:6px">⛔ ${esc(v)}</div>`).join("")}${(it.no_trade || []).map((v) => `<span class="chip">${esc(v)}</span>`).join("")}${(it.explanation || []).map((x) => `<div class="small muted">• ${esc(x)}</div>`).join("")}${(() => { const q = it.data_quality || {}; const bad = [q.halt && `거래정지 의심: ${q.halt}`, q.stale && `시세 지연: ${q.stale}`, q.jump_sigma >= 4 && `급변 ${q.jump_sigma}σ`].filter(Boolean); return bad.length ? `<div class="small warn-t">데이터 품질: ${esc(bad.join(" · "))}</div>` : ""; })()}${!(it.vetoes || []).length && !(it.explanation || []).length ? '<span class="small good">통과 — 거부권·보류 사유 없음</span>' : ""}`;
    if (st.stage === "order") return it.length ? `<div class="scroll"><table><thead><tr><th>장부</th><th>구분</th><th class="r">수량</th><th>상태</th><th class="r">기준가</th><th class="r">체결가</th><th class="r">슬리피지</th><th>멱등 키</th></tr></thead><tbody>${it.map((o) => `<tr><td><span class="chip">${esc(o.mode)}</span></td><td class="${o.side === "buy" ? "up" : "down"}">${o.side === "buy" ? "매수" : "매도"}</td><td class="r num">${num(o.qty)}</td><td>${esc(o.status)}</td><td class="r num">${num(o.ref_price)}</td><td class="r num">${num(o.avg_price)}</td><td class="r num">${o.slippage_bps == null ? "-" : o.slippage_bps.toFixed(1) + "bp"}</td><td class="xs mono dim">${esc((o.client_order_id || "").split(":").slice(-3).join(":"))}</td></tr>`).join("")}</tbody></table></div>` : '<span class="dim small">이 판단으로 나간 주문 없음</span>';
    if (st.stage === "outcome") return it.correct == null ? `<span class="dim small">${it.horizon || ""}거래일 경과 후 자동 채점</span>` : `<div class="cons-top"><div class="big ${it.realized_return >= 0 ? "up" : "down"}">${pct(it.realized_return)}</div>${it.correct ? '<span class="chip ok">예측 적중</span>' : '<span class="chip bad">빗나감</span>'}</div>${it.cause ? `<div class="lesson" style="margin-top:8px">원인: ${esc(it.cause)}</div>` : ""}`;
    return "";
  };
  el.innerHTML = `
  <div class="card"><div class="card-h"><h3>근거 추적 (Evidence Chain) · ${esc(e.name)} <span class="dim small">${esc(e.symbol)} · ${date(e.as_of)}</span></h3><div class="right">${badge(e.action)} <a class="link" href="#journal">저널로 ${ICONS.arrow}</a></div></div>
    <div class="small muted">이 판단을 내릴 때 AI 들이 본 재료부터 실제 결과까지 한 번에 추적합니다. 그때의 데이터를 판단과 함께 저장하므로 나중에 봐도 같은 근거가 재현됩니다.</div></div>
  ${card("", `<div class="chain">${e.chain.map((st) => `<div class="step"><span class="node">${icon[st.stage] || "•"}</span><h4>${esc(st.title)}</h4><div class="box">${body(st)}</div></div>`).join("")}</div>`)}`;
}

// ------------------------------------------------------------ 뷰: 주문 내역 (멱등성 · 복구 · 슬리피지)
async function viewOrderHistory(el) {
  const o = await api(`/api/orders${S.orderMode ? "?mode=" + S.orderMode : ""}`);
  const stCls = { filled: "ok", partial: "", unfilled: "", cancelled: "", rejected: "bad", error: "bad", unknown: "bad", pending: "", submitted: "" };
  const stKo = { filled: "체결", partial: "부분체결", unfilled: "미체결", cancelled: "취소", rejected: "거부", error: "오류", unknown: "상태 불명", pending: "제출 대기", submitted: "접수" };
  const tabs = `<div class="tabs" id="om-tabs">${Object.entries({ "": "전체", live: "LIVE", shadow: "SHADOW", paper: "PAPER" }).map(([k, v]) => `<button data-k="${k}" class="${(S.orderMode || "") === k ? "on" : ""}">${v}</button>`).join("")}</div>`;
  const rows = o.rows.map((r) => `<tr ${r.consensus_id ? `class="click" data-ev="${r.consensus_id}"` : ""}><td class="dim small">${time(r.ts, true)}</td><td><span class="chip">${esc(r.mode)}</span></td><td><b>${esc(r.name)}</b></td>
    <td class="${r.side === "buy" ? "up" : "down"}">${r.side === "buy" ? "매수" : "매도"}</td><td class="r num">${num(r.qty)}</td><td><span class="chip ${stCls[r.status] || ""}">${stKo[r.status] || esc(r.status)}</span></td>
    <td class="r num">${r.avg_price ? num(r.avg_price) : "-"}</td><td class="r num">${r.slippage_bps == null ? "-" : r.slippage_bps.toFixed(1) + "bp"}</td>
    <td class="xs mono dim" title="${esc(r.client_order_id || "")}">${esc((r.client_order_id || "-").split(":").slice(-2).join(":"))}${r.broker_order_id ? `<div>#${esc(r.broker_order_id)}</div>` : ""}</td>
    <td class="small muted" style="white-space:normal;max-width:360px">${esc(r.reason || "")}</td></tr>`).join("");
  const sl = Object.entries(o.slippage || {}).map(([m, s]) => `<div class="stat"><div class="l">${m.toUpperCase()} 슬리피지</div><div class="big num">${s.n ? s.mean_bps.toFixed(1) + "bp" : "-"}</div><div class="small muted">${s.n ? `n=${s.n} · p90 ${s.p90_bps}bp · 가정 ${s.assumed_bps}bp` : `체결 기록 없음 · 가정 ${s.assumed_bps}bp`}</div></div>`).join("");
  el.innerHTML = `
  <div class="card"><div class="card-h"><h3>주문 내역 <span class="small dim">모든 주문은 멱등 키로 추적 · 재시작하면 미완료 주문을 증권사에서 확인·취소 후 확정</span></h3><div class="right">${tabs}</div></div>
    <div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px">${Object.entries(o.counts).map(([k, v]) => `<span class="chip ${stCls[k] || ""}">${stKo[k] || k} ${v}</span>`).join("")}</div>
    <div class="stat-row" style="grid-template-columns:repeat(3,1fr)">${sl}</div></div>
  ${card("", rows ? `<div class="scroll" style="max-height:640px"><table><thead><tr><th>시각</th><th>장부</th><th>종목</th><th>구분</th><th class="r">수량</th><th>상태</th><th class="r">체결가</th><th class="r">슬리피지</th><th>멱등 키 · 주문번호</th><th>사유</th></tr></thead><tbody>${rows}</tbody></table></div>` : empty("주문 기록 없음"))}`;
  document.querySelectorAll("#om-tabs button").forEach((b) => b.onclick = () => { S.orderMode = b.dataset.k; render(); });
  document.querySelectorAll("tr[data-ev]").forEach((x) => x.onclick = () => { location.hash = `#evidence/${x.dataset.ev}`; });
}

function pfTabs() {
  return `<div class="tabs" id="pf-tabs">${["paper", "shadow", "live"].map((m) => `<button data-m="${m}" class="${S.pfMode === m ? "on" : ""}">${m.toUpperCase()}</button>`).join("")}</div>`;
}

function portfolioSummary(pf) {
  const stock = pf.positions.reduce((a, p) => a + (p.value || 0), 0);
  const segs = [{ label: "현금", value: Math.max(pf.cash, 0), color: "#64748b" },
    ...pf.positions.slice(0, 6).map((p, i) => ({ label: p.name, value: p.value, color: PALETTE[i] }))];
  const rest = pf.positions.slice(6).reduce((a, p) => a + p.value, 0);
  if (rest > 0) segs.push({ label: "기타", value: rest, color: "#334155" });
  const eq = pf.equity || 1;
  return `<div class="donut-wrap">${donut(segs, "₩" + num(pf.equity / 10000) + "만")}
    <div class="legend">${segs.map((s) => `<div><span class="sw" style="background:${s.color}"></span>${esc(s.label)}<b class="num">${((s.value / eq) * 100).toFixed(1)}%</b></div>`).join("")}</div></div>
    <div class="small muted" style="margin:10px 0 4px">누적 수익률 ${pct(pf.return_pct)} · 주식 ${((stock / eq) * 100).toFixed(0)}%</div>
    ${pf.positions.length ? `<div class="scroll"><table><thead><tr><th>종목</th><th class="r">수량</th><th class="r">수익률</th></tr></thead><tbody>
    ${pf.positions.slice(0, 5).map((p) => `<tr><td>${esc(p.name)}<span class="sub num">평균 ${price(p.avg_price, p.symbol)}</span></td><td class="r num">${num(p.qty)}</td><td class="r">${pct(p.pnl_pct)}</td></tr>`).join("")}</tbody></table></div>` : ""}`;
}

function sentChip(s) {
  if (s === null || s === undefined) return "";
  if (s > 0.15) return '<span class="chip pos">긍정</span>';
  if (s < -0.15) return '<span class="chip neg">부정</span>';
  return '<span class="chip">중립</span>';
}

function scoreMatrix(d, compactDirection = false) {
  const rows = d.scoreboard.filter((r) => r.analyst !== "challenger" || !compactDirection);
  if (!rows.length) return empty("채점된 의견이 아직 없습니다 (horizon 경과 후 자동 채점)");
  const labels = d.analyst_labels, cats = d.category_labels;
  if (compactDirection) {
    const dir = rows.filter((r) => r.category === "direction").sort((a, b) => (b.accuracy ?? 0) - (a.accuracy ?? 0));
    return `<div style="display:grid;gap:10px">${dir.map((r) => `<div><div style="display:flex;justify-content:space-between;font-size:13px"><span>${esc(labels[r.analyst] || r.analyst)}</span><span class="num"><b>${r.accuracy === null ? "-" : (r.accuracy * 100).toFixed(0) + "%"}</b> <span class="dim small">n=${r.n}</span></span></div>
      <div class="conf-bar" style="margin-top:4px"><div style="width:${(r.accuracy ?? 0) * 100}%;background:${heatColor(r.accuracy)}"></div></div></div>`).join("")}</div>`;
  }
  const analysts = [...new Set(rows.map((r) => r.analyst))];
  const catKeys = Object.keys(cats).filter((c) => rows.some((r) => r.category === c));
  const get = (a, c) => rows.find((r) => r.analyst === a && r.category === c);
  return `<div class="scroll"><table class="heat"><thead><tr><th>AI</th>${catKeys.map((c) => `<th class="r">${esc(cats[c])}</th>`).join("")}</tr></thead><tbody>
    ${analysts.map((a) => `<tr><td><b>${esc(labels[a] || a)}</b></td>${catKeys.map((c) => { const r = get(a, c); return r ? `<td class="cell num" style="background:${heatColor(r.accuracy)}33">${(r.accuracy * 100).toFixed(0)}% <span class="dim small">n=${r.n}</span></td>` : '<td class="cell dim">-</td>'; }).join("")}</tr>`).join("")}
  </tbody></table></div><div class="small dim" style="margin-top:8px">성적은 앙상블 가중치에 자동 반영됩니다 (표본 30개 전까지는 50% 쪽으로 수축).</div>`;
}
function heatColor(a) { if (a === null || a === undefined) return css("--line-2"); return a >= 0.58 ? "#22c55e" : a >= 0.52 ? "#84cc16" : a >= 0.48 ? "#f59e0b" : "#ef4444"; }

function nameOf(sym) { const w = (S.data?.all_symbols || S.data?.watchlist || []).find((x) => x.symbol === sym); return w ? `${w.name} (${w.symbol})` : sym || ""; }

// ------------------------------------------------------------ 뷰: AI 종목 분석
async function viewAnalysis(el) {
  const d = S.data;
  const sym = S.symbol || d.watchlist[0]?.symbol;
  if (!sym) { el.innerHTML = card("AI 종목 분석", empty()); return; }
  const a = await api(`/api/analysis?symbol=${encodeURIComponent(sym)}`);
  const selList = [...(d.all_symbols || d.watchlist)];
  if (!selList.some((w) => w.symbol === sym)) selList.unshift({ symbol: sym, name: a.name || sym });
  const sel = `<select class="sym-select" id="sym-select">${selList.map((w) => `<option value="${esc(w.symbol)}" ${w.symbol === sym ? "selected" : ""}>${esc(w.name)} (${esc(w.symbol)})</option>`).join("")}</select>`;
  const c = a.consensus;
  const labels = d.analyst_labels;
  const ops = c ? (c.contributions || []).map((v) => {
    const det = a.details[v.analyst] || {};
    const reasons = (det.reasons || []).map((r) => `<li>${esc(r)}</li>`).join("");
    const risks = (det.risks || []).map((r) => `<li>${esc(r)}</li>`).join("");
    return `<div class="op"><h4><span>${esc(labels[v.analyst] || v.analyst)}</span><span class="${v.prob_up === null ? "flat" : v.stance >= 0 ? "up" : "down"} num">${v.prob_up === null ? "기권" : (v.prob_up * 100).toFixed(0) + "%"}</span></h4>
      <div class="small dim">${esc(v.backend)} · 가중치 ${(v.weight * 100).toFixed(0)}% · 확신 ${(v.confidence * 100).toFixed(0)}%</div>
      <div class="small" style="margin-top:6px">${esc(det.summary || v.summary || "")}</div>
      ${det.veto_reason ? `<div class="veto" style="margin-top:8px">⛔ ${esc(det.veto_reason)}</div>` : ""}
      ${reasons ? `<ul>${reasons}</ul>` : ""}${risks ? `<div class="small muted" style="margin-top:6px">리스크</div><ul>${risks}</ul>` : ""}</div>`;
  }).join("") : "";
  const sc = a.scenario;
  const scen = sc ? `<div class="scen">${sc.cases.map((x) => `<div><div class="t ${x.name}">${{ bull: "강세", base: "기준", bear: "약세" }[x.name]} <span class="dim small">${(x.probability * 100).toFixed(0)}%</span></div>
    <div class="num" style="margin:4px 0">${price(x.price_low, sym)} ~ ${price(x.price_high, sym)}</div><div class="small muted">${esc(x.narrative)}</div></div>`).join("")}</div>
    <div class="small dim" style="margin-top:8px">기준가 ${price(sc.last_close, sym)} · 1일 변동성 σ ${(sc.sigma * 100).toFixed(2)}% · 국면 ${esc(REGIME_KO[sc.regime] || sc.regime || "-")}</div>` : empty("시나리오 없음 (예측 모드 실행 필요)");
  const sim = a.similar.map((s) => `<tr><td class="dim small">${date(s.ts)}</td><td style="white-space:normal">${esc(s.text)}</td><td class="r num small">${s.similarity.toFixed(2)}</td></tr>`).join("");
  const hist = a.history.map((h) => `<tr><td class="dim small">${date(h.ts)}</td><td>${badge(h.action)}</td><td class="r num">${(h.prob_up * 100).toFixed(0)}%</td><td class="r num">${Math.round(h.confidence)}</td><td class="r">${h.realized === null ? '<span class="dim">대기</span>' : pct(h.realized)}</td><td class="r">${h.correct === null ? "" : h.correct ? "✔" : "✘"}</td></tr>`).join("");

  const isGlobal = a.market === "GLOBAL" || (!c && !/^\d{6}$/.test(sym));
  const fetchErr = a.fetch?.error && !a.fetch?.ok;
  const globalNote = isGlobal ? `<div class="lesson" style="margin-top:12px">🌐 <b>해외 종목</b> — 국내 코어 전략·AI 합의(자동 매매) 대상이 아닙니다. 아래 일정·지표·전망은 무료 공개 자료(Yahoo·Nasdaq)입니다.
    ${fetchErr ? `<div class="veto" style="margin-top:6px">시세를 받지 못했습니다 (네트워크): ${esc(a.fetch.error)}</div>` : ""}</div>` : "";
  const isKRsym = /^\d{6}$/.test(sym);
  const autoAn = isKRsym && !c && a.has_llm && !(S._analyzed?.[sym] && Date.now() - S._analyzed[sym] < 600e3);
  const anCard = isKRsym && !c ? analyzeCard(sym, a.name || sym, autoAn) : "";
  const cur = a.currency || (isKRsym ? "KRW" : "USD");
  const chgCls = a.chg_pct == null ? "flat" : a.chg_pct >= 0 ? "up" : "down";
  const head = `<div id="pf-sit" class="pf-sit"></div><div class="card stock-head">
    <div class="sh-top"><div style="min-width:0">
      <div class="sh-name">${esc(a.name || sym)} <span class="dim small">${esc(sym)}</span> <span class="chip xs">${isGlobal ? "해외" : "국내"}</span></div>
      <div class="sh-price"><span class="num" data-live-sym="${esc(sym)}">${a.last == null ? "-" : esc(priceCur(a.last, cur))}</span>
        <span class="${chgCls} num">${a.chg == null ? "" : `${a.chg >= 0 ? "▲" : "▼"} ${esc(priceCur(Math.abs(a.chg), cur))} (${pct(a.chg_pct)})`}</span>
        <span class="xs dim">${a.last_ts ? date(a.last_ts) + " 종가" : ""}</span></div></div>
      <div class="sh-act">${sel}${isKRsym ? '<button class="btn-sm" id="tk-jump">모의 주문</button>' : ""}<button class="btn-sm primary" data-ask="${esc(a.name || sym)} 지금 사도 될까? 일정·지표·뉴스 보고 판단 근거 알려줘">${ICONS.chat} AI 에게 묻기</button></div></div>
    ${c ? `<div class="sh-pred"><span class="xs muted">AI 예상 (${esc(date(c.as_of))})</span> <b>${c.horizon || 5}거래일 상승 확률 ${(c.prob_up * 100).toFixed(0)}%</b>
      ${c.expected_return != null ? ` · 예상 <b class="${c.expected_return >= 0 ? "up" : "down"}">${c.expected_return >= 0 ? "+" : ""}${(c.expected_return * 100).toFixed(1)}%</b>` : ""} ${badge(c.action)}
      ${c.trigger ? `<span class="chip xs" title="${esc(c.trigger)}">⚡ 이벤트 재분석</span>` : ""}</div>` : ""}
    <div id="pf-os" class="pf-os"></div>
    <div id="pf-fresh" class="pf-fresh"></div>
    <div id="pf-trust" class="pf-trust"></div>
    <div id="pf-dday" style="margin-top:8px"></div>
    <div class="small muted" style="margin:12px 0 6px"><b style="color:var(--text)">다가오는 일정</b> · 실적 발표 · 배당 · 공시</div>
    <div id="pf-events"><div class="ev-none small dim">일정 불러오는 중…</div></div>${globalNote}${anCard}</div>`;
  const aiSections = c || scen.includes("scen") || hist ? `
  <div class="grid g-2">
    ${card("AI 합의 신호 <span class='small dim'>플랫폼 AI · 매일 채점</span>", consensusBlock(c, labels), c ? `<span class="small dim">${time(c.as_of, true)}</span>` : "")}
    ${card("다음 거래일 시나리오", scen)}
  </div>
  ${ops ? card("AI 별 독립 의견 <span class='small dim'>서로의 의견을 모른 채 판단 · Risk AI 거부권은 희석되지 않음</span>", `<div class="op-grid">${ops}</div>`) : ""}
  ${hist ? card("판단 이력 · 채점", `<div class="scroll" style="max-height:360px"><table><thead><tr><th>일자</th><th>신호</th><th class="r">P(상승)</th><th class="r">신뢰도</th><th class="r">실제</th><th class="r"></th></tr></thead><tbody>${hist}</tbody></table></div>`) : ""}` : "";
  // v16 순서: 차트 → 뉴스 → 공시 → 실적 → 재무 → 수급 → AI → Risk → 내 보유 (섹션은 ⚙ 로 숨기기·순서 변경, 서버 저장)
  el.innerHTML = `${head}
  <div id="pf-nav" class="pf-nav"></div>
  <div id="pf-body">
  <section data-w="pf-chart"><div class="grid g-21">
    ${card("차트 <span class='small dim'>AI 매수 관심구간 · 목표 · 위험 · 지지/저항 · 뉴스·공시·실적 표시</span>", `<div id="an-chart" class="chart"></div>`)}
    ${card("투자 전 체크 <span class='small dim'>핵심 지표</span>", `<div id="pf-stats"><div class="ev-none small dim">불러오는 중…</div></div>`)}
  </div></section>
  <section data-w="pf-news"><div id="pf-news"></div></section>
  <section data-w="pf-disc"><div id="pf-disc"></div></section>
  <section data-w="pf-earn"><div id="pf-earn"></div></section>
  <section data-w="pf-fin"><div id="pf-sections"></div></section>
  <section data-w="pf-flow"><div id="pf-extra"></div></section>
  <section data-w="pf-ai"><div id="pf-why"></div><div id="pf-verify"></div><div id="pf-desk"></div>${aiSections}
    ${sim ? card("과거 유사 사례 (RAG 메모리)", `<div class="scroll"><table><tbody>${sim}</tbody></table></div>`) : ""}</section>
  <section data-w="pf-risk"><div class="grid g-2"><div id="pf-risk"></div><div id="pf-pretrade"></div></div><div id="pf-ticket"></div></section>
  <section data-w="pf-mine"><div id="pf-hold"></div><div class="grid g-2"><div id="pf-story"></div><div id="pf-thesis"></div></div></section>
  </div>`;
  $("#sym-select").onchange = (e) => { location.hash = `#analysis/${e.target.value}`; };
  if (anCard) bindAnalyze(sym, autoAn, () => render());
  candleChart($("#an-chart"), sym);
  whyCard(sym); holdCard(sym); stockPage(sym, a);  // 왜 BUY/SELL/NO TRADE · 내 보유 · 과거 적중률 (truth.js)
  if (typeof stockDesk === "function") stockDesk(sym);  // 매매 계획 · 이벤트 · 옵션 · 실적 모델 · 관계 — 종목 정보와 따로 (desk.js)
  loadProfile(sym, a.name || sym, a);
}

// ------------------------------------------------------------ 뷰: 시장 분석
function viewMarket(d) {
  const m = d.market;
  const stats = m.regime ? `<div class="stat-row">
    <div class="stat"><div class="l">현재 국면</div><div class="big">${esc(m.regime_label)}</div></div>
    <div class="stat"><div class="l">추세 (MA50/200)</div><div class="big ${m.trend >= 0 ? "up" : "down"}">${(m.trend * 100).toFixed(1)}%</div></div>
    <div class="stat"><div class="l">변동성 분위</div><div class="big">${Math.round(m.vol_pct * 100)}</div></div>
    <div class="stat"><div class="l">52주 고점 대비</div><div class="big down">${(m.drawdown * 100).toFixed(1)}%</div></div></div>
    <div class="small muted" style="margin-top:10px">리스크 엔진 노출 배수 <b style="color:var(--text)">${m.exposure_multiplier}</b> · 20일 모멘텀 ${pct(m.momentum)} · 지지 ${num(m.support, 2)} / 저항 ${num(m.resistance, 2)}</div>` : empty();
  const macro = d.macro.length ? `<table><thead><tr><th>지표</th><th class="r">값</th><th class="r">변화</th></tr></thead><tbody>${d.macro.map((x) => `<tr><td>${esc(x.label)}</td><td class="r num">${num(x.last, 2)}</td><td class="r">${pct(x.chg_pct)}</td></tr>`).join("")}</tbody></table>` : empty("FRED_API_KEY 설정 후 수집");
  const hist = (m.history || []).slice(-20).reverse().map(([t, r]) => `<tr><td class="dim small">${date(t)}</td><td>${esc(r)}</td></tr>`).join("");
  return `
  ${card("시장 국면 (Market Regime Engine)", stats)}
  <div class="grid g-21">
    ${card("지수", `<div class="idx-row">${d.indices.map((i) => `<div class="idx"><div class="n">${esc(i.name)}</div><div class="v num">${num(i.last, 2)}</div><div class="small">${pct(i.chg_pct)}</div>${spark(i.spark)}</div>`).join("")}</div>
      <div id="idx-chart" class="chart" style="margin-top:14px"></div>`)}
    ${card("경제지표", macro)}
  </div>
  ${card("국면 변화 이력", hist ? `<div class="scroll" style="max-height:300px"><table><tbody>${hist}</tbody></table></div>` : empty())}`;
}

// ------------------------------------------------------------ 뷰: 포트폴리오
function viewPortfolio(d) {
  const pf = d.portfolios[S.pfMode] || {};
  const stats = pf.equity ? `<div class="stat-row">
    <div class="stat"><div class="l">평가 자산</div><div class="big num">₩${num(pf.equity)}</div></div>
    <div class="stat"><div class="l">현금</div><div class="big num">₩${num(pf.cash)}</div></div>
    <div class="stat"><div class="l">누적 수익률</div><div class="big">${pct(pf.return_pct)}</div></div>
    <div class="stat"><div class="l">보유 종목</div><div class="big num">${pf.positions.length}</div></div></div>` : empty(`${S.pfMode} 기록 없음`);
  const rows = (pf.positions || []).map((p) => `<tr><td><b>${esc(p.name)}</b> <span class="dim small">${esc(p.symbol)}</span></td><td class="r num">${num(p.qty)}</td><td class="r num">${price(p.avg_price, p.symbol)}</td><td class="r num">${price(p.last, p.symbol)}</td><td class="r num">₩${num(p.value)}</td><td class="r num">${(p.weight * 100).toFixed(1)}%</td><td class="r">${pct(p.pnl_pct)}</td></tr>`).join("");
  return `
  <div class="card"><div class="card-h"><h3>포트폴리오</h3><div class="right">${pfTabs()}</div></div>${stats}
    <div class="small muted" style="margin-top:10px">PAPER = 가상매매 · SHADOW = 실제 주문이었다면(호가 기준 체결) · LIVE = 실계좌 (Shadow 검증 통과 champion 모델 + 안전장치 필요)</div></div>
  <div class="grid g-21">
    ${card("자산 추이", `<div id="eq-chart" class="chart"></div>`)}
    ${card("자산 구성", pf.equity ? portfolioSummary(pf) : empty())}
  </div>
  ${card("보유 종목", rows ? `<div class="scroll"><table><thead><tr><th>종목</th><th class="r">수량</th><th class="r">평균가</th><th class="r">현재가</th><th class="r">평가금액</th><th class="r">비중</th><th class="r">수익률</th></tr></thead><tbody>${rows}</tbody></table></div>` : empty("보유 없음"))}`;
}

// ------------------------------------------------------------ 뷰: 거래/리스크
function viewTrades(d) {
  const t = d.trades.map((x) => `<tr><td class="dim small">${time(x.ts, true)}</td><td><span class="chip">${esc(x.mode)}</span></td><td>${esc(x.name)}</td><td class="${x.side === "buy" ? "up" : "down"}">${x.side === "buy" ? "매수" : "매도"}</td><td class="r num">${num(x.qty)}</td><td class="r num">${price(x.price, x.symbol)}</td><td class="r num">${num(x.fee)}</td><td class="small muted" style="white-space:normal">${esc([x.reason, ...(x.reasons || [])].filter(Boolean).join(" · "))}</td></tr>`).join("");
  const r = d.risk_log.map((x) => `<tr><td class="dim small">${time(x.ts, true)}</td><td><span class="chip">${esc(x.mode)}</span></td><td>${esc(x.name)}</td><td><span class="chip neg">${esc(x.status)}</span></td><td class="small" style="white-space:normal">${esc((x.reasons || []).join(" · "))}</td></tr>`).join("");
  return `
  ${card("체결 내역", t ? `<div class="scroll" style="max-height:480px"><table><thead><tr><th>시각</th><th>모드</th><th>종목</th><th>구분</th><th class="r">수량</th><th class="r">체결가</th><th class="r">비용</th><th>사유</th></tr></thead><tbody>${t}</tbody></table></div>` : empty("체결 없음"))}
  ${card("리스크 게이트 로그 <span class='small dim'>거부·축소된 주문과 Risk AI 거부권</span>", r ? `<div class="scroll" style="max-height:420px"><table><thead><tr><th>시각</th><th>단계</th><th>종목</th><th>결과</th><th>사유</th></tr></thead><tbody>${r}</tbody></table></div>` : empty("거부된 주문 없음"))}`;
}

// ------------------------------------------------------------ 뷰: 뉴스
function viewNews(d) {
  const kinds = { all: "전체", news: "뉴스", disclosure: "공시" };
  const list = d.news.filter((n) => S.newsKind === "all" || n.kind === S.newsKind);
  const rows = list.map((n) => `<tr><td class="dim small">${time(n.ts, true)}</td><td><span class="chip">${n.kind === "news" ? "뉴스" : "공시"}</span></td><td style="white-space:normal">${esc(n.title)}</td><td class="small">${(n.symbols || []).map((s) => esc(nameOf(s).split(" (")[0])).join(", ")}</td><td class="small muted">${(n.events || []).map(esc).join(", ")}</td><td class="r">${sentChip(n.sentiment)}</td></tr>`).join("");
  return card("뉴스 & 공시", rows ? `<div class="scroll"><table><thead><tr><th>시각</th><th>구분</th><th>제목</th><th>종목</th><th>이벤트</th><th class="r">감성</th></tr></thead><tbody>${rows}</tbody></table></div>` : empty(),
    `<div class="tabs" id="news-tabs">${Object.entries(kinds).map(([k, v]) => `<button data-k="${k}" class="${S.newsKind === k ? "on" : ""}">${v}</button>`).join("")}</div>`);
}

// ------------------------------------------------------------ 뷰: 복기 / 성적표
async function viewReview(el) {
  const d = S.data;
  const reviews = await api("/api/reviews");
  const r = reviews[0];
  const s = r?.summary || {};
  const stats = r ? `<div class="stat-row">
    <div class="stat"><div class="l">채점된 합의 신호</div><div class="big num">${s.n_resolved ?? 0}</div></div>
    <div class="stat"><div class="l">합의 정확도</div><div class="big num">${s.consensus_accuracy == null ? "-" : (s.consensus_accuracy * 100).toFixed(0) + "%"}</div></div>
    <div class="stat"><div class="l">매매 신호 정확도</div><div class="big num">${s.traded_accuracy == null ? "-" : (s.traded_accuracy * 100).toFixed(0) + "%"}</div></div>
    <div class="stat"><div class="l">NO TRADE 가 피한 하락</div><div class="big num">${s.no_trade_avoided_loss_rate == null ? "-" : (s.no_trade_avoided_loss_rate * 100).toFixed(0) + "%"}</div></div></div>` : empty("복기 기록 없음");
  const lessons = r?.lessons?.length ? r.lessons.map((l) => `<div class="lesson">${esc(l)}</div>`).join("") : empty("아직 도출된 교훈이 없습니다");
  const misses = (s.worst_misses || []).map((m) => `<tr><td class="dim small">${date(m.as_of)}</td><td>${esc(nameOf(m.symbol).split(" (")[0])}</td><td>${badge(m.action)}</td><td class="r num">${Math.round(m.confidence)}</td><td class="r">${pct(m.realized)}</td><td class="small" style="white-space:normal">${esc(m.cause)}</td></tr>`).join("");
  const lbl = { ...REGIME_KO, low: "낮음", medium: "보통", high: "높음" };
  const grp = (arr, key) => (arr || []).map((x) => `<tr><td>${esc(lbl[x[key]] || x[key] || "-")}</td><td class="r num">${(x.accuracy * 100).toFixed(0)}%</td><td class="r num dim">${x.count}</td></tr>`).join("");
  el.innerHTML = `
  ${card(`자동 복기 <span class="small dim">${r ? esc(r.date) + " · 최근 " + (s.window_days || "") + "일" : ""}</span>`, stats)}
  ${card("AI 성적표 <span class='small dim'>카테고리별 적중률 · 실제 결과로 자동 채점</span>", scoreMatrix(d))}
  <div class="grid g-2">
    ${card("오늘의 교훈", lessons)}
    ${card("크게 틀린 판단 (고신뢰 오답)", misses ? `<div class="scroll"><table><thead><tr><th>일자</th><th>종목</th><th>신호</th><th class="r">신뢰도</th><th class="r">실제</th><th>원인 분류</th></tr></thead><tbody>${misses}</tbody></table></div>` : empty())}
  </div>
  <div class="grid g-3">
    ${card("국면별 정확도", s.by_regime ? `<table><tbody>${grp(s.by_regime, "regime")}</tbody></table>` : empty())}
    ${card("신뢰도 구간별 (보정)", s.by_confidence ? `<table><tbody>${grp(s.by_confidence, "conf_bucket")}</tbody></table>` : empty())}
    ${card("의견 충돌별", s.by_conflict ? `<table><tbody>${grp(s.by_conflict, "conflict")}</tbody></table>` : empty())}
  </div>`;
}

// ------------------------------------------------------------ 뷰: 모델
function viewModels(d) {
  const st = { champion: "b-BUY", shadow: "b-NO_TRADE", candidate: "b-HOLD", rejected: "b-SELL", retired: "b-none" };
  const num3 = (v) => v == null ? "-" : Number(v).toFixed(2);
  const psrCls = (v, t) => v == null ? "" : v >= t ? "conflict-low" : "conflict-high";
  const rows = d.models.map((m) => `<tr class="click" data-model="${m.id}"><td><b>${esc(m.name)}</b><div class="dim small mono">${esc(m.version)}</div></td><td><span class="badge ${st[m.status] || "b-none"}">${esc(m.status.toUpperCase())}</span></td>
    <td class="r num">${m.sharpe ?? "-"}<span class="sub">${m.sharpe_ci ? `95% ${num3(m.sharpe_ci[0])}~${num3(m.sharpe_ci[1])}` : ""}</span></td>
    <td class="r num ${psrCls(m.psr, 0.9)}">${num3(m.psr)}</td><td class="r num ${psrCls(m.dsr, 0.5)}">${num3(m.dsr)}</td>
    <td class="r num ${m.stress_sharpe != null && m.stress_sharpe < 0 ? "conflict-high" : ""}">${m.stress_sharpe ?? "-"}</td>
    <td class="r">${pct(m.total_return, 1)}<span class="sub">BM ${m.benchmark_return == null ? "-" : (m.benchmark_return * 100).toFixed(0) + "%"}</span></td><td class="r">${pct(m.mdd, 1)}</td>
    <td class="r num">${m.ic ?? "-"}</td>
    <td class="small muted" style="white-space:normal;max-width:340px">${esc(m.notes || "")}${m.shadow ? `<div class="dim">Shadow ${m.shadow.days}일 · 정확도 ${(m.shadow.accuracy * 100).toFixed(0)}% · MDD ${(m.shadow.max_drawdown * 100).toFixed(1)}%</div>` : ""}</td></tr>`).join("");
  const sel = d.models.find((m) => m.id === S.modelId) || d.models.find((m) => m.status === "champion") || d.models[0];
  const cal = sel?.calibration?.length ? `<table><thead><tr><th>예측 구간</th><th class="r">표본</th><th class="r">예측 확률</th><th class="r">실제 상승률</th><th>차이</th></tr></thead><tbody>
    ${sel.calibration.map((c) => { const gap = c.actual - c.predicted; return `<tr><td class="mono small">${esc(c.bin)}</td><td class="r num">${num(c.n)}</td><td class="r num">${(c.predicted * 100).toFixed(1)}%</td><td class="r num">${(c.actual * 100).toFixed(1)}%</td>
      <td><div class="stance" style="width:140px"><div style="left:${gap >= 0 ? 50 : 50 + gap * 500}%;width:${Math.min(50, Math.abs(gap) * 500)}%;background:${Math.abs(gap) < 0.03 ? css("--good") : css("--warn")}"></div></div></td></tr>`; }).join("")}</tbody></table>
    <div class="small dim" style="margin-top:8px">예측 확률과 실제 상승률이 가까울수록 확률이 '정직'합니다. 앙상블은 이 확률을 그대로 결합하므로 보정이 중요합니다.</div>` : empty();
  return `
  ${card("모델 레지스트리 <span class='small dim'>검증된 모델만 교체</span>", `<div class="small muted" style="margin-bottom:12px">candidate → (walk-forward 백테스트 게이트) → shadow → (Shadow 실전 검증 게이트) → champion. Live 는 champion 만 사용합니다.</div>
    ${rows ? `<div class="scroll"><table><thead><tr><th>모델</th><th>상태</th><th class="r">Sharpe</th><th class="r" title="P(진짜 Sharpe > 0)">PSR</th><th class="r" title="다중검정 보정 PSR">DSR</th><th class="r" title="비용 2배">스트레스</th><th class="r">수익률</th><th class="r">MDD</th><th class="r" title="날짜별 횡단면 순위상관">IC</th><th>게이트 결과</th></tr></thead><tbody>${rows}</tbody></table></div>` : empty()}`)}
  <div class="grid g-2">
    ${card(`확률 보정 (Calibration) <span class="small dim">${sel ? esc(sel.name + " " + sel.version.slice(0, 8)) : ""}</span>`, cal)}
    ${card("게이트 기준", `<ul class="plain small">
      <li><span class="chk">◉</span>PSR ≥ 0.90 — 수익률 분포(왜도·첨도)와 표본 길이를 고려해 Sharpe 가 우연이 아닐 확률</li>
      <li><span class="chk">◉</span>DSR ≥ 0.50 — 지금까지 시도한 후보 수만큼 기준을 올린 PSR (많이 시도할수록 엄격)</li>
      <li><span class="chk">◉</span>비용 2배 스트레스 Sharpe ≥ 0 — 비용 가정이 틀려도 손실 전략이 아닐 것</li>
      <li><span class="chk">◉</span>IC ≥ 0.02, IC t-stat ≥ 2 — 순위 예측력이 통계적으로 유의</li>
      <li><span class="chk">◉</span>Brier ≤ 기저율 기준 — 확률이 '항상 평균만 말하기'보다 나쁘지 않을 것</li>
      <li><span class="chk">◉</span>MDD ≥ -25%, 기존 champion 대비 개선 · Shadow 20일 이상 실전 검증</li></ul>`)}
  </div>`;
}

// ------------------------------------------------------------ 뷰: 코어-위성
async function viewCore(el) {
  const r = await api("/api/core-satellite");
  const p = r.plan;
  if (!p || !p.core) { el.innerHTML = card("코어-위성 전략", empty("아직 실행 기록 없음 — quant-ai cycle 또는 replay 실행")); return; }
  const nm = (c) => esc((p.names || {})[c] || c);
  const f1 = (v) => v == null ? "-" : (v * 100).toFixed(1) + "%";
  const coreRows = p.core.map((c) => `<tr class="click" data-sym="${esc(c)}"><td class="num dim">#${(p.ranks[c] ?? -1) + 1}</td><td><b>${nm(c)}</b><span class="sub">${esc(c)}</span></td><td class="r num">${f1(p.weights[c])}</td><td class="r num">${p.scores[c] == null ? "-" : Number(p.scores[c]).toFixed(2)}</td></tr>`).join("");
  const sat = p.satellite.map((x) => `<tr class="click" data-sym="${esc(x.symbol)}"><td><b>${nm(x.symbol)}</b><span class="sub">${esc(x.symbol)}</span></td><td class="r num">${Math.round(x.confidence)}</td><td class="r num">${(x.prob_up * 100).toFixed(0)}%</td><td class="r num">${f1(x.weight)}</td></tr>`).join("");
  const vet = Object.entries(p.vetoed).map(([c, w]) => `<tr><td><b>${nm(c)}</b><span class="sub">#${(p.ranks[c] ?? -1) + 1} 순위였으나 편입 거부</span></td><td class="small" style="white-space:normal">${esc(w)}</td></tr>`).join("")
    + Object.entries(p.exits).map(([c, w]) => `<tr><td><b>${nm(c)}</b><span class="sub down">보유 중 긴급 청산</span></td><td class="small" style="white-space:normal">${esc(w)}</td></tr>`).join("");
  const labels = { "attr-core": "① 코어만 (AI 없음)", "attr-veto": "② 코어 + AI 거부권", "attr-full": "③ 코어 + 거부권 + 위성", paper: "실제 장부 (PAPER)", shadow: "실제 장부 (SHADOW)", live: "실제 장부 (LIVE)", kospi: "KOSPI" };
  const colors = { "attr-core": "#94a3b8", "attr-veto": "#22c55e", "attr-full": "#3b82f6", kospi: "#f59e0b" };
  const b = r.books;
  const base = b["attr-core"]?.return;
  const bookRows = Object.entries(b).map(([k, v]) => `<tr><td><b>${esc(labels[k] || k)}</b></td><td class="r">${pct(v.return)}</td>
    <td class="r num">${v.perf?.max_drawdown == null ? "-" : (v.perf.max_drawdown * 100).toFixed(1) + "%"}</td>
    <td class="r">${base == null || k === "attr-core" || k === "kospi" ? '<span class="dim">-</span>' : pct(v.return - base)}</td><td class="r num dim">${v.days}일</td></tr>`).join("");
  const ai = p.ai || {};
  const tr = p.trend || {};
  el.innerHTML = `
  ${healthCard(r.health)}
  <div class="card"><div class="card-h"><h3>코어-위성 전략</h3><div class="right"><span class="small dim">기준 ${date(p.as_of)} · ${esc(p.mode)} · 마지막 코어 리밸런싱 ${date(p.last_rebalance)} (${p.rebalances || 0}회)</span></div></div>
    <div class="stat-row">
      <div class="stat"><div class="l">코어 ${Math.round(p.config.core_weight * 100)}%</div><div class="big num">${p.core.length}종목</div><div class="small dim">모멘텀+저변동성+52주고점 · ${p.config.core_rebalance_days}거래일 리밸런싱</div></div>
      <div class="stat"><div class="l">위성 ${Math.round((1 - p.config.core_weight) * 100)}%</div><div class="big num">${p.satellite.length}/${p.config.satellite_k}</div><div class="small dim">AI 합의 BUY · 신뢰도 ≥ ${p.config.satellite_min_confidence}</div></div>
      <div class="stat"><div class="l">AI 분석</div><div class="big num">${ai.analyzed ?? "-"}종목</div><div class="small dim">BUY ${ai.buys ?? 0} · 거부권 ${ai.vetoes ?? 0} · 긴급청산 ${ai.exits ?? 0}</div></div>
      <div class="stat"><div class="l">유니버스</div><div class="big num">${p.universe_size}</div><div class="small dim">전월 말 시총 상위 (point-in-time)</div></div></div>
    <div class="small muted" style="margin-top:10px">${tr.index ? `추세 필터: KOSPI ${num(tr.index)} ${tr.below ? "&lt;" : "≥"} 200일선 ${num(tr.ma)} → 코어 비중 ×${tr.applied_scale ?? tr.scale} · ` : ""}${(p.notes || []).map(esc).join(" · ")}</div></div>
  ${card("AI 기여도 측정 <span class='small dim'>같은 가격·같은 코어로 굴린 가상 장부 비교 — AI 가 수익을 더했는가?</span>",
    `${bookRows ? `<div class="scroll"><table><thead><tr><th>장부</th><th class="r">수익률</th><th class="r">MDD</th><th class="r">코어만 대비</th><th class="r">기간</th></tr></thead><tbody>${bookRows}</tbody></table></div>` : empty()}
     <div id="cs-chart" class="chart" style="margin-top:12px"></div>
     <div class="small dim" style="margin-top:8px">기간이 짧으면 차이는 대부분 우연입니다. 최소 수개월 누적 후 판단하세요 (LLM 은 과거로 백테스트할 수 없어 전진 성과만 유효).</div>`)}
  <div class="grid g-2">
    ${card(`코어 구성 <span class="small dim">${p.core_rebalanced ? "이번에 리밸런싱" : "유지 중"}</span>`, `<div class="scroll" style="max-height:520px"><table><thead><tr><th>순위</th><th>종목</th><th class="r">비중</th><th class="r">점수</th></tr></thead><tbody>${coreRows}</tbody></table></div>`)}
    <div style="display:grid;gap:16px;align-content:start">
      ${card("위성 (AI 종목 선택)", sat ? `<table><thead><tr><th>종목</th><th class="r">신뢰도</th><th class="r">P(상승)</th><th class="r">비중</th></tr></thead><tbody>${sat}</tbody></table>` : empty("확신 있는 AI 합의 BUY 없음 → 위성 비중은 현금"))}
      ${card("AI 거부권 · 긴급 청산", vet ? `<table><tbody>${vet}</tbody></table>` : empty("이번 사이클 거부 없음"))}
    </div>
  </div>`;
  const series = Object.entries(b).filter(([k]) => colors[k]).map(([k, v]) => ({ data: v.curve, color: colors[k], title: labels[k] }));
  if (series.length) lineChart($("#cs-chart"), series);
}

// ------------------------------------------------------------ 전략 건강검진
const H_ICON = { ok: "🟢", warn: "🟡", critical: "🔴", insufficient: "⚪" };
const H_LABEL = { ok: "정상 범위", warn: "주의 (과거 하위 5%)", critical: "위험 (과거에 없던 수준)", insufficient: "판단 보류" };
function healthCard(h) {
  if (!h) return "";
  const fmt = (c) => c.value == null ? "-" : c.name === "underwater" ? `${Math.round(c.value)}일` : c.name === "factor_ic" ? "t " + Number(c.value).toFixed(2) : pct(c.value, 1);
  const rows = (h.checks || []).map((c) => `<tr><td>${H_ICON[c.status]} <b>${esc(c.label)}</b></td><td class="r">${fmt(c)}</td><td class="small muted" style="white-space:normal">${esc(c.note)}</td></tr>`).join("");
  const color = { ok: "var(--good)", warn: "var(--warn)", critical: "var(--bad)" }[h.status] || "var(--line-2)";
  const e = h.expectations || {};
  return card(`전략 건강검진 <span class="small dim">실제 성과가 16년 백테스트 범위 안인가 · ${esc(h.mode || "")} ${h.days ?? 0}일</span>`,
    `<div class="lesson" style="border-left-color:${color};margin-bottom:12px"><b>${H_ICON[h.status]} ${H_LABEL[h.status] || esc(h.status)}</b> — ${esc(h.action || h.error || "")}</div>
     ${rows ? `<div class="scroll"><table><thead><tr><th>항목</th><th class="r">현재</th><th>과거 기준</th></tr></thead><tbody>${rows}</tbody></table></div>` : ""}
     ${e.cagr != null ? `<div class="small dim" style="margin-top:8px">기대치 참고: 과거 연평균 ${(e.cagr * 100).toFixed(1)}% · 1년 보유 시 플러스 ${Math.round(e.positive_1y_share * 100)}% · KOSPI 초과 ${Math.round(e.beat_kospi_1y_share * 100)}% (1년 단위로는 절반 가까이 지거나 잃습니다)</div>` : ""}`,
    `<button class="btn-sm" id="health-refresh">다시 검사</button>`);
}

// ------------------------------------------------------------ 뷰: 주문표 (수동 매매 · 다른 증권사 · ISA)
async function viewOrders(el) {
  const saved = { cash: safeGet("qa_os_cash") || "", holdings: safeGet("qa_os_holdings") || "" };
  el.innerHTML = `
  ${card("리밸런싱 주문표 <span class='small dim'>자동매매 없이 이 전략을 따라 하는 방법 — 주문은 내지 않습니다</span>",
    `<div class="grid g-2">
      <div>
        <label class="small muted">보유 종목 (한 줄에 하나: 종목코드,수량)</label>
        <textarea id="os-holdings" class="input" rows="10" placeholder="005930,10&#10;000660,3">${esc(saved.holdings)}</textarea>
      </div>
      <div>
        <label class="small muted">주문 가능 현금 (원)</label>
        <input id="os-cash" class="input" inputmode="numeric" placeholder="10000000" value="${esc(saved.cash)}">
        <label class="small" style="display:flex;gap:8px;align-items:center;margin-top:12px"><input type="checkbox" id="os-ai"> AI 오버레이 (거부권 · 위성) — LLM 키가 있으면 비용 발생</label>
        <button class="btn" id="os-run" style="margin-top:14px">주문표 만들기</button>
        <ul class="plain small muted" style="margin-top:14px">
          <li><span class="chk">①</span>월 1회(약 20거래일마다) 실행. 중간에는 손대지 않기</li>
          <li><span class="chk">②</span>매도 먼저 → 체결 확인 → 매수. 09:10 이후 지정가로</li>
          <li><span class="chk">③</span>지정가를 넘는 급변이면 그날은 건너뛰고 다음 날 다시 실행</li>
          <li><span class="chk">④</span>입력한 값은 이 브라우저에만 저장됩니다</li></ul>
      </div></div>`)}
  <div id="os-result" style="display:grid;gap:16px;min-width:0"></div>`;
  $("#os-run").onclick = async () => {
    const cash = $("#os-cash").value.replace(/[, 원]/g, "");
    const holdings = $("#os-holdings").value;
    safeSet("qa_os_cash", cash); safeSet("qa_os_holdings", holdings);
    const out = $("#os-result");
    out.innerHTML = card("계산 중", empty("팩터 점수·추세·목표 수량 계산 중…"));
    try {
      const r = await api("/api/order-sheet", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ cash, holdings, use_ai: $("#os-ai").checked }) });
      out.innerHTML = orderSheetView(r);
      $("#os-csv").onclick = () => {
        const blob = new Blob(["\ufeff" + r.csv], { type: "text/csv;charset=utf-8" });
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob); a.download = `orders_${r.as_of}.csv`; a.click();
        setTimeout(() => URL.revokeObjectURL(a.href), 1000);
      };
    } catch (e) { out.innerHTML = card("오류", `<div class="veto">${esc(e.message)}</div>`); }
  };
}

function orderSheetView(r) {
  const sideKo = { BUY: "매수", SELL: "매도", HOLD: "유지" };
  const cls = { BUY: "up", SELL: "down", HOLD: "dim" };
  const rows = r.lines.map((l, i) => `<tr><td class="num dim">${l.side === "HOLD" ? "" : i + 1}</td><td><b class="${cls[l.side]}">${sideKo[l.side]}</b></td>
    <td><b>${esc(l.name)}</b><span class="sub">${esc(l.symbol)}</span></td><td class="r num"><b>${l.side === "HOLD" ? "-" : num(l.qty)}</b></td>
    <td class="r num">${l.limit_price ? num(l.limit_price) : "-"}</td><td class="r num">${num(l.ref_price)}</td><td class="r num">${l.side === "HOLD" ? "-" : num(l.value)}</td>
    <td class="r num dim">${num(l.current_qty)} → ${num(l.target_qty)}</td><td class="r num">${(l.target_weight * 100).toFixed(1)}%</td>
    <td class="small muted" style="white-space:normal">${esc(l.reason)}</td></tr>`).join("");
  const t = r.totals, tr = r.trend || {};
  return `
  <div class="stat-row">
    <div class="stat"><div class="l">평가금액</div><div class="big num">${num(r.equity)}</div><div class="small dim">기준일 ${esc(r.as_of)} 종가</div></div>
    <div class="stat"><div class="l">매도 / 매수</div><div class="big num">${t.n_sell} / ${t.n_buy}</div><div class="small dim">${num(t.sell_value)}원 / ${num(t.buy_value)}원</div></div>
    <div class="stat"><div class="l">예상 비용</div><div class="big num">${num(t.cost)}</div><div class="small dim">수수료 + 매도세 · 회전율 ${(t.turnover * 100).toFixed(0)}%</div></div>
    <div class="stat"><div class="l">추세 필터</div><div class="big ${tr.below ? "down" : ""}">${tr.below ? "축소" : "정상"}</div><div class="small dim">${tr.index ? `KOSPI ${num(tr.index)} vs 200일선 ${num(tr.ma)}` : "지수 데이터 없음"}</div></div></div>
  ${card("주문 목록 <span class='small dim'>위에서부터 순서대로 · 지정가는 상한(매수)/하한(매도)</span>",
    `<div class="scroll"><table><thead><tr><th>#</th><th>구분</th><th>종목</th><th class="r">수량</th><th class="r">지정가</th><th class="r">기준가</th><th class="r">금액</th><th class="r">보유→목표</th><th class="r">목표비중</th><th>사유</th></tr></thead><tbody>${rows}</tbody></table></div>
     ${(r.notes || []).length ? `<div class="small muted" style="margin-top:10px">${r.notes.map(esc).join("<br>")}</div>` : ""}
     <div style="margin-top:12px;display:flex;gap:10px;align-items:center"><button class="btn" id="os-csv">CSV 다운로드</button><span class="small dim">주문 후 현금 ${num(r.cash_after)}원 (투자 비중 ${(t.invested_after * 100).toFixed(0)}%)</span></div>`)}`;
}

// ------------------------------------------------------------ 뷰: 실데이터 연구
async function viewResearch(el) {
  const r = await api("/api/research");
  if (r.kind === "lab") return viewLabResult(el, r);
  if (!r.trials) { el.innerHTML = card("실데이터 연구", empty("리포트 없음 — quant-ai research krx --marcap-dir ... 실행")); return; }
  const f2 = (v) => v == null ? "-" : Number(v).toFixed(2);
  const rows = r.trials.map((t) => { const s = t.metrics.strategy, p = t.metrics.prediction; return `<tr class="${t.trial.name === r.best ? "" : ""}">
    <td><b>${esc(t.trial.name)}</b>${t.trial.name === r.best ? ' <span class="chip pos">최고</span>' : ""}</td>
    <td class="r num">${f2(s.sharpe)}<span class="sub">95% ${f2(s.sharpe_ci95[0])}~${f2(s.sharpe_ci95[1])}</span></td>
    <td class="r num">${f2(s.psr)}</td><td class="r num ${t.dsr < 0.5 ? "conflict-high" : "conflict-low"}">${f2(t.dsr)}</td>
    <td class="r">${pct(s.cagr, 1)}</td><td class="r">${pct(s.max_drawdown, 1)}</td>
    <td class="r num">${f2(p.ic * 100)}%<span class="sub">t=${f2(p.ic_t)}</span></td>
    <td class="r num">${f2(t.stress.sharpe)}</td><td class="r num ${t.information_ratio_vs_ew < 0 ? "down" : "up"}">${f2(t.information_ratio_vs_ew)}</td>
    <td class="r num">${t.metrics.avg_exposure == null ? "-" : Math.round(t.metrics.avg_exposure * 100) + "%"}</td></tr>`; }).join("");
  const best = r.trials.find((t) => t.trial.name === r.best) || r.trials[0];
  const years = Object.keys(best.yearly.strategy);
  const yrows = years.map((y) => `<tr><td class="mono">${y}</td><td class="r">${pct(best.yearly.strategy[y], 1)}</td><td class="r">${pct(best.yearly.kospi[y], 1)}</td><td class="r">${pct(best.yearly.universe_ew[y], 1)}</td>
    <td class="r num">${best.ic_by_year[y] == null ? "-" : (best.ic_by_year[y] * 100).toFixed(2) + "%"}</td></tr>`).join("");
  const ks = best.metrics.benchmark, ew = best.universe_ew, s = best.metrics.strategy;
  el.innerHTML = `
  ${card(`실제 KRX 데이터 walk-forward 연구 <span class="small dim">${esc(r.data.source)} · ${r.data.start}~${r.data.end} · 월말 시총 상위 ${r.data.top_n} (상장폐지 포함 ${r.data.symbols}종목) · 시도 ${r.n_trials}회</span>`,
    `<div class="stat-row">
      <div class="stat"><div class="l">전략 (${esc(r.best)})</div><div class="big num">${f2(s.sharpe)}</div><div class="small dim">Sharpe · CAGR ${(s.cagr * 100).toFixed(1)}% · MDD ${(s.max_drawdown * 100).toFixed(1)}%</div></div>
      <div class="stat"><div class="l">KOSPI (시총가중)</div><div class="big num">${f2(ks.sharpe)}</div><div class="small dim">CAGR ${(ks.cagr * 100).toFixed(1)}% · MDD ${(ks.max_drawdown * 100).toFixed(1)}%</div></div>
      <div class="stat"><div class="l">유니버스 동일가중</div><div class="big num">${f2(ew.sharpe)}</div><div class="small dim">CAGR ${(ew.cagr * 100).toFixed(1)}% · MDD ${(ew.max_drawdown * 100).toFixed(1)}%</div></div>
      <div class="stat"><div class="l">DSR (시도 ${r.n_trials}회 보정)</div><div class="big num ${best.dsr < 0.5 ? "down" : ""}">${f2(best.dsr)}</div><div class="small dim">0.5 미만이면 우연일 가능성이 큼</div></div></div>
    <div id="rs-chart" class="chart" style="margin-top:14px"></div>`)}
  ${card("설정별 결과 <span class='small dim'>가장 좋은 것만 고르지 않고 전부 공개</span>", `<div class="scroll"><table><thead><tr><th>설정</th><th class="r">Sharpe</th><th class="r">PSR</th><th class="r">DSR</th><th class="r">CAGR</th><th class="r">MDD</th><th class="r">IC</th><th class="r">비용2배</th><th class="r" title="유니버스 동일가중 대비 정보비율">IR</th><th class="r">평균 노출</th></tr></thead><tbody>${rows}</tbody></table></div>`)}
  ${card("연도별 수익률", `<div class="scroll"><table><thead><tr><th>연도</th><th class="r">전략</th><th class="r">KOSPI</th><th class="r">유니버스 EW</th><th class="r">IC</th></tr></thead><tbody>${yrows}</tbody></table></div>`)}`;
  lineChart($("#rs-chart"), [
    { data: best.equity, color: "#3b82f6", title: "전략" },
    { data: best.benchmark, color: "#94a3b8", title: "KOSPI" },
    { data: best.universe_ew_curve, color: "#f59e0b", title: "유니버스 EW" },
  ]);
}

function viewLabResult(el, r) {
  const f2 = (v) => v == null ? "-" : Number(v).toFixed(2);
  const pc = (v) => v == null ? "-" : (v * 100).toFixed(1) + "%";
  const row = (x) => { const d = x.dev.strategy, h = x.holdout.strategy, chosen = x.name === r.chosen; return `<tr>
    <td><b>${esc(x.name)}</b>${chosen ? ' <span class="chip pos">dev 선택</span>' : ""}</td>
    <td class="r num">${f2(d.sharpe)}</td><td class="r">${pct(d.cagr, 1)}</td><td class="r">${pct(d.max_drawdown, 1)}</td><td class="r num">${f2(x.dev_stress.sharpe)}</td><td class="r num ${x.dev_dsr < 0.5 ? "conflict-high" : "conflict-low"}">${f2(x.dev_dsr)}</td>
    <td class="r num" style="border-left:1px solid var(--line)">${f2(h.sharpe)}</td><td class="r">${pct(h.cagr, 1)}</td><td class="r">${pct(h.max_drawdown, 1)}</td><td class="r num ${x.holdout.ir_vs_universe_ew < 0 ? "down" : "up"}">${f2(x.holdout.ir_vs_universe_ew)}</td>
    <td class="r num">${x.turnover.toFixed(1)}배</td></tr>`; };
  const best = r.results.find((x) => x.name === r.chosen) || r.results[0];
  const k = best.holdout.kospi, e = best.holdout.universe_ew, bh = best.holdout.strategy, full = best.full;
  const years = Object.keys(best.yearly.strategy).slice(1);
  const yrows = years.map((y) => `<tr><td class="mono">${y}</td><td class="r">${pct(best.yearly.strategy[y], 1)}</td><td class="r">${pct(best.yearly.kospi[y], 1)}</td><td class="r">${pct(best.yearly.universe_ew[y], 1)}</td></tr>`).join("");
  el.innerHTML = `
  ${card(`전략 연구소 · 실제 KRX 데이터 <span class="small dim">${esc(r.data.source)} · ${r.data.start}~${r.data.end} · 월말 시총 상위 ${r.data.top_n} (상장폐지 포함 ${r.data.symbols}종목)</span>`,
    `<div class="small muted" style="margin-bottom:12px">설정 선택은 <b>개발 구간(~${esc(r.dev_end)})</b> 성과로만 했고, 이후 구간(holdout)은 선택이 끝난 뒤 한 번 계산했습니다. 누적 시도 ${r.n_trials}회로 DSR 보정.</div>
    <div class="stat-row">
      <div class="stat"><div class="l">선택 전략 holdout</div><div class="big num">${f2(bh.sharpe)}</div><div class="small dim">Sharpe · CAGR ${pc(bh.cagr)} · MDD ${pc(bh.max_drawdown)}</div></div>
      <div class="stat"><div class="l">KOSPI holdout</div><div class="big num">${f2(k.sharpe)}</div><div class="small dim">CAGR ${pc(k.cagr)} · MDD ${pc(k.max_drawdown)}</div></div>
      <div class="stat"><div class="l">전체 구간 (전략 / KOSPI)</div><div class="big num">${f2(full.strategy.sharpe)} / ${f2(full.kospi.sharpe)}</div><div class="small dim">CAGR ${pc(full.strategy.cagr)} / ${pc(full.kospi.cagr)}</div></div>
      <div class="stat"><div class="l">dev DSR (다중검정 보정)</div><div class="big num ${best.dev_dsr < 0.5 ? "down" : ""}">${f2(best.dev_dsr)}</div><div class="small dim">0.5 미만 = 개발 구간 우위가 우연일 수 있음</div></div></div>
    <div id="lab-chart" class="chart" style="margin-top:14px"></div>`)}
  ${card("전략별 결과 <span class='small dim'>왼쪽 = 개발 구간(선택 근거), 오른쪽 = holdout</span>", `<div class="scroll"><table><thead><tr><th>전략</th><th class="r">dev Sharpe</th><th class="r">CAGR</th><th class="r">MDD</th><th class="r">비용2배</th><th class="r">DSR</th><th class="r" style="border-left:1px solid var(--line)">holdout Sharpe</th><th class="r">CAGR</th><th class="r">MDD</th><th class="r" title="같은 종목군 동일가중 대비">IR</th><th class="r">연회전</th></tr></thead><tbody>${r.results.map(row).join("")}</tbody></table></div>`)}
  ${card(`연도별 수익률 <span class="small dim">${esc(best.name)}</span>`, `<div class="scroll"><table><thead><tr><th>연도</th><th class="r">전략</th><th class="r">KOSPI</th><th class="r">유니버스 EW</th></tr></thead><tbody>${yrows}</tbody></table></div>`)}`;
  lineChart($("#lab-chart"), [
    { data: best.equity, color: "#3b82f6", title: best.name },
    { data: best.benchmarks.kospi, color: "#94a3b8", title: "KOSPI" },
    { data: best.benchmarks.universe_ew, color: "#f59e0b", title: "유니버스 EW" },
  ]);
}

// ------------------------------------------------------------ 뷰: 운영
async function viewOps(el) {
  const o = await api("/api/ops");
  const ks = o.kill_switch || {};
  const jobs = o.jobs.map((j) => `<tr><td><b>${esc(j.job)}</b></td><td>${j.ok === false ? '<span class="chip neg">실패</span>' : j.ok ? '<span class="chip" style="color:var(--good)">정상</span>' : '<span class="chip">실행 중</span>'}</td>
    <td class="dim small">${time(j.last_run, true)}</td><td class="r num">${j.runs}</td><td class="r num ${j.failures ? "down" : ""}">${j.failures}</td><td class="small muted" style="white-space:normal">${esc(j.error || "")}</td></tr>`).join("");
  const llmRows = o.llm.by_model.map((m) => `<tr><td class="mono small">${esc(m.model)}</td><td class="r num">${m.calls}</td><td class="r num">${m.cached}</td><td class="r num ${m.errors ? "down" : ""}">${m.errors}</td><td class="r num">${m.latency_ms == null ? "-" : (m.latency_ms / 1000).toFixed(1) + "s"}</td><td class="r num">$${m.cost.toFixed(2)}</td></tr>`).join("");
  const used = o.llm_budget_usd > 0 ? Math.min(100, (o.llm.today_cost / o.llm_budget_usd) * 100) : 0;
  const dq = Object.entries(o.data_quality || {}).map(([sym, r]) => `<tr><td>${esc(nameOf(sym).split(" (")[0])}</td><td class="r num">${num(r.rows_out)}/${num(r.rows_in)}</td>
    <td class="small">${Object.entries(r.dropped || {}).map(([k, v]) => `<span class="chip neg">${esc(k)} ${v}</span>`).join("") || '<span class="chip" style="color:var(--good)">이상 없음</span>'}</td>
    <td class="small muted" style="white-space:normal">${(r.warnings || []).slice(0, 3).map(esc).join("<br>")}</td></tr>`).join("");
  const notes = o.notifications.map((n) => `<div class="lesson" style="border-left-color:${n.level === "critical" ? "var(--bad)" : n.level === "warn" ? "var(--warn)" : "var(--accent)"}">${esc(n.message)}</div>`).join("");
  el.innerHTML = `
  <div class="stat-row">
    <div class="stat"><div class="l">킬스위치</div><div class="big ${ks.on ? "down" : ""}" style="${ks.on ? "color:var(--bad)" : "color:var(--good)"}">${ks.on ? "ON" : "OFF"}</div><div class="small dim">${ks.at ? `${esc(ks.by || "")} · ${time(ks.at, true)} ${esc(ks.reason || "")}` : ""}</div></div>
    <div class="stat"><div class="l">오늘 LLM 비용</div><div class="big num">$${o.llm.today_cost.toFixed(2)}</div><div class="conf-bar" style="margin-top:6px"><div style="width:${used}%;background:${used > 80 ? css("--bad") : ""}"></div></div><div class="small dim">일 예산 $${o.llm_budget_usd}</div></div>
    <div class="stat"><div class="l">증권사</div><div class="big">${esc(o.broker === "kis" ? "KIS" : "미연결")}</div><div class="small dim">${o.broker === "kis" ? (o.kis_env === "real" ? "실전 계좌" : "모의투자") : "Paper/Shadow 만 가능"}</div></div>
    <div class="stat"><div class="l">알림</div><div class="big">${o.notifier_enabled ? "연결됨" : "미설정"}</div><div class="small dim">Discord · Slack · Telegram</div></div>
  </div>
  ${card("스케줄러 작업", jobs ? `<div class="scroll"><table><thead><tr><th>작업</th><th>상태</th><th>마지막 실행</th><th class="r">실행</th><th class="r">실패</th><th>오류</th></tr></thead><tbody>${jobs}</tbody></table></div>` : empty("기록 없음 — quant-ai run 으로 스케줄러를 실행하세요"))}
  <div class="grid g-2">
    ${card("LLM 사용량 <span class='small dim'>최근 7일 · 캐시·예산·감사 로그</span>", llmRows ? `<table><thead><tr><th>모델</th><th class="r">호출</th><th class="r">캐시</th><th class="r">실패</th><th class="r">지연</th><th class="r">비용</th></tr></thead><tbody>${llmRows}</tbody></table>` : empty("LLM 호출 없음 (API 키 미설정 시 휴리스틱 사용)"))}
    ${card("최근 알림", notes || empty("알림 없음"))}
  </div>
  ${card("데이터 품질 <span class='small dim'>수집 시 자동 검사 · 문제 행 제거</span>", dq ? `<div class="scroll"><table><thead><tr><th>종목</th><th class="r">유효/수집</th><th>제거</th><th>경고</th></tr></thead><tbody>${dq}</tbody></table></div>` : empty("quant-ai collect 로 수집한 데이터부터 검사됩니다"))}`;
}

// ------------------------------------------------------------ 뷰: 설정
function viewSettings(d) {
  const sys = d.system;
  const risk = Object.entries(sys.risk).map(([k, v]) => `<tr><td class="muted">${esc(k)}</td><td class="r num">${esc(v)}</td></tr>`).join("");
  return `
  <div class="grid g-2">
    ${card("AI 구성 <span class='small dim'>역할마다 다른 모델 → 관점 분산 · 무료 한도 초과 시 다음 모델로 자동 전환</span>", `<div class="scroll"><table><thead><tr><th>역할</th><th>공급자</th><th>모델 (앞에서부터 시도)</th><th class="r">일 한도</th></tr></thead><tbody>
      ${(sys.ai_roles || []).map((r) => `<tr><td><b>${esc(r.label)}</b><span class="sub">${esc(r.desc)}</span></td><td>${esc(r.provider)}${r.free && r.models.length ? ' <span class="chip pos">무료</span>' : ""}</td><td class="small mono" style="white-space:normal">${r.models.map(esc).join(" → ") || "-"}</td><td class="r num">${r.daily ?? "-"}</td></tr>`).join("")}
      <tr><td><b>Quant Model</b></td><td colspan="3" class="small">모델 레지스트리의 champion (없으면 shadow/candidate, Live 는 champion 만)</td></tr>
      <tr><td><b>RAG 임베딩</b></td><td colspan="3" class="small mono">${esc(sys.embeddings)}</td></tr></tbody></table></div>
      <div class="small dim" style="margin-top:10px">API 키는 서버 환경변수로만 설정합니다 (.env). 화면에는 키가 표시되지 않습니다.</div>`)}
    ${card("리스크 한도", `<table><tbody>${risk}</tbody></table>`)}
  </div>
  ${card("실매매 안전장치", `<ul class="plain">
    <li><span class="chk">${sys.live_enabled ? "◉" : "○"}</span>QUANT_LIVE_ENABLED=${sys.live_enabled}</li>
    <li><span class="chk">◉</span>QUANT_LIVE_CONFIRM 확인 문구 · QUANT_LIVE_MAX_CAPITAL 소액 상한 · Shadow 통과 champion 필수</li>
    <li><span class="chk">◉</span>AI 는 주문 권한 없음: AI → 신호 → 앙상블 → 리스크 게이트 → 실행 엔진 → 증권사 API</li>
    <li><span class="chk">◉</span>킬스위치 ON 시 신규 매수 즉시 중단 (매도·위험 축소는 허용)</li></ul>`)}`;
}

// ------------------------------------------------------------ 내비게이션
const NAV = [
  // v16 메뉴: 홈 · 종목 · 시장 · 뉴스/공시 · AI · 포트폴리오 · 리스크 · 백테스트 · AI 성적표 · 일정 · 투자일지 (+ 신뢰 · 시스템)
  ["핵심", [["dashboard", "home", "홈"], ["action", "bell", "오늘 할 일 · Action Center"], ["analysis", "ai", "종목"], ["watch", "score", "관심종목"], ["map", "market", "증시 지도"], ["market", "market", "시장 국면 · 지표"], ["news", "news", "뉴스 보드"], ["newslist", "news", "뉴스 · 공시 원문"], ["calendar", "bell", "일정 (D-Day)"], ["replay", "review", "그날 재현 (날짜 선택)"], ["compare", "compare", "종목 비교"], ["chat", "chat", "AI 어시스턴트"]]],
  ["AI", [["scorecard", "score", "AI 성적표 (공개)"], ["ailab", "lab", "AI Lab · 실패 연구"], ["ai", "score", "AI 성적 · 보정"], ["aihealth", "pulse", "AI · 모델 Health"], ["power", "learn", "실제 예측력"], ["pead", "evidence", "실적 이벤트 전략 (전진 기록)"], ["verify", "evidence", "검증실 · 예측 장부"], ["alpha", "alpha", "증명 체인 · Net Alpha"], ["graph", "models", "지식 그래프 · 업종"]]],
  ["포트폴리오 · 리스크", [["budget", "risk", "내 투자 한도"], ["pos", "portfolio", "Portfolio OS"], ["portfolio", "portfolio", "장부별 포트폴리오"], ["risk", "risk", "리스크 관리"], ["notrade", "stop", "거래 안 한 이유"], ["accounts", "portfolio", "계좌 · 세금 · 배당"], ["manual", "orders", "수동 모의 장부"], ["core", "auto", "자동매매 (코어-위성)"], ["orders", "orders", "주문 내역"], ["execution", "engine", "체결 · 증권사 검증"], ["sheet", "sheet", "리밸런싱 주문표"], ["usorder", "sheet", "미국 주식 주문표"]]],
  ["기록 · 연구", [["myjournal", "journal", "투자일지 vs AI"], ["profile", "settings", "내 투자 성향"], ["journal", "journal", "AI 판단 저널"], ["research", "research", "백테스트 · 리서치"], ["lab", "lab", "실험 · 승격"], ["review", "review", "복기 리포트"], ["reports", "review", "리포트 · 브리핑"], ["models", "models", "모델 · 검증"]]],
  ["신뢰 · 시스템", [["datahealth", "data", "데이터 건강"], ["readiness", "check", "매매 준비"], ["truth", "shield", "Truth Center"], ["validation", "check", "실전 검증 진행표"], ["control", "control", "24H 관제실"], ["safety", "shield", "안전 센터"], ["governance", "shield", "규제 · 보안 · 라이선스"], ["server", "server", "서버 · DB"], ["trades", "evidence", "거래 · 리스크 로그"], ["ops", "ops", "운영 · 시스템"], ["settings", "settings", "설정"]]],
];
function buildNav() {
  $("#nav").innerHTML = NAV.map(([g, items]) => `<div class="grp">${g}</div>` + items.map(([v, ic, label]) =>
    `<a href="#${v}" data-view="${v}">${ICONS[ic] || ""}<span>${label}</span></a>`).join("")).join("");
}

// ------------------------------------------------------------ 렌더링
async function render() {
  // 화면마다 새 컨테이너: 느린 이전 화면의 응답이 늦게 도착해도 지금 화면을 덮어쓰지 않는다
  const el = document.createElement("div");
  el.className = "view-inner";
  el.innerHTML = skeleton();
  $("#view").replaceChildren(el);
  clearCharts();
  const navView = S.view === "evidence" ? "journal" : S.view;
  document.querySelectorAll("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.view === navView));
  const d = S.data;
  if (!d) { el.innerHTML = skeleton(); return; }
  try {
    if (S.view === "dashboard") { el.innerHTML = viewDashboard(d); fillHome(d); homeOneLine(el); todayCard(el).then(() => osHomeBrief(el)).then(() => weeklyCard(el, "#home-brief")); applyHomeLayout(el); }
    else if (S.view === "analysis") await viewAnalysis(el);
    else if (S.view === "market") {
      el.innerHTML = viewMarket(d);
      const first = d.indices[0];
      if (first) candleChart($("#idx-chart"), first.symbol);
    } else if (S.view === "portfolio") {
      el.innerHTML = viewPortfolio(d);
      const pf = d.portfolios[S.pfMode];
      if (pf?.curve?.length) lineChart($("#eq-chart"), [{ data: pf.curve, color: "#3b82f6", title: S.pfMode }]);
    } else if (S.view === "trades") el.innerHTML = viewTrades(d);
    else if (S.view === "news") await viewNewsBoard(el);  // v17: 보는 뉴스 보드
    else if (S.view === "newslist") el.innerHTML = viewNews(d);  // 원문 목록 (예전 화면)
    else if (S.view === "map") await viewMarketMap(el);
    else if (S.view === "review") await viewReview(el);
    else if (S.view === "models") el.innerHTML = viewModels(d);
    else if (S.view === "ops") await viewOps(el);
    else if (S.view === "research") await viewResearch(el);
    else if (S.view === "core") {
      await viewCore(el);
      const hb = $("#health-refresh");
      if (hb) hb.onclick = async () => { hb.disabled = true; hb.textContent = "검사 중…"; await api("/api/strategy-health", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }); render(); };
    } else if (S.view === "sheet") await viewOrders(el);
    else if (S.view === "orders") await viewOrderHistory(el);
    else if (S.view === "ai") await viewAIScore(el);
    else if (S.view === "risk") await viewRisk(el);
    else if (S.view === "journal") await viewJournal(el);
    else if (S.view === "evidence") await viewEvidence(el, S.param);
    else if (S.view === "settings") { el.innerHTML = alertSettingsCard() + extraSettingsCard() + viewSettings(d); bindAlertSettings(); bindExtraSettings(); notifyCard(el); }
    else if (S.view === "verify") { await viewVerify(el); if (typeof notaryCard === "function") notaryCard(el); }
    else if (S.view === "readiness") await viewReadiness(el);
    else if (S.view === "truth") await viewTruth(el);
    else if (S.view === "compare") await viewCompare(el);
    else if (S.view === "aihealth") await viewAIHealth(el);
    else if (S.view === "power") await viewPower(el);
    else if (S.view === "calendar") { await viewCalendar(el); weeklyCard(el, null); }
    else if (S.view === "execution") await viewExecution(el);
    else if (S.view === "myjournal") await viewMyJournal(el);
    else if (S.view === "accounts") await viewAccounts(el);
    else if (S.view === "ledger") await viewLedger(el);
    else if (S.view === "graph") { await viewGraph(el); if (typeof rotationPanel === "function") rotationPanel(el); }
    else if (S.view === "reports") await viewReports(el);
    else if (S.view === "control") await viewControl(el);
    else if (S.view === "alpha") await viewAlpha(el);
    else if (S.view === "safety") await viewSafety(el);
    else if (S.view === "lab") await viewLab(el);
    else if (S.view === "server") await viewServer(el);
    else if (S.view === "chat") await viewChat(el);
    // v16
    else if (S.view === "action") await viewActionCenter(el);
    else if (S.view === "watch") await viewWatch(el);
    else if (S.view === "notrade") await viewNoTrade(el);
    else if (S.view === "scorecard") await viewScorecard(el);
    else if (S.view === "ailab") await viewAILabV16(el);
    else if (S.view === "datahealth") await viewDataHealth(el);
    else if (S.view === "pos") await viewPortfolioOS(el);
    else if (S.view === "profile") await viewProfile(el);
    else if (S.view === "validation") await viewValidation(el);
    else if (S.view === "governance") await viewGovernance(el);
    else if (S.view === "manual") await viewManual(el);
    else if (S.view === "budget") await viewBudget(el);
    else if (S.view === "pead") await viewPead(el);
    else if (S.view === "usorder") await viewUSOrder(el);
    else if (S.view === "replay") await viewReplay(el);
    else el.innerHTML = card("페이지 없음", empty(`'${esc(S.view)}' 화면이 없습니다`));
  } catch (e) {
    el.innerHTML = card("오류", `<div class="veto">${esc(e.message)}</div>`);
  }
  bindCommon();
  animateView(el);
  markTab();
}

function bindCommon() {
  document.querySelectorAll("tr[data-sym]").forEach((tr) => tr.onclick = () => {
    S.symbol = tr.dataset.sym;
    if (S.view === "dashboard") { S.chartSym = tr.dataset.sym; render(); } else { location.hash = "#analysis"; }
  });
  document.querySelectorAll("tr[data-model]").forEach((tr) => tr.onclick = () => { S.modelId = Number(tr.dataset.model); render(); });
  document.querySelectorAll("#pf-tabs button").forEach((b) => b.onclick = () => { S.pfMode = b.dataset.m; render(); });
  document.querySelectorAll("#news-tabs button").forEach((b) => b.onclick = () => { S.newsKind = b.dataset.k; render(); });
  const tabBind = (id, key, num = false) => document.querySelectorAll(`#${id} button`).forEach((b) => b.onclick = () => { S[key] = num ? Number(b.dataset.k) : b.dataset.k; render(); });
  tabBind("idx-tabs", "idxTab"); tabBind("watch-tabs", "watchTab"); tabBind("feed-tabs", "feedTab"); tabBind("range-tabs", "chartN", true);
  const kb = $("#chart-kospi");
  if (kb) kb.onclick = () => { S.chartSym = S.data.indices[0]?.symbol; render(); };
  document.querySelectorAll(".feed-it[data-ev]").forEach((x) => x.onclick = () => { location.hash = `#evidence/${x.dataset.ev}`; });
}

function modeInfo(d) {
  const sys = d.system;
  const mode = homeMode(d);
  if (mode === "live" && sys.broker === "kis") return sys.kis_env === "real"
    ? { title: "실전 거래", sub: "KIS 실계좌 · 실제 돈", color: "#ef4444", icon: "●" }
    : { title: "모의투자 자동매매", sub: "KIS 모의계좌 · 실제 돈 아님", color: "#22c55e", icon: "▶" };
  if (mode === "shadow") return { title: "Shadow Trading", sub: "호가 기준 가상 체결", color: "#8b5cf6", icon: "◐" };
  return { title: "Paper Trading", sub: "가상매매 · 실제 주문 없음", color: "#3b82f6", icon: "◆" };
}

function renderChrome(d) {
  $("#demo-badge").hidden = !d.demo;
  const mi = modeInfo(d);
  $("#mode-card").innerHTML = `<div class="t">운영 모드</div><div class="mode-box"><span class="ic" style="background:${mi.color}">${mi.icon}</span><div><b>${esc(mi.title)}</b><span class="xs muted">${esc(mi.sub)}${d.system.core_only ? " · 코어 전용" : ""}</span></div></div>`;
  const pf = d.portfolios[homeMode(d)] || {};
  $("#balance-card").innerHTML = `<div class="t">계좌 평가금액 (${homeMode(d).toUpperCase()})</div><div class="bal num">${pf.equity ? krw(pf.equity) : "-"}</div><div class="small">${pf.equity ? pct(pf.return_pct) + ' <span class="muted">누적</span>' : '<span class="muted">기록 없음</span>'}</div>`;
  const kb = $("#kill-btn");
  kb.classList.toggle("on", d.kill_switch);
  kb.innerHTML = d.halted ? `${ICONS.stop} HALTED · 자동 정지` : d.kill_switch ? `${ICONS.play} 매수 정지 중 · 해제` : `${ICONS.stop} 긴급 정지`;
  kb.title = d.halted ? `자동 정지 사유: ${d.kill_info?.reason || ""}` : "긴급 정지: 신규 매수 즉시 중단 (매도·위험 축소는 계속)";
  if (!window.MKT_CLOCK_ON) $("#market-badges").innerHTML = Object.entries(d.markets).map(([k, v]) => `<span class="pill ${v === "open" ? "open" : ""}">${k} ${v === "open" ? "장중" : v === "pre_open" ? "장전" : "장외"}</span>`).join(" ");
  $("#sys-last").textContent = d.system.last_bar ? `데이터 ${date(d.system.last_bar)}` : "-";
  $("#avatar").textContent = homeMode(d).slice(0, 2).toUpperCase();
  api("/api/health").then((h) => {
    const bad = !h.ok || h.recent_job_failures > 0;
    const sp = $("#sys-status");
    sp.classList.toggle("bad", bad);
    sp.innerHTML = `<span class="dot ${bad ? "bad" : "ok"}"></span>${!h.ok ? "DB 오류" : h.recent_job_failures ? `작업 실패 ${h.recent_job_failures}건` : h.kill_switch ? "정상 · 매수 정지" : "시스템 정상"}`;
    sp.title = "서버 · DB 화면에서 자세히";
    if (h.recent_job_failures) api("/api/ops").then((o) => {  // 인증된 API 로만 실패 내용을 본다 (/api/health 는 공개용)
      const f = (o.jobs || []).filter((j) => j.ok === false);
      if (f.length) sp.title = f.map((j) => `${j.job}: ${j.error || ""}`).join("\n");
    }).catch(() => {});
    sp.style.cursor = "pointer";
    sp.onclick = () => { location.hash = "#server"; };
  }).catch(() => { $("#sys-status").innerHTML = `<span class="dot bad"></span>연결 실패`; });
}

function tickClock() {
  const k = new Date(Date.now() + 9 * 3600e3);
  const p = (n) => String(n).padStart(2, "0");
  const c = $("#clock");
  if (c) c.textContent = `${k.getUTCFullYear()}.${p(k.getUTCMonth() + 1)}.${p(k.getUTCDate())} ${p(k.getUTCHours())}:${p(k.getUTCMinutes())}:${p(k.getUTCSeconds())} (KST)`;
}

async function refresh() {
  try {
    S.data = await api("/api/dashboard");
    renderChrome(S.data);
  } catch (e) {
    $("#sys-status").innerHTML = `<span class="dot bad"></span>연결 실패`;
    throw e;
  }
}

function route() {
  const h = (location.hash || "#dashboard").slice(1) || "dashboard";
  const [v, param] = h.split("/");
  S.view = v; S.param = param || null;
  document.body.classList.toggle("on-chat", v === "chat");
  if (v === "analysis" && param) S.symbol = decodeURIComponent(param);
  $(".side").classList.remove("open");
  window.scrollTo(0, 0);
  render();
}

// 검색: 서버 검색 (한글 별칭 · 해외 종목) — pro.js
initSearch();
document.addEventListener("click", (e) => {
  if (!e.target.closest(".search")) $("#search-results").classList.remove("open");
  if (!e.target.closest(".bell-wrap")) $("#bell-panel").classList.remove("open");
});

$("#kill-btn").onclick = async () => {
  if (S.data?.halted) { location.hash = "#safety"; return; }
  const on = !S.data?.kill_switch;
  let reason = "";
  if (on) {
    reason = prompt("긴급 정지: 모든 신규 매수가 즉시 중단됩니다 (매도·위험 축소는 계속).\n사유를 입력하세요:", "수동 정지");
    if (reason === null) return;
  } else if (!confirm("긴급 정지를 해제하고 신규 매수를 재개할까요?")) return;
  await api("/api/killswitch", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ on, reason }) });
  await refresh(); render();
};
function setTheme(t) {
  document.documentElement.dataset.theme = t; safeSet("qa_theme", t);
  $("#theme-btn").innerHTML = t === "light" ? ICONS.moon : ICONS.sun;
}
$("#theme-btn").onclick = () => {
  const t = document.documentElement.dataset.theme === "light" ? "dark" : "light";
  setTheme(t); render();
  post("/api/prefs", { theme: t }).then(() => { if (S.prefs) S.prefs.theme = t; }).catch(() => {});  // v16: 기기 간 동기화
};
$("#menu-btn").onclick = () => $(".side").classList.toggle("open");

$("#brand-logo").innerHTML = ICONS.logo;
$("#search-ico").innerHTML = ICONS.search;
$("#settings-btn").innerHTML = ICONS.settings;
$("#bell-btn").innerHTML = ICONS.bell;
setTheme(safeGet("qa_theme") || "dark");
loadPrefs().then((p) => { if (p.theme && p.theme !== document.documentElement.dataset.theme) setTheme(p.theme); }).catch(() => {});
buildNav();
initChatWidget();
initLive();
initPWA();
initV14();
if (typeof initAsOfChip === "function") initAsOfChip();
tickClock(); setInterval(tickClock, 1000);

window.addEventListener("hashchange", route);
refresh().then(route).catch(() => route());
setInterval(async () => { if (S.view === "dashboard" || S.view === "trades") { await refresh(); render(); } else { await refresh(); } }, 60000);
