// v17 — '읽는' 대신 '보는' 화면: 뉴스 보드(같은 소식 한 장 · 톤 색 · 관련 종목 미니 차트 · 뉴스 이후 주가) · 증시 지도(업종별 타일)
/* global $, S, api, post, esc, card, empty, badge, num, P, R, render, toast, spark */

const EV_IC = { earnings: "📊", guidance: "🧭", contract: "📝", capital: "💰", mna: "🤝", legal: "⚖️", product: "🧪", macro: "🌐",
  analyst: "🎯", flow: "🌊", delisting: "⛔", other: "📰" };
const TONE_COLOR = { 긍정: "var(--up)", 부정: "var(--down)", 중립: "var(--dim)" };

function miniSpark(v, w = 70, h = 22) {
  const a = (v || []).filter((x) => x != null);
  if (a.length < 2) return "";
  const mn = Math.min(...a), mx = Math.max(...a), r = mx - mn || 1;
  const pts = a.map((x, i) => `${(i / (a.length - 1) * w).toFixed(1)},${(h - (x - mn) / r * h).toFixed(1)}`).join(" ");
  const col = a[a.length - 1] >= a[0] ? "var(--up)" : "var(--down)";
  return `<svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" class="msp"><polyline fill="none" stroke="${col}" stroke-width="1.6" points="${pts}"/></svg>`;
}

