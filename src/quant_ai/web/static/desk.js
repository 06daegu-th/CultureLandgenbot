/* v13 화면: 매매 준비 · 데이터 기준 · 이벤트 캘린더 · 실제 예측력 · 체결/증권사 검증 · 내 저널 · 리스크 2.0 · 종목 데스크 */
"use strict";

// ------------------------------------------------------------ 공통: 기준 시각 (AS-OF)
const KST_FMT = new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false });
function kst(ts, withTime = true) {
  if (!ts) return "-";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return String(ts);
  const s = KST_FMT.format(d);
  return withTime ? `${s} KST` : s.slice(0, 10);
}
const ST_CLS = { fresh: "ok", stale: "warn", old: "bad", none: "bad" };
function asOf(labelOrTs, status = "fresh", prefix = "기준") {
  if (!labelOrTs) return `<span class="asof s-none" title="기준 시각 없음">${prefix} 없음</span>`;
  const lbl = /KST$|^\d{4}-\d{2}-\d{2}$/.test(labelOrTs) ? labelOrTs : kst(labelOrTs);
  return `<span class="asof s-${esc(status)}" title="이 데이터의 기준 시각">${prefix} ${esc(lbl)}</span>`;
}
const GATE_ICON = { green: "🟢", yellow: "🟡", red: "🔴", na: "⚪" };
const RD_LABEL = { READY: "READY · 매수 가능", CAUTION: "CAUTION · 주의", NOT_READY: "NOT READY · 신규 매수 차단" };
const RD_CLS = { READY: "rd-ready", CAUTION: "rd-caution", NOT_READY: "rd-not" };

// 상단 '데이터 기준' 칩 — 1분마다 갱신, 누르면 매매 준비 화면
async function refreshAsOfChip() {
  const el = $("#asof-chip");
  if (!el) return;
  try {
    const f = await api("/api/freshness");
    const it = f.items || {};
    const bar = it.bar_kr || it.bar_us || {};
    el.className = `asof-chip s-${f.status}`;
    el.innerHTML = `<span class="dot"></span>데이터 ${esc(bar.label || "-")}${bar.age && bar.status !== "fresh" ? ` · ${esc(bar.age)}` : ""}`;
    el.title = Object.values(it).map((v) => `${v.name}: ${v.label || "없음"} (${v.age || "-"})`).join("\n");
  } catch { el.textContent = "데이터 기준 -"; }
}
function initAsOfChip() {
  const tr = document.querySelector(".top-right");
  if (!tr || $("#asof-chip")) return;
  const a = document.createElement("a");
  a.id = "asof-chip"; a.href = "#readiness"; a.className = "asof-chip";
  a.textContent = "데이터 기준 …";
  tr.insertBefore(a, tr.firstChild);
  refreshAsOfChip();
  setInterval(refreshAsOfChip, 60_000);
}

// 홈·관제실 상단 준비 상태 띠
function readinessStrip(rd) {
  if (!rd || !rd.status) return `<a class="rd-strip rd-none" href="#readiness"><b>매매 준비 점검</b><span class="small muted">아직 점검 전 — 눌러서 지금 점검</span></a>`;
  const chips = (rd.checks || []).map((c) => `<span class="rd-gate g-${esc(c.status)}" title="${esc(c.detail)}">${GATE_ICON[c.status] || ""} ${esc(c.key)}</span>`).join("");
  return `<a class="rd-strip ${RD_CLS[rd.status] || ""}" href="#readiness"><b>${esc(RD_LABEL[rd.status] || rd.status)}</b><span class="rd-gates">${chips}</span><span class="xs muted">${esc(rd.as_of || kst(rd.at))}</span></a>`;
}

// ------------------------------------------------------------ 작은 SVG 차트
function dLine(series, { w = 640, h = 180, bands = [], yfmt = (v) => v.toFixed(2), zero = null } = {}) {
  const all = series.flatMap((s) => s.data.filter((v) => v != null)).concat(bands.map((b) => b.y));
  if (!all.length) return empty("데이터 없음");
  let lo = Math.min(...all), hi = Math.max(...all);
  if (zero != null) { lo = Math.min(lo, zero); hi = Math.max(hi, zero); }
  if (hi === lo) { hi += 1; lo -= 1; }
  const pad = 26, X = (i, n) => pad + (i / Math.max(n - 1, 1)) * (w - pad - 6), Y = (v) => 8 + (1 - (v - lo) / (hi - lo)) * (h - 24);
  const lines = series.map((s) => {
    const pts = s.data.map((v, i) => v == null ? null : `${X(i, s.data.length).toFixed(1)},${Y(v).toFixed(1)}`).filter(Boolean);
    return `<polyline fill="none" stroke="${s.color || "var(--accent)"}" stroke-width="${s.width || 1.8}" points="${pts.join(" ")}"${s.dash ? ` stroke-dasharray="${s.dash}"` : ""}/>`;
  }).join("");
  const bl = bands.map((b) => `<line x1="${pad}" x2="${w - 6}" y1="${Y(b.y).toFixed(1)}" y2="${Y(b.y).toFixed(1)}" stroke="${b.color}" stroke-dasharray="4 4" stroke-width="1"/><text x="${w - 8}" y="${(Y(b.y) - 4).toFixed(1)}" text-anchor="end" class="svg-lbl" fill="${b.color}">${esc(b.label)}</text>`).join("");
  const zl = zero != null ? `<line x1="${pad}" x2="${w - 6}" y1="${Y(zero).toFixed(1)}" y2="${Y(zero).toFixed(1)}" stroke="var(--line-2)" stroke-width="1"/>` : "";
  return `<svg class="mini-line" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><text x="2" y="12" class="svg-lbl">${esc(yfmt(hi))}</text><text x="2" y="${h - 14}" class="svg-lbl">${esc(yfmt(lo))}</text>${zl}${bl}${lines}</svg>`;
}
function dBars(vals, labels, { w = 520, h = 150, fmt = (v) => (v * 100).toFixed(1) + "%" } = {}) {
  if (!vals || !vals.length) return empty("데이터 없음");
  const m = Math.max(...vals.map(Math.abs), 1e-9), bw = (w - 20) / vals.length, mid = h / 2;
  return `<svg class="mini-bars" viewBox="0 0 ${w} ${h}">${vals.map((v, i) => {
    const bh = Math.abs(v) / m * (mid - 16), x = 10 + i * bw + 2, y = v >= 0 ? mid - bh : mid;
    return `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${(bw - 4).toFixed(1)}" height="${Math.max(bh, 1).toFixed(1)}" rx="2" fill="${v >= 0 ? "var(--up)" : "var(--down)"}" opacity=".85"><title>${esc(labels?.[i] ?? i + 1)}: ${fmt(v)}</title></rect>
      <text x="${(x + (bw - 4) / 2).toFixed(1)}" y="${h - 2}" text-anchor="middle" class="svg-lbl">${esc(labels?.[i] ?? i + 1)}</text>`;
  }).join("")}<line x1="10" x2="${w - 10}" y1="${mid}" y2="${mid}" stroke="var(--line-2)"/></svg>`;
}
const pill = (status, text) => `<span class="st-pill st-${esc(status)}">${esc(text)}</span>`;
const kv = (k, v, cls = "") => `<div class="kvt"><div class="xs muted">${k}</div><div class="num b ${cls}">${v}</div></div>`;
async function deskRun(name, btn, params = {}) {
  if (btn) { btn.disabled = true; btn.dataset.t = btn.textContent; btn.textContent = "실행 중…"; }
  try {
    await api("/api/action", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, ...params }) });
    for (let i = 0; i < 90; i++) {
      await new Promise((r) => setTimeout(r, 1000));
      const st = await api(`/api/action?name=${encodeURIComponent(name)}`);
      if (!st.running) { if (st.error && typeof toast === "function") toast({ title: "실패", body: st.error, level: "bad" }); break; }
    }
  } catch (e) { if (typeof toast === "function") toast({ title: "실패", body: e.message, level: "bad" }); }
  if (btn) { btn.disabled = false; btn.textContent = btn.dataset.t; }
  render();
}
function bindRun(root = document) {
  root.querySelectorAll("[data-run]").forEach((b) => b.onclick = () => deskRun(b.dataset.run, b));
}

