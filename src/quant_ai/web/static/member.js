/* v34 여러 사용자 서비스 화면: 가입 · 로그인 · 비밀번호 찾기/재설정 · 이메일 확인 · 약관 재동의 · 내 계정 · 운영 콘솔 · 회원 메뉴.
   서버가 QUANT_SERVICE_MODE=multi 일 때만 켜진다 (1인 모드에서는 아무것도 바꾸지 않음). */
"use strict";

// 회원 메뉴 (운영 기능 없음). 투자 정보 범위가 info 일 때만 'AI 추천'
const MEMBER_NAV = [["dashboard", "home", "홈"], ["watch", "star", "관심종목"], ["goal", "target", "내 목표"], ["pos", "portfolio", "모의투자"],
  ["accounts", "portfolio", "내 계좌"], ["market", "market", "시장"], ["news", "news", "뉴스 · 공시"], ["calendar", "calendar", "일정"],
  ["alerts", "bell", "알림 설정"], ["more", "grid", "더보기"]];
// 회원이 열 수 있는 화면 (나머지는 '운영자 전용' 안내)
const MEMBER_VIEWS = new Set(["dashboard", "watch", "goal", "pos", "accounts", "market", "news", "newslist", "calendar", "alerts", "more",
  "analysis", "n", "d", "map", "compare", "myjournal", "manual", "chat", "account", "profile", "picks", "aitrust", "scorecard", "report",
  "verify", "signup", "reset", "login"]);
const ADVICE_VIEWS = new Set(["picks", "aitrust", "scorecard", "report"]);

const isMulti = () => !!(S.auth && S.auth.multi);
const isMember = () => isMulti() && S.me && S.me.role === "member";
const isAdminUser = () => isMulti() && S.me && S.me.role !== "member";
const adviceOn = () => !isMember() || (S.auth && S.auth.advice === "info");

function memberNav() {
  const nav = MEMBER_NAV.slice();
  if (adviceOn()) nav.splice(6, 0, ["picks", "ai", "AI 추천"]);
  return nav;
}
function memberCanView(v) {
  if (!isMember()) return true;
  if (ADVICE_VIEWS.has(v)) return adviceOn();
  return MEMBER_VIEWS.has(v);
}

// ------------------------------------------------------------ 시작: 서버가 여러 사용자 모드인가 · 로그인했나
async function authBoot() {
  try { S.auth = await (await fetch("/api/auth", { credentials: "same-origin" })).json(); } catch { S.auth = null; return; }
  if (!isMulti()) return;
  document.body.classList.add("multi");
  S.me = S.auth.user || null;
  const h = (location.hash || "").slice(1);
  const [v, tok] = h.split("/");
  if (v === "verify" && tok) { await verifyLink(tok); return authBoot(); }
  if (v === "reset" && tok) { await memberAuth("reset", tok); return authBoot(); }
  if (!S.me && !S.auth.role) { await memberAuth(v === "signup" ? "signup" : "login", v === "signup" ? tok : null); return; }
  if (S.me && !S.me.terms_ok) await termsDialog();
  document.body.classList.toggle("is-member", isMember());
  if (v === "signup" || v === "login") location.hash = "#dashboard";
}

async function verifyLink(tok) {
  history.replaceState(null, "", location.pathname + "#dashboard");
  try {
    const r = await fetch("/api/verify-email", { method: "POST", headers: { "Content-Type": "application/json" }, credentials: "same-origin", body: JSON.stringify({ token: tok }) });
    const j = await r.json();
    authNotice(r.ok ? j.message : j.error, r.ok ? "good" : "bad");
  } catch { authNotice("확인하지 못했어요 — 잠시 뒤 다시 눌러 주세요", "bad"); }
}
function authNotice(msg, lv = "good") { S.authNotice = { msg, lv }; }

