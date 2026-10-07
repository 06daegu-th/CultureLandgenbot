/* v19: 내 목표 — '500만원 → 1억' 을 확률로 · 월 적립식 · 홈 진행률 */
"use strict";

const won = (v) => v == null ? "-" : Math.abs(v) >= 1e8 ? `${(v / 1e8).toFixed(2).replace(/\.?0+$/, "")}억` : `${Math.round(v / 1e4).toLocaleString("ko-KR")}만`;

// 연도별 10%·50%·90% 경로 + 목표선 + 낸 돈 — 작은 SVG 부채꼴 차트
function goalFan(yearly, goalAmt, years) {
  const xs = yearly.slice(0, Math.max(years + 5, 10));
  const W = 640, H = 220, L = 46, R = 10, T = 12, B = 24;
  const hi = Math.max(goalAmt * 1.15, ...xs.map((y) => y.p90 * 0.9));
  const x = (i) => L + (W - L - R) * (i / Math.max(xs.length - 1, 1));
  const y = (v) => T + (H - T - B) * (1 - Math.min(v, hi) / hi);
  const line = (k) => xs.map((r, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(r[k]).toFixed(1)}`).join("");
  const band = xs.map((r, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(r.p90).toFixed(1)}`).join("") + xs.slice().reverse().map((r, j) => `L${x(xs.length - 1 - j).toFixed(1)},${y(r.p10).toFixed(1)}`).join("") + "Z";
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => hi * f);
  const tx = xs.map((r, i) => (r.year % 5 === 0 || r.year === 1) ? `<text x="${x(i)}" y="${H - 6}" font-size="10" text-anchor="middle" fill="currentColor" opacity=".6">${r.year}년</text>` : "").join("");
  const ti = years - 1 < xs.length ? `<line x1="${x(years - 1)}" x2="${x(years - 1)}" y1="${T}" y2="${H - B}" stroke="#f59e0b" stroke-dasharray="3 3" opacity=".8"/>` : "";
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" role="img" aria-label="연도별 자산 경로 (10%·50%·90%)">
    ${ticks.map((v) => `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="currentColor" opacity=".08"/><text x="${L - 6}" y="${y(v) + 3}" font-size="10" text-anchor="end" fill="currentColor" opacity=".6">${won(v)}</text>`).join("")}
    <path d="${band}" fill="rgba(59,130,246,.18)"/><path d="${line("p50")}" fill="none" stroke="#3b82f6" stroke-width="2.5"/>
    <path d="${line("paid")}" fill="none" stroke="currentColor" stroke-width="1.5" stroke-dasharray="4 4" opacity=".5"/>
    <line x1="${L}" x2="${W - R}" y1="${y(goalAmt)}" y2="${y(goalAmt)}" stroke="#22c55e" stroke-width="2"/>
    <text x="${W - R}" y="${y(goalAmt) - 5}" font-size="11" text-anchor="end" fill="#22c55e">목표 ${won(goalAmt)}</text>${ti}${tx}</svg>
    <div class="xs muted">파란 선: 가운데(절반은 이보다 좋고 절반은 나쁨) · 파란 띠: 10%~90% 범위 · 점선: 낸 돈 · 노란 점선: 목표 기간</div>`;
}

async function viewGoal(el) {
  const q = S.goalQ || {};
  const qs = new URLSearchParams(Object.entries(q).filter(([, v]) => v !== "" && v != null)).toString();
  let g;
  try { g = await api(`/api/goal${qs ? `?${qs}` : ""}`); } catch (e) { el.innerHTML = card("내 목표", `<div class="veto">${esc(e.message)}</div>`); return; }
  const i = g.inputs, sim = g.sim, pr = g.progress || {}, d = (g.saved || {}).dca || {};
  const pct = (v) => `${Math.round(v * 100)}%`;
  const opt = Object.entries(g.presets).map(([k, p]) => `<option value="${k}" ${k === i.strategy ? "selected" : ""}>${esc(p.name)} (연 ${(p.mu * 100).toFixed(1)}% 가정)</option>`).join("");
  const cmp = g.compare.map((c) => `<tr class="${c.key === i.strategy ? "sel" : ""}"><td>${esc(c.name)}</td><td class="r b">${pct(c.p_target)}</td><td class="r">${c.median_years ? c.median_years + "년" : "30년+"}</td><td class="r down">${c.mdd_p50 ? P(c.mdd_p50, 0) : "0%"}</td></tr>`).join("");
  const wi = g.what_if.map((w) => `<tr class="${w.monthly === i.monthly ? "sel" : ""}"><td>매달 ${won(w.monthly)}원</td><td class="r b">${pct(w.p_target)}</td><td class="r">${w.median_years ? w.median_years + "년" : "30년+"}</td></tr>`).join("");
  el.innerHTML = `
  <div class="card goal-hero"><div class="card-h"><h3>내 목표 — 확률로 보는 계획</h3><div class="right xs dim">${esc(g.assumption.source)}</div></div>
    <div class="goal-form">
      <label>지금 원금<input id="g-principal" inputmode="numeric" value="${num(i.principal)}"></label>
      <label>매달 적립<input id="g-monthly" inputmode="numeric" value="${num(i.monthly)}"></label>
      <label>목표 금액<input id="g-goal" inputmode="numeric" value="${num(i.goal)}"></label>
      <label>목표 기간 (년)<input id="g-years" inputmode="numeric" value="${i.target_years}"></label>
      <label>투자 방식<select id="g-strategy">${opt}</select></label>
      <label>해마다 적립액 늘리기 (%)<input id="g-raise" inputmode="decimal" value="${(i.raise_pct * 100).toFixed(0)}"></label>
    </div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin:8px 0"><button class="btn-sm primary" id="g-calc">다시 계산</button><button class="btn-sm" id="g-save">이 목표로 저장 (홈에 진행률 표시)</button><span class="xs muted" id="g-msg"></span></div>
    <div class="goal-head"><div class="goal-p ${g.p_target >= 0.7 ? "up" : g.p_target >= 0.4 ? "warn-t" : "down"}">${pct(g.p_target)}</div>
      <div><div class="b">${esc(g.headline)}</div>
      <div class="small muted">${i.target_years}년 안에 50% 확률로 닿으려면 매달 <b>${g.need_monthly.p50 ? won(g.need_monthly.p50) + "원" : "불가"}</b> · 80% 확률이면 <b>${g.need_monthly.p80 ? won(g.need_monthly.p80) + "원" : "불가"}</b></div>
      <div class="small muted">가는 길에 겪을 가장 큰 하락(중간값) <b class="down">${P(sim.mdd_p50, 0)}</b> · −30% 넘게 떨어지는 구간을 겪을 확률 ${pct(sim.p_mdd30)}</div></div></div>
    ${goalFan(sim.yearly, i.goal, i.target_years)}
    ${(g.honest || []).map((h) => `<div class="lesson small" style="margin-top:6px">${esc(h)}</div>`).join("")}
  </div>
  <div class="grid g-2">
    ${card("투자 방식별 — 같은 원금·적립액으로", `<table class="tight"><thead><tr><th>방식</th><th class="r">${i.target_years}년 안 확률</th><th class="r">절반의 경우</th><th class="r">중간 최대 하락</th></tr></thead><tbody>${cmp}</tbody></table>
      <div class="xs dim" style="margin-top:6px">AI 위성 전략은 넣지 않았습니다 — 검증을 통과하기 전까지 계획에 쓰지 않습니다 (지금 성적은 지수보다 나쁨).</div>`)}
    ${card("적립액을 바꾸면", `<table class="tight"><thead><tr><th>적립</th><th class="r">${i.target_years}년 안 확률</th><th class="r">절반의 경우</th></tr></thead><tbody>${wi}</tbody></table>`)}
  </div>
  ${(g.tax || []).length ? card("어느 계좌에 넣을까 — 같은 돈 · 같은 방식, 세금만 다르게", `<table class="tight tax-tbl"><thead><tr><th>계좌</th><th class="r">${i.target_years}년 안 확률</th><th class="r">절반의 경우</th><th>조건</th></tr></thead><tbody>
      ${g.tax.map((t) => `<tr class="${t.best ? "sel" : ""}"><td class="b">${esc(t.name)}${t.best ? ' <span class="chip xs ok">가장 유리</span>' : ""}</td>
        ${t.ok ? `<td class="r b">${pct(t.p_target)}</td><td class="r">${t.median_years ? t.median_years + "년" : "30년+"}</td>` : `<td class="r dim" colspan="2">${esc(t.why_not || "불가")}</td>`}
        <td class="xs muted">${esc(t.note)}${t.refund_year ? ` · 해마다 돌려받는 세금 약 ${won(t.refund_year)}원` : ""}</td></tr>`).join("")}</tbody></table>
    <div class="xs dim" style="margin-top:6px">2026년 세법 기준 단순 추정입니다. 연금저축은 55세 전에 깨면 공제받은 세금을 돌려줘야 하니 나이와 기간을 먼저 확인하세요. ISA 는 3년을 채워야 혜택이 있습니다.</div>`) : ""}
  ${card("월 적립식 — 정한 날 자동으로", `<div class="goal-form">
      <label class="chk-row"><span>자동 적립</span><span><input type="checkbox" id="d-on" ${d.on ? "checked" : ""}> 켜기</span></label>
      <label>매달 며칠 (1~28)<input id="d-day" inputmode="numeric" value="${d.day || 25}"></label>
      <label>금액<input id="d-amount" inputmode="numeric" value="${num(d.amount || i.monthly)}"></label>
      <label>장부<select id="d-mode"><option value="paper" ${d.mode !== "live" ? "selected" : ""}>모의 (자동으로 넣고 삼)</option><option value="live" ${d.mode === "live" ? "selected" : ""}>실계좌 (알림 + 주문표만)</option></select></label>
      <label>무엇을 살까<select id="d-target"><option value="core" ${d.target !== "etf" ? "selected" : ""}>코어 전략 (시스템이 종목 선택)</option><option value="etf" ${d.target === "etf" ? "selected" : ""}>지수 ETF 하나만</option></select></label>
      <label>ETF<select id="d-etf">${Object.entries(g.etfs || {}).map(([k, v]) => `<option value="${k}" ${(d.etf || "069500") === k ? "selected" : ""}>${esc(v)} (${k})</option>`).join("")}</select></label></div>
    <div class="xs muted">휴장일이면 다음 거래일 · 한 달에 한 번만 · ETF 는 따로 만든 'ETF 적립 장부'에서 원금부터 함께 굴립니다 (코어 장부와 섞지 않음) · 실계좌는 돈을 옮길 수 없어서 알림과 주문표(몇 주 살지)만 보냅니다${d.last ? ` · 마지막 적립 ${esc(d.last)}` : ""}</div>
    <button class="btn-sm" id="d-save" style="margin-top:8px">적립 설정 저장</button>`)}
  <div id="g-base"></div>
  ${pr.set ? card("진행률", goalBar(pr)) : ""}
  <div class="xs dim">${esc(g.note)}</div>`;
  baselineCard($("#g-base"));
  const val = (id) => $(id).value.replace(/[^\d.]/g, "");
  const read = () => ({ principal: val("#g-principal"), monthly: val("#g-monthly"), goal: val("#g-goal"), target_years: val("#g-years"),
    strategy: $("#g-strategy").value, raise_pct: String((Number(val("#g-raise")) || 0) / 100) });
  $("#g-calc").onclick = () => { S.goalQ = read(); render(); };
  const save = async (extra = {}) => {
    const r = await post("/api/goal", { ...read(), ...extra }).catch((e) => ({ error: e.message }));
    $("#g-msg").textContent = r.error ? `저장 실패: ${r.error}` : "저장했습니다 — 홈에 진행률이 나옵니다";
    if (!r.error) { S.goalQ = null; setTimeout(render, 600); }
  };
  $("#g-save").onclick = () => save();
  $("#d-save").onclick = () => save({ dca: { on: $("#d-on").checked, day: Number(val("#d-day")), amount: Number(val("#d-amount")), mode: $("#d-mode").value, target: $("#d-target").value, etf: $("#d-etf").value } });
}

function goalBar(pr) {
  const w = Math.max(1, Math.min(100, pr.pct * 100));
  return `<div class="goal-bar"><i style="width:${w}%"></i></div>
    <div class="small"><b>${won(pr.total)}원</b> / ${won(pr.goal)}원 <span class="muted">(${(pr.pct * 100).toFixed(1)}%)</span> ·
      ${Math.abs(pr.ahead || 0) < 10000 ? `<span class="muted">계획대로 가는 중</span>` : `<span class="${pr.on_track ? "up" : "down"}">${pr.on_track ? "계획대로 가는 중" : "계획보다 뒤처짐"} (${pr.ahead >= 0 ? "+" : "−"}${won(Math.abs(pr.ahead))}원)</span>`}</div>
    <div class="xs dim">${esc(pr.source || "")} 기준 · ${pr.months ? `시작 ${pr.months}개월째` : "이번 달 시작"}${pr.dca?.on ? ` · 매달 ${pr.dca.day}일 ${won(pr.dca.amount)}원 적립` : ""}</div>`;
}

// v20: 코어 vs '그냥 지수 ETF 를 샀다면' — 이 시스템을 쓸 이유가 돈으로 있는가
async function baselineCard(box) {
  if (!box) return;
  let b;
  try { b = await api("/api/baseline"); } catch (e) { box.innerHTML = ""; return; }
  const rs = b.research, lv = b.live || {}, rc = b.recommend || {};
  const row = (x) => `<td class="r">${P(x.cagr, 1)}</td><td class="r">${(x.vol * 100).toFixed(1)}%</td><td class="r down">${P(x.mdd, 0)}</td>`;
  const pct = (v) => v == null ? "-" : P(v, 1);
  const live = lv.status && lv.days ? `
    <div class="small b" style="margin-top:10px">실제 장부 vs ETF 그림자 장부 <span class="xs dim">(${esc(lv.from)} ~ ${esc(lv.to)} · 같은 돈·같은 날 입금)</span></div>
    <table class="tight"><thead><tr><th></th><th class="r">지금 금액</th><th class="r">수익률</th><th class="r">최대 하락</th></tr></thead><tbody>
      <tr><td>이 시스템</td><td class="r b">${won(lv.mine)}원</td><td class="r">${pct(lv.ret_mine)}</td><td class="r down">${pct(lv.mdd_mine)}</td></tr>
      <tr><td>지수 ETF 였다면</td><td class="r b">${won(lv.etf)}원</td><td class="r">${pct(lv.ret_etf)}</td><td class="r down">${pct(lv.mdd_etf)}</td></tr></tbody></table>
    <div class="small ${lv.gap >= 0 ? "up" : "down"}">${esc(lv.text)}</div>` : `<div class="small muted" style="margin-top:8px">${esc(lv.text || "")}</div>`;
  box.innerHTML = card("기준선 — 그냥 지수 ETF 를 샀다면?", `
    <table class="tight"><thead><tr><th>${esc(rs.period)}</th><th class="r">연수익</th><th class="r">흔들림</th><th class="r">최대 하락</th></tr></thead><tbody>
      <tr><td>${esc(rs.core.name)}</td>${row(rs.core)}</tr><tr><td>${esc(rs.index.name)}</td>${row(rs.index)}</tr></tbody></table>
    <div class="xs muted">${esc(rs.verdict)}</div>${live}
    <div class="lesson small ${rc.level === "bad" ? "down" : ""}" style="margin-top:8px">${lvDot({ good: "good", bad: "bad", warn: "warn" }[rc.level] || "idle")}${esc(rc.text || "")}</div>
    <div class="xs dim">지수는 배당 제외 · ETF 보수 연 0.15% 반영 — 실제 ETF 는 배당만큼 더 유리하므로 이 비교는 코어 쪽에 기울어 있습니다</div>`);
}
