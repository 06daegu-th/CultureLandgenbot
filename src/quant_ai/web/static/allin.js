// v15 — 올인원 종목 페이지(섹션 이동 · 데이터 신뢰도 · D-day · 왜 샀나/안 샀나 · 사전 리스크 게이트 · 뉴스/공시 요약) ·
// 홈 편집(카드 숨기기·순서, 서버 저장) · AI/모델 Health · 외부 알림 설정(종류별 채널 · 조용한 시간)
/* global $, S, api, post, esc, card, empty, badge, kv, R, P, num, asOf, pill, render, toast, ICONS, CAL_ICON, stockExtras, bindRun */

const ST_ICON = { ok: lvDot("good"), warn: lvDot("warn"), bad: lvDot("bad"), na: lvDot("idle") };  // v21: 이모지 대신 색 점

// ------------------------------------------------------------ 사용자 설정 (서버)
async function loadPrefs(force = false) {
  if (S.prefs && !force) return S.prefs;
  try { S.prefs = await api("/api/prefs"); } catch { S.prefs = { home: { hidden: [], order: [] }, notify: {} }; }
  return S.prefs;
}

// ------------------------------------------------------------ 홈 편집
const HOME_SEL = {
  today: ["#home-today", "오늘 할 일 · 시장 한눈에"], readiness: [".rd-strip", "매매 준비 띠"], live: ["#live-strip", "실시간 시세 띠"],
  setup: ["#setup-box", "시작 체크리스트"], proof: [".grid.h0", "Net Alpha · 안전 상태"], market: [".grid.h1", "지수 · 시장 상태 · 뉴스"],
  watch: [".grid.h2", "차트 · 관심 종목 · AI 종합"], portfolio: [".grid.h3", "포트폴리오 · AI 분석 · 신호 · 이벤트"],
  system: [".grid.h4", "시스템 · 교훈 · 바로가기"],
};

async function applyHomeLayout(root) {
  const p = await loadPrefs();
  const h = p.home || { hidden: [], order: [] };
  const blocks = Object.entries(HOME_SEL).map(([k, [sel]]) => [k, root.querySelector(sel)]).filter(([, e]) => e);
  const hidden = new Set(h.hidden || []);
  blocks.forEach(([k, e]) => { e.style.display = hidden.has(k) ? "none" : ""; e.dataset.home = k; });
  const order = h.order || [];
  if (order.length) {
    const anchor = root.querySelector(".asof-line");
    const rank = (k) => { const i = order.indexOf(k); return i < 0 ? 100 + Object.keys(HOME_SEL).indexOf(k) : i; };
    blocks.sort((a, b) => rank(a[0]) - rank(b[0])).forEach(([, e]) => root.appendChild(e));
    if (anchor) root.prepend(anchor);
  }
  if (!root.querySelector("#home-edit")) {
    const b = document.createElement("button");
    b.id = "home-edit"; b.className = "btn-sm home-edit"; b.textContent = "⚙ 홈 편집";
    b.onclick = () => homeEditor(root);
    const line = root.querySelector(".asof-line");
    (line || root).appendChild(b);
  }
}

