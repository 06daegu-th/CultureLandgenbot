// v16 — Stock OS: 현재 상황 한 줄 · OS 헤더(가격·AI·실적 D-n·뉴스 톤·공시·수급·밸류·위험·내 보유) · 신선도(초 단위) ·
// 뉴스/공시 v2(톤·예상 영향·AI 요약·영향 분석) · 실적 · 종목 리스크 · 차트 AI 선/표시 · 과거 동일 조건 검증 · 투자 논리 · 모의 주문 ·
// 종목 페이지 섹션 숨기기/순서(서버 저장)
/* global $, S, api, post, esc, card, empty, badge, num, P, R, render, toast, ICONS, loadPrefs, pill, kv, hbar */

const OS_ST = { ok: "🟢", warn: "🟡", bad: "🔴", none: "⚪", na: "⚪" };
const TONE_CLS = { 긍정: "pos", 부정: "neg", 중립: "" };

// ------------------------------------------------------------ 종목 페이지 섹션 (숨기기 · 순서)
const OS_SECTIONS = [["pf-chart", "차트"], ["pf-news", "뉴스"], ["pf-disc", "공시"], ["pf-earn", "실적"], ["pf-fin", "재무"], ["pf-flow", "수급·알림"],
  ["pf-ai", "AI 판단"], ["pf-risk", "Risk · 주문"], ["pf-mine", "내 보유 · 논리"]];

async function osApplyWidgets() {
  const body = $("#pf-body");
  if (!body) return;
  const p = await loadPrefs();
  const w = p.widgets || { hidden: [], order: [] };
  const hidden = new Set(w.hidden || []);
  const secs = [...body.querySelectorAll(":scope > [data-w]")];
  secs.forEach((e) => { e.style.display = hidden.has(e.dataset.w) ? "none" : ""; });
  if ((w.order || []).length) {
    const rank = (k) => { const i = w.order.indexOf(k); return i < 0 ? 100 + OS_SECTIONS.findIndex(([x]) => x === k) : i; };
    secs.sort((a, b) => rank(a.dataset.w) - rank(b.dataset.w)).forEach((e) => body.appendChild(e));
  }
  osNav(hidden, w.order || []);
}

// v20: 종목 페이지 탭 — 긴 한 페이지 대신 '전체 / 차트 / 뉴스·공시 / 실적·재무 / AI / 위험·주문 / 내 보유'
const OS_TABS = [["all", "전체", null], ["chart", "차트", ["pf-chart"]], ["news", "뉴스·공시", ["pf-news", "pf-disc", "pf-flow"]],
  ["earn", "실적·재무", ["pf-earn", "pf-fin"]], ["ai", "AI 판단", ["pf-ai"]], ["risk", "위험·주문", ["pf-risk"]], ["mine", "내 보유", ["pf-mine"]]];
function osTab(key, hidden) {
  const body = $("#pf-body");
  if (!body) return;
  S.stockTab = key;
  const grp = (OS_TABS.find(([k]) => k === key) || OS_TABS[0])[2];
  hidden = hidden || S.stockHidden || new Set();
  body.querySelectorAll(":scope > [data-w]").forEach((e) => { e.style.display = hidden.has(e.dataset.w) || (grp && !grp.includes(e.dataset.w)) ? "none" : ""; });
  $("#pf-nav")?.querySelectorAll("[data-tab]").forEach((b) => b.classList.toggle("on", b.dataset.tab === key));
  const c = $("#an-chart");  // 숨겨진 채로 그려진 차트는 폭이 0 → 보일 때 다시 맞춤
  if (c?._chart && c.clientWidth) c._chart.applyOptions({ width: c.clientWidth });
}
function osShow(w) {  // 다른 곳(타일·'지금 가장 중요한 것')에서 섹션으로 이동 — 그 섹션이 있는 탭으로 바꾼 뒤 스크롤
  const t = OS_TABS.find(([, , g]) => g && g.includes(w));
  if (t && S.stockTab !== "all" && S.stockTab !== t[0]) osTab(t[0]);
  document.querySelector(`#pf-body > [data-w="${w}"]`)?.scrollIntoView({ behavior: "smooth", block: "start" });
}
function osNav(hidden, order) {
  const nav = $("#pf-nav");
  if (!nav) return;
  S.stockHidden = hidden;
  const tabs = OS_TABS.filter(([, , g]) => !g || g.some((k) => !hidden.has(k)));
  nav.innerHTML = `<div class="pf-tabs" role="tablist">${tabs.map(([k, l]) => `<button role="tab" class="pf-tab" data-tab="${k}">${l}</button>`).join("")}</div>`
    + '<button class="chip" id="pf-wedit" title="섹션 숨기기·순서 (서버 저장)">⚙</button>';
  nav.querySelectorAll("[data-tab]").forEach((b) => b.onclick = () => { osTab(b.dataset.tab, hidden); nav.scrollIntoView({ block: "nearest" }); });
  $("#pf-wedit").onclick = osWidgetEditor;
  osTab(tabs.some(([k]) => k === S.stockTab) ? S.stockTab : "all", hidden);
}

