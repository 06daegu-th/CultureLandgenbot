/* v35 '목표를 끝까지': 체험 계산기 · 꾸준함(연속 적립 · 월간 리포트 · 배지 · 급락 안내) · 함께(친구 초대 · 모임). */
"use strict";

const XP = { q: null };  // 계산기 입력 (화면을 다시 그려도 유지)
const xWon = (v) => (typeof tMoney === "function" ? tMoney(v) : `${num(v)}원`);
const pctTxt = (v, d = 0) => (v == null ? "-" : `${(v * 100).toFixed(d)}%`);
const LV_COL = { great: "good", good: "good", warn: "warn", bad: "bad" };

// ------------------------------------------------------------ 체험 계산기 (가입 전에도)
function fanChart(path, goal, years) {
  if (!path || path.length < 2) return "";
  const W = 640, H = 220, P = { l: 8, r: 8, t: 14, b: 26 };
  const n = path.length;
  const max = Math.max(goal * 1.15, ...path.map((r) => r.p90)) || 1;
  const x = (i) => P.l + (i / (n - 1)) * (W - P.l - P.r);
  const y = (v) => H - P.b - (Math.max(0, v) / max) * (H - P.t - P.b);
  const line = (k) => path.map((r, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(r[k]).toFixed(1)}`).join("");
  const band = `${line("p90")}${path.slice().reverse().map((r, j) => `L${x(n - 1 - j).toFixed(1)},${y(r.p10).toFixed(1)}`).join("")}Z`;
  const ti = Math.min(n - 1, Math.max(0, years - 1));
  const ticks = path.map((r, i) => (i === 0 || (i + 1) % 5 === 0 || i === ti) ? `<text x="${x(i).toFixed(1)}" y="${H - 8}" text-anchor="middle">${r.year}년</text>` : "").join("");
  return `<svg class="xp-fan" viewBox="0 0 ${W} ${H}" role="img" aria-label="해마다 예상 범위">
    <path class="xp-band" d="${band}"/><path class="xp-p50" d="${line("p50")}"/><path class="xp-paid" d="${line("paid")}"/>
    <line class="xp-goal" x1="${P.l}" x2="${W - P.r}" y1="${y(goal).toFixed(1)}" y2="${y(goal).toFixed(1)}"/>
    <text class="xp-goal-t" x="${W - P.r}" y="${(y(goal) - 5).toFixed(1)}" text-anchor="end">목표 ${xWon(goal)}</text>
    <line class="xp-tgt" x1="${x(ti).toFixed(1)}" x2="${x(ti).toFixed(1)}" y1="${P.t}" y2="${H - P.b}"/>${ticks}</svg>
    <div class="xp-legend"><span><i class="b"></i>10명 중 8명이 들어가는 범위</span><span><i class="m"></i>보통(중간)</span><span><i class="p"></i>내가 넣은 돈</span></div>`;
}

function exploreBody(r) {
  const t = r.at_target, i = r.inputs, lv = LV_COL[r.level] || "idle";
  const paidShare = t.p50 > 0 ? Math.min(1, t.paid / Math.max(t.p50, t.paid)) : 1;
  const alts = (r.alternatives || []).map((a, k) => `<button class="xp-alt" data-alt="${k}"><b>${esc(a.title)}</b><span>${esc(a.detail)}</span></button>`).join("");
  return `
    <section class="xp-res lv-${lv}">
      <div class="xp-big"><span class="t-k">${i.years}년 안에 ${xWon(i.goal)}이 될 확률</span><b class="num">${pctTxt(r.prob)}</b><em>${esc(r.verdict)}</em></div>
      ${tProg(r.prob, lv, "달성 확률")}
      <div class="xp-split">
        <div class="xp-split-bar"><i class="p" style="width:${(paidShare * 100).toFixed(1)}%"></i><i class="g" style="width:${((1 - paidShare) * 100).toFixed(1)}%"></i></div>
        <div class="xp-split-k"><span><i class="p"></i>내가 넣은 돈 <b class="num">${xWon(t.paid)}</b></span><span><i class="g"></i>시장이 보탠 돈(보통) <b class="num">${xWon(Math.max(0, t.growth_p50))}</b></span></div>
        <div class="t-sub">${i.years}년 뒤 보통 <b>${xWon(t.p50)}</b> · 10명 중 8명은 ${xWon(t.p10)} ~ ${xWon(t.p90)} 사이</div>
      </div>
    </section>
    ${tCard("해마다 이렇게 가요", fanChart(r.path, i.goal, i.years))}
    ${alts ? tCard("확률을 올리는 방법 (내가 정할 수 있는 것)", `<div class="xp-alts">${alts}</div>`) : ""}
    ${(r.honest || []).length ? `<div class="t-note warn">${r.honest.map(esc).join("<br>")}</div>` : ""}
    ${tKV([["보통 걸리는 기간", r.median_years ? `${r.median_years}년` : "30년 넘게"], ["확률 절반이 되는 해", r.years_50 ? `${r.years_50}년` : "-"],
      ["중간에 빠지는 폭(보통)", pctTxt(r.mdd_p50), "down"], ["필요한 연 수익률", r.need_cagr != null ? pctTxt(r.need_cagr) : "-", r.need_cagr > 0.2 ? "down" : ""]])}
    <div class="t-foot">${esc(r.assumption.name)} 가정 (연 ${(r.assumption.mu * 100).toFixed(1)}% · 변동성 ${(r.assumption.vol * 100).toFixed(0)}%) · ${esc(r.assumption.source)} · ${esc(r.note)}</div>`;
}

async function exploreView(el, opts = {}) {
  const pub = !!opts.public;
  const q = XP.q || { principal: 1000000, monthly: 0, goal: 10000000, years: 5, strategy: "kospi" };
  XP.q = q;
  const field = (id, label, v, hint = "") => `<label class="t2-field"><span class="t-k">${label}</span><input id="${id}" class="t-in" inputmode="numeric" value="${num(v)}">${hint ? `<em>${hint}</em>` : ""}</label>`;
  el.innerHTML = `<div class="ts xp ${pub ? "xp-pub" : ""}">${pub ? "" : tHead("얼마를 언제까지 — 체험 계산기")}
    <div class="xp-intro"><b>얼마로 시작해서, 매달 얼마를 넣으면, 언제 목표에 닿을까?</b><span>약속이 아니라 확률이에요 — 2,000가지 미래를 만들어 목표에 닿은 경우를 셌어요</span></div>
    <div class="xp-presets" id="xp-presets"></div>
    <section class="t-card t-sec xp-in"><div class="t-form">
      ${field("xp-p", "시작 금액 (원)", q.principal)}${field("xp-m", "매달 넣을 돈 (원)", q.monthly, "0원이면 한 번만 넣기")}
      ${field("xp-g", "목표 금액 (원)", q.goal)}${field("xp-y", "기간 (년)", q.years)}
      <label class="t2-field"><span class="t-k">투자 방식</span><select id="xp-s" class="t-in">
        <option value="kospi" ${q.strategy === "kospi" ? "selected" : ""}>국내 지수 ETF</option>
        <option value="sp500" ${q.strategy === "sp500" ? "selected" : ""}>미국 S&P500 ETF</option>
        <option value="deposit" ${q.strategy === "deposit" ? "selected" : ""}>예금</option></select><em>연 8.3% · 9% · 3% 가정</em></label></div>
      <div class="t-pro"><button class="t-btn primary" id="xp-go">계산하기</button><span class="t-sub" id="xp-msg"></span></div></section>
    <div id="xp-out">${tSkel(3)}</div></div>`;
  if (!pub) tBindHead(el);
  const read = () => {
    const v = (id) => Number(String(el.querySelector(id).value).replace(/[^\d.]/g, "")) || 0;
    XP.q = { principal: v("#xp-p"), monthly: v("#xp-m"), goal: v("#xp-g"), years: Math.max(1, Math.min(30, Math.round(v("#xp-y")) || 5)), strategy: el.querySelector("#xp-s").value };
  };
  const run = async () => {
    read();
    const out = el.querySelector("#xp-out");
    out.classList.add("xp-loading");
    let r;
    try {
      const res = await fetch(`/api/explore?${new URLSearchParams(XP.q)}`, { credentials: "same-origin" });
      r = await res.json();
      if (!res.ok) throw new Error(r.error || "계산하지 못했어요");
    } catch (e) { out.classList.remove("xp-loading"); el.querySelector("#xp-msg").textContent = e.message; return; }
    el.querySelector("#xp-msg").textContent = "";
    out.classList.remove("xp-loading");
    if (!el.querySelector("#xp-presets").innerHTML) {
      el.querySelector("#xp-presets").innerHTML = (r.presets || []).map((p) => `<button class="t-chip" data-pre="${esc(p.key)}">${esc(p.title)}</button>`).join("");
      el.querySelectorAll("[data-pre]").forEach((b) => b.onclick = () => {
        const p = r.presets.find((x) => x.key === b.dataset.pre);
        XP.q = { principal: p.principal, monthly: p.monthly, goal: p.goal, years: p.years, strategy: "kospi" };
        el.querySelector("#xp-p").value = num(p.principal); el.querySelector("#xp-m").value = num(p.monthly);
        el.querySelector("#xp-g").value = num(p.goal); el.querySelector("#xp-y").value = p.years; el.querySelector("#xp-s").value = "kospi";
        run();
      });
    }
    const cta = pub ? `<div class="xp-cta"><button class="t-btn primary" id="xp-start">이 계획으로 시작하기 — 가입</button><span class="t-sub">가입하면 적립일 알림 · 진행률 · 월간 리포트를 챙겨 드려요</span></div>`
      : `<div class="xp-cta"><button class="t-btn primary" id="xp-start">이 계획을 내 목표로 저장</button><span class="t-sub" id="xp-save-msg">실계좌 적립: 적립일에 알림과 주문표가 와요 (돈은 직접 옮겨요)</span></div>`;
    out.innerHTML = exploreBody(r) + cta;
    out.querySelectorAll("[data-alt]").forEach((b) => b.onclick = () => {
      const a = r.alternatives[+b.dataset.alt];
      el.querySelector("#xp-m").value = num(a.monthly); el.querySelector("#xp-y").value = a.years;
      run();
    });
    out.querySelector("#xp-start").onclick = async () => {
      if (pub) {
        try { localStorage.setItem("qa_pending_plan", JSON.stringify(XP.q)); } catch { /* 저장 못 해도 가입은 됨 */ }
        if (typeof opts.onStart === "function") opts.onStart();
        return;
      }
      await saveExplorePlan(XP.q, out.querySelector("#xp-save-msg"));
    };
  };
  el.querySelector("#xp-go").onclick = run;
  el.querySelectorAll(".xp-in input").forEach((i) => i.onkeydown = (e) => { if (e.key === "Enter") run(); });
  await run();
}

async function saveExplorePlan(q, msgEl) {
  const day = new Date().getDate() > 25 ? 25 : Math.max(1, new Date().getDate());
  try {
    await post("/api/goal", { principal: q.principal, monthly: q.monthly, goal: q.goal, target_years: q.years, strategy: q.strategy === "deposit" ? "kospi" : q.strategy,
      restart: true, dca: { on: q.monthly > 0, day, mode: "live", target: "etf", etf: q.strategy === "sp500" ? "360750" : "069500", amount: q.monthly } });
    if (msgEl) msgEl.innerHTML = '저장했어요 — <a href="#goal">내 목표에서 보기 ›</a>';
    return true;
  } catch (e) { if (msgEl) msgEl.textContent = e.message; return false; }
}

// 가입 전에 계산해 둔 계획 → 로그인 뒤 한 번 물어보고 저장
async function applyPendingPlan() {
  let q = null;
  try { q = JSON.parse(localStorage.getItem("qa_pending_plan") || "null"); localStorage.removeItem("qa_pending_plan"); } catch { q = null; }
  if (!q || !q.goal) return;
  if (confirm(`가입 전에 계산한 계획을 내 목표로 저장할까요?\n${xWon(q.principal)} + 매달 ${xWon(q.monthly)} → ${xWon(q.goal)} (${q.years}년)`)) {
    if (await saveExplorePlan(q)) location.hash = "#goal";
  }
}

TV.explore = (el) => exploreView(el);

// ------------------------------------------------------------ 꾸준함 (내 목표 화면)
function calmCard(c) {
  if (!c) return "";
  return `<section class="t-card t-sec xp-calm"><div class="xp-calm-h"><b>${esc(c.title)}</b><span class="t-sub">${esc(c.as_of || "")} · ${esc(c.source || "")}</span></div>
    <div>${esc(c.body)}</div>${(c.lines || []).length ? `<ul>${c.lines.map((l) => `<li>${esc(l)}</li>`).join("")}</ul>` : ""}<div class="t-sub">${esc(c.note || "")}</div></section>`;
}
function streakDots(st) {
  return `<div class="hb-cal">${(st.calendar || []).map((c) => `<span class="${c.done ? "on" : ""} ${c.current ? "cur" : ""}" title="${esc(c.month)} ${c.done ? "적립함" : "기록 없음"}"><i></i><em>${+c.month.slice(5)}월</em></span>`).join("")}</div>`;
}
async function habitSection(box) {
  if (!box) return;
  let h;
  try { h = await api("/api/habit"); } catch { box.innerHTML = ""; return; }
  if (!box.isConnected || !h.set) { box.innerHTML = ""; return; }
  const st = h.streak, rep = h.report;
  const lv = st.status === "done" ? "good" : st.status === "missed" ? "warn" : "idle";
  const earned = (h.badges || []).filter((b) => b.earned), next = (h.badges || []).find((b) => !b.earned);
  const repB = !rep ? "" : rep.empty ? `<div class="t-sub">${esc(rep.text)}</div>` : `
    <div class="hb-rep"><div><span class="t-k">${+rep.month.slice(5)}월 자산 변화</span><b class="num ${rep.end_total - rep.start_total >= 0 ? "up" : "down"}">${tSigned(rep.end_total - rep.start_total)}</b></div>
      <div><span class="t-k">내가 넣은 돈</span><b class="num">${xWon(rep.added)}</b></div>
      <div><span class="t-k">시장이 보탠 돈</span><b class="num ${rep.market >= 0 ? "up" : "down"}">${tSigned(rep.market)}</b></div>
      <div><span class="t-k">계획 대비</span><b>${esc(rep.position || "-")}</b></div></div>
    ${rep.tone ? `<div class="t-sub">${esc(rep.tone)}</div>` : ""}`;
  box.innerHTML = `${calmCard(h.calm)}
    ${tCard(`꾸준함 <span class="t-tag">${esc(st.text)}</span>`, `
      <div class="hb-top lv-${lv}"><div><b class="t-big num">${st.current}</b><span>개월 연속 적립</span></div>
        <div><b class="num">${st.best}</b><span>최장 연속</span></div><div><b class="num">${st.total}</b><span>적립한 달</span></div>
        <div><b class="num">${(st.missed || []).length}</b><span>빠진 달 (최근 1년)</span></div></div>
      ${streakDots(st)}
      ${st.status === "missed" ? '<div class="t-note warn">이번 달 적립 기록이 없어요 — 늦어도 괜찮아요. 샀으면 위 \'샀어요\'로 기록하면 이어져요.</div>' : ""}`)}
    ${rep ? tCard("지난달 목표 리포트", repB) : ""}
    ${tCard(`배지 <span class="t-sub">${earned.length}/${(h.badges || []).length}</span>`, `<div class="hb-badges">${(h.badges || []).map((b) => `<span class="hb-badge ${b.earned ? "on" : ""}" title="${b.earned ? `${esc(b.at)} 달성` : "아직"}">${esc(b.title)}</span>`).join("")}</div>
      ${next ? `<div class="t-sub">다음 배지: ${esc(next.title)}</div>` : ""}`)}`;
}

// ------------------------------------------------------------ 함께 (친구 초대 · 모임)
TV.together = async (el) => {
  const L = await tLoad(el, "together", "같이 모으기", "t2-tg", () => api("/api/together"));
  if (!L) return;
  const d = L.d;
  if (!d.available) { tPaint(L.root, "같이 모으기", tCard("", tEmpty("회원 로그인 후 쓸 수 있어요", d.why || ""))); return; }
  const r = d.referral || {};
  tPaint(L.root, "같이 모으기", `
    ${tHero({ k: "혼자보다 같이", big: "같이 모으면 덜 멈춰요", sub: "모임에서는 금액이 보이지 않아요 — 각자 목표의 몇 %인지와 연속 적립만 보여요", lv: "idle" })}
    ${tCard(`내 모임 <span class="t-sub">${d.clubs.length}/${d.max_clubs}</span>`, `${d.clubs.length ? `<div class="t-list">${d.clubs.map((c) => `<a class="t-li" href="#club/${esc(c.id)}"><span class="t-co-t"><b>${esc(c.name)}${c.is_owner ? ' <span class="t-tag">모임장</span>' : ""}</b><span>${c.n}명 · 이번 달 적립 ${c.month_total ? `${c.month_done}/${c.month_total}명` : "-"}</span></span><span class="t-chev">${TI.chev}</span></a>`).join("")}</div>`
      : tEmpty("아직 모임이 없어요", "친구·가족·동료와 같은 목표로 모여 보세요")}
      <div class="t-form">${tField("새 모임 이름", '<input id="tg-name" class="t-in" maxlength="30" placeholder="예: 첫 1,000만원 모임">')}${tField("참여 코드 (8자리)", '<input id="tg-code" class="t-in" maxlength="9" placeholder="예: K7P2XQ9M" autocapitalize="characters">')}</div>
      <div class="t-pro"><button class="t-btn primary" id="tg-create">모임 만들기</button><button class="t-btn" id="tg-join">코드로 참여</button><span class="t-sub" id="tg-msg"></span></div>`)}
    ${tCard("친구 초대", r.enabled ? `<div class="t-sub">${r.reward_days ? `친구가 가입하면 <b>나와 친구 모두 프로 ${r.reward_days}일</b> (한 사람 1년까지)` : "초대 링크로 친구가 바로 가입할 수 있어요"} · 지금까지 ${r.joined}명 가입${r.granted_days ? ` · 받은 보상 ${r.granted_days}일` : ""}</div>
      ${r.link ? `<code class="adm-mail" id="tg-link">${esc(r.link)}</code><div class="t-pro"><button class="t-btn" id="tg-copy">링크 복사</button>${navigator.share ? '<button class="t-btn" id="tg-share">공유하기</button>' : ""}<span class="t-sub">${esc(r.expires || "")}까지</span></div>`
        : '<div class="t-pro"><button class="t-btn primary" id="tg-mklink">내 초대 링크 만들기</button></div>'}`
      : `<div class="t-sub">${esc(r.why || "지금은 초대할 수 없어요")}</div>`)}
    <div class="t-foot">모임 기능은 서로 계획을 지키도록 돕는 것이에요 — 투자 종목 추천·매매 권유를 주고받는 곳이 아니에요.</div>`);
  const msg = (t) => { L.root.querySelector("#tg-msg").textContent = t; };
  const act = async (body, go) => { try { const o = await post("/api/together", body); if (go) location.hash = `#club/${o.club.id}`; else TV.together(el); } catch (e) { msg(e.message); } };
  L.root.querySelector("#tg-create").onclick = () => act({ action: "create", name: L.root.querySelector("#tg-name").value }, true);
  L.root.querySelector("#tg-join").onclick = () => act({ action: "join", code: L.root.querySelector("#tg-code").value }, true);
  L.root.querySelector("#tg-mklink")?.addEventListener("click", () => act({ action: "invite_link" }));
  L.root.querySelector("#tg-copy")?.addEventListener("click", (e) => navigator.clipboard?.writeText(r.link).then(() => { e.target.textContent = "복사됨"; }));
  L.root.querySelector("#tg-share")?.addEventListener("click", () => navigator.share({ title: "같이 모아요", text: "목표 적립을 같이 해요", url: r.link }).catch(() => {}));
};

TV.club = async (el) => {
  const id = S.param || "";
  const L = await tLoad(el, "club", "모임", "t2-club", () => api(`/api/club?id=${encodeURIComponent(id)}`));
  if (!L) return;
  const c = L.d, me = c.rows.find((r) => r.me) || {};
  tPaint(L.root, esc(c.name), `
    ${tHero({ k: `${c.n}명 · ${esc(c.created)} 시작`, big: c.month_rate != null ? `이번 달 ${c.month_done}/${c.month_total}명 적립` : "아직 목표를 정한 사람이 없어요",
      sub: `모두의 연속 적립 합계 ${c.streak_sum}개월`, body: c.month_rate != null ? tProg(c.month_rate, c.month_rate >= 0.7 ? "good" : "warn", "이번 달 적립") : "", lv: "idle" })}
    ${tCard("모임 사람들", `<div class="t-list">${c.rows.map((r) => `<div class="t-li"><span class="t-co-t"><b>${esc(r.nick)}${r.me ? " (나)" : ""}${r.owner ? ' <span class="t-tag">모임장</span>' : ""}</b>
      <span>${r.has_goal ? `${r.streak}개월 연속 · 최장 ${r.best} · ${r.this_month ? "이번 달 적립 완료" : "이번 달 아직"}` : "아직 목표를 정하지 않았어요"}</span></span>
      <span class="t-li-r">${r.has_goal ? (r.pct != null ? `<span class="hb-pct"><b class="num">${pctTxt(r.pct, 1)}</b><i style="width:${Math.min(100, r.pct * 100).toFixed(1)}%"></i></span>` : '<span class="t-sub">진행률 비공개</span>') : ""}</span></div>`).join("")}</div>
      <div class="t-sub">${esc(c.note)}</div>`)}
    ${tCard("초대 · 내 설정", `<div class="t-sub">친구에게 참여 코드를 알려 주세요 — 같이 모으기 화면에서 코드로 참여해요</div>
      <div class="xp-code"><b class="num">${esc(c.code)}</b><button class="t-btn sm" id="cl-copy">복사</button></div>
      <div class="t-form">${tField("모임에서 쓸 별명", `<input id="cl-nick" class="t-in" maxlength="16" value="${esc(me.nick || "")}">`)}</div>
      <label class="t2-check"><input type="checkbox" id="cl-hide" ${me.hide_pct ? "checked" : ""}> 내 진행률(%) 숨기기 — 연속 적립만 보여 줘요</label>
      <div class="t-pro"><button class="t-btn" id="cl-save">저장</button><button class="t-btn ghost" id="cl-leave">모임 나가기</button><span class="t-sub" id="cl-msg"></span></div>`)}`);
  L.root.querySelector("#cl-copy").onclick = (e) => navigator.clipboard?.writeText(c.code).then(() => { e.target.textContent = "복사됨"; });
  L.root.querySelector("#cl-save").onclick = async () => {
    try { await post("/api/together", { action: "me", id: c.id, nick: L.root.querySelector("#cl-nick").value, hide_pct: L.root.querySelector("#cl-hide").checked }); TV.club(el); }
    catch (e) { L.root.querySelector("#cl-msg").textContent = e.message; }
  };
  L.root.querySelector("#cl-leave").onclick = async () => {
    if (!confirm("이 모임에서 나갈까요?")) return;
    try { await post("/api/together", { action: "leave", id: c.id }); location.hash = "#together"; } catch (e) { L.root.querySelector("#cl-msg").textContent = e.message; }
  };
};