// ------------------------------------------------------------ 뷰: 매매 준비 · 데이터
async function viewReadiness(el) {
  const [r, p] = await Promise.all([api("/api/readiness"), api("/api/pipeline")]);
  const fr = r.freshness || {};
  const gates = (r.checks || []).map((c) => `<div class="gate g-${esc(c.status)}"><div class="gate-h"><span class="gate-ic">${GATE_ICON[c.status] || ""}</span><b>${esc(c.key)}</b><span class="small muted">${esc(c.title)}</span></div><div class="small">${esc(c.detail)}</div>${(c.items || []).length > 1 ? `<ul class="plain xs muted">${c.items.slice(0, 5).map((x) => `<li>· ${esc(x)}</li>`).join("")}</ul>` : ""}</div>`).join("");
  const rows = Object.values(fr.items || {}).map((v) => `<tr><td>${esc(v.name || v.kind)}</td><td class="mono small">${esc(v.label || "-")}</td><td>${pill(ST_CLS[v.status] || "warn", v.age || "없음")}</td><td class="xs dim">${v.n_symbols ? `${v.n_symbols}종목 · 가장 늦은 ${esc(v.oldest)}` : ""}</td></tr>`).join("");
  const src = (p.sources || []).map((s) => `<tr><td>${esc(s.source)}</td><td>${pill({ ok: "ok", degraded: "warn", down: "bad", none: "none" }[s.status], { ok: "정상", degraded: "간헐 실패", down: "연속 실패", none: "기록 없음" }[s.status])}</td><td class="r num">${s.runs}</td><td class="r num">${s.fails}</td><td class="small mono">${s.last_ok ? kst(s.last_ok) : "-"}</td><td class="xs dim" style="white-space:normal">${esc(s.last_error || "")}</td></tr>`).join("");
  const rec = p.recovery || {};
  const hist = (r.history || []).slice(-24);
  el.innerHTML = `
  <div class="card rd-hero ${RD_CLS[r.status] || ""}">
    <div class="rd-top"><div><div class="xs muted">Trading Readiness — 지금 새로 사도 되는가 · 7개 관문 · ${esc(r.mode || "")} 장부${r.gate ? " · 게이트 적용 중" : " · 게이트 미적용(기록만)"}</div>
      <h2>${esc(RD_LABEL[r.status] || "-")}</h2>
      <div class="small muted">${asOf(r.as_of || r.at)} · ${r.real_money ? "실제 돈 계좌" : "가상/모의 계좌"}</div>
      ${(r.blockers || []).length ? `<div class="veto" style="margin-top:8px">⛔ ${r.blockers.map(esc).join("<br>⛔ ")}</div>` : ""}
      ${(r.truth_bad || []).length ? `<div class="small" style="margin-top:6px">Truth Center 가 찾은 문제 ${r.truth_bad.length}건이 관문에 반영됨 → <a href="#truth">자세히</a></div>` : ""}</div>
      <div class="vf-act"><button class="btn-sm primary" data-run="readiness">지금 점검</button><button class="btn-sm" data-run="event_calendar">캘린더 갱신</button></div></div>
    <div class="rd-hist">${hist.map((h) => `<span class="rd-dot ${RD_CLS[h.status] || ""}" title="${esc(kst(h.at))} ${esc(h.status)} ${esc((h.reds || []).join(","))}"></span>`).join("")}<span class="xs dim">최근 점검 이력</span></div>
  </div>
  <div class="gates">${gates}</div>
  <div class="grid g-2">
    ${card(`데이터 기준 시각 <span class="small dim">모든 데이터의 AS-OF · 일봉은 거래소 휴장일을 반영해 '밀린 거래일'로</span>`, rows ? `<div class="scroll"><table class="tight"><thead><tr><th>데이터</th><th>기준 시각</th><th>상태</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>` : empty(), asOf(fr.now_label, "fresh", "지금"))}
    ${card("재시작 복구 · 심장박동", `<div class="kv-grid">${kv("마지막 심장박동", p.heartbeat?.at ? kst(p.heartbeat.at) : "-")}${kv("직전 정지 시간", rec.downtime_s == null ? "-" : `${(rec.downtime_s / 60).toFixed(0)}분`)}${kv("DB 점검", rec.db ? (rec.db.ok ? "정상" : "문제") : "-", rec.db && !rec.db.ok ? "bad-t" : "")}${kv("정리한 멈춘 작업", rec.stale_jobs ?? "-")}</div>
      ${(rec.catch_up || []).length ? `<div class="small" style="margin-top:8px">따라잡기: ${rec.catch_up.map((c) => `${esc(c.step)} ${c.ok ? "✓" : "✗"}`).join(" · ")}</div>` : ""}
      <div class="xs dim" style="margin-top:8px">서버가 꺼졌다 켜지면: 멈춘 작업 정리 → 놓친 채점·봉인·평가 따라잡기 → 준비 점검. 가상 장부의 미완료 주문은 다음 매매 사이클이 먼저 정리합니다.</div>`)}
  </div>
  ${card("데이터 파이프라인 · 소스 상태 <span class='small dim'>최근 3일 작업 기록 · 3회 연속 실패 = 중단</span>", src ? `<div class="scroll"><table class="tight"><thead><tr><th>소스</th><th>상태</th><th class="r">실행</th><th class="r">실패</th><th>마지막 성공</th><th>마지막 오류</th></tr></thead><tbody>${src}</tbody></table></div>` : empty())}
  ${fxCard(p.fx)}`;
  bindRun(el);
}

function fxCard(fx) {
  if (!fx || fx.insufficient || fx.error) return card("미국 장부 · 원화 환산", empty(fx?.message || fx?.error || "기록 없음"));
  const c = fx.curve || [];
  return card(`미국 장부 · 원화 환산 성과 <span class="small dim">${esc(fx.start)} ~ ${esc(fx.end)} · 환전 비용 제외</span>`, `
    <div class="kv-grid">${kv("달러 기준", P(fx.usd_return, 2), fx.usd_return >= 0 ? "up" : "down")}${kv("환율 (원/달러)", P(fx.fx_return, 2), fx.fx_return >= 0 ? "up" : "down")}${kv("원화 기준", P(fx.krw_return, 2), fx.krw_return >= 0 ? "up" : "down")}${kv("환율", `${num(fx.fx_start, 1)} → ${num(fx.fx_end, 1)}`)}</div>
    <div class="small" style="margin:8px 0">${esc(fx.read)}</div>
    ${dLine([{ data: c.map((x) => x.usd), color: "var(--accent)" }, { data: c.map((x) => x.krw), color: "var(--up)" }, { data: c.map((x) => x.fx), color: "var(--muted)", dash: "3 3", width: 1.2 }], { zero: 0, yfmt: (v) => (v * 100).toFixed(1) + "%" })}
    <div class="xs dim">파랑 = 달러 · 빨강 = 원화 · 점선 = 환율 변화</div>`);
}

// ------------------------------------------------------------ 뷰: 이벤트 캘린더 2.0
const CAL_ICON = { bok: "🇰🇷", fomc: "🏦", cpi: "📈", nfp: "👷", gdp: "📊", pce: "🧾", retail: "🛒", earnings: "💼", ex_div: "💰", div_pay: "💵", options_expiry: "🎯", quad_witching: "🎯", index_rebalance: "⚖️", holiday: "🛑", half_day: "⏰", export: "🚢", disclosure: "📄", custom: "📌" };
async function viewCalendar(el) {
  const c = await api("/api/calendar");
  const f = S.calFilter || "all";
  const mk = S.calMarket || "all";
  const evs = (c.events || []).filter((e) => e.d_day >= (S.calPast ? -14 : 0))
    .filter((e) => f === "all" || (f === "stock" ? !!e.symbol : f === "macro" ? ["bok", "fomc", "cpi", "nfp", "gdp", "pce", "retail", "export"].includes(e.kind) : f === "deriv" ? ["options_expiry", "quad_witching", "index_rebalance"].includes(e.kind) : ["holiday", "half_day"].includes(e.kind)))
    .filter((e) => mk === "all" || e.market === mk || e.market === "GLOBAL");
  const byDay = {};
  evs.forEach((e) => (byDay[e.date] = byDay[e.date] || []).push(e));
  const tabs = (id, cur, opts) => `<div class="tabs" id="${id}">${Object.entries(opts).map(([k, v]) => `<button data-k="${k}" class="${cur === k ? "on" : ""}">${v}</button>`).join("")}</div>`;
  const list = Object.entries(byDay).map(([d, xs]) => `<div class="cal-day ${xs[0].d_day === 0 ? "today" : ""}"><div class="cal-date"><b>${esc(d.slice(5))}</b><span class="xs muted">${esc(xs[0].d_label)}</span></div><div class="cal-items">${xs.map((e) => `
      <div class="cal-it imp-${e.importance >= 0.8 ? "h" : e.importance >= 0.5 ? "m" : "l"}"><span class="cal-ic">${CAL_ICON[e.kind] || "•"}</span><span class="chip xs">${esc(e.label)}</span>
      ${e.symbol ? `<a href="#analysis/${esc(e.symbol)}"><b>${esc(e.title)}</b></a>` : `<b>${esc(e.title)}</b>`}
      <span class="xs dim">${esc(e.market)}${e.time ? " · " + esc(e.time) : ""} · ${esc(e.source)}${e.estimated ? " · <span class='warn-t'>추정</span>" : ""}</span></div>`).join("")}</div></div>`).join("");
  const risk = (c.risk || []).slice(0, 20).map((r) => `<tr><td><a href="#analysis/${esc(r.symbol)}"><b>${esc(r.name || r.symbol)}</b></a></td><td>${hbar(r.score, 1, r.level === "high" ? "bad" : r.level === "medium" ? "warn" : "")} <span class="num xs">${r.score.toFixed(2)}</span></td>
    <td class="small">${r.next ? `${esc(r.next.title)} <span class="dim">${esc(r.next.d_label)}</span>` : (r.market_events || [])[0] ? `<span class="muted">시장: ${esc(r.market_events[0].title)} <span class="dim">${esc(r.market_events[0].d_label)}</span></span>` : "-"}</td><td class="r num">${r.expected_move ? "±" + R(r.expected_move) : "-"}</td>
    <td>${r.buy_multiplier < 1 ? `<span class="chip warn">매수 ×${r.buy_multiplier}</span> <span class="xs">${esc(r.reason)}</span>` : '<span class="xs dim">제한 없음</span>'}</td></tr>`).join("");
  const im = c.impact || {};
  const mkRows = (im.market_kr || []).map((x) => `<tr><td>${CAL_ICON[x.kind] || ""} ${esc(({ bok_change: "기준금리 변경", fomc: "FOMC", options_expiry: "옵션 만기", quad_witching: "동시만기", nfp: "미국 고용" })[x.kind] || x.kind)}</td><td class="r num">${x.n}</td><td class="r num ${x.vs_normal > 1.2 ? "warn-t" : ""}">×${x.vs_normal ?? "-"}</td><td class="r">${P(x.mean, 2)}</td><td class="r">${P(x.worst, 2)}</td></tr>`).join("");
  const stRows = (im.stock || []).map((x) => `<tr><td>${esc(x.kind)}</td><td class="r num">${x.n}</td><td class="r ${(x.mean || 0) >= 0 ? "up" : "down"}">${P(x.mean, 2)}</td><td class="r num">${R(x.abs_mean, 2)}</td><td class="r num">${x.t ?? "-"}</td><td>${x.significant ? '<span class="chip ok">우연 아님</span>' : '<span class="chip">불확실</span>'}</td></tr>`).join("");
  const pa = im.prediction || {};
  el.innerHTML = marketVolCard(c) + `
  ${card(`이벤트 캘린더 <span class="small dim">종목 · 시장 · 경제 · 실적 · 배당 · 공시 · 옵션 만기 · 휴장 — 출처와 '추정' 여부를 함께 표시</span>`,
    `<div class="cal-bar">${tabs("cal-f", f, { all: "전체", stock: "종목", macro: "경제", deriv: "만기·지수", mkt: "휴장" })}${tabs("cal-m", mk, { all: "전체 시장", KR: "한국", US: "미국" })}<label class="xs"><input type="checkbox" id="cal-past" ${S.calPast ? "checked" : ""}> 지난 2주 포함</label></div>
    <div class="cal-list">${list || empty("해당 이벤트 없음")}</div>
    <div class="xs dim" style="margin-top:8px">휴장일: ${esc(c.calendar_source || "")} · FOMC: 연준 공표 일정 · CPI/고용: FRED_API_KEY 가 있으면 공식 발표일(없으면 첫째 금요일 추정) · 사용자 일정: artifacts/events.json</div>`,
    `${asOf(c.as_of || c.at)} <button class="btn-sm" data-run="event_calendar">갱신</button>`)}
  ${card(`보유·관심 종목 이벤트 위험 <span class="small dim">가까운 큰 이벤트일수록 점수↑ · 실적 D-1 이내 매수 ×0.5 · D-3 이내 ×0.75 (게이트: ${esc(c.gate || "reduce")})</span>`, risk ? `<div class="scroll"><table class="tight"><thead><tr><th>종목</th><th>위험</th><th>다음 이벤트</th><th class="r">예상 변동</th><th>매수 제한</th></tr></thead><tbody>${risk}</tbody></table></div>` : empty("보유·관심 종목이 없습니다"))}
  <div class="grid g-2">
    ${card("시장 이벤트의 실제 영향 <span class='small dim'>그날 KOSPI 변동폭 ÷ 평소</span>", mkRows ? `<table class="tight"><thead><tr><th>이벤트</th><th class="r">n</th><th class="r">변동폭</th><th class="r">평균</th><th class="r">최악</th></tr></thead><tbody>${mkRows}</tbody></table>` : empty("지수 이력이 부족합니다"), im.at ? asOf(im.at) : `<button class="btn-sm" data-run="event_impact">계산</button>`)}
    ${card("종목 이벤트 반응 <span class='small dim'>베타 조정 비정상 수익률 [-1,+1]</span>", stRows ? `<div class="scroll"><table class="tight"><thead><tr><th>유형</th><th class="r">n</th><th class="r">평균</th><th class="r">|평균|</th><th class="r">t</th><th></th></tr></thead><tbody>${stRows}</tbody></table></div>` : empty("공시·실적 이력이 부족합니다"))}
  </div>
  ${card("예측 오차의 이벤트 귀속 <span class='small dim'>보유 기간 안에 큰 이벤트가 끼어 있던 예측 vs 아닌 예측</span>", pa.n ? `<div class="kv-grid">${kv("이벤트 있던 예측 적중", `${R(pa.with_event?.hit_rate)} <span class="xs dim">n=${pa.with_event?.n}</span>`)}${kv("이벤트 없던 예측 적중", `${R(pa.without_event?.hit_rate)} <span class="xs dim">n=${pa.without_event?.n}</span>`)}${kv("빗나간 예측 중 이벤트 낀 비율", R(pa.miss_share_with_event))}</div>
    ${(pa.by_kind || []).length ? `<div class="small" style="margin-top:8px">${pa.by_kind.map((k) => `${esc(k.kind)} ${R(k.hit_rate)} (n=${k.n})`).join(" · ")}</div>` : ""}` : empty("채점된 예측이 아직 없습니다"))}`;
  el.querySelectorAll("#cal-f button").forEach((b) => b.onclick = () => { S.calFilter = b.dataset.k; render(); });
  el.querySelectorAll("#cal-m button").forEach((b) => b.onclick = () => { S.calMarket = b.dataset.k; render(); });
  const pc = $("#cal-past");
  if (pc) pc.onchange = () => { S.calPast = pc.checked; render(); };
  bindRun(el);
}