async function osWidgetEditor() {
  const p = await loadPrefs(true);
  const w = { hidden: [...(p.widgets?.hidden || [])], order: [...(p.widgets?.order || [])] };
  let keys = OS_SECTIONS.map(([k]) => k);
  if (w.order.length) keys = [...w.order.filter((k) => keys.includes(k)), ...keys.filter((k) => !w.order.includes(k))];
  const label = Object.fromEntries(OS_SECTIONS);
  const d = document.createElement("div");
  d.className = "kbd-help";
  const draw = () => {
    d.innerHTML = `<div class="card"><div class="card-h"><h3>종목 페이지 섹션 <span class="small dim">서버 저장 — PC·휴대폰 공통</span></h3><div class="right"><button class="btn-sm" id="we-reset">기본값</button> <button class="btn-sm primary" id="we-save">저장</button> <button class="btn-sm" id="we-x">닫기</button></div></div>
      <div class="he-list">${keys.map((k, i) => `<div class="he-it"><label class="chk-chip"><input type="checkbox" data-k="${k}" ${w.hidden.includes(k) ? "" : "checked"}> ${esc(label[k])}</label>
      <span><button class="btn-sm" data-up="${i}" ${i === 0 ? "disabled" : ""}>▲</button><button class="btn-sm" data-dn="${i}" ${i === keys.length - 1 ? "disabled" : ""}>▼</button></span></div>`).join("")}</div></div>`;
    d.querySelectorAll("[data-k]").forEach((c) => c.onchange = () => { w.hidden = c.checked ? w.hidden.filter((x) => x !== c.dataset.k) : [...w.hidden, c.dataset.k]; });
    d.querySelectorAll("[data-up]").forEach((b) => b.onclick = () => { const i = +b.dataset.up; [keys[i - 1], keys[i]] = [keys[i], keys[i - 1]]; draw(); });
    d.querySelectorAll("[data-dn]").forEach((b) => b.onclick = () => { const i = +b.dataset.dn; [keys[i + 1], keys[i]] = [keys[i], keys[i + 1]]; draw(); });
    d.querySelector("#we-x").onclick = () => d.remove();
    d.querySelector("#we-reset").onclick = async () => { await post("/api/prefs", { widgets: { hidden: [], order: [] } }); S.prefs = null; d.remove(); osApplyWidgets(); };
    d.querySelector("#we-save").onclick = async () => {
      const r = await post("/api/prefs", { widgets: { hidden: w.hidden, order: keys } });
      toast({ title: r.error ? "저장 실패" : "섹션 구성 저장", body: r.error || "", level: r.error ? "warn" : "good" });
      S.prefs = null; d.remove(); osApplyWidgets();
    };
  };
  draw();
  d.onclick = (e) => { if (e.target === d) d.remove(); };
  document.body.appendChild(d);
}

// ------------------------------------------------------------ 진입점 (allin.js stockPage 가 /api/stock 결과로 호출)
function stockOS(sym, d) {
  osSituation(d.situation);
  osHeader(sym, d.header, d.position);
  osFresh(d.freshness);
  osVerify(d.verify);
  osThesis(sym, d.thesis, d.header);
  osNews(sym);
  osEarnings(sym);
  osRisk(sym);
  osOverlay(sym);
  osTicket(sym);
  osApplyWidgets();
  const j = $("#tk-jump");
  if (j) j.onclick = () => { $("#pf-ticket")?.scrollIntoView({ behavior: "smooth", block: "start" }); setTimeout(() => $("#tk-amount")?.focus(), 400); };
}

function osSituation(s) {
  const box = $("#pf-sit");
  if (!box || !s || s.error) return;
  box.className = `pf-sit lv-${esc(s.level)}`;
  box.innerHTML = `<b>${esc(s.head)}</b><span class="sit-why">${(s.why || []).map(esc).join(" · ") || "특이사항 없음"}</span>
    <span class="sit-chips">${(s.chips || []).map((c) => `<span class="chip xs">${esc(c)}</span>`).join("")}</span>`;
}