// ------------------------------------------------------------ 가입 · 로그인 · 비밀번호 찾기 · 재설정 (전체 화면)
let _maP = null;
function memberAuth(mode = "login", token = null) {
  if (_maP) return _maP;
  _maP = new Promise((resolve) => {
    const a = S.auth || {};
    const brand = esc(a.brand || "Quant AI");
    const d = document.createElement("div");
    d.className = "ma-ov";
    document.body.appendChild(d);
    document.body.classList.add("ma-open");
    const field = (name, label, type = "text", attrs = "") => `<label class="ma-f"><span>${label}</span><input name="${name}" type="${type}" ${attrs}></label>`;
    const pwHint = '<em class="ma-hint">10자 이상 · 흔한 비밀번호·이메일 아이디는 안 돼요</em>';
    const notice = S.authNotice ? `<div class="ma-msg ${S.authNotice.lv}">${esc(S.authNotice.msg)}</div>` : "";
    S.authNotice = null;
    const views = {
      login: () => `<h2>로그인</h2>${notice}
        ${field("email", "이메일", "email", 'autocomplete="username" required')}
        ${field("password", "비밀번호", "password", 'autocomplete="current-password" required')}
        <div class="ma-otp" hidden>${field("otp", "2단계 인증 코드 (OTP 앱 6자리)", "text", 'inputmode="numeric" autocomplete="one-time-code" maxlength="6"')}</div>
        <button class="t-btn primary ma-go" type="submit">로그인</button>
        <div class="ma-links"><a href="#" data-go="forgot">비밀번호를 잊었어요</a>${a.signup !== "closed" ? `<a href="#" data-go="signup">${a.signup === "invite" ? "초대받았어요 — 가입" : "처음이에요 — 가입하기"}</a>` : ""}</div>
        ${a.needs_owner ? '<div class="ma-msg warn">아직 운영자 계정이 없어요 — 서버에서 <code>./run.sh users owner --email 내이메일</code> 을 먼저 실행하세요</div>' : ""}`,
      signup: () => `<h2>가입하기</h2>${notice}
        ${a.signup === "invite" ? field("invite", "초대 코드 (초대 링크로 오면 자동으로 채워져요)", "text", `value="${esc(token || "")}" required`) : ""}
        ${field("email", "이메일", "email", 'autocomplete="email" required')}
        ${field("name", "이름 (화면에 보일 이름 · 선택)", "text", 'maxlength="60" autocomplete="nickname"')}
        ${field("password", "비밀번호", "password", 'autocomplete="new-password" required minlength="10"')}${pwHint}
        ${field("password2", "비밀번호 확인", "password", 'autocomplete="new-password" required')}
        <div class="ma-consent">
          <label class="ma-all"><input type="checkbox" data-all> <b>모두 동의</b></label>
          <label><input type="checkbox" name="terms" required> (필수) <a href="/legal/terms.html" target="_blank" rel="noopener">이용약관</a></label>
          <label><input type="checkbox" name="privacy" required> (필수) <a href="/legal/privacy.html" target="_blank" rel="noopener">개인정보 수집·이용</a></label>
          <label><input type="checkbox" name="risk" required> (필수) 투자 위험 안내를 읽었어요 — 이 서비스는 정보 제공 도구이며, 투자 판단과 손실의 책임은 나에게 있어요</label>
          <label><input type="checkbox" name="age14" required> (필수) 만 14세 이상이에요</label>
          <label><input type="checkbox" name="marketing"> (선택) 새 기능·이벤트 소식 받기</label>
        </div>
        <button class="t-btn primary ma-go" type="submit">가입하기</button>
        <div class="ma-links"><a href="#" data-go="login">이미 계정이 있어요 — 로그인</a></div>`,
      forgot: () => `<h2>비밀번호 찾기</h2>${notice}<p class="ma-sub">가입한 이메일로 재설정 링크를 보내 드려요 (1시간 동안 유효).</p>
        ${field("email", "이메일", "email", 'autocomplete="email" required')}
        <button class="t-btn primary ma-go" type="submit">재설정 링크 받기</button>
        <div class="ma-links"><a href="#" data-go="login">로그인으로</a></div>`,
      reset: () => `<h2>새 비밀번호 정하기</h2>${notice}
        ${field("password", "새 비밀번호", "password", 'autocomplete="new-password" required minlength="10"')}${pwHint}
        ${field("password2", "새 비밀번호 확인", "password", 'autocomplete="new-password" required')}
        <button class="t-btn primary ma-go" type="submit">바꾸기</button>`,
    };
    const show = (m, msg) => {
      mode = m;
      d.innerHTML = `<form class="ma-card" novalidate><div class="ma-brand">${typeof ICONS === "object" ? ICONS.logo || "" : ""}<b>${brand}</b><span>목표를 정하고, 꾸준히, 기록으로 확인하는 투자</span></div>
        ${a.notice ? `<div class="ma-msg info">${esc(a.notice)}</div>` : ""}${views[m]()}<div class="ma-err" role="alert">${msg ? esc(msg) : ""}</div>
        <div class="ma-foot">정보 제공 서비스예요 · 투자 권유나 수익 보장이 아니에요${a.support ? ` · 문의 ${esc(a.support)}` : ""}</div></form>`;
      const f = d.querySelector("form");
      d.querySelectorAll("[data-go]").forEach((x) => x.onclick = (e) => { e.preventDefault(); show(x.dataset.go); });
      const all = f.querySelector("[data-all]");
      if (all) all.onchange = () => f.querySelectorAll(".ma-consent input[name]").forEach((c) => { c.checked = all.checked; });
      if (m === "signup" && token) {
        fetch(`/api/invite?token=${encodeURIComponent(token)}`).then((r) => r.json()).then((j) => {
          if (j.ok && j.email) { f.email.value = j.email; f.email.readOnly = true; }
          if (!j.ok) f.querySelector(".ma-err").textContent = "초대 링크가 없거나 만료됐어요 — 초대한 사람에게 새 링크를 받아 주세요";
        }).catch(() => {});
      }
      f.onsubmit = (e) => { e.preventDefault(); submit(f); };
      setTimeout(() => (f.querySelector("input:not([type=checkbox]):not([readonly])") || f.querySelector("input"))?.focus(), 30);
    };
    const call = async (path, body) => {
      const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, credentials: "same-origin", body: JSON.stringify(body) });
      let j = {};
      try { j = await r.json(); } catch { /* 빈 응답 */ }
      return { ok: r.ok, j };
    };
    // 로그인되면 새로 연다 (이 페이지의 대기 중인 요청은 이어 가지 않는다 — 회원 문맥으로 처음부터)
    const done = () => { location.hash = "#dashboard"; location.reload(); };
    const submit = async (f) => {
      const err = f.querySelector(".ma-err");
      const btn = f.querySelector(".ma-go");
      err.textContent = "";
      const v = (n) => (f[n] ? f[n].value : "");
      if ((mode === "signup" || mode === "reset") && v("password") !== v("password2")) { err.textContent = "두 비밀번호가 달라요"; return; }
      if (mode === "signup" && !["terms", "privacy", "risk", "age14"].every((n) => f[n].checked)) { err.textContent = "필수 항목에 모두 동의해 주세요"; return; }
      btn.disabled = true;
      try {
        if (mode === "login") {
          const { ok, j } = await call("/api/login", { email: v("email"), password: v("password"), otp: v("otp") });
          if (ok) return done();
          if (j.code === "otp_required") { f.querySelector(".ma-otp").hidden = false; f.otp.focus(); }
          err.textContent = j.error || "로그인하지 못했어요";
        } else if (mode === "signup") {
          const { ok, j } = await call("/api/signup", { email: v("email"), password: v("password"), name: v("name"), invite: v("invite") || token || "",
            consent: { terms: f.terms.checked, privacy: f.privacy.checked, risk: f.risk.checked, age14: f.age14.checked, marketing: f.marketing.checked } });
          if (ok && j.user) return done();
          if (ok && j.verify) { authNotice(j.message, "good"); show("login"); return; }
          err.textContent = j.error || "가입하지 못했어요";
        } else if (mode === "forgot") {
          const { j } = await call("/api/password/forgot", { email: v("email") });
          authNotice(j.message || j.error || "", "good"); show("login");
        } else if (mode === "reset") {
          const { ok, j } = await call("/api/password/reset", { token, password: v("password") });
          if (ok) { history.replaceState(null, "", location.pathname + "#login"); authNotice(j.message, "good"); show("login"); return; }
          err.textContent = j.error || "바꾸지 못했어요";
        }
      } catch { err.textContent = "서버에 연결하지 못했어요 — 잠시 뒤 다시 해 주세요"; }
      finally { btn.disabled = false; }
    };
    show(mode);
  });
  return _maP;
}