// ------------------------------------------------------------ 뷰: 실제 예측력
async function viewPower(el) {
  const p = await api("/api/power");
  const fw = p.forward || {}, reg = p.prereg || {}, st = p.study || {}, dec = p.decay || {}, dr = p.drift || {};
  const cls = { pass: "good", fail: "bad-t", running: "warn-t", done: "good", none: "dim" };
  const gradeCls = { strong: "good", partial: "warn-t", none: "bad-t" };
  const layers = (p.layers || []).map((l) => `<div class="layer"><div class="layer-k">${esc(l.key)}</div><div><b>${esc(l.title)}</b><div class="small ${l.key === "A" && st.grade ? gradeCls[st.grade] : cls[l.status] || ""}">${esc(l.detail)}</div></div></div>`).join("");
  const path = fw.path || [];
  const part = (x, name) => x && x.n_periods ? `<tr><td>${name}<div class="xs dim">${esc(x.start)} ~ ${esc(x.end)} · ${x.n_periods}회</div></td><td class="r num ${x.ic_t >= 2 ? "good" : ""}">${x.ic_mean?.toFixed(3)} <span class="xs dim">t=${x.ic_t}</span></td><td class="r num dim">${x.placebo_ic_mean?.toFixed(3)} <span class="xs">t=${x.placebo_ic_t}</span></td><td class="r num">${R(x.top_quintile_hit)}</td><td class="r ${x.top20_excess_net >= 0 ? "up" : "down"}">${P(x.top20_excess_net, 2)} <span class="xs dim">t=${x.top20_excess_net_t}</span></td><td class="r">${P(x.annualized_excess_net, 1)}</td></tr>` : "";
  const hold = st.holdout || {};
  const decRoll = ((dec.consensus || {}).rolling || []);
  const feats = (dr.features || []).map((f) => `<tr><td>${esc(f.label)}</td><td class="r num">${f.psi ?? "-"}</td><td class="r num">${f.ks ?? "-"}</td><td class="r num">${f.ks_p == null ? "-" : f.ks_p < 0.001 ? "<0.001" : f.ks_p.toFixed(3)}</td><td>${pill(f.status === "drift" ? "bad" : f.status === "warn" ? "warn" : "ok", f.status)}</td></tr>`).join("");
  const ab = (p.batch_ab?.rows || []).map((r) => `<tr><td>${esc(r.analyst)}</td><td class="r num">${R(r.batch.hit_rate)} <span class="xs dim">n=${r.batch.n}</span></td><td class="r num">${R(r.single.hit_rate)} <span class="xs dim">n=${r.single.n}</span></td><td class="r num">${r.p_value ?? "-"}</td><td class="small">${esc(r.verdict)}</td></tr>`).join("");
  el.innerHTML = `
  <div class="card vf-hero">
    <div class="vf-top"><div><div class="xs muted">가장 중요한 질문 — 코드가 좋은 것과, 실제 시장에서 돈을 벌 수 있다는 것은 다르다</div>
      <h2 class="${fw.decision === "H1" ? "good" : fw.decision === "H0" ? "bad-t" : "warn-t"}">${esc(p.bottom_line || "-")}</h2>
      <div class="small muted">${asOf(p.at)} · 증거 세 층은 섞지 않습니다: 과거(A) ≠ 앞으로(C)</div></div>
      <div class="vf-act"><button class="btn-sm primary" data-run="prediction_power">지금 갱신</button><button class="btn-sm" data-run="model_decay">노후 점검</button></div></div>
    <div class="layers">${layers}</div>
  </div>
  <div class="grid g-21">
    ${card(`C. 전진 검증 — 순차 확률비 검정 (SPRT) <span class="small dim">매일 들여다봐도 거짓 양성이 α 로 유지</span>`, `
      <div class="kv-grid">${kv("등록 후 채점", `${fw.scored ?? 0}건`)}${kv("적중", fw.hit_rate == null ? "-" : R(fw.hit_rate))}${kv("판정", esc(fw.label || "-"))}${kv("예상 판정일", fw.eta || (fw.decision !== "continue" ? "판정 완료" : "-"))}</div>
      ${dLine([{ data: path.length ? path : [0], color: "var(--accent-3)" }], { bands: [{ y: fw.upper ?? 1.4, color: "var(--good)", label: "예측력 있음" }, { y: fw.lower ?? -1.5, color: "var(--bad)", label: "예측력 없음" }], zero: 0 })}
      <div class="xs dim">선이 위 점선을 넘으면 '예측력 있음'(사전 등록 기준 통과), 아래 점선 밑이면 '없음'으로 확정. 그 사이는 계속 관찰. ${fw.more_needed != null ? `약 ${num(fw.more_needed)}건 더 필요${fw.per_day ? ` (지금 속도 하루 ${fw.per_day}건 → ${esc(fw.eta || "")})` : " — 등록 이후 채점된 예측이 쌓이면 예상 판정일을 계산합니다"}.` : ""}</div>`)}
    ${card("B. 사전 등록 (봉인된 성공 기준)", `<div class="small"><b>${esc(reg.h1 || "")}</b></div><div class="small muted">귀무: ${esc(reg.h0 || "")}</div>
      <div class="kv-grid" style="margin-top:8px">${kv("α / β", `${reg.alpha} / ${reg.beta}`)}${kv("고정 표본 필요", `${reg.required_n_fixed}건`)}${kv("등록", esc((reg.registered_at || "").slice(0, 10)))}${kv("버전", reg.version)}</div>
      <div class="xs mono dim" style="margin-top:8px;word-break:break-all">SHA-256 ${esc(reg.hash || "")}</div>
      <div class="xs dim" style="margin-top:6px">${esc(reg.rule || "")}</div>
      <table class="tight" style="margin-top:8px"><thead><tr><th>검출하려는 우위</th><th class="r">필요 예측 수 (검정력 80%)</th></tr></thead><tbody>${(p.power_table || []).map((t) => `<tr><td>+${t.edge_pp}%p</td><td class="r num">${num(t.n)}</td></tr>`).join("")}</tbody></table>`,
      `<button class="btn-sm" data-run="prereg" title="기준을 바꾸면 새 버전으로 등록 (이전 결과는 남음)">새로 등록</button>`)}
  </div>
  ${card(`A. 사후 검증 — 실제 KRX 과거 데이터 <span class="small dim">${esc(st.source?.data || "")} · 시총 상위 ${st.source?.top_n ?? "-"} · 그 시점 유니버스 · ${st.horizon ?? 20}거래일 간격 · 비용 ${st.cost_bps ?? "-"}bp</span>`,
    st.dev ? `<div class="study-verdict ${st.grade === "strong" ? "good" : st.grade === "partial" ? "warn-t" : "bad-t"}">${esc(st.verdict)}</div>
      <div class="scroll"><table class="tight"><thead><tr><th>구간</th><th class="r">순위 IC</th><th class="r">무작위 대조</th><th class="r">상위 5분위 적중</th><th class="r">비용 후 상위20 초과/20일</th><th class="r">연환산</th></tr></thead><tbody>${part(st.dev, "개발 (선택에 사용)")}${part(st.holdout, "검증 (선택 후 한 번)")}</tbody></table></div>
      <div class="grid g-2" style="margin-top:10px"><div><div class="small muted">검증 구간 10분위 평균 수익 (1=점수 최하 → 10=최상)</div>${dBars(hold.deciles || [], (hold.deciles || []).map((_, i) => i + 1))}</div>
      <div><div class="small muted">연도별 순위 IC</div>${dBars((st.yearly || []).map((y) => y.ic), (st.yearly || []).map((y) => String(y.year).slice(2)), { fmt: (v) => v.toFixed(3) })}</div></div>
      <div class="xs dim">${esc(st.caveat || "")}</div>` : empty("연구 결과가 없습니다 — quant-ai power-study --marcap-dir <경로>"))}
  <div class="grid g-2">
    ${card(`모델 노후 <span class="small dim">CUSUM · 추세 · 모델 나이별 적중</span>`, (dec.consensus || {}).n ? `<div class="small ${dec.status === "decaying" ? "bad-t" : dec.status === "watch" ? "warn-t" : "good"}">${esc(dec.message || "")}</div>
      ${dLine([{ data: decRoll, color: "var(--accent)" }], { bands: [{ y: dec.consensus.reference ?? 0.5, color: "var(--muted)", label: "초기 적중" }], yfmt: (v) => (v * 100).toFixed(0) + "%" })}
      <div class="small">${((dec.consensus || {}).by_age || []).map((a) => `${esc(a.age)} ${a.hit_rate == null ? "-" : R(a.hit_rate)} (n=${a.n})`).join(" · ")}${dec.consensus.half_life_days ? ` · 우위 반감기 약 ${dec.consensus.half_life_days}일` : ""}</div>` : empty(dec.message || "채점 40건부터 판단"), asOf(dec.at))}
    ${card(`데이터 드리프트 <span class="small dim">PSI(얼마나) + KS(우연인가)</span>`, feats ? `<div class="small" style="margin-bottom:6px">${esc(dr.message || "")}</div><table class="tight"><thead><tr><th>피처</th><th class="r">PSI</th><th class="r">KS D</th><th class="r">p</th><th></th></tr></thead><tbody>${feats}</tbody></table>` : empty("드리프트 점검 전"), asOf(dr.at))}
  </div>
  ${card(`묶음 호출 A/B <span class="small dim">약 20%는 하나씩 물어 판단 품질 비교 · 하나씩이 유의하게 나으면 묶음 크기 자동 축소</span>`, ab ? `<table class="tight"><thead><tr><th>AI</th><th class="r">묶음 적중</th><th class="r">하나씩 적중</th><th class="r">p</th><th>판정</th></tr></thead><tbody>${ab}</tbody></table>` : empty("묶음 호출 기록이 아직 없습니다 (LLM 공급자 설정 시)"))}`;
  bindRun(el);
}