async function homeEditor(root) {
  const p = await loadPrefs(true);
  const h = { hidden: [...(p.home?.hidden || [])], order: [...(p.home?.order || [])] };
  let keys = Object.keys(HOME_SEL);
  if (h.order.length) keys = [...h.order.filter((k) => HOME_SEL[k]), ...keys.filter((k) => !h.order.includes(k))];
  const d = document.createElement("div");
  d.className = "kbd-help";
  const draw = () => {
    d.innerHTML = `<div class="card"><div class="card-h"><h3>홈 편집 <span class="small dim">서버에 저장 — PC·휴대폰 공통</span></h3><div class="right"><button class="btn-sm" id="he-reset">기본값</button> <button class="btn-sm primary" id="he-save">저장</button> <button class="btn-sm" id="he-x">닫기</button></div></div>
      <div class="he-list">${keys.map((k, i) => `<div class="he-it"><label class="chk-chip"><input type="checkbox" data-k="${k}" ${h.hidden.includes(k) ? "" : "checked"}> ${esc(HOME_SEL[k][1])}</label>
        <span><button class="btn-sm" data-up="${i}" ${i === 0 ? "disabled" : ""}>▲</button><button class="btn-sm" data-dn="${i}" ${i === keys.length - 1 ? "disabled" : ""}>▼</button></span></div>`).join("")}</div>
      <div class="xs dim" style="margin-top:8px">체크를 끄면 그 묶음을 홈에서 숨깁니다. 매매 준비 띠는 켜 두는 것을 권장합니다.</div></div>`;
    d.querySelectorAll("[data-k]").forEach((c) => c.onchange = () => { h.hidden = c.checked ? h.hidden.filter((x) => x !== c.dataset.k) : [...h.hidden, c.dataset.k]; });
    d.querySelectorAll("[data-up]").forEach((b) => b.onclick = () => { const i = +b.dataset.up; [keys[i - 1], keys[i]] = [keys[i], keys[i - 1]]; draw(); });
    d.querySelectorAll("[data-dn]").forEach((b) => b.onclick = () => { const i = +b.dataset.dn; [keys[i + 1], keys[i]] = [keys[i], keys[i + 1]]; draw(); });
    d.querySelector("#he-x").onclick = () => d.remove();
    d.querySelector("#he-reset").onclick = async () => { await post("/api/prefs", { home: { hidden: [], order: [] } }); S.prefs = null; d.remove(); render(); };
    d.querySelector("#he-save").onclick = async () => {
      const r = await post("/api/prefs", { home: { hidden: h.hidden, order: keys } });
      toast({ title: r.error ? "저장 실패" : "홈 구성 저장", body: r.error || "", level: r.error ? "warn" : "good" });
      S.prefs = null; d.remove(); render();
    };
  };
  draw();
  d.onclick = (e) => { if (e.target === d) d.remove(); };
  document.body.appendChild(d);
}

// ------------------------------------------------------------ 올인원 종목 페이지
const SEC = [["an-chart", "차트"], ["pf-why", "왜 BUY/SELL"], ["pf-hold", "보유 · 적중률"], ["pf-story", "왜 샀나/안 샀나"], ["pf-pretrade", "사전 리스크"],
  ["pf-desk", "매매 계획"], ["pf-digest", "뉴스·공시 요약"], ["pf-sections", "실적·재무"], ["pf-extra", "알림·수급·관계"]];

function stockNav() {
  const nav = $("#pf-nav");
  if (!nav) return;
  nav.innerHTML = SEC.map(([id, l]) => `<button class="chip" data-sec="${id}">${l}</button>`).join("");
  nav.querySelectorAll("[data-sec]").forEach((b) => b.onclick = () => {
    const t = document.getElementById(b.dataset.sec);
    (t?.closest(".card") || t)?.scrollIntoView({ behavior: "smooth", block: "start" });
  });
}

async function stockPage(sym, a) {
  if (typeof osNav !== "function") stockNav();  // v16: 섹션 이동은 os.js (숨기기·순서 포함)
  if (typeof stockExtras === "function") stockExtras(sym, a || {}, {});  // 외부 자료(프로필)를 기다리지 않고 알림·관계·공시부터
  let d;
  try { d = await api(`/api/stock?symbol=${encodeURIComponent(sym)}`); } catch { return; }
  if (S.view !== "analysis" || (S.symbol && S.symbol !== sym)) return;
  trustChip(d.trust);
  ddayStrip(d.events);
  storyCard(sym, d.story);
  if (!$("#pf-news")) digestCard(d.digest);  // v16 은 뉴스·공시 v2(os.js)가 대신
  pretradeCard(sym);
  if (typeof stockOS === "function") stockOS(sym, d);
}

function trustChip(t) {
  const box = $("#pf-trust");
  if (!box || !t || t.error) return;
  box.innerHTML = `<details class="trust ${esc(t.status)}"><summary>${ST_ICON[t.status]} 데이터 신뢰도 <b>${t.score}</b>/100 <span class="xs dim">${esc((t.checks.find((c) => c.key === "fresh") || {}).detail || "")}</span></summary>
    <div class="tr-rows" style="margin-top:6px">${t.checks.map((c) => `<div class="tr-row"><span class="tr-ic">${ST_ICON[c.status]}</span><div><div class="small b">${esc(c.label)}</div><div class="xs muted">${esc(c.detail)}</div></div></div>`).join("")}</div></details>`;
}