// ------------------------------------------------------------ 뉴스 보드
async function viewNewsBoard(el) {
  S.nb = S.nb || { days: 3, only: "" };
  const d = await api(`/api/news-board?days=${S.nb.days}&only=${encodeURIComponent(S.nb.only)}`);
  const c = d.counts || {};
  const tot = (c["긍정"] || 0) + (c["중립"] || 0) + (c["부정"] || 0) || 1;
  const tabs = (id, list, cur) => `<div class="tabs" id="${id}">${list.map(([k, v]) => `<button data-k="${k}" class="${String(cur) === String(k) ? "on" : ""}">${v}</button>`).join("")}</div>`;
  const cardHtml = (x) => `<div class="nb-card" style="--tone:${TONE_COLOR[x.tone]}">
    <div class="nb-top"><span class="nb-ev" title="${esc(x.event_ko)}">${EV_IC[x.event] || "📰"} ${esc(x.event_ko)}</span>
      <span class="nb-tone" style="color:${TONE_COLOR[x.tone]}">● ${esc(x.tone)}${x.confidence != null ? ` <span class="xs dim">확신 ${Math.round(x.confidence * 100)}%</span>` : ""}</span>
      ${x.rumor ? '<span class="chip xs warn">루머·관측</span>' : ""}${x.n > 1 ? `<span class="chip xs">같은 소식 ${x.n}건 · 매체 ${x.sources.length}곳</span>` : ""}
      <span class="xs dim nb-time">${esc(x.first)}</span></div>
    <a class="nb-title" href="${esc(x.articles[0]?.url || "#")}" target="_blank" rel="noopener noreferrer">${esc(x.title)}</a>
    ${x.summary ? `<div class="nb-sum">🤖 ${esc(x.summary)}</div>` : ""}
    ${x.symbols.length ? `<div class="nb-syms">${x.symbols.map((s) => `<a class="nb-sym" href="#analysis/${esc(s.symbol)}"><b>${esc(s.name)}</b> ${miniSpark(s.spark)}
      <span class="num ${s.since >= 0 ? "up" : "down"}">${s.since == null ? "" : `뉴스 후 ${P(s.since, 1)}`}</span>${s.ai ? ` ${badge(s.ai)}` : ""}</a>`).join("")}</div>` : ""}
    <details class="nb-more"><summary class="xs muted">출처 ${x.sources.map(esc).join(" · ") || "-"} · 신뢰도 ${Math.round(x.trust * 100)}%${x.why.length ? " · 왜 이 톤?" : ""}</summary>
      ${x.why.length ? `<div class="xs">근거 단어: ${x.why.map((w) => `<span class="chip xs ${w.includes("(부정)") ? "warn" : ""}">${esc(w)}</span>`).join(" ")} <span class="dim">${x.by === "llm" ? "· AI 구조화" : "· 규칙(키워드+부정어)"}</span></div>` : ""}
      ${x.articles.map((a) => `<div class="xs"><a href="${esc(a.url || "#")}" target="_blank" rel="noopener noreferrer">${esc(a.title)}</a> <span class="dim">${esc(a.source || "")} · ${esc(a.at)}</span></div>`).join("")}</details></div>`;
  el.innerHTML = `
  <div class="card"><div class="card-h"><h3>뉴스 보드 <span class="small dim">같은 소식은 한 장 · 색 = 톤 · 관련 종목은 뉴스 이후 주가와 함께</span></h3>
    <div class="right"><button class="btn-sm" id="nb-ex">지금 분석</button> <a class="btn-sm" href="#newslist">원문 목록</a></div></div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">${tabs("nb-days", [[1, "오늘"], [3, "3일"], [7, "7일"]], S.nb.days)}
      ${tabs("nb-only", [["", "전체"], ["mine", "보유·관심"], ["긍정", "긍정"], ["부정", "부정"]], S.nb.only)}</div>
    <div class="nb-bar" title="긍정 · 중립 · 부정"><i style="flex:${c["긍정"] || 0};background:var(--up)"></i><i style="flex:${c["중립"] || 0};background:var(--dim)"></i><i style="flex:${c["부정"] || 0};background:var(--down)"></i></div>
    <div class="xs muted">긍정 ${c["긍정"] || 0} (${Math.round((c["긍정"] || 0) / tot * 100)}%) · 중립 ${c["중립"] || 0} · 부정 ${c["부정"] || 0} · 기사 ${num(d.n_articles)}건 ·
      ${d.extract?.llm_on ? `AI 구조화 켜짐 (${esc(d.extract.at || "-")})` : "AI 구조화 꺼짐 — 키워드+부정어 규칙 (LLM 키를 넣으면 기사마다 이벤트·방향·확신도·한 줄 요약)"}</div></div>
  <div class="nb-grid">${(d.cards || []).map(cardHtml).join("") || card("", empty("이 기간 뉴스가 없습니다 — 뉴스 수집이 돌면 채워집니다"))}</div>
  <div class="xs dim">${esc(d.note)}</div>`;
  el.querySelectorAll("#nb-days button").forEach((b) => b.onclick = () => { S.nb.days = +b.dataset.k; render(); });
  el.querySelectorAll("#nb-only button").forEach((b) => b.onclick = () => { S.nb.only = b.dataset.k; render(); });
  $("#nb-ex").onclick = async (e) => {
    e.target.disabled = true; e.target.textContent = "분석 중…";
    const r = await post("/api/news-extract", {});
    toast({ title: "뉴스 분석", body: r.error || `규칙 ${r.rule}건 · AI ${r.llm}건 · 묶음 ${r.clustered}건`, level: r.error ? "warn" : "good" });
    render();
  };
}

// ------------------------------------------------------------ 증시 지도
function heatColor(chg) {
  const c = Math.max(-0.05, Math.min(0.05, chg || 0)) / 0.05;  // ±5% 에서 최대
  const a = (0.18 + Math.abs(c) * 0.72).toFixed(2);
  return c >= 0 ? `rgba(240,71,79,${a})` : `rgba(59,140,255,${a})`;  // 한국식: 상승 빨강 · 하락 파랑
}