function osHeader(sym, h, pos) {
  const box = $("#pf-os");
  if (!box || !h || h.error) return;
  const n = h.news || {};
  const tile = (label, val, sub = "", cls = "", link = "") => `<div class="os-t ${cls}" ${link ? `data-jump="${link}"` : ""}><div class="xs muted">${label}</div><div class="b">${val}</div>${sub ? `<div class="xs dim">${sub}</div>` : ""}</div>`;
  const tiles = [  // v19: 장 상태 · AI 판단은 위 '첫 화면'에 크게 있으므로 여기서는 뺀다
    tile("실적 발표", h.earnings ? `<span class="${/D-Day|D-1$/.test(h.earnings.d_label) ? "warn-t" : ""}">${esc(h.earnings.d_label)}</span>` : '<span class="dim">-</span>', h.earnings ? esc(h.earnings.date) + (h.earnings.time ? " · " + esc(h.earnings.time) : "") + (h.earnings.estimated ? " 추정" : "") : "일정 없음", "", "pf-earn"),
    tile("뉴스 7일", n.n ? `<span class="up">+${n["긍정"]}</span> · ${n["중립"]} · <span class="down">−${n["부정"]}</span>` : '<span class="dim">없음</span>', "긍정 · 중립 · 부정", "", "pf-news"),
    tile("수급", h.flow?.signal ? esc(h.flow.signal) : '<span class="dim">-</span>', h.flow?.divergence ? esc(h.flow.divergence) : "외국인·기관", "", "pf-flow"),
    tile("밸류에이션", h.valuation ? esc(h.valuation.level) : '<span class="dim">-</span>', h.valuation ? esc(h.valuation.text) : "PER 없음", "", "pf-fin"),
    tile("위험", h.risk ? `<span class="${h.risk.level === "HIGH" ? "bad-t" : h.risk.level === "MEDIUM" ? "warn-t" : "good"}">${esc(h.risk.level)}</span>` : "-", h.risk ? esc(h.risk.text) : "", "", "pf-risk"),
  ];
  if (pos && pos.total_qty) {
    tiles.push(tile("내 보유", `${num(pos.total_qty)}주 <span class="${pos.pnl_pct >= 0 ? "up" : "down"}">${P(pos.pnl_pct)}</span>`,
      `평단 ${pos.avg_price ? num(pos.avg_price, pos.avg_price < 1000 ? 2 : 0) : "-"}${(pos.rows || []).find((r) => r.weight) ? ` · 비중 ${R((pos.rows || []).find((r) => r.weight).weight, 1)}` : ""}`, "mine", "pf-mine"));
  } else tiles.push(tile("내 보유", '<span class="dim">없음</span>', "계좌 입력 시 표시", "", "pf-mine"));
  const discs = (h.disclosures || []).map((x) => `<span class="chip xs ${x.important ? "warn" : ""}" title="${esc(x.title)}">${x.important ? "⚠ " : ""}${esc(x.date.slice(5))} ${esc(x.title.slice(0, 22))}</span>`).join(" ");
  const LVL = { bad: "neg", warn: "warn", info: "" };
  const banner = h.earnings_banner ? `<div class="os-banner">📊 ${esc(h.earnings_banner)}</div>` : "";
  const today = (h.today || []).length ? `<div class="os-today"><span class="xs muted b">오늘 중요한 것</span> ${h.today.map((x) => `<span class="chip ${LVL[x.level] || ""}">${x.icon} ${esc(x.text)}</span>`).join(" ")}</div>`
    : '<div class="os-today xs dim">오늘 중요한 것: 특이 사항 없음</div>';
  const hz = (h.horizons || []).some((x) => x.p != null) ? `<div class="os-hz xs"><span class="muted">AI 기간별 상승확률 <span class="dim">(과거 같은 확률대에서 실제로 오른 비율)</span></span> ${h.horizons.map((x) => `<b>${x.label}</b> ${x.p == null ? '<span class="dim">표본 부족</span>' : R(x.p, 0)} <span class="dim">(${x.n})</span>`).join(" · ")}</div>` : "";
  const fx = h.fx && h.price ? `<div class="os-fx xs"><button class="btn-sm" id="ccy-tg">${S.ccy === "KRW" ? "$ 로 보기" : "₩ 로 보기"}</button>
    <span id="ccy-val">${S.ccy === "KRW" ? `₩${num(h.price * h.fx.usdkrw)} <span class="dim">($${num(h.price, 2)})</span>` : `$${num(h.price, 2)} <span class="dim">≈ ₩${num(h.price * h.fx.usdkrw)}</span>`}</span>
    <span class="dim">환율 ${num(h.fx.usdkrw, 1)} · ${esc(h.fx.source)}</span></div>` : "";
  const t1 = $("#pf-top1");  // v19: 차트 바로 위 '지금 가장 중요한 것' 한 줄
  if (t1) t1.innerHTML = (h.today || []).length ? `<span class="xs muted b">지금 가장 중요한 것</span> <span class="chip ${LVL[h.today[0].level] || ""}">${h.today[0].icon} ${esc(h.today[0].text)}</span>${h.today.length > 1 ? ` <span class="xs dim">외 ${h.today.length - 1}개</span>` : ""}`
    : '<span class="xs dim">지금 가장 중요한 것: 특이 사항 없음</span>';
  box.innerHTML = `${banner}${today}<div class="os-grid">${tiles.join("")}</div>${hz}${fx}${discs ? `<div class="os-disc xs"><span class="muted">최근 공시</span> ${discs}</div>` : ""}`;
  const tg = $("#ccy-tg");
  if (tg) tg.onclick = () => { S.ccy = S.ccy === "KRW" ? "USD" : "KRW"; safeSet("qa_ccy", S.ccy); osHeader(sym, h, pos); };
  box.querySelectorAll("[data-jump]").forEach((t) => t.onclick = () => osShow(t.dataset.jump));
}

// ------------------------------------------------------------ 신선도 (1초마다 다시 셈)
function osAge(s) {
  if (s == null) return "-";
  if (s < 60) return `${Math.max(0, Math.round(s))}초 전`;
  if (s < 3600) return `${Math.round(s / 60)}분 전`;
  if (s < 86400) return `${Math.round(s / 3600)}시간 전`;
  return `${Math.round(s / 86400)}일 전`;
}
function osFresh(f) {
  const box = $("#pf-fresh");
  if (!box || !f || f.error) return;
  const now = Date.now();
  box.innerHTML = `<div class="fresh-row">${f.items.map((i) => `<span class="fr fr-${esc(i.status)}" title="${esc([i.source, i.note, i.sla_s ? `기준 ${osAge(i.sla_s).replace(" 전", "")} 이내` : "장외 — 기준 없음"].filter(Boolean).join(" · "))}">
      ${OS_ST[i.status] || "⚪"} ${esc(i.name)} <b data-at="${esc(i.at || "")}">${i.at ? osAge((now - Date.parse(i.at)) / 1000) : "없음"}</b></span>`).join("")}
    ${(f.warn || []).length ? `<span class="fr-warn">⚠ ${f.warn.map(esc).join(" · ")}</span>` : ""}</div>`;
  clearInterval(S._freshT);
  S._freshT = setInterval(() => {
    if (!document.body.contains(box)) { clearInterval(S._freshT); return; }
    box.querySelectorAll("b[data-at]").forEach((b) => { if (b.dataset.at) b.textContent = osAge((Date.now() - Date.parse(b.dataset.at)) / 1000); });
  }, 1000);
}