// 약관이 바뀌었을 때 다시 동의 받기
function termsDialog() {
  return new Promise((resolve) => {
    const d = document.createElement("div");
    d.className = "ma-ov";
    d.innerHTML = `<form class="ma-card"><h2>약관이 바뀌었어요</h2><p class="ma-sub">계속 쓰시려면 바뀐 <a href="/legal/terms.html" target="_blank" rel="noopener">이용약관</a>과
      <a href="/legal/privacy.html" target="_blank" rel="noopener">개인정보 처리방침</a>을 확인하고 동의해 주세요.</p>
      <label class="ma-chk"><input type="checkbox" name="ok" required> 바뀐 약관과 개인정보 처리방침에 동의해요</label>
      <button class="t-btn primary ma-go" type="submit">동의하고 계속</button><div class="ma-links"><a href="#" id="tm-out">동의하지 않고 로그아웃</a></div><div class="ma-err"></div></form>`;
    document.body.appendChild(d);
    const f = d.querySelector("form");
    d.querySelector("#tm-out").onclick = (e) => { e.preventDefault(); logout(); };
    f.onsubmit = async (e) => {
      e.preventDefault();
      if (!f.ok.checked) { f.querySelector(".ma-err").textContent = "동의가 필요해요"; return; }
      await post("/api/me", { accept_terms: true });
      S.me.terms_ok = true; d.remove(); resolve(true);
    };
  });
}