async function viewMarketMap(el) {
  const m = await api("/api/market-map");
  if (!m.tiles?.length) { el.innerHTML = card("증시 지도", empty(m.message || "데이터 없음")); return; }
  const bySec = {};
  m.tiles.forEach((t) => (bySec[t.sector] = bySec[t.sector] || []).push(t));
  const secs = m.sectors.filter((s) => bySec[s.sector]);
  const b = m.breadth;
  const tile = (t) => `<a class="mm-t" href="#analysis/${esc(t.symbol)}" style="flex-grow:${Math.max(1, Math.round(Math.sqrt(t.weight) * 300))};background:${heatColor(t.chg)}" title="${esc(t.name)} ${P(t.chg, 2)} · 5일 ${P(t.chg5, 1)}">
    <b>${esc(t.name)}</b><span class="num">${P(t.chg, 1)}</span></a>`;
  const mover = (t) => `<a class="mv-it" href="#analysis/${esc(t.symbol)}"><b>${esc(t.name)}</b> <span class="num ${t.chg >= 0 ? "up" : "down"}">${P(t.chg, 1)}</span>
    ${t.news ? `<div class="xs muted">📰 ${esc(t.news.title)} <span class="dim">${esc(t.news.at)}</span></div>` : '<div class="xs dim">관련 뉴스 없음</div>'}</a>`;
  el.innerHTML = `
  <div class="card"><div class="card-h"><h3>증시 지도 <span class="small dim">${esc(m.date)} 마감 기준 · 타일 크기 = 거래대금 · 색 = 등락</span></h3></div>
    <div class="mm-head">${m.index ? `<div><div class="xs muted">${esc(m.index.name)}</div><div class="big num">${num(m.index.last, 2)}</div><div class="num ${m.index.chg >= 0 ? "up" : "down"}">${P(m.index.chg, 2)} <span class="xs dim">20일 ${P(m.index.chg20, 1)}</span></div></div><div>${miniSpark(m.index.spark, 160, 44)}</div>` : ""}
      <div><div class="xs muted">시장 분위기</div><div class="b">${esc(b.mood)}</div><div class="xs">상승 <span class="up">${b.up}</span> · 하락 <span class="down">${b.down}</span> · 보합 ${b.flat}</div></div>
      <div><div class="xs muted">20일선 위 종목</div><div class="b">${R(b.above20, 0)}</div><div class="hbar" style="width:120px;margin:4px 0"><i style="width:${b.above20 * 100}%"></i></div></div></div>
    <div class="mm-legend xs"><span style="background:${heatColor(-0.05)}"></span>−5% <span style="background:${heatColor(-0.01)}"></span> <span style="background:${heatColor(0.01)}"></span> <span style="background:${heatColor(0.05)}"></span>+5%</div>
    <div class="mm-map">${secs.map((s) => `<div class="mm-sec" style="flex-grow:${Math.max(1, Math.round(s.weight * 100))}"><div class="mm-sh"><b>${esc(s.sector)}</b> <span class="num ${s.chg >= 0 ? "up" : "down"}">${P(s.chg, 1)}</span></div>
      <div class="mm-tiles">${bySec[s.sector].slice(0, 30).map(tile).join("")}</div></div>`).join("")}</div></div>
  <div class="grid g-2">${card("많이 오른 종목 <span class='small dim'>가장 가까운 뉴스 = '왜' 후보</span>", m.gainers.map(mover).join(""))}${card("많이 내린 종목", m.losers.map(mover).join(""))}</div>
  <div class="xs dim">${esc(m.note)}</div>`;
}