// ------------------------------------------------------------ 뷰: 체결 · 증권사 검증
async function viewExecution(el) {
  const x = await api("/api/execution");
  const k = x.kis || {}, sl = x.slippage || {}, m = sl.model || sl.live || {}, par = sl.parity || {};
  const steps = (k.steps || []).map((s) => `<div class="step-row"><span>${s.ok ? "✅" : s.ok === null ? "⏭" : "❌"}</span><b>${esc(s.title)}</b><span class="small">${esc(s.detail)}</span><span class="xs dim num">${s.ms ? s.ms + "ms" : ""}</span></div>`).join("");
  const books = Object.entries(x.books || {}).slice(0, 4).map(([sym, b]) => {
    const asks = (b.asks || []).slice(0, 5).reverse(), bids = (b.bids || []).slice(0, 5);
    const mx = Math.max(...asks.concat(bids).map((r) => r[1]), 1);
    return `<div class="book"><div class="small"><b>${esc(sym)}</b> <span class="xs dim">${esc(kst(b.at))}</span></div>${asks.map(([p, q]) => `<div class="bk ask"><span class="num">${num(p)}</span><span class="bar" style="width:${q / mx * 100}%"></span><span class="num xs">${num(q)}</span></div>`).join("")}${bids.map(([p, q]) => `<div class="bk bid"><span class="num">${num(p)}</span><span class="bar" style="width:${q / mx * 100}%"></span><span class="num xs">${num(q)}</span></div>`).join("")}</div>`;
  }).join("");
  el.innerHTML = `
  ${card(`KIS 모의투자 검증 스위트 <span class="small dim">설정 → 토큰 → 잔고·장부 대조 → 시세·호가 → 지연 → 주문·조회·취소 → (선택) 실제 체결 → 실시간 접속</span>`,
    steps ? `<div class="kis-sum ${k.ok ? "good" : "bad-t"}"><b>${k.ok ? "통과" : "실패"}</b> · ${esc(k.env === "real" ? "실전 계좌 (주문 테스트 안 함)" : "모의투자")} · 주문 경로 ${k.order_path_verified ? "확인됨" : "미확인 (장중에 다시)"} · 체결 경로 ${k.fill_path_verified ? "확인됨" : "미확인"}</div><div class="steps">${steps}</div>` : empty(k.message || "아직 실행 전 — KIS 키가 .env 에 있어야 합니다"),
    `${k.at ? asOf(k.at) : ""} <button class="btn-sm primary" data-run="kis_validate">검증 실행</button>`)}
  <div class="grid g-2">
    ${card(`실측 슬리피지 → 비용 자동 보정 <span class="small dim">슬리피지 = 고정분 + 계수 × σ × √(주문/거래대금)</span>`, `
      <div class="kv-grid">${kv("실측 표본", `${m.n ?? 0}건`)}${kv("고정분 (보정)", m.fixed_bps == null ? "-" : `${m.fixed_bps}bp`)}${kv("충격 계수 (보정)", m.impact_coef ?? "-")}${kv("적용", m.applied ? "적용 중" : "표본 부족 — 가정 유지", m.applied ? "good" : "dim")}</div>
      <div class="small" style="margin-top:8px">${esc(m.verdict || "")}</div>
      <div class="xs dim" style="margin-top:6px">가정: 슬리피지 ${x.costs?.assumed?.slippage_bps}bp · 충격 계수 ${x.costs?.assumed?.impact_coef} · 50건부터 표본 가중(n/(n+50))으로 실측 쪽으로 옮깁니다. 실측 평균 ${m.mean_bps ?? "-"}bp · p90 ${m.p90_bps ?? "-"}bp</div>`,
      `${sl.at ? asOf(sl.at) : ""} <button class="btn-sm" data-run="slippage_cal">다시 계산</button>`)}
    ${card("체결 시뮬레이터 vs 실제 (parity)", par.n ? `<div class="kv-grid">${kv("비교 건수", `${par.n} / ${par.min_n ?? 100}`)}${kv("편향 (실제−예측)", `${par.bias_bps}bp`)}${kv("평균 오차", `${par.mae_bps}bp`)}${kv("섀도에 적용 중인 보정", par.applied_bias_bps ? `${par.applied_bias_bps}bp` : "아직 (100건부터)")}</div><div class="small" style="margin-top:8px">${esc(par.verdict)}</div>` : empty("KIS 실주문이 체결될 때마다 · 매일 장중 자동 검증(QUANT_KIS_FILL_TEST=true)마다 시뮬레이터 예측과 실제 체결가를 나란히 기록합니다. 100건부터 섀도 체결에 편향 보정 적용"))}
  </div>
  ${card(`실시간 호가 (KIS H0STASP0) <span class="small dim">보유 상위 10종목 · 섀도 체결은 이 잔량을 먹어 들어가며 계산</span>`, books ? `<div class="books">${books}</div>` : empty(x.broker === "kis" ? "장중에 웹소켓이 연결되면 표시됩니다" : "KIS 미설정 — 섀도 체결은 1단계 호가·모델 스프레드로 계산"), x.ws?.state ? `<span class="chip">${esc(x.ws.state)} · 체결 ${x.ws.subs ?? 0} · 호가 ${x.ws.book_subs ?? 0}</span>` : "")}`;
  bindRun(el);
}