// ------------------------------------------------------------ 회원 화면 틀 (운영 정보 숨김)
function memberChrome() {
  if (!isMulti()) return;
  const u = S.me || {};
  const av = $("#avatar");
  if (av) {
    av.textContent = (u.name || u.email || "?").slice(0, 1).toUpperCase();
    av.title = `${u.email || ""} · ${u.role === "member" ? (u.plan === "pro" ? "프로" : "무료") : "운영자"} — 내 계정`;
    av.style.cursor = "pointer";
    av.onclick = () => { location.hash = "#account"; };
  }
  if (!isMember()) return;
  for (const id of ["#kill-btn", "#mode-card", "#balance-card", "#demo-badge"]) { const el = $(id); if (el) el.hidden = true; }
  const sp = $("#sys-status");
  if (sp) { sp.innerHTML = `<span class="dot ok"></span>${esc(u.plan === "pro" ? "프로" : "무료")} 요금제`; sp.onclick = () => { location.hash = "#account"; }; sp.style.cursor = "pointer"; }
  const sl = $("#sys-last");
  if (sl) sl.textContent = (S.auth && S.auth.brand) || "Quant AI";
}
function memberDenied(el, v) {
  const advice = ADVICE_VIEWS.has(v);
  el.innerHTML = `<div class="ts">${tHead(advice ? "AI 판단" : "운영자 전용")}${tSec("", tEmpty(advice ? "이 서비스는 AI 매수·매도 판단을 제공하지 않아요" : "운영자만 쓸 수 있는 화면이에요",
    advice ? "시세 · 뉴스 · 일정 · 목표 계획 · 모의투자 도구는 그대로 쓸 수 있어요" : "회원 메뉴에서 필요한 기능을 찾아 보세요",
    '<a class="t-btn primary" href="#dashboard">홈으로</a>'), "", "t-card")}</div>`;
  tBindHead(el);
}
function memberMore(el) {
  const tile = (h, ic, t, d) => `<a class="t-tile" href="${h}"><span class="t-tile-ic">${ICONS[ic] || ic}</span><b>${t}</b><span>${d}</span></a>`;
  const theme = document.documentElement.dataset.theme;
  el.innerHTML = `<div class="ts ts-more"><h1 class="t-h1">더보기</h1>
    <div class="t-tiles">
      ${tile("#goal", "target", "내 목표", "적립 계획 · 진행률")}
      ${tile("#manual", "orders", "모의 주문", "가상 1,000만원으로 연습")}
      ${tile("#accounts", "portfolio", "내 계좌", "직접 입력 · 세금 · 배당")}
      ${tile("#myjournal", "journal", "투자 일지", "내 판단 기록 · 채점")}
      ${tile("#map", "data", "증시 지도", "업종별 오늘 움직임")}
      ${tile("#compare", "compare", "종목 비교", "두세 종목 나란히")}
      ${tile("#calendar", "calendar", "일정", "실적 · FOMC · CPI D-Day")}
      ${tile("#alerts", "bell", "알림 설정", "가격 · 뉴스 · 적립일")}
      ${tile("#chat", TI.news, "AI 에게 묻기", "종목 · 시장 · 용어")}
      ${adviceOn() ? tile("#picks", "ai", "AI 추천", "모두에게 같은 정보 · 참고용") + tile("#aitrust", "shield", "AI 신뢰 센터", "AI 성적 공개") : ""}
      ${tile("#profile", "settings", "투자 성향", "나에게 맞는 정보")}
      ${tile("#account", "settings", "내 계정", "보안 · 요금제 · 내 데이터")}
    </div>
    ${tSec("화면", `<div class="t-list"><div class="t-li"><span class="t-co-t"><b>테마</b><span>기기 설정을 따르다가 직접 바꾸면 기억해요</span></span><span class="t-li-r">${tChips("tm-th", [["light", "라이트"], ["dark", "다크"]], theme)}</span></div></div>`, "", "t-card")}
    <div class="t-foot">시세·지수는 지연될 수 있어요 · 이 서비스는 정보 제공 도구이며 투자 판단과 결과의 책임은 본인에게 있어요 ·
      <a href="/legal/terms.html" target="_blank" rel="noopener">이용약관</a> · <a href="/legal/privacy.html" target="_blank" rel="noopener"><b>개인정보 처리방침</b></a></div></div>`;
  tBind(el, "tm-th", (k) => { if (k !== document.documentElement.dataset.theme) $("#theme-btn").click(); });
}