// ------------------------------------------------------------ 실적 이벤트 전략 (PEAD) — 전진 기록
async function viewPead(el) {
  const r = await api("/api/pead");
  const f = r.forward || {}, b = r.backfill || {};
  const st = (x, lbl, cls = "") => `<div class="kpi ${cls}"><div class="xs muted">${lbl}</div><div class="big num">${x.n || 0}건</div>
    <div class="xs">평균 초과수익(방향 반영) <b class="${(x.mean_signed_excess || 0) >= 0 ? "up" : "down"}">${P(x.mean_signed_excess, 2)}</b> · 적중 ${R(x.hit, 0)} · t ${x.t ?? "-"}</div></div>`;
  const prog = Math.min(1, (f.n || 0) / 30);
  const row = (x) => `<tr><td><a href="#analysis/${esc(x.symbol)}"><b>${esc(x.symbol)}</b></a></td><td class="small">${esc(x.date)}</td>
    <td class="r num ${x.surprise >= 0 ? "up" : "down"}">${x.surprise >= 0 ? "+" : ""}${x.surprise}%</td><td>${x.direction > 0 ? "▲ 사기" : "▼ 피하기"}</td>
    <td class="r num">${P(x.ret, 1)}</td><td class="r num dim">${P(x.bench, 1)}</td><td class="r num ${x.signed >= 0 ? "up" : "down"}"><b>${P(x.signed, 1)}</b></td>
    <td>${x.forward ? '<span class="chip xs good">전진</span>' : '<span class="chip xs">사후</span>'}${x.intact ? "" : ' <span class="chip xs warn">봉인 깨짐</span>'}</td></tr>`;
  el.innerHTML = `
  <div class="card"><div class="card-h"><h3>실적 이벤트 전략 <span class="small dim">PEAD · 결과 나오기 전에 봉인한 신호만 센다</span></h3><div class="right xs dim">${esc(r.as_of)}</div></div>
    <div class="lesson small"><b>판정: ${esc(r.decision)}</b><div class="xs muted" style="margin-top:4px">규칙(고정, ${esc(r.version)}): ${esc(r.rule)}</div></div>
    <div class="xs muted" style="margin:8px 0 4px">전진 기록 진행 ${f.n || 0} / 30건</div><div class="hbar"><i style="width:${prog * 100}%"></i></div>
    <div class="grid g-2" style="margin-top:10px">${st(f, "전진 기록 (판정에 쓰는 것)", "hi")}${st(b, "사후 채움 (참고만 — 가설을 만든 데이터)")}</div>
    <div class="xs dim" style="margin-top:6px">이벤트 ${r.n_events}건 · 신호 ${r.n_signals}건${r.tampered ? ` · <span class="warn-t">봉인 불일치 ${r.tampered}건</span>` : ""} · ${esc(r.note)}</div></div>
  ${card("최근 채점된 신호 <span class='small dim'>진입 = 발표 다음 거래일 종가 · 20거래일 뒤 · 성과 = 종목 − 지수</span>", (r.recent || []).length ? `<div class="scroll"><table class="tight"><thead><tr><th>종목</th><th>발표일</th><th class="r">서프라이즈</th><th>신호</th><th class="r">종목</th><th class="r">지수</th><th class="r">방향×초과</th><th></th></tr></thead><tbody>${r.recent.map(row).join("")}</tbody></table></div>` : empty("아직 20거래일이 지난 신호가 없습니다 — 실적 데이터가 쌓이면 자동으로 채점됩니다"))}
  ${card("결과 대기 중", (r.pending || []).length ? `<ul class="plain small">${r.pending.map((x) => `<li><a href="#analysis/${esc(x.symbol)}"><b>${esc(x.symbol)}</b></a> ${esc(x.date)} · 서프라이즈 ${x.surprise >= 0 ? "+" : ""}${x.surprise}% · ${x.direction > 0 ? "▲" : "▼"} ${x.forward ? '<span class="chip xs good">전진</span>' : '<span class="chip xs">사후</span>'}</li>`).join("")}</ul>` : empty("대기 중인 신호 없음"))}`;
}

