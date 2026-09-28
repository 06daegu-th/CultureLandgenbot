/* Quant AI — 신뢰 계층 화면: Net Alpha · 안전 센터(자동 킬스위치) · 실험·승격 · 서버·DB · 채팅 AI · 시작 체크리스트
   app.js 의 공용 헬퍼(esc, card, empty, pct, num, api …)를 호출 시점에 사용한다. */
"use strict";

Object.assign(ICONS, {
  alpha: _s('<path d="M4 19 9 9l4 6 3-4 4 8"/><path d="M4 5h16"/><circle cx="9" cy="9" r="1.2"/>'),
  shield: _s('<path d="M12 3 4 6v6c0 4.5 3.4 8 8 9 4.6-1 8-4.5 8-9V6Z"/><path d="m8.5 12 2.5 2.5 4.5-5"/>'),
  lab: _s('<path d="M9 3h6M10 3v5l-5 9a2 2 0 0 0 1.7 3h10.6a2 2 0 0 0 1.7-3l-5-9V3"/><path d="M8 14h8"/>'),
  server: _s('<rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01M11 7.5h6M11 16.5h6"/>'),
  chat: `<svg viewBox="0 0 24 24" width="22" height="22" fill="none"><path d="M12 2.5l1.6 4.2 4.2 1.6-4.2 1.6L12 14.1l-1.6-4.2-4.2-1.6 4.2-1.6Z" fill="currentColor"/><path d="M18.5 13l.9 2.3 2.3.9-2.3.9-.9 2.3-.9-2.3-2.3-.9 2.3-.9Z" fill="currentColor" opacity=".8"/><path d="M6 15.5l.7 1.8 1.8.7-1.8.7L6 20.5l-.7-1.8-1.8-.7 1.8-.7Z" fill="currentColor" opacity=".65"/></svg>`,
  send: _s('<path d="M4 12 20 4l-6 16-3-7Z"/><path d="m11 13 9-9"/>'),
  refresh: _s('<path d="M20 11a8 8 0 0 0-14.5-4.5L4 8"/><path d="M4 4v4h4"/><path d="M4 13a8 8 0 0 0 14.5 4.5L20 16"/><path d="M20 20v-4h-4"/>'),
});

const STEP_ST = { pass: ["✓", "ok", "통과"], fail: ["✕", "bad", "미달"], insufficient: ["…", "na", "표본 부족"] };
const G_ST = { ok: ["●", "ok", "정상"], warn: ["▲", "warn", "주의"], critical: ["■", "bad", "심각"], na: ["○", "na", "해당 없음"], unknown: ["?", "na", "확인 불가"] };
const STATE_UI = {
  TRADING: ["TRADING", "ok", "자동 매매 정상"], DEGRADED: ["DEGRADED", "warn", "주의 조건 있음 · 매매 계속"],
  BUY_BLOCKED: ["BUY BLOCKED", "warn", "수동 긴급정지 · 신규 매수만 중단"], HALTED: ["HALTED", "bad", "자동 정지 · 새 주문 없음"],
};
const pp = (v, d = 2) => v == null ? "-" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(d)}%`;
const ppp = (v, d = 2) => v == null ? "-" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(d)}%p`;
const sgn = (v) => v == null ? "flat" : v > 0 ? "up" : v < 0 ? "down" : "flat";
const post = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });

function lineSvg(values, w = 520, h = 120, zero = true) {
  const v = (values || []).filter((x) => x != null);
  if (v.length < 2) return "";
  const min = Math.min(...v, zero ? 0 : Infinity), max = Math.max(...v, zero ? 0 : -Infinity), rng = max - min || 1;
  const y = (x) => h - 6 - ((x - min) / rng) * (h - 12);
  const pts = v.map((x, i) => [(i / (v.length - 1)) * w, y(x)]);
  const col = v[v.length - 1] >= 0 ? css("--up") : css("--down");
  const line = pts.map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join("");
  const id = "a" + Math.random().toString(36).slice(2, 8);
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" class="line-svg"><defs><linearGradient id="${id}" x1="0" x2="0" y1="0" y2="1"><stop offset="0" stop-color="${col}" stop-opacity=".3"/><stop offset="1" stop-color="${col}" stop-opacity="0"/></linearGradient></defs>
    ${zero ? `<line x1="0" x2="${w}" y1="${y(0).toFixed(1)}" y2="${y(0).toFixed(1)}" stroke="${css("--line-2")}" stroke-dasharray="4 4"/>` : ""}
    <path d="${line}L${w},${h}L0,${h}Z" fill="url(#${id})"/><path d="${line}" fill="none" stroke="${col}" stroke-width="2"/></svg>`;
}

// ================================================================= Net Alpha
function funnel(steps, big = false) {
  const short = { data: "시점 정확", calib: "확률 보정", ai: "AI 추가수익", cost: "비용 후", exec: "실주문 동일", repeat: "반복" };
  return `<div class="funnel ${big ? "big" : ""}">${steps.map((s, i) => {
    const [ic, cls, lab] = STEP_ST[s.status];
    return `<div class="fstep ${cls}" title="${esc(s.detail)}"><div class="fno">${i + 1}</div><div class="fbody"><div class="ft">${esc(short[s.key] || s.title)}</div>
      <div class="fv num">${esc(s.headline)}</div><div class="fs"><span class="fic">${ic}</span>${lab}</div></div></div>`;
  }).join('<div class="farrow">›</div>')}</div>`;
}

function marketTabs() {
  const m = S.alphaMarket || "KR";
  return `<div class="tabs mkt-tabs">${[["KR", "🇰🇷 국내"], ["US", "🇺🇸 미국"]].map(([k, v]) => `<button data-mk="${k}" class="${m === k ? "on" : ""}">${v}</button>`).join("")}</div>`;
}
function bindMarketTabs() {
  document.querySelectorAll(".mkt-tabs button").forEach((b) => b.onclick = () => { S.alphaMarket = b.dataset.mk; safeSet("qa_mkt", b.dataset.mk); render(); });
  const us = $("#us-start");
  if (us) us.onclick = async () => { us.disabled = true; us.textContent = "미국 일봉 받는 중…"; const r = await runAction("us_cycle", (p) => { us.textContent = p; }); us.textContent = r.error ? `실패: ${r.error}` : "완료"; setTimeout(render, 800); };
}
function usEmpty(na) {
  return `<div class="card alpha-hero us-empty"><div class="ah-left"><div class="ah-k">${ICONS.alpha}<span>PROOF CHAIN</span>${marketTabs()}</div><div class="ah-big">—</div><div class="ah-sub">미국 장부 기록 없음</div></div>
    <div class="ah-mid"><div class="small muted">미국 대형주 코어 팩터 + AI 장부 4개(실제 설정 · 코어 · 코어+거부권 · 코어+거부권+위성)를 <b>가상매매</b>로 운용해 국내와 같은 6단계로 증명합니다.
      과거 백테스트는 생존편향 때문에 쓰지 않고 오늘부터의 전진 기록만 씁니다. 24시간 운영 중이면 미국 장 마감 뒤 자동 갱신됩니다.</div>
      <button class="btn-sm primary" id="us-start" style="margin-top:12px">미국 장부 시작 (무료 일봉 받기)</button>${na?.error ? `<div class="veto" style="margin-top:8px">${esc(na.error)}</div>` : ""}</div><div></div></div>`;
}

function alphaHero(na, compact = true) {
  if (!na || na.error) return (S.alphaMarket === "US") ? usEmpty(na) : card("증명 체인", empty(na?.error || "계산 중…"));
  if (na.market === "US" && !(na.headline || {}).days) return usEmpty(na);
  const h = na.headline || {};
  const v = na.verdict || {};
  const vcls = { pass: "ok", fail: "bad", insufficient: "na" }[v.status] || "na";
  const aiCurve = (na.ai_curve || []).map((x) => x[1]);
  return `<div class="card alpha-hero">
    <div class="ah-left">
      <div class="ah-k">${ICONS.alpha}<span>PROOF CHAIN</span>${marketTabs()}</div>
      <div class="ah-big num ${sgn(h.ai_excess)}">${h.ai_excess == null ? "—" : ppp(h.ai_excess)}</div>
      <div class="ah-sub">AI 알파 = AI 장부 − 코어 장부 <span class="dim">(비용 후 · t=${h.ai_t ?? "-"} · ${h.days || 0}거래일)</span></div>
      <div class="ah-kpis">
        <div><span class="l">운용</span><b class="num ${sgn(h.book)}">${pp(h.book)}</b></div>
        <div><span class="l">${esc(na.bench_name || "KOSPI")}</span><b class="num ${sgn(h.bench)}">${pp(h.bench)}</b></div>
        <div><span class="l">벤치 초과</span><b class="num ${sgn(h.excess)}">${ppp(h.excess)}</b></div>
        <div><span class="l">연 알파</span><b class="num ${sgn(h.alpha_ann)}">${pp(h.alpha_ann, 1)}</b></div>
        <div><span class="l">정보비율</span><b class="num">${h.ir ?? "-"}</b></div>
        <div><span class="l">장부</span><b class="num">${esc((na.mode || "").toUpperCase())}${na.core_only ? " · 코어전용" : ""}</b></div>
      </div>
    </div>
    <div class="ah-mid">
      <div class="ah-q">AI 가 똑똑한가? 가 아니라 — <b>시점이 정확한 데이터로, 비용 후에도, 실제 주문에서, 반복해서 돈이 남았나?</b></div>
      ${funnel(na.steps || [])}
      <div class="verdict ${vcls}"><b>${esc(v.title || "")}</b><span>${esc(v.message || "")}</span></div>
    </div>
    <div class="ah-right">
      <div class="small muted">누적 AI 알파 (AI 장부 − 코어 장부)</div>
      <div class="ah-chart">${lineSvg(aiCurve, 360, 130) || empty("AI 장부·코어 장부가 쌓이면 표시")}</div>
      ${compact ? `<a class="link" href="#alpha">분석 전체 보기 ${ICONS.arrow}</a>` : ""}
    </div>
  </div>`;
}

function waterfall(parts) {
  if (!parts || !parts.length) return empty("운용 기록이 쌓이면 수익을 시장·팩터·AI·비용으로 나눠 보여줍니다");
  const max = Math.max(...parts.map((p) => Math.abs(p.value)), 1e-6);
  return `<div class="wf">${parts.map((p) => `<div class="wf-row ${p.key === "total" ? "tot" : ""}"><span class="wf-l">${esc(p.label)}</span>
    <div class="wf-bar"><div class="wf-mid"></div><div class="wf-fill ${p.value >= 0 ? "pos" : "neg"}" style="${p.value >= 0 ? "left:50%" : `right:50%`};width:${(Math.abs(p.value) / max) * 50}%"></div></div>
    <span class="wf-v num ${sgn(p.value)}">${pp(p.value)}</span></div>`).join("")}</div>`;
}

function aiAlphaCard(na) {
  const a = na.ai_alpha;
  if (!a) return empty("AI 장부와 코어 장부가 같은 기간 쌓이면, AI 가 코어 전략에 더한 수익을 분리해 보여줍니다");
  const row = (lab, r, sub) => !r ? "" : `<tr><td><b>${lab}</b><span class="sub">${sub}</span></td><td class="r num ${sgn(r.excess)}"><b>${ppp(r.excess)}</b></td>
    <td class="r num">${r.t}</td><td class="r num ${sgn(r.ann)}">${pp(r.ann, 1)}</td><td class="r num small">${pp(r.ci90_ann[0], 1)} ~ ${pp(r.ci90_ann[1], 1)}</td><td class="r num">${r.days}</td>
    <td class="r">${r.days >= 60 && r.excess > 0 && r.t > 1.64 ? '<span class="chip ok">증명</span>' : r.days >= 60 && r.excess <= 0 ? '<span class="chip bad">없음</span>' : '<span class="chip">표본 부족</span>'}</td></tr>`;
  return `<div class="scroll"><table><thead><tr><th>구성</th><th class="r">누적</th><th class="r">t</th><th class="r">연율</th><th class="r">90% 구간</th><th class="r">일수</th><th class="r">판정</th></tr></thead><tbody>
    ${row("AI 알파 전체", a.total, `${esc(na.ai_book)} − 코어`)}${row("└ 거부권·긴급청산", a.veto, "코어+거부권 − 코어")}${row("└ 위성 베팅", a.satellite, "전체 − 코어+거부권")}</tbody></table></div>
    <div class="small dim" style="margin-top:8px">세 장부는 같은 날·같은 가격·같은 코어 규칙으로 움직이고 AI 가 관여한 부분만 다릅니다 → 차이가 곧 AI 의 순수 기여 (비용 포함). t &gt; 1.64 (단측 5%) 이고 60거래일 이상일 때만 '증명'.</div>`;
}

function blocksSvg(blocks) {
  if (!blocks || !blocks.length) return "";
  const max = Math.max(...blocks.map(Math.abs), 1e-6);
  return `<div class="blocks">${blocks.map((b, i) => `<div class="blk" title="구간 ${i + 1}: ${ppp(b)}"><div class="${b >= 0 ? "pos" : "neg"}" style="height:${Math.max(3, Math.abs(b) / max * 100)}%"></div></div>`).join("")}</div>`;
}

async function viewAlpha(el) {
  const mk = S.alphaMarket || "KR";
  const [na, cf, ex] = await Promise.all([api(`/api/net-alpha?market=${mk}`), mk === "KR" ? api("/api/an/counterfactual") : Promise.resolve({}), api(`/api/an/execution${mk === "US" ? "?mode=us-paper" : ""}`)]);
  if (mk === "US" && !(na.headline || {}).days) { el.innerHTML = usEmpty(na); bindMarketTabs(); return; }
  const steps = (na.steps || []).map((s) => {
    const [ic, cls, lab] = STEP_ST[s.status];
    return `<div class="step-card ${cls}"><div class="sc-h"><span class="fic">${ic}</span><b>${esc(s.title)}</b><span class="chip ${cls === "ok" ? "ok" : cls === "bad" ? "bad" : ""}">${lab}</span></div>
      <div class="sc-v num">${esc(s.headline)}</div><div class="small muted">${esc(s.detail)}</div></div>`;
  }).join("");
  const rep = (na.steps || []).find((s) => s.key === "repeat") || {};
  const regRows = (rep.rows || []).map((r) => `<tr><td>${esc(r.label)}</td><td class="r num">${r.days}</td><td class="r num ${sgn(r.excess)}"><b>${ppp(r.excess)}</b></td></tr>`).join("");
  const paths = Object.entries((cf && cf.paths) || {}).map(([k, p]) => `<tr><td><b>${esc(p.label)}</b> <span class="dim xs mono">${esc(k)}</span></td><td class="r num ${sgn(p.return)}">${pp(p.return)}</td><td class="r num down">${pp(p.max_drawdown)}</td></tr>`).join("");
  const nt = ((cf && cf.no_trade) || []).map((x) => `<tr><td>${esc(x.label)}</td><td class="r num">${x.n}</td><td class="r num">${pctRaw(x.avoided_loss_rate, 0)}</td><td class="r num ${sgn(x.avg_return_if_entered)}">${pp(x.avg_return_if_entered)}</td></tr>`).join("");
  const v = (cf && cf.veto) || {};
  const sl = ex.slippage_bps;
  el.innerHTML = `
  ${alphaHero(na, false)}
  <div class="steps-grid">${steps}</div>
  ${card(`AI Alpha 분리 증명 <span class='small dim'>AI 가 코어 전략 대비 실제로 더한 수익 · ${mk === "US" ? "미국" : "국내"}</span>`, aiAlphaCard(na))}
  <div class="grid g-2">
    ${card(`누적 곡선 <span class='small dim'>AI 장부 − 코어 · 운용 − ${esc(na.bench_name || "KOSPI")}</span>`, `<div id="alpha-chart" class="chart"></div>`)}
    ${card("수익 분해 (Alpha Attribution) <span class='small dim'>같은 기간 · 같은 가격</span>", waterfall(na.attribution))}
  </div>
  <div class="grid g-2">
    ${card("⑥ 반복되나 <span class='small dim'>20거래일 구간별 AI 알파 · 국면별</span>", (rep.blocks?.length ? blocksSvg(rep.blocks) + `<div class="small dim" style="margin:6px 0 10px">${esc(rep.detail || "")}</div>` : "")
      + (regRows ? `<table><thead><tr><th>국면</th><th class="r">일수</th><th class="r">AI 알파</th></tr></thead><tbody>${regRows}</tbody></table>` : empty("구간·국면 기록이 쌓이면 표시")))}
    ${card("반사실 (Counterfactual) <span class='small dim'>다르게 했다면?</span>", (paths ? `<div class="scroll"><table><thead><tr><th>경로</th><th class="r">수익</th><th class="r">최대낙폭</th></tr></thead><tbody>${paths}</tbody></table></div>` : empty())
      + (v.n ? `<div class="lesson" style="margin-top:10px">거부권 ${v.n}건 — 샀다면 평균 <b class="${sgn(v.avg_return_if_bought)}">${pp(v.avg_return_if_bought)}</b> · 하락 회피율 ${pctRaw(v.avoided_loss_rate, 0)} → <b>${esc(v.verdict)}</b></div>` : ""))}
  </div>
  <div class="grid g-2">
    ${card("NO TRADE 사유별 가치", nt ? `<div class="scroll"><table><thead><tr><th>사유</th><th class="r">건수</th><th class="r">하락 회피율</th><th class="r">진입했다면</th></tr></thead><tbody>${nt}</tbody></table></div>` : empty())}
    ${card(`실행 품질 <span class='small dim'>${esc((ex.mode || "").toUpperCase())} · 최근 ${ex.days}일</span>`, `<div class="stat-row" style="grid-template-columns:repeat(4,1fr)">
      <div class="stat"><div class="l">주문</div><div class="big num">${ex.orders ?? 0}</div></div>
      <div class="stat"><div class="l">체결률</div><div class="big num">${pctRaw(ex.fill_rate, 0)}</div></div>
      <div class="stat"><div class="l">슬리피지 평균</div><div class="big num">${sl ? sl.mean + "bp" : "-"}</div><div class="xs dim">가정 ${ex.assumed_slippage_bps}bp</div></div>
      <div class="stat"><div class="l">총 비용</div><div class="big num">${ex.cost_bps != null ? ex.cost_bps + "bp" : "-"}</div></div></div>
      ${ex.verdict ? `<div class="lesson" style="margin-top:10px;border-left-color:${ex.verdict[0] === "warn" ? "var(--warn)" : "var(--good)"}">${esc(ex.verdict[1])}</div>` : ""}
      <div class="small dim" style="margin-top:8px">부분체결 ${pctRaw(ex.partial_rate, 0)} · 미체결/취소 ${pctRaw(ex.cancel_rate, 0)} · 상태불명 ${ex.unknown || 0} · 거부 ${ex.rejected || 0}</div>`)}
  </div>`;
  const ch = $("#alpha-chart");
  const series = [];
  const toPct = (c) => (c || []).map(([t, v]) => [t, +(v * 100).toFixed(3)]);  // 축을 %p 로
  if ((na.ai_curve || []).length) series.push({ data: toPct(na.ai_curve), color: "#a855f7", title: "AI−코어 %p" });
  if ((na.curve || []).length) series.push({ data: toPct(na.curve), color: "#3b82f6", title: `운용−${na.bench_name || "KOSPI"} %p` });
  bindMarketTabs();
  if (ch) series.length ? lineChart(ch, series) : (ch.innerHTML = empty("운용 기록이 쌓이면 표시"));
}

// ================================================================= 안전 센터 (자동 킬스위치)
function safetyMini(g) {
  if (!g || g.error) return empty(g?.error || "점검 중…");
  const [label, cls, sub] = STATE_UI[g.state] || [g.state, "na", ""];
  return `<div class="safe-mini"><div class="state-pill ${cls}"><span class="pulse"></span>${esc(label)}</div><div class="small muted">${esc(sub)}</div>
    <div class="cond-grid">${g.conditions.map((c) => { const [ic, k] = G_ST[c.status] || G_ST.unknown; return `<div class="cond ${k}" title="${esc(c.detail)}"><span>${ic}</span>${esc(c.name)}</div>`; }).join("")}</div></div>`;
}

async function viewSafety(el) {
  const [g, champ, conf, st] = await Promise.all([api("/api/guardian"), api("/api/an/champion"), api("/api/an/confidence"), api("/api/an/stress")]);
  const [label, cls, sub] = STATE_UI[g.state] || [g.state, "na", ""];
  const ks = g.kill_switch || {};
  const conds = g.conditions.map((c) => { const [ic, k, lab] = G_ST[c.status] || G_ST.unknown; return `<tr class="cond-row ${k}"><td><span class="cdot ${k}">${ic}</span></td><td><b>${esc(c.name)}</b><span class="sub">${esc(c.label)}</span></td><td><span class="chip ${k === "ok" ? "ok" : k === "bad" ? "bad" : ""}">${lab}</span></td><td class="small" style="white-space:normal">${esc(c.detail)}</td></tr>`; }).join("");
  const ev = (champ.events || []).map((e) => `<div class="lesson" style="border-left-color:var(--bad)"><b>${date(e.at)} 롤백</b> ${esc(e.rolled_back)} → ${esc(e.restored || "champion 없음")}<div class="small muted">${esc(e.reason || "")}</div></div>`).join("");
  const items = (conf.items || []).map((i) => `<tr><td>${esc(i.label)}</td><td><span class="chip ${i.status === "ok" ? "ok" : i.status === "stale" || i.status === "missing" ? "bad" : ""}">${{ ok: "신선", stale: "오래됨", missing: "없음", off: "꺼짐" }[i.status] || i.status}</span></td><td class="small mono">${esc(i.last || "-")}</td><td class="small dim">${i.status === "ok" ? "" : esc(i.hint)}</td></tr>`).join("");
  const sc = (st.scenarios || []).map((s) => `<tr><td><b>${esc(s.label)}</b><span class="sub">${esc(s.period)} · ${esc(s.method)}</span></td><td class="r num ${sgn(s.kospi)}">${pp(s.kospi, 1)}</td><td class="r num ${sgn(s.portfolio)}"><b>${pp(s.portfolio, 1)}</b></td><td class="r num">${num(s.loss_krw)}</td></tr>`).join("");
  el.innerHTML = `
  <div class="card safety-hero ${cls}">
    <div class="flow"><div class="fl-node ${g.state === "HALTED" ? "" : "on"}">LIVE TRADING</div><div class="fl-arrow">→</div><div class="fl-node ${g.state === "HALTED" ? "on bad" : ""}">HALTED</div></div>
    <div class="state-pill big ${cls}"><span class="pulse"></span>${esc(label)}</div>
    <div><div><b>${esc(sub)}</b></div><div class="small muted">${ks.on ? `${ks.halt ? "자동 정지" : "수동 정지"} · ${esc(ks.by || "")} · ${time(ks.at, true)} — ${esc(ks.reason || "")}` : "10개 조건 중 하나라도 '심각' 이면 즉시 HALTED (매도 포함 새 주문 없음). 자동 해제는 없습니다 — 원인을 확인한 사람이 해제합니다."}</div></div>
    <div class="sh-btns"><button class="btn-sm" id="g-check">${ICONS.refresh} 지금 점검</button>${ks.on ? `<button class="btn-sm danger" id="g-release">정지 해제</button>` : ""}</div>
  </div>
  ${card("자동 킬스위치 조건 <span class='small dim'>5분마다 자동 점검 · 매매 사이클 직전에도 확인</span>", `<div class="scroll"><table class="cond-table"><tbody>${conds}</tbody></table></div>`)}
  <div class="grid g-2">
    ${card("Champion 모델 감시 → 자동 롤백", champ.status === "none" ? empty("champion 모델 없음 — 코어 팩터 전략은 모델 없이 운용됩니다") : `<div class="stat-row" style="grid-template-columns:repeat(4,1fr)">
      <div class="stat"><div class="l">champion</div><div class="num"><b>${esc(champ.version || "-")}</b></div></div>
      <div class="stat"><div class="l">전진 표본</div><div class="big num">${champ.n ?? 0}</div></div>
      <div class="stat"><div class="l">전진 적중률</div><div class="big num">${pctRaw(champ.accuracy)}</div></div>
      <div class="stat"><div class="l">상태</div><div class="big ${champ.passed === false ? "bad-t" : "good"}">${champ.passed === false ? "이상" : "정상"}</div></div></div>
      ${(champ.failures || []).map((f) => `<div class="veto">${esc(f)}</div>`).join("")}
      <div class="small dim" style="margin-top:8px">롤백 기준: 전진 60건 이상에서 적중률 45% 미만 · Brier 가 기저율보다 0.01 이상 나쁨 · 확률 붕괴(표준편차 0.003 미만)</div>`)
      + (ev ? card("롤백 기록", ev) : "")}
    ${card(`데이터 신뢰도 <span class="chip ${conf.score >= 80 ? "ok" : conf.score < 55 ? "bad" : ""}">${conf.score ?? "-"} / 100 · ${esc(conf.label || "")}</span>`, `<div class="scroll"><table class="tight"><tbody>${items}</tbody></table></div>
      <div class="small dim" style="margin-top:8px">커버리지: 마지막 거래일에 시세가 있는 종목 ${conf.symbols_on_last_bar}/${conf.symbols} (${pctRaw(conf.coverage, 0)}) · 품질 경고 ${conf.quality_warnings}건</div>`)}
  </div>
  ${card(`포트폴리오 스트레스 테스트 <span class='small dim'>${esc(st.note || "")}</span>`, st.n_positions ? `<div class="scroll"><table><thead><tr><th>시나리오</th><th class="r">KOSPI</th><th class="r">내 포트폴리오</th><th class="r">손실(원)</th></tr></thead><tbody>${sc}</tbody></table></div>
    <div class="chips" style="margin-top:10px">${(st.factor || []).map((f) => `<span class="chip">${esc(f.label)} → <b class="${sgn(f.portfolio)}">${pp(f.portfolio, 1)}</b></span>`).join(" ")}</div>` : empty("보유 종목이 없어 스트레스 테스트 대상 없음"))}`;
  $("#g-check").onclick = async (e) => { e.target.disabled = true; await runAction("guardian"); render(); };
  const rb = $("#g-release");
  if (rb) rb.onclick = async () => {
    if (!confirm("정지를 해제할까요?\n원인을 확인하셨나요? 조건이 여전히 '심각' 이면 다음 점검에서 다시 멈춥니다.")) return;
    await post("/api/killswitch", { on: false, reason: "대시보드 해제" }); await refresh(); render();
  };
}

// ================================================================= 실험 · 승격
async function viewLab(el) {
  const [v, exps, evs] = await Promise.all([api("/api/an/ai_verdict"), api("/api/an/experiments"), api("/api/an/events")]);
  const prog = Math.round((v.progress ?? 0) * 100);
  const steps = (v.steps || []).map((s) => `<tr><td><b>${esc(s.label)}</b><span class="sub mono">${esc(s.how)}</span></td><td class="r num ${sgn(s.excess)}">${ppp(s.excess)}</td><td class="r num ${sgn(-s.dd_diff)}">${ppp(s.dd_diff)}</td><td class="r">${s.passes ? '<span class="chip ok">통과</span>' : '<span class="chip">미달</span>'}</td></tr>`).join("");
  const models = (exps.models || []).map((m) => `<tr><td class="small dim">${date(m.created_at)}</td><td><b>${esc(m.name)}</b><span class="sub mono">${esc(m.version)}</span></td><td><span class="chip ${m.status === "champion" ? "ok" : m.status === "rejected" || m.status === "rolled_back" ? "bad" : ""}">${esc(m.status)}</span></td><td class="r num">${m.metrics?.sharpe?.toFixed?.(2) ?? "-"}</td><td class="r num">${m.metrics?.dsr?.toFixed?.(2) ?? "-"}</td><td class="small" style="white-space:normal">${esc(m.notes || "")}</td></tr>`).join("");
  const research = (exps.research || []).map((r) => `<tr><td class="small mono">${esc(r.file)}</td><td>${esc(r.kind)}</td><td class="r num">${r.n_trials ?? "-"}</td><td class="small dim">${esc(r.dev_end || "")}</td></tr>`).join("");
  const ev = (evs.types || []).map((t) => `<tr><td><b>${esc(t.type)}</b></td><td class="r num">${t.d1?.n ?? 0}</td><td class="r num ${sgn(t.d1?.mean)}">${pp(t.d1?.mean)}</td><td class="r num">${pctRaw(t.d1?.pos_rate, 0)}</td><td class="r num ${sgn(t.d5?.mean)}">${pp(t.d5?.mean)}</td><td class="r num">${t.d1?.t ?? "-"}</td></tr>`).join("");
  el.innerHTML = `
  <div class="card lifecycle"><div class="lc-title">AI 가 스스로 바뀌는 플랫폼이 아니라 — <b>연구하고, 검증을 통과한 것만 승격</b>하는 플랫폼</div>
    <div class="lc">${[["연구", "walk-forward · 비용 2배 · DSR"], ["Candidate", "백테스트 게이트"], ["Shadow", "실시간 채점만 · 주문 없음"], ["Champion", "실제 매매 모델"], ["감시", "전진 성과 · 확률 붕괴"], ["Rollback", "이상 시 직전 champion 복귀"]]
      .map(([t, s], i) => `<div class="lc-node ${i === 5 ? "bad" : i === 3 ? "ok" : ""}"><b>${t}</b><span>${s}</span></div>`).join('<div class="lc-arr">→</div>')}</div></div>
  ${card(`AI 켜기 판정 <span class="chip ${v.status === "promote" ? "ok" : v.status === "keep_core" ? "bad" : ""}">${esc(v.title || "")}</span>`, `<div class="small">${esc(v.message || "")}</div>
    <div class="prog"><div style="width:${prog}%"></div></div><div class="small dim">증거 ${prog}% — 섀도 ${v.days ?? 0}/${v.min_days} 거래일 · 채점 ${v.scored ?? 0}/${v.min_scored}건 · 합의 Brier Skill ${v.brier_skill ?? "-"}</div>
    ${steps ? `<table style="margin-top:12px"><thead><tr><th>단계</th><th class="r">코어 대비 수익</th><th class="r">낙폭 차이</th><th class="r">판정</th></tr></thead><tbody>${steps}</tbody></table>` : ""}`)}
  ${card(`실험 레지스트리 <span class='small dim'>${esc(exps.note || "")} · 누적 시험 ${exps.n_trials_total ?? 0}회</span>`, (models ? `<div class="scroll"><table><thead><tr><th>일자</th><th>모델</th><th>상태</th><th class="r">Sharpe</th><th class="r">DSR</th><th>게이트</th></tr></thead><tbody>${models}</tbody></table></div>` : empty("등록된 모델 후보 없음 (매일 장 마감 후 자동 학습)"))
    + (research ? `<div class="small muted" style="margin:12px 0 6px">실데이터 연구 리포트</div><table class="tight"><tbody>${research}</tbody></table>` : "")) }
  ${card(`이벤트 반응 DB <span class='small dim'>${esc(evs.note || "")}</span>`, ev ? `<div class="scroll"><table><thead><tr><th>공시 유형</th><th class="r">표본</th><th class="r">다음날</th><th class="r">양수 비율</th><th class="r">5일</th><th class="r">t</th></tr></thead><tbody>${ev}</tbody></table></div>` : empty("공시(DART) 데이터가 쌓이면 유형별 과거 반응을 계산합니다 — DART_API_KEY 설정"))}`;
}

// ================================================================= 서버 · DB
async function viewServer(el) {
  const [s, db] = await Promise.all([api("/api/server"), api("/api/db")]);
  const q = (s.llm_quota || []).map((x) => `<div class="quota"><span>${aiIcon(x.provider, null, null, 22)} <b>${esc(x.provider)}</b></span><div class="bar"><div style="width:${x.limit ? Math.min(100, x.used / x.limit * 100) : 0}%"></div></div><span class="num small">${x.used}/${x.limit ?? "-"} <span class="dim">캐시 ${x.cached}</span></span></div>`).join("");
  const jobs = (s.scheduler.jobs || []).map((j) => `<tr><td><b>${esc(j.job)}</b></td><td class="small mono">${esc(j.last)}</td><td class="r num">${j.runs}</td><td class="r num ${j.fails ? "bad-t" : ""}">${j.fails}</td><td>${j.ok === false ? `<span class="chip bad">실패</span> <span class="xs dim">${esc(j.error)}</span>` : '<span class="chip ok">정상</span>'}</td></tr>`).join("");
  const rows = db.rows.map((r) => `<tr><td>${esc(r.label)}</td><td class="r num ${r.rows ? "warn-t" : "dim"}">${num(r.rows)}</td></tr>`).join("");
  const tables = Object.entries(db.tables || {}).map(([k, v]) => `<span class="chip">${esc(k)} <b>${num(v)}</b></span>`).join(" ");
  const mb = (b) => b == null ? "-" : `${(b / 1e6).toFixed(1)}MB`;
  el.innerHTML = `
  <div class="stat-row srv-top">
    <div class="stat card"><div class="l">DB</div><div class="big ${s.db.ok ? "good" : "bad-t"}">${s.db.ok ? "정상" : "응답 없음"}</div><div class="xs dim">${esc(s.db.kind)} · ${s.db.latency_ms}ms · ${mb(s.db.size_bytes)}</div></div>
    <div class="stat card"><div class="l">스케줄러</div><div class="big ${s.scheduler.alive ? "good" : "warn-t"}">${s.scheduler.alive ? "동작 중" : "미실행"}</div><div class="xs dim">마지막 ${esc(s.scheduler.last_job || "-")}</div></div>
    <div class="stat card"><div class="l">매매 상태</div><div class="big">${esc(s.guardian.state || "-")}</div><div class="xs dim">킬스위치 ${s.kill_switch?.on ? "ON" : "OFF"}</div></div>
    <div class="stat card"><div class="l">데이터 신뢰도</div><div class="big num">${s.data_confidence}</div><div class="xs dim">/ 100</div></div>
    <div class="stat card"><div class="l">디스크 여유</div><div class="big num">${s.disk.free_gb}GB</div><div class="xs dim">가동 ${Math.round(s.uptime_min)}분 · v${esc(s.version)}</div></div>
  </div>
  <div class="grid g-2">
    ${card("AI 무료 한도 (오늘, UTC 기준)", q || empty("AI 키 없음 — .env 에 GEMINI_API_KEY 등"))}
    ${card(`DB 정리 <span class='small dim'>${mb(db.size_before)}</span>`, `<table class="tight"><tbody>${rows}</tbody></table><div class="small dim" style="margin:8px 0">${esc(db.kept)}</div>
      <div class="chips">${tables}</div>
      <div style="display:flex;gap:8px;margin-top:12px;align-items:center"><button class="btn-sm danger" id="db-clean" ${db.total ? "" : "disabled"}>정리 실행 (${num(db.total)}행)</button><span class="small dim" id="db-msg">${db.last?.at ? `마지막 정리 ${date(db.last.at)} · ${mb(db.last.before)} → ${mb(db.last.after)}` : ""}</span></div>`)}
  </div>
  ${card("작업 실행 현황 (최근 2일)", jobs ? `<div class="scroll"><table><thead><tr><th>작업</th><th>마지막 실행</th><th class="r">실행</th><th class="r">실패</th><th>상태</th></tr></thead><tbody>${jobs}</tbody></table></div>` : empty("스케줄러 기록 없음 — ./run.sh 로 24시간 운영을 시작하세요"))}`;
  $("#db-clean").onclick = async (e) => {
    if (!confirm(`DB 정리를 실행할까요?\n${db.rows.filter((r) => r.rows).map((r) => `· ${r.label}: ${r.rows}행`).join("\n")}\n\n주문·체결·AI 판단·복기·모델 기록은 지우지 않습니다.`)) return;
    e.target.disabled = true;
    const r = await runAction("db_clean", (p) => { $("#db-msg").textContent = p; });
    $("#db-msg").textContent = r.error ? `실패: ${r.error}` : `완료 · ${mb(r.result?.size_before)} → ${mb(r.result?.size_after)}`;
    setTimeout(render, 1200);
  };
}

async function runAction(name, onProgress, params = {}) {
  let st = await post("/api/action", { name, ...params });
  const key = st.name || name;  // 종목 분석은 'analyze:000660' 처럼 종목별로 따로 돈다
  for (let i = 0; i < 900 && st.running; i++) {
    await new Promise((r) => setTimeout(r, 1000));
    st = await api(`/api/action?name=${encodeURIComponent(key)}`);
    if (onProgress) onProgress(st.progress || "");
  }
  return st;
}

// ================================================================= 시작 체크리스트 ('데이터 없음' 대신)
function setupBanner(s) {
  if (!s || s.ready) return "";
  const act = s.action || {};
  return `<div class="card setup-card"><div class="setup-h"><div><b>시작 체크리스트</b><span class="small muted"> — 비어 있는 화면은 아래 항목이 채워지면 자동으로 채워집니다</span></div>
    <button class="btn-sm primary" id="warmup-btn" ${act.running ? "disabled" : ""}>${act.running ? "채우는 중…" : "지금 채우기"}</button></div>
    <div class="setup-steps">${s.steps.map((x) => `<div class="ss ${x.done ? "done" : x.optional ? "opt" : ""}"><span class="ssi">${x.done ? "✓" : x.optional ? "○" : "!"}</span><div><b>${esc(x.label)}</b>${x.optional ? ' <span class="xs dim">선택</span>' : ""}<div class="xs muted">${esc(x.detail)}</div>${x.done ? "" : `<div class="xs" style="color:var(--accent-3)">${esc(x.fix)}</div>`}</div></div>`).join("")}</div>
    <div class="small dim" id="warmup-msg">${act.running ? esc(act.progress || "") : act.error ? "실패: " + esc(act.error) : act.finished_at ? "마지막 채우기 " + time(act.finished_at, true) : ""}</div></div>`;
}

function bindSetup() {
  const b = $("#warmup-btn");
  if (!b) return;
  b.onclick = async () => {
    b.disabled = true; b.textContent = "채우는 중…";
    const r = await runAction("warmup", (p) => { const m = $("#warmup-msg"); if (m) m.textContent = p; });
    const m = $("#warmup-msg");
    if (m) m.textContent = r.error ? `실패: ${r.error}` : "완료 — 화면을 새로 고칩니다";
    await refresh(); render();
  };
}

// ================================================================= 채팅 AI
// 대화 id 는 app.js 의 safeGet 이 준비된 뒤(첫 사용 시) 정한다
const CHAT = {
  _sid: null, busy: false, info: null,
  get sid() {
    if (!this._sid) { this._sid = safeGet("qa_chat_sid") || Math.random().toString(36).slice(2, 12); safeSet("qa_chat_sid", this._sid); }
    return this._sid;
  },
};
const SUGGEST = ["하이닉스 지금 상태 어때?", "엔비디아 어때?", "오늘 시장 어때?", "서버 상태 알려줘", "DB 정리해줘", "AI 성과 검증 결과", "킬스위치 상태", "내 포트폴리오"];
const TOOL_LABEL = { search_stocks: "🔎 종목 검색", stock_overview: "📈 종목 조회", market_overview: "🌐 시장", portfolio: "💼 포트폴리오", net_alpha: "🏆 Net Alpha", safety_status: "🛡️ 안전 점검", ai_performance: "🎯 AI 성적", server_status: "🖥️ 서버", db_status: "🗄️ DB", propose_action: "▶ 실행 제안" };

function md(text) {
  const lines = esc(text || "").split("\n");
  let out = "", list = false, table = [];
  const inline = (s) => s.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/#(analysis|alpha|safety|lab|server|risk|portfolio|journal)(\/[A-Za-z0-9.\-]+)?/g, '<a class="link" href="#$1$2">#$1$2</a>');
  const flushTable = () => {
    if (!table.length) return;
    const rows = table.filter((r) => !/^\|?\s*:?-{2,}/.test(r)).map((r) => r.replace(/^\||\|$/g, "").split("|").map((c) => inline(c.trim())));
    out += `<table class="tight md-t"><thead><tr>${rows[0].map((c) => `<th>${c}</th>`).join("")}</tr></thead><tbody>${rows.slice(1).map((r) => `<tr>${r.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
    table = [];
  };
  for (const raw of lines) {
    const l = raw.trimEnd();
    if (/^\|.*\|$/.test(l.trim())) { if (list) { out += "</ul>"; list = false; } table.push(l.trim()); continue; }
    flushTable();
    if (/^\s*[-*•]\s+/.test(l)) { if (!list) { out += "<ul>"; list = true; } out += `<li>${inline(l.replace(/^\s*[-*•]\s+/, ""))}</li>`; continue; }
    if (list) { out += "</ul>"; list = false; }
    if (/^#{1,4}\s/.test(l)) out += `<div class="md-h">${inline(l.replace(/^#{1,4}\s/, ""))}</div>`;
    else if (/^\d+\.\s/.test(l)) out += `<div class="md-ol">${inline(l)}</div>`;
    else if (l.trim()) out += `<p>${inline(l)}</p>`;
  }
  flushTable();
  if (list) out += "</ul>";
  return out;
}

function chatMsgHtml(m) {
  if (m.role === "user") return `<div class="msg user"><div class="bubble">${esc(m.content)}</div></div>`;
  const tools = (m.tools || []).map((t) => `<span class="tchip ${t.ok === false ? "bad" : ""}">${TOOL_LABEL[t.tool] || esc(t.tool)}${t.args?.symbol ? " · " + esc(t.args.symbol) : ""}</span>`).join("");
  const cards = (m.cards || []).map((c) => `<a class="stock-card" href="#analysis/${esc(c.symbol)}"><div><b>${esc(c.name)}</b> <span class="xs dim">${esc(c.symbol)}</span><div class="num">${c.currency === "USD" ? "$" + num(c.last, 2) : num(c.last) + "원"} <span class="${sgn(c.ret_1d)}">${pp(c.ret_1d)}</span></div><div class="xs dim">${esc(c.date || "")} 종가</div></div><div class="sc-spark">${spark(c.spark, 120, 36)}</div></a>`).join("");
  const acts = (m.actions || []).map((a) => `<button class="act-btn ${a.danger ? "danger" : ""}" data-act="${esc(a.action)}" data-sym="${esc(a.symbol || "")}" title="${esc(a.reason || "")}">▶ ${esc(a.label)}</button>`).join("");
  return `<div class="msg ai"><div class="avatar-ai">${ICONS.chat}</div><div class="bubble">${tools ? `<div class="tchips">${tools}</div>` : ""}${md(m.content)}${cards ? `<div class="cards">${cards}</div>` : ""}${acts ? `<div class="acts">${acts}</div>` : ""}${m.model ? `<div class="xs dim" style="margin-top:6px">${esc(m.model)}</div>` : ""}</div></div>`;
}

function chatShell(id, full = false) {
  return `<div class="chat ${full ? "full" : ""}" id="${id}">
    <div class="chat-h"><span class="ch-ic">${ICONS.chat}</span><div><b>Quant AI 어시스턴트</b><div class="xs muted" data-role="model">연결 확인 중…</div></div>
      <button class="icon-btn" data-role="clear" title="대화 지우기">${ICONS.refresh}</button>${full ? "" : `<button class="icon-btn" data-role="close" title="닫기">✕</button>`}</div>
    <div class="chat-body" data-role="body"></div>
    <div class="chat-sug" data-role="sug">${SUGGEST.map((s) => `<button>${esc(s)}</button>`).join("")}</div>
    <form class="chat-in" data-role="form"><textarea rows="1" placeholder="종목·시장·서버·DB 무엇이든 물어보세요 (Enter 전송)" maxlength="2000"></textarea><button type="submit" class="send">${ICONS.send}</button></form>
  </div>`;
}

async function chatLoad(root) {
  const body = root.querySelector('[data-role="body"]');
  try {
    const h = await api(`/api/chat?sid=${CHAT.sid}`);
    CHAT.info = h;
    root.querySelector('[data-role="model"]').innerHTML = h.model ? `${aiIcon(h.provider, null, h.model, 14)} ${esc(h.model)} · 플랫폼 데이터 연결됨` : "AI 키 없음 · 플랫폼 데이터 요약 모드";
    body.innerHTML = h.messages.length ? h.messages.map(chatMsgHtml).join("") : `<div class="chat-hello"><div class="hello-ic">${ICONS.chat}</div><b>무엇이든 물어보세요</b><div class="small muted">이 사이트의 시세·AI 판단·성과·서버 상태를 직접 조회해서 답합니다.<br>DB 정리 같은 작업은 버튼을 눌러야 실행됩니다.</div></div>`;
  } catch (e) { body.innerHTML = empty(e.message); }
  body.scrollTop = body.scrollHeight;
  bindChatActs(root);
}

function bindChatActs(root) {
  root.querySelectorAll(".act-btn").forEach((b) => b.onclick = async () => {
    const a = b.dataset.act;
    if (b.classList.contains("danger") && !confirm("실행할까요? (DB 정리는 오래된 로그만 지우며 주문·판단 기록은 보존합니다)")) return;
    b.disabled = true; b.textContent = "실행 중…";
    const r = await runAction(a, (p) => { b.textContent = p || "실행 중…"; }, b.dataset.sym ? { symbol: b.dataset.sym } : {});
    b.textContent = r.error ? `실패: ${r.error}` : a === "analyze" && r.result ? `✓ ${r.result.action} · 상승확률 ${pctRaw(r.result.prob_up, 0)} — 종목 화면에서 근거 보기` : "✓ 완료";
    if (a === "analyze" && !r.error) { b.onclick = () => { location.hash = `#analysis/${b.dataset.sym}`; }; b.disabled = false; }
    S._cacheBust = Date.now();
  });
}

async function chatSend(root, text) {
  if (CHAT.busy || !text.trim()) return;
  CHAT.busy = true;
  const body = root.querySelector('[data-role="body"]');
  body.querySelector(".chat-hello")?.remove();
  body.insertAdjacentHTML("beforeend", chatMsgHtml({ role: "user", content: text }) + `<div class="msg ai typing"><div class="avatar-ai">${ICONS.chat}</div><div class="bubble"><span class="dots"><i></i><i></i><i></i></span> 플랫폼 데이터를 조회하는 중…</div></div>`);
  body.scrollTop = body.scrollHeight;
  try {
    const r = await post("/api/chat", { message: text, sid: CHAT.sid });
    body.querySelector(".typing")?.remove();
    body.insertAdjacentHTML("beforeend", chatMsgHtml({ role: "assistant", content: r.answer, tools: r.tools_used, cards: r.cards, actions: r.actions, model: r.model }));
  } catch (e) {
    body.querySelector(".typing")?.remove();
    body.insertAdjacentHTML("beforeend", `<div class="msg ai"><div class="avatar-ai">${ICONS.chat}</div><div class="bubble"><div class="veto">${esc(e.message)}</div></div></div>`);
  }
  CHAT.busy = false;
  body.scrollTop = body.scrollHeight;
  bindChatActs(root);
}

function mountChat(root) {
  const form = root.querySelector('[data-role="form"]');
  const ta = form.querySelector("textarea");
  form.onsubmit = (e) => { e.preventDefault(); const t = ta.value; ta.value = ""; ta.style.height = ""; chatSend(root, t); };
  ta.onkeydown = (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); form.requestSubmit(); } };
  ta.oninput = () => { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 140) + "px"; };
  root.querySelectorAll('[data-role="sug"] button').forEach((b) => b.onclick = () => chatSend(root, b.textContent));
  root.querySelector('[data-role="clear"]').onclick = async () => { await post("/api/chat/clear", { sid: CHAT.sid }); chatLoad(root); };
  const close = root.querySelector('[data-role="close"]');
  if (close) close.onclick = () => root.parentElement.classList.remove("open");
  chatLoad(root);
}

function initChatWidget() {
  const w = document.createElement("div");
  w.className = "chat-dock";
  w.innerHTML = `<button class="chat-fab" id="chat-fab" title="AI 어시스턴트">${ICONS.chat}<span>AI 에게 묻기</span></button>${chatShell("chat-pop")}`;
  document.body.appendChild(w);
  let mounted = false;
  $("#chat-fab").onclick = () => {
    w.classList.toggle("open");
    if (!mounted) { mountChat($("#chat-pop")); mounted = true; }
    if (w.classList.contains("open")) setTimeout(() => $("#chat-pop textarea")?.focus(), 50);
  };
  window.askAI = (q) => {
    w.classList.add("open");
    if (!mounted) { mountChat($("#chat-pop")); mounted = true; }
    chatSend($("#chat-pop"), q);
  };
}

async function viewChat(el) {
  el.innerHTML = chatShell("chat-full", true);
  mountChat($("#chat-full"));
}

// ================================================================= 검색 (서버: 한글 별칭 · 해외 종목)
function initSearch() {
  const input = $("#search");
  const box = $("#search-results");
  let timer = null, seq = 0;
  input.placeholder = "종목명·별칭·티커 (예: 하이닉스, 삼전, 엔비디아, AAPL)";
  input.addEventListener("input", () => {
    clearTimeout(timer);
    const q = input.value.trim();
    if (!q) { box.classList.remove("open"); return; }
    timer = setTimeout(async () => {
      const my = ++seq;
      let res = [];
      try { res = (await api(`/api/search?q=${encodeURIComponent(q)}`)).results; } catch { res = []; }
      if (my !== seq) return;
      box.innerHTML = res.map((w) => `<a data-sym="${esc(w.symbol)}"><span>${w.market === "GLOBAL" ? "🌐 " : ""}${esc(w.name)} <span class="dim small">${esc(w.symbol)}</span></span>${w.market === "GLOBAL" ? '<span class="chip">해외</span>' : badge(w.action)}</a>`).join("")
        + `<a data-ask="${esc(q)}" class="ask"><span>${ICONS.chat} AI 에게 “${esc(q)}” 물어보기</span></a>`;
      box.classList.add("open");
      box.querySelectorAll("a[data-sym]").forEach((a) => a.onclick = () => { box.classList.remove("open"); input.value = ""; location.hash = `#analysis/${a.dataset.sym}`; });
      box.querySelector("a[data-ask]").onclick = () => { box.classList.remove("open"); input.value = ""; window.askAI?.(`${q} 어때?`); };
    }, 180);
  }, true);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") box.querySelector("a")?.click(); });
}


// ================================================================= 종목 AI 분석 (판단 기록이 없을 때 바로 실행)
function analyzeCard(sym, name, auto) {
  return `<div class="analyze-card" id="analyze-card"><div class="an-ic">${ICONS.chat}</div><div style="flex:1;min-width:0"><b>${esc(name)} — 아직 AI 판단 기록이 없습니다</b>
    <div class="small muted" id="analyze-msg">${auto ? "AI 들이 지금 독립적으로 판단하는 중… (공급자마다 동시에 · 보통 10~60초)" : "매일 판단하는 종목은 코어 후보·보유·관심 종목입니다. 이 종목은 지금 바로 판단할 수 있습니다 (주문 없음)."}</div></div>
    <button class="btn-sm primary" id="analyze-btn" ${auto ? "disabled" : ""}>${auto ? "분석 중…" : "지금 AI 분석"}</button></div>`;
}

async function startAnalyze(sym, rerender) {
  const b = $("#analyze-btn"), m = $("#analyze-msg");
  if (b) { b.disabled = true; b.textContent = "분석 중…"; }
  const r = await runAction("analyze", (p) => { if (m) m.textContent = p || "AI 판단 중…"; }, { symbol: sym });
  if (r.error) { if (m) m.innerHTML = `<span class="bad-t">실패: ${esc(r.error)}</span>`; if (b) { b.disabled = false; b.textContent = "다시 시도"; } return; }
  S._analyzed = { ...(S._analyzed || {}), [sym]: Date.now() };
  rerender();
}

function bindAnalyze(sym, auto, rerender) {
  const b = $("#analyze-btn");
  if (b) b.onclick = () => startAnalyze(sym, rerender);
  // AI 키가 있으면 화면을 여는 순간 자동으로 한 번 (같은 종목은 10분 안에 다시 자동 실행하지 않음)
  if (auto) startAnalyze(sym, rerender);
}

// ================================================================= 종목 상세 (투자 전에 보는 것들)
const PF = {};  // 종목별 상세 캐시 (화면 전환 때 다시 부르지 않게)
const WD = ["일", "월", "화", "수", "목", "금", "토"];
function moneyShort(v, cur) {
  if (v == null) return "-";
  const a = Math.abs(v);
  if (cur === "KRW") return a >= 1e12 ? `${(v / 1e12).toFixed(1)}조원` : a >= 1e8 ? `${num(v / 1e8)}억원` : `${num(v)}원`;
  const s = cur === "USD" ? "$" : "";
  return a >= 1e12 ? `${s}${(v / 1e12).toFixed(2)}T` : a >= 1e9 ? `${s}${(v / 1e9).toFixed(2)}B` : a >= 1e6 ? `${s}${(v / 1e6).toFixed(1)}M` : `${s}${num(v, 2)}`;
}
function evDate(iso) {
  if (!iso) return "-";
  const [y, m, d] = iso.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  return `${m}월 ${d}일 (${WD[dt.getUTCDay()]})`;
}
const EV_ICON = { earnings: "📊", earnings_prelim: "📊", ex_div: "✂️", div_pay: "💰", dividend: "💰", ir: "🎤", agm: "🏛️", report: "📑", offering: "⚠️", buyback: "🔁" };
const safeUrl = (u) => /^https?:\/\//i.test(u || "") ? esc(u) : null;

function pfEvents(p) {
  const evs = p.events || [];
  if (!evs.length) return `<div class="ev-none small dim">예정된 실적 발표·배당 일정 정보가 없습니다${(p.sources || []).length ? "" : " — 외부 데이터 소스에 연결하지 못했습니다"}</div>`;
  const earn = evs.find((e) => (e.kind === "earnings") && !e.past);
  const warn = earn && earn.d_day <= 7 ? `<div class="ev-warn">⚡ <b>${earn.d_label === "오늘" ? "오늘" : earn.d_label}</b> 실적 발표 — 발표 전후로 주가 변동이 커질 수 있습니다. 새로 사기 전에 발표 결과를 확인하는 것도 방법입니다.</div>` : "";
  return `<div class="ev-strip">${evs.slice(0, 6).map((e) => `<div class="ev ${e.kind === "earnings" && !e.past ? "hot" : ""} ${e.past ? "past" : ""}">
    <div class="ev-d">${esc(e.d_label)}</div>
    <div class="ev-b"><div><span>${EV_ICON[e.kind] || "📌"}</span> <b>${esc(e.label)}</b>${e.estimated ? ' <span class="chip xs">예상</span>' : ""}${e.filed ? ' <span class="chip xs">공시</span>' : ""}</div>
      <div class="small">${evDate(e.date)}${e.date_end ? ` ~ ${evDate(e.date_end)}` : ""}${e.time ? ` · ${esc(e.time)}` : ""}</div>
      ${e.detail ? `<div class="xs dim ev-det">${e.url && safeUrl(e.url) ? `<a class="link" href="${safeUrl(e.url)}" target="_blank" rel="noopener">${esc(e.detail)}</a>` : esc(e.detail)}</div>` : ""}</div></div>`).join("")}</div>${warn}`;
}

function range52(st, cur) {
  if (st.high52 == null || st.low52 == null) return "";
  const pos = st.pos52 == null ? null : Math.max(0, Math.min(1, st.pos52));
  return `<div class="r52"><div class="xs muted">52주 범위 ${pos == null ? "" : `· 현재 <b>${Math.round(pos * 100)}%</b> 위치`}</div>
    <div class="r52-bar">${pos == null ? "" : `<i style="left:${pos * 100}%"></i>`}</div>
    <div class="r52-l xs"><span class="down">${esc(priceCur(st.low52, cur))}</span><span class="up">${esc(priceCur(st.high52, cur))}</span></div></div>`;
}
const priceCur = (v, cur) => v == null ? "-" : cur === "KRW" ? `${num(v)}원` : `${cur === "USD" ? "$" : ""}${num(v, 2)}`;
const ratio = (v, d = 1) => v == null ? "-" : `${(v * 100).toFixed(d)}%`;
const times = (v) => v == null ? "-" : `${v.toFixed(1)}배`;

function pfStats(p, cur) {
  const s = p.stats || {};
  const tiles = [
    ["시가총액", moneyShort(s.market_cap, cur), "회사 전체 가격"],
    ["PER", times(s.per), "주가 ÷ 1주당 순이익 (낮을수록 이익 대비 싸다)"],
    ["선행 PER", times(s.fwd_per), "내년 예상 이익 기준"],
    ["PBR", times(s.pbr), "주가 ÷ 1주당 순자산"],
    ["EPS", s.eps == null ? "-" : priceCur(s.eps, cur), "1주당 순이익 (최근 4분기)"],
    ["배당수익률", s.div_yield == null ? "-" : ratio(s.div_yield, 2), s.div_rate ? `1주당 연 ${priceCur(s.div_rate, cur)}` : "연간 배당 ÷ 주가"],
    ["베타", s.beta == null ? "-" : s.beta.toFixed(2), "시장이 1% 움직일 때 평균 움직임"],
    ["평균 거래량", s.avg_volume == null ? "-" : num(s.avg_volume), "최근 3개월 하루 평균"],
    ...(s.foreign_rate != null ? [["외국인 소진율", ratio(s.foreign_rate), "외국인 한도 대비 보유"]] : []),
  ].filter((t) => t[1] !== "-");
  const known = new Set(["시총", "PER", "PBR", "EPS", "배당수익률", "52주 최고", "52주 최저", "외인소진율", "추정PER"]);
  const extra = (p.stats_text || []).filter((r) => !known.has(r.label)).map((r) => `<span class="chip">${esc(r.label)} <b>${esc(r.value)}</b></span>`).join(" ");
  if (!tiles.length && !extra && s.high52 == null) return "";
  return `<div class="kv-grid">${tiles.map(([l, v, h]) => `<div class="kvt" title="${esc(h)}"><div class="xs muted">${l}</div><div class="num b">${esc(v)}</div></div>`).join("")}</div>
    ${range52(s, cur)}${extra ? `<div class="chips" style="margin-top:8px">${extra}</div>` : ""}`;
}

function pfAnalyst(p, cur) {
  const a = p.analyst || {}, s = p.stats || {};
  if (!a.target_mean && !a.rating && !(a.reports || []).length) return "";
  let bar = "";
  if (a.target_low && a.target_high && a.target_high > a.target_low) {
    const lo = Math.min(a.target_low, s.price || a.target_low), hi = Math.max(a.target_high, s.price || a.target_high);
    const x = (v) => `${((v - lo) / (hi - lo)) * 100}%`;
    bar = `<div class="tg-bar"><div class="tg-rng" style="left:${x(a.target_low)};right:calc(100% - ${x(a.target_high)})"></div>
      ${s.price ? `<i class="now" style="left:${x(s.price)}" title="현재가"></i>` : ""}<i class="avg" style="left:${x(a.target_mean)}" title="평균 목표가"></i></div>
      <div class="r52-l xs"><span>최저 ${esc(priceCur(a.target_low, cur))}</span><span>최고 ${esc(priceCur(a.target_high, cur))}</span></div>`;
  }
  const dist = a.distribution;
  const tot = dist ? Object.values(dist).reduce((x, y) => x + y, 0) : 0;
  const DL = { strongBuy: ["강력매수", "#ef4444"], buy: ["매수", "#f97316"], hold: ["보유", "#94a3b8"], sell: ["매도", "#3b82f6"], strongSell: ["강력매도", "#1d4ed8"] };
  const distBar = tot ? `<div class="dist">${Object.entries(dist).filter(([, v]) => v).map(([k, v]) => `<span style="flex:${v};background:${DL[k][1]}" title="${DL[k][0]} ${v}"></span>`).join("")}</div>
    <div class="xs muted">${Object.entries(dist).filter(([, v]) => v).map(([k, v]) => `${DL[k][0]} ${v}`).join(" · ")}</div>` : "";
  const reps = (a.reports || []).map((r) => `<tr><td class="dim small">${esc(r.date || "")}</td><td style="white-space:normal">${esc(r.title)}</td><td class="small muted">${esc(r.broker || "")}</td></tr>`).join("");
  return `<div class="tg-top"><div><div class="xs muted">평균 목표가${a.n ? ` · ${Math.round(a.n)}명` : ""}</div><div class="big num">${a.target_mean ? esc(priceCur(a.target_mean, cur)) : "-"}</div>
      ${a.upside != null ? `<div class="${a.upside >= 0 ? "up" : "down"} b">현재가 대비 ${a.upside >= 0 ? "+" : ""}${(a.upside * 100).toFixed(1)}%</div>` : ""}</div>
    ${a.rating ? `<div class="r"><div class="xs muted">투자의견</div><div class="rating">${esc(a.rating)}</div>${a.rec_mean ? `<div class="xs dim">평균 ${a.rec_mean.toFixed(2)} — ${esc(a.scale || "")}</div>` : ""}</div>` : ""}</div>
    ${bar}${distBar}
    ${reps ? `<div class="small muted" style="margin:10px 0 4px">최근 증권사 리포트</div><table class="tight"><tbody>${reps}</tbody></table>` : ""}
    <div class="xs dim" style="margin-top:8px">증권사 애널리스트 의견입니다. 이 플랫폼의 AI 판단과는 별개입니다${a.as_of ? ` · ${esc(a.as_of)} 기준` : ""}.</div>`;
}

function barsSvg(rows, key, cur, color) {
  const vals = rows.map((r) => r[key]).filter((v) => v != null);
  if (vals.length < 2) return "";
  const mx = Math.max(...vals.map(Math.abs)) || 1, W = 100 / rows.length;
  return `<svg viewBox="0 0 100 44" preserveAspectRatio="none" class="qbars">${rows.map((r, i) => {
    const v = r[key]; if (v == null) return "";
    const h = Math.abs(v) / mx * 36;
    return `<rect x="${i * W + W * 0.18}" width="${W * 0.64}" y="${v >= 0 ? 38 - h : 38}" height="${Math.max(h, 0.5)}" rx="1" fill="${v >= 0 ? color : "var(--down)"}"><title>${r.period} ${moneyShort(v, cur)}</title></rect>`;
  }).join("")}</svg><div class="qlab xs dim">${rows.map((r) => `<span>${esc(r.period.slice(2))}</span>`).join("")}</div>`;
}

function pfEarnings(p, cur) {
  const h = p.earnings_history || [], q = p.quarterly || [];
  if (!h.length && !q.length) return "";
  const hist = h.slice().reverse().map((x) => `<tr><td class="small">${esc(x.date)}</td><td class="r num">${x.eps_estimate == null ? "-" : x.eps_estimate.toFixed(2)}</td><td class="r num b">${x.eps_actual.toFixed(2)}</td>
    <td class="r">${x.surprise_pct == null ? "" : `<span class="chip ${x.beat ? "pos" : "neg"}">${x.beat ? "상회" : "하회"} ${x.surprise_pct >= 0 ? "+" : ""}${x.surprise_pct.toFixed(1)}%</span>`}</td></tr>`).join("");
  const beats = h.filter((x) => x.beat != null);
  const last = q[q.length - 1];
  const margin = last && last.revenue && last.op_income != null ? last.op_income / last.revenue : null;
  return `${q.length ? `<div class="q-grid">
      <div><div class="xs muted">분기 매출 ${last?.revenue ? `· 최근 <b>${esc(moneyShort(last.revenue, cur))}</b>` : ""}</div>${barsSvg(q, "revenue", cur, "var(--accent)")}</div>
      <div><div class="xs muted">영업이익 ${margin != null ? `· 이익률 <b>${(margin * 100).toFixed(1)}%</b>` : ""}</div>${barsSvg(q, "op_income", cur, "var(--good)")}</div></div>` : ""}
    ${hist ? `<div class="small muted" style="margin:10px 0 4px">EPS 서프라이즈 ${beats.length ? `— 최근 ${beats.length}번 중 <b>${beats.filter((x) => x.beat).length}번</b> 예상 상회` : ""}</div>
      <table class="tight"><thead><tr><th>발표</th><th class="r">예상</th><th class="r">실제</th><th class="r"></th></tr></thead><tbody>${hist}</tbody></table>` : ""}`;
}

function pfFinance(p, cur) {
  const s = p.stats || {};
  const tone = (v, good, bad) => v == null ? "" : v >= good ? "good" : v <= bad ? "bad-t" : "";
  const rows = [
    ["매출 성장률 (전년 대비)", s.rev_growth, ratio(s.rev_growth), tone(s.rev_growth, 0.1, 0)],
    ["이익 성장률 (전년 대비)", s.earn_growth, ratio(s.earn_growth), tone(s.earn_growth, 0.1, 0)],
    ["영업이익률", s.op_margin, ratio(s.op_margin), tone(s.op_margin, 0.15, 0.03)],
    ["순이익률", s.profit_margin, ratio(s.profit_margin), tone(s.profit_margin, 0.1, 0)],
    ["ROE (자기자본이익률)", s.roe, ratio(s.roe), tone(s.roe, 0.15, 0.05)],
    ["부채비율 (부채 ÷ 자본)", s.debt_to_equity, ratio(s.debt_to_equity, 0), s.debt_to_equity == null ? "" : s.debt_to_equity <= 0.5 ? "good" : s.debt_to_equity >= 2 ? "bad-t" : ""],
    ["잉여현금흐름 (FCF)", s.fcf, moneyShort(s.fcf, cur), tone(s.fcf, 1, -1)],
    ["현금 / 총부채", s.cash, `${moneyShort(s.cash, cur)} / ${moneyShort(s.debt, cur)}`, ""],
    ["매출 (최근 4분기)", s.revenue, moneyShort(s.revenue, cur), ""],
    ["기관 보유 비율", s.institutions, ratio(s.institutions), ""],
    ["공매도 비율 (유통주식 대비)", s.short_float, ratio(s.short_float), s.short_float >= 0.1 ? "warn-t" : ""],
  ].filter((r) => r[1] != null);
  if (!rows.length) return "";
  return `<table class="tight"><tbody>${rows.map(([l, , v, t]) => `<tr><td class="muted">${l}</td><td class="r num b ${t}">${esc(v)}</td></tr>`).join("")}</tbody></table>
    <div class="xs dim" style="margin-top:6px">초록 = 양호 · 빨강 = 주의 (업종마다 기준이 다르니 참고용)</div>`;
}

function pfCompany(p, name) {
  const c = p.company || {};
  if (!c.sector && !c.summary && !c.industry) return "";
  const facts = [["섹터", c.sector], ["산업", c.industry], ["직원 수", c.employees ? `${num(c.employees)}명` : null],
    ["국가", c.country], ["거래소", c.exchange]].filter((x) => x[1]);
  const web = safeUrl(c.website);
  return `<div class="chips">${facts.map(([k, v]) => `<span class="chip">${k} <b>${esc(v)}</b></span>`).join(" ")}${web ? ` <a class="chip link" href="${web}" target="_blank" rel="noopener">웹사이트 ↗</a>` : ""}</div>
    ${c.summary ? `<div class="co-sum small muted" id="co-sum">${esc(c.summary)}</div><div style="display:flex;gap:8px;margin-top:8px"><button class="btn-sm" id="co-more">더 보기</button>
    <button class="btn-sm primary" onclick="askAI('${esc((name || "").replace(/'/g, ""))} 회사 소개를 한국어로 쉽게 요약하고, 투자 전에 확인할 점을 알려줘')">${ICONS.chat} AI 한국어 요약</button></div>` : ""}`;
}

function pfNews(p, dbNews) {
  const items = [
    ...(dbNews || []).map((n) => ({ ...n, src: n.source || "RSS" })),
    ...(p.news || []).map((n) => ({ ...n, src: n.source })),
  ];
  const seen = new Set();
  const list = items.filter((n) => { const k = (n.title || "").slice(0, 40).toLowerCase(); if (seen.has(k)) return false; seen.add(k); return true; })
    .sort((a, b) => String(b.ts || "").localeCompare(String(a.ts || ""))).slice(0, 15);
  if (!list.length) return "";
  return `<div class="news-list">${list.map((n) => { const u = safeUrl(n.url); return `<div class="pfn"><div style="flex:1;min-width:0">
    <div class="t">${u ? `<a href="${u}" target="_blank" rel="noopener">${esc(n.title)}</a>` : esc(n.title)}</div>
    <div class="xs dim">${esc(n.src || "")} · ${n.ts ? time(n.ts, true) : ""}</div></div>${n.sentiment != null ? sentChip(n.sentiment) : ""}</div>`; }).join("")}</div>`;
}

async function loadProfile(sym, name, a, refresh = false) {
  const box = $("#pf-sections"), evBox = $("#pf-events"), stBox = $("#pf-stats");
  if (!box) return;
  let p = !refresh && PF[sym] && Date.now() - PF[sym].at < 300e3 ? PF[sym].p : null;
  if (!p) {
    try { p = await api(`/api/profile?symbol=${encodeURIComponent(sym)}${refresh ? "&refresh=1" : ""}`); PF[sym] = { p, at: Date.now() }; } catch (e) { p = { errors: [String(e.message || e)], events: [], sources: [] }; }
  }
  if (S.view !== "analysis" || (S.symbol && S.symbol !== sym)) return;  // 그 사이 다른 화면·종목으로 이동
  const cur = p.currency || a.currency || (isKR(sym) ? "KRW" : "USD");
  if (evBox) evBox.innerHTML = pfEvents(p);
  if (stBox) stBox.innerHTML = pfStats(p, cur) || empty("지표를 받지 못했습니다");
  const secs = [
    ["애널리스트 전망", pfAnalyst(p, cur)], ["실적", pfEarnings(p, cur)],
    ["재무 건전성", pfFinance(p, cur)], ["기업 정보", pfCompany(p, name)],
  ].filter((x) => x[1]);
  const news = pfNews(p, a.news);
  const src = (p.sources || []).length ? `출처: ${p.sources.map(esc).join(" · ")}${p.fetched_at ? ` · ${time(p.fetched_at, true)} 기준` : ""}${p.stale ? " · <span class='warn-t'>최신 갱신 실패 — 이전 자료</span>" : ""}` : `<span class="warn-t">외부 데이터 소스에 연결하지 못했습니다</span> — 30분 뒤 자동으로 다시 시도합니다`;
  const errs = (p.errors || []).length ? `<details class="xs dim" style="display:inline"><summary>세부</summary>${p.errors.map(esc).join("<br>")}</details>` : "";
  box.innerHTML = `${secs.length ? `<div class="grid g-2">${secs.map(([t, b]) => card(t, b)).join("")}</div>` : ""}
    ${news ? card("관련 뉴스", news, `<span class="xs dim">${(a.news || []).length ? "국내 RSS" : ""}${(a.news || []).length && (p.news || []).length ? " + " : ""}${(p.news || []).length ? "Yahoo" : ""}</span>`) : ""}
    <div class="pf-src xs dim">${src} ${errs} <button class="btn-sm" id="pf-refresh">새로 고침</button></div>`;
  const more = $("#co-more");
  if (more) more.onclick = () => { $("#co-sum").classList.toggle("open"); more.textContent = $("#co-sum").classList.contains("open") ? "접기" : "더 보기"; };
  $("#pf-refresh").onclick = (e) => { e.target.disabled = true; e.target.textContent = "불러오는 중…"; loadProfile(sym, name, a, true); };
}