// ------------------------------------------------------------ '검증 가능한 AI': 같은 확률의 과거 판단들은 실제로 어땠나
function osVerify(v) {
  const box = $("#pf-verify");
  if (!box || !v || v.error) { if (box) box.innerHTML = ""; return; }
  const calCls = { GOOD: "good", FAIR: "warn-t", POOR: "bad-t" }[v.calibration] || "";
  box.innerHTML = card(`검증 가능한 AI <span class="small dim">'AI 가 분석했으니 오른다' 대신 숫자로</span>`, `
    <div class="vf-line"><b>${esc(v.sentence)}</b></div>
    <div class="kv-grid" style="margin-top:8px">
      ${kv("지금 상승 확률", R(v.prob, 0))}${kv("과거 동일 조건", `${num(v.similar_n)}회`)}${kv("실제 상승", R(v.similar_up, 1))}
      ${kv("Calibration", `<span class="${calCls}">${esc(v.calibration || "-")}</span>`)}${kv("이 종목만", v.same_symbol_n ? `${v.same_symbol_n}회 · ${R(v.same_symbol_up, 0)}` : "-")}
      ${kv("최근 100회 적중", R(v.recent100_hit, 0))}${kv("모델 상태", esc(v.model_status))}</div>
    <div class="xs dim" style="margin-top:6px">과거 동일 조건 = 상승 확률 ±${Math.round(v.band * 100)}%p 안의 채점 끝난 판단 · Calibration: 예측 확률과 실제 상승 비율의 차이 5%p 이내 GOOD · 10%p 이내 FAIR</div>`,
  `<a class="link" href="#scorecard/${esc(v.symbol)}">종목 성적표 ${ICONS.arrow}</a>`);
}

// ------------------------------------------------------------ 투자 논리 (Thesis)
function osThesis(sym, t, h) {
  const box = $("#pf-thesis");
  if (!box) return;
  const cur = h?.price;
  const view = t && !t.error ? `<div class="small"><b>왜 샀나</b> ${esc(t.why)}</div>
      ${t.sell_if ? `<div class="small" style="margin-top:4px"><b>무엇이 틀리면 판다</b> ${esc(t.sell_if)}</div>` : ""}
      <div class="kv-grid" style="margin-top:8px">${kv("목표", t.target ? num(t.target, t.target < 1000 ? 2 : 0) + (cur ? ` <span class="xs dim">${P(t.target / cur - 1)}</span>` : "") : "-", "up")}
        ${kv("무효화", t.stop ? num(t.stop, t.stop < 1000 ? 2 : 0) + (cur ? ` <span class="xs dim">${P(t.stop / cur - 1)}</span>` : "") : "-", "down")}
        ${kv("점검일", esc(t.review_date || "-"))}${kv("작성", esc((t.updated_at || "").slice(0, 10)))}</div>
      ${cur && t.target && cur >= t.target ? '<div class="lesson" style="margin-top:8px">🎯 목표 도달 — 이익 실현 또는 논리 갱신</div>' : ""}
      ${cur && t.stop && cur <= t.stop ? '<div class="veto" style="margin-top:8px">⛔ 무효화 가격 이탈 — 논리가 틀렸다고 정한 가격</div>' : ""}` : '<div class="small muted">아직 적지 않았습니다. 사기 전에 "왜 사는지 · 무엇이 틀리면 팔지"를 적어 두면, 목표·무효화 가격에 닿을 때 알림이 옵니다.</div>';
  box.innerHTML = card("투자 논리 <span class='small dim'>Thesis · 30분마다 감시</span>", `${view}
    <details class="th-form" ${t ? "" : "open"} style="margin-top:10px"><summary class="small">${t ? "수정" : "작성"}</summary>
      <div class="form-grid" style="margin-top:8px">
        <label>왜 샀나<textarea id="th-why" rows="2" maxlength="1000">${esc(t?.why || "")}</textarea></label>
        <label>무엇이 틀리면 판다<textarea id="th-sell" rows="2" maxlength="1000">${esc(t?.sell_if || "")}</textarea></label>
        <label>목표 가격<input id="th-target" type="number" step="any" value="${t?.target ?? ""}"></label>
        <label>무효화(손절) 가격<input id="th-stop" type="number" step="any" value="${t?.stop ?? ""}"></label>
        <label>점검일<input id="th-review" type="date" value="${esc(t?.review_date || "")}"></label>
      </div>
      <div style="margin-top:8px;display:flex;gap:8px"><button class="btn-sm primary" id="th-save">저장</button>${t ? '<button class="btn-sm" id="th-del">삭제</button>' : ""}<span class="xs" id="th-msg"></span></div></details>`);
  $("#th-save").onclick = async () => {
    const r = await post("/api/thesis", { symbol: sym, why: $("#th-why").value, sell_if: $("#th-sell").value, target: $("#th-target").value, stop: $("#th-stop").value, review_date: $("#th-review").value });
    if (r.error) { $("#th-msg").innerHTML = `<span class="bad-t">${esc(r.error)}</span>`; return; }
    toast({ title: "투자 논리 저장", body: sym, level: "good" });
    osThesis(sym, r.thesis, h);
  };
  const del = $("#th-del");
  if (del) del.onclick = async () => { if (!confirm("투자 논리를 지울까요?")) return; await post("/api/thesis", { symbol: sym, delete: true }); osThesis(sym, null, h); };
}