function ddayStrip(evs) {
  const box = $("#pf-dday");
  if (!box || !Array.isArray(evs)) return;
  box.innerHTML = evs.length ? `<div class="dday">${evs.slice(0, 8).map((e) => `<span class="dd ${e.d_day >= 0 && e.d_day <= 1 ? "hot" : ""} ${e.d_day < 0 ? "past" : ""}" title="${esc(e.source || "")}${e.estimated ? " · 추정" : ""}"><b>${esc(e.d_label)}</b> ${calDot(e.kind)} ${esc(e.title)}${e.scope === "시장" ? ' <span class="xs dim">시장</span>' : ""}${e.estimated ? ' <span class="xs dim">추정</span>' : ""}</span>`).join("")}</div>`
    : '<div class="xs dim">이벤트 캘린더에 이 종목·시장의 가까운 일정 없음</div>';
}

function storyCard(sym, s) {
  const box = $("#pf-story");
  if (!box || !s || s.error) return;
  const tl = (s.timeline || []).map((t) => `<tr><td class="xs dim">${esc(t.ts)}</td><td><span class="chip xs">${esc({ order: "주문", risk_block: "차단", dedupe: "중복 방지", signal: "신호", reconcile: "잔고 대조", recovery: "복구" }[t.kind] || t.kind)}</span></td><td class="small" style="white-space:normal">${esc(t.message)}${t.reason ? ` · <b>${esc(t.reason)}</b>` : ""}${(t.reasons || []).length ? `<div class="xs muted">${t.reasons.map(esc).join(" · ")}</div>` : ""}</td></tr>`).join("");
  box.innerHTML = card(`왜 샀나 / 왜 안 샀나 <span class="small dim">${esc(s.mode)} 장부 · 실제 기록</span>`, `
    <div class="why-head"><b>${esc(s.headline)}</b></div>
    <ul class="plain small" style="margin-top:8px">${(s.facts || []).map((f) => `<li>· ${esc(f)}</li>`).join("")}</ul>
    ${s.signal ? `<div class="small" style="margin-top:6px">AI 합의 ${badge(s.signal.action)} P(상승) ${R(s.signal.prob_up, 0)} · 신뢰도 ${s.signal.confidence} <span class="xs dim">${esc(s.signal.as_of)}</span></div>` : ""}
    ${tl ? `<div class="scroll" style="max-height:220px;margin-top:8px"><table class="tight"><tbody>${tl}</tbody></table></div>` : '<div class="xs dim" style="margin-top:6px">최근 90일 이 종목 주문·차단 기록 없음</div>'}
    ${s.rule ? `<div class="xs dim" style="margin-top:6px">규칙: ${esc(s.rule)}</div>` : ""}`, s.plan_at ? asOf(s.plan_at, "fresh", "계획") : "");
}

