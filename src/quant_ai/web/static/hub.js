// v16 — 새 화면: 오늘(Action Center·AI 브리핑) · 관심종목(그룹·신호 변화) · 거래 안 한 이유 · AI 성적표(종목별·공개 1000회) ·
// AI Lab(구조·모델 단계·실패 연구) · 데이터 건강(점수·Sentinel·Fail-Closed) · Portfolio OS(위험·시뮬레이션) · 내 투자 성향 ·
// 실전 검증 진행표(KIS·WS·슬리피지·Forward·장기 + 비용 실측) · 규제·보안·라이선스·감사 · 수동 모의 장부
/* global $, S, api, post, esc, card, empty, badge, num, P, R, render, toast, ICONS, pill, kv, hbar, OS_ST, osAge */

const LV_CLS = { ok: "good", warn: "warn-t", bad: "bad-t", setup: "dim", na: "dim", none: "dim" };
const pfModeQ = () => `mode=${encodeURIComponent(S.pfMode && ["paper", "shadow", "live"].includes(S.pfMode) ? S.pfMode : "paper")}`;
const stRow = (c) => `<div class="tr-row"><span class="tr-ic">${OS_ST[c.status] || "⚪"}</span><div><div class="small b">${esc(c.title || c.name || c.key)}</div><div class="xs muted">${esc(c.detail || "")}</div></div></div>`;
const bar100 = (v) => `<div class="hbar ${v >= 0.8 ? "" : v >= 0.4 ? "warn" : "neg"}"><i style="width:${Math.max(2, Math.min(100, (v || 0) * 100))}%"></i></div>`;

// ------------------------------------------------------------ 홈: 오늘의 AI 브리핑 (①~⑤ + 오늘 확인할 것 3)
async function osHomeBrief(root) {
  if (!root) return;
  const box = document.createElement("div");
  box.id = "home-brief";
  const anchor = root.querySelector("#home-today");
  if (anchor) anchor.after(box); else root.prepend(box);
  let b;
  try { b = await api(`/api/briefing?${pfModeQ()}`); } catch { box.remove(); return; }
  if (!(b.items || []).length && !(b.check3 || []).length) { box.remove(); return; }
  const circ = ["①", "②", "③", "④", "⑤"];
  box.innerHTML = card(`오늘의 AI 브리핑 <span class="small dim">${esc(b.as_of)}</span>`, `<div class="grid g-2">
    <div>${(b.items || []).map((x, i) => `<a class="brief-it" href="${esc(x.link)}"><b>${circ[i]}</b> ${esc(x.text)}</a>`).join("") || '<div class="xs dim">큰 변화 없음</div>'}</div>
    <div><div class="small muted" style="margin-bottom:4px">오늘 확인할 것</div>${(b.check3 || []).map((c) => `<a class="brief-it" href="#analysis/${esc(c.symbol)}"><b>${esc(c.name)}</b>${c.held ? ' <span class="chip xs">보유</span>' : ""} <span class="xs muted">${(c.why || []).map(esc).join(" · ")}</span></a>`).join("") || '<div class="xs dim">없음</div>'}</div></div>`,
  `<a class="link" href="#action">Action Center ${ICONS.arrow}</a>`);
}

