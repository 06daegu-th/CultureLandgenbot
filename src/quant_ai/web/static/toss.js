/* v25 토스식 화면 — 쉬운 화면의 홈 · 종목 · 뉴스/공시 · 기사 · 포트폴리오 · 시장 · 관심종목 · AI 리포트 · 알림 · 더보기.
   화면 하나 = API 하나(/api/t/*)로 먼저 그리고, 느린 칸(AI 판단·뉴스)은 나중에 채운다.
   '전체' 화면(전문가용)은 그대로 두고, 각 화면 아래 '자세히(전문가용)' 로 언제든 넘어갈 수 있다. */
"use strict";

const T = { feed: { tab: "all", region: "all", topic: "all" }, pfTab: "hold", alertTab: "price", stab: {}, period: 63, watchTab: "all", watchSort: "default", mvTab: "kr" };

// ------------------------------------------------------------ 작은 부품
const tCls = (v) => v == null ? "flat" : v > 0 ? "up" : v < 0 ? "down" : "flat";
const tPct = (v, d = 2) => v == null || Number.isNaN(v) ? "-" : `${v > 0 ? "+" : ""}${(v * 100).toFixed(d)}%`;
const tPx = (v, sym) => v == null ? "-" : (sym && !isKR(sym) ? `$${num(v, 2)}` : `${num(v, Math.abs(v) < 100 ? 2 : 0)}원`);
const tWon = (v) => v == null ? "-" : `${num(Math.round(v))}원`;
const tSigned = (v, sym) => v == null ? "" : `${v > 0 ? "+" : v < 0 ? "-" : ""}${tPx(Math.abs(v), sym)}`;
const tMoney = (v) => {  // 1억 2,345만원 처럼 읽기 쉬운 원화
  if (v == null) return "-";
  const a = Math.abs(v), s = v < 0 ? "-" : "";
  if (a >= 1e8) { const e = Math.floor(a / 1e8), m = Math.round((a % 1e8) / 1e4); return `${s}${num(e)}억${m ? ` ${num(m)}만` : ""}원`; }
  if (a >= 1e4) return `${s}${num(Math.round(a / 1e4))}만원`;
  return `${s}${num(Math.round(a))}원`;
};
const tVol = (v) => v == null ? "-" : v >= 1e8 ? `${(v / 1e8).toFixed(1)}억` : v >= 1e4 ? `${num(Math.round(v / 1e4))}만` : num(v);
const tCap = (v, sym) => {
  if (v == null) return "-";
  if (sym && !isKR(sym)) return v >= 1e12 ? `$${(v / 1e12).toFixed(2)}조` : v >= 1e9 ? `$${num(v / 1e9, 1)}B` : `$${num(v / 1e6, 0)}M`;
  return v >= 1e12 ? `${num(v / 1e12, 1)}조원` : `${num(Math.round(v / 1e8))}억원`;
};
const TI = {  // 이 화면에서만 쓰는 선 아이콘 (icons.js 와 같은 굵기)
  back: '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 18 9 12l6-6"/></svg>',
  chev: '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m9 18 6-6-6-6"/></svg>',
  star: '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9Z"/></svg>',
  starOn: '<svg viewBox="0 0 24 24" width="22" height="22" fill="#ffb800" stroke="#ffb800" stroke-width="1.8" stroke-linejoin="round"><path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9Z"/></svg>',
  search: '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>',
  ext: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/></svg>',
  grid: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="4" y="4" width="6.5" height="6.5" rx="1.5"/><rect x="13.5" y="4" width="6.5" height="6.5" rx="1.5"/><rect x="4" y="13.5" width="6.5" height="6.5" rx="1.5"/><rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.5"/></svg>',
  doc: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M6 3h8l4 4v14H6Z"/><path d="M14 3v4h4M9 12h6M9 16h6"/></svg>',
  cal: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><rect x="4" y="5" width="16" height="15" rx="2"/><path d="M4 10h16M9 3v4M15 3v4"/></svg>',
  news: '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 9h6M7 13h10M7 16h7"/></svg>',
};
if (typeof ICONS === "object" && !ICONS.grid) ICONS.grid = TI.grid;
// 메뉴 단추: 글자(☰) 대신 선 아이콘 (이모지 정리기가 기호를 지워 빈 칸이 되던 문제)
document.addEventListener("DOMContentLoaded", () => { const m = document.getElementById("menu-btn"); if (m) m.innerHTML = '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 7h16M4 12h16M4 17h16"/></svg>'; });

function tSpark(vals, o = {}) {  // 작은 흐름선 (오르면 빨강 · 내리면 파랑)
  const v = (vals || []).filter((x) => x != null && !Number.isNaN(x));
  const W = o.w || 120, H = o.h || 36;
  if (v.length < 2) return `<svg class="t-spark" viewBox="0 0 ${W} ${H}" width="100%" height="${H}"></svg>`;
  const lo = Math.min(...v), hi = Math.max(...v);
  const x = (i) => (i / (v.length - 1)) * W, y = (a) => hi === lo ? H / 2 : H - 2 - ((a - lo) / (hi - lo)) * (H - 4);
  const d = v.map((a, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(a).toFixed(1)}`).join("");
  const c = o.color || (v[v.length - 1] >= v[0] ? "var(--up)" : "var(--down)");
  return `<svg class="t-spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" width="100%" height="${H}" aria-hidden="true">
    ${o.fill === false ? "" : `<path d="${d}L${W},${H}L0,${H}Z" fill="${c}" opacity=".10"/>`}<path d="${d}" fill="none" stroke="${c}" stroke-width="${o.sw || 1.6}" vector-effect="non-scaling-stroke" stroke-linejoin="round"/></svg>`;
}
// AI 판단 알약: 매수 빨강 · 매도 파랑 · 관망 회색 · 쉬어가기 점선
function tPill(action, prob, o = {}) {
  if (!action) return `<span class="t-pill p-none">${o.short ? "-" : "AI 의견 없음"}</span>`;
  const a = action === "NO TRADE" ? "NO_TRADE" : action;
  const p = prob != null && a !== "NO_TRADE" ? ` ${Math.round(prob * 100)}%` : "";
  return `<span class="t-pill p-${esc(a)}" title="${o.title ? esc(o.title) : "AI 최종 판단 · 숫자 = 오를 확률"}">${esc(koAct(a))}${p}</span>`;
}
const tRow = (href, left, right, o = {}) => `<a class="t-li${o.cls ? ` ${o.cls}` : ""}" href="${esc(href)}"${o.attr || ""}>${left}<span class="t-li-r">${right}</span>${o.chev ? `<span class="t-chev">${TI.chev}</span>` : ""}</a>`;
const tName = (sym, name, sub = "", size = 40) => `<span class="t-co">${stockLogo(sym, name, size)}<span class="t-co-t"><b>${esc(name || sym)}</b><span>${sub}</span></span></span>`;
const tPrice = (last, chg, sym) => `<b class="num t-pv">${tPx(last, sym)}</b><span class="num t-pc ${tCls(chg)}">${tPct(chg)}</span>`;
function tTabs(id, items, cur, cls = "") { return `<div class="t-tabs ${cls}" id="${id}" role="tablist">${items.map(([k, l]) => `<button role="tab" data-k="${esc(k)}" class="${k === cur ? "on" : ""}" aria-selected="${k === cur}">${l}</button>`).join("")}</div>`; }
function tChips(id, items, cur) { return `<div class="t-chips" id="${id}">${items.map(([k, l]) => k === "|" ? '<i class="t-chip-sep"></i>' : `<button data-k="${esc(k)}" class="${k === cur || (Array.isArray(cur) && cur.includes(k)) ? "on" : ""}">${l}</button>`).join("")}</div>`; }
function tBind(root, id, fn) { root.querySelectorAll(`#${id} button[data-k]`).forEach((b) => b.onclick = () => fn(b.dataset.k, b)); }
const tSec = (title, body, right = "", cls = "") => `<section class="t-sec ${cls}">${title ? `<div class="t-sec-h"><h2>${title}</h2>${right ? `<div class="t-sec-r">${right}</div>` : ""}</div>` : ""}${body}</section>`;
const tEmpty = (title, sub = "", act = "") => `<div class="t-empty"><b>${esc(title)}</b>${sub ? `<span>${esc(sub)}</span>` : ""}${act}</div>`;
const tErr = (x) => `<div class="t-empty err"><b>불러오지 못했어요</b><span>${esc(x?.error || x?.message || x || "")}</span></div>`;
const tSkel = (n = 3) => `<div class="t-skel">${'<div class="sk-row"><i></i><div><b></b><b></b></div></div>'.repeat(n)}</div>`;
function tBar(title, o = {}) {  // 상세 화면 위 막대: ← 제목 · 오른쪽 단추
  return `<div class="t-bar"><button class="t-back" aria-label="뒤로">${TI.back}</button><div class="t-bar-t">${title || ""}</div><div class="t-bar-r">${o.right || ""}</div></div>`;
}
function tBindBar(root, fallback = "#dashboard") {
  const b = root.querySelector(".t-back");
  if (b) b.onclick = () => { if (T_NAV.length) history.back(); else location.hash = fallback; };  // 앱 안에서 왔으면 진짜 뒤로, 바로 들어왔으면 상위 화면으로
  root.querySelectorAll("[data-t-search]").forEach((x) => x.onclick = () => { const s = $("#search"); if (s) { s.focus(); s.scrollIntoView({ block: "nearest" }); } });
}
const DOW = ["일", "월", "화", "수", "목", "금", "토"];
function tDate(iso) {  // 날짜만 있는 값('2026-10-05')은 시간대 변환 없이 그 날짜 그대로 (브라우저 시간대가 달라도 하루 밀리지 않게)
  if (!iso) return "";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso));
  const d = m ? new Date(Date.UTC(+m[1], +m[2] - 1, +m[3])) : new Date(new Date(iso).getTime() + 9 * 3600e3);
  return `${d.getUTCMonth() + 1}.${String(d.getUTCDate()).padStart(2, "0")} (${DOW[d.getUTCDay()]})`;
}
const tDday = (e) => `<span class="t-dday ${e.d_day <= 1 ? "soon" : e.d_day <= 3 ? "near" : ""}">${esc(e.d_label)}</span>`;
const EV_KO = { earnings: "실적", ex_div: "배당락", div_pay: "배당", holiday: "휴장", half_day: "단축 거래", fomc: "FOMC", cpi: "물가(CPI)", nfp: "고용", pce: "PCE", bok: "금통위", options_expiry: "옵션 만기", gdp: "GDP" };
function tEventRow(e) {
  const left = e.symbol ? stockLogo(e.symbol, "", 40) : `<span class="t-ev-ic ev-${esc(e.kind || "")}">${TI.cal}</span>`;
  const sub = [tDate(e.date), EV_KO[e.kind] || "", e.market && !e.symbol ? (e.market === "KR" ? "한국" : e.market === "US" ? "미국" : e.market) : "", e.estimated ? "추정일" : ""].filter(Boolean).join(" · ");
  return tRow(e.symbol ? `#analysis/${encodeURIComponent(e.symbol)}` : "#calendar", `<span class="t-co">${left}<span class="t-co-t"><b>${esc(e.title)}</b><span>${esc(sub)}</span></span></span>`, tDday(e));
}

// 아래에서 올라오는 시트 (모의 주문 · 확인)
function tSheet(html, onMount) {
  document.querySelector(".t-sheet-ov")?.remove();
  const ov = document.createElement("div");
  ov.className = "t-sheet-ov";
  ov.innerHTML = `<div class="t-sheet" role="dialog" aria-modal="true"><div class="t-sheet-grip"></div><div class="t-sheet-b">${html}</div></div>`;
  document.body.appendChild(ov);
  const close = () => { ov.remove(); document.removeEventListener("keydown", onKey); };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);
  ov.onclick = (e) => { if (e.target === ov) close(); };
  requestAnimationFrame(() => ov.classList.add("open"));
  if (onMount) onMount(ov.querySelector(".t-sheet-b"), close);
  return close;
}