// ------------------------------------------------------------ 내 계정
TV.account = async (el) => {
  if (!isMulti()) { el.innerHTML = `<div class="ts">${tHead("내 계정")}${tSec("", tEmpty("혼자 쓰는 모드예요", "여러 사용자 모드(QUANT_SERVICE_MODE=multi)에서 회원 계정이 생겨요"), "", "t-card")}</div>`; tBindHead(el); return; }
  const [me, ss] = await Promise.all([api("/api/me"), api("/api/me/sessions").catch(() => ({ sessions: [] }))]);
  const u = me.user || {};
  const us = me.usage || {};
  const lim = us.limits || {};
  const plans = (me.policy || {}).plans || {};
  const useRow = (k, label) => lim[k] != null ? `<div class="t-li"><span class="t-co-t"><b>${label}</b><span>오늘 ${num(us.today?.[k] || 0)} / ${num(lim[k])}번</span></span><span class="t-li-r" style="min-width:120px">${tProg((us.today?.[k] || 0) / lim[k])}</span></div>` : "";
  const planCard = (k) => { const p = plans[k] || {}; const cur = u.plan === k; return `<div class="ac-plan ${cur ? "on" : ""}"><b>${esc(p.title || k)}</b><span class="num">${p.price ? `월 ${num(p.price)}원` : "무료"}</span>
    <ul><li>관심종목 ${num(p.watch)}개</li><li>가격 알림 ${num(p.alert_rules)}개</li><li>내 계좌 ${num(p.accounts)}개</li><li>AI 채팅 하루 ${num(p.chat)}번</li><li>AI 설명 하루 ${num(p.ai_explain)}번</li><li>모의 주문 하루 ${num(p.ticket)}번</li></ul>
    ${cur ? '<em>지금 쓰는 요금제</em>' : '<em>운영자에게 문의</em>'}</div>`; };
  el.innerHTML = `<div class="ts">${tHead("내 계정")}
    ${tHero({ k: u.role === "member" ? "회원" : u.owner ? "소유자 · 운영자" : "운영자", big: esc(u.name || u.email || ""), sub: `${esc(u.email || "")} · ${u.verified ? "이메일 확인됨" : "이메일 확인 전"} · 가입 ${esc((u.created_at || "").slice(0, 10))}`, lv: "idle" })}
    ${!u.verified ? tCard("이메일 확인", `<div class="t-note warn">받은 메일의 링크를 누르면 확인돼요 — 비밀번호를 잊었을 때 이 이메일로 찾아요</div><div class="t-pro"><button class="t-btn" id="ac-resend">확인 메일 다시 받기</button><span class="t-sub" id="ac-resend-msg"></span></div>`) : ""}
    ${tCard("프로필", `<div class="t-form">${tField("이름", `<input class="t-in" id="ac-name" maxlength="60" value="${esc(u.name || "")}">`)}</div>
      <div class="t-pro"><label class="t-sub"><input type="checkbox" id="ac-mkt" ${me.user?.marketing ? "checked" : ""}> 새 기능·이벤트 소식 받기 (선택)</label><button class="t-btn" id="ac-save">저장</button><span class="t-sub" id="ac-save-msg"></span></div>`)}
    ${tCard("비밀번호", `<div class="t-form">${tField("지금 비밀번호", '<input class="t-in" id="ac-old" type="password" autocomplete="current-password">')}
      ${tField("새 비밀번호", '<input class="t-in" id="ac-new" type="password" autocomplete="new-password" minlength="10">', "10자 이상")}
      ${tField("새 비밀번호 확인", '<input class="t-in" id="ac-new2" type="password" autocomplete="new-password">')}</div>
      <div class="t-pro"><button class="t-btn" id="ac-pw">바꾸기</button><span class="t-sub" id="ac-pw-msg">바꾸면 다른 기기는 로그아웃돼요</span></div>`)}
    ${tCard("2단계 인증", u.mfa ? `<div class="t-note">켜져 있어요 — 로그인할 때 OTP 앱의 6자리를 함께 넣어요</div>
        <div class="t-form">${tField("비밀번호", '<input class="t-in" id="mfa-pw" type="password" autocomplete="current-password">')}${tField("지금 6자리", '<input class="t-in" id="mfa-code" inputmode="numeric" maxlength="6">')}</div>
        <div class="t-pro"><button class="t-btn ghost" id="mfa-off">끄기</button><span class="t-sub" id="mfa-msg"></span></div>`
      : `<div class="t-sub">비밀번호가 새도 내 휴대폰 없이는 로그인할 수 없게 — 켜 두기를 권해요</div><div id="mfa-box"></div><div class="t-pro"><button class="t-btn primary" id="mfa-begin">켜기</button><span class="t-sub" id="mfa-msg"></span></div>`)}
    ${u.role === "member" ? tCard(`요금제 · 오늘 사용량`, `<div class="ac-plans">${["free", "pro"].map(planCard).join("")}</div><div class="t-list">${useRow("chat", "AI 채팅")}${useRow("ai_explain", "AI 설명")}${useRow("ticket", "모의 주문")}${useRow("journal", "투자 일지")}</div>
      <div class="t-sub">결제는 아직 연결되지 않았어요 — 프로가 필요하면 운영자에게 문의해 주세요</div>`) : ""}
    ${tCard("로그인한 기기", `<div class="t-list">${(ss.sessions || []).map((x) => `<div class="t-li"><span class="t-co-t"><b>${esc(x.agent || "알 수 없는 기기")}${x.current ? " · 지금 이 기기" : ""}</b><span>${esc(x.ip || "")} · 마지막 ${esc(tAgo(x.last_seen_at || x.created_at))}</span></span></div>`).join("") || tEmpty("기록 없음")}</div>
      <div class="t-pro"><button class="t-btn" id="ac-others">다른 기기 모두 로그아웃</button><button class="t-btn ghost" id="ac-logout">이 기기 로그아웃</button><span class="t-sub" id="ac-others-msg"></span></div>`)}
    ${tCard("내 데이터", `<div class="t-sub">목표 계획 · 계좌 · 관심종목 · 알림 · 투자 일지 · 모의 주문을 파일 하나로 받아요 (개인정보 열람·이동권)${me.encryption ? " · 서버에는 암호화해서 보관돼요" : ""}</div>
      <div class="t-pro"><a class="t-btn" href="/api/me/export" download>내 데이터 내려받기 (JSON)</a></div>`)}
    ${u.owner ? "" : tCard("탈퇴", `<div class="t-note warn">탈퇴하면 내 데이터(목표·계좌·관심종목·알림·일지·모의 장부)가 바로 지워지고 되돌릴 수 없어요.</div>
      <div class="t-form">${tField("확인을 위해 이메일 입력", `<input class="t-in" id="del-email" placeholder="${esc(u.email || "")}">`)}${tField("비밀번호", '<input class="t-in" id="del-pw" type="password" autocomplete="current-password">')}</div>
      <div class="t-pro"><button class="t-btn danger" id="ac-del">탈퇴하기</button><span class="t-sub" id="ac-del-msg"></span></div>`)}
    <div class="t-foot"><a href="/legal/terms.html" target="_blank" rel="noopener">이용약관</a> · <a href="/legal/privacy.html" target="_blank" rel="noopener"><b>개인정보 처리방침</b></a>${S.auth?.support ? ` · 문의 ${esc(S.auth.support)}` : ""}</div></div>`;
  tBindHead(el);
  const say = (id, msg, bad) => { const m = el.querySelector(id); if (m) { m.textContent = msg; m.classList.toggle("bad-t", !!bad); } };
  const run = async (id, fn) => { try { await fn(); } catch (e) { say(id, e.message, true); } };
  el.querySelector("#ac-resend")?.addEventListener("click", () => run("#ac-resend-msg", async () => { const r = await post("/api/me/verify-resend", {}); say("#ac-resend-msg", r.sent ? "보냈어요 — 메일함(스팸함)을 확인해 주세요" : "보내지 못했어요 — 운영자에게 문의해 주세요"); }));
  el.querySelector("#ac-save").onclick = () => run("#ac-save-msg", async () => { await post("/api/me", { name: el.querySelector("#ac-name").value, marketing: el.querySelector("#ac-mkt").checked }); say("#ac-save-msg", "저장했어요"); });
  el.querySelector("#ac-pw").onclick = () => run("#ac-pw-msg", async () => {
    const n = el.querySelector("#ac-new").value;
    if (n !== el.querySelector("#ac-new2").value) { say("#ac-pw-msg", "두 새 비밀번호가 달라요", true); return; }
    const r = await post("/api/me/password", { old: el.querySelector("#ac-old").value, new: n });
    say("#ac-pw-msg", r.message);
  });
  el.querySelector("#mfa-begin")?.addEventListener("click", () => run("#mfa-msg", async () => {
    const r = await post("/api/me/mfa", { action: "begin" });
    el.querySelector("#mfa-box").innerHTML = `<div class="t-note">① OTP 앱에서 '키 직접 입력' → 아래 키를 넣으세요<br><code class="ac-key">${esc(r.secret.replace(/(.{4})/g, "$1 ").trim())}</code><br>② 앱에 나온 6자리를 입력하세요</div>
      <div class="t-form">${tField("6자리", '<input class="t-in" id="mfa-code" inputmode="numeric" maxlength="6" autocomplete="one-time-code">')}</div><div class="t-pro"><button class="t-btn primary" id="mfa-on">확인하고 켜기</button></div>`;
    el.querySelector("#mfa-begin").hidden = true;
    el.querySelector("#mfa-on").onclick = () => run("#mfa-msg", async () => { await post("/api/me/mfa", { action: "enable", code: el.querySelector("#mfa-code").value }); render(); });
  }));
  el.querySelector("#mfa-off")?.addEventListener("click", () => run("#mfa-msg", async () => {
    await post("/api/me/mfa", { action: "disable", password: el.querySelector("#mfa-pw").value, code: el.querySelector("#mfa-code").value }); render();
  }));
  el.querySelector("#ac-others").onclick = () => run("#ac-others-msg", async () => { const r = await post("/api/me/logout-others", {}); say("#ac-others-msg", `${r.logged_out}곳 로그아웃`); });
  el.querySelector("#ac-logout").onclick = () => logout();
  el.querySelector("#ac-del")?.addEventListener("click", () => run("#ac-del-msg", async () => {
    if (!confirm("정말 탈퇴할까요? 모든 데이터가 지워지고 되돌릴 수 없어요.")) return;
    await post("/api/me/delete", { confirm: el.querySelector("#del-email").value, password: el.querySelector("#del-pw").value });
    alert("탈퇴했어요. 그동안 써 주셔서 고마워요."); location.hash = ""; location.reload();
  }));
};

