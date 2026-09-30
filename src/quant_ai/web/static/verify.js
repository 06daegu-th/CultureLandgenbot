/* 검증실 · 지식 그래프/섹터 · 리포트 · 알림 규칙 · 앱 설치/푸시 · 모바일 탭바 */
"use strict";

const P = (v, d = 1) => v == null ? "-" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(d)}%`;
const R = (v, d = 1) => v == null ? "-" : `${(v * 100).toFixed(d)}%`;
const vtabs = (id, obj, cur) => `<div class="tabs" id="${id}">${Object.entries(obj).map(([k, v]) => `<button data-k="${k}" class="${cur === k ? "on" : ""}">${v}</button>`).join("")}</div>`;
const STATUS_CHIP = { pass: ["통과", "pos"], fail: ["미달", "neg"], wait: ["보류", ""], partial: ["일부", ""], insufficient: ["표본 부족", ""],
  stable: ["안정", "pos"], warn: ["주의", ""], drift: ["변화 큼", "neg"], unknown: ["-", ""] };
const chipOf = (st) => { const [l, c] = STATUS_CHIP[st] || [st, ""]; return `<span class="chip ${c}">${esc(l)}</span>`; };
function hbar(v, max = 1, cls = "") {
  const w = v == null ? 0 : Math.max(0, Math.min(100, Math.abs(v) / (max || 1) * 100));
  return `<div class="hbar ${cls}"><i style="width:${w}%"></i></div>`;
}

// ================================================================= 검증실
async function viewVerify(el) {
  const v = await api("/api/verify");
  const e = v.evaluation || {};
  const led = v.ledger || {};
  const leak = e.leakage || {};
  const dr = v.drift || {};
  const base = e.baselines || {};
  const hitCi = e.hit_ci95;
  const verdictCls = e.status === "pass" ? "good" : e.status === "fail" || e.status === "worse" ? "bad-t" : "warn-t";
  const tbl = (rows, cols) => rows && rows.length ? `<div class="scroll"><table class="tight"><thead><tr>${cols.map((c) => `<th class="${c[2] || ""}">${c[0]}</th>`).join("")}</tr></thead><tbody>${rows.map((r) => `<tr>${cols.map((c) => `<td class="${c[2] || ""}">${c[1](r)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>` : empty("표본 부족");
  const grp = (rows) => tbl(rows, [["구분", (r) => esc(r.key)], ["n", (r) => r.n, "r num"], ["적중", (r) => `${R(r.hit_rate)} ${hbar(r.hit_rate)}`, "r"], ["평균 실제", (r) => P(r.avg_actual, 2), "r num"]]);
  const errs = e.errors || {};
  const b = e.brier || {};
  el.innerHTML = `
  <div class="card vf-hero">
    <div class="vf-top"><div><div class="xs muted">독립 평가기 — 예측을 만든 코드를 믿지 않고 가격에서 다시 채점</div>
      <h2 class="${verdictCls}">${esc(e.verdict || "아직 평가 기록이 없습니다")}</h2>
      <div class="small muted">${e.evaluated_at ? `평가 ${time(e.evaluated_at, true)} · ` : ""}채점된 예측 ${e.n ?? 0}건 · 가격 재계산 ${e.recomputed ?? 0}건 · 저장값 불일치 ${(e.mismatches || []).length}건${e.sealed_share != null ? ` · 해시 봉인 ${R(e.sealed_share)}` : ""}</div></div>
      <div class="vf-act"><button class="btn-sm primary" id="vf-eval">지금 평가</button><button class="btn-sm" id="vf-drift">드리프트 점검</button><button class="btn-sm" id="vf-retrain">재학습 후보 만들기</button></div></div>
    <div class="vf-kpi">
      <div><div class="xs muted">방향 적중률</div><div class="big num" ${e.hit_rate != null ? `data-count="${(e.hit_rate * 100).toFixed(1)}" data-dec="1" data-suf="%"` : ""}>${R(e.hit_rate)}</div><div class="xs dim">95% 구간 ${hitCi ? `${R(hitCi[0])} ~ ${R(hitCi[1])}` : "-"}</div></div>
      <div><div class="xs muted">기준선 대비</div><div class="big num ${(e.edge_vs_baseline ?? 0) > 0 ? "good" : "bad-t"}">${e.edge_vs_baseline == null ? "-" : `${(e.edge_vs_baseline * 100).toFixed(1)}%p`}</div><div class="xs dim">가장 강한 기준선 ${R(e.best_baseline)}</div></div>
      <div><div class="xs muted">우연일 확률 (p값)</div><div class="big num ${e.p_value != null && e.p_value < 0.05 ? "good" : ""}">${e.p_value == null ? "-" : e.p_value < 0.0001 ? "<0.0001" : e.p_value.toFixed(4)}</div><div class="xs dim">0.05 미만이면 유의</div></div>
      <div><div class="xs muted">비용 후 신호 수익</div><div class="big num ${(e.net_signal?.mean ?? 0) > 0 ? "up" : "down"}">${P(e.net_signal?.mean, 2)}</div><div class="xs dim">95% ${e.net_signal?.ci95 ? `${P(e.net_signal.ci95[0], 2)} ~ ${P(e.net_signal.ci95[1], 2)}` : "-"}</div></div>
      <div><div class="xs muted">시장 대비 초과</div><div class="big num ${(e.excess?.mean ?? 0) > 0 ? "up" : "down"}">${P(e.excess?.mean, 2)}</div><div class="xs dim">${e.excess?.n ?? 0}건 · 95% ${e.excess?.ci95 ? `${P(e.excess.ci95[0], 2)} ~ ${P(e.excess.ci95[1], 2)}` : "-"}</div></div>
    </div>
    <div class="vf-base small">${Object.entries({ coin: "동전 던지기", always_up: "많이 나온 쪽 찍기", momentum: "20일 모멘텀" }).map(([k, l]) => `<div><span class="muted">${l}</span>${hbar(base[k], 1, "dim")}<b class="num">${R(base[k])}</b></div>`).join("")}
      <div><span class="muted"><b>AI 합의</b></span>${hbar(e.hit_rate, 1, "acc")}<b class="num">${R(e.hit_rate)}</b></div></div>
  </div>
  <div class="grid g-2">
    ${card(`예측 장부 무결성 <span class='small dim'>해시 봉인 · 결과를 보고 고치지 않았다는 증거</span>`, `
      <div class="led-st ${led.ok ? "ok" : "bad"}">${led.ok ? "🔒" : "⚠️"} <b>${esc(led.message || "-")}</b></div>
      <div class="kv-grid" style="margin-top:10px"><div class="kvt"><div class="xs muted">봉인된 예측</div><div class="num b">${num(led.sealed)}</div></div>
        <div class="kvt"><div class="xs muted">봉인 사슬</div><div class="num b">${num(led.anchors)}</div></div>
        <div class="kvt"><div class="xs muted">봉인 대기 · 이전 기록</div><div class="num b">${num(led.pending)} · ${num(led.legacy)}</div></div></div>
      ${led.last_digest ? `<div class="xs muted" style="margin-top:10px">마지막 봉인 해시 (예측 #${led.last_upto_id} 까지 · ${time(led.last_anchor_at, true)})</div>
        <div class="digest mono" id="vf-digest" title="눌러서 복사">${esc(led.last_digest)}</div><div class="xs dim">일일 리포트와 함께 텔레그램으로도 보냅니다 — 이 컴퓨터 밖에 남은 해시가 "나중에 고치지 않았다" 의 증거입니다.</div>` : `<div class="xs dim" style="margin-top:10px">첫 봉인은 예측이 저장되고 한 시간 안에 만들어집니다.</div>`}
      ${(led.tampered || []).length ? `<div class="veto" style="margin-top:8px">해시 불일치 예측: #${led.tampered.join(", #")}</div>` : ""}
      ${(led.breaks || []).length ? `<div class="veto" style="margin-top:8px">${led.breaks.map((x) => `봉인 #${x.anchor}: ${esc(x.why)}`).join("<br>")}</div>` : ""}`,
      `<a class="link" href="#ledger">장부 보기 ${ICONS.arrow}</a>`)}
    ${card(`미래 정보 누수 감사 ${leak.status ? chipOf(leak.status) : ""}`, (leak.checks || []).length ? `<div class="conds">${leak.checks.map((c) => `<div class="cond st-${c.status}"><span class="cond-ic">${c.status === "pass" ? "✔" : c.status === "fail" ? "✘" : "…"}</span><div style="min-width:0"><b>${esc(c.key)} ${esc(c.title)}</b><div class="xs muted">${esc(c.detail)}</div></div></div>`).join("")}</div>` : empty("야간 평가 때 함께 검사합니다"))}
  </div>
  <div class="grid g-2">
    ${card(`확률 품질 (Brier 분해) <span class='small dim'>신뢰도↓ 분해능↑ 일수록 좋다</span>`, b.brier != null ? `<div class="bd-rows">
        <div><span>Brier</span><b class="num">${b.brier.toFixed(4)}</b></div>
        <div><span>신뢰도 (정직함 · 작을수록)</span>${hbar(b.reliability, Math.max(b.uncertainty || 0.25, 0.01), "neg")}<b class="num">${b.reliability}</b></div>
        <div><span>분해능 (구별력 · 클수록)</span>${hbar(b.resolution, Math.max(b.uncertainty || 0.25, 0.01), "pos")}<b class="num">${b.resolution}</b></div>
        <div><span>불확실성 (시장 자체의 어려움)</span>${hbar(b.uncertainty, 0.25, "dim")}<b class="num">${b.uncertainty}</b></div>
        <div><span>기술 점수 (분해능−신뢰도)/불확실성</span><b class="num ${(b.skill ?? 0) > 0 ? "good" : "bad-t"}">${b.skill ?? "-"}</b></div></div>
        <div class="xs dim" style="margin-top:8px">크기 예측력 (기대수익↔실제 상관) ${e.size_corr == null ? "-" : e.size_corr.toFixed(3)}</div>` : empty("표본 부족"))}
    ${card("어느 AI 가 틀렸나 <span class='small dim'>틀린 예측의 책임 몫 · 합의가 틀릴 때 맞힌 횟수</span>", tbl(errs.by_ai, [
      ["AI", (r) => `<b>${esc((S.data?.analyst_labels || {})[r.analyst] || r.analyst)}</b>`], ["n", (r) => r.n, "r num"],
      ["적중", (r) => R(r.hit_rate), "r num"], ["Brier", (r) => r.brier.toFixed(3), "r num"],
      ["책임 몫", (r) => `${hbar(r.blame_share, 1, "neg")}<span class="num">${R(r.blame_share, 0)}</span>`, "r"], ["구해냄", (r) => r.saves, "r num"]]))}
  </div>
  <div class="grid g-3">
    ${card("요인별 적중 <span class='small dim'>요인이 강하게 가리켰을 때</span>", tbl(errs.by_factor, [["요인", (r) => esc(r.factor)], ["n", (r) => r.n, "r num"], ["방향 맞음", (r) => `${hbar(r.hit_rate)}${R(r.hit_rate)}`, "r"]]))}
    ${card("확신도별", grp(errs.by_confidence))}
    ${card("틀린 이유 분포", (errs.miss_reasons || []).length ? `<div class="bd-rows">${errs.miss_reasons.map((m) => `<div><span>${esc(m.reason)}</span>${hbar(m.n, Math.max(...errs.miss_reasons.map((x) => x.n)), "neg")}<b class="num">${m.n}</b></div>`).join("")}</div>` : empty("틀린 예측 없음"))}
  </div>
  <div class="grid g-3">
    ${card("국면별", grp(errs.by_regime))}
    ${card("의견 충돌별", grp(errs.by_conflict))}
    ${card("이벤트 재분석 vs 정기 · 시장별", grp([...(errs.by_trigger || []), ...(errs.by_market || [])]))}
  </div>
  <div class="grid g-2">
    ${card(`데이터 드리프트 ${chipOf(dr.status || "unknown")} <span class='small dim'>PSI · 0.1 주의 · 0.25 큰 변화</span>`, (dr.features || []).length ? `<div class="bd-rows">${dr.features.map((f) => `<div><span>${esc(f.label)}</span>${hbar(f.psi, 0.5, f.status === "drift" ? "neg" : f.status === "warn" ? "warn" : "pos")}<b class="num">${f.psi ?? "-"}</b></div>`).join("")}</div>
      <div class="small muted" style="margin-top:8px">${esc(dr.message || "")}${dr.prediction?.psi != null ? ` · AI 확률 분포 PSI ${dr.prediction.psi}` : ""}${dr.prediction?.up_rate_cur != null ? ` · 최근 상승 비율 ${R(dr.prediction.up_rate_cur)} (이전 ${R(dr.prediction.up_rate_ref)})` : ""}</div>` : empty("야간에 점검합니다 — '드리프트 점검' 으로 지금 확인"))}
    ${card("모델 계보 · 재학습 후보 <span class='small dim'>왜 · 무엇에서 · 어떤 데이터로</span>", tbl(v.models, [
      ["모델", (r) => `<b>${esc(r.name)}</b><div class="xs dim mono">${esc(r.version)}</div>`], ["상태", (r) => `<span class="chip">${esc(r.status)}</span>`],
      ["이유", (r) => `<span class="small">${esc(r.reason || "-")}</span>`], ["부모", (r) => `<span class="xs mono">${esc((r.parent || "-").split("@").pop().slice(0, 14))}</span>`],
      ["Sharpe", (r) => r.sharpe ?? "-", "r num"], ["DSR", (r) => r.dsr ?? "-", "r num"]]))}
  </div>
  ${card("프롬프트 버전 <span class='small dim'>문구가 바뀌면 버전이 바뀌고, 예측 장부에 함께 봉인됩니다</span>", `<div class="chips">${(v.prompts || []).map((p) => `<span class="chip">${esc(p.title)} <b class="mono">${esc(p.version)}</b></span>`).join(" ")}</div>
    ${(errs.by_prompt || []).length ? `<div style="margin-top:10px">${tbl(errs.by_prompt, [["역할", (r) => esc(r.role)], ["버전", (r) => `<span class="mono">${esc(r.prompt)}</span>`], ["n", (r) => r.n, "r num"], ["적중", (r) => R(r.hit_rate), "r num"]])}</div>` : ""}`)}`;
  const act = async (btn, name, label) => {
    btn.disabled = true; const t = btn.textContent; btn.textContent = "실행 중…";
    const r = await runAction(name, (p) => { btn.textContent = p || "실행 중…"; });
    btn.disabled = false; btn.textContent = t;
    toast({ title: r.error ? `${label} 실패` : `${label} 완료`, body: r.error || JSON.stringify(r.result || {}).slice(0, 140), level: r.error ? "warn" : "good" });
    render();
  };
  $("#vf-eval").onclick = (ev) => act(ev.target, "evaluation", "독립 평가");
  $("#vf-drift").onclick = (ev) => act(ev.target, "drift", "드리프트 점검");
  $("#vf-retrain").onclick = (ev) => act(ev.target, "retrain", "재학습 후보");
  const dg = $("#vf-digest");
  if (dg) dg.onclick = () => { navigator.clipboard?.writeText(dg.textContent).then(() => toast({ title: "해시를 복사했습니다", level: "good" })).catch(() => {}); };
}

async function viewLedger(el) {
  const r = await api(`/api/ledger${S.ledgerBefore ? `?before=${S.ledgerBefore}` : ""}`);
  const rows = r.rows.map((x) => `<tr><td class="num dim">#${x.id}</td><td><a href="#analysis/${esc(x.symbol)}"><b>${esc(x.name)}</b></a></td>
    <td class="small">${time(x.as_of, true)}</td><td class="small">${x.created_at ? time(x.created_at, true) : '<span class="dim">이전 기록</span>'}</td>
    <td>${badge(x.action)}</td><td class="r num">${R(x.prob_up, 0)}</td><td class="r num">${P(x.expected)}</td>
    <td class="r num ${x.realized == null ? "" : x.realized >= 0 ? "up" : "down"}">${x.realized == null ? '<span class="dim">대기</span>' : P(x.realized)}</td>
    <td class="small num">${x.outcomes ? ["1", "5", "20"].map((h) => x.outcomes[h] == null ? "·" : P(x.outcomes[h], 1)).join(" / ") : ""}</td>
    <td>${x.correct == null ? "" : x.correct ? "✔" : "✘"}</td>
    <td class="mono xs" title="${esc(x.hash || "")}">${x.hash ? `${x.hash_ok ? "🔒" : "⚠️"} ${esc(x.hash.slice(0, 10))}` : '<span class="dim">-</span>'}</td></tr>`).join("");
  el.innerHTML = card(`예측 장부 <span class="small dim">모든 판단을 저장 순간에 봉인 · 결과는 나중에 붙는다</span>`,
    rows ? `<div class="scroll"><table class="tight"><thead><tr><th>#</th><th>종목</th><th>기준 봉</th><th>저장 시각</th><th>신호</th><th class="r">상승</th><th class="r">예상</th><th class="r">실제</th><th>1·5·20일</th><th></th><th>해시</th></tr></thead><tbody>${rows}</tbody></table></div>
    <div style="margin-top:10px;display:flex;gap:8px"><button class="btn-sm" id="lg-new">최신</button><button class="btn-sm" id="lg-more">이전 60개</button></div>` : empty("아직 예측이 없습니다"),
    `<a class="link" href="#verify">검증실 ${ICONS.arrow}</a>`);
  const more = $("#lg-more");
  if (more) more.onclick = () => { S.ledgerBefore = r.rows[r.rows.length - 1]?.id; render(); };
  const nw = $("#lg-new");
  if (nw) nw.onclick = () => { S.ledgerBefore = null; render(); };
}

// ================================================================= 지식 그래프 · 섹터 · 에이전트
const SECTOR_COLORS = ["#3b82f6", "#f97316", "#22c55e", "#a855f7", "#ef4444", "#14b8a6", "#eab308", "#ec4899", "#6366f1", "#84cc16", "#06b6d4", "#f43f5e"];
function secColor(sec) {
  if (!sec) return "#64748b";
  let h = 0;
  for (const ch of sec) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return SECTOR_COLORS[h % SECTOR_COLORS.length];
}

function layoutForce(nodes, edges, W, H, iters = 220) {
  const ids = Object.keys(nodes);
  const n = ids.length;
  const pos = {};
  ids.forEach((id, i) => { const a = (i / n) * Math.PI * 2; pos[id] = { x: W / 2 + Math.cos(a) * W * 0.35, y: H / 2 + Math.sin(a) * H * 0.35, dx: 0, dy: 0 }; });
  const k = Math.sqrt((W * H) / Math.max(n, 1)) * 0.75;
  let t = W / 8;
  for (let it = 0; it < iters; it++) {
    for (const a of ids) { pos[a].dx = 0; pos[a].dy = 0; }
    for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
      const A = pos[ids[i]], B = pos[ids[j]];
      let dx = A.x - B.x, dy = A.y - B.y; const d = Math.max(Math.hypot(dx, dy), 0.01);
      const f = (k * k) / d; dx /= d; dy /= d;
      A.dx += dx * f; A.dy += dy * f; B.dx -= dx * f; B.dy -= dy * f;
    }
    for (const e of edges) {
      const A = pos[e.a], B = pos[e.b]; if (!A || !B) continue;
      let dx = A.x - B.x, dy = A.y - B.y; const d = Math.max(Math.hypot(dx, dy), 0.01);
      const f = (d * d) / k * Math.min(1.5, 0.4 + (e.weight || 0.5)); dx /= d; dy /= d;
      A.dx -= dx * f; A.dy -= dy * f; B.dx += dx * f; B.dy += dy * f;
    }
    for (const a of ids) {  // 중심 인력: 떨어진 무리가 모서리로 밀려나지 않게
      const p = pos[a]; p.dx += (W / 2 - p.x) * 0.35; p.dy += (H / 2 - p.y) * 0.35;
      const d = Math.max(Math.hypot(p.dx, p.dy), 0.01);
      p.x += (p.dx / d) * Math.min(d, t); p.y += (p.dy / d) * Math.min(d, t);
    }
    t *= 0.97;
  }
  // 이름표가 잘리지 않게 여백 안으로 맞춘다 (확대는 최대 1.4배)
  const mx = 70, my = 28;
  const xs = ids.map((a) => pos[a].x), ys = ids.map((a) => pos[a].y);
  const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
  const sc = Math.min((W - 2 * mx) / Math.max(x1 - x0, 1), (H - 2 * my - 12) / Math.max(y1 - y0, 1), 1.4);
  for (const a of ids) {
    pos[a].x = W / 2 + (pos[a].x - (x0 + x1) / 2) * sc;
    pos[a].y = H / 2 + (pos[a].y - (y0 + y1) / 2) * sc;
  }
  return pos;
}

function graphSvg(g, W, H, center = null) {
  const nodes = g.nodes || {}, edges = g.edges || [];
  if (!Object.keys(nodes).length) return empty(g.message || "그래프가 없습니다");
  let pos;
  if (center) {  // 종목 중심: 가운데 + 이웃 원형 (가까울수록 관계가 강함)
    pos = { [center]: { x: W / 2, y: H / 2 } };
    const nb = (g.neighbors || []).map((x) => x.symbol).filter((s) => nodes[s]);
    const maxW = Math.max(...(g.neighbors || []).map((x) => x.weight || 0), 1);
    nb.forEach((s, i) => { const a = (i / nb.length) * Math.PI * 2 - Math.PI / 2; const w = (g.neighbors[i].weight || 0) / maxW; const r = Math.min(W, H) * (0.42 - 0.16 * w); pos[s] = { x: W / 2 + Math.cos(a) * r, y: H / 2 + Math.sin(a) * r }; });
  } else pos = layoutForce(nodes, edges, W, H);
  const deg = {};
  edges.forEach((e) => { deg[e.a] = (deg[e.a] || 0) + 1; deg[e.b] = (deg[e.b] || 0) + 1; });
  const lines = edges.filter((e) => pos[e.a] && pos[e.b]).map((e) => `<line x1="${pos[e.a].x.toFixed(1)}" y1="${pos[e.a].y.toFixed(1)}" x2="${pos[e.b].x.toFixed(1)}" y2="${pos[e.b].y.toFixed(1)}"
    class="ge ${e.co_mention ? "co" : ""} ${e.same_sector && !e.corr && !e.co_mention ? "sec" : ""}" stroke-width="${(0.6 + (e.weight || 0) * 1.6).toFixed(2)}"><title>${esc((nodes[e.a]?.name || e.a) + " ↔ " + (nodes[e.b]?.name || e.b))}${e.corr ? ` · 상관 ${e.corr}` : ""}${e.co_mention ? ` · 뉴스 동시언급 ${e.co_mention}회` : ""}${e.same_sector ? ` · ${e.same_sector}` : ""}</title></line>`).join("");
  const dots = Object.entries(nodes).filter(([s]) => pos[s]).map(([s, v]) => {
    const r = s === center ? 14 : Math.min(11, 4 + Math.sqrt(deg[s] || 1) * 1.6);
    return `<g class="gn ${s === center ? "center" : ""}" data-sym="${esc(s)}" transform="translate(${pos[s].x.toFixed(1)},${pos[s].y.toFixed(1)})"><circle r="${r}" fill="${secColor(v.sector)}"><title>${esc(v.name)}${v.sector ? ` · ${esc(v.sector)}` : ""}</title></circle><text y="${r + 11}">${esc((v.name || s).slice(0, 8))}</text></g>`;
  }).join("");
  return `<svg class="graph" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">${lines}${dots}</svg>`;
}

async function viewGraph(el) {
  const [g, ag] = await Promise.all([api(`/api/graph${S.graphSym ? `?symbol=${encodeURIComponent(S.graphSym)}` : ""}`), api("/api/agents")]);
  const sv = ag.sector_view || {}, mb = ag.macro_brief || {}, nd = ag.news_digest || {};
  const secRows = (sv.table || []).map((r) => `<tr><td class="num dim">${r.rank}</td><td><span class="sec-dot" style="background:${secColor(r.sector)}"></span><b>${esc(r.sector)}</b> <span class="xs dim">${r.n}종목</span></td>
    <td class="r num ${r.ret_5 >= 0 ? "up" : "down"}">${P(r.ret_5)}</td><td class="r num ${r.ret_20 >= 0 ? "up" : "down"}">${P(r.ret_20)}</td>
    <td class="r num ${r.rs_20 >= 0 ? "up" : "down"}">${P(r.rs_20)}</td><td class="r">${hbar(r.breadth)}<span class="num xs">${R(r.breadth, 0)}</span></td>
    <td class="small">${(r.leaders || []).map((m) => `<a href="#analysis/${esc(m.symbol)}">${esc(m.name)}</a> <span class="${m.ret_5 >= 0 ? "up" : "down"}">${P(m.ret_5)}</span>`).join(" · ")}</td></tr>`).join("");
  const nb = (g.neighbors || []).map((n) => `<div class="nb-it"><span class="sec-dot" style="background:${secColor((g.nodes[n.symbol] || {}).sector)}"></span><a href="#analysis/${esc(n.symbol)}"><b>${esc(n.name)}</b></a>
    <span class="xs muted">${[n.corr ? `상관 ${n.corr}` : null, n.co_mention ? `뉴스 ${n.co_mention}회` : null, n.same_sector ? `같은 업종` : null].filter(Boolean).join(" · ")}</span>
    ${n.title ? `<div class="xs dim nb-t">📰 ${esc(n.title)}</div>` : ""}</div>`).join("");
  const nodesList = Object.entries(g.nodes || {}).sort((a, b) => (a[1].name || "").localeCompare(b[1].name || ""));
  el.innerHTML = `
  <div class="grid g-3 ag-row">
    ${card(`매크로 에이전트 <span class="chip xs">${esc(mb.source || "-")}</span>`, mb.view ? `<div class="ag-risk">위험 <b>${esc(mb.risk_level || "-")}</b> · 위험 선호 ${hbar(((mb.stance ?? 0) + 1) / 2, 1, "acc")}</div><div class="small" style="margin-top:6px">${esc(mb.view)}</div>
      ${(mb.watch || []).length ? `<div class="xs muted" style="margin-top:6px">오늘 볼 것: ${mb.watch.map(esc).join(" · ")}</div>` : ""}` : empty("아직 없음 — 12시간마다"))}
    ${card(`뉴스 에이전트 <span class="chip xs">${esc(nd.source || "-")}</span>`, nd.summary ? `<div class="small">${esc(nd.summary)}</div><div class="ag-news">${(nd.events || []).slice(0, 5).map((x) => `<div><span class="imp-bar ${x.impact >= 0 ? "up" : "down"}" style="width:${Math.round(Math.abs(x.impact || 0) * 40) + 4}px"></span><span class="small">${esc(x.title)}</span>${x.priced_in ? ' <span class="chip xs">선반영</span>' : ""}<div class="xs dim">${esc(x.why || "")}</div></div>`).join("")}</div>` : empty("아직 없음 — 장중 매시간"))}
    ${card(`섹터 에이전트 <span class="chip xs">${esc(sv.source || "-")}</span>`, sv.view ? `<div class="small">${esc(sv.view)}</div><div class="xs muted" style="margin-top:6px">순환: ${esc(sv.rotation || "-")}</div>` : empty(`업종 지도 ${ag.sector_map_size || 0}종목 — 장외에 조금씩 채워집니다`))}
  </div>
  ${card(`업종 강약 <span class="small dim">지수 대비 상대강도(RS 20일) 순 · 폭 = 20일선 위 비율</span>`, secRows ? `<div class="scroll"><table class="tight"><thead><tr><th>#</th><th>업종</th><th class="r">5일</th><th class="r">20일</th><th class="r">RS</th><th class="r">폭</th><th>주도 종목</th></tr></thead><tbody>${secRows}</tbody></table></div>` : empty("업종 정보가 아직 없습니다"),
    `<button class="btn-sm" id="gr-build">지금 만들기</button>`)}
  <div class="grid g-21">
    ${card(`지식 그래프 ${S.graphSym ? `<span class="small dim">· ${esc((g.nodes[S.graphSym] || {}).name || S.graphSym)} 중심</span>` : `<span class="small dim">${g.stats ? `${g.stats.nodes}종목 · 관계 ${g.stats.edges}개 (뉴스 ${g.stats.co_mention_pairs} · 가격 ${g.stats.corr_pairs})` : ""}</span>`}`,
      `<div id="graph-box">${graphSvg(g, 900, S.graphSym ? 520 : 620, S.graphSym)}</div><div class="xs dim">선 굵기 = 관계 강도 · 파란 선 = 뉴스 동시 언급 · 색 = 업종 · 점을 누르면 그 종목 중심으로</div>`,
      `${S.graphSym ? '<button class="btn-sm" id="gr-all">전체 보기</button>' : ""} <select class="sym-select" id="gr-sel"><option value="">종목 선택…</option>${nodesList.map(([s, v]) => `<option value="${esc(s)}" ${s === S.graphSym ? "selected" : ""}>${esc(v.name)}</option>`).join("")}</select>`)}
    ${card(S.graphSym ? "연관 종목" : "그래프 읽는 법", S.graphSym ? (nb || empty("연결 없음")) : `<ul class="plain small"><li>· <b>가격 연동</b>: 120거래일 수익률 상관 0.55 이상</li><li>· <b>뉴스 동시 언급</b>: 90일 동안 같은 기사에 2번 이상 (공급망·경쟁·같은 이슈)</li><li>· <b>같은 업종</b>: 섹터 엔진의 업종</li><li>· AI 가 종목을 판단할 때 연관 종목의 최근 움직임과 관계 근거를 함께 봅니다.</li></ul>`)}
  </div>`;
  document.querySelectorAll("#graph-box .gn").forEach((n) => n.onclick = () => { S.graphSym = n.dataset.sym; render(); });
  const sel = $("#gr-sel"); if (sel) sel.onchange = () => { S.graphSym = sel.value || null; render(); };
  const all = $("#gr-all"); if (all) all.onclick = () => { S.graphSym = null; render(); };
  $("#gr-build").onclick = async (ev) => { ev.target.disabled = true; ev.target.textContent = "만드는 중…"; await runAction("agents"); const r = await runAction("graph"); toast({ title: r.error ? "실패" : "그래프·업종·에이전트 갱신", body: r.error || "", level: r.error ? "warn" : "good" }); render(); };
}

// ================================================================= 리포트
async function viewReports(el) {
  const list = (await api("/api/reports")).reports;
  const cur = S.param || list[0]?.file;
  let r = null;
  if (cur) { try { r = await api(`/api/report?file=${encodeURIComponent(cur)}`); } catch { r = null; } }
  const items = list.map((x) => `<a class="rp-it ${x.file === cur ? "on" : ""}" href="#reports/${esc(x.file)}">${x.kind === "morning" ? "🌅 아침 브리핑" : "📊 일일 리포트"}<span class="xs dim">${esc(x.date)}</span></a>`).join("");
  let body = empty("리포트가 없습니다 — 평일 08:30 아침 브리핑, 16:10 일일 리포트가 자동으로 만들어집니다");
  if (r) {
    const lines = (r.text || "").split("\n").slice(1).map((l) => `<div class="rp-line">${esc(l)}</div>`).join("");
    body = `<div class="rp-head"><h2>${r.kind === "morning" ? "🌅 아침 브리핑" : "📊 일일 리포트"} <span class="dim">${esc(r.date)}</span></h2></div><div class="rp-lines">${lines}</div>`;
    if (r.kind === "morning" && (r.predictions || []).length) body += `<table class="tight" style="margin-top:12px"><thead><tr><th>종목</th><th>신호</th><th class="r">상승</th><th class="r">예상</th></tr></thead><tbody>${r.predictions.map((p) => `<tr><td><a href="#analysis/${esc(p.symbol)}">${esc(p.name)}</a></td><td>${badge(p.action)}</td><td class="r num">${R(p.prob_up, 0)}</td><td class="r num">${P(p.expected)}</td></tr>`).join("")}</tbody></table>`;
    if (r.kind === "daily" && (r.recent || []).length) body += `<table class="tight" style="margin-top:12px"><thead><tr><th>종목</th><th>신호</th><th class="r">예상</th><th class="r">실제</th><th></th></tr></thead><tbody>${r.recent.map((x) => `<tr><td>${esc(x.name)}</td><td>${badge(x.action)}</td><td class="r num">${P(x.expected)}</td><td class="r num ${x.actual >= 0 ? "up" : "down"}">${P(x.actual)}</td><td class="small">${x.hit ? "✔" : "✘ " + esc(x.miss_reason || "")}</td></tr>`).join("")}</tbody></table>`;
    if (r.ledger) body += `<div class="xs muted" style="margin-top:12px">예측 장부 봉인 #${r.ledger.upto_id}</div><div class="digest mono">${esc(r.ledger.digest)}</div>`;
  }
  el.innerHTML = `<div class="grid rp-grid">${card("리포트", `<div class="rp-list">${items || empty()}</div><div style="display:grid;gap:6px;margin-top:10px"><button class="btn-sm" id="rp-m">아침 브리핑 지금 만들기</button><button class="btn-sm" id="rp-d">일일 리포트 지금 만들기</button></div>`)}${card("", body)}</div>`;
  const mk = (id, name) => { const b = $(id); b.onclick = async () => { b.disabled = true; b.textContent = "만드는 중…"; const x = await runAction(name); toast({ title: x.error ? "실패" : "리포트 생성 · 알림 전송", body: x.error || x.result?.file || "", level: x.error ? "warn" : "good" }); location.hash = x.result?.file ? `#reports/${x.result.file}` : "#reports"; render(); }; };
  mk("#rp-m", "morning_brief"); mk("#rp-d", "daily_report");
}

// ================================================================= 종목 화면: 알림 규칙 · 수급 · 연관 종목 · 공시 요약
async function stockExtras(sym, a, p) {
  const box = $("#pf-extra");
  if (!box) return;
  const [rules, g] = await Promise.all([api("/api/rules").catch(() => ({ rules: [] })), api(`/api/graph?symbol=${encodeURIComponent(sym)}`).catch(() => ({}))]);
  const mine = (rules.rules || []).filter((r) => r.symbol === sym);
  const KIND = rules.kinds || {};
  const ruleRows = mine.map((r) => `<div class="rule-it ${r.active ? "" : "off"}"><b>${esc(KIND[r.kind] || r.kind)}</b> <span class="num">${r.kind === "move" ? `±${r.value}%` : r.kind === "volume" ? `${r.value}배` : num(r.value, r.value >= 1000 ? 0 : 2)}</span>
    ${r.repeat ? '<span class="chip xs">매일</span>' : ""}${r.active ? "" : ' <span class="chip xs">울림</span>'} <span class="xs dim">${esc(r.note || "")}</span>
    <button class="btn-sm" data-rule-toggle="${r.id}" data-on="${r.active ? 0 : 1}">${r.active ? "끄기" : "다시 켜기"}</button><button class="btn-sm" data-rule-del="${r.id}">삭제</button></div>`).join("");
  const last = a.last;
  const ruleCard = card("알림 설정 <span class='small dim'>이 종목만의 기준 · 토스트 + 텔레그램 + 푸시</span>", `
    <div class="rule-form"><select id="rl-kind"><option value="above">목표가 이상</option><option value="below">손절가 이하</option><option value="move">등락률 ±%</option><option value="volume">거래량 평소의 N배</option></select>
      <input id="rl-val" type="number" step="any" placeholder="${last ? num(last * 1.1, 0) : "값"}"><input id="rl-note" placeholder="메모 (선택)" maxlength="60">
      <label class="chk-chip"><input type="checkbox" id="rl-rep"> 매일 반복</label><button class="btn-sm primary" id="rl-add">추가</button></div>
    <div class="xs dim" style="margin:6px 0 8px">${last ? `현재가 ${num(last, 2)} · 빠른 설정: <button class="btn-sm" data-q="above:1.1">+10% 목표</button> <button class="btn-sm" data-q="below:0.93">−7% 손절</button> <button class="btn-sm" data-q="move:5">±5%</button> <button class="btn-sm" data-q="volume:3">거래량 3배</button>` : ""}</div>
    ${ruleRows || '<div class="xs dim">아직 규칙이 없습니다</div>'}`);
  const fl = p.flow;
  let flowCard = "";
  if (fl && (fl.rows || []).length) {
    const rows = fl.rows.slice(-20), mx = Math.max(...rows.map((r) => Math.max(Math.abs(r.foreign || 0), Math.abs(r.inst || 0))), 1);
    const bars = rows.map((r) => `<div class="fl-col" title="${esc(r.date)} 외국인 ${num(r.foreign)} · 기관 ${num(r.inst)}"><i class="f ${r.foreign >= 0 ? "p" : "n"}" style="height:${Math.abs(r.foreign || 0) / mx * 40}px"></i><i class="o ${r.inst >= 0 ? "p" : "n"}" style="height:${Math.abs(r.inst || 0) / mx * 40}px"></i></div>`).join("");
    const sm = fl.summary || {};
    flowCard = card("수급 <span class='small dim'>외국인 · 기관 순매수 (주)</span>", `<div class="kv-grid">
      <div class="kvt"><div class="xs muted">외국인 5일</div><div class="num b ${sm.foreign_5d >= 0 ? "up" : "down"}">${num(sm.foreign_5d)}</div></div>
      <div class="kvt"><div class="xs muted">기관 5일</div><div class="num b ${sm.inst_5d >= 0 ? "up" : "down"}">${num(sm.inst_5d)}</div></div>
      <div class="kvt"><div class="xs muted">외국인 연속</div><div class="num b">${sm.foreign_streak ? `${Math.abs(sm.foreign_streak)}일 ${sm.foreign_streak > 0 ? "매수" : "매도"}` : "-"}</div></div></div>
      <div class="fl-bars">${bars}</div><div class="xs dim">왼쪽 막대 외국인 · 오른쪽 기관 · 빨강 순매수 · 파랑 순매도${sm.foreign_ratio ? ` · 외국인 보유율 ${R(sm.foreign_ratio)}` : ""}</div>`);
  }
  const graphCard = (g.neighbors || []).length ? card("연관 종목 <span class='small dim'>지식 그래프</span>", `${graphSvg(g, 420, 300, sym)}<div class="nb-list">${g.neighbors.slice(0, 6).map((n) => `<div class="nb-it"><a href="#analysis/${esc(n.symbol)}"><b>${esc(n.name)}</b></a> <span class="xs muted">${[n.corr ? `상관 ${n.corr}` : null, n.co_mention ? `뉴스 ${n.co_mention}회` : null, n.same_sector ? "같은 업종" : null].filter(Boolean).join(" · ")}</span></div>`).join("")}</div>`, `<a class="link" href="#graph">그래프 ${ICONS.arrow}</a>`) : "";
  const discs = (a.disclosures || []);
  const discCard = discs.length ? card("최근 공시 <span class='small dim'>원문 요약 포함</span>", discs.map((d) => `<div class="pfn"><div style="flex:1;min-width:0"><div class="t">${safeUrl(d.url) ? `<a href="${safeUrl(d.url)}" target="_blank" rel="noopener">${esc(d.title)}</a>` : esc(d.title)}</div>
      ${d.summary ? `<div class="xs muted disc-sum">${esc(d.summary)}</div>` : ""}<div class="xs dim">${esc(d.date)}</div></div></div>`).join("")) : "";
  box.innerHTML = `<div class="grid g-2">${ruleCard}${flowCard || graphCard}</div>${flowCard && graphCard ? `<div class="grid g-2">${graphCard}${discCard}</div>` : discCard}`;
  const add = async (kind, value) => {
    try { await post("/api/rules", { symbol: sym, kind, value, note: $("#rl-note")?.value || "", repeat: $("#rl-rep")?.checked }); toast({ title: "알림 규칙 추가", body: `${KIND[kind] || kind} ${value}`, level: "good", kind: "rule" }); stockExtras(sym, a, p); }
    catch (e) { toast({ title: "추가 실패", body: e.message, level: "warn" }); }
  };
  $("#rl-add").onclick = () => { const v = Number($("#rl-val").value); if (!v) { toast({ title: "값을 입력하세요", level: "warn" }); return; } add($("#rl-kind").value, v); };
  box.querySelectorAll("[data-q]").forEach((b) => b.onclick = () => { const [k, f] = b.dataset.q.split(":"); add(k, k === "above" || k === "below" ? Math.round(last * Number(f) * 100) / 100 : Number(f)); });
  box.querySelectorAll("[data-rule-toggle]").forEach((b) => b.onclick = async () => { await post("/api/rules", { id: Number(b.dataset.ruleToggle), active: b.dataset.on === "1" }); stockExtras(sym, a, p); });
  box.querySelectorAll("[data-rule-del]").forEach((b) => b.onclick = async () => { await post("/api/rules", { id: Number(b.dataset.ruleDel), delete: true }); stockExtras(sym, a, p); });
  box.querySelectorAll(".gn").forEach((n) => n.onclick = () => { if (n.dataset.sym !== sym) location.hash = `#analysis/${n.dataset.sym}`; });
}

// ================================================================= 설정: 외부 알림 · 앱 설치 · 푸시 · 전체 규칙 · 백업
function extraSettingsCard() {
  return card("휴대폰 · 외부 알림 · 백업", `<div id="xs-box">${empty("불러오는 중…")}</div>`);
}
async function bindExtraSettings() {
  const box = $("#xs-box");
  if (!box) return;
  const [pi, rules] = await Promise.all([api("/api/push/key").catch(() => ({})), api("/api/rules").catch(() => ({ rules: [] }))]);
  const hasSW = "serviceWorker" in navigator, hasPush = hasSW && "PushManager" in window;
  const secure = window.isSecureContext;
  const KIND = rules.kinds || {};
  box.innerHTML = `
    <div class="xs-row"><div><b>📱 앱으로 설치</b><div class="xs muted">홈 화면·작업표시줄에 아이콘. 창 없이 앱처럼 열립니다.</div></div><button class="btn-sm" id="xs-install" ${S.installEvt ? "" : "disabled"}>${S.installEvt ? "설치" : "브라우저 메뉴 → 앱 설치"}</button></div>
    <div class="xs-row"><div><b>🔔 푸시 알림 (사이트를 닫아도)</b><div class="xs muted">${!hasPush ? "이 브라우저는 푸시를 지원하지 않습니다" : !secure ? "HTTPS 주소에서만 됩니다 (docs/MOBILE.md)" : pi.available ? `구독 ${pi.subscriptions}개 · 급등락·규칙·신호·실적·자동정지·리포트` : esc(pi.message || "")}</div></div>
      <div style="display:flex;gap:6px"><button class="btn-sm primary" id="xs-push" ${hasPush && secure && pi.available ? "" : "disabled"}>이 기기 구독</button><button class="btn-sm" id="xs-push-test" ${pi.subscriptions ? "" : "disabled"}>테스트</button></div></div>
    <div class="xs-row"><div><b>✈️ 텔레그램 · 디스코드</b><div class="xs muted">.env 의 QUANT_TELEGRAM_TOKEN · QUANT_TELEGRAM_CHAT_ID (또는 디스코드 웹훅). 키는 채팅에 붙여넣지 마세요.</div><div class="xs" id="xs-nt-res"></div></div><button class="btn-sm" id="xs-nt">테스트 전송</button></div>
    <div class="xs-row"><div><b>💾 DB 백업</b><div class="xs muted">매일 자동 (7개 보관 · 압축 · 무결성 확인) — artifacts/backups</div><div class="xs" id="xs-bk-res"></div></div><button class="btn-sm" id="xs-bk">지금 백업</button></div>
    <div class="small muted" style="margin:14px 0 6px"><b>종목별 알림 규칙</b> (종목 화면에서 추가)</div>
    ${(rules.rules || []).length ? `<div class="scroll"><table class="tight"><tbody>${rules.rules.map((r) => `<tr class="${r.active ? "" : "dim"}"><td><a href="#analysis/${esc(r.symbol)}">${esc(r.name)}</a></td><td>${esc(KIND[r.kind] || r.kind)}</td><td class="r num">${r.kind === "move" ? `±${r.value}%` : r.kind === "volume" ? `${r.value}배` : num(r.value, r.value >= 1000 ? 0 : 2)}</td><td class="small">${r.active ? "대기" : "울림/꺼짐"}${r.repeat ? " · 매일" : ""}</td><td class="r"><button class="btn-sm" data-rule-del="${r.id}">삭제</button></td></tr>`).join("")}</tbody></table></div>` : '<div class="xs dim">없음</div>'}`;
  const ib = $("#xs-install");
  if (ib && S.installEvt) ib.onclick = async () => { S.installEvt.prompt(); await S.installEvt.userChoice; S.installEvt = null; bindExtraSettings(); };
  const pb = $("#xs-push");
  if (pb && !pb.disabled) pb.onclick = async () => {
    try {
      const perm = await Notification.requestPermission();
      if (perm !== "granted") throw new Error("알림 권한이 거부되었습니다");
      const reg = await navigator.serviceWorker.ready;
      const key = Uint8Array.from(atob(pi.public_key.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - pi.public_key.length % 4) % 4)), (c) => c.charCodeAt(0));
      const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
      await post("/api/push/subscribe", { subscription: sub.toJSON(), label: navigator.userAgent.slice(0, 60) });
      toast({ title: "이 기기에서 푸시를 받습니다", level: "good" }); bindExtraSettings();
    } catch (e) { toast({ title: "푸시 구독 실패", body: e.message, level: "warn" }); }
  };
  const pt = $("#xs-push-test");
  if (pt && !pt.disabled) pt.onclick = async () => { const r = await post("/api/push/test", {}); toast({ title: `푸시 전송 ${r.sent}건`, body: (r.errors || []).join(" · "), level: r.sent ? "good" : "warn" }); };
  $("#xs-nt").onclick = async () => {
    const r = await post("/api/notify/test", {});
    $("#xs-nt-res").innerHTML = r.enabled ? Object.entries(r.results || {}).map(([k, v]) => `<span class="chip ${v === "ok" ? "pos" : "neg"}">${esc(k)} ${esc(v)}</span>`).join(" ") : `<span class="warn-t">${esc(r.message)}</span>`;
  };
  $("#xs-bk").onclick = async (ev) => { ev.target.disabled = true; const r = await runAction("backup"); ev.target.disabled = false; $("#xs-bk-res").textContent = r.error ? `실패: ${r.error}` : r.result?.file ? `${r.result.file} · ${(r.result.size / 1e6).toFixed(1)}MB · 무결성 ${r.result.integrity}` : (r.result?.skipped || ""); };
  box.querySelectorAll("[data-rule-del]").forEach((b) => b.onclick = async () => { await post("/api/rules", { id: Number(b.dataset.ruleDel), delete: true }); bindExtraSettings(); });
}