// 면적 차트 (가격 흐름 + 거래량) — 손가락/마우스를 올리면 위 가격 칸이 그 날로 바뀐다
function tArea(el, bars, sym, onHover) {
  if (!el) return null;
  if (!window.LightweightCharts) { el.innerHTML = tEmpty("차트를 그리지 못했어요", "차트 파일을 불러오지 못했습니다"); return null; }
  if (!bars?.length) { el.innerHTML = tEmpty("차트 자료가 없어요", "일봉이 쌓이면 그려집니다"); return null; }
  const first = bars[0].close, last = bars[bars.length - 1].close;
  const c = last >= first ? (css("--up") || "#f04452") : (css("--down") || "#3182f6");
  const chart = LightweightCharts.createChart(el, {
    ...chartOpts(el),
    grid: { vertLines: { visible: false }, horzLines: { color: css("--line") } },
    rightPriceScale: { borderVisible: false, scaleMargins: { top: 0.12, bottom: 0.22 } },
    timeScale: { borderVisible: false, fixLeftEdge: true, fixRightEdge: true },
    handleScroll: false, handleScale: false,
    crosshair: { mode: 1, vertLine: { color: css("--dim"), width: 1, style: 2, labelVisible: false }, horzLine: { visible: false, labelVisible: false } },
  });
  S.charts.push(chart);
  const kr = isKR(sym);
  const s = chart.addAreaSeries({ lineColor: c, topColor: c + "38", bottomColor: c + "00", lineWidth: 2, priceLineVisible: false, lastValueVisible: true,
    priceFormat: { type: "price", precision: kr ? 0 : 2, minMove: kr ? 1 : 0.01 } });
  s.setData(bars.map((b) => ({ time: b.time, value: b.close })));
  const vol = chart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "vol", lastValueVisible: false, priceLineVisible: false });
  chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.84, bottom: 0 } });
  vol.setData(bars.map((b, i) => ({ time: b.time, value: b.volume || 0, color: (i && b.close < bars[i - 1].close ? css("--down") : css("--up")) + "55" })));
  chart.timeScale().fitContent();
  const byTime = new Map(bars.map((b, i) => [b.time, i]));
  chart.subscribeCrosshairMove((p) => {
    if (!onHover) return;
    if (!p || !p.time || !byTime.has(p.time)) { onHover(null); return; }
    const i = byTime.get(p.time);
    onHover({ bar: bars[i], chg: i ? bars[i].close / bars[0].close - 1 : 0 });
  });
  return chart;
}