async function pretradeCard(sym, weight = "", mode = S.ptMode || "paper") {
  const box = $("#pf-pretrade");
  if (!box) return;
  let r;
  try { r = await api(`/api/pretrade?symbol=${encodeURIComponent(sym)}&mode=${mode}${weight ? `&weight=${weight}` : ""}`); } catch (e) { r = { error: e.message }; }
  const V = { 통과: "ok", 축소: "warn", 차단: "bad" };
  const body = r.error ? empty(r.error) : `
    <div class="pt-top"><span class="st-pill st-${V[r.verdict]}" style="font-size:14px">${esc(r.verdict)}</span>
      <span class="small">원하는 비중 ${R(r.want_weight, 1)} (${num(r.want_qty)}주) → 허용 <b>${R(r.allowed_weight, 1)} (${num(r.allowed_qty)}주)</b></span></div>
    <div class="tr-rows" style="margin-top:8px">${r.steps.map((s) => `<div class="tr-row"><span class="tr-ic">${ST_ICON[s.status] || ""}</span><div><div class="small b">${esc(s.gate)}</div><div class="xs muted">${esc(s.detail)}</div></div></div>`).join("")}</div>
    <div class="xs dim" style="margin-top:6px">${esc(r.note)} · ${esc(r.as_of)} · 평가 자산 ${num(r.equity)}</div>`;
  box.innerHTML = card("사전 리스크 게이트 <span class='small dim'>지금 이 종목을 사면?</span>", `
    <div class="pt-form"><select id="pt-mode">${["paper", "shadow", "live"].map((m) => `<option ${m === mode ? "selected" : ""}>${m}</option>`).join("")}</select>
      <input id="pt-w" type="number" min="0.5" max="100" step="0.5" placeholder="비중 %" value="${esc(weight)}"><button class="btn-sm primary" id="pt-go">시험</button></div>${body}`);
  const q = (id) => box.querySelector(id);  // 늦게 온 응답이면 box 는 이미 화면 밖 — 문서 전체가 아니라 box 안에서 찾는다
  q("#pt-go").onclick = () => { S.ptMode = q("#pt-mode").value; pretradeCard(sym, q("#pt-w").value, S.ptMode); };
}

function digestCard(g) {
  const box = $("#pf-digest");
  if (!box || !g || g.error) return;
  const hl = (g.headlines || []).map((n) => `<div class="pfn"><div style="flex:1;min-width:0"><div class="t">${esc(n.title)}</div><div class="xs dim">${esc(n.at)} · ${esc(n.source)} · 감성 ${n.sent > 0 ? "+" : ""}${n.sent}</div></div></div>`).join("");
  const ds = (g.disclosures || []).map((x) => `<div class="pfn"><div style="flex:1;min-width:0"><div class="t">📑 ${esc(x.title)}</div>${x.summary ? `<div class="xs muted disc-sum">${esc(x.summary)}</div>` : ""}<div class="xs dim">${esc(x.date)}</div></div></div>`).join("");
  box.innerHTML = card(`뉴스·공시 자동 요약 <span class="small dim">${esc(g.tone || "")}</span>`, `
    <ul class="plain small">${(g.summary || []).map((l) => `<li>→ ${esc(l)}</li>`).join("")}</ul>
    ${(g.extracted || []).length ? `<div style="margin-top:6px">${g.extracted.map((e) => `<span class="chip xs ${e.polarity > 0 ? "ok" : e.polarity < 0 ? "bad" : ""}" title="${esc(e.title)}">${esc(e.label)} ${esc(e.date)}</span>`).join(" ")}</div>` : ""}
    ${hl || ds ? `<div class="grid g-2" style="margin-top:8px"><div>${hl || '<div class="xs dim">중요 뉴스 없음</div>'}</div><div>${ds || '<div class="xs dim">최근 공시 없음</div>'}</div></div>` : ""}`);
}