// ------------------------------------------------------------ 뉴스 · 공시 v2
async function osNews(sym) {
  const nb = $("#pf-news"), db = $("#pf-disc");
  if (!nb) return;
  nb.innerHTML = card("뉴스 · AI 요약", '<div class="xs dim">불러오는 중…</div>');
  let d;
  try { d = await api(`/api/stock/news?symbol=${encodeURIComponent(sym)}`); } catch (e) { nb.innerHTML = card("뉴스", empty(e.message)); return; }
  if (S.symbol && S.symbol !== sym) return;
  const c = d.counts || {};
  const ai = d.ai_summary;
  const aiBox = ai ? `<div class="ai-sum"><div class="small"><b>🤖 AI 요약</b> <span class="chip xs ${TONE_CLS[ai.tone] || ""}">${esc(ai.tone || "-")}</span> <span class="xs dim">예상 영향 ${ai.impact > 0 ? "+" : ""}${ai.impact} (−2~+2) · ${esc((ai.at || "").slice(0, 16).replace("T", " "))} UTC</span></div>
      <ul class="plain small">${(ai.summary || []).map((x) => `<li>· ${esc(x)}</li>`).join("")}</ul>${(ai.watch || []).length ? `<div class="xs muted">지켜볼 점: ${ai.watch.map(esc).join(" · ")}</div>` : ""}</div>`
    : `<div class="ai-sum dim small">${d.has_llm ? `${d.ai_stale ? "새 뉴스가 있어 요약이 오래됐습니다. " : ""}<button class="btn-sm primary" id="nw-ai">AI 요약 만들기</button> <span class="xs">뉴스·공시 제목만 근거 · 목록에 없는 사실은 만들지 않음</span>` : "LLM 키가 없어 규칙 요약만 표시합니다"}</div>`;
  const items = (d.items || []).map((x) => `<div class="nw-it" data-nid="${x.id}"><div class="nw-h"><span class="chip xs ${TONE_CLS[x.tone] || ""}">${esc(x.tone)}</span>
      <a href="${esc(x.url || "#")}" target="_blank" rel="noopener noreferrer" class="small b">${esc(x.title)}</a></div>
      <div class="xs dim">${esc(x.source || "")} · ${esc(x.at)}${(x.events || []).length ? " · " + x.events.map(esc).join(", ") : ""}</div>
      <div class="xs muted">📈 ${esc(x.impact)} <button class="btn-xs" data-imp="${x.id}">영향 분석</button></div></div>`).join("");
  nb.innerHTML = card(`뉴스 <span class="small dim">최근 30일 중요도 순 ${(d.items || []).length}건 · 7일 톤 ${esc(d.overall)}</span>`, `
    <div class="small" style="margin-bottom:8px">${(d.rule_summary || []).map((x) => `<div>· ${esc(x)}</div>`).join("")}</div>${aiBox}
    <div class="tone-bar" title="최근 7일 긍정/중립/부정"><i class="pos" style="flex:${c["긍정"] || 0}"></i><i style="flex:${c["중립"] || 0}"></i><i class="neg" style="flex:${c["부정"] || 0}"></i></div>
    ${items || empty("저장된 이 종목 뉴스가 없습니다")}`);
  const b = $("#nw-ai");
  if (b) b.onclick = async () => {
    b.disabled = true; b.textContent = "요약 중…";
    const r = await post("/api/stock/digest", { symbol: sym });
    if (r.error) { b.disabled = false; b.textContent = "다시 시도"; toast({ title: "AI 요약 실패", body: r.error, level: "warn" }); return; }
    osNews(sym);
  };
  nb.querySelectorAll("[data-imp]").forEach((x) => x.onclick = () => openDetail("news", +x.dataset.imp));  // v18: 원문·번역·쉬운 설명·영향
  if (db) {
    const rows = (d.disclosures || []).map((x) => `<div class="nw-it ${x.important ? "imp" : ""}"><div class="nw-h">${x.important ? '<span class="chip xs warn">⚠ 중요</span>' : ""}${x.polarity > 0 ? '<span class="chip xs pos">호재성</span>' : x.polarity < 0 ? '<span class="chip xs neg">악재성</span>' : ""}
      <a href="#" data-disc="${x.id}" class="small b" title="원문·번역·요약·중요한 숫자·주가 영향">${esc(x.title)}</a> <a class="xs" href="${esc(x.url || "#")}" target="_blank" rel="noopener noreferrer">원문 ↗</a></div>
      <div class="xs dim">${esc(x.source || "DART")} · ${esc(x.date)}${(x.events || []).length ? " · " + x.events.map(esc).join(", ") : ""}</div>${x.summary ? `<div class="xs muted">${esc(x.summary.slice(0, 220))}</div>` : ""}</div>`).join("");
    db.innerHTML = card(`공시 <span class="small dim">최근 90일 · 중요 공시 강조</span>`, rows || empty(/^\d{6}$/.test(sym) ? "최근 90일 공시 없음 (DART 키 필요)" : "해외 종목 — DART 공시 없음"));
  }
}

async function osImpact(id) {
  const d = document.createElement("div");
  d.className = "kbd-help";
  d.innerHTML = `<div class="card"><div class="xs dim">영향 분석 중…</div></div>`;
  d.onclick = (e) => { if (e.target === d || e.target.dataset.x) d.remove(); };
  document.body.appendChild(d);
  const r = await api(`/api/news-impact?id=${id}`).catch((e) => ({ error: e.message }));
  if (r.error) { d.innerHTML = `<div class="card">${empty(r.error)}<button class="btn-sm" data-x="1">닫기</button></div>`; return; }
  const n = r.news;
  const rel = (r.related || []).map((x) => `<div class="imp-it"><div><a href="#analysis/${esc(x.symbol)}"><b>${esc(x.name)}</b></a> <span class="xs dim">${esc(x.sector)}</span> ${x.ai ? badge(x.ai.action) : ""}</div>
    <div class="xs">과거 비슷한 뉴스 ${x.similar_n}회 → 다음 날 평균 <b class="${(x.similar_avg_1d || 0) >= 0 ? "up" : "down"}">${P(x.similar_avg_1d, 2)}</b> · 5일 ${P(x.similar_avg_5d, 2)} · 오른 비율 ${R(x.similar_up_share, 0)}</div>
    <div class="xs">이번 뉴스 뒤 실제: 1일 ${P(x.after_this?.["1d"], 2)} · 5일 ${P(x.after_this?.["5d"], 2)}</div>
    <div class="xs"><b>AI 판단 변화</b> ${esc(x.ai_change?.text || "-")}</div>
    ${(x.examples || []).length ? `<div class="xs dim">예: ${x.examples.map((e) => `${esc(e.at)} ${esc(e.title)}`).join(" / ")}</div>` : ""}</div>`).join("");
  d.innerHTML = `<div class="card"><div class="card-h"><h3>이 뉴스가 내 투자에 어떤 의미인가</h3><div class="right"><button class="btn-sm" data-x="1">닫기</button></div></div>
    <div class="small b">${esc(n.title)}</div><div class="xs dim">${esc(n.source || "")} · ${esc(n.at)} · 톤 ${esc(n.tone)}${(n.events || []).length ? " · " + n.events.map(esc).join(", ") : ""}</div>
    <div class="imp-flow xs">원문 → 관련 종목·업종 → 과거 유사 뉴스 → 예상 영향 → AI 판단 변화</div>
    ${rel || empty("관련 종목 없음")}
    ${(r.sector_peers || []).length ? `<div class="xs muted" style="margin-top:6px">같은 업종: ${r.sector_peers.map((p) => `<a href="#analysis/${esc(p.symbol)}">${esc(p.name)}</a>`).join(" · ")}</div>` : ""}
    <div class="xs dim" style="margin-top:8px">${esc(r.note)}</div></div>`;
}