// ============================================================ 홈
async function tHome(el) {
  const q = typeof pfModeQ === "function" ? pfModeQ() : "mode=paper";
  el.innerHTML = `<div class="ts ts-home">${tSkel(4)}</div>`;
  const [h, pf] = await Promise.all([api(`/api/t/home?${q}`), api(`/api/t/portfolio?${q}`).catch((e) => ({ error: e.message }))]);
  const root = el.querySelector(".ts");
  const idx = Array.isArray(h.indices) ? h.indices : [];
  const proxy = idx.find((x) => x.proxy && x.proxy_note);
  const idxB = h.indices?.error ? tErr(h.indices) : idx.length ? `<div class="t-idx">${idx.map((x) => x.missing ? `<a class="t-idx-c miss" href="#datahealth"><div class="t-idx-n">${esc(x.name)}</div><div class="t-idx-v dim">-</div><div class="t-idx-d">${esc(x.why)}</div></a>` : `<a class="t-idx-c" href="#market">
      <div class="t-idx-n">${esc(x.name)}${x.proxy ? '<span class="t-tag" title="실제 지수가 아닌 대용값">대용</span>' : ""}</div>
      <div class="t-idx-v num">${num(x.last, 2)}</div>
      <div class="num t-idx-c2 ${tCls(x.chg_pct)}">${tPct(x.chg_pct)}</div>
      ${tSpark(x.spark, { h: 34 })}<div class="t-idx-d">${esc(x.as_of || "")} 기준</div></a>`).join("")}</div>
      ${proxy ? `<div class="t-foot">대용: ${esc(proxy.proxy_note)}</div>` : ""}`
    : tEmpty("지수 자료가 아직 없어요", "./run.sh 로 데이터를 받으면 채워집니다");
  const tr = h.trust || {};
  const trustLine = tr.key ? `<a class="t-trust t-trust-${esc(tr.key)}" href="#aitrust">${lvDot({ verified: "good", checking: "warn", banned: "bad" }[tr.key])}
      <span><b>AI 신뢰 · ${esc(tr.label || "")}</b> ${esc(tr.headline || tr.why || "")}</span>${TI.chev}</a>` : "";
  const picks = Array.isArray(h.picks) ? h.picks : [];
  const picksB = h.picks?.error ? tErr(h.picks) : picks.length ? `<div class="t-hs">${picks.map((p) => `<a class="t-pick" href="#analysis/${encodeURIComponent(p.symbol)}">
      <div class="t-pick-top">${stockLogo(p.symbol, p.name, 40)}${p.focus ? '<span class="t-tag">관심·보유</span>' : ""}</div>
      <b class="t-pick-n">${esc(p.name)}</b><span class="t-pick-s">${esc(p.symbol)}</span>
      <div class="t-pick-p">${tPrice(p.last, p.chg_pct, p.symbol)}</div>
      ${tPill(p.action, p.prob_up)}</a>`).join("")}</div>`
    : tEmpty("아직 AI 판단이 없어요", "관심종목을 담고 하루가 지나면 AI 가 매일 아침 판단합니다", '<button class="t-btn" data-t-search>종목 검색</button>');
  const ev = h.events?.rows || [];
  const evB = h.events?.error ? tErr(h.events) : ev.length ? `<div class="t-list">${ev.slice(0, 5).map(tEventRow).join("")}</div>` : tEmpty("30일 안에 큰 일정이 없어요");
  const hd = h.holdings || {};
  const pos = Array.isArray(pf.positions) ? pf.positions : [];
  const hdB = hd.error ? tErr(hd) : `<a class="t-hold-top" href="#pos"><div class="t-hold-l"><span class="t-k">총 자산</span><b class="t-big num">${tWon(hd.equity)}</b>
      <span class="num ${tCls(hd.pnl)}">${tSigned(hd.pnl)} (${tPct(hd.pnl_pct)})</span></div>${TI.chev}</a>
      <div class="t-hold-ch">${(hd.spark || []).length > 1 ? tSpark(hd.spark, { h: 64, w: 300 }) : '<div class="t-sub">자산 흐름은 하루 이상 운용하면 그려져요</div>'}</div>
      ${pos.length ? `<div class="t-list">${pos.slice(0, 3).map((r) => tRow(`#analysis/${encodeURIComponent(r.symbol)}`, tName(r.symbol, r.name, `${num(r.qty)}주 · 평균 ${tPx(r.avg_price, r.symbol)}`),
        `<b class="num t-pv">${tWon(r.value)}</b><span class="num t-pc ${tCls(r.pnl_pct)}">${tPct(r.pnl_pct)}</span>`)).join("")}</div>
        ${pos.length > 3 ? `<a class="t-more" href="#pos">${pos.length - 3}종목 더 보기</a>` : ""}`
      : `<div class="t-sub">아직 보유 종목이 없어요 · 현금 ${tWon(pf.cash)}</div>`}`;
  const mk = (h.markets || []).map((m) => `<span class="t-mk ${m.open ? "open" : ""}">${m.open ? '<i class="live-dot"></i>' : ""}${esc(m.name)} ${esc(m.state)}${m.holiday ? `<em>${esc(m.holiday)}</em>` : ""}${!m.open && m.next_kst ? `<em>${esc(m.next === "개장" ? "개장" : "마감")} ${esc(String(m.next_kst).replace(" KST", ""))}</em>` : ""}</span>`).join("");
  root.innerHTML = `
    <section class="t-hello"><div><div class="t-date">${esc(h.date || "")} · ${esc(h.as_of || "")}</div><h1>${esc(h.greeting || "안녕하세요")}</h1></div><div class="t-mks">${mk}</div></section>
    <div id="th-guide"></div>
    ${tSec("지수", idxB, '<a href="#market">시장 전체</a>', "t-sec-idx")}
    ${tSec("AI가 본 오늘의 종목", trustLine + picksB, '<a href="#report">AI 분석</a>')}
    <div class="t-grid2">
      ${tSec("오늘의 주요 이벤트", evB, '<a href="#calendar">일정 전체</a>', "t-card")}
      ${tSec("내 보유 종목 현황", hdB, '<a href="#pos">포트폴리오</a>', "t-card")}
    </div>
    <div class="t-grid2" id="th-todo">${tSec("오늘 확인할 것", tSkel(2), "", "t-card")}${tSec("오늘 주의할 것", tSkel(2), "", "t-card")}</div>
    <div id="th-news"></div>
    <div id="th-concl"></div>
    <div class="t-pro"><button class="t-btn ghost" id="th-pro">자세한 홈 (전문가용)</button><span>AI 브리핑 · 매매 준비 · 시장 체크리스트 · 데이터 상태</span></div>`;
  root.querySelector("#th-pro").onclick = () => { S.homeDetail = true; render(); };
  tBindBar(root);
  tHomeMore(root, q);
}
async function tHomeMore(root, q) {  // 느린 칸: 할 일·주의·뉴스·결론·시작 안내 (홈 첫 계산 ~2초)
  const [h5, g, p] = await Promise.all([api(`/api/home5?${q}`).catch((e) => ({ error: e.message })), api("/api/start-guide").catch(() => null), loadPrefs().catch(() => ({}))]);
  if (!root.isConnected) return;
  const g5 = root.querySelector("#th-guide");
  if (g && !g.done && !(p?.ui?.guide_hidden) && typeof guideCard === "function") { g5.innerHTML = guideCard(g); if (typeof bindGuide === "function") bindGuide(g5); }
  if (h5.error) { root.querySelector("#th-todo").innerHTML = tSec("오늘 확인할 것", tErr(h5), "", "t-card"); return; }
  const td = h5.todo || {};
  const it = (x, dot) => tRow(x.link || "#action", `<span class="t-co">${x.symbol ? stockLogo(x.symbol, "", 36) : `<span class="t-dot-ic">${lvDot(dot)}</span>`}<span class="t-co-t"><b>${esc(koText(x.text))}</b>${x.why ? `<span>${esc(x.why)}</span>` : ""}</span></span>`, "", { chev: true });
  const todo = (td.items || []).length ? `<div class="t-list">${td.items.map((x) => it(x, "warn")).join("")}</div>${td.more ? `<a class="t-more" href="#action">${td.more}개 더 보기</a>` : ""}`
    : tEmpty(td.calm || td.empty_hint || "오늘 꼭 할 일은 없어요");
  const cau = Array.isArray(h5.caution) ? h5.caution : [];
  const cauB = cau.length ? `<div class="t-list">${cau.map((x) => it(x, x.level === "bad" ? "bad" : "warn")).join("")}</div>` : tEmpty("특별히 조심할 것은 없어요");
  root.querySelector("#th-todo").innerHTML = tSec("오늘 확인할 것", todo, td.as_of ? `<span>${esc(td.as_of)}</span>` : "", "t-card") + tSec("오늘 주의할 것", cauB, "", "t-card");
  const nw = h5.news || {};
  if ((nw.items || []).length) {
    root.querySelector("#th-news").innerHTML = tSec("중요한 뉴스", `<div class="t-list">${nw.items.slice(0, 4).map((n) => tRow(`#n/${encodeURIComponent(n.id)}`,
      `<span class="t-co">${(n.symbols || [])[0] ? stockLogo(n.symbols[0].symbol, n.symbols[0].name, 36) : `<span class="t-ev-ic">${TI.news}</span>`}<span class="t-co-t"><b class="clamp2">${esc(n.title)}</b><span>${esc([(n.symbols || []).map((x) => x.name).join(" · "), n.first].filter(Boolean).join(" · "))}</span></span></span>`, "", { chev: true })).join("")}</div>`,
    '<a href="#news">뉴스 · 공시 전체</a>', "t-card");
  }
  const cc = h5.conclusion || {};
  const sys = Array.isArray(h5.system) ? h5.system : [];
  root.querySelector("#th-concl").innerHTML = (cc.text ? `<section class="t-concl t-concl-${esc(cc.level || "ok")}"><span class="t-k">오늘의 결론</span><b>${esc(cc.text)}</b></section>` : "")
    + (sys.length ? `<details class="t-sys"><summary>${lvDot(sys.some((x) => x.level === "bad") ? "bad" : "warn")}시스템 점검 필요 ${sys.length}건 <span>투자 판단과는 별개 · 눌러서 해결 방법</span></summary>
      ${sys.map((x) => `<div><b>${esc(x.text)}</b><span>${esc(x.fix)}</span></div>`).join("")}</details>` : "");
}

// ============================================================ 종목 상세
async function tStock(el, sym) {
  el.innerHTML = `<div class="ts ts-stock">${tBar("")}${tSkel(3)}</div>`;
  tBindBar(el.querySelector(".ts"), "#watch");
  const [st, starred] = await Promise.all([api(`/api/t/stock?symbol=${encodeURIComponent(sym)}`), api("/api/star").catch(() => ({ starred: [] }))]);
  const root = el.querySelector(".ts");
  if (!root?.isConnected) return;
  sym = st.symbol || sym;
  try { const r = JSON.parse(safeGet("qa_recent_t") || "[]").filter((x) => x.s !== sym); r.unshift({ s: sym, n: st.identity?.name || sym }); safeSet("qa_recent_t", JSON.stringify(r.slice(0, 12))); } catch { /* 무시 */ }
  const idt = st.identity || {};
  const name = idt.name || sym;
  const isStar = (starred.starred || []).some((x) => (x.symbol || x) === sym);
  const live = st.live;
  const last = live?.price ?? st.last, chgP = live?.chg_pct ?? st.chg_pct;
  const chgAbs = live ? (st.prev ? live.price - st.prev : null) : st.chg;
  const m = st.market || {};
  const when = live ? `실시간 · ${esc(time(live.at))}` : st.bar_date ? `${esc(st.bar_date.slice(5).replace("-", "."))} 종가` : "가격 자료 없음";
  const mkt = `<span class="t-mk ${m.state === "장중" ? "open" : ""}">${m.state === "장중" ? '<i class="live-dot"></i>' : ""}${esc(m.state || "")}${m.holiday ? `<em>${esc(m.holiday)}</em>` : ""}${m.state !== "장중" && m.next_kst ? `<em>${m.next === "폐장" ? "마감" : "개장"} ${esc(String(m.next_kst).replace(" KST", ""))}</em>` : ""}</span>`;
  const sub = [sym, idt.exchange, idt.industry || idt.sector].filter(Boolean).map(esc).join(" · ");
  root.innerHTML = `
    ${tBar(`<span class="t-bar-co">${stockLogo(sym, name, 24)}<b>${esc(name)}</b></span>`, { right: `<button class="t-ic" data-t-search aria-label="검색">${TI.search}</button><button class="t-ic t-star ${isStar ? "on" : ""}" id="tsk-star" aria-label="관심종목">${isStar ? TI.starOn : TI.star}</button>` })}
    <div class="tsk">
      <header class="tsk-head">
        <div class="tsk-id">${stockLogo(sym, name, 52)}<div><h1>${esc(name)}</h1><div class="t-sub">${sub}${idt.name_en && idt.name_en !== name ? ` · ${esc(idt.name_en)}` : ""}${isKR(sym) ? ` · <button class="t-link" data-logo-up="${esc(sym)}" data-logo-name="${esc(name)}">로고 바꾸기</button>` : ""}</div></div></div>
        <div class="tsk-px" id="tsk-px">${last == null ? `<b class="tsk-price dim">가격 자료 없음</b><span class="t-sub">${isKR(sym) ? "국내 일봉을 받으면 나와요 (./run.sh)" : "해외 시세는 인터넷 연결이 되면 받아와요 — 데이터 상태에서 확인"}</span>`
          : `<b class="num tsk-price">${tPx(last, sym)}</b><span class="num tsk-chg ${tCls(chgP)}">${chgAbs != null ? tSigned(chgAbs, sym) + " " : ""}(${tPct(chgP)})</span>`}</div>
        <div class="tsk-when">${when} ${mkt}</div>
      </header>
      <aside class="tsk-side" id="tsk-side"><div class="tsk-cards"><div class="tsk-card sk"></div><div class="tsk-card sk"></div></div></aside>
      <div class="tsk-main">
        ${tTabs("tsk-tabs", [["sum", "종합"], ["chart", "차트"], ["news", "뉴스"], ["disc", "공시"], ["earn", "실적"], ["ai", "분석"]], T.stab[sym] || "sum", "t-tabs-line sticky")}
        <div id="tsk-body"></div>
      </div>
    </div>
    <div class="tsk-act" id="tsk-act"><button class="t-btn ghost ${isStar ? "on" : ""}" id="tsk-star2">${isStar ? "관심종목 해제" : "관심종목"}</button><button class="t-btn primary" id="tsk-buy">모의 매수</button></div>`;
  tBindBar(root, "#watch");
  const setStar = async (on) => {
    try { await post("/api/star", { symbol: sym, on }); } catch (e) { toast({ title: "관심종목", body: e.message, level: "warn" }); return; }
    const a = root.querySelector("#tsk-star"), b = root.querySelector("#tsk-star2");
    a.classList.toggle("on", on); a.innerHTML = on ? TI.starOn : TI.star;
    b.classList.toggle("on", on); b.textContent = on ? "관심종목 해제" : "관심종목";
    toast({ title: on ? "관심종목에 담았어요" : "관심종목에서 뺐어요", body: name, level: "info", ms: 2500 });
  };
  root.querySelector("#tsk-star").onclick = () => setStar(!root.querySelector("#tsk-star").classList.contains("on"));
  root.querySelector("#tsk-star2").onclick = () => setStar(!root.querySelector("#tsk-star").classList.contains("on"));
  root.querySelector("#tsk-buy").onclick = () => tOrderSheet(sym, name, last);
  const ctx = { sym, name, st, root, v: null };
  tBind(root, "tsk-tabs", (k) => { T.stab[sym] = k; root.querySelectorAll("#tsk-tabs button").forEach((b) => { b.classList.toggle("on", b.dataset.k === k); b.setAttribute("aria-selected", b.dataset.k === k); }); tStockTab(ctx, k); });
  tStockTab(ctx, T.stab[sym] || "sum");
  // AI 판단 · 실적 D-day · 내 보유 (verdict 는 조금 느릴 수 있어 나중에)
  api(`/api/verdict?symbol=${encodeURIComponent(sym)}`).then((v) => { ctx.v = v; if (root.isConnected) tStockSide(ctx); })
    .catch((e) => { const s = root.querySelector("#tsk-side"); if (s) s.innerHTML = `<div class="tsk-cards">${tErr(e)}</div>`; });
}
function tStockSide(ctx) {
  const { v, sym, root } = ctx;
  const side = root.querySelector("#tsk-side");
  if (!side) return;
  const banned = v.trust?.key === "banned";
  const ai = v.final ? `<a class="tsk-card ai fin-${esc(v.final)}" href="#report/${encodeURIComponent(sym)}">
      <span class="t-k">AI 판단</span><div class="tsk-ai">${tPill(v.final, v.prob_up)}</div>
      <span class="t-sub">${v.horizon}거래일 기준 · ${esc(String(v.as_of || "").slice(5, 16))}${banned ? " · 참고만" : ""}</span></a>`
    : `<div class="tsk-card ai"><span class="t-k">AI 판단</span><div class="tsk-ai">${tPill(null)}</div><span class="t-sub" id="analyze-msg">아직 판단 기록이 없어요 · 지금 바로 판단할 수 있어요 (주문 없음)</span>
      <button class="t-btn primary sm" id="analyze-btn">지금 AI 분석</button></div>`;
  const e = v.earnings;
  const ev = e && e.d_label ? `<a class="tsk-card" href="#analysis/${encodeURIComponent(sym)}" data-tab="earn"><span class="t-k">실적 발표</span><b class="tsk-dd ${e.trading_days != null && e.trading_days <= 3 ? "soon" : ""}">${esc(e.d_label)}</b>
      <span class="t-sub">${esc(tDate(e.date))}${e.estimated ? " · 추정" : ""}${e.timing ? ` · ${esc(e.timing)}` : ""}</span></a>`
    : `<div class="tsk-card"><span class="t-k">실적 발표</span><b class="tsk-dd dim">일정 미확인</b><span class="t-sub">공개 일정이 아직 없어요</span></div>`;
  const h = v.holding;
  const hold = h ? `<a class="tsk-card wide" href="#pos"><span class="t-k">내 보유 · ${esc(h.book || "")}</span><b class="num">${num(h.qty)}주 · 평균 ${tPx(h.avg_price, sym)}</b><span class="num ${tCls(h.pnl_pct)}">${tPct(h.pnl_pct)}</span></a>` : "";
  const r = v.range52;
  const r52 = r && r.pos != null ? `<div class="tsk-52"><div class="t-k">52주 종가 범위 안 지금 위치</div><div class="t-range"><i style="left:${Math.round(r.pos * 100)}%"></i></div>
      <div class="t-range-l num"><span>${tPx(r.low, sym)}</span><span class="${tCls(r.from_high)}">고점 대비 ${tPct(r.from_high, 1)}</span><span>${tPx(r.high, sym)}</span></div></div>` : "";
  side.innerHTML = `<div class="tsk-cards">${ai}${ev}${hold}</div>${banned ? '<a class="t-note warn" href="#aitrust">AI 성적이 아직 기준 미달이라 판단은 참고만 하세요 — 주문에는 쓰이지 않아요</a>' : ""}${r52}`;
  const ab = side.querySelector("#analyze-btn");
  if (ab && typeof startAnalyze === "function") ab.onclick = () => startAnalyze(sym, () => render());
  side.querySelector("[data-tab=earn]")?.addEventListener("click", (ev2) => { ev2.preventDefault(); root.querySelector('#tsk-tabs button[data-k="earn"]')?.click(); });
  if ((T.stab[sym] || "sum") === "sum") tStockSumAI(ctx);
}
const PERIODS = [[1, "1일"], [5, "1주"], [21, "1개월"], [63, "3개월"], [126, "6개월"], [252, "1년"], [756, "3년"]];
async function tStockTab(ctx, k) {
  const { sym, st, root } = ctx;
  const body = root.querySelector("#tsk-body");
  if (!body) return;
  clearCharts();
  body.innerHTML = tSkel(3);
  if (k === "sum") {
    const s = st.stats || {};
    const stat = (l, val) => `<div class="t-stat"><span>${l}</span><b class="num">${val}</b></div>`;
    body.innerHTML = `
      <div class="tsk-chart-w"><div class="t-hover" id="tsk-hover"></div><div class="tsk-chart" id="tsk-chart"></div>
        ${tChips("tsk-per", PERIODS.map(([n, l]) => [String(n), l]), String(T.period))}<div class="t-foot">1일 = 5분봉 (조금 늦을 수 있어요) · 나머지는 일봉 종가 기준</div></div>
      ${tSec("시세", `<div class="t-stats">${stat("시가", tPx(s.open, sym))}${stat("고가", `<span class="up">${tPx(s.high, sym)}</span>`)}${stat("저가", `<span class="down">${tPx(s.low, sym)}</span>`)}
        ${stat("거래량", tVol(s.volume))}${stat("52주 최고", tPx(s.high52, sym))}${stat("52주 최저", tPx(s.low52, sym))}${stat("시가총액", tCap(st.market_cap, sym))}${stat("20일 평균 거래량", tVol(s.avg_volume20))}</div>
        ${st.bar_date ? `<div class="t-foot">${esc(st.bar_date)} 일봉 기준${st.market_cap ? "" : " · 시가총액은 종목 상세 자료를 받으면 나와요"}</div>` : ""}`, "", "t-card")}
      <div id="tsk-sum-ai"></div><div id="tsk-sum-news"></div>`;
    tBind(body, "tsk-per", (n) => { T.period = Number(n); body.querySelectorAll("#tsk-per button").forEach((b) => b.classList.toggle("on", b.dataset.k === n)); tStockChart(ctx); });
    tStockChart(ctx);
    if (ctx.v) tStockSumAI(ctx);
    api(`/api/stock/news?symbol=${encodeURIComponent(sym)}`).then((n) => { const b = root.querySelector("#tsk-sum-news"); if (b) b.innerHTML = tSec("최근 소식", tNewsList(n, 3), '<button class="t-link" data-go="news">더 보기</button>', "t-card"); tGoTab(root); }).catch(() => {});
  } else if (k === "chart") {
    body.innerHTML = `<div class="t-card t-sec">${tChips("tsk-per2", PERIODS.filter(([n]) => n > 1).map(([n, l]) => [String(n), l]), String(Math.max(T.period, 21)))}<div class="tsk-candle" id="tsk-candle"></div>
      <div class="t-legend"><span><i style="background:#f59e0b"></i>5일 평균</span><span><i style="background:#a78bfa"></i>20일</span><span><i style="background:#38bdf8"></i>60일</span><span><i class="mk"></i>AI 매수/매도 표시</span></div></div>`;
    const draw = (n) => { clearCharts(); const c = body.querySelector("#tsk-candle"); c.innerHTML = ""; candleChart(c, sym, n).catch((e) => { c.innerHTML = tErr(e); }); };
    tBind(body, "tsk-per2", (n) => { body.querySelectorAll("#tsk-per2 button").forEach((b) => b.classList.toggle("on", b.dataset.k === n)); draw(Number(n)); });
    draw(Math.max(T.period, 21));
  } else if (k === "news" || k === "disc") {
    let n;
    try { n = await api(`/api/stock/news?symbol=${encodeURIComponent(sym)}`); } catch (e) { body.innerHTML = tErr(e); return; }
    if (!body.isConnected) return;
    const sum = (n.rule_summary || []).length ? `<div class="t-note">${n.rule_summary.map((x) => `<div>${esc(x)}</div>`).join("")}</div>` : "";
    const com = k === "news" ? await api(`/api/t/community?symbol=${encodeURIComponent(sym)}`).catch(() => null) : null;
    const comB = com ? tSec("커뮤니티 분위기", com.at ? `<div class="t-kvs"><div><span class="t-k">${esc(com.source || "")} · ${esc(com.ago || "")}</span><b>${esc(com.label || "-")}</b><span class="t-sub">글 ${num(com.n)}개 · 낙관 ${num(com.bull)} · 비관 ${num(com.bear)}</span></div></div>
        <div class="t-list">${(com.posts || []).slice(0, 6).map((p) => `<a class="t-li" href="${esc(p.url || "#")}" target="_blank" rel="noopener noreferrer"><span class="t-co-t"><b class="clamp2">${esc(p.title)}</b></span>${p.sentiment != null ? `<span class="t-tone ${p.sentiment > 0.1 ? "up" : p.sentiment < -0.1 ? "down" : ""}">${p.sentiment > 0.1 ? "낙관" : p.sentiment < -0.1 ? "비관" : "중립"}</span>` : ""}</a>`).join("")}</div>
        <div class="t-foot">${esc(com.note)}</div>`
      : tEmpty("아직 모은 커뮤니티 글이 없어요", com.failed_at ? "최근 수집이 실패했어요 — 데이터 상태 화면에서 이유를 볼 수 있어요" : "관심종목·보유 종목만 30분마다 모아요 (24시간 운영이 켜져 있을 때)", '<a class="t-btn" href="#datahealth">수집 상태</a>'), "", "t-card") : "";
    body.innerHTML = k === "news" ? `${comB}<div class="t-card t-sec">${n.overall ? `<div class="t-sub">뉴스 분위기 · <b>${esc(n.overall)}</b> (긍정 ${n.counts?.긍정 ?? 0} · 중립 ${n.counts?.중립 ?? 0} · 부정 ${n.counts?.부정 ?? 0})</div>` : ""}${tNewsList(n, 30)}${sum}</div>`
      : `<div class="t-card t-sec">${tDiscList(n.disclosures || [])}</div>`;
  } else if (k === "earn") {
    let e;
    try { e = await api(`/api/stock/earnings?symbol=${encodeURIComponent(sym)}`); } catch (er) { body.innerHTML = tErr(er); return; }
    if (!body.isConnected) return;
    const up = e.upcoming || e.kr_upcoming;
    const q = (e.quarterly || []).slice(-8).reverse();
    const rows = (e.rows || []).slice(-8).reverse();
    body.innerHTML = `<div class="t-card t-sec">
      <div class="t-kvs"><div><span class="t-k">다음 발표</span><b>${up ? esc(up.d_label || up.date || "") : "미확인"}</b><span class="t-sub">${up ? esc([tDate(up.date), up.timing, up.estimated ? "추정" : ""].filter(Boolean).join(" · ")) : "공개된 일정 없음"}</span></div>
        <div><span class="t-k">예상치 상회 비율</span><b>${e.beat_rate == null ? "-" : `${Math.round(e.beat_rate * 100)}%`}</b><span class="t-sub">최근 ${e.n || 0}회</span></div>
        <div><span class="t-k">상회 뒤 반응</span><b class="${tCls(e.avg_reaction_beat)}">${tPct(e.avg_reaction_beat, 1)}</b><span class="t-sub">시장 대비</span></div>
        <div><span class="t-k">하회 뒤 반응</span><b class="${tCls(e.avg_reaction_miss)}">${tPct(e.avg_reaction_miss, 1)}</b><span class="t-sub">시장 대비</span></div></div>
      ${rows.length ? `<table class="t-table"><thead><tr><th>발표일</th><th class="r">EPS 예상</th><th class="r">실제</th><th class="r">놀람</th><th class="r">다음날 반응</th></tr></thead><tbody>${rows.map((r) => `<tr><td>${esc(r.date || "")}</td><td class="r num">${r.eps_est == null ? "-" : num(r.eps_est, 2)}</td><td class="r num">${r.eps == null ? "-" : num(r.eps, 2)}</td><td class="r num ${tCls(r.surprise)}">${tPct(r.surprise, 1)}</td><td class="r num ${tCls(r.reaction)}">${tPct(r.reaction, 1)}</td></tr>`).join("")}</tbody></table>`
        : q.length ? `<table class="t-table"><thead><tr><th>분기</th><th class="r">매출</th><th class="r">영업이익</th></tr></thead><tbody>${q.map((r) => `<tr><td>${esc(r.period || r.q || "")}</td><td class="r num">${r.revenue == null ? "-" : tCap(r.revenue, sym)}</td><td class="r num">${r.op_income == null ? "-" : tCap(r.op_income, sym)}</td></tr>`).join("")}</tbody></table>`
          : tEmpty("실적 기록이 아직 없어요", (e.sources || []).join(" · "))}
      ${e.guidance ? `<div class="t-foot">${esc(e.guidance)}</div>` : ""}${e.note ? `<div class="t-foot">${esc(e.note)}</div>` : ""}</div>`;
  } else if (k === "ai") {
    body.innerHTML = `<div id="tsk-rep">${tSkel(3)}</div><div class="t-pro"><a class="t-btn ghost" href="#analysis/${encodeURIComponent(sym)}/full">전체 분석 화면 (전문가용)</a><span>AI 의견 원문 · 시나리오 · 사전 리스크 점검 · 차트 표시 · 수급</span></div>`;
    tReportInto(body.querySelector("#tsk-rep"), sym, true);
  }
}
function tGoTab(root) { root.querySelectorAll("[data-go]").forEach((b) => b.onclick = () => root.querySelector(`#tsk-tabs button[data-k="${b.dataset.go}"]`)?.click()); }
async function tStockChart(ctx) {
  const { sym, root, st } = ctx;
  const el = root.querySelector("#tsk-chart");
  if (!el) return;
  clearCharts();
  el.innerHTML = "";
  if (T.period === 1) { await tStockIntraday(ctx, el); return; }  // v27: 하루 안 움직임 (5분봉)
  let d;
  try { d = await api(`/api/chart?symbol=${encodeURIComponent(sym)}&n=${T.period + 1}`); } catch (e) { el.innerHTML = tErr(e); return; }
  if (!el.isConnected) return;
  const bars = d.bars || [];
  const hv = root.querySelector("#tsk-hover");
  const per = bars.length > 1 ? bars[bars.length - 1].close / bars[0].close - 1 : null;
  const label = (PERIODS.find(([n]) => n === T.period) || [0, ""])[1];
  const base = () => { if (hv) hv.innerHTML = per == null ? "" : `<span>${label} 동안</span> <b class="num ${tCls(per)}">${tPct(per)}</b>`; };
  base();
  tArea(el, bars, sym, (p) => {
    if (!hv) return;
    if (!p) { base(); return; }
    const dt = new Date(p.bar.time * 1000);
    hv.innerHTML = `<span>${dt.getUTCFullYear()}.${String(dt.getUTCMonth() + 1).padStart(2, "0")}.${String(dt.getUTCDate()).padStart(2, "0")}</span> <b class="num">${tPx(p.bar.close, sym)}</b> <span class="num ${tCls(p.chg)}">${tPct(p.chg)}</span>`;
  });
  void st;
}
// v27: '1일' — 오늘(또는 마지막 거래일) 5분봉. 기준선 = 어제 종가. 못 받으면 이유 + 일봉 단추
async function tStockIntraday(ctx, el) {
  const { sym, root } = ctx;
  const hv = root.querySelector("#tsk-hover");
  let d;
  try { d = await api(`/api/t/intraday?symbol=${encodeURIComponent(sym)}`); } catch (e) { d = { points: [], error: e.message }; }
  if (!el.isConnected) return;
  const pts = d.points || [];
  if (pts.length < 2) {
    el.innerHTML = tEmpty("하루 움직임(분봉)을 받지 못했어요", d.error || "장 시작 전이거나 휴장일 수 있어요", '<button class="t-btn" id="tsk-to-week">1주 차트로 보기</button>');
    if (hv) hv.innerHTML = "";
    const b = el.querySelector("#tsk-to-week");
    if (b) b.onclick = () => root.querySelector('#tsk-per button[data-k="5"]')?.click();
    return;
  }
  const off = Number(d.gmtoffset) || 0;  // 차트는 UTC 로 그리므로 거래소 현지 시각이 보이게 더한다
  const bars = pts.map((p) => ({ time: p[0] + off, close: p[1], volume: p[2] || 0 }));
  const prev = Number(d.prev_close) || bars[0].close;
  const last = bars[bars.length - 1].close;
  const hm = (t) => { const x = new Date(t * 1000); return `${String(x.getUTCHours()).padStart(2, "0")}:${String(x.getUTCMinutes()).padStart(2, "0")}`; };
  const base = () => { if (hv) hv.innerHTML = `<span>오늘 (어제 종가 대비)</span> <b class="num ${tCls(last - prev)}">${tPct(last / prev - 1)}</b> <span class="t-sub">${esc(d.source || "")}</span>`; };
  base();
  const ch = tArea(el, bars, sym, (p) => {
    if (!hv) return;
    if (!p) { base(); return; }
    hv.innerHTML = `<span>${hm(p.bar.time)}</span> <b class="num">${tPx(p.bar.close, sym)}</b> <span class="num ${tCls(p.bar.close - prev)}">${tPct(p.bar.close / prev - 1)}</span>`;
  });
  if (ch) ch.applyOptions({ timeScale: { timeVisible: true, secondsVisible: false } });
}
function tStockSumAI(ctx) {
  const box = ctx.root.querySelector("#tsk-sum-ai");
  const v = ctx.v;
  if (!box || !v) return;
  if (!v.final) { box.innerHTML = tSec("AI 한 줄 요약", tEmpty("아직 AI 판단이 없어요", v.why_none || ""), "", "t-card"); return; }
  const li = (xs) => xs.slice(0, 3).map((x, i) => `<li><span class="t-num">${i + 1}</span>${esc(koText(x))}</li>`).join("");
  box.innerHTML = tSec("AI 한 줄 요약", `<div class="t-ai-sum">${tPill(v.final, v.prob_up)}<span>${esc(koText((v.plain || [])[2] || (v.plain || [])[0] || v.headline || ""))}</span></div>
    <div class="t-why2"><div><div class="t-k up">근거</div><ol class="t-ol">${li(v.why_buy || []) || "<li>뚜렷한 근거 없음</li>"}</ol></div>
      <div><div class="t-k down">주의할 점</div><ol class="t-ol">${li(v.why_not || []) || "<li>막는 이유 없음</li>"}</ol></div></div>`,
  `<a href="#report/${encodeURIComponent(ctx.sym)}">AI 리포트</a>`, "t-card");
}
function tNewsList(n, lim) {
  const xs = (n.items || []).slice(0, lim);
  if (!xs.length) return tEmpty("저장된 뉴스가 없어요", "뉴스는 24시간 운영(./run.sh)이 켜져 있을 때 모입니다");
  return `<div class="t-list">${xs.map((a) => tRow(`#n/${encodeURIComponent(a.id)}`, `<span class="t-co"><span class="t-ev-ic">${TI.news}</span><span class="t-co-t"><b class="clamp2">${esc(a.title)}</b><span>${esc([a.source, a.at].filter(Boolean).join(" · "))}</span></span></span>`,
    a.sent != null ? `<span class="t-tone ${a.sent > 0.2 ? "up" : a.sent < -0.2 ? "down" : ""}">${a.sent > 0.2 ? "긍정" : a.sent < -0.2 ? "부정" : "중립"}</span>` : "")).join("")}</div>`;
}
function tDiscList(ds) {
  if (!ds.length) return tEmpty("최근 공시가 없어요", "국내는 DART 키, 미국은 SEC 수집이 켜져 있어야 모입니다");
  return `<div class="t-list">${ds.map((d) => tRow(`#d/${encodeURIComponent(d.id)}`, `<span class="t-co"><span class="t-ev-ic">${TI.doc}</span><span class="t-co-t"><b class="clamp2">${esc(d.title)}</b><span>${esc([d.source, d.date].filter(Boolean).join(" · "))}${d.summary ? ` · ${esc(String(d.summary).slice(0, 60))}` : ""}</span></span></span>`, "", { chev: true })).join("")}</div>`;
}

// 모의 매수 시트 (국내: 수동 모의 장부 · 실제 돈 아님)
function tOrderSheet(sym, name, last) {
  const us = !isKR(sym);  // v27: 미국 종목도 모의 주문 (달러 장부 us-manual · 원화 환산은 참고 환율)
  tSheet(`<h3>${esc(name)} <span class="t-sub">${tPx(last, sym)}</span></h3>
    ${tTabs("tk2-side", [["buy", "매수"], ["sell", "매도"]], "buy", "t-seg")}
    <label class="t-field"><span>수량</span><div class="t-stepper"><button data-d="-1" aria-label="빼기">−</button><input id="tk2-qty" type="number" inputmode="numeric" min="1" value="1"><button data-d="1" aria-label="더하기">+</button></div></label>
    <div class="t-sub" id="tk2-amt"></div>
    <div id="tk2-out"></div>
    <div class="t-sheet-act"><button class="t-btn ghost" id="tk2-prev">점검하기</button><button class="t-btn primary" id="tk2-go" disabled>모의 주문</button></div>
    <div class="t-foot">${us ? "미국 모의 장부(달러 · 시작 $100,000)에 기록돼요 · 수수료 0.25% 가정 · 환전 비용은 빼고 계산" : "수동 모의 장부에 기록돼요"} · 실제 돈·실제 주문이 아니에요 · 주문 전에 데이터 상태·한도·AI 판단을 먼저 점검해요</div>`, (b, close) => {
    let side = "buy", last2 = null;
    const qty = b.querySelector("#tk2-qty"), out = b.querySelector("#tk2-out"), go = b.querySelector("#tk2-go");
    const amt = () => { b.querySelector("#tk2-amt").textContent = `예상 금액 약 ${us ? `$${num((Number(qty.value) || 0) * (last || 0), 2)}` : tWon((Number(qty.value) || 0) * (last || 0))}`; go.disabled = true; out.innerHTML = ""; };
    amt();
    qty.oninput = amt;
    b.querySelectorAll(".t-stepper button").forEach((x) => x.onclick = () => { qty.value = Math.max(1, (Number(qty.value) || 0) + Number(x.dataset.d)); amt(); });
    tBind(b, "tk2-side", (k) => { side = k; b.querySelectorAll("#tk2-side button").forEach((x) => x.classList.toggle("on", x.dataset.k === k)); go.textContent = k === "buy" ? "모의 매수" : "모의 매도"; amt(); });
    const run = async (place) => {
      out.innerHTML = tSkel(1);
      const v = place ? await post("/api/ticket", { symbol: sym, side, qty: qty.value, confirm: true }).catch((e) => ({ error: e.message }))
        : await api(`/api/ticket?symbol=${encodeURIComponent(sym)}&side=${side}&qty=${encodeURIComponent(qty.value)}`).catch((e) => ({ error: e.message }));
      if (v.error) { out.innerHTML = tErr(v); return; }
      last2 = v;
      const ok = v.verdict === "통과" || v.verdict === "축소";
      out.innerHTML = `<div class="t-check ${v.verdict === "통과" ? "ok" : v.verdict === "축소" ? "warn" : "bad"}"><b>${esc(v.verdict)}</b> · 허용 ${num(v.allowed_qty)}주 / 요청 ${num(v.want_qty)}주${v.fee_tax ? ` · 비용 ${us ? `$${num(v.fee_tax, 2)}` : tWon(v.fee_tax)}` : ""}${us && v.notional_krw ? ` · 약 ${tMoney(v.notional_krw)} (환율 ${num(v.fx, 1)}원)` : ""}</div>
        <div class="t-steps">${(v.steps || []).map((s) => `<div>${lvDot({ ok: "good", warn: "warn", bad: "bad" }[s.status] || "idle")}<b>${esc(s.gate)}</b><span>${esc(s.detail)}</span></div>`).join("")}</div>
        ${v.message ? `<div class="t-check ${v.placed ? "ok" : "bad"}">${esc(v.message)}</div>` : ""}`;
      go.disabled = place || !ok || !(v.allowed_qty > 0);
      if (place && v.placed) { toast({ title: "모의 체결", body: v.message, level: "good" }); setTimeout(close, 900); }
    };
    b.querySelector("#tk2-prev").onclick = () => run(false);
    go.onclick = () => { if (last2 && confirm(`모의 ${side === "buy" ? "매수" : "매도"} ${last2.allowed_qty}주 — 실제 돈이 아닌 모의 장부에 기록합니다.`)) run(true); };
  });
}

// ============================================================ 종목 찾기 (#analysis 에 종목이 없을 때)
async function tFind(el) {
  let recent = [];
  try { recent = JSON.parse(safeGet("qa_recent_t") || "[]"); } catch { /* 무시 */ }
  el.innerHTML = `<div class="ts ts-find"><h1 class="t-h1">종목</h1>
    <button class="t-searchbox" data-t-search>${TI.search}<span>종목 이름이나 코드로 검색 (예: 삼성전자, NVDA)</span><kbd>/</kbd></button>
    ${recent.length ? tSec("최근 본 종목", `<div class="t-chips">${recent.map((r) => `<a class="t-chipa" href="#analysis/${encodeURIComponent(r.s)}">${stockLogo(r.s, r.n, 18)}${esc(r.n)}</a>`).join("")}</div>`) : ""}
    <div id="tf-w">${tSkel(4)}</div></div>`;
  tBindBar(el);
  const w = await api("/api/watchlist").catch((e) => ({ error: e.message }));
  const box = el.querySelector("#tf-w");
  if (box) box.innerHTML = w.error ? tErr(w) : tSec("관심 · 보유 종목", tWatchRows(w.rows || []), '<a href="#watch">편집</a>', "t-card");
}

// ============================================================ 관심종목
function tWatchRows(rows, o = {}) {
  if (!rows.length) return tEmpty("관심종목이 없어요", "종목을 검색해 ☆ 를 누르면 여기 모여요", '<button class="t-btn" data-t-search>종목 검색</button>');
  return `<div class="t-list">${rows.map((r) => tRow(`#analysis/${encodeURIComponent(r.symbol)}`,
    tName(r.symbol, r.name, `${esc(r.symbol)}${r.held ? ' · <em class="t-held">보유</em>' : ""} · ${tPill(r.ai, r.prob_up, { short: true })}`),
    tPrice(r.last, r.chg_pct, r.symbol), { attr: o.edit ? ` data-sym="${esc(r.symbol)}"` : "" })).join("")}</div>`;
}
async function tWatch(el) {
  el.innerHTML = `<div class="ts ts-watch"><h1 class="t-h1">관심종목</h1>${tSkel(5)}</div>`;
  const w = await api("/api/watchlist");
  const root = el.querySelector(".ts");
  if (!root?.isConnected) return;
  const all = w.rows || [];
  const groups = [...new Set(all.map((r) => r.group).filter(Boolean))];
  const draw = () => {
    let rows = all.filter((r) => T.watchTab === "all" || (T.watchTab === "kr" && isKR(r.symbol)) || (T.watchTab === "us" && !isKR(r.symbol)) || (T.watchTab === "held" && r.held) || (T.watchTab === `g:${r.group}`));
    const SORT = { up: (a, b) => (b.chg_pct ?? -9) - (a.chg_pct ?? -9), down: (a, b) => (a.chg_pct ?? 9) - (b.chg_pct ?? 9), name: (a, b) => String(a.name).localeCompare(String(b.name), "ko"), ai: (a, b) => (b.prob_up ?? 0) - (a.prob_up ?? 0) };
    if (SORT[T.watchSort]) rows = [...rows].sort(SORT[T.watchSort]);
    const n = (f) => all.filter(f).length;
    root.innerHTML = `<div class="t-h1row"><h1 class="t-h1">관심종목</h1><button class="t-ic" data-t-search aria-label="검색">${TI.search}</button></div>
      ${tTabs("tw-tab", [["all", `전체 ${all.length}`], ["kr", `국내 ${n((r) => isKR(r.symbol))}`], ["us", `해외 ${n((r) => !isKR(r.symbol))}`], ["held", `보유 ${n((r) => r.held)}`], ...groups.filter((g) => g !== "기본").map((g) => [`g:${g}`, esc(g)])], T.watchTab, "t-tabs-line")}
      <div class="t-sortrow">${tChips("tw-sort", [["default", "기본"], ["up", "상승률"], ["down", "하락률"], ["ai", "AI 확률"], ["name", "이름"]], T.watchSort)}</div>
      <div class="t-card t-sec">${tWatchRows(rows)}</div>
      <div class="t-foot">가격은 ${esc((all[0] || {}).price_src || "일봉 종가")} · AI 판단 시각 ${esc((all.find((r) => r.ai_at) || {}).ai_at || "-")}</div>`;
    tBind(root, "tw-tab", (k) => { T.watchTab = k; draw(); });
    tBind(root, "tw-sort", (k) => { T.watchSort = k; draw(); });
    tBindBar(root);
  };
  draw();
}

// ============================================================ 뉴스 · 공시 · 일정 한 목록
async function tFeed(el) {
  const f = T.feed;
  el.innerHTML = `<div class="ts ts-feed"><h1 class="t-h1">뉴스 · 공시</h1>
    ${tTabs("tf-tab", [["all", "전체"], ["news", "뉴스"], ["disc", "공시"], ["event", "일정"]], f.tab, "t-tabs-line")}
    ${tChips("tf-chip", [["all", "전체"], ["kr", "한국"], ["us", "미국"], ["|"], ["ai", "AI"], ["semi", "반도체"], ["mine", "내 종목"]], [f.region === "all" && f.topic === "all" ? "all" : "", f.region, f.topic])}
    <div class="t-card t-sec" id="tf-list">${tSkel(6)}</div>
    <div class="t-pro"><a class="t-btn ghost" href="#newslist">원문 목록 · 검색 (전문가용)</a></div></div>`;
  const root = el.querySelector(".ts");
  tBind(root, "tf-tab", (k) => { f.tab = k; tFeed(el); });
  tBind(root, "tf-chip", (k) => {
    if (k === "all") { f.region = "all"; f.topic = "all"; } else if (k === "kr" || k === "us") f.region = f.region === k ? "all" : k; else f.topic = f.topic === k ? "all" : k;
    tFeed(el);
  });
  let d;
  try { d = await api(`/api/t/feed?tab=${f.tab}&region=${f.region}&topic=${f.topic}`); } catch (e) { root.querySelector("#tf-list").innerHTML = tErr(e); return; }
  const box = root.querySelector("#tf-list");
  if (!box?.isConnected) return;
  const items = d.items || [];
  if (!items.length) {
    box.innerHTML = tEmpty(f.region !== "all" || f.topic !== "all" ? "조건에 맞는 소식이 없어요" : "최근 7일 소식이 없어요",
      f.tab === "disc" ? "공시는 DART 키(국내)·SEC 수집(미국)이 켜져 있어야 모여요" : "뉴스는 24시간 운영(./run.sh)이 켜져 있을 때 모여요",
      '<a class="t-btn" href="#datahealth">수집 상태 보기</a>');
    return;
  }
  const KIND = { news: "뉴스", disc: "공시", event: "일정" };
  box.innerHTML = `<div class="t-list">${items.map((x) => {
    const href = x.kind === "news" ? `#n/${x.id}` : x.kind === "disc" ? `#d/${x.id}` : x.symbols[0] ? `#analysis/${encodeURIComponent(x.symbols[0])}` : "#calendar";
    const ic = x.symbols[0] ? stockLogo(x.symbols[0], x.names[0], 44) : `<span class="t-ev-ic big">${x.kind === "event" ? TI.cal : x.kind === "disc" ? TI.doc : TI.news}</span>`;
    const meta = x.kind === "event" ? [`<em class="t-kind k-event">${esc(EV_KO[x.ev_kind] || "일정")}</em>`, esc(tDate(x.date)), x.market ? (x.market === "KR" ? "한국" : x.market === "US" ? "미국" : esc(x.market)) : "", esc(x.names[0] || "")].filter(Boolean).join(" · ")
      : [x.kind !== "news" ? `<em class="t-kind k-${x.kind}">${KIND[x.kind]}</em>` : "", esc(x.names.slice(0, 2).join(" · ")), esc(x.source || ""), esc(x.ago || "")].filter(Boolean).join(" · ");
    const right = x.kind === "event" ? tDday({ d_day: x.d_day, d_label: x.d_label }) : x.tone !== "중립" ? `<span class="t-tone ${x.tone === "긍정" ? "up" : "down"}">${esc(x.tone)}</span>` : "";
    return tRow(href, `<span class="t-co">${ic}<span class="t-co-t"><b class="clamp2">${esc(x.title)}</b><span>${meta}${x.mine ? ' · <em class="t-held">내 종목</em>' : ""}</span></span></span>`, right, { cls: "t-li-feed" });
  }).join("")}</div><div class="t-foot">${d.n}건 · ${esc(d.as_of)} · 최근 7일 (일정은 30일 안)</div>`;
}

// ============================================================ 기사 · 공시 상세 (전체 화면)
async function tArticle(el, kind, id) {
  el.innerHTML = `<div class="ts ts-art">${tBar(kind === "news" ? "뉴스" : "공시")}${tSkel(4)}</div>`;
  tBindBar(el.querySelector(".ts"), "#news");
  let d;
  try { d = await api(`/api/${kind === "news" ? "news" : "disclosure"}/${encodeURIComponent(id)}`); } catch (e) { el.querySelector(".ts").innerHTML = tBar("") + tErr(e); tBindBar(el.querySelector(".ts"), "#news"); return; }
  const root = el.querySelector(".ts");
  if (!root?.isConnected) return;
  if (d.error) { root.innerHTML = tBar("") + tErr(d); tBindBar(root, "#news"); return; }
  const ex = d.explain || {};
  const foreign = d.lang && d.lang !== "ko";
  const translated = !foreign || !!ex.translation;
  const syms = [...new Map([...(d.chain || []), ...(d.related || [])].map((c) => [c.symbol, c])).values()].slice(0, 8);
  const lead = syms[0];
  const w = d.so_what || {};
  const tab = T.artTab || (foreign && ex.translation ? "ko" : "orig");
  const reasons = [w.meaning, (w.mine || []).length ? `내 보유: ${w.mine.map((m) => `${m.name} ${num(m.qty)}주`).join(" · ")}` : "", ...(w.ai_changes || []).map((c) => `${c.name} AI 판단 ${koText(c.text)}`), ex.impact].filter(Boolean);
  root.innerHTML = `${tBar(kind === "news" ? "뉴스" : "공시", { right: d.url ? `<a class="t-ic" href="${esc(d.url)}" target="_blank" rel="noopener noreferrer" aria-label="원문">${TI.ext}</a>` : "" })}
    <article class="t-art">
      <div class="t-art-hero">${lead ? stockLogo(lead.symbol, lead.name, 56) : `<span class="t-ev-ic big">${kind === "news" ? TI.news : TI.doc}</span>`}
        <div class="t-art-hero-t">${lead ? `<b>${esc(lead.name || lead.symbol)}${syms.length > 1 ? ` <span class="t-sub">외 ${syms.length - 1}종목</span>` : ""}</b>` : ""}<div class="t-art-tags">${d.level?.label ? `<span class="t-tag lv">${esc(d.level.label)}</span>` : ""}${d.event_ko ? `<span class="t-tag">${esc(d.event_ko)}</span>` : ""}
          ${d.tone_ko ? `<span class="t-tag ${d.tone_ko === "긍정" ? "up" : d.tone_ko === "부정" ? "down" : ""}">${esc(d.tone_ko)}</span>` : ""}${d.rumor ? '<span class="t-tag warn">루머·관측</span>' : ""}${d.important ? '<span class="t-tag warn">중요 공시</span>' : ""}</div></div></div>
      <h1 class="t-art-title">${esc(foreign && ex.ko_title ? ex.ko_title : d.title)}</h1>
      ${foreign && ex.ko_title ? `<div class="t-art-orig">${esc(d.title)}</div>` : ""}
      <div class="t-art-meta">${esc(d.source || "")}${d.source_weight ? ` · 출처 신뢰도 ${Math.round(d.source_weight * 100)}%` : kind !== "news" ? " · 1차 자료" : ""} · ${esc(d.time?.text || "")}</div>
      <div class="t-art-chips">${d.url ? `<a class="t-chipa" href="${esc(d.url)}" target="_blank" rel="noopener noreferrer">원문 보기 ${TI.ext}</a>` : ""}
        <span class="t-chipa ${translated ? "ok" : ""}">${!foreign ? "한국어 기사" : ex.translation ? "번역 완료" : "번역 전"}</span>
        <span class="t-chipa ${ex.by === "llm" ? "ok" : ""}">${ex.by === "llm" ? "AI 설명 완료" : "규칙 기반 설명"}</span></div>
      ${tTabs("ta-tab", [["orig", "원문"], ["ko", "한국어 번역"], ["ai", "AI 요약"]], tab, "t-tabs-line")}
      <div class="t-art-body" id="ta-body"></div>
      ${reasons.length || w.conclusion ? tSec(kind === "news" ? "이 뉴스가 중요한 이유" : "이 공시가 중요한 이유", `<ol class="t-ol big">${reasons.slice(0, 4).map((x, i) => `<li><span class="t-num">${i + 1}</span>${esc(koText(x))}</li>`).join("")}</ol>
        ${w.conclusion ? `<div class="t-concl t-concl-${esc(w.level || "ok")}"><span class="t-k">결론</span><b>${esc(w.conclusion)}</b></div>` : ""}${w.note ? `<div class="t-foot">${esc(w.note)}</div>` : ""}`, "", "t-card") : ""}
      ${(d.fin || []).length || (d.numbers || []).length ? tSec("중요한 숫자", `<div class="t-fin">${(d.fin || []).map((f) => `<div><span class="t-k">${esc(f.label)}</span><b class="num">${esc(f.value)}</b>${f.change ? `<span class="num ${f.change.startsWith("-") ? "down" : "up"}">${esc(f.change)}</span>` : ""}</div>`).join("")}
        ${(d.numbers || []).map((n) => `<div><span class="t-k">${esc(n.label || "기사 속 숫자")}</span><b class="num">${esc(n.value)}</b></div>`).join("")}</div>`, "", "t-card") : ""}
      ${syms.length ? tSec("관련 종목", `<div class="t-hs" id="ta-rel">${syms.map((c) => `<a class="t-relc" href="#analysis/${encodeURIComponent(c.symbol)}" data-sym="${esc(c.symbol)}">${stockLogo(c.symbol, c.name, 36)}<b>${esc(c.name)}</b><span class="num t-pc" data-chg>&nbsp;</span>${c.level ? `<em>${esc({ "🔴": "기사에 직접", "🟠": "같은 업종", "🟡": "함께 언급" }[c.level] || c.level)}</em>` : ""}</a>`).join("")}</div>`, "", "t-card") : ""}
      ${(d.terms || []).length ? tSec("용어 풀이", d.terms.map((t) => `<div class="t-term"><b>${esc(t.term)}</b><span>${esc(t.meaning)}</span></div>`).join(""), "", "t-card") : ""}
      ${(d.siblings || []).length ? tSec(`같은 소식 · 다른 매체 ${d.siblings.length}곳`, `<div class="t-list">${d.siblings.map((a) => `<a class="t-li" href="${esc(a.url || "#")}" target="_blank" rel="noopener noreferrer"><span class="t-co-t"><b class="clamp2">${esc(a.title)}</b><span>${esc(a.source || "")} · ${esc(a.at || "")}</span></span></a>`).join("")}</div>`, "", "t-card") : ""}
      <div class="t-foot">${esc(d.note || "")}</div>
    </article>`;
  tBindBar(root, "#news");
  const drawBody = (k) => {
    T.artTab = k;
    const b = root.querySelector("#ta-body");
    const aiBtn = `<button class="t-btn" id="ta-ai">${ex.by === "llm" ? "AI 설명 다시 받기" : "AI 로 번역 · 쉬운 설명 받기"}</button>`;
    if (k === "orig") b.innerHTML = `<div class="t-text">${esc(d.body || d.summary || "본문이 저장되지 않았어요 — 원문 보기에서 확인하세요")}</div>`;
    else if (k === "ko") b.innerHTML = !foreign ? `<div class="t-text">${esc(d.body || d.summary || "")}</div><div class="t-foot">한국어 기사라 번역이 필요 없어요</div>`
      : ex.translation ? `<div class="t-text">${esc(ex.translation)}</div><div class="t-foot">AI 번역 · 숫자·고유명사는 원문과 꼭 대조하세요</div>`
        : `${tEmpty("아직 번역하지 않았어요", ex.note || "AI 번역은 .env 에 LLM 키가 있어야 해요 (키가 없으면 제목만 규칙으로 바꿔요)")}${aiBtn}`;
    else b.innerHTML = `<div class="t-easy">${esc(ex.easy || "-")}</div>${ex.impact ? `<div class="t-sub">주가 영향: ${esc(ex.impact)}</div>` : ""}${d.impact?.ai_change ? `<div class="t-sub">AI 판단: ${esc(d.impact.ai_change.text)}</div>` : ""}${aiBtn}`;
    const btn = b.querySelector("#ta-ai");
    if (btn) btn.onclick = async () => {
      btn.disabled = true; btn.textContent = "AI 가 읽는 중…";
      const r = await post(kind === "news" ? "/api/news-explain" : "/api/disclosure-explain", { id: +id }).catch((er) => ({ error: er.message }));
      if (r.error) { btn.disabled = false; btn.textContent = "다시 시도"; toast({ title: "AI 설명", body: r.error, level: "warn" }); return; }
      tArticle(el, kind, id);
    };
  };
  tBind(root, "ta-tab", (k) => { root.querySelectorAll("#ta-tab button").forEach((x) => x.classList.toggle("on", x.dataset.k === k)); drawBody(k); });
  drawBody(tab);
  if (syms.length) api(`/api/t/quotes?symbols=${syms.map((c) => encodeURIComponent(c.symbol)).join(",")}`).then((q) => {
    (q.rows || []).forEach((r) => { const c = root.querySelector(`#ta-rel [data-sym="${CSS.escape(r.symbol)}"] [data-chg]`); if (c) { c.textContent = tPct(r.chg_pct); c.classList.add(tCls(r.chg_pct)); } });
  }).catch(() => {});
}