// ------------------------------------------------------------ 오늘 · Action Center
async function viewActionCenter(el) {
  const [a, b] = await Promise.all([api(`/api/action-center?${pfModeQ()}`), api(`/api/briefing?${pfModeQ()}`).catch(() => ({}))]);
  const check = (a.check || []).map((c) => `<a class="ac-it" href="#analysis/${esc(c.symbol)}"><b>${esc(c.name)}</b>${c.held ? ' <span class="chip xs">보유</span>' : ""}<div class="xs muted">${(c.why || []).map(esc).join(" · ")}</div></a>`).join("");
  const evs = (a.events || []).map((e) => `<a class="ac-it" href="${e.symbol ? `#analysis/${esc(e.symbol)}` : "#calendar"}"><b class="${e.d_day <= 1 ? "warn-t" : ""}">${esc(e.d_label)}</b> ${esc(e.title)}${e.held ? ' <span class="chip xs">보유</span>' : ""}${e.estimated ? ' <span class="xs dim">추정</span>' : ""}</a>`).join("");
  const sig = (a.signal_changes || []).map((c) => `<a class="ac-it" href="#analysis/${esc(c.symbol)}"><b>${esc(c.name)}</b> ${badge(c.from)} → ${badge(c.to)} <span class="xs dim">${esc(c.at || "")}</span></a>`).join("");
  const disc = (a.disclosures || []).map((d) => `<a class="ac-it" href="#analysis/${esc(d.symbol)}"><b>${esc(d.name)}</b> <span class="small">${esc(d.title)}</span> <span class="xs dim">${esc(d.date || "")}</span></a>`).join("");
  const rk = a.risk || {};
  el.innerHTML = `
  <div class="card ac-hero"><div class="card-h"><h3>오늘 할 일 · Action Center <span class="small dim">${esc(a.as_of)}</span></h3><div class="right"><a class="btn-sm" href="#notrade">거래 안 한 이유</a></div></div>
    ${a.empty_hint ? `<div class="lesson small">${esc(a.empty_hint)}</div>` : ""}
    ${(b.items || []).length ? `<div class="brief-row">${b.items.map((x, i) => `<a class="chip" href="${esc(x.link)}">${"①②③④⑤"[i]} ${esc(x.text)}</a>`).join("")}</div>` : ""}</div>
  <div class="grid g-3">
    ${card("확인할 종목 <span class='small dim'>최대 3</span>", check || empty("오늘 두드러진 종목 없음"))}
    ${card("주의 이벤트", evs || empty("가까운 큰 일정 없음"), '<a class="link" href="#calendar">일정</a>')}
    ${card("AI 신호 변경", sig || empty("신호 변화 없음"))}
  </div>
  <div class="grid g-2">
    ${card(`위험 <span class="small ${LV_CLS[rk.level] || ""}">${esc(rk.headline || "")}</span>`, (rk.rising || []).length ? `<ul class="plain small">${rk.rising.map((x) => `<li>⚠ ${esc(x)}</li>`).join("")}</ul>` : empty("위험 증가 신호 없음"), '<a class="link" href="#pos">Portfolio OS</a>')}
    ${card("확인 필요 공시", disc || empty("관심·보유 종목의 중요 공시 없음"))}
  </div>`;
}

// ------------------------------------------------------------ 관심종목 (그룹 · 국내/미국/ETF · 신호 변화)
async function viewWatch(el) {
  const w = await api("/api/watchlist");
  S.wlGroup = S.wlGroup || "전체"; S.wlType = S.wlType || "전체";
  const rows = (w.rows || []).filter((r) => (S.wlGroup === "전체" || r.group === S.wlGroup) && (S.wlType === "전체" || r.type === S.wlType));
  const tabs = (id, list, cur) => `<div class="tabs" id="${id}">${["전체", ...list].map((g) => `<button data-k="${esc(g)}" class="${cur === g ? "on" : ""}">${esc(g)}</button>`).join("")}</div>`;
  const tr = rows.map((r) => `<tr><td><button class="star-btn on" data-unstar="${esc(r.symbol)}" title="관심 해제">★</button></td>
    <td><a href="#analysis/${esc(r.symbol)}"><b>${esc(r.name)}</b></a> <span class="xs dim">${esc(r.symbol)}</span>${r.held ? ' <span class="chip xs">보유</span>' : ""}</td>
    <td class="small">${esc(r.type)}</td><td class="r num">${r.last != null ? num(r.last, r.last < 1000 ? 2 : 0) : "-"}</td>
    <td class="r ${r.chg_pct >= 0 ? "up" : "down"}">${P(r.chg_pct, 2)}</td><td>${badge(r.ai)} <span class="xs num">${R(r.prob_up, 0)}</span></td>
    <td class="small">${r.change ? `<span class="${r.change_dir > 0 ? "up" : r.change_dir < 0 ? "down" : ""}">${esc(r.change)}</span>` : '<span class="dim">-</span>'}</td>
    <td><select data-grp="${esc(r.symbol)}" class="mini-sel">${[...new Set([...(w.groups || []), "기본"])].map((g) => `<option ${g === r.group ? "selected" : ""}>${esc(g)}</option>`).join("")}<option value="__new">+ 새 그룹</option></select></td></tr>`).join("");
  el.innerHTML = card(`관심종목 <span class="small dim">★ ${w.n_star}개 · 최근 검색은 검색창(/)에서</span>`, `
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px">${tabs("wl-grp", w.groups || [], S.wlGroup)}${tabs("wl-type", w.types || [], S.wlType)}</div>
    ${tr ? `<div class="scroll"><table class="tight"><thead><tr><th></th><th>종목</th><th>구분</th><th class="r">현재가</th><th class="r">등락</th><th>AI</th><th>신호 변화</th><th>그룹</th></tr></thead><tbody>${tr}</tbody></table></div>` : empty("관심종목이 없습니다 — 종목 화면에서 ☆ 를 누르세요 (단축키 s)")}`);
  el.querySelectorAll("#wl-grp button").forEach((b) => b.onclick = () => { S.wlGroup = b.dataset.k; render(); });
  el.querySelectorAll("#wl-type button").forEach((b) => b.onclick = () => { S.wlType = b.dataset.k; render(); });
  el.querySelectorAll("[data-unstar]").forEach((b) => b.onclick = async () => { await post("/api/star", { symbol: b.dataset.unstar, on: false }); render(); });
  el.querySelectorAll("[data-grp]").forEach((s) => s.onchange = async () => {
    let g = s.value;
    if (g === "__new") { g = (prompt("새 그룹 이름 (20자 이내)") || "").trim(); if (!g) { render(); return; } }
    const r = await post("/api/watch-group", { symbol: s.dataset.grp, group: g === "기본" ? null : g });
    if (r.error) toast({ title: "그룹 변경 실패", body: r.error, level: "warn" });
    render();
  });
}

// ------------------------------------------------------------ 왜 거래하지 않았나
async function viewNoTrade(el) {
  const r = await api(`/api/notrade?${pfModeQ()}&days=${S.ntDays || 30}`);
  const lab = Object.fromEntries((r.by_category || []).map((c) => [c.key, c.label]));
  const cats = (r.by_category || []).map((c) => `<span class="chip">${esc(c.label)} <b>${c.n}</b></span>`).join(" ");
  const rows = (r.rows || []).map((x) => `<tr><td class="small mono">${esc(x.ts)}</td><td>${x.symbol ? `<a href="#analysis/${esc(x.symbol)}"><b>${esc(x.name)}</b></a>` : esc(x.name)}</td>
    <td><span class="chip xs ${x.blocked ? "neg" : ""}">${esc(x.outcome)}</span></td><td class="small">${esc(lab[x.category] || x.category)}</td>
    <td class="small" style="white-space:normal">${(x.reasons || []).map(esc).join(" · ")}${x.plan ? `<div class="xs dim">계획: ${esc(x.plan)}</div>` : ""}</td></tr>`).join("");
  el.innerHTML = card(`왜 거래하지 않았나 <span class="small dim">${esc(r.mode)} 장부 · 최근 ${r.days}일</span>`, `
    <div class="small b">${esc(r.headline)}</div><div style="margin:8px 0">${cats}</div>
    <div class="tabs" id="nt-days">${[7, 30, 90].map((d) => `<button data-k="${d}" class="${(S.ntDays || 30) === d ? "on" : ""}">${d}일</button>`).join("")}</div>
    ${rows ? `<div class="scroll" style="margin-top:8px"><table class="tight"><thead><tr><th>시각</th><th>종목</th><th>결과</th><th>분류</th><th>사유</th></tr></thead><tbody>${rows}</tbody></table></div>` : empty("막히거나 줄어든 매수가 없습니다")}
    <div class="xs dim" style="margin-top:8px">분류: Risk(한도) · 이벤트 · 데이터 부족 · 포트폴리오 한도 · 유동성 · 긴급 정지/HALTED · 매매 준비 · AI 판단${r.ai_on ? "" : " · (AI 오버레이가 꺼져 있어 'AI BUY 미실행'은 집계 안 함)"}</div>`);
  el.querySelectorAll("#nt-days button").forEach((b) => b.onclick = () => { S.ntDays = +b.dataset.k; render(); });
}

// ------------------------------------------------------------ AI 성적표 (전체 · 종목별 · 공개 1000회)
function calTable(cal) {
  return (cal || []).length ? `<table class="tight"><thead><tr><th>예측 확률</th><th class="r">n</th><th class="r">평균 예측</th><th class="r">실제 상승</th><th></th></tr></thead><tbody>${cal.map((c) => `<tr><td>${esc(c.range)}</td><td class="r num">${c.n}</td><td class="r num">${R(c.predicted, 0)}</td><td class="r num">${R(c.actual, 0)}</td><td>${Math.abs(c.actual - c.predicted) <= 0.05 ? "🟢" : Math.abs(c.actual - c.predicted) <= 0.1 ? "🟡" : "🔴"}</td></tr>`).join("")}</tbody></table>` : empty("표본 부족");
}
async function viewScorecard(el) {
  const sym = S.param ? decodeURIComponent(S.param) : "";
  const [c, pub] = await Promise.all([api(`/api/ai-card${sym ? `?symbol=${encodeURIComponent(sym)}` : ""}`), sym ? Promise.resolve(null) : api("/api/ai-public?n=1000")]);
  const w = c.windows || {};
  const col = (k) => { const x = w[k]; return x ? `<div class="sc-col"><div class="small muted">최근 ${k}회 <span class="xs dim">(n=${x.n})</span></div>
    <div class="big">${R(x.hit, 0)}</div><div class="xs dim">적중 · 95% ${R(x.ci95?.[0], 0)}~${R(x.ci95?.[1], 0)} · 기준(오른 비율) ${R(x.base_up, 0)}</div>
    <div class="kv-grid" style="margin-top:6px">${kv("Brier", x.brier?.toFixed(3) ?? "-")}${kv("Brier Skill", `<span class="${x.brier_skill >= 0 ? "good" : "bad-t"}">${x.brier_skill?.toFixed(3) ?? "-"}</span>`)}${kv("실제 수익/건", P(x.ret_all, 2))}${kv("AI Alpha/건", `<span class="${x.ai_alpha >= 0 ? "good" : "bad-t"}">${P(x.ai_alpha, 2)}</span>`)}</div>
    <div class="xs" style="margin-top:6px">${esc(x.verdict)}</div></div>` : `<div class="sc-col">${empty(`최근 ${k}회 — 표본 없음`)}</div>`; };
  const pubCard = pub ? card(`공개 AI 성적표 <span class="small dim">최근 ${pub.n}건 전부 · ${esc(pub.from)}~${esc(pub.to)} · 좋은 숫자만 고르지 않음</span>`, `
    <div class="kv-grid">${kv("방향 정확도", `${R(pub.direction_accuracy, 1)} <span class="xs dim">(${R(pub.ci95?.[0], 0)}~${R(pub.ci95?.[1], 0)})</span>`)}${kv("기준(오른 비율)", R(pub.base_up, 1))}
      ${kv("Brier Skill", `<span class="${pub.brier_skill >= 0 ? "good" : "bad-t"}">${pub.brier_skill}</span>`)}${kv("Calibration", `${pub.calibration_pct}%`)}
      ${kv("Net Alpha/건", `<span class="${pub.net_alpha_per_pred >= 0 ? "good" : "bad-t"}">${P(pub.net_alpha_per_pred, 2)}</span>`)}${kv("누적 Alpha", P(pub.alpha_cum, 1))}${kv("MDD", P(pub.mdd, 1), "down")}${kv("90일 안정성", esc(pub.stability_90d))}</div>
    <div class="lesson small" style="margin-top:8px">${esc(pub.verdict)}</div>
    <div class="grid g-2" style="margin-top:8px"><div>${calTable(pub.calibration)}</div>
      <div><div class="small muted">실패 사례 (크게 틀린 순)</div><table class="tight"><tbody>${(pub.failures || []).map((f) => `<tr><td class="xs">${esc(f.at)}</td><td><a href="#analysis/${esc(f.symbol)}">${esc(f.name)}</a></td><td>${badge(f.action)}</td><td class="r num">${R(f.prob_up, 0)}</td><td class="r ${f.realized >= 0 ? "up" : "down"}">${P(f.realized, 1)}</td></tr>`).join("")}</tbody></table></div></div>
    <div class="xs dim" style="margin-top:6px">${esc(pub.method)} · ${esc(pub.stability_msg || "")}</div>`) : "";
  el.innerHTML = `
  <div class="card"><div class="card-h"><h3>AI 성적표 ${sym ? `— <a href="#analysis/${esc(sym)}">${esc(sym)}</a>` : "— 전체"} <span class="small dim">결과가 확정된 판단만 · ${esc(c.horizon)} · 마지막 ${esc(c.last || "-")}</span></h3>
    <div class="right"><input id="sc-sym" class="mini-in" placeholder="종목 코드" value="${esc(sym)}"><button class="btn-sm" id="sc-go">보기</button>${sym ? '<a class="btn-sm" href="#scorecard">전체</a>' : ""}</div></div>
    <div class="small">채점 ${num(c.n_scored)}건 / 전체 판단 ${num(c.n_total)}건</div>
    <div class="sc-grid" style="margin-top:10px">${col("50")}${col("100")}${col("300")}</div>
    ${card("확률 보정 (Calibration) — 최근 100회", calTable(c.main?.calibration), "", "flat")}
    <div class="xs dim">${esc(c.note)} · Brier Skill = 1 − Brier/기준 Brier (0 보다 작으면 '늘 평균 확률을 말하는 것'보다 못함) · AI Alpha = AI 가 고른 판단(P≥50%)의 평균 수익 − 전체 평균</div></div>
  ${pubCard}`;
  const go = () => { const v = $("#sc-sym").value.trim(); location.hash = v ? `#scorecard/${encodeURIComponent(v)}` : "#scorecard"; };
  $("#sc-go").onclick = go;
  $("#sc-sym").onkeydown = (e) => { if (e.key === "Enter") go(); };
}

// ------------------------------------------------------------ AI Lab (구조 · 모델 단계 · 실패 연구)
async function viewAILabV16(el) {
  const [l, f] = await Promise.all([api("/api/ai-lab"), api("/api/failure-lab")]);
  const node = (x) => `<div class="lab-n st-${esc(x.status)}"><div class="small b">${OS_ST[x.status] || "⚪"} ${esc(x.name)}</div><div class="xs muted">${x.n_scored ? `채점 ${num(x.n_scored)} · 적중 ${R(x.hit, 0)}${x.hit_recent != null ? ` · 최근 ${R(x.hit_recent, 0)}` : ""}` : esc(x.detail)}</div></div>`;
  const models = (l.models || []).map((m) => `<tr><td><span class="chip xs ${m.status === "champion" ? "pos" : m.status === "rejected" ? "neg" : ""}">${esc(m.stage)}</span></td><td class="small"><b>${esc(m.name)}</b> <span class="xs dim">${esc(m.version)}</span></td><td class="xs">${esc(m.created)}</td>
    <td class="xs">${Object.entries(m.metrics || {}).map(([k, v]) => `${esc(k)} ${typeof v === "number" ? v.toFixed(3) : esc(v)}`).join(" · ") || "-"}</td><td class="xs muted" style="white-space:normal">${esc(m.notes || m.stage_desc)}</td></tr>`).join("");
  const tags = (f.by_tag || []).map((t) => `<tr><td class="small">${esc(t.name)}</td><td class="r num">${num(t.n)}</td><td class="r num">${R(t.share, 0)}</td><td class="r num">${R(t.hit_with, 0)}</td><td class="r num">${R(t.hit_without, 0)}</td><td class="r num ${t.z <= -2 ? "bad-t" : t.z >= 2 ? "good" : ""}">${t.z ?? "-"}</td></tr>`).join("");
  el.innerHTML = `
  ${card(`AI Lab <span class="small dim">여러 AI → 합의 → Risk Gate → 최종 판단 · ${esc(l.as_of)}</span>`, `
    <div class="lab-flow"><div class="lab-col">${(l.layers || []).map(node).join("")}</div><div class="lab-arrow">→</div>
      <div class="lab-col"><div class="lab-n st-${esc(l.ensemble.status)}"><div class="small b">${OS_ST[l.ensemble.status] || "⚪"} Ensemble (가중 합의)</div><div class="xs muted">30일 ${l.ensemble.n_30d}건 · ${Object.entries(l.ensemble.actions_30d || {}).map(([k, v]) => `${esc(k)} ${v}`).join(" · ")}</div><div class="xs">${esc(l.ensemble.detail)}</div></div></div>
      <div class="lab-arrow">→</div><div class="lab-col"><div class="lab-n"><div class="small b">🛡 Risk Gate</div><div class="xs muted">${esc(l.risk_gate.detail)}</div><div class="xs dim">${Object.entries(l.risk_gate.codes || {}).map(([k, v]) => `${esc(k)} ${v}`).join(" · ")}</div></div></div>
      <div class="lab-arrow">→</div><div class="lab-col"><div class="lab-n"><div class="small b">🎯 최종</div><div class="xs muted">${esc(l.final.detail)}</div></div></div></div>
    <div class="xs dim" style="margin-top:8px">${esc(l.note)}</div>`)}
  <div class="grid g-2">
    ${card("모델 단계 <span class='small dim'>Research → Challenger → Shadow → Champion</span>", `<div class="xs muted" style="margin-bottom:6px">${(l.pipeline || []).map(esc).join(" → ")}</div>${models ? `<div class="scroll"><table class="tight"><tbody>${models}</tbody></table></div>` : empty("등록된 모델 없음")}
      ${(l.research || []).length ? `<div class="small muted" style="margin-top:8px">Research</div>${l.research.map((r) => `<div class="xs"><span class="chip xs">${esc(r.kind)}</span> ${esc(r.name)} — ${esc(r.detail)}</div>`).join("")}` : ""}`, '<a class="link" href="#lab">실험 · 승격</a>')}
    ${card(`틀린 예측 자동 연구 <span class="small dim">${f.n ? `${num(f.n)}건 · ${f.days}일 · 적중 ${R(f.hit, 0)}` : ""}</span>`, f.n ? `
      ${(f.findings || []).map((x) => `<div class="lesson small" style="margin-bottom:6px"><b>${esc(x.text)}</b><div class="xs muted">→ ${esc(x.action)}</div></div>`).join("") || '<div class="xs dim">유의한 약점 패턴 없음</div>'}
      <div class="scroll"><table class="tight"><thead><tr><th>원인 후보</th><th class="r">n</th><th class="r">비중</th><th class="r">있을 때</th><th class="r">없을 때</th><th class="r">z</th></tr></thead><tbody>${tags}</tbody></table></div>
      <div class="xs dim" style="margin-top:6px">${esc(f.note || "")}</div>` : empty(f.message || "채점된 예측 없음"),
    '<button class="btn-sm" id="fl-run">다시 분석</button>')}
  </div>
  ${(f.losses || []).length ? card("크게 틀린 예측 <span class='small dim'>원인 후보 태그</span>", `<div class="scroll"><table class="tight"><thead><tr><th>날짜</th><th>종목</th><th>예측</th><th class="r">확률</th><th class="r">실제</th><th>원인 후보</th></tr></thead><tbody>${f.losses.map((x) => `<tr><td class="xs">${esc(x.at)}</td><td><a href="#analysis/${esc(x.symbol)}">${esc(x.name)}</a></td><td>${esc(x.predicted)}</td><td class="r num">${R(x.prob, 0)}</td><td class="r ${x.actual >= 0 ? "up" : "down"}">${P(x.actual, 1)}</td><td class="xs">${(x.why || []).map(esc).join(" · ") || '<span class="dim">설명 안 됨</span>'}</td></tr>`).join("")}</tbody></table></div>`) : ""}`;
  $("#fl-run").onclick = async (e) => { e.target.disabled = true; e.target.textContent = "분석 중…"; await api("/api/failure-lab?refresh=1"); render(); };
}

// ------------------------------------------------------------ 데이터 건강 (점수 · Sentinel · Fail-Closed)
async function viewDataHealth(el) {
  const [h, sn, g] = await Promise.all([api("/api/data-health"), api("/api/sentinel"), api("/api/governance")]);
  const rows = (h.rows || []).map((r) => `<tr><td class="small b">${OS_ST[r.status] || "⚪"} ${esc(r.name)}</td><td style="min-width:120px">${r.pct == null ? '<span class="xs dim">해당 없음</span>' : `${bar100(r.pct / 100)} <span class="xs num">${r.pct}%</span>`}</td><td class="xs" style="white-space:normal">${esc(r.detail)}</td><td class="xs dim">${esc(r.source)}</td></tr>`).join("");
  el.innerHTML = `
  <div class="card dh-hero ${h.trading === "BLOCKED" ? "blocked" : ""}"><div class="card-h"><h3>DATA HEALTH <span class="small dim">${esc(h.as_of)}</span></h3><div class="right"><button class="btn-sm" id="dh-re">다시 점검</button></div></div>
    <div class="dh-top"><div class="big num">${h.overall}%</div><div><div class="b ${h.trading === "BLOCKED" ? "bad-t" : "good"}">${h.trading === "BLOCKED" ? "⛔ TRADING BLOCKED" : "✅ 거래 가능"}</div><div class="xs muted">${esc(h.block_reason || h.price_delay?.detail || "")}</div></div></div>
    <div class="scroll" style="margin-top:10px"><table class="tight"><thead><tr><th>분야</th><th>점수</th><th>상태</th><th>출처</th></tr></thead><tbody>${rows}</tbody></table></div>
    <div class="xs dim" style="margin-top:6px">데이터가 믿을 수 없으면 사지 않는다: 주가 점수 50% 미만 또는 장중 실시간 가격 15분 이상 지연 → 신규 매수 차단 (매도·위험 축소는 가능)</div></div>
  <div class="grid g-2">
    ${card(`자동 감시 (Sentinel) <span class="small ${LV_CLS[sn.status]}">${esc({ ok: "정상", warn: "주의", bad: "문제" }[sn.status] || sn.status)}</span>`, `<div class="tr-rows">${(sn.checks || []).map(stRow).join("")}</div><div class="xs dim" style="margin-top:6px">5분마다 · 나빠지면 알림, 회복하면 알림 · ${esc(sn.as_of)}</div>`)}
    ${card("장애가 나도 돈은 안전 (Fail-Closed)", `<table class="tight"><thead><tr><th>장애</th><th>기존 포지션</th><th>신규 매수</th><th>매도</th></tr></thead><tbody>${(g.failmode || []).map((m) => `<tr><td class="small b">${esc(m.part)}</td><td class="small">${esc(m.positions)}</td><td class="small ${/차단|제한/.test(m.new_buys) ? "bad-t" : ""}">${esc(m.new_buys)}</td><td class="small">${esc(m.sells)}</td></tr>`).join("")}</tbody></table>`)}
  </div>`;
  $("#dh-re").onclick = async (e) => { e.target.disabled = true; await api("/api/data-health?refresh=1"); render(); };
  keysCard(el);
}

// v17: API 키 진단 (DART · FRED · ECOS) — 값은 끝 4자리만
async function keysCard(el) {
  const box = document.createElement("div");
  el.appendChild(box);
  let k;
  try { k = await api("/api/keys"); } catch { box.remove(); return; }
  const ST = { ok: "🟢", warn: "🟡", bad: "🔴", missing: "⚪" };
  box.innerHTML = card(`API 키 진단 <span class="small dim">${esc(k.env_file || ".env 를 찾지 못함")}</span>`, `
    ${(k.issues || []).map((x) => `<div class="xs warn-t">⚠ ${esc(x)}</div>`).join("")}
    <div class="tr-rows">${k.keys.map((x) => `<div class="tr-row"><span class="tr-ic">${ST[x.status]}</span><div><div class="small b">${esc(x.title)} <span class="xs mono dim">${esc(x.key)}${x.masked ? " " + esc(x.masked) : ""}</span></div>
      <div class="xs muted">${x.loaded ? "서버에 반영됨" : "서버에 없음"}${x.lines.length ? ` · .env ${x.lines.join(", ")}번째 줄` : ""}${x.length ? ` · 길이 ${x.length}` : ""}</div>
      ${(x.tips || []).map((t) => `<div class="xs">· ${esc(t)}</div>`).join("")}</div></div>`).join("")}</div>
    <div style="margin-top:8px;display:flex;gap:6px"><button class="btn-sm" id="kc-reload">키 다시 읽기</button><button class="btn-sm" id="kc-probe">연결 시험</button></div>
    <div class="xs" id="kc-msg" style="margin-top:6px"></div><div class="xs dim" style="margin-top:4px">${esc(k.note)} · 터미널: <code>./run.sh keys --probe</code></div>`);
  const m = $("#kc-msg");
  $("#kc-reload").onclick = async () => { const r = await post("/api/keys/reload", {}); m.textContent = r.changed?.length ? `반영됨: ${r.changed.join(", ")}` : "바뀐 키 없음"; if (r.changed?.length) render(); };
  $("#kc-probe").onclick = async () => {
    m.textContent = "시험 중…";
    const r = await post("/api/keys/probe", {});
    m.innerHTML = r.error ? esc(r.error) : Object.entries(r.results).map(([n, v]) => `${v.ok ? "🟢" : v.status === "missing" ? "⚪" : "🔴"} ${n.toUpperCase()}: ${esc(v.message)}${v.ms ? ` (${v.ms}ms)` : ""}`).join("<br>");
  };
}

// ------------------------------------------------------------ Portfolio OS (위험 · 시뮬레이션)
async function viewPortfolioOS(el) {
  const [o, rs] = await Promise.all([api(`/api/portfolio-os?${pfModeQ()}`), api(`/api/risk-simple?${pfModeQ()}`).catch(() => ({}))]);
  if (o.empty) { el.innerHTML = card("Portfolio OS", `<div class="lesson">${esc(o.headline)}</div>`, '<a class="link" href="#accounts">계좌 입력</a>'); return; }
  const lvCls = { LOW: "good", MEDIUM: "warn-t", HIGH: "bad-t" }[o.risk_level] || "";
  const hold = (o.holdings || []).map((h) => `<tr><td><a href="#analysis/${esc(h.symbol)}"><b>${esc(h.name)}</b></a></td><td class="small">${esc(h.sector)}</td><td class="r num">₩${num(h.value)}</td><td style="min-width:120px">${hbar(h.weight, Math.max(0.3, o.top?.weight || 0.3))} <span class="xs num">${R(h.weight, 1)}</span></td></tr>`).join("");
  el.innerHTML = `
  <div class="card pos-hero"><div class="card-h"><h3>Portfolio OS <span class="small dim">${esc(o.source)} · ${esc(o.as_of)}</span></h3><div class="right"><a class="btn-sm" href="#accounts">계좌</a></div></div>
    <div class="kv-grid">${kv("내 자산", "₩" + num(o.total))}${kv("주식", R(o.stock_pct, 0))}${kv("현금", R(o.cash_pct, 0))}${kv("종목 수", o.n)}${kv("업종 집중", esc(o.sector_level))}${kv("최대 비중", o.top ? `${esc(o.top.name)} ${R(o.top.weight, 0)}` : "-")}${kv("Risk", `<span class="${lvCls}">${esc(o.risk_level)}</span>`)}</div>
    <div class="biggest"><div class="xs muted">지금 포트폴리오에서 가장 큰 위험은?</div><div class="b">${esc(o.biggest_risk)}</div>${(o.other_risks || []).length ? `<div class="xs muted">${o.other_risks.map(esc).join(" · ")}</div>` : ""}</div></div>
  <div class="grid g-2">
    ${card("보유 비중", hold ? `<table class="tight"><tbody>${hold}</tbody></table>` : empty(), "", "")}
    ${card(`쉬운 위험 <span class="small dim">시스템 ${esc(rs.mode || "paper")} 장부 기준</span> <span class="small ${LV_CLS[rs.level] || ""}">${esc(rs.headline || "")}</span>`, (rs.cards || []).map((c) => `<div class="tr-row"><span class="tr-ic">${OS_ST[c.level] || "⚪"}</span><div><div class="small b">${esc(c.title)} <span class="num">${esc(c.value)}</span></div><div class="xs muted">${esc(c.plain)}</div></div></div>`).join("") || empty("시스템 장부 기준 위험 카드 없음"))}
  </div>
  <div id="pos-sim">${card("실제 돈을 넣기 전에 — 시뮬레이션", '<div class="xs dim">계산 중…</div>')}</div>`;
  const sim = await api(`/api/simulate?${pfModeQ()}`).catch((e) => ({ error: e.message }));
  const box = $("#pos-sim");
  if (!box) return;
  if (sim.error || !(sim.strategies || []).length) { box.innerHTML = card("시뮬레이션", empty(sim.error || "비교할 전략 없음")); return; }
  const names = [...new Set(sim.strategies.flatMap((s) => (s.stress || []).map((x) => x.name)))];
  box.innerHTML = card("실제 돈을 넣기 전에 — 전략 · 위기 시뮬레이션", `<div class="scroll"><table class="tight"><thead><tr><th>전략</th><th class="r">종목</th><th class="r">투자 비율</th><th class="r">1년 수익</th><th class="r">변동성</th><th class="r">1일 VaR</th><th class="r">1년 MDD</th>${names.map((n) => `<th class="r">${esc(n)}</th>`).join("")}</tr></thead><tbody>
    ${sim.strategies.map((s) => `<tr><td class="small b">${esc(s.name)}</td><td class="r num">${s.n}</td><td class="r num">${R(s.invested, 0)}</td><td class="r ${s.ret_1y >= 0 ? "up" : "down"}">${P(s.ret_1y, 1)}</td><td class="r num">${R(s.vol, 0)}</td><td class="r num">${R(s.var95, 1)}</td><td class="r down">${P(s.mdd_1y, 1)}</td>${names.map((n) => { const x = (s.stress || []).find((y) => y.name === n); return `<td class="r down" title="${esc(x?.basis || "")}">${x ? "−" + R(x.loss, 1) : "-"}</td>`; }).join("")}</tr>`).join("")}</tbody></table></div>
    <div class="xs dim" style="margin-top:6px">${esc(sim.note)}</div>`);
}

// ------------------------------------------------------------ 내 투자 성향 (개인화 · 조건 목록 · 실수 패턴)
async function viewProfile(el) {
  const [p, d, m] = await Promise.all([api("/api/user-profile"), api("/api/discover"), api("/api/mistakes")]);
  const pr = p.profile || {};
  const chk = (name, val, label, on) => `<label class="chk-chip"><input type="checkbox" name="${name}" value="${esc(val)}" ${on ? "checked" : ""}> ${esc(label)}</label>`;
  el.innerHTML = `
  <div class="grid g-2">
    ${card("내 투자 성향 <span class='small dim'>시스템이 여기에 맞춰 보여줌 · 서버 저장</span>", `
      <div class="small muted">성향</div><div class="tabs" id="pf-style">${Object.entries(p.styles || {}).map(([k, v]) => `<button data-k="${k}" class="${(pr.style || "balanced") === k ? "on" : ""}">${esc(v)}</button>`).join("")}</div>
      <div class="small muted" style="margin-top:8px">관심 시장</div><div>${chk("mkt", "KR", "국내", (pr.markets || ["KR", "US"]).includes("KR"))}${chk("mkt", "US", "미국", (pr.markets || ["KR", "US"]).includes("US"))}</div>
      <label class="small muted" style="display:block;margin-top:8px">관심 업종 (쉼표로)<input id="pf-sec" value="${esc((pr.sectors || []).join(", "))}" placeholder="반도체, 2차전지"></label>
      <label class="small muted" style="display:block;margin-top:8px">목표<input id="pf-goal" value="${esc(pr.goal || "")}" placeholder="예: 5년 뒤 배당 월 50만원"></label>
      <label class="small muted" style="display:block;margin-top:8px">1일 최대 손실 한도 (%)<input id="pf-mdl" type="number" step="0.1" value="${pr.max_daily_loss ?? ""}" placeholder="예: 2"></label>
      <div style="margin-top:10px"><button class="btn-sm primary" id="pf-save">저장</button> <span class="xs" id="pf-msg"></span></div>`)}
    ${card(`성향에 맞는 종목 <span class="small dim">${esc(d.style || "")}</span>`, (d.rows || []).length ? `<table class="tight"><tbody>${d.rows.map((r) => `<tr><td><a href="#analysis/${esc(r.symbol)}"><b>${esc(r.name)}</b></a> <span class="xs dim">${esc(r.sector)}</span></td><td class="small">${esc(r.why)}</td><td>${badge(r.ai)} <span class="xs num">${R(r.prob_up, 0)}</span></td></tr>`).join("")}</tbody></table>` : empty("조건에 맞는 종목이 아직 없습니다 (종목 상세 수집 후 채워짐)"),
    "", "")}
  </div>
  ${card("가장 자주 하는 실수 <span class='small dim'>내 저널 · 결과 확정된 기록</span>", (m.items || []).length ? `<table class="tight"><thead><tr><th>패턴</th><th class="r">횟수</th><th class="r">적중</th><th class="r">평균 결과</th><th>조언</th></tr></thead><tbody>${m.items.map((x) => `<tr class="${x.worse ? "bad-row" : ""}"><td class="small b">${esc(x.title)}</td><td class="r num">${x.n}</td><td class="r num">${R(x.hit, 0)}</td><td class="r num">${P(x.avg_ret, 1)}</td><td class="xs">${esc(x.advice || "")}</td></tr>`).join("")}</tbody></table><div class="xs dim">전체 적중 ${R(m.base_hit, 0)} 보다 5%p 이상 낮으면 빨간색</div>` : empty(m.message || "기록 부족"),
  '<a class="link" href="#myjournal">투자일지</a>')}
  <div class="xs dim">${esc(d.note || "")} — 투자 권유가 아닙니다.</div>`;
  let style = pr.style || "balanced";
  el.querySelectorAll("#pf-style button").forEach((b) => b.onclick = () => { style = b.dataset.k; el.querySelectorAll("#pf-style button").forEach((x) => x.classList.toggle("on", x === b)); });
  $("#pf-save").onclick = async () => {
    const markets = [...el.querySelectorAll('input[name="mkt"]:checked')].map((x) => x.value);
    const r = await post("/api/user-profile", { style, markets, sectors: $("#pf-sec").value.split(",").map((x) => x.trim()).filter(Boolean), goal: $("#pf-goal").value, max_daily_loss: $("#pf-mdl").value });
    if (r.error) { $("#pf-msg").innerHTML = `<span class="bad-t">${esc(r.error)}</span>`; return; }
    toast({ title: "성향 저장", level: "good" }); render();
  };
}

// ------------------------------------------------------------ 실전 검증 진행표 (+ 비용 실측)
async function viewValidation(el) {
  const [v, c] = await Promise.all([api("/api/validation"), api("/api/exec-costs")]);
  const it = (v.items || []).map((x) => `<div class="val-it"><div class="val-h"><b>${OS_ST[x.status] || (x.status === "setup" ? "⚙️" : "⚪")} ${esc(x.title)}</b><span class="num">${R(x.progress, 0)}</span></div>${bar100(x.progress)}
    <div class="xs muted">${esc(x.detail)}</div><div class="xs">다음: <code>${esc(x.next)}</code></div></div>`).join("");
  const rows = (c.rows || []).map((r) => `<tr><td><b>${esc(r.mode)}</b>${r.measured ? ' <span class="chip xs pos">실측</span>' : ' <span class="chip xs">모델</span>'}</td><td class="r num">${r.orders}</td><td class="r num">${R(r.fill_rate, 0)}</td><td class="r num">${r.slippage_bps ?? "-"}bp</td><td class="r num">${r.slippage_p90_bps ?? "-"}bp</td><td class="r num">${r.commission_bps ?? "-"}bp</td><td class="r num">${r.roundtrip_bps}bp</td></tr>`).join("");
  el.innerHTML = `
  <div class="card"><div class="card-h"><h3>실전 검증 진행표 <span class="small dim">${esc(v.as_of)}</span></h3><div class="right"><span class="big num">${R(v.overall, 0)}</span></div></div>
    <div class="lesson small">${esc(v.headline)}</div><div class="val-grid" style="margin-top:10px">${it}</div></div>
  ${card("거래 비용 — 예상 vs 실제", `<div class="scroll"><table class="tight"><thead><tr><th>장부</th><th class="r">주문</th><th class="r">체결률</th><th class="r">슬리피지</th><th class="r">p90</th><th class="r">수수료</th><th class="r">왕복</th></tr></thead><tbody>${rows || '<tr><td colspan="7">' + empty("주문 기록 없음") + "</td></tr>"}</tbody></table></div>
    <div class="kv-grid" style="margin-top:8px">${kv("가정 슬리피지", c.assumed.slippage_bps + "bp")}${kv("가정 수수료", c.assumed.commission_bps + "bp")}${kv("매도세", c.assumed.sell_tax_bps + "bp")}${kv("가정 왕복", c.assumed.roundtrip_bps + "bp")}${kv("보정", c.calibrated?.applied ? "실측 적용" : "미적용")}</div>
    <div class="lesson small" style="margin-top:8px">${esc(c.verdict)}</div><div class="xs dim">${esc(c.note)}</div>`, '<a class="link" href="#execution">체결 · 증권사 검증</a>')}`;
}

// ------------------------------------------------------------ 규제 단계 · 보안 · 데이터 라이선스 · 감사 로그
async function viewGovernance(el) {
  const g = await api("/api/governance");
  const sv = g.service, lic = g.licenses, sec = g.security;
  const lv = sv.levels.map((l, i) => `<div class="gov-lv"><div class="gov-n">${i + 1}</div><div><b>${esc(l.name)}</b> <span class="xs dim">${esc(l.key.toUpperCase())}</span><div class="xs muted">${esc(l.desc)}</div><div class="xs">⚖ ${esc(l.legal)}</div>${l.features.length ? `<div class="xs dim">이 시스템: ${l.features.map(esc).join(" · ")}</div>` : ""}</div></div>`).join("");
  const RK = { high: "bad-t", medium: "warn-t", low: "good" };
  el.innerHTML = `
  ${card(`서비스 단계 · 규제 <span class="small dim">지금: ${esc(sv.current_label)}</span>`, `<div class="gov-flow">${lv}</div><div class="lesson small" style="margin-top:8px">${esc(sv.note)}</div>`)}
  ${card(`데이터 라이선스 <span class="small dim">${esc(lic.usage)}</span>`, `${lic.warning ? `<div class="veto">${esc(lic.warning)}</div>` : ""}
    <div class="scroll"><table class="tight"><thead><tr><th>출처</th><th>쓰는 곳</th><th>개인 사용</th><th>상용 서비스</th><th>위험</th></tr></thead><tbody>${lic.rows.map((r) => `<tr><td class="small b">${esc(r.source)}</td><td class="xs">${esc(r.used)}</td><td class="xs">${esc(r.personal)}</td><td class="xs" style="white-space:normal">${esc(r.commercial)}</td><td class="${RK[r.risk]}">${esc(r.risk.toUpperCase())}</td></tr>`).join("")}</tbody></table></div>
    <div class="xs muted" style="margin-top:6px">상용화 경로: ${lic.path.map(esc).join(" → ")}</div><div class="xs dim">${esc(lic.note)}</div>`)}
  <div class="grid g-2">
    ${card(`보안 점검 <span class="small dim">${sec.score}/100</span>`, `<div class="tr-rows">${sec.items.map((x) => `<div class="tr-row"><span class="tr-ic">${{ ok: "🟢", partial: "🟡", missing: "🔴" }[x.status]}</span><div><div class="small b">${esc(x.item)}</div><div class="xs muted">${esc(x.detail)}</div></div></div>`).join("")}</div><div class="xs dim" style="margin-top:6px">${esc(sec.note)}</div>`)}
    ${card("감사 로그 <span class='small dim'>민감한 조작 · 추가만 가능</span>", (g.audit || []).length ? `<div class="scroll" style="max-height:420px"><table class="tight"><tbody>${g.audit.map((a) => `<tr><td class="xs mono">${esc(a.as_of)}</td><td><span class="chip xs">${esc(a.action)}</span></td><td class="xs" style="white-space:normal">${esc(a.detail)}</td><td class="xs dim">${esc(a.actor)}</td></tr>`).join("")}</tbody></table></div>` : empty("기록 없음"))}
  </div>`;
}

// ------------------------------------------------------------ 수동 모의 장부
async function viewManual(el) {
  const b = await api("/api/ticket/book");
  const rows = (b.positions || []).map((p) => `<tr><td><a href="#analysis/${esc(p.symbol)}"><b>${esc(p.symbol)}</b></a></td><td class="r num">${num(p.qty)}</td><td class="r num">${num(p.avg_price, 0)}</td><td class="r num">${num(p.price, 0)}</td><td class="r ${p.pnl_pct >= 0 ? "up" : "down"}">${P(p.pnl_pct)}</td></tr>`).join("");
  el.innerHTML = card("수동 모의 장부 <span class='small dim'>종목 화면의 [모의 주문]으로 기록 · 실제 돈 아님 · 전략 장부와 분리</span>", `
    <div class="kv-grid">${kv("평가", "₩" + num(b.equity))}${kv("현금", "₩" + num(b.cash))}${kv("수익률", P(b.return))}</div>
    ${rows ? `<div class="scroll" style="margin-top:8px"><table class="tight"><thead><tr><th>종목</th><th class="r">수량</th><th class="r">평단</th><th class="r">현재가</th><th class="r">손익</th></tr></thead><tbody>${rows}</tbody></table></div>` : empty("아직 모의 주문이 없습니다 — 종목 검색(/) → 종목 → [모의 주문]")}`,
  '<a class="link" href="#orders">주문 내역</a>');
}