// ------------------------------------------------------------ 실적
async function osEarnings(sym) {
  const box = $("#pf-earn");
  if (!box) return;
  const spct = (v) => v == null ? "-" : P(v / 100, 1);  // 수집기는 % 단위(5.2 = +5.2%)로 저장
  let e;
  try { e = await api(`/api/stock/earnings?symbol=${encodeURIComponent(sym)}`); } catch { box.innerHTML = ""; return; }
  const rows = (e.rows || []).map((r) => `<tr><td class="small">${esc(r.date)}${r.period ? ` <span class="xs dim">${esc(r.period)}</span>` : ""}</td>
    <td class="r num">${r.eps_estimate != null ? num(r.eps_estimate, 2) : "-"}</td><td class="r num">${r.eps_actual != null ? num(r.eps_actual, 2) : "-"}</td>
    <td class="r ${r.beat ? "up" : r.beat === false ? "down" : ""}">${spct(r.eps_surprise_pct ?? r.base_surprise_pct)}</td>
    <td class="r">${spct(r.revenue_surprise_pct)}</td>
    <td class="r ${r.reaction_1d >= 0 ? "up" : "down"}">${P(r.reaction_1d, 2)}</td><td class="r">${P(r.drift_20d, 1)}</td><td class="xs dim">${esc(r.source)}</td></tr>`).join("");
  const trend = earnTrend(e.rows || []);
  const up = e.upcoming || (e.kr_upcoming ? { date: e.kr_upcoming.date || e.kr_upcoming, estimated: true } : null);
  box.innerHTML = card(`실적 <span class="small dim">예상 vs 실제 · 서프라이즈 · 발표 후 반응(시장 대비)</span>`, `
    <div class="kv-grid">${kv("다음 발표", up ? esc(String(up.date).slice(0, 10)) + (up.estimated ? " (추정)" : "") : "-")}${kv("예상 EPS", up?.eps_estimate != null ? num(up.eps_estimate, 2) : "-")}
      ${kv("상회 비율", R(e.beat_rate, 0))}${kv("상회 시 반응", P(e.avg_reaction_beat, 2))}${kv("하회 시 반응", P(e.avg_reaction_miss, 2))}${kv("매출 YoY", P(e.revenue_yoy, 1))}</div>
    ${trend}
    ${rows ? `<div class="scroll" style="margin-top:8px"><table class="tight"><thead><tr><th>발표</th><th class="r">EPS 예상</th><th class="r">EPS 실제</th><th class="r">EPS 서프</th><th class="r">매출 서프</th><th class="r">다음날</th><th class="r">20일</th><th>출처</th></tr></thead><tbody>${rows}</tbody></table></div>` : empty("실적 이력 없음 — 종목 상세·국내 컨센서스 수집 후 채워집니다")}
    <div class="xs muted" style="margin-top:6px">가이던스: ${esc(e.guidance)}</div><div class="xs dim">${esc(e.note)} · 출처 ${(e.sources || []).map(esc).join(", ")}</div>`);
}

// v18: 최근 실적 추세 — 분기별 EPS 예상(회색) vs 실제(상회 초록 · 하회 빨강) 막대
function earnTrend(rows) {
  const xs = rows.filter((r) => r.eps_actual != null).slice(0, 12).reverse();
  if (xs.length < 2) return "";
  const vals = xs.flatMap((r) => [r.eps_actual, r.eps_estimate ?? r.eps_actual]);
  const hi = Math.max(0, ...vals), lo = Math.min(0, ...vals), span = hi - lo || 1;
  const W = 560, H = 140, pad = 18, bw = (W - pad * 2) / xs.length;
  const y = (v) => pad + (hi - v) / span * (H - pad * 2);
  const bar = (x, v, w, c) => { const a = y(Math.max(v, 0)), b = y(Math.min(v, 0)); return `<rect x="${x.toFixed(1)}" y="${a.toFixed(1)}" width="${w.toFixed(1)}" height="${Math.max(1, b - a).toFixed(1)}" fill="${c}" rx="2"/>`; };
  const bars = xs.map((r, i) => {
    const x = pad + i * bw, w = bw * 0.36;
    const c = r.beat === false ? "var(--down)" : r.beat ? "var(--up)" : "#64748b";
    return (r.eps_estimate != null ? bar(x + bw * 0.1, r.eps_estimate, w, "rgba(148,163,184,.55)") : "") + bar(x + bw * 0.1 + w + 2, r.eps_actual, w, c)
      + `<text x="${(x + bw / 2).toFixed(1)}" y="${H - 3}" font-size="9" text-anchor="middle" fill="currentColor" opacity=".6">${esc(String(r.period || r.date || "").slice(0, 7))}</text>`;
  }).join("");
  return `<div class="earn-trend"><div class="xs muted">최근 실적 추세 — <span style="color:#94a3b8">■</span> EPS 예상 · <span class="up">■</span> 실제(상회) · <span class="down">■</span> 실제(하회)</div>
    <svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" role="img" aria-label="분기별 EPS 예상 대비 실제"><line x1="${pad}" x2="${W - pad}" y1="${y(0).toFixed(1)}" y2="${y(0).toFixed(1)}" stroke="currentColor" opacity=".25"/>${bars}</svg></div>`;
}