// ------------------------------------------------------------ AI · 모델 Health
async function viewAIHealth(el) {
  const h = await api("/api/health/ai");
  if (h.error) { el.innerHTML = card("AI · 모델 Health", empty(h.error)); return; }
  const D = { decaying: ["bad", "성능 저하"], watch: ["warn", "관찰"], stable: ["ok", "안정"], insufficient: ["none", "표본 부족"] };
  const rows = h.analysts.map((a) => `<tr><td><b>${esc(a.label)}</b><div class="xs dim">${esc(a.analyst)}</div></td><td class="r num">${num(a.n)}</td><td class="r num">${a.n_scored}</td>
    <td class="r num">${R(a.hit, 0)}</td><td class="r num">${R(a.hit_ref, 0)}</td><td class="r num ${a.hit_recent != null && a.hit_ref != null && a.hit_recent < a.hit_ref - 0.05 ? "down" : ""}">${R(a.hit_recent, 0)}</td>
    <td>${pill(D[a.decay]?.[0] || "none", D[a.decay]?.[1] || a.decay)}</td><td class="r num">${a.abstain_14d == null ? "-" : R(a.abstain_14d, 0)}</td>
    <td class="xs ${a.stale ? "warn-t" : "dim"}">${esc(a.last || "-")}</td></tr>`).join("");
  const pv = h.providers.map((p) => `<tr><td>${esc(p.provider)}</td><td class="r num">${p.ok}</td><td class="r num ${p.error ? "bad-t" : ""}">${p.error}</td><td class="r num">${p.cached}</td><td class="r num">${p.error_rate == null ? "-" : R(p.error_rate, 0)}</td><td class="r num">${p.latency_ms ?? "-"}</td></tr>`).join("");
  const m = h.model;
  const G = { green: "ok", yellow: "warn", red: "bad", na: "none" };
  const dc = (x, t) => x && x.status ? `<div class="kvt"><div class="xs muted">${t}</div><div class="small">${pill(D[x.status]?.[0] || "none", D[x.status]?.[1] || x.status)} ${x.n ? `n=${x.n}` : ""}</div><div class="xs dim">${esc(x.message || "")}</div></div>` : "";
  el.innerHTML = `
  <div class="card rd-hero ${{ ok: "rd-ready", warn: "rd-caution", bad: "rd-not" }[h.status]}">
    <div class="rd-top"><div><div class="xs muted">AI · 모델 Health — AI 별 성적 · 성능 저하 자동 감지 · 응답 상태 · 모델 상태</div>
      <h2>${esc({ ok: "정상", warn: "관찰 필요", bad: "문제 있음" }[h.status])}</h2><div class="small muted">${asOf(h.as_of)} ${h.has_llm ? "" : "· LLM 키 없음 → 휴리스틱 AI 로 동작"}</div>
      ${h.problems.length ? `<div class="veto" style="margin-top:8px">⛔ ${h.problems.map(esc).join("<br>⛔ ")}</div>` : ""}</div>
      <div class="vf-act"><button class="btn-sm primary" data-run="model_decay">지금 저하 점검</button><a class="btn-sm" href="#ai">AI 성적 · 보정</a><a class="btn-sm" href="#power">실제 예측력</a></div></div>
  </div>
  <div class="gates">${m.checks.map((c) => `<div class="gate g-${esc(c.status)}"><div class="gate-h"><b>${esc(c.key)}</b></div><div class="small">${esc(c.detail)}</div></div>`).join("")}
    <div class="gate"><div class="gate-h"><b>CHAMPION</b></div><div class="small">${esc(m.champion || "없음 (후보가 게이트 탈락)")}${m.trained ? ` · 학습 ${esc(m.trained)}` : ""}</div></div></div>
  ${card("AI 별 성적 · 성능 저하 <span class='small dim'>저하 = 최근 적중이 초기보다 유의하게 낮고 CUSUM/추세가 확인 → 알림</span>", rows ? `<div class="scroll"><table class="tight"><thead><tr><th>AI</th><th class="r">판단</th><th class="r">채점</th><th class="r">적중(전체)</th><th class="r">초기</th><th class="r">최근 20일</th><th>저하</th><th class="r">기권(14일)</th><th>마지막 판단</th></tr></thead><tbody>${rows}</tbody></table></div>` : empty("AI 판단 기록 없음"))}
  <div class="grid g-2">
    ${card("모델 노후 · 예측력", `<div class="kv-grid">${dc(m.decay.consensus, "AI 합의")}${dc(m.decay.quant, "퀀트 모델")}
      <div class="kvt"><div class="xs muted">독립 평가</div><div class="small">${esc(m.evaluation.verdict || m.evaluation.status || "-")}</div></div>
      <div class="kvt"><div class="xs muted">전진 검증</div><div class="small">${esc(m.power.label || m.power.decision || "-")} ${m.power.n != null ? `· ${m.power.n}건` : ""}${m.power.more_needed ? ` · 약 ${m.power.more_needed}건 더 필요` : ""}</div><div class="xs dim">${esc(m.power.bottom_line || "")}</div></div>
      <div class="kvt"><div class="xs muted">드리프트</div><div class="small">${esc(m.drift.message || m.drift.status || "-")}</div></div></div>
      <div class="xs dim" style="margin-top:6px">노후 점검 ${esc(m.decay.at ? m.decay.at.slice(0, 16).replace("T", " ") : "기록 없음")} (12시간마다 자동)</div>`)}
    ${card("LLM 응답 상태 <span class='small dim'>최근 24시간</span>", pv ? `<table class="tight"><thead><tr><th>공급자</th><th class="r">성공</th><th class="r">실패</th><th class="r">캐시</th><th class="r">실패율</th><th class="r">지연 ms</th></tr></thead><tbody>${pv}</tbody></table>
      ${(h.llm_errors || []).length ? `<div class="xs muted" style="margin-top:6px">${h.llm_errors.map((e) => `${esc(e.at)} ${esc(e.provider)}: ${esc(e.error)}`).join("<br>")}</div>` : ""}` : empty(h.has_llm ? "최근 24시간 호출 없음" : "LLM 키 없음 — .env 에 무료 키를 넣으면 AI 가 실제 모델로 판단"))}
  </div>`;
  if (typeof bindRun === "function") bindRun(el);
}