// ------------------------------------------------------------ 미국 주식 주문표 (수동 · 환전 · 세금)
async function viewUSOrder(el) {
  S.us = S.us || { holdings: "", targets: "", avg: "", cash_usd: "", cash_krw: "", ytd: "" };
  const u = S.us;
  el.innerHTML = `
  <div class="card"><div class="card-h"><h3>미국 주식 주문표 <span class="small dim">증권사 앱에서 그대로 따라 넣는 표 · 환전·수수료·양도세 추정 포함</span></h3></div>
    <div class="lesson small">KIS 해외 주문 API는 아직 연결하지 않았습니다 (모의 검증 전 실주문 경로를 열지 않음). 이 표를 보고 직접 주문하세요 — <b>지정가 권장</b>.</div>
    <div class="grid g-3" style="margin-top:10px">
      <label class="small">보유 (한 줄에 <code>종목,수량</code>)<textarea id="us-h" rows="5" placeholder="AAPL,10&#10;MSFT,3">${esc(u.holdings)}</textarea></label>
      <label class="small">목표 비중 (비우면 시스템 미국 가상 장부를 따라감)<textarea id="us-t" rows="5" placeholder="AAPL,0.3&#10;MSFT,0.3&#10;NVDA,0.4">${esc(u.targets)}</textarea></label>
      <label class="small">평균 단가 USD (매도 이익·세금 계산용)<textarea id="us-a" rows="5" placeholder="AAPL,150">${esc(u.avg)}</textarea></label></div>
    <div class="grid g-3">
      <label class="small">달러 현금 (USD)<input id="us-cu" inputmode="decimal" value="${esc(u.cash_usd)}" placeholder="0"></label>
      <label class="small">원화 현금 (환전할 돈, 원)<input id="us-ck" inputmode="decimal" value="${esc(u.cash_krw)}" placeholder="0"></label>
      <label class="small">올해 이미 실현한 해외주식 이익 (원)<input id="us-y" inputmode="decimal" value="${esc(u.ytd)}" placeholder="0"></label></div>
    <div style="margin-top:8px"><button class="btn" id="us-go">주문표 만들기</button></div></div>
  <div id="us-out"></div>`;
  const pairs = (s) => Object.fromEntries(s.split(/\n/).map((x) => x.split(",").map((y) => y.trim())).filter((x) => x.length === 2 && x[0] && x[1]));
  $("#us-go").onclick = async () => {
    Object.assign(u, { holdings: $("#us-h").value, targets: $("#us-t").value, avg: $("#us-a").value, cash_usd: $("#us-cu").value, cash_krw: $("#us-ck").value, ytd: $("#us-y").value });
    const box = $("#us-out");
    box.innerHTML = skeleton();
    let r;
    try {
      r = await post("/api/us-sheet", { holdings: pairs(u.holdings), targets: u.targets.trim() ? pairs(u.targets) : null, avg_cost: u.avg.trim() ? pairs(u.avg) : null,
        cash_usd: u.cash_usd, cash_krw: u.cash_krw, ytd_gain_krw: u.ytd });
    } catch (e) { r = { error: e.message }; }
    if (r.error) { box.innerHTML = card("", `<div class="veto">${esc(r.error)}</div>`); return; }
    const s = r.summary, t = s.tax;
    const rows = r.rows.map((x) => `<tr class="${x.order_qty > 0 ? "" : x.order_qty < 0 ? "sell-row" : "dim"}"><td><b>${esc(x.symbol)}</b></td>
      <td><b class="${x.order_qty > 0 ? "up" : x.order_qty < 0 ? "down" : ""}">${x.side}</b></td><td class="r num"><b>${Math.abs(x.order_qty)}</b>주</td>
      <td class="r num">$${num(x.price, 2)}</td><td class="r num">$${num(x.amount_usd, 2)}</td><td class="r num dim">$${num(x.commission_usd, 2)}</td>
      <td class="r num">${x.held} → ${x.target_qty}</td><td class="r num">${R(x.target_weight, 1)}</td>
      <td class="r num">${x.gain_krw != null ? `<span class="${x.gain_krw >= 0 ? "up" : "down"}">${num(x.gain_krw)}원</span>` : ""}</td></tr>`).join("");
    box.innerHTML = `
    <div class="grid g-4">
      <div class="kpi"><div class="xs muted">팔 금액</div><div class="big num">$${num(s.sell_usd, 2)}</div></div>
      <div class="kpi"><div class="xs muted">살 금액 (수수료 포함)</div><div class="big num">$${num(s.buy_usd, 2)}</div></div>
      <div class="kpi"><div class="xs muted">새로 환전할 달러</div><div class="big num">$${num(s.need_usd, 2)}</div><div class="xs">≈ ${num(s.need_krw)}원 · 환전 비용 ≈ ${num(s.fx_cost_krw)}원</div></div>
      <div class="kpi ${t && t.tax_krw > 0 ? "warn" : ""}"><div class="xs muted">양도세 추정</div><div class="big num">${t ? `${num(t.tax_krw)}원` : "-"}</div>
        <div class="xs">${t ? `실현 이익 ${num(t.gain_krw)}원 · 남은 공제 ${num(t.deduction_left)}원` : "매도 이익 없음 (평단을 넣으면 계산)"}</div></div></div>
    ${card(`주문 순서 <span class="small dim">매도 먼저 → 매수 · 환율 ${num(r.fx, 1)}원 (${esc(r.fx_source)}) · 목표 = ${esc(r.source)}</span>`,
      `<div class="scroll"><table class="tight"><thead><tr><th>종목</th><th>구분</th><th class="r">수량</th><th class="r">참고가</th><th class="r">금액</th><th class="r">수수료</th><th class="r">보유→목표</th><th class="r">목표 비중</th><th class="r">실현 이익</th></tr></thead><tbody>${rows}</tbody></table></div>
      ${r.missing.length ? `<div class="xs warn-t">가격 없음(제외): ${r.missing.map(esc).join(", ")}</div>` : ""}`,
      `<button class="btn-sm" id="us-csv">CSV 받기</button>`)}
    <div class="xs dim">${esc(r.note)}</div>`;
    $("#us-csv").onclick = () => {
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob(["﻿" + r.csv], { type: "text/csv" }));
      a.download = `us-orders-${new Date().toISOString().slice(0, 10)}.csv`;
      a.click();
    };
  };
}