// ------------------------------------------------------------ 종목 리스크
async function osRisk(sym) {
  const box = $("#pf-risk");
  if (!box) return;
  let r;
  try { r = await api(`/api/stock/risk?symbol=${encodeURIComponent(sym)}`); } catch { box.innerHTML = ""; return; }
  if (r.error) { box.innerHTML = card("종목 리스크", empty(r.error)); return; }
  const dv = r.dividend || {};
  box.innerHTML = card(`종목 리스크 <span class="small dim">과거 1년 일봉</span>`, `
    <div class="lesson small">${esc(r.read)}</div>
    <div class="kv-grid" style="margin-top:8px">${kv("변동성 20일", R(r.vol_20d, 0))}${kv("변동성 1년", R(r.vol_1y, 0))}${kv("베타", r.beta ?? "-")}${kv("1년 최대 낙폭", P(r.mdd_1y, 1), "down")}
      ${kv("1일 VaR 95%", R(r.var95_1d, 1))}${kv("내 보유 VaR", r.var95_krw ? "₩" + num(r.var95_krw) : "-")}${kv("청산 일수", r.days_to_liquidate ?? "-")}${kv("상·하한가", r.limit_hits ?? "-")}</div>
    <div class="xs muted" style="margin-top:6px">배당: ${dv.yield != null ? `수익률 ${R(dv.yield > 1 ? dv.yield / 100 : dv.yield, 2)}` : "정보 없음"}${dv.payout != null ? ` · 배당성향 ${R(dv.payout, 0)}` : ""}${(dv.events || []).length ? " · " + dv.events.map((x) => `${esc(x.label || x.kind)} ${esc(String(x.date || "").slice(0, 10))}`).join(", ") : ""}</div>
    <div class="xs dim">VaR = 하루에 이보다 크게 잃는 날이 20일에 1번꼴 · 청산 일수 = 보유 금액 ÷ (평균 거래대금 × 10%)</div>`);
}

// ------------------------------------------------------------ 차트 위 AI 정보 (지지/저항 · 뉴스·공시·실적 표시)
async function osOverlay(sym) {
  let o;
  try { o = await api(`/api/stock/overlay?symbol=${encodeURIComponent(sym)}`); } catch { return; }
  if (!o || o.error) return;
  for (let i = 0; i < 40; i++) {
    const el = $("#an-chart");
    if (!el || el.dataset.sym !== sym || !el._series) { await new Promise((r) => setTimeout(r, 150)); continue; }
    const s = el._series;
    const LC = { support: ["#14b8a6", 1], resistance: ["#f472b6", 1], avg_cost: ["#facc15", 0], high52: ["#94a3b8", 3], low52: ["#94a3b8", 3],
      range_hi: ["#60a5fa", 2], range_lo: ["#60a5fa", 2] };
    const LP = { avg_cost: 9, support: 5, resistance: 5, range_hi: 4, range_lo: 4, high52: 3, low52: 3 };
    (o.lines || []).filter((l) => LC[l.kind]).forEach((l) =>
      chartLine(el, { price: l.price, color: LC[l.kind][0], width: l.kind === "avg_cost" ? 2 : 1, style: LC[l.kind][1], title: l.title, pri: LP[l.kind] }));
    const times = el._times || [];
    const at = (d) => { const t = Date.parse(d + "T00:00:00Z") / 1000; return times.find((x) => x >= t); };
    const seen = new Set(), mk = (el._markers || []).map((m) => ({ ...m }));
    (o.marks || []).forEach((m) => {
      const t = at(m.date);
      if (t == null) return;
      const key = `${t}:${m.kind}`;
      if (seen.has(key)) return;
      seen.add(key);
      if (m.kind === "earnings") mk.push({ time: t, position: "aboveBar", shape: "square", color: "#a855f7", text: "실적" });
      else if (m.kind === "disclosure" && m.important) mk.push({ time: t, position: "aboveBar", shape: "square", color: "#f59e0b", text: "공시" });
      else if (m.kind === "volume") mk.push({ time: t, position: "belowBar", shape: "circle", color: "#a78bfa", text: `거래량 ${m.ratio}배` });
      else if (m.kind === "macro") mk.push({ time: t, position: "aboveBar", shape: "square", color: "#38bdf8", text: { fomc: "FOMC", cpi: "CPI", nfp: "고용", pce: "PCE" }[m.event] || "지표" });
      else if (m.kind === "move") mk.push({ time: t, position: m.chg > 0 ? "aboveBar" : "belowBar", shape: m.chg > 0 ? "arrowUp" : "arrowDown", color: m.chg > 0 ? "#f0474f" : "#3b8cff", text: `${m.chg > 0 ? "+" : ""}${(m.chg * 100).toFixed(0)}%` });
      else if (m.kind === "ai_change") mk.push({ time: t, position: m.to === "SELL" ? "aboveBar" : "belowBar", shape: m.to === "SELL" ? "arrowDown" : m.to === "BUY" ? "arrowUp" : "circle",
        color: m.to === "BUY" ? "#22c55e" : m.to === "SELL" ? "#ef4444" : "#94a3b8", text: `AI ${{ BUY: "매수", SELL: "매도", HOLD: "관망", NO_TRADE: "쉼" }[m.to] || m.to}` });
      else if (m.kind === "news" && m.tone !== "중립") mk.push({ time: t, position: "belowBar", shape: "circle", color: m.tone === "긍정" ? "#22c55e" : "#ef4444", text: "" });
    });
    mk.sort((a, b) => a.time - b.time);
    try { s.setMarkers(declutterMarkers(mk, times)); } catch { /* 표시 실패는 무시 */ }
    const leg = document.createElement("div");
    leg.className = "chart-legend xs";
    leg.innerHTML = `<span style="color:#14b8a6">┈ 지지</span> <span style="color:#f472b6">┈ 저항</span> <span style="color:#a855f7">■ 실적</span> <span style="color:#f59e0b">■ 중요 공시</span> <span style="color:#22c55e">● 긍정</span>/<span style="color:#ef4444">●</span> 부정 뉴스 <span style="color:#f0474f">▲</span>/<span style="color:#3b8cff">▼</span> 급등락 <span style="color:#94a3b8">↑ AI 신호 변화</span> <span style="color:#a78bfa">● 거래량 급증</span> <span style="color:#38bdf8">■ FOMC·CPI·고용</span> <span style="color:#facc15">━ 내 평균 매수가</span> <span style="color:#60a5fa">┄ 5일 보통 범위</span> <span style="color:#94a3b8">┄ 52주 고/저</span> <span class="dim">· ${esc(o.note)}</span>`;
    el.parentElement?.appendChild(leg);
    return;
  }
}