// ------------------------------------------------------------ 외부 알림 설정 (서버 저장)
const NOTIFY_KO = { price: "급등·급락", signal: "AI 신호", event: "이벤트 재분석", disclosure: "공시", news: "중요 뉴스", earnings: "실적 발표", result: "예측 채점",
  guardian: "자동 감시", ladder: "승격·강등", market: "시장·AI 성능", job: "작업 실패", rule: "내 알림 규칙", readiness: "매매 준비", power: "예측력 판정",
  ops: "운영", brief: "아침 브리핑", report: "일일 리포트" };

async function notifyCard(el) {
  const box = document.createElement("div");
  box.id = "notify-card";
  el.prepend(box);
  const p = await loadPrefs(true);
  const n = p.notify || {}, def = p.defaults || { external: [], push: [] }, q = n.quiet || { on: false, start: "23:00", end: "07:00" };
  const on = (ch, k) => (n[ch] && k in n[ch]) ? n[ch][k] : def[ch].includes(k);
  box.innerHTML = card("외부 알림 설정 <span class='small dim'>서버 저장 · 텔레그램/디스코드 · 웹 푸시</span>", `
    <div class="small muted" style="margin-bottom:6px">연결: 텔레그램/디스코드 ${p.channels?.external ? "✅" : "❌ (.env 설정 필요)"} · 웹 푸시 ${p.channels?.push ? "✅" : "❌ (설치 후 구독)"}</div>
    <div class="scroll"><table class="tight"><thead><tr><th>알림 종류</th><th>텔레그램·디스코드</th><th>웹 푸시</th></tr></thead><tbody>
    ${(p.kinds || []).map((k) => `<tr><td>${esc(NOTIFY_KO[k] || k)}</td><td><input type="checkbox" data-ch="external" data-k="${k}" ${on("external", k) ? "checked" : ""}></td><td><input type="checkbox" data-ch="push" data-k="${k}" ${on("push", k) ? "checked" : ""}></td></tr>`).join("")}</tbody></table></div>
    <div class="pt-form" style="margin-top:8px"><label class="chk-chip"><input type="checkbox" id="nq-on" ${q.on ? "checked" : ""}> 🌙 조용한 시간</label>
      <input id="nq-s" type="time" value="${esc(q.start)}"> ~ <input id="nq-e" type="time" value="${esc(q.end)}"><button class="btn-sm primary" id="nq-save">저장</button></div>
    <div class="xs dim" style="margin-top:6px">조용한 시간에는 외부로 보내지 않고 사이트 알림센터에만 쌓입니다. 긴급(자동 정지 등 🔴)은 예외로 항상 보냅니다.</div>`);
  $("#nq-save").onclick = async () => {
    const body = { notify: { external: {}, push: {}, quiet: { on: $("#nq-on").checked, start: $("#nq-s").value, end: $("#nq-e").value } } };
    box.querySelectorAll("input[data-ch]").forEach((c) => { body.notify[c.dataset.ch][c.dataset.k] = c.checked; });
    const r = await post("/api/prefs", body);
    S.prefs = null;
    toast({ title: r.error ? "저장 실패" : "알림 설정 저장", body: r.error || (body.notify.quiet.on ? `조용한 시간 ${body.notify.quiet.start}~${body.notify.quiet.end}` : ""), level: r.error ? "warn" : "good" });
  };
}