// ============================================================ 포트폴리오
const PF_MODES = [["paper", "모의투자"], ["shadow", "그림자"], ["live", "실계좌"], ["us-paper", "미국 모의"]];
async function tPortfolio(el) {
  const mode = T.pfMode || (S.pfMode && ["paper", "shadow", "live"].includes(S.pfMode) ? S.pfMode : "paper");
  el.innerHTML = `<div class="ts ts-pf"><h1 class="t-h1">포트폴리오</h1>${tSkel(4)}</div>`;
  const p = await api(`/api/t/portfolio?mode=${encodeURIComponent(mode)}`);
  const root = el.querySelector(".ts");
  if (!root?.isConnected) return;
  const pos = p.positions || [];
  const tabB = () => {
    if (T.pfTab === "hold") return pos.length ? `<div class="t-list">${pos.map((r) => tRow(`#analysis/${encodeURIComponent(r.symbol)}`,
      tName(r.symbol, r.name, `${num(r.qty)}주 · 평균 ${tPx(r.avg_price, r.symbol)}`),
      `<b class="num t-pv">${tWon(r.value)}</b><span class="num t-pc ${tCls(r.pnl)}">${tSigned(r.pnl)} (${tPct(r.pnl_pct)})</span>`)).join("")}</div>`
      : tEmpty("보유 종목이 없어요", mode === "live" ? "실계좌 연결(KIS)과 주문이 있어야 보여요" : "AI 자동 모의매매나 종목 화면의 '모의 매수'로 담을 수 있어요");
    if (T.pfTab === "alloc") {
      const al = (p.alloc || []).filter((a) => a.value > 0);
      const segs = al.map((a, i) => ({ v: a.weight, c: a.name === "현금" ? "var(--line-2)" : PALETTE[i % PALETTE.length], n: a.name }));
      let acc = 0;
      const R = 52, C = 2 * Math.PI * R;
      const ring = segs.map((s) => { const len = s.v * C; const o = `<circle r="${R}" cx="64" cy="64" fill="none" stroke="${s.c}" stroke-width="18" stroke-dasharray="${len.toFixed(2)} ${(C - len).toFixed(2)}" stroke-dashoffset="${(-acc).toFixed(2)}" transform="rotate(-90 64 64)"/>`; acc += len; return o; }).join("");
      return al.length ? `<div class="t-alloc"><svg viewBox="0 0 128 128" width="150" height="150" role="img" aria-label="자산 구성">${ring}<text x="64" y="60" text-anchor="middle" class="t-alloc-k">주식</text><text x="64" y="78" text-anchor="middle" class="t-alloc-v">${Math.round((1 - ((al.find((a) => a.name === "현금") || {}).weight || 0)) * 100)}%</text></svg>
        <div class="t-list">${al.map((a, i) => `<div class="t-li"><span class="t-co"><i class="t-sw" style="background:${segs[i].c}"></i><span class="t-co-t"><b>${esc(a.name)}</b><span>${tWon(a.value)}</span></span></span><span class="t-li-r"><b class="num">${(a.weight * 100).toFixed(1)}%</b></span></div>`).join("")}</div></div>`
        : tEmpty("자산 구성 자료가 없어요");
    }
    const tr = p.trades || [];
    const ST = { filled: "체결", partial: "일부 체결", submitted: "접수", rejected: "거부", cancelled: "취소", error: "오류", unknown: "확인 필요", blocked: "차단", simulated: "가상 체결" };
    return tr.length ? `<div class="t-list">${tr.map((t) => `<a class="t-li" href="#analysis/${encodeURIComponent(t.symbol)}">${tName(t.symbol, t.name, `${esc(t.at)} · ${esc(ST[t.status] || t.status)}`, 36)}
      <span class="t-li-r"><b class="${t.side === "buy" ? "up" : "down"}">${t.side === "buy" ? "매수" : "매도"} ${num(t.qty)}주</b><span class="num t-sub">${t.price ? tPx(t.price, t.symbol) : "-"}</span></span></a>`).join("")}</div>`
      : tEmpty("거래 내역이 없어요");
  };
  const spark = (p.spark || []).map((x) => Array.isArray(x) ? x[1] : x);
  root.innerHTML = `<div class="t-h1row"><h1 class="t-h1">포트폴리오</h1></div>
    ${tChips("tp-mode", PF_MODES, mode)}
    <section class="t-pf-hero"><span class="t-k">총 자산</span><b class="t-big num">${tWon(p.equity)}</b>
      <span class="num ${tCls(p.pnl)}">${tSigned(p.pnl)} (${tPct(p.pnl_pct)}) <em>시작·입금 ${tMoney(p.base)} 대비</em></span>
      <div class="t-pf-kv"><div><span>주식</span><b class="num">${tMoney(p.stock)}</b></div><div><span>현금</span><b class="num">${tMoney(p.cash)}</b></div><div><span>종목</span><b class="num">${pos.length}개</b></div></div>
      <div class="t-pf-ch">${spark.length > 1 ? tSpark(spark, { h: 90, w: 600 }) : ""}</div><div class="t-foot">${spark.length > 1 ? `최근 ${spark.length}일 평가금액 흐름` : "자산 흐름은 하루 이상 운용하면 나와요"} · ${esc(p.as_of || "")}</div></section>
    ${tTabs("tp-tab", [["hold", "보유종목"], ["alloc", "자산 구성"], ["trades", "거래내역"]], T.pfTab, "t-tabs-line")}
    <div class="t-card t-sec" id="tp-body">${tabB()}</div>
    <div class="t-pro"><a class="t-btn ghost" href="#pos/full">위험 · 업종 집중 · 세금 · 계좌 (전문가용)</a><a class="t-btn ghost" href="#goal">내 목표</a><a class="t-btn ghost" href="#budget">투자 한도</a></div>`;
  tBind(root, "tp-mode", (k) => { T.pfMode = k; tPortfolio(el); });
  tBind(root, "tp-tab", (k) => { T.pfTab = k; root.querySelectorAll("#tp-tab button").forEach((b) => b.classList.toggle("on", b.dataset.k === k)); root.querySelector("#tp-body").innerHTML = tabB(); });
}

// ============================================================ 시장
async function tMarket(el) {
  el.innerHTML = `<div class="ts ts-mkt"><h1 class="t-h1">시장</h1>${tSkel(5)}</div>`;
  const m = await api("/api/t/market");
  const root = el.querySelector(".ts");
  if (!root?.isConnected) return;
  const idx = Array.isArray(m.indices) ? m.indices : [];
  const proxy = idx.find((x) => x.proxy_note);
  const draw = () => {
    const mv = (m.movers || {})[T.mvTab] || { up: [], down: [], turnover: [] };
    const list = (xs) => xs.length ? `<div class="t-list">${xs.map((r, i) => tRow(`#analysis/${encodeURIComponent(r.symbol)}`, `<span class="t-rank">${i + 1}</span>${tName(r.symbol, r.name, esc(r.symbol), 36)}`, tPrice(r.last, r.chg_pct, r.symbol))).join("")}</div>` : tEmpty("자료가 없어요", T.mvTab === "us" ? "미국 일봉은 네트워크로 받아요" : "");
    root.innerHTML = `<h1 class="t-h1">시장</h1>
      <div class="t-mks big">${(m.markets || []).map((x) => `<div class="t-mkc ${x.open ? "open" : ""}"><span class="t-k">${esc(x.name)} 증시</span><b>${x.open ? '<i class="live-dot"></i>' : ""}${esc(x.state)}</b>
        <span class="t-sub">${x.holiday ? `${esc(x.holiday)} · ` : ""}${x.next_kst ? `${x.next === "폐장" ? "마감" : "개장"} ${esc(String(x.next_kst).replace(" KST", ""))} (한국시간)` : ""}</span></div>`).join("")}</div>
      ${tSec("지수", idx.length ? `<div class="t-idx">${idx.map((x) => x.missing ? `<div class="t-idx-c miss"><div class="t-idx-n">${esc(x.name)}</div><div class="t-idx-v dim">-</div><div class="t-idx-d">${esc(x.why)}</div></div>` : `<div class="t-idx-c"><div class="t-idx-n">${esc(x.name)}${x.proxy ? '<span class="t-tag">대용</span>' : ""}</div>
        <div class="t-idx-v num">${num(x.last, 2)}</div><div class="num t-idx-c2 ${tCls(x.chg_pct)}">${tPct(x.chg_pct)}</div>${tSpark(x.spark, { h: 44 })}<div class="t-idx-d">${esc(x.as_of)}${x.source ? ` · ${esc(x.source)}` : " · 60일"}</div></div>`).join("")}</div>
        ${proxy ? `<div class="t-foot">대용: ${esc(proxy.proxy_note)}</div>` : ""}` : tErr(m.indices))}
      ${tTabs("tm-mk", [["kr", "국내"], ["us", "해외"]], T.mvTab, "t-tabs-line")}
      <div class="t-grid3">${tSec("많이 오른", list(mv.up), "", "t-card")}${tSec("많이 내린", list(mv.down), "", "t-card")}${tSec("거래대금 많은", list(mv.turnover), "", "t-card")}</div>
      <div class="t-foot">${mv.date ? `${esc(mv.date)} 일봉 · ` : ""}거래대금 상위 ${mv.n || 0}종목 안에서 (거래가 적은 종목의 큰 등락은 뺌)</div>
      ${Array.isArray(m.events) && m.events.length ? tSec("시장 일정", `<div class="t-list">${m.events.map(tEventRow).join("")}</div>`, '<a href="#calendar">전체</a>', "t-card") : ""}
      <div class="t-pro"><a class="t-btn ghost" href="#map">증시 지도</a><a class="t-btn ghost" href="#market/full">경제지표 · 시장 분위기 (전문가용)</a><a class="t-btn ghost" href="#graph">업종 · 종목 관계도</a></div>`;
    tBind(root, "tm-mk", (k) => { T.mvTab = k; draw(); });
  };
  draw();
}

// ============================================================ AI 리포트
async function tReportInto(box, sym, compact = false) {
  let v, r;
  try { [v, r] = await Promise.all([api(`/api/verdict?symbol=${encodeURIComponent(sym)}`), api(`/api/t/report?symbol=${encodeURIComponent(sym)}`)]); } catch (e) { box.innerHTML = tErr(e); return; }
  if (!box.isConnected) return;
  if (!v.final) { box.innerHTML = tEmpty("아직 AI 판단이 없어요", v.why_none || "관심종목에 담으면 다음 아침 판단부터 생겨요"); return; }
  const delta = r.delta;
  const banned = v.trust?.key === "banned";
  const hist = r.history || [];
  const W = 600, H = 120, x = (i) => hist.length > 1 ? (i / (hist.length - 1)) * W : W / 2, y = (p) => H - 8 - (p - 0.3) / 0.4 * (H - 16);
  const histSvg = hist.length > 1 ? `<svg class="t-hist" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" width="100%" height="${H}" role="img" aria-label="최근 판단 흐름">
      <line x1="0" x2="${W}" y1="${y(0.5)}" y2="${y(0.5)}" stroke="var(--line-2)" stroke-dasharray="4 4" vector-effect="non-scaling-stroke"/>
      <path d="${hist.map((h, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(Math.max(0.3, Math.min(0.7, h.prob_up))).toFixed(1)}`).join("")}" fill="none" stroke="var(--accent)" stroke-width="2" vector-effect="non-scaling-stroke"/>
      ${hist.map((h, i) => `<circle cx="${x(i).toFixed(1)}" cy="${y(Math.max(0.3, Math.min(0.7, h.prob_up))).toFixed(1)}" r="3" class="hd-${esc(h.action)}"><title>${esc(h.date)} ${esc(koAct(h.action))} ${Math.round(h.prob_up * 100)}%</title></circle>`).join("")}</svg>
      <div class="t-hist-l"><span>${esc(hist[0].date)}</span><span>점선 = 50% (반반)</span><span>${esc(hist[hist.length - 1].date)}</span></div>` : "";
  const li = (xs, emptyTxt) => xs.length ? `<ol class="t-ol big">${xs.slice(0, 4).map((t, i) => `<li><span class="t-num">${i + 1}</span>${esc(koText(t))}</li>`).join("")}</ol>` : `<div class="t-sub">${emptyTxt}</div>`;
  const u = v.used || {};
  box.innerHTML = `
    <section class="t-verdict fin-${esc(v.final)}">
      <span class="t-k">AI 최종 판단 · ${v.horizon}거래일 뒤 기준</span>
      <div class="t-verdict-big"><b class="vd-act">${esc(koAct(v.final))}</b>${v.final !== "NO_TRADE" && v.prob_up != null ? `<b class="num vd-p">${Math.round(v.prob_up * 100)}%</b>` : ""}
        ${delta != null ? `<span class="num vd-d ${tCls(delta)}">직전 판단 대비 ${delta > 0 ? "+" : ""}${Math.round(delta * 100)}%p</span>` : ""}</div>
      <div class="t-sub">${esc(koText(v.headline || ""))}</div>
      <div class="t-sub">${esc(v.as_of || "")} 판단${u.sealed ? " · 기록 봉인됨 (나중에 고칠 수 없음)" : ""}</div>
      ${banned ? `<a class="t-note warn" href="#aitrust">AI 성적이 아직 기준 미달 (${esc(v.trust.label)}) — 이 판단은 참고만, 주문에는 쓰이지 않아요</a>` : v.trust ? `<a class="t-note" href="#aitrust">AI 성적 · ${esc(v.trust.label)}</a>` : ""}
    </section>
    ${tSec("주요 근거", li(v.why_buy || [], "뚜렷한 근거가 없어요"), "", "t-card")}
    ${tSec("주의할 점", li(v.why_not || [], "막는 이유가 없어요"), "", "t-card")}
    ${(v.plain || []).length ? tSec("쉽게 말하면", `<div class="t-text">${v.plain.map((p) => `<p>${esc(koText(p))}</p>`).join("")}</div>`, "", "t-card") : ""}
    ${tSec("AI 의견별", `<div class="t-list">${(v.votes || []).map((o) => `<div class="t-li"><span class="t-co-t"><b>${esc(o.label || koRole(o.role))}</b><span>${esc(koText(o.summary || ""))}</span></span>
      <span class="t-li-r">${o.prob_up == null ? `<span class="t-sub">${esc(koAct(o.view))}</span>` : tPill(o.view, o.prob_up)}<span class="t-sub">비중 ${Math.round((o.weight || 0) * 100)}%</span></span></div>`).join("")}</div>`, "", "t-card")}
    ${hist.length > 1 ? tSec("판단 흐름", histSvg, `<span>최근 ${hist.length}번</span>`, "t-card") : ""}
    ${v.plan?.stop || v.plan?.target ? tSec("가격 기준", `<div class="t-kvs">${v.plan.stop ? `<div><span class="t-k">이 아래면 틀린 판단</span><b class="num down">${tPx(v.plan.stop, sym)}</b><span class="t-sub">${tPct(v.plan.stop_pct, 1)}</span></div>` : ""}
      ${v.plan.target ? `<div><span class="t-k">기대 가격</span><b class="num up">${tPx(v.plan.target, sym)}</b><span class="t-sub">예상 ${tPct(v.expected_return, 1)}</span></div>` : ""}</div>`, "", "t-card") : ""}
    <div class="t-foot">근거 데이터 · 가격 ${esc(u.price || "-")} · 뉴스 ${u.news?.n ?? 0}건 · 공시 ${u.disclosures?.n ?? 0}건${u.macro?.n ? ` · 경제지표 ${u.macro.n}개` : ""} · ${esc(v.note || "")}</div>
    ${compact ? "" : `<div class="t-pro"><a class="t-btn primary" href="#analysis/${encodeURIComponent(sym)}">종목 화면</a><a class="t-btn ghost" href="#aitrust">AI 신뢰 센터</a><a class="t-btn ghost" href="#evidence/${esc(v.id || "")}">판단 근거 원본</a></div>`}`;
}
async function tReport(el, sym) {
  sym = sym ? decodeURIComponent(sym) : null;
  if (sym) {
    el.innerHTML = `<div class="ts ts-rep">${tBar(`<span class="t-bar-co">${stockLogo(sym, "", 24)}<b>AI 투자 분석 리포트</b></span>`)}<div id="tr-b">${tSkel(4)}</div></div>`;
    tBindBar(el.querySelector(".ts"), "#report");
    await tReportInto(el.querySelector("#tr-b"), sym);
    return;
  }
  el.innerHTML = `<div class="ts ts-rep"><h1 class="t-h1">AI 분석</h1>${tSkel(5)}</div>`;
  const [w, tr] = await Promise.all([api("/api/watchlist"), api("/api/t/home").catch(() => ({}))]);
  const root = el.querySelector(".ts");
  if (!root?.isConnected) return;
  const t = tr.trust || {};
  const rows = (w.rows || []).filter((r) => r.ai).sort((a, b) => (b.prob_up ?? 0) - (a.prob_up ?? 0));
  root.innerHTML = `<h1 class="t-h1">AI 분석</h1>
    ${t.key ? `<a class="t-trust t-trust-${esc(t.key)} big" href="#aitrust">${lvDot({ verified: "good", checking: "warn", banned: "bad" }[t.key])}<span><b>지금 AI 를 믿어도 될까? · ${esc(t.label)}</b>${esc(t.headline || "")}<em>${esc(t.why || "")}</em></span>${TI.chev}</a>` : ""}
    ${tSec("관심 · 보유 종목 AI 판단", rows.length ? `<div class="t-list">${rows.map((r) => tRow(`#report/${encodeURIComponent(r.symbol)}`, tName(r.symbol, r.name, `${esc(r.symbol)} · ${esc(r.ai_at || "")}`), tPill(r.ai, r.prob_up), { chev: true })).join("")}</div>`
      : tEmpty("AI 판단이 있는 종목이 없어요", "관심종목을 담으면 매일 아침 AI 가 판단해요", '<button class="t-btn" data-t-search>종목 검색</button>'), "", "t-card")}
    <div class="t-pro"><a class="t-btn ghost" href="#scorecard">AI 성적표</a><a class="t-btn ghost" href="#ailab">AI 가 틀린 이유</a><a class="t-btn ghost" href="#notrade">거래 안 한 이유</a><a class="t-btn ghost" href="#chat">AI 에게 묻기</a></div>`;
  tBindBar(root);
}

// ============================================================ 알림 설정
async function tAlerts(el) {
  el.innerHTML = `<div class="ts ts-al">${tBar("알림 설정")}${tSkel(4)}</div>`;
  tBindBar(el.querySelector(".ts"), "#more");
  const a = await api("/api/t/alerts");
  const root = el.querySelector(".ts");
  if (!root?.isConnected) return;
  const sw = (id, on, attrs = "") => `<label class="t-switch"><input type="checkbox" id="${id}" ${on ? "checked" : ""} ${attrs}><i></i></label>`;
  const draw = () => {
    let body;
    if (T.alertTab === "price") {
      body = a.price.length ? `<div class="t-sub" style="margin-bottom:6px">하루에 정한 만큼(±%) 움직이면 알려 드려요</div><div class="t-list">${a.price.map((r) => `<div class="t-li">${tName(r.symbol, r.name, r.last != null ? tPx(r.last, r.symbol) : "")}
        <span class="t-li-r t-al-r"><select class="t-sel" data-sym="${esc(r.symbol)}" aria-label="움직임 크기">${[3, 5, 7, 10].map((n) => `<option value="${n}" ${Number(r.value) === n ? "selected" : ""}>±${n}%</option>`).join("")}</select>
        ${sw(`al-${esc(r.symbol)}`, r.on, `data-sym="${esc(r.symbol)}" data-rule="${esc(r.rule_id ?? "")}"`)}</span></div>`).join("")}</div>`
        : tEmpty("관심·보유 종목이 없어요", "종목을 관심종목에 담으면 가격 알림을 켤 수 있어요");
    } else {
      const xs = a[T.alertTab] || [];
      body = `<div class="t-list">${xs.map((x) => `<div class="t-li"><span class="t-co-t"><b>${esc(x.title)}</b><span>${esc(x.desc)}</span></span><span class="t-li-r">${sw(`al-k-${x.kind}`, x.on, `data-kind="${esc(x.kind)}"`)}</span></div>`).join("")}</div>`;
    }
    const ch = a.channels || {};
    root.innerHTML = `${tBar("알림 설정")}
      ${!ch.push && !ch.external ? `<a class="t-note warn" href="#settings">휴대폰(웹 푸시)·텔레그램이 아직 연결되지 않았어요 — 켜 둔 알림은 사이트 알림함(종 모양)에만 쌓여요. 설정에서 연결하기</a>` : `<div class="t-note">보내는 곳: ${[ch.push ? "휴대폰(웹 푸시)" : "", ch.external ? "텔레그램/디스코드" : ""].filter(Boolean).join(" · ")}</div>`}
      ${tTabs("ta2-tab", [["price", "가격 알림"], ["news", "뉴스 · 공시"], ["event", "일정 · AI"]], T.alertTab, "t-tabs-line")}
      <div class="t-card t-sec">${body}</div>
      <div class="t-foot">${esc(a.note || "")}</div>
      <div class="t-pro"><a class="t-btn ghost" href="#settings">조용한 시간 · 채널 · 종목별 규칙 (설정)</a></div>`;
    tBindBar(root, "#more");
    tBind(root, "ta2-tab", (k) => { T.alertTab = k; draw(); });
    root.querySelectorAll(".t-switch input[data-kind]").forEach((i) => i.onchange = async () => {
      try { await post("/api/t/alerts", { kind: i.dataset.kind, on: i.checked }); const g = [...a.news, ...a.event].find((x) => x.kind === i.dataset.kind); if (g) g.on = i.checked; toast({ title: "알림", body: `${i.checked ? "켰어요" : "껐어요"}`, ms: 1800 }); }
      catch (e) { i.checked = !i.checked; toast({ title: "알림 설정 실패", body: e.message, level: "warn" }); }
    });
    const setPrice = async (s, on) => {
      const r = a.price.find((x) => x.symbol === s);
      const val = Number(root.querySelector(`select[data-sym="${CSS.escape(s)}"]`).value);
      try {
        if (r.rule_id) { await post("/api/t/alerts", { symbol: s, rule_id: r.rule_id }); r.rule_id = null; }
        if (on) { const x = await post("/api/t/alerts", { symbol: s, price_on: true, value: val }); r.rule_id = x.rule_id; }
        r.on = on; r.value = val;
        toast({ title: r.name, body: on ? `±${val}% 움직이면 알려 드려요` : "가격 알림을 껐어요", ms: 2200 });
      } catch (e) { toast({ title: "알림 설정 실패", body: e.message, level: "warn" }); draw(); }
    };
    root.querySelectorAll(".t-switch input[data-sym]").forEach((i) => i.onchange = () => setPrice(i.dataset.sym, i.checked));
    root.querySelectorAll("select[data-sym]").forEach((s) => s.onchange = () => { const r = a.price.find((x) => x.symbol === s.dataset.sym); if (r?.on) setPrice(s.dataset.sym, true); });
  };
  draw();
}

// ============================================================ 더보기
async function tMore(el) {
  const tile = (h, ic, t, d) => `<a class="t-tile" href="${h}"><span class="t-tile-ic">${ICONS[ic] || ic}</span><b>${t}</b><span>${d}</span></a>`;
  const theme = document.documentElement.dataset.theme;
  el.innerHTML = `<div class="ts ts-more"><h1 class="t-h1">더보기</h1>
    <div class="t-tiles">
      ${tile("#aitrust", "shield", "AI 신뢰 센터", "지금 AI 를 믿어도 되나")}
      ${tile("#report", "ai", "AI 분석 리포트", "종목별 판단 · 근거")}
      ${tile("#research", "research", "리서치", "과거로 시험하기 (백테스트)")}
      ${tile("#market", "market", "글로벌 시장", "지수 · 장 상태 · 많이 오른")}
      ${tile("#calendar", "calendar", "경제지표 캘린더", "실적 · FOMC · CPI D-Day")}
      ${tile("#map", "data", "증시 지도", "업종별 오늘 움직임")}
      ${tile("#goal", "target", "내 목표", "몇 년 안에 몇 %")}
      ${tile("#budget", "risk", "투자 한도", "원금 · 최대 손실")}
      ${tile("#alerts", "bell", "알림 설정", "가격 · 뉴스 · 일정")}
      ${tile("#chat", TI.news, "AI 에게 묻기", "종목 · 시장 · 서버")}
      ${tile("#datahealth", "pulse", "데이터 상태", "키 진단 · 수집 상태")}
      ${tile("#settings", "settings", "설정", "로그인 · 알림 · 화면")}
    </div>
    ${tSec("화면", `<div class="t-list">
      <div class="t-li"><span class="t-co-t"><b>화면 모드</b><span>쉬운 화면 = 토스식 / 전체 = 모든 메뉴·전문가 화면</span></span><span class="t-li-r">${tChips("tm-ui", [["easy", "쉬운"], ["pro", "전체"]], uiMode())}</span></div>
      <div class="t-li"><span class="t-co-t"><b>테마</b><span>기기 설정을 따르다가 직접 바꾸면 기억해요</span></span><span class="t-li-r">${tChips("tm-th", [["light", "라이트"], ["dark", "다크"]], theme)}</span></div></div>`, "", "t-card")}
    ${tSec("모든 기능", NAV.map(([g, items]) => `<details class="t-allnav"><summary>${esc(g)} <span>${items.length}</span></summary><div class="t-list">${items.map(([v, ic, l]) => `<a class="t-li" href="#${v}"><span class="t-co"><span class="t-ev-ic">${ICONS[ic] || ""}</span><span class="t-co-t"><b>${esc(l)}</b></span></span><span class="t-chev">${TI.chev}</span></a>`).join("")}</div></details>`).join(""), "", "t-card")}</div>`;
  tBind(el, "tm-ui", (k) => { if (k !== uiMode()) setUiMode(k); });
  tBind(el, "tm-th", (k) => { if (k !== document.documentElement.dataset.theme) $("#theme-btn").click(); });
}

// ============================================================ 화면 연결
// 이 화면을 toss.js 가 그리는가 (아니면 기존 화면 + 공통 스킨 + 화면 머리)
function tossOwns(v = S.view) {
  if (["n", "d", "report", "alerts", "more"].includes(v)) return true;
  if (typeof TV === "object" && TV[v] && S.sub !== "full") return true;  // v27: 나머지 화면도 토스식 (toss2.js) — 두 화면 모드 모두
  if (uiMode() !== "easy" || S.sub === "full") return false;
  return (v === "dashboard" && !S.homeDetail) || ["analysis", "watch", "news", "pos", "market"].includes(v);
}
// 기존 화면 위에 붙는 머리: ← 제목 (메뉴 이름을 그대로 — 어디에 와 있는지 늘 보이게)
const TL_TITLE = { goal: "내 목표", ledger: "예측 장부", evidence: "판단 근거 원본", dashboard: "홈 (자세히)", analysis: "종목 (전문가용)", market: "시장 분위기 · 경제지표", pos: "내 자산 (전문가용)" };
function tlHead() {
  const v = S.view;
  const flat = [...NAV_EASY, ...NAV.flatMap(([, it]) => it)];
  const label = TL_TITLE[v] || (flat.find((x) => x[0] === v) || [])[2] || "";
  if (!label || (v === "dashboard" && uiMode() !== "easy")) return null;
  const h = document.createElement("div");
  h.className = "tl-head";
  h.innerHTML = `<button class="t-back" aria-label="뒤로">${TI.back}</button><h1>${esc(label)}</h1>`;
  h.querySelector(".t-back").onclick = () => { if (T_NAV.length) history.back(); else location.hash = uiMode() === "easy" ? "#more" : "#dashboard"; };
  return h;
}
async function tossRender(el) {
  const v = S.view;
  const own = { n: () => tArticle(el, "news", S.param), d: () => tArticle(el, "disclosure", S.param), report: () => tReport(el, S.param), alerts: () => tAlerts(el), more: () => tMore(el) };
  if (own[v]) { await own[v](); return true; }
  if (uiMode() !== "easy" || S.sub === "full") { if (typeof TV === "object" && TV[v] && S.sub !== "full") { await TV[v](el); return true; } return false; }
  if (v === "dashboard" && !S.homeDetail) { await tHome(el); return true; }
  if (v === "analysis") { const sym = S.param ? decodeURIComponent(S.param) : S.symbol; if (sym) await tStock(el, sym); else await tFind(el); return true; }
  if (v === "watch") { await tWatch(el); return true; }
  if (v === "news") { await tFeed(el); return true; }
  if (v === "pos") { await tPortfolio(el); return true; }
  if (v === "market") { await tMarket(el); return true; }
  if (typeof TV === "object" && TV[v]) { await TV[v](el); return true; }
  return false;
}
// 쉬운 화면에서는 뉴스·공시를 누르면 작은 창 대신 전체 화면 기사로 (뒤로 가기 됨)
document.addEventListener("click", (e) => {
  if (uiMode() !== "easy") return;
  const t = e.target.closest("[data-news],[data-disc]");
  if (!t || e.target.closest("a[target=_blank]")) return;
  e.preventDefault(); e.stopImmediatePropagation();
  location.hash = t.dataset.news ? `#n/${t.dataset.news}` : `#d/${t.dataset.disc}`;
}, true);
// 앱 안 이동 기록 (뒤로 단추가 사이트 밖으로 나가지 않게): 앞으로 가면 쌓고, 뒤로 오면 뺀다
const T_NAV = [];
window.addEventListener("hashchange", (e) => {
  document.querySelector(".t-sheet-ov")?.remove();  // v27: 다른 화면으로 가면 열린 시트(주문·로고·용어 풀이)는 닫는다
  const old = (() => { try { return new URL(e.oldURL).hash || "#dashboard"; } catch { return "#dashboard"; } })();
  if (T_NAV.length && T_NAV[T_NAV.length - 1] === (location.hash || "#dashboard")) T_NAV.pop(); else T_NAV.push(old);
  if (T_NAV.length > 50) T_NAV.shift();
});
// 종목 화면: 이름이 화면 위로 지나가면 위 막대에 로고·이름을 띄운다 (토스처럼)
window.addEventListener("scroll", () => { const b = document.querySelector(".ts-stock .t-bar"); if (b) b.classList.toggle("scrolled", window.scrollY > 140); }, { passive: true });