// ------------------------------------------------------------ 운영 콘솔 (소유자·운영자)
TV.admin = async (el) => {
  if (!isMulti() || isMember()) { memberDenied(el, "admin"); return; }
  const q = S.adminQ || "";
  const [ov, us] = await Promise.all([api("/api/admin/overview"), api(`/api/admin/users?q=${encodeURIComponent(q)}`)]);
  const st = ov.stats || {}, pol = ov.policy || {};
  const maxN = Math.max(1, ...(st.signups_14d || []).map((x) => x.n));
  const bars = `<div class="adm-bars">${(st.signups_14d || []).map((x) => `<span title="${esc(x.date)} ${x.n}명"><i style="height:${Math.round(x.n / maxN * 100)}%"></i><em>${esc(x.date.slice(8))}</em></span>`).join("")}</div>`;
  const warn = [!ov.encryption && "개인 데이터 암호화 키(QUANT_DATA_KEY)가 없어요 — 서비스 전에 꼭 넣으세요",
    !ov.smtp && "메일(SMTP)이 없어 확인·재설정 메일이 아래 '보낼 편지함'에만 쌓여요",
    !ov.public_url && "QUANT_PUBLIC_URL(서비스 주소)이 없어 메일 링크가 접속한 주소로 만들어져요"].filter(Boolean);
  const userRow = (u) => `<tr data-id="${u.id}"><td><b>${esc(u.email)}</b><div class="t-sub">${esc(u.name || "")} · #${u.id}${u.mfa ? " · 2단계" : ""}${u.verified ? "" : " · 이메일 미확인"}${u.locked ? " · 잠김" : ""}</div></td>
    <td>${u.owner ? "소유자" : `<select class="t-in adm-role" ${u.owner ? "disabled" : ""}><option value="member" ${u.role === "member" ? "selected" : ""}>회원</option><option value="admin" ${u.role === "admin" ? "selected" : ""}>운영자</option></select>`}</td>
    <td><select class="t-in adm-plan"><option value="free" ${u.plan === "free" ? "selected" : ""}>무료</option><option value="pro" ${u.plan === "pro" ? "selected" : ""}>프로</option></select>${u.plan_until ? `<div class="t-sub">~${esc(u.plan_until.slice(0, 10))}</div>` : ""}</td>
    <td>${u.owner ? "" : `<button class="t-btn sm adm-st">${u.status === "active" ? "정지" : "다시 사용"}</button>`}${u.locked ? '<button class="t-btn sm adm-unlock">잠금 풀기</button>' : ""}</td>
    <td class="t-sub">${esc(tAgo(u.last_seen_at) || "-")}</td><td>${u.owner ? "" : '<button class="t-btn sm ghost adm-del">삭제</button>'}</td></tr>`;
  el.innerHTML = `<div class="ts">${tHead("운영 콘솔")}
    ${warn.length ? `<div class="t-note warn">${warn.map(esc).join("<br>")}</div>` : ""}
    ${tCard("회원", `${tKV([["전체", num(st.total)], ["오늘 접속", num(st.active_1d)], ["7일 접속", num(st.active_7d)], ["프로", num(st.plans?.pro || 0)], ["2단계 인증", num(st.mfa)]])}
      <div class="t-sub" style="margin-top:8px">최근 14일 가입</div>${bars}`)}
    ${tCard("서비스 정책", `<div class="t-list">
      <div class="t-li"><span class="t-co-t"><b>가입 방식</b><span>open = 누구나 · invite = 초대 링크로만 · closed = 막음</span></span><span class="t-li-r">${tChips("adm-signup", [["open", "누구나"], ["invite", "초대"], ["closed", "막음"]], pol.signup)}</span></div>
      <div class="t-li"><span class="t-co-t"><b>투자 정보 범위</b><span>none = AI 매수·매도 판단을 회원에게 안 보여 줌 (법률 검토 전 기본) · info = 모두에게 같은 AI 분석을 정보로 (유사투자자문업 신고 등 확인 후)</span></span><span class="t-li-r">${tChips("adm-advice", [["none", "보여 주지 않음"], ["info", "정보로 보여 줌"]], pol.advice)}</span></div>
      <div class="t-li"><span class="t-co-t"><b>이메일 확인 필수</b><span>메일(SMTP)이 있을 때 켜는 것을 권해요</span></span><span class="t-li-r">${tChips("adm-verify", [["1", "필수"], ["0", "선택"]], pol.require_verify ? "1" : "0")}</span></div></div>
      <div class="t-form">${tField("서비스 이름", `<input class="t-in" id="adm-brand" maxlength="40" value="${esc(pol.brand || "")}">`)}${tField("문의 이메일", `<input class="t-in" id="adm-support" maxlength="120" value="${esc(pol.support || "")}">`)}${tField("로그인 화면 공지", `<input class="t-in" id="adm-notice" maxlength="300" value="${esc(pol.notice || "")}">`)}</div>
      <div class="t-pro"><button class="t-btn" id="adm-pol-save">저장</button><span class="t-sub" id="adm-pol-msg"></span></div>`)}
    ${tCard("초대", `<div class="t-form">${tField("이메일 (비우면 여러 명이 쓰는 링크)", '<input class="t-in" id="inv-email" type="email" placeholder="friend@example.com">')}
      ${tField("요금제", '<select class="t-in" id="inv-plan"><option value="free">무료</option><option value="pro">프로</option></select>')}
      ${tField("사람 수 (링크)", '<input class="t-in" id="inv-uses" inputmode="numeric" value="10">')}${tField("유효 일수", '<input class="t-in" id="inv-days" inputmode="numeric" value="7">')}</div>
      <div class="t-pro"><button class="t-btn primary" id="inv-go">초대 링크 만들기</button></div><div id="inv-out"></div>`)}
    ${tCard(`회원 목록 <span class="t-sub">${num(us.total)}명</span>`, `<div class="t2-filter"><input class="t-in" id="adm-q" placeholder="이메일로 찾기" value="${esc(q)}"></div>
      <div class="t2-scroll"><table class="t-table adm-users"><thead><tr><th>회원</th><th>권한</th><th>요금제</th><th>상태</th><th>최근</th><th></th></tr></thead><tbody>${(us.rows || []).map(userRow).join("")}</tbody></table></div>
      <div class="t-sub" id="adm-msg"></div>`)}
    ${tCard(`보낼 편지함 <span class="t-sub">${ov.smtp ? "메일 서버로 보냄 (본문은 남기지 않음)" : "메일 서버 없음 — 직접 전달"}</span>`, `<div class="t-list">${(ov.outbox?.items || []).slice(0, 20).map((m) => `<div class="t-li"><span class="t-co-t"><b>${esc(m.subject)} → ${esc(m.to)}</b><span>${esc(tAgo(m.at))} · ${m.sent ? "보냄" : m.error ? `실패: ${esc(m.error)}` : "보내지 않음"}</span>${m.body ? `<code class="adm-mail">${esc(m.body)}</code>` : ""}</span></div>`).join("") || tEmpty("아직 없음")}</div>`)}
    <div class="t-foot">운영 기능(데이터 수집 · AI · 자동매매 · 서버)은 왼쪽 메뉴의 '더 많은 기능'에 그대로 있어요</div></div>`;
  tBindHead(el);
  const msg = (t, bad) => { const m = el.querySelector("#adm-msg"); m.textContent = t; m.classList.toggle("bad-t", !!bad); };
  const act = async (body) => { try { await post("/api/admin/user", body); render(); } catch (e) { msg(e.message, true); } };
  el.querySelectorAll(".adm-users tr[data-id]").forEach((tr) => {
    const id = +tr.dataset.id;
    tr.querySelector(".adm-role")?.addEventListener("change", (e) => act({ id, role: e.target.value }));
    tr.querySelector(".adm-plan")?.addEventListener("change", (e) => { const days = e.target.value === "pro" ? prompt("프로 기간(일) — 비우면 기간 없음", "30") : ""; act({ id, plan: e.target.value, days: days || "" }); });
    tr.querySelector(".adm-st")?.addEventListener("click", (e) => act({ id, status: e.target.textContent === "정지" ? "disabled" : "active" }));
    tr.querySelector(".adm-unlock")?.addEventListener("click", () => act({ id, unlock: true }));
    tr.querySelector(".adm-del")?.addEventListener("click", () => { if (confirm("이 회원과 모든 데이터를 지울까요? 되돌릴 수 없어요.")) act({ id, delete: true }); });
  });
  const pol2 = {};
  tBind(el, "adm-signup", (k) => { pol2.signup = k; el.querySelectorAll("#adm-signup button").forEach((b) => b.classList.toggle("on", b.dataset.k === k)); });
  tBind(el, "adm-advice", (k) => {
    if (k === "info" && !confirm("회원에게 AI 매수·매도 판단을 '정보'로 보여 줍니다.\n유사투자자문업 신고 등 법률 검토를 마쳤나요?")) return;
    pol2.advice = k; el.querySelectorAll("#adm-advice button").forEach((b) => b.classList.toggle("on", b.dataset.k === k));
  });
  tBind(el, "adm-verify", (k) => { pol2.require_verify = k === "1"; el.querySelectorAll("#adm-verify button").forEach((b) => b.classList.toggle("on", b.dataset.k === k)); });
  el.querySelector("#adm-pol-save").onclick = async () => {
    try {
      await post("/api/admin/policy", { ...pol2, brand: el.querySelector("#adm-brand").value, support: el.querySelector("#adm-support").value, notice: el.querySelector("#adm-notice").value });
      el.querySelector("#adm-pol-msg").textContent = "저장했어요 — 회원 화면에 바로 반영돼요";
    } catch (e) { el.querySelector("#adm-pol-msg").textContent = e.message; }
  };
  el.querySelector("#inv-go").onclick = async () => {
    try {
      const email = el.querySelector("#inv-email").value.trim();
      const r = await post("/api/admin/invite", { email, plan: el.querySelector("#inv-plan").value, max_uses: email ? 1 : +el.querySelector("#inv-uses").value || 1, days: +el.querySelector("#inv-days").value || 7 });
      el.querySelector("#inv-out").innerHTML = `<div class="t-note">${r.emailed ? "메일로 보냈어요 · " : ""}${r.max_uses}명 · ${r.expires_days}일 동안 유효<br><code class="adm-mail">${esc(r.link)}</code>
        <button class="t-btn sm" id="inv-copy">복사</button></div>`;
      el.querySelector("#inv-copy").onclick = () => navigator.clipboard?.writeText(r.link).then(() => { el.querySelector("#inv-copy").textContent = "복사됨"; });
    } catch (e) { el.querySelector("#inv-out").innerHTML = `<div class="t-note warn">${esc(e.message)}</div>`; }
  };
  const qi = el.querySelector("#adm-q");
  qi.onkeydown = (e) => { if (e.key === "Enter") { S.adminQ = qi.value.trim(); render(); } };
};