// ------------------------------------------------------------ 뷰: 내 저널 vs AI
async function viewMyJournal(el) {
  const j = await api("/api/myjournal");
  const rows = (j.rows || []).map((r) => `<tr><td class="small mono">${esc(kst(r.at))}</td><td><a href="#analysis/${esc(r.symbol)}"><b>${esc(r.name)}</b></a></td><td>${badge(r.action)}</td><td class="r num">${"★".repeat(r.conviction)}</td><td class="r num">${r.horizon}일</td><td class="small" style="white-space:normal">${esc(r.reason || "")}</td>
    <td class="r">${r.ret == null ? '<span class="dim small">채점 대기</span>' : pct(r.ret)}</td><td>${r.correct == null ? "" : r.correct ? '<span class="chip ok">적중</span>' : '<span class="chip bad">빗나감</span>'}${r.sealed ? ' <span class="xs dim" title="저장 순간 해시 봉인">🔒</span>' : ""}</td></tr>`).join("");
  const conv = (j.by_conviction || []).map((c) => `<div class="wbar"><span class="small">확신 ${"★".repeat(c.conviction)}</span><div class="bar"><div style="width:${c.hit_rate * 100}%"></div></div><span class="num small r">${R(c.hit_rate)} <span class="xs dim">n=${c.n}</span></span></div>`).join("");
  el.innerHTML = `
  ${card(`내 투자 판단 기록 <span class="small dim">AI 와 똑같이 저장 순간 봉인 → 다음 날 시가 진입 · 기간 뒤 종가로 채점</span>`, `
    <form class="mj-form" id="mj-form"><input name="symbol" placeholder="종목 코드 (예: 005930, NVDA)" required maxlength="12">
      <select name="action"><option value="BUY">BUY (오른다)</option><option value="SELL">SELL (내린다)</option><option value="HOLD">HOLD</option></select>
      <select name="conviction">${[1, 2, 3, 4, 5].map((k) => `<option value="${k}" ${k === 3 ? "selected" : ""}>확신 ${"★".repeat(k)}</option>`).join("")}</select>
      <select name="horizon">${[1, 5, 20].map((k) => `<option value="${k}" ${k === 5 ? "selected" : ""}>${k}거래일</option>`).join("")}</select>
      <input name="reason" placeholder="이유 (나중에 고치면 봉인이 깨져 표시됩니다)" maxlength="500"><button class="btn-sm primary">기록</button></form>`)}
  <div class="grid g-3">
    ${card("나 vs AI (같은 종목 · ±2일)", j.pairs?.n ? `<div class="kv-grid">${kv("나의 적중", R(j.pairs.me_hit))}${kv("AI 적중", R(j.pairs.ai_hit))}${kv("의견 일치율", R(j.pairs.agree_rate))}${kv("짝지은 판단", j.pairs.n)}</div>` : empty("같은 시기 AI 판단과 짝지을 기록이 아직 없습니다"))}
    ${card("의견이 달랐을 때 누가 맞았나", j.disagree?.n ? `<div class="big num">${j.disagree.me_right} : ${j.disagree.ai_right}</div><div class="small muted">나 : AI · 의견 불일치 ${j.disagree.n}건</div>` : empty("불일치 기록 없음"))}
    ${card("확신도 보정 · 내 습관", `${conv || empty("채점 기록 없음")}${(j.biases || []).map((b) => `<div class="veto" style="margin-top:6px">⚠ ${esc(b)}</div>`).join("")}${(j.tampered || []).length ? `<div class="veto">🔓 봉인 후 수정된 기록 ${j.tampered.length}건</div>` : ""}`)}
  </div>
  ${card(`기록 <span class="small dim">나 ${j.me?.hit_rate == null ? "-" : R(j.me.hit_rate)} 적중 · 채점 ${j.scored}/${j.n}</span>`, rows ? `<div class="scroll"><table class="tight"><thead><tr><th>기록 시각</th><th>종목</th><th>판단</th><th class="r">확신</th><th class="r">기간</th><th>이유</th><th class="r">실제</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>` : empty("첫 판단을 기록해 보세요"))}`;
  const f = $("#mj-form");
  if (f) f.onsubmit = async (e) => {
    e.preventDefault();
    const body = Object.fromEntries(new FormData(f).entries());
    try { await api("/api/myjournal", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }); if (typeof toast === "function") toast({ title: "기록됨 (봉인)", body: `${body.symbol} ${body.action}`, level: "good" }); render(); }
    catch (err) { if (typeof toast === "function") toast({ title: "기록 실패", body: err.message, level: "bad" }); }
  };
}