// ------------------------------------------------------------ 모의 주문 (수동 장부 · 실제 돈 아님)
async function osTicket(sym) {
  const box = $("#pf-ticket");
  if (!box) return;
  const isKR = /^\d{6}$/.test(sym);
  if (!isKR) { box.innerHTML = ""; return; }
  box.innerHTML = card(`모의 주문 <span class="small dim">수동 모의 장부 · 실제 돈·실제 주문 아님</span>`, `
    <div class="tk-form"><div class="tabs" id="tk-side"><button data-k="buy" class="on">매수</button><button data-k="sell">매도</button></div>
      <input id="tk-amount" type="number" inputmode="numeric" placeholder="금액 (원)" min="0" step="10000">
      <span class="xs dim">또는</span><input id="tk-qty" type="number" inputmode="numeric" placeholder="수량 (주)" min="0">
      <button class="btn-sm" id="tk-preview">미리 보기</button></div>
    <div id="tk-out" style="margin-top:10px"></div>`, '<a class="link" href="#manual">모의 장부</a>');
  let side = "buy";
  box.querySelectorAll("#tk-side button").forEach((b) => b.onclick = () => { side = b.dataset.k; box.querySelectorAll("#tk-side button").forEach((x) => x.classList.toggle("on", x === b)); });
  const out = $("#tk-out");
  const run = async (place) => {
    const amount = $("#tk-amount").value, qty = $("#tk-qty").value;
    let v;
    if (place) v = await post("/api/ticket", { symbol: sym, side, qty, amount, confirm: true });
    else v = await api(`/api/ticket?symbol=${encodeURIComponent(sym)}&side=${side}&qty=${encodeURIComponent(qty)}&amount=${encodeURIComponent(amount)}`).catch((e) => ({ error: e.message }));
    if (v.error) { out.innerHTML = `<div class="veto">${esc(v.error)}</div>`; return; }
    const cls = v.verdict === "통과" ? "good" : v.verdict === "축소" ? "warn-t" : "bad-t";
    out.innerHTML = `<div class="tk-sum"><b class="${cls}">${esc(v.verdict)}</b> · ${side === "buy" ? "매수" : "매도"} ${num(v.allowed_qty)}주 / 요청 ${num(v.want_qty)}주
      · 예상 체결 ${v.est_fill ? num(v.est_fill, 0) : "-"} · 금액 ₩${num(v.notional)} · 수수료·세금 ₩${num(v.fee_tax)} · 주문 후 비중 ${R(v.weight_after, 1)}</div>
      <div class="xs dim">기준가 ${num(v.price, 0)} (${esc(v.price_source)}) · 모의 현금 ₩${num(v.cash)} · 보유 ${num(v.held)}주</div>
      ${v.ai ? `<div class="xs" style="margin-top:4px">AI: ${badge(v.ai.action)} ${esc(v.ai.headline)}</div>` : ""}
      <div class="tr-rows" style="margin-top:6px">${(v.steps || []).map((s) => `<div class="tr-row"><span class="tr-ic">${OS_ST[s.status] || "⚪"}</span><div><div class="small b">${esc(s.gate)}</div><div class="xs muted">${esc(s.detail)}</div></div></div>`).join("")}</div>
      ${v.message ? `<div class="small ${v.placed ? "good" : "bad-t"}" style="margin-top:6px">${esc(v.message)}</div>` : ""}
      ${!place && v.allowed_qty > 0 ? '<button class="btn-sm primary" id="tk-place" style="margin-top:8px">모의 주문 실행</button>' : ""}
      <div class="xs dim" style="margin-top:6px">${esc(v.note)}</div>`;
    const pb = $("#tk-place");
    if (pb) pb.onclick = () => { if (confirm(`모의 ${side === "buy" ? "매수" : "매도"} ${v.allowed_qty}주 — 실제 돈이 아닌 수동 모의 장부에 기록합니다. 실행할까요?`)) run(true); };
    if (place && v.placed) toast({ title: "모의 체결", body: v.message, level: "good" });
  };
  $("#tk-preview").onclick = () => run(false);
}