// ================================================================= 관제실: 시장 에이전트 한 줄
async function fillAgentStrip() {
  const b = $("#ag-strip");
  if (!b) return;
  try {
    const ag = await api("/api/agents");
    const mb = ag.macro_brief || {}, nd = ag.news_digest || {}, sv = ag.sector_view || {};
    b.innerHTML = `<div class="grid g-3">
      ${card("🌐 매크로", mb.view ? `<div class="small"><b>위험 ${esc(mb.risk_level || "-")}</b> · ${esc(mb.view)}</div>` : empty("대기"), `<a class="link" href="#graph">자세히 ${ICONS.arrow}</a>`)}
      ${card("📰 오늘의 뉴스", nd.summary ? `<div class="small">${esc(nd.summary)}</div>` : empty("대기"))}
      ${card("🏭 업종", sv.view ? `<div class="small">${esc(sv.view)}</div>` : empty("대기"))}</div>`;
  } catch { b.innerHTML = ""; }
}

// ================================================================= 모바일 하단 탭 · 서비스 워커
function buildTabbar() {
  const t = $("#tabbar");
  if (!t) return;
  const items = [["dashboard", "home", "홈"], ["control", "control", "관제실"], ["readiness", "check", "준비"], ["power", "score", "예측력"], ["chat", "chat", "AI"]];
  t.innerHTML = items.map(([v, ic, l]) => `<a href="#${v}" data-tab="${v}">${ICONS[ic] || ""}<span>${l}</span></a>`).join("");
}
function markTab() {
  document.querySelectorAll("#tabbar a").forEach((a) => a.classList.toggle("on", a.dataset.tab === S.view));
}
function initPWA() {
  buildTabbar();
  window.addEventListener("beforeinstallprompt", (e) => { e.preventDefault(); S.installEvt = e; });
  if ("serviceWorker" in navigator && window.isSecureContext) navigator.serviceWorker.register("/sw.js").catch(() => {});
}