// ------------------------------------------------------------ 그날 재현 (날짜 선택)
async function viewReplay(el) {
  const d0 = S.param && /^\d{4}-\d{2}-\d{2}$/.test(S.param) ? S.param : (S.replayDate || "");
  const r = await api(`/api/replay?date=${encodeURIComponent(d0)}`);
  S.replayDate = r.date;
  const mv = (x) => `<a class="mv-it" href="#analysis/${esc(x.symbol)}"><b>${esc(x.name)}</b> <span class="num ${x.chg >= 0 ? "up" : "down"}">${P(x.chg, 1)}</span></a>`;
  const b = r.breadth;
  const aiRow = (a) => `<tr><td><a href="#analysis/${esc(a.symbol)}"><b>${esc(a.name)}</b></a></td><td>${badge(a.action)}</td><td class="r num">${R(a.prob_up, 0)}</td><td class="r num">${a.confidence}</td>
    <td class="r num">${a.later ? `<span class="${a.later.ret >= 0 ? "up" : "down"}">${P(a.later.ret, 1)}</span> ${a.later.correct ? "✅" : "❌"}` : '<span class="dim">미확정</span>'}</td></tr>`;
  el.innerHTML = `
  <div class="card"><div class="card-h"><h3>그날 재현 <span class="small dim">${esc(r.note)}</span></h3>
    <div class="right"><button class="btn-sm" id="rp-prev">◀ ${esc(r.prev)}</button> <input type="date" id="rp-d" value="${esc(r.date)}"> <button class="btn-sm" id="rp-next">${esc(r.next)} ▶</button></div></div>
    <div class="mm-head">
      <div><div class="xs muted">${esc(r.date)} (${esc(r.weekday)})</div><div class="b">${r.trading ? "거래일" : `휴장${r.holiday ? ` · ${esc(r.holiday)}` : ""}`}</div></div>
      ${r.index ? `<div><div class="xs muted">KOSPI</div><div class="big num">${num(r.index.close, 2)}</div><div class="num ${r.index.chg >= 0 ? "up" : "down"}">${P(r.index.chg, 2)}</div></div>` : ""}
      ${b.n ? `<div><div class="xs muted">시장 분위기</div><div class="xs">상승 <span class="up">${b.up}</span> · 하락 <span class="down">${b.down}</span> / ${b.n}종목</div>
        <div class="nb-bar" style="width:160px"><i style="flex:${b.up};background:var(--up)"></i><i style="flex:${b.n - b.up - b.down};background:var(--dim)"></i><i style="flex:${b.down};background:var(--down)"></i></div></div>` : ""}
      ${r.book ? `<div><div class="xs muted">가상 장부 (${esc(r.book.at)})</div><div class="b num">${num(r.book.equity)}원</div><div class="xs">보유 ${r.book.n}종목</div></div>` : ""}</div></div>
  ${r.trading ? `<div class="grid g-2">${card("그날 많이 오른 종목", r.gainers.map(mv).join("") || empty())}${card("그날 많이 내린 종목", r.losers.map(mv).join("") || empty())}</div>` : ""}
  <div class="grid g-2">
    ${card(`그날 AI 판단 <span class="small dim">${r.ai_n}건${r.ai_hit_later != null ? ` · 나중에 맞은 비율 ${R(r.ai_hit_later, 0)}` : ""}</span>`, r.ai.length ? `<div class="scroll" style="max-height:420px"><table class="tight"><thead><tr><th>종목</th><th>판단</th><th class="r">P(상승)</th><th class="r">신뢰</th><th class="r">나중 결과</th></tr></thead><tbody>${r.ai.map(aiRow).join("")}</tbody></table></div>` : empty("그날 AI 판단 기록 없음"))}
    ${card(`그날 뉴스 <span class="small dim">${r.news.length}건</span>`, r.news.length ? r.news.map((n) => `<div class="nw-it"><a href="${esc(n.url || "#")}" target="_blank" rel="noopener noreferrer"><span style="color:${TONE_COLOR[n.sent > 0.2 ? "긍정" : n.sent < -0.2 ? "부정" : "중립"]}">●</span> ${esc(n.title)}</a>
      <div class="xs dim">${esc(n.source || "")} · ${esc(n.at)}${n.symbols.length ? ` · ${n.symbols.map(esc).join(", ")}` : ""}</div>${n.summary ? `<div class="xs muted">🤖 ${esc(n.summary)}</div>` : ""}</div>`).join("") : empty("그날 저장된 뉴스 없음"))}</div>
  <div class="grid g-2">${card("그날 일정", r.events.length ? `<ul class="plain small">${r.events.map((e) => `<li>${CAL_ICON?.[e.kind] || "•"} ${esc(e.title)}</li>`).join("")}</ul>` : empty("일정 없음"))}
    ${card("그날 알림", r.alerts.length ? `<ul class="plain small">${r.alerts.map((a) => `<li><span class="xs dim">${esc(a.at)}</span> ${esc(a.title)}</li>`).join("")}</ul>` : empty("알림 없음"))}</div>`;
  const go = (d) => { S.replayDate = d; location.hash = `#replay/${d}`; };
  $("#rp-prev").onclick = () => go(r.prev);
  $("#rp-next").onclick = () => go(r.next);
  $("#rp-d").onchange = (e) => e.target.value && go(e.target.value);
}

// ------------------------------------------------------------ 이번 주 보유·관심 종목 일정 (홈·일정 화면 카드)
async function weeklyCard(root, anchorSel) {
  if (!root) return;
  let w;
  try { w = await api("/api/weekly"); } catch { return; }
  const box = document.createElement("div");
  box.innerHTML = card(`이번 주 내 종목 일정 <span class="small dim">보유·관심 · 7일 · ${esc(w.as_of)}</span>`,
    (w.rows || []).length ? `<div class="dday">${w.rows.slice(0, 14).map((e) => `<a class="dd ${e.d_day <= 1 ? "hot" : ""}" href="${e.symbol ? `#analysis/${esc(e.symbol)}` : "#calendar"}"><b>${esc(e.d_label)}</b> ${CAL_ICON?.[e.kind] || "•"} ${esc(e.title)}${e.estimated ? ' <span class="xs dim">추정</span>' : ""}</a>`).join("")}</div>`
      : empty("이번 주 보유·관심 종목 일정 없음 (관심종목을 ★ 하면 여기 모입니다)"));
  const anchor = anchorSel && root.querySelector(anchorSel);
  if (anchor) anchor.after(box); else root.append(box);
}