// ------------------------------------------------------------ 리스크 2.0 (viewRisk 아래에 붙는다)
function corrColor(v) {
  if (v == null) return "transparent";
  const a = Math.min(Math.abs(v), 1);
  return v >= 0 ? `rgba(240,71,79,${0.12 + a * 0.6})` : `rgba(59,140,255,${0.12 + a * 0.6})`;
}
function riskV13(r) {
  if (!r || r.insufficient || !r.var) return "";
  const v = r.var || {}, c = r.concentration || {};
  const vm = [["과거 시뮬레이션 95%", v.hist95], ["과거 시뮬레이션 99%", v.hist99], ["ES 95% (최악 5% 평균)", v.es95], ["ES 99%", v.es99], ["정규분포 95%", v.normal95], ["Cornish-Fisher 95% (꼬리 보정)", v.cornish_fisher95], ["EWMA 95% (최근 변동성)", v.ewma95], ["10일 VaR 95%", v.hist95_10d], ["유동성 조정 VaR 95%", r.lvar95]]
    .map(([k, x]) => `<tr><td>${k}</td><td class="r num">${R(x, 2)}</td><td class="r num dim">${x == null ? "-" : krw(x * (r.equity || 0))}</td></tr>`).join("");
  const sec = (r.sectors || []).map((s) => `<div class="wbar"><span class="small">${esc(s.sector)} <span class="xs dim">${s.n}종목</span></span><div class="bar"><div style="width:${Math.min(s.weight / (r.limits?.max_sector_weight || 0.4), 1) * 100}%;background:${s.weight > (r.limits?.max_sector_weight || 0.4) ? "var(--bad)" : "var(--accent)"}"></div></div><span class="num small r">${R(s.weight)}</span></div>`).join("");
  const cv = (r.component_var || []).slice(0, 10).map((x) => `<tr><td>${esc(x.name)}</td><td class="r num">${R(x.weight)}</td><td class="r num">${R(x.component, 2)}</td><td class="r">${hbar(Math.max(x.share, 0), 1, x.share > 0.3 ? "bad" : "")} <span class="num xs">${R(x.share, 0)}</span></td></tr>`).join("");
  const cm = r.corr_matrix || {};
  const heat = cm.symbols ? `<div class="scroll"><table class="heat"><thead><tr><th></th>${cm.names.map((n) => `<th title="${esc(n)}">${esc(n.slice(0, 5))}</th>`).join("")}</tr></thead><tbody>${cm.values.map((row, i) => `<tr><th>${esc(cm.names[i].slice(0, 6))}</th>${row.map((x, j) => `<td style="background:${i === j ? "var(--panel-3)" : corrColor(x)}" title="${esc(cm.names[i])} ↔ ${esc(cm.names[j])} ${x}">${i === j ? "" : x == null ? "" : x.toFixed(2)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>` : empty("2종목 이상 필요");
  const stress = (r.stress || []).map((x) => `<tr><td>${x.kind === "historical" ? "📜" : "🧪"} ${esc(x.name)}${x.period ? `<div class="xs dim">${esc(x.period)}</div>` : ""}</td><td class="r num ${x.loss > (r.limits?.max_stress_loss || 0.25) ? "bad-t" : ""}">${x.loss == null ? "-" : "−" + R(x.loss)}</td><td class="r num dim">${x.loss == null ? "-" : krw(-x.loss * (r.equity || 0))}</td><td class="xs dim">${esc(x.basis || "")}${x.market != null ? ` · 지수 ${P(x.market)}` : ""}</td></tr>`).join("");
  const ru = r.ruin || {};
  const ruin = ru.insufficient ? empty(ru.message || "표본 부족") : `<div class="small ${String(ru.verdict).startsWith("위험") ? "bad-t" : String(ru.verdict).startsWith("주의") ? "warn-t" : "good"}"><b>${esc(ru.verdict || "")}</b></div>
    <div class="kv-grid" style="margin-top:8px">${Object.entries(ru.p_drawdown || {}).map(([k, x]) => kv(`1년 내 −${k}% 낙폭 확률`, R(x, 1), k === "50" && x > 0 ? "bad-t" : "")).join("")}${kv("1년 뒤 중앙값", P(ru.terminal?.median))}${kv("1년 뒤 하위 5%", P(ru.terminal?.p5), "down")}${kv("최대 낙폭 95%", "−" + R(ru.mdd?.p95))}${kv("켈리 대비", ru.kelly_leverage == null ? "-" : ru.over_betting ? `과잉 (켈리 ${ru.kelly_leverage}배)` : `여유 (켈리 ${ru.kelly_leverage}배)`)}</div>
    <div class="xs dim" style="margin-top:6px">${esc(ru.source || "")} · ${ru.paths}회 경로 · 5일 블록 부트스트랩 (변동성 군집 보존)</div>`;
  return `
  <div class="grid g-3">
    ${card("VaR · ES — 여러 방식 <span class='small dim'>방식마다 크게 다르면 꼬리가 두껍다</span>", `<table class="tight"><tbody>${vm}</tbody></table><div class="xs dim" style="margin-top:6px">왜도 ${v.skew} · 초과 첨도 ${v.excess_kurtosis}${v.fat_tail ? ' · <span class="warn-t">두꺼운 꼬리</span>' : ""} · 분산 효과 ${R(r.diversification_benefit)} (상관 1 이면 VaR ${R(r.undiversified_var95, 2)})</div>`, asOf(r.as_of ? String(r.as_of).slice(0, 10) : null, "fresh", "가격"))}
    ${card("업종 노출 <span class='small dim'>WICS 우선</span>", sec || empty("업종 정보 없음"))}
    ${card("집중도", `<div class="kv-grid">${kv("유효 종목 수", c.effective_n ?? "-", (c.effective_n || 99) < 5 ? "warn-t" : "")}${kv("HHI", c.hhi ?? "-")}${kv("최대 1종목", R(c.top1))}${kv("상위 5종목", R(c.top5))}</div><div class="xs dim" style="margin-top:6px">유효 종목 수 = 1/HHI — 10종목을 들고 있어도 한두 종목에 몰리면 2~3 에 가깝다</div>`)}
  </div>
  <div class="grid g-2">
    ${card("성분 VaR <span class='small dim'>각 종목이 포트폴리오 VaR 에 보태는 몫 (합 = 100%)</span>", cv ? `<table class="tight"><thead><tr><th>종목</th><th class="r">비중</th><th class="r">성분 VaR</th><th class="r">몫</th></tr></thead><tbody>${cv}</tbody></table>` : empty())}
    ${card("상관 행렬 <span class='small dim'>빨강 = 같이 움직임 · 파랑 = 반대</span>", heat)}
  </div>
  ${card(`스트레스 테스트 <span class="small dim">📜 실제 과거 위기 재생 · 🧪 가정 시나리오 · 경고선 ${R(r.limits?.max_stress_loss)}</span>`, stress ? `<div class="scroll"><table class="tight"><thead><tr><th>시나리오</th><th class="r">손실</th><th class="r">금액</th><th>근거</th></tr></thead><tbody>${stress}</tbody></table></div>` : empty())}
  ${card("Risk-of-Ruin <span class='small dim'>지금 방식으로 1년 — 몬테카를로</span>", ruin)}
  <div class="xs dim" style="margin:6px 2px 14px">주문 사전 게이트: 새 매수는 넣은 뒤의 포트폴리오가 업종 ${R(r.limits?.max_sector_weight, 0)} · 같이 움직이는 묶음 ${R(r.limits?.max_cluster_weight, 0)} · 1일 VaR95 ${R(r.limits?.max_var95, 0)} 를 넘지 않는 만큼만 들어갑니다.</div>`;
}

// ------------------------------------------------------------ 종목 데스크 (종목 화면에 붙는다)
async function stockDesk(sym) {
  const box = $("#pf-desk");
  if (!box) return;
  let d;
  try { d = await api(`/api/desk?symbol=${encodeURIComponent(sym)}`); } catch (e) { box.innerHTML = ""; return; }
  if (d.error) { box.innerHTML = ""; return; }
  const p = d.plan ? { ...d.plan } : null, sz = p?.sizing || {}, en = p?.entry || {};
  if (p && !p.action && d.action) {  // v13 이전에 봉인된 계획: 신호를 화면에서 반영
    p.action = d.action;
    if (d.action !== "BUY") { p.tradeable = false; p.note = `합의 신호 ${d.action} — 이 계획은 '산다면' 참고용이며 주문은 나가지 않습니다`; }
  }
  let planBody = empty("AI 판단이 아직 없어 매매 계획이 없습니다");
  if (p) {
    const lo = Math.min(p.stop, en.low) * 0.995, hi = Math.max(en.no_chase_above || en.high, p.target || en.high, p.ref_price) * 1.005;
    const X = (v) => ((v - lo) / (hi - lo) * 100).toFixed(1);
    planBody = `${p.note ? `<div class="small warn-t" style="margin-bottom:6px">ⓘ ${esc(p.note)}</div>` : ""}<div class="plan-top"><div>${p.tradeable ? '<span class="chip ok">매수 계획</span>' : p.action && p.action !== "BUY" ? `<span class="chip">신호 ${esc(p.action)} · 참고용</span>` : '<span class="chip bad">매수 안 함</span>'} ${d.plan_sealed ? '<span class="chip" title="예측과 함께 해시 봉인 — 나중에 고칠 수 없음">🔒 봉인됨</span>' : ""}</div><div class="xs dim">${esc(d.plan_at || "")} 판단 기준 · P(상승) ${R(p.prob_up)} · ${p.horizon}거래일</div></div>
      <div class="zone"><div class="zone-bar"><span class="z-stop" style="left:${X(p.stop)}%" title="무효화(손절) ${num(p.stop, 2)}"></span><span class="z-entry" style="left:${X(en.low)}%;width:${Math.max(X(en.high) - X(en.low), 0.8)}%" title="진입 구간"></span>${en.no_chase_above ? `<span class="z-chase" style="left:${X(en.no_chase_above)}%" title="추격 금지"></span>` : ""}<span class="z-now" style="left:${X(p.ref_price)}%" title="기준가"></span>${p.target ? `<span class="z-tgt" style="left:${X(p.target)}%" title="목표"></span>` : ""}</div>
        <div class="zone-lbl xs"><span class="bad-t">손절 ${num(p.stop, 2)} (${P(p.stop_pct)})</span><span>진입 ${num(en.low, 2)} ~ ${num(en.high, 2)}</span>${en.no_chase_above ? `<span class="warn-t">추격 금지 ${num(en.no_chase_above, 2)}↑</span>` : ""}${p.target ? `<span class="up">목표 ${num(p.target, 2)}</span>` : ""}</div></div>
      <div class="kv-grid">${kv(p.tradeable ? "권장 비중" : "산다면 비중", R(sz.weight, 1), p.tradeable && sz.weight > 0 ? "good" : "dim")}${kv("결정 요인", esc(sz.binding || "-"))}${kv("비용 후 기대", P(p.net_expected, 2), p.net_expected > 0 ? "up" : "down")}${kv("손익비", p.reward_risk ?? "-")}</div>
      <div class="xs dim" style="margin-top:4px">반켈리 ${R(sz.half_kelly, 1)} · 변동성 목표 ${R(sz.vol_target, 1)} · 한도 ${R(sz.cap, 0)} · 이벤트 ×${sz.event_mult}${sz.event_reason ? ` (${esc(sz.event_reason)})` : ""} · 준비 상태 ×${sz.readiness_mult} · 왕복 비용 ${p.cost_bps}bp</div>
      <div class="small" style="margin-top:8px"><b>무효화 조건</b> (하나라도 맞으면 판단을 틀렸다고 인정)</div><ul class="plain small">${(p.invalidation || []).map((i) => `<li>· ${esc(i.rule)}</li>`).join("")}</ul>`;
  }
  chartPlanLines(sym, p);
  const er = d.event_risk;
  const evBody = `${er ? `<div class="kv-grid">${kv("이벤트 위험", `${er.score?.toFixed(2)} <span class="xs">${esc({ high: "높음", medium: "보통", low: "낮음" }[er.level] || "")}</span>`, er.level === "high" ? "bad-t" : er.level === "medium" ? "warn-t" : "")}${kv("예상 변동", er.expected_move ? "±" + R(er.expected_move) : "-")}${kv("매수 제한", er.buy_multiplier < 1 ? `×${er.buy_multiplier}` : "없음", er.buy_multiplier < 1 ? "warn-t" : "")}</div>` : ""}
    <div class="ev-mini">${(d.events || []).map((e) => `<div><span>${CAL_ICON[e.kind] || "•"}</span> <b>${esc(e.title)}</b> <span class="xs dim">${esc(e.date)} · ${esc(e.d_label)}${e.estimated ? " · 추정" : ""}</span></div>`).join("") || '<span class="xs dim">예정된 종목 이벤트 없음</span>'}</div>
    ${(er?.market_events || []).length ? `<div class="xs muted" style="margin-top:6px">시장: ${er.market_events.map((m) => `${esc(m.title)} ${esc(m.d_label)}`).join(" · ")}</div>` : ""}`;
  const o = d.options || {};
  const ki = d.kr_implied;
  const optBody = ki ? `<div class="kv-grid">${kv(ki.proxy ? "시장 변동 (대용)" : "VKOSPI", `${ki.vkospi}${ki.percentile_1y != null ? ` <span class="xs dim">1년 ${R(ki.percentile_1y, 0)}</span>` : ""}`)}${kv("베타", ki.beta ?? "-")}${kv("예상 변동 1일", "±" + R(ki.move_1d, 2))}${kv("5일", "±" + R(ki.move_5d, 1))}${kv("20일", "±" + R(ki.move_20d, 1))}</div>
    <div class="xs dim" style="margin-top:6px">국내 종목별 옵션은 무료 데이터가 없어 √((베타×VKOSPI)² + 고유 변동²) 으로 근사 · ${esc(ki.source || "")} · ${esc(ki.as_of || "")}</div>` : o.available ? `<div class="kv-grid">${kv("시장 예상 변동", o.implied_move ? "±" + R(o.implied_move) : "-")}${kv("ATM IV", R(o.atm_iv))}${kv("스큐 (풋−콜)", o.skew == null ? "-" : (o.skew * 100).toFixed(1) + "%p")}${kv("풋/콜 (미결제)", o.pc_oi ?? "-")}${kv("맥스 페인", o.max_pain ?? "-")}${o.earnings_implied_move ? kv("실적 내재 변동", "±" + R(o.earnings_implied_move)) : ""}</div><div class="small" style="margin-top:6px">${esc(o.read || "")}</div>` : empty(o.message || "옵션 데이터 없음");
  const em = d.earnings_model || {};
  const cs = em.consensus;
  const consLine = cs ? `<div class="small" style="margin-top:8px"><b>${esc(cs.label)} 컨센서스</b> 매출 ${num(cs.revenue)} · 영업이익 ${num(cs.op_income)}${cs.op_income_yoy != null ? ` (전년 대비 ${P(cs.op_income_yoy)})` : ""}${cs.eps != null ? ` · EPS ${num(cs.eps)}` : ""} <span class="xs dim">억원 · 네이버</span></div>` : "";
  const emBody = em.error ? empty("실적 이력을 받지 못했습니다") : `<div class="kv-grid">${kv("P(예상 상회)", R(em.p_beat), em.p_beat >= 0.6 ? "good" : "")}${kv("상회 이력", `${em.beats ?? 0}/${em.n_history ?? 0}`)}${kv("상회 때 반응", P(em.reaction_on_beat, 1), "up")}${kv("하회 때 반응", P(em.reaction_on_miss, 1), "down")}${kv("기대 반응", P(em.expected_reaction, 1))}${kv("발표 후 표류", P(em.pead, 1))}</div><div class="xs dim" style="margin-top:6px">${esc(em.asymmetry || "")} · 신뢰 ${esc(em.confidence || "-")} · 사전 ${esc(em.prior || "")}${em.next_date ? ` · 다음 발표 ${esc(em.next_date)}` : ""} · 출처 ${esc(em.source || "-")}${em.kr_history != null && d.symbol && /^\d/.test(d.symbol) ? ` · 국내 서프라이즈 이력 ${em.kr_history}건` : ""}</div>${consLine}`;
  const alt = d.alt || {}, w = alt.raw?.wiki || {};
  const altBody = w.series ? `<div class="kv-grid">${kv("위키 관심 z", w.z ?? "-", w.spike ? "warn-t" : "")}${kv("최근 7일/평소", w.ratio ? w.ratio + "배" : "-")}${alt.search_attention_z != null ? kv("검색 관심 z", alt.search_attention_z) : ""}</div>${dLine([{ data: w.series, color: "var(--accent-3)" }], { h: 70, yfmt: (v) => v.toFixed(0) })}<div class="xs dim">관심도는 방향이 아닙니다 · 급증 + 가격 급등이 겹치면 과열 신호</div>` : empty("대체 데이터 수집 전 (하루 1회)");
  const g = d.graph || {};
  const rel = (g.neighbors || []).map((n) => `<div class="nb-it"><a href="#analysis/${esc(n.symbol)}"><b>${esc(n.name)}</b></a> ${n.relation_label ? `<span class="chip xs">${esc(n.relation_label)}</span>` : ""} <span class="xs muted">${[n.corr ? `상관 ${n.corr}` : null, n.co_mention ? `뉴스 ${n.co_mention}회` : null].filter(Boolean).join(" · ")}</span>${(n.evidence || [])[0] ? `<div class="xs dim nb-t">📰 ${esc(n.evidence[0].title)}</div>` : ""}</div>`).join("");
  const two = (g.two_hop || []).slice(0, 5).map((t) => `<a href="#analysis/${esc(t.symbol)}">${esc(t.name)}</a> <span class="xs dim">(${esc(t.via)} 경유)</span>`).join(" · ");
  const ex = (d.extracted || []).map((e) => `<div class="small"><span class="chip xs ${e.polarity > 0 ? "ok" : e.polarity < 0 ? "bad" : ""}">${esc(e.label)}</span> ${esc(e.title)} <span class="xs dim">${esc(e.date)} · ${esc(e.source)}${e.amount_krw ? ` · ${num(e.amount_krw / 1e8)}억원` : ""}${e.materiality ? ` · 시총의 ${R(e.materiality, 2)}` : ""}</span></div>`).join("");
  const fl = d.flow || {};
  const flowBody = fl.signal ? `<div class="kv-grid">${kv("수급 신호", esc(fl.signal), fl.signal.includes("순매수") ? "up" : fl.signal.includes("순매도") ? "down" : "")}${kv("외국인 z(5일)", fl.foreign_z5 ?? "-")}${kv("기관 z(5일)", fl.inst_z5 ?? "-")}${kv("가격-수급", esc(fl.divergence || "-"))}${kv("수급→5일 수익 상관", fl.flow_ret5_corr ?? "-")}</div>${(fl.smart_money_curve || []).length ? dLine([{ data: fl.smart_money_curve, color: "var(--up)" }], { h: 70, yfmt: (v) => num(v / 1e3) + "k" }) : ""}<div class="xs dim">외국인+기관 누적 순매수 (주) · ${esc(d.flow_at || "")}</div>` : "";
  box.innerHTML = `
  <div class="grid g-2">
    ${card(`매매 계획 <span class="small dim">사이징 · 진입 구간 · 무효화</span>`, planBody, asOf(d.bar_as_of, "fresh", "일봉"))}
    ${card("이벤트 · 위험", evBody)}
  </div>
  <div class="grid g-3">
    ${card(ki ? "예상 변동 (VKOSPI 기반)" : "옵션 (시장이 매긴 변동)", optBody, o.at ? asOf(o.at) : "")}
    ${card("실적 서프라이즈 모델", emBody)}
    ${card("대체 데이터 · 관심도", altBody)}
  </div>
  <div class="grid g-2">
    ${card("관계 (종목 지식 그래프)", `${rel || empty("연결 없음")}${two ? `<div class="small" style="margin-top:8px"><b>2단계 연결</b> ${two}</div>` : ""}`)}
    ${card("추출된 이벤트 (NLP)", ex || empty("최근 30일 추출된 이벤트 없음"))}
  </div>
  ${flowBody ? card("외국인 · 기관 수급 (강화)", flowBody) : ""}`;
}

// ------------------------------------------------------------ 그래프 화면: 섹터 로테이션 (RRG) · WICS · 이벤트 추출
async function rotationPanel(el) {
  const box = document.createElement("div");
  el.appendChild(box);
  let r;
  try { r = await api("/api/rotation"); } catch { return; }
  const rows = r.rows || [];
  const W = 520, H = 360, pad = 30;
  const xs = rows.flatMap((x) => x.trail.map((t) => t.x)), ys = rows.flatMap((x) => x.trail.map((t) => t.y));
  const span = (a) => a.length ? Math.max(Math.abs(Math.max(...a) - 100), Math.abs(Math.min(...a) - 100), 1) * 1.15 : 5;
  const sx = span(xs), sy = span(ys);
  const X = (v) => pad + ((v - (100 - sx)) / (2 * sx)) * (W - 2 * pad), Y = (v) => pad + (1 - (v - (100 - sy)) / (2 * sy)) * (H - 2 * pad);
  const quad = `<rect x="${X(100)}" y="${pad}" width="${W - pad - X(100)}" height="${Y(100) - pad}" fill="rgba(34,197,94,.07)"/><rect x="${X(100)}" y="${Y(100)}" width="${W - pad - X(100)}" height="${H - pad - Y(100)}" fill="rgba(245,158,11,.07)"/><rect x="${pad}" y="${Y(100)}" width="${X(100) - pad}" height="${H - pad - Y(100)}" fill="rgba(239,68,68,.07)"/><rect x="${pad}" y="${pad}" width="${X(100) - pad}" height="${Y(100) - pad}" fill="rgba(59,130,246,.07)"/>
    <text x="${W - pad - 4}" y="${pad + 14}" text-anchor="end" class="svg-lbl">주도</text><text x="${W - pad - 4}" y="${H - pad - 6}" text-anchor="end" class="svg-lbl">약화</text><text x="${pad + 4}" y="${H - pad - 6}" class="svg-lbl">침체</text><text x="${pad + 4}" y="${pad + 14}" class="svg-lbl">개선</text>
    <line x1="${X(100)}" x2="${X(100)}" y1="${pad}" y2="${H - pad}" stroke="var(--line-2)"/><line x1="${pad}" x2="${W - pad}" y1="${Y(100)}" y2="${Y(100)}" stroke="var(--line-2)"/>`;
  const trails = rows.map((x) => {
    const col = typeof secColor === "function" ? secColor(x.sector) : "var(--accent)";
    const pts = x.trail.map((t) => `${X(t.x).toFixed(1)},${Y(t.y).toFixed(1)}`).join(" ");
    const last = x.trail[x.trail.length - 1];
    return `<polyline points="${pts}" fill="none" stroke="${col}" stroke-width="1.4" opacity=".7"/><circle cx="${X(last.x).toFixed(1)}" cy="${Y(last.y).toFixed(1)}" r="5" fill="${col}"><title>${esc(x.sector)} · ${esc(x.quadrant)} · Ratio ${x.rs_ratio} · Mom ${x.rs_mom}</title></circle><text x="${(X(last.x) + 7).toFixed(1)}" y="${(Y(last.y) + 4).toFixed(1)}" class="svg-lbl">${esc(x.sector)}</text>`;
  }).join("");
  const ex = (r.extract?.events || []).slice(0, 14).map((e) => `<div class="small"><span class="chip xs ${e.polarity > 0 ? "ok" : e.polarity < 0 ? "bad" : ""}">${esc(e.label)}</span> ${e.symbol ? `<a href="#analysis/${esc(e.symbol)}">${esc(e.title)}</a>` : esc(e.title)} <span class="xs dim">${esc(e.date)}${e.amount_krw ? ` · ${num(e.amount_krw / 1e8)}억원` : ""}</span></div>`).join("");
  box.innerHTML = `<div class="grid g-21">
    ${card(`섹터 로테이션 (RRG) <span class="small dim">상대강도 비율 × 모멘텀 · 꼬리 = 최근 8주 · 시계 방향으로 돈다</span>`, rows.length ? `<svg class="rrg" viewBox="0 0 ${W} ${H}">${quad}${trails}</svg>` : empty("업종 지도가 채워지면 표시됩니다"),
      `<span class="xs dim">업종: ${r.wics?.n ? `WICS ${r.wics.n}종목 (${esc(r.wics.day || "")})` : "Yahoo 기반"}</span> <button class="btn-sm" data-run="wics">WICS 갱신</button>`)}
    ${card(`이벤트 추출 (NLP) <span class="small dim">최근 30일 뉴스·공시 · 규칙 기반 (재현 가능)</span>`, `${(r.extract?.by_type || []).length ? `<div class="xs muted" style="margin-bottom:6px">${r.extract.by_type.slice(0, 8).map(([k, n]) => `${esc(k)} ${n}`).join(" · ")}</div>` : ""}${ex || empty("추출된 이벤트 없음")}`, `<button class="btn-sm" data-run="event_extract">다시 추출</button>`)}
  </div>`;
  bindRun(box);
}

// 공증 카드 (검증실)
async function notaryCard(el) {
  let p;
  try { p = await api("/api/pipeline"); } catch { return; }
  const n = p.notary || {}, last = n.last;
  const box = document.createElement("div");
  box.innerHTML = card(`장부 외부 공증 <span class="small dim">봉인 해시를 제3자(OpenTimestamps · 선택: GitHub Gist)에 남김 → 날짜를 아무도 속일 수 없음</span>`,
    last ? `<div class="kv-grid">${kv("마지막 공증", kst(last.at))}${kv("장부 번호까지", `#${last.upto_id}`)}${kv("결과", last.ok ? "성공" : "실패", last.ok ? "good" : "bad-t")}${kv("누적", `${(n.receipts || []).length}회`)}</div>
      <div class="xs mono dim" style="margin-top:6px;word-break:break-all">${esc(last.digest)}</div>
      <div class="small" style="margin-top:6px">${(last.results || []).map((r) => r.error ? `❌ ${esc(r.service)}: ${esc(r.error)}` : r.url ? `✅ gist <a class="link" href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.url)}</a>` : `✅ ${esc(r.service)} · ${esc(r.status || "")} · artifacts/notary/${esc(r.file || "")}`).join("<br>")}</div>
      <div class="xs dim" style="margin-top:6px">검증: <span class="mono">ots upgrade anchor-N.txt.ots && ots verify anchor-N.txt.ots</span> (비트코인 확정 후 · 수 시간)</div>` : empty("아직 공증 전 — 하루 한 번 자동"),
    `<button class="btn-sm" data-run="notary">지금 공증</button>`);
  el.appendChild(box.firstElementChild);
  bindRun(el);
}

// ------------------------------------------------------------ 뷰: 계좌 · 세금 · 배당
async function viewAccounts(el) {
  const a = await api("/api/accounts");
  const t = a.total || {};
  const acctCards = (a.accounts || []).map((r) => `
    <div class="card acct">
      <div class="card-h"><h3>${esc(r.name)} <span class="chip xs">${esc(r.type_label)}</span>${r.broker ? ` <span class="xs dim">${esc(r.broker)}</span>` : ""}</h3>
        <div class="right">${r.readonly ? '<span class="xs dim">읽기 전용</span>' : `<button class="btn-sm" data-acct-edit="${esc(r.id)}">수정</button> <button class="btn-sm" data-acct-del="${esc(r.id)}">삭제</button>`}</div></div>
      <div class="kv-grid">${kv("평가금액 (현금 포함)", krw(r.total))}${kv("평가손익", `${krw(r.gain)} <span class="xs">${r.ret == null ? "" : P(r.ret)}</span>`, r.gain >= 0 ? "up" : "down")}${kv("예상 배당 (12개월)", krw(r.div_12m))}${kv("지금 팔면 세금", r.type === "overseas" || r.type === "general" ? "아래 합산" : krw(r.tax_now))}</div>
      <div class="xs dim" style="margin:6px 0">${esc(r.note)}</div>
      ${r.holdings.length ? `<div class="scroll"><table class="tight"><thead><tr><th>종목</th><th class="r">수량</th><th class="r">평단</th><th class="r">현재가</th><th class="r">평가</th><th class="r">손익</th><th class="r">배당/년</th><th>배당락</th></tr></thead><tbody>${r.holdings.map((h) => `<tr><td><a href="#analysis/${esc(h.symbol)}"><b>${esc(h.name || h.symbol)}</b></a> <span class="xs dim">${esc(h.currency)}</span></td><td class="r num">${num(h.qty)}</td><td class="r num">${num(h.avg_price, h.currency === "USD" ? 2 : 0)}</td><td class="r num">${h.priced ? num(h.price, h.currency === "USD" ? 2 : 0) : '<span class="dim">가격 없음</span>'}</td><td class="r num">${krw(h.value)}</td><td class="r ${h.gain >= 0 ? "up" : "down"}">${h.ret == null ? "-" : P(h.ret)}</td><td class="r num">${h.div_12m ? krw(h.div_12m) : "-"}</td><td class="small">${esc(h.ex_date || "")}</td></tr>`).join("")}</tbody></table></div>` : empty("보유 종목 없음")}
    </div>`).join("");
  const types = Object.entries(a.types || {}).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join("");
  el.innerHTML = `
  <div class="card vf-hero">
    <div class="vf-top"><div><div class="xs muted">여러 증권사 · 계좌를 한 곳에서 · 세금은 '지금 전부 판다면' 기준 대략치 (신고용 아님 · ${a.rules?.year}년 규칙)</div>
      <h2>${krw(t.value)}</h2><div class="small muted">평가손익 ${krw(t.gain)} · 예상 배당 12개월 ${krw(t.div_12m)} (세후 ${krw(t.div_12m - t.div_tax)}) · 환율 ${num(a.fx, 1)}원 (${esc(a.fx_source)})</div></div>
      <div class="vf-act"><button class="btn-sm primary" id="acct-new">계좌 추가</button></div></div>
    <div class="kv-grid" style="margin-top:12px">${kv("해외 양도차익 (올해 추정)", krw(a.overseas?.gain_ytd_est))}${kv("공제 후 과세 대상", krw(a.overseas?.taxable))}${kv("해외 양도세 (22%)", krw(a.overseas?.tax), a.overseas?.tax > 0 ? "warn-t" : "")}${kv("배당소득세 (15.4%)", krw(t.div_tax))}${kv("금융소득 추정", krw(a.financial_income_est), a.financial_income_est > 16e6 ? "warn-t" : "")}${kv("지금 전부 판다면 세금", krw(t.tax_now))}</div>
    ${(a.tips || []).map((x) => `<div class="lesson" style="margin-top:8px">💡 ${esc(x)}</div>`).join("")}
  </div>
  <div id="acct-form-box"></div>
  ${acctCards || card("계좌", empty("아직 계좌가 없습니다 — '계좌 추가'로 증권사 계좌를 입력하세요"))}
  ${card("배당 일정 <span class='small dim'>보유 종목 배당락일 (종목 정보를 연 적이 있는 종목)</span>", (a.dividend_calendar || []).length ? `<table class="tight"><tbody>${a.dividend_calendar.map((d) => `<tr><td class="mono small">${esc(d.ex_date)}</td><td>${esc(d.symbol)}</td><td class="small dim">${esc(d.account)}</td><td class="r num">${krw(d.div)}</td></tr>`).join("")}</tbody></table>` : empty("다가오는 배당락 없음"))}
  ${(a.no_div_info || []).length ? `<div class="xs dim" style="margin:0 2px 12px">배당 정보 없음 (종목 화면을 한 번 열면 채워짐): ${a.no_div_info.map(esc).join(", ")}</div>` : ""}`;
  const form = (acct) => {
    const hs = (acct?.holdings || []).map((h) => `${h.symbol},${h.qty},${h.avg_price}`).join("\n");
    $("#acct-form-box").innerHTML = card(acct ? "계좌 수정" : "계좌 추가", `<form id="acct-form" class="acct-form">
      <input name="name" placeholder="이름 (예: 키움 ISA)" value="${esc(acct?.name || "")}" required maxlength="40">
      <select name="type">${types}</select><input name="broker" placeholder="증권사" value="${esc(acct?.broker || "")}" maxlength="30">
      <input name="cash" type="number" step="any" placeholder="현금 (원)" value="${acct?.cash ?? ""}">
      <input name="realized_ytd" type="number" step="any" placeholder="올해 실현손익 (원)" value="${acct?.realized_ytd ?? ""}">
      <input name="dividends_ytd" type="number" step="any" placeholder="올해 받은 배당 (원)" value="${acct?.dividends_ytd ?? ""}">
      <label class="xs"><input type="checkbox" name="low_income" ${acct?.low_income ? "checked" : ""}> ISA 서민형 (400만원 비과세)</label>
      <textarea name="holdings" rows="5" placeholder="종목,수량,평균단가 (한 줄에 하나)&#10;005930,10,71000&#10;NVDA,3,120.5">${esc(hs)}</textarea>
      <div><button class="btn-sm primary">저장</button> <button type="button" class="btn-sm" id="acct-cancel">취소</button></div></form>`);
    const f = $("#acct-form");
    if (acct) f.type.value = acct.type;
    $("#acct-cancel").onclick = () => { $("#acct-form-box").innerHTML = ""; };
    f.onsubmit = async (e) => {
      e.preventDefault();
      const fd = Object.fromEntries(new FormData(f).entries());
      const holdings = (fd.holdings || "").split("\n").map((l) => l.split(",").map((x) => x.trim())).filter((x) => x[0]).map(([symbol, qty, avg]) => ({ symbol, qty: Number(qty), avg_price: Number(avg) }));
      try {
        await api("/api/accounts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...fd, id: acct?.id, low_income: !!fd.low_income, holdings }) });
        if (typeof toast === "function") toast({ title: "계좌 저장", body: fd.name, level: "good" });
        render();
      } catch (err) { if (typeof toast === "function") toast({ title: "저장 실패", body: err.message, level: "bad" }); }
    };
  };
  $("#acct-new").onclick = () => form(null);
  el.querySelectorAll("[data-acct-edit]").forEach((b) => b.onclick = () => form((a.accounts || []).find((x) => x.id === b.dataset.acctEdit)));
  el.querySelectorAll("[data-acct-del]").forEach((b) => b.onclick = async () => {
    if (!confirm("이 계좌를 삭제할까요?")) return;
    await api("/api/accounts", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ delete: b.dataset.acctDel }) });
    render();
  });
}

// 이벤트 캘린더 화면 위: VKOSPI · 기준금리
function marketVolCard(c) {
  const vk = c.vkospi || {}, bk = c.bok || {}, rate = bk.rate || {}, sch = bk.schedule || {};
  const next = (sch.dates || []).find((d) => d >= (c.today || ""));
  return `<div class="grid g-2">
    ${card(`${vk.proxy ? "국내 시장 변동성 (대용)" : "VKOSPI"} <span class="small dim">KOSPI200 옵션 내재변동성 · 앞으로 30일</span>`, vk.available ? `<div class="kv-grid">${kv("현재", vk.level, vk.fear ? "bad-t" : "")}${kv("1년 백분위", R(vk.percentile_1y, 0), vk.fear ? "bad-t" : "")}${kv("예상 변동 1일", "±" + R(vk.daily_move, 2))}${kv("5일", "±" + R(vk.move_5d, 1))}${kv("20일", "±" + R(vk.move_20d, 1))}</div>${dLine([{ data: vk.series || [], color: vk.fear ? "var(--bad)" : "var(--accent-3)" }], { h: 70, yfmt: (v) => v.toFixed(0) })}<div class="xs dim">${esc(vk.source || "")}${vk.fear ? " · 공포 구간 (상위 20%) → 매매 준비 EVENT 주의" : ""}</div>` : empty(vk.message || "없음"), vk.as_of ? asOf(vk.as_of, "fresh", "기준") : "")}
    ${card("한국은행 금통위 · 기준금리", `<div class="kv-grid">${kv("기준금리", rate.current == null ? "-" : rate.current.toFixed(2) + "%")}${kv("다음 금통위", next || "일정 없음")}${kv("일정 출처", esc(sch.source || "-"))}</div>
      ${(rate.changes || []).length ? `<div class="small" style="margin-top:8px">최근 변경: ${rate.changes.slice(-4).reverse().map((x) => `${esc(x.date)} ${x.bp > 0 ? "▲" : "▼"}${Math.abs(x.bp)}bp → ${x.to}%`).join(" · ")}</div>` : `<div class="xs dim" style="margin-top:8px">${esc(rate.message || "")}</div>`}
      <div class="xs dim" style="margin-top:6px">일정: .env QUANT_BOK_DATES (한은 공지) → 한국은행 공개 일정 페이지 순 · 추측으로 날짜를 만들지 않습니다</div>`)}
  </div>`;
}
