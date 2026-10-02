/* 살아 있는 화면: 토스트 알림 · 알림센터 · 데스크톱 알림 · 소리 · 실시간 가격 깜빡임 · 애니메이션 · 24H 관제실 */
"use strict";

// ================================================================= 설정 (이 브라우저에만 저장)
const ALERT_KINDS = {
  price: ["급등·급락", "📈"], signal: ["AI 신호", "🤖"], event: ["이벤트 재분석", "⚡"], disclosure: ["공시", "📑"],
  news: ["중요 뉴스", "📰"], earnings: ["실적 발표", "📊"], result: ["예측 채점", "🎯"], guardian: ["자동 감시", "🛡️"],
  ladder: ["승격·강등", "🪜"], market: ["시장 상태", "🌐"], job: ["작업 실패", "⚠️"],
};
const PREF_DEFAULT = { toast: Object.fromEntries(Object.keys(ALERT_KINDS).map((k) => [k, true])), desktop: false, sound: false };
function alertPrefs() {
  try { const p = JSON.parse(safeGet("qa_alert_prefs") || "{}"); return { ...PREF_DEFAULT, ...p, toast: { ...PREF_DEFAULT.toast, ...(p.toast || {}) } }; } catch { return PREF_DEFAULT; }
}
function saveAlertPrefs(p) { safeSet("qa_alert_prefs", JSON.stringify(p)); }
const reduceMotion = () => window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// ================================================================= 토스트
function toastBox() {
  let box = $("#toasts");
  if (!box) { box = document.createElement("div"); box.id = "toasts"; box.setAttribute("aria-live", "polite"); document.body.appendChild(box); }
  return box;
}
function toast({ title, body = "", level = "info", kind = "", link = "", dir = "", ms = 7000 }) {
  const box = toastBox();
  while (box.children.length >= 4) box.firstElementChild.remove();
  const t = document.createElement("div");
  const ic = kind === "price" && dir === "down" ? "📉" : (ALERT_KINDS[kind] || [])[1] || (level === "bad" ? "⛔" : level === "warn" ? "⚠️" : level === "good" ? "✅" : "🔔");
  t.className = `toast lv-${level} ${dir ? "dir-" + dir : ""}`;
  t.innerHTML = `<div class="toast-ic">${ic}</div><div class="toast-b"><b>${esc(title)}</b>${body ? `<div class="small">${esc(body)}</div>` : ""}</div>
    <button class="toast-x" aria-label="닫기">×</button><div class="toast-bar" style="animation-duration:${ms}ms"></div>`;
  const close = () => { t.classList.add("out"); setTimeout(() => t.remove(), 280); };
  let timer = setTimeout(close, ms);
  t.onmouseenter = () => { clearTimeout(timer); t.classList.add("hold"); };
  t.onmouseleave = () => { t.classList.remove("hold"); timer = setTimeout(close, 2500); };
  t.querySelector(".toast-x").onclick = (e) => { e.stopPropagation(); close(); };
  t.onclick = () => { if (link) location.hash = link; close(); };
  box.appendChild(t);
  return t;
}
window.toast = toast;

let _audio;
function beep(level) {
  try {
    _audio = _audio || new (window.AudioContext || window.webkitAudioContext)();
    const o = _audio.createOscillator(), g = _audio.createGain();
    o.type = "sine"; o.frequency.value = level === "bad" ? 330 : level === "warn" ? 520 : 780;
    g.gain.setValueAtTime(0.0001, _audio.currentTime);
    g.gain.exponentialRampToValueAtTime(0.12, _audio.currentTime + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, _audio.currentTime + 0.35);
    o.connect(g); g.connect(_audio.destination); o.start(); o.stop(_audio.currentTime + 0.36);
  } catch { /* 소리 없음 */ }
}

// ================================================================= 알림 폴링 · 알림센터
const AL = { last: 0, items: [], seen: 0, started: false };  // seen 은 initLive 에서 (app.js 의 safeGet 준비 후)
function unread() { return AL.items.filter((a) => a.id > AL.seen).length; }
function updateBell() {
  const w = $(".bell-wrap");
  if (!w) return;
  let c = w.querySelector(".cnt");
  const n = unread();
  if (!n) { if (c) c.remove(); return; }
  if (!c) { c = document.createElement("span"); c.className = "cnt"; w.appendChild(c); }
  c.textContent = n > 99 ? "99+" : n;
  c.classList.remove("pop"); void c.offsetWidth; c.classList.add("pop");
}
function ago(ts) {
  const s = (Date.now() - new Date(ts).getTime()) / 1000;
  return s < 60 ? "방금" : s < 3600 ? `${Math.floor(s / 60)}분 전` : s < 86400 ? `${Math.floor(s / 3600)}시간 전` : `${Math.floor(s / 86400)}일 전`;
}
function alertRow(a) {
  const dir = a.data?.dir;
  const [label, ic0] = ALERT_KINDS[a.kind] || ["알림", "🔔"];
  const ic = a.kind === "price" && dir === "down" ? "📉" : ic0;
  return `<a class="al-it lv-${esc(a.level)} ${a.id > AL.seen ? "new" : ""} ${dir ? "dir-" + dir : ""}" href="${esc(a.link || "#control")}">
    <span class="al-ic">${ic}</span><div style="min-width:0;flex:1"><div class="al-t"><b>${esc(a.title)}</b></div>
    ${a.body ? `<div class="xs muted al-b">${esc(a.body)}</div>` : ""}<div class="xs dim">${esc(label)} · ${ago(a.ts)}</div></div></a>`;
}
function renderBellPanel() {
  const p = $("#bell-panel");
  const prefs = alertPrefs();
  p.innerHTML = `<div class="al-head"><b>알림</b><span class="xs dim">${unread()}개 새 알림</span>
      <button class="btn-sm" id="al-read">모두 읽음</button><a class="btn-sm" href="#settings" id="al-set">설정</a></div>
    <div class="al-list">${AL.items.length ? AL.items.map(alertRow).join("") : empty("아직 알림이 없습니다 — 급등락·AI 신호·공시·실적 D-1 등이 여기에 쌓입니다")}</div>
    <div class="xs dim al-foot">${prefs.desktop ? "🖥️ 데스크톱 알림 켜짐" : "🖥️ 데스크톱 알림 꺼짐"} · ${prefs.sound ? "🔊 소리 켜짐" : "🔈 소리 꺼짐"}</div>`;
  $("#al-read").onclick = (e) => { e.stopPropagation(); markRead(); renderBellPanel(); };
}
function markRead() {
  AL.seen = AL.last; safeSet("qa_alert_seen", String(AL.seen)); updateBell();
}
async function pollAlerts(first = false) {
  let r;
  try { r = await api(`/api/alerts?after=${first ? 0 : AL.last}`); } catch { return; }
  const fresh = (r.items || []).filter((a) => a.id > AL.last).sort((a, b) => a.id - b.id);
  if (first) {
    AL.items = r.items || [];
    AL.last = r.last_id || 0;
    if (!AL.seen) { AL.seen = AL.last; safeSet("qa_alert_seen", String(AL.seen)); }
    updateBell();
    return;
  }
  if (!fresh.length) return;
  const desc = [...fresh].reverse();  // 최신 먼저 (알림센터·토스트 모두 위에서부터 최신)
  AL.items = [...desc, ...AL.items].slice(0, 60);
  AL.last = Math.max(AL.last, r.last_id || 0);
  updateBell();
  const prefs = alertPrefs();
  const show = desc.filter((a) => prefs.toast[a.kind] !== false).slice(0, 4);
  show.forEach((a, i) => setTimeout(() => toast({ title: a.title, body: a.body, level: a.level, kind: a.kind, link: a.link, dir: a.data?.dir }), i * 350));
  if (prefs.sound && show.length) beep(show.some((a) => a.level === "bad") ? "bad" : show.some((a) => a.level === "warn") ? "warn" : "info");
  if (prefs.desktop && document.hidden && "Notification" in window && Notification.permission === "granted") {
    show.forEach((a) => { try { const n = new Notification(a.title, { body: a.body || "", tag: `qa-${a.id}` }); n.onclick = () => { window.focus(); location.hash = a.link || "#control"; }; } catch { /* 무시 */ } });
  }
  if ($("#bell-panel")?.classList.contains("open")) renderBellPanel();
}

// ================================================================= 실시간 가격 (깜빡임) · 티커
const LQ = { q: {}, at: null };
function fmtLive(sym, v) { return /^\d{6}$/.test(sym) ? `${num(v)}원` : `$${num(v, 2)}`; }
function applyLive(root = document) {
  root.querySelectorAll("[data-live-sym]").forEach((el) => {
    const q = LQ.q[el.dataset.liveSym];
    if (!q) return;
    const prev = Number(el.dataset.livePx || 0);
    const kind = el.dataset.liveKind || "price";
    const txt = kind === "chg" ? (q.chg_pct == null ? "" : `${q.chg_pct >= 0 ? "+" : ""}${(q.chg_pct * 100).toFixed(2)}%`) : fmtLive(el.dataset.liveSym, q.price);
    if (el.textContent === txt && prev === q.price) return;  // 같은 값이면 DOM 을 건드리지 않는다 (관찰자 루프 방지)
    el.textContent = txt;
    if (kind === "chg") { el.classList.toggle("up", q.chg_pct > 0); el.classList.toggle("down", q.chg_pct < 0); }
    if (prev && prev !== q.price && !reduceMotion()) {
      el.classList.remove("flash-up", "flash-down"); void el.offsetWidth;
      el.classList.add(q.price > prev ? "flash-up" : "flash-down");
    }
    el.dataset.livePx = q.price;
  });
}
function renderTicker() {
  let bar = $("#ticker");
  const rows = Object.entries(LQ.q).filter(([, q]) => q && q.price);
  if (!rows.length) { if (bar) bar.remove(); return; }
  if (!bar) { bar = document.createElement("div"); bar.id = "ticker"; bar.className = "ticker"; $("header.top").after(bar); }
  const items = rows.map(([s, q]) => `<a class="tk" href="#analysis/${esc(s)}"><b>${esc(q.name || s)}</b> <span class="num" data-live-sym="${esc(s)}">${fmtLive(s, q.price)}</span> <span class="num ${q.chg_pct >= 0 ? "up" : "down"}">${q.chg_pct == null ? "" : (q.chg_pct >= 0 ? "▲" : "▼") + Math.abs(q.chg_pct * 100).toFixed(2) + "%"}</span></a>`).join("");
  bar.innerHTML = `<span class="tk-live"><i></i>LIVE</span><div class="tk-track ${reduceMotion() || rows.length < 5 ? "static" : ""}"><div class="tk-row">${items}${reduceMotion() || rows.length < 5 ? "" : items}</div></div>`;
}
async function pollQuotes() {
  try {
    const r = await api("/api/quotes");
    LQ.q = r.quotes || {}; LQ.at = r.at;
    renderTicker(); applyLive();
  } catch { /* 다음 회차 */ }
}

// ================================================================= 화면 애니메이션 · 스켈레톤
function skeleton(rows = 3) {
  return `<div class="card sk-card">${Array.from({ length: rows }, (_, i) => `<div class="sk" style="width:${[92, 76, 84, 60][i % 4]}%"></div>`).join("")}</div>
    <div class="grid g-2"><div class="card sk-card"><div class="sk tall"></div></div><div class="card sk-card"><div class="sk tall"></div></div></div>`;
}
function countUp(el) {
  if (el.dataset.counted) return;  // 한 번만 (화면이 부분 갱신돼도 다시 0 부터 세지 않는다)
  el.dataset.counted = "1";
  const to = Number(el.dataset.count), dec = Number(el.dataset.dec || 0), suf = el.dataset.suf || "", pre = el.dataset.pre || "";
  if (!Number.isFinite(to) || reduceMotion()) { el.textContent = pre + to.toFixed(dec) + suf; return; }
  const t0 = performance.now(), dur = 700;
  const step = (t) => {
    const k = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - k, 3);
    el.textContent = pre + (to * e).toFixed(dec) + suf;
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}
function animateView(root) {
  if (!root) return;
  let i = 0;
  root.querySelectorAll(":scope > .card, :scope > .grid > .card, :scope > .grid > div > .card, :scope > div > .card").forEach((c) => {
    if (c.dataset.anim) return;
    c.dataset.anim = "1"; c.style.setProperty("--i", Math.min(i++, 10)); c.classList.add("rise");
  });
  root.querySelectorAll("[data-count]").forEach(countUp);
  applyLive(root);
}
// 비동기로 채워지는 칸(홈 카드 등)도 애니메이션·실시간 가격을 받도록
function watchView() {
  const v = $("#view");
  if (!v || v._obs) return;
  let pending = false;
  v._obs = new MutationObserver(() => {
    if (pending) return;
    pending = true;
    requestAnimationFrame(() => { pending = false; animateView(v.firstElementChild); });
  });
  v._obs.observe(v, { childList: true, subtree: true });
}

// ================================================================= 설정 카드 (설정 화면에 붙는다)
function alertSettingsCard() {
  const p = alertPrefs();
  const perm = "Notification" in window ? Notification.permission : "unsupported";
  return card("알림 설정 <span class='small dim'>이 브라우저에만 저장</span>", `
    <div class="small muted" style="margin-bottom:8px">토스트로 띄울 알림 (알림센터에는 모두 쌓입니다)</div>
    <div class="chips al-prefs">${Object.entries(ALERT_KINDS).map(([k, [l, ic]]) => `<label class="chk-chip"><input type="checkbox" data-k="${k}" ${p.toast[k] !== false ? "checked" : ""}> ${ic} ${l}</label>`).join("")}</div>
    <div class="al-sw">
      <label class="chk-chip"><input type="checkbox" id="al-desktop" ${p.desktop ? "checked" : ""} ${perm === "unsupported" ? "disabled" : ""}> 🖥️ 창이 가려져 있을 때 데스크톱 알림 ${perm === "denied" ? '<span class="xs bad-t">(브라우저에서 차단됨)</span>' : ""}</label>
      <label class="chk-chip"><input type="checkbox" id="al-sound" ${p.sound ? "checked" : ""}> 🔊 알림음</label>
      <button class="btn-sm" id="al-test">테스트 알림</button>
      <button class="btn-sm" id="al-px">지금 시세 확인</button>
    </div>
    <div class="xs dim" style="margin-top:8px">급등·급락 기준: 전일 대비 ±3% · ±5% · ±10% 돌파, 20분 안 ±2% 급변 (보유·관심·코어 종목, 장중 2.5분마다). 보유 종목 ±5% 이상과 자동 정지·강등은 텔레그램 등 외부 알림으로도 보냅니다.</div>`);
}
function bindAlertSettings() {
  document.querySelectorAll(".al-prefs input").forEach((i) => i.onchange = () => { const p = alertPrefs(); p.toast[i.dataset.k] = i.checked; saveAlertPrefs(p); });
  const d = $("#al-desktop");
  if (d) d.onchange = async () => {
    const p = alertPrefs();
    if (d.checked && "Notification" in window && Notification.permission !== "granted") {
      const r = await Notification.requestPermission();
      if (r !== "granted") { d.checked = false; toast({ title: "데스크톱 알림이 허용되지 않았습니다", level: "warn" }); }
    }
    p.desktop = d.checked; saveAlertPrefs(p);
  };
  const s = $("#al-sound");
  if (s) s.onchange = () => { const p = alertPrefs(); p.sound = s.checked; saveAlertPrefs(p); if (s.checked) beep("info"); };
  const t = $("#al-test");
  if (t) t.onclick = () => { toast({ title: "SK하이닉스 급등 +5.2% (예시)", body: "412,000원 · 전일 대비 +5% 돌파 (보유)", level: "info", kind: "price", dir: "up", link: "#control" }); if (alertPrefs().sound) beep("info"); };
  const px = $("#al-px");
  if (px) px.onclick = async () => { px.disabled = true; px.textContent = "받는 중…"; const r = await runAction("price_watch"); px.disabled = false; px.textContent = "지금 시세 확인"; toast({ title: r.error ? "시세 받기 실패" : `시세 ${r.result?.checked ?? 0}종목 확인`, body: r.error || `새 알림 ${r.result?.alerts ?? 0}개`, level: r.error ? "warn" : "good" }); pollQuotes(); pollAlerts(); };
}

// ================================================================= 24H 관제실
const ltabs = (id, obj, cur) => `<div class="tabs" id="${id}">${Object.entries(obj).map(([k, v]) => `<button data-k="${k}" class="${cur === k ? "on" : ""}">${v}</button>`).join("")}</div>`;
const FAC = (lv) => lv > 0 ? `<span class="fac up">${"+".repeat(lv)}</span>` : lv < 0 ? `<span class="fac down">${"−".repeat(-lv)}</span>` : '<span class="fac flat">·</span>';
const pctS = (v, d = 1) => v == null ? "-" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(d)}%`;
const STAGE_IC = { collect: "data", organize: "news", analyze: "ai", decide: "engine", predict: "journal", compare: "review", score: "score", promote: "ladder" };

function predCard(f) {
  const exp = f.expected, done = f.correct != null;
  const price = f.last_close != null ? (f.market === "KR" ? `${num(f.last_close)}원` : `$${num(f.last_close, 2)}`) : "";
  return `<div class="pred ${done ? (f.correct ? "hit" : "miss") : ""}">
    <div class="pred-h"><a href="#analysis/${esc(f.symbol)}"><b>${esc(f.name)}</b> <span class="dim small">${esc(f.symbol)}</span></a>
      <span class="xs dim">${time(f.as_of, true)}</span></div>
    <div class="pred-px"><span class="num" data-live-sym="${esc(f.symbol)}">${esc(price)}</span> ${badge(f.action)}${f.trigger ? ` <span class="chip xs" title="${esc(f.trigger)}">⚡ 이벤트</span>` : ""}</div>
    <div class="pred-f">${f.factors.map((x) => `<div><span class="muted">${esc(x.key)}</span>${FAC(x.level)}</div>`).join("")}</div>
    <div class="pred-ai"><span class="muted small">AI 예상</span> <b>${f.horizon}거래일 상승 확률 ${(f.prob_up * 100).toFixed(0)}%</b>${exp != null ? ` · 예상 <b class="${exp >= 0 ? "up" : "down"}">${pctS(exp)}</b>` : ""}</div>
    ${done ? `<div class="pred-r">실제 결과 <b class="${f.realized >= 0 ? "up" : "down"}">${pctS(f.realized)}</b> → ${f.correct ? '<span class="good">적중 ✔</span>' : '<span class="bad-t">빗나감 ✘</span>'}</div>` : `<div class="pred-r dim small">결과 대기 중 · ${f.horizon}거래일 뒤 자동 채점</div>`}
  </div>`;
}

function curveSvg(curve) {
  if (!curve || curve.length < 2) return "";
  const v = curve.map((x) => x[1]), mn = Math.min(1, ...v), mx = Math.max(1, ...v), W = 300, H = 70;
  const y = (x) => H - 4 - ((x - mn) / (mx - mn || 1)) * (H - 8);
  const pts = v.map((x, i) => `${(i / (v.length - 1)) * W},${y(x)}`).join(" ");
  const last = v[v.length - 1];
  return `<svg viewBox="0 0 ${W} ${H}" class="curve" preserveAspectRatio="none"><line x1="0" x2="${W}" y1="${y(1)}" y2="${y(1)}" class="base"/>
    <polyline points="${pts}" class="${last >= 1 ? "pos" : "neg"}"/></svg>`;
}
function calibSvg(bins) {
  if (!bins || !bins.length) return "";
  const W = 160, H = 110;
  return `<svg viewBox="0 0 ${W} ${H}" class="calib"><line x1="0" y1="${H}" x2="${W}" y2="0" class="diag"/>
    ${bins.map((b) => `<circle cx="${b.pred * W}" cy="${H - b.actual * H}" r="${Math.min(9, 2.5 + Math.sqrt(b.n))}"><title>예측 ${(b.pred * 100).toFixed(0)}% → 실제 ${(b.actual * 100).toFixed(0)}% (${b.n}회)</title></circle>`).join("")}</svg>`;
}

function scoreBlock(sc, mkt) {
  const s = sc?.summary || {};
  if (!s.n) return empty(`${mkt === "US" ? "미국" : "국내"} 예측이 아직 채점되지 않았습니다 — 예측 후 ${mkt === "US" ? "5" : "5"}거래일이 지나면 자동으로 채점됩니다 (대기 ${sc?.pending ?? 0}건)`);
  const tile = (l, v, sub, cls = "") => `<div class="sc-t"><div class="xs muted">${l}</div><div class="big num ${cls}">${v}</div><div class="xs dim">${sub}</div></div>`;
  const r30 = sc.last_30d || {};
  return `<div class="sc-grid">
      ${tile("방향 적중률", `<span data-count="${(s.hit_rate * 100).toFixed(1)}" data-dec="1" data-suf="%">${(s.hit_rate * 100).toFixed(1)}%</span>`, `최근 ${s.n}회 · 30일 ${r30.hit_rate == null ? "-" : (r30.hit_rate * 100).toFixed(0) + "%"}`, s.hit_rate > 0.55 ? "good" : s.hit_rate < 0.5 ? "bad-t" : "")}
      ${tile("평균 기대수익", pctS(s.avg_expected, 2), "AI 가 말한 크기")}
      ${tile("평균 실제수익", pctS(s.avg_actual, 2), "모든 예측 평균", s.avg_actual >= 0 ? "up" : "down")}
      ${tile("비용 차감 후", pctS(s.avg_signal_net, 2), `BUY/SELL ${s.n_trades}건 · 비용 ${pctS(s.cost, 2).replace("+", "")}`, (s.avg_signal_net ?? 0) > 0 ? "good" : "bad-t")}
      ${tile("MDD", pctS(s.mdd, 1), "신호를 나눠 따랐다면", s.mdd < -0.15 ? "bad-t" : "")}
      ${tile("확률 보정", s.brier_skill == null ? "-" : s.brier_skill.toFixed(3), `ECE ${s.ece == null ? "-" : (s.ece * 100).toFixed(1) + "%"} · 0 이상이면 정보 있음`, (s.brier_skill ?? 0) > 0 ? "good" : "")}
    </div>
    <div class="sc-charts"><div><div class="xs muted">신호를 따랐다면 (비용 차감 · 누적)</div>${curveSvg(s.curve) || empty("신호 거래 없음")}</div>
      <div><div class="xs muted">확률 보정 (대각선에 가까울수록 정직)</div>${calibSvg(s.calibration)}</div></div>`;
}

async function viewControl(el) {
  const [c, lad] = await Promise.all([api("/api/control"), api("/api/ladder")]);
  S.ctlMkt = S.ctlMkt || "KR";
  const st = lad.state || {}, stage = st.stage || "backtest";
  const alive = c.scheduler_alive;
  const lastJob = c.jobs.filter((j) => j.last).sort((a, b) => b.last.localeCompare(a.last))[0];
  const stageIdx = lad.stages.findIndex((x) => x.key === stage);
  const flow = c.stages.map((s, i) => `<div class="flow-n st-${s.status}" style="--i:${i}"><div class="flow-ic">${ICONS[STAGE_IC[s.key]] || ""}</div>
      <b>${esc(s.label)}</b><div class="xs muted">${esc(s.detail)}</div><div class="xs dim">${s.last ? ago(s.last) : "기록 없음"}</div></div>`).join('<div class="flow-arrow"><i></i></div>');
  const conds = (lad.conditions || []).map((x) => `<div class="cond st-${x.status}"><span class="cond-ic">${x.status === "pass" ? "✔" : x.status === "fail" ? "✘" : "…"}</span>
      <div style="min-width:0"><b>${esc(x.label)}</b><div class="xs muted">${esc(x.value)} <span class="dim">· 기준 ${esc(x.need)}</span></div></div></div>`).join("");
  const steps = lad.stages.map((x, i) => `<div class="lstep ${i < stageIdx ? "done" : i === stageIdx ? "cur" : ""}"><span class="lnum">${i + 1}</span><div><b>${esc(x.label)}</b><div class="xs muted">${esc(x.desc)}</div></div></div>`).join("");
  const eff = lad.effects || {};
  const hist = (st.history || []).slice().reverse().slice(0, 5).map((h) => `<div class="xs"><span class="${h.kind === "demote" ? "bad-t" : "good"}">${h.kind === "demote" ? "▼ 강등" : "▲ 승격"}</span> ${esc(lad.stages.find((x) => x.key === h.from)?.label || h.from)} → ${esc(lad.stages.find((x) => x.key === h.to)?.label || h.to)} <span class="dim">${date(h.at)}</span></div>`).join("");
  const jobs = c.jobs.map((j) => `<tr><td><b>${esc(j.label)}</b><div class="xs dim">${esc(j.job)}</div></td><td class="small">${esc(j.cadence)}</td>
      <td class="small">${j.last ? ago(j.last) : '<span class="dim">-</span>'}</td><td class="small">${j.next ? (new Date(j.next) < new Date() ? '<span class="dim">곧</span>' : time(j.next)) : "-"}</td>
      <td>${j.ok === false ? `<span class="chip neg" title="${esc(j.error || "")}">실패</span>` : j.ok ? '<span class="chip pos">정상</span>' : '<span class="chip">대기</span>'}</td></tr>`).join("");
  const sc = c.scorecard[S.ctlMkt];
  const pulse = (c.pulse || []).map((p) => p.score);
  const rdy = await api("/api/readiness").catch(() => null);
  el.innerHTML = (typeof readinessStrip === "function" ? readinessStrip(rdy) : "") + `
  <div class="card ctl-hero">
    <div class="ctl-top"><div><div class="ctl-live ${alive ? "on" : ""}"><i></i>${alive ? "AI 24시간 관찰 중" : "스케줄러 멈춤 — ./run.sh 로 켜 두세요"}</div>
      <h2>24H 관제실</h2><div class="small muted">컴퓨터만 켜 두면 AI 가 시장을 관찰하고 · 모든 판단을 기록하고 · 틀린 이유까지 복기하면서 · 검증을 통과한 경우에만 실제 돈을 씁니다.</div></div>
      <div class="ctl-kpi">
        <div><div class="xs muted">누적 예측</div><div class="big num" data-count="${c.counts.predictions}">${c.counts.predictions}</div></div>
        <div><div class="xs muted">결과 대기</div><div class="big num" data-count="${c.counts.pending}">${c.counts.pending}</div></div>
        <div><div class="xs muted">24시간 알림</div><div class="big num" data-count="${c.counts.alerts_24h}">${c.counts.alerts_24h}</div></div>
        <div><div class="xs muted">검증 단계</div><div class="big stage-chip">${esc(lad.stages[stageIdx]?.label || stage)}</div></div>
      </div></div>
    <div class="xs dim">${lastJob ? `마지막 작업: ${esc(lastJob.label)} · ${ago(lastJob.last)}` : "아직 실행 기록이 없습니다"}${pulse.length > 1 ? ` · 시장 심리 흐름 ${spark(pulse, 120, 22)}` : ""}</div>
    <div class="flow">${flow}</div>
  </div>
  <div id="ag-strip"></div>
  <div class="grid g-21">
    ${card(`방금 저장된 예측 <span class="small dim">요인 +++ · AI 예상 · 실제 결과</span>`, c.feed.length ? `<div class="pred-grid">${c.feed.slice(0, 12).map(predCard).join("")}</div>` : empty("아직 예측이 없습니다 — AI 판단이 한 번 돌면 여기에 쌓입니다"), `<a class="link" href="#journal">판단 저널 ${ICONS.arrow}</a>`)}
    <div class="stack">
      ${card(`검증 사다리 <span class="small dim">좋은 모델만 승격</span>`, `<div class="lsteps">${steps}</div>
        <div class="lad-eff small"><div>가상 장부 AI <b class="${eff.paper_ai ? "good" : "dim"}">${eff.paper_ai ? "켜짐" : "꺼짐"}</b></div>
          <div>실전 AI <b class="${eff.live_ai ? "good" : "dim"}">${eff.live_ai == null ? "실전 계좌 없음" : eff.live_ai ? "켜짐" : "꺼짐 (소액 Live 부터)"}</b></div>
          <div>실전 운용 상한 <b>${krw(eff.live_cap)}</b></div></div>
        ${lad.would ? `<div class="lesson" style="margin-top:10px">다음 야간 평가 때 <b>${esc(lad.stages.find((x) => x.key === lad.would)?.label || lad.would)}</b> 로 ${lad.would_kind === "demote" ? "강등" : "승격"} 예정</div>` : ""}
        ${(lad.reasons || []).length ? `<div class="lesson" style="margin-top:10px">${lad.reasons.map(esc).join("<br>")}</div>` : ""}
        ${hist ? `<div style="margin-top:10px">${hist}</div>` : ""}
        <button class="btn-sm" id="lad-eval" style="margin-top:10px">지금 평가</button>`)}
      ${card(`자동 승인 조건 <span class="small dim">7개 모두 통과해야 실제 돈</span>`, `<div class="conds">${conds}</div>
        <div class="xs dim" style="margin-top:8px">✔ 통과 · ✘ 미달 · … 표본 부족(판정 보류). 기준은 미리 정해 두고 바꾸지 않습니다. 성능이 떨어지면 자동으로 Shadow 로 강등됩니다.</div>`)}
    </div>
  </div>
  ${card(`예측 성적표 <span class="small dim">"10개 중 몇 개" 가 아니라 돈으로</span>`, `<div id="sc-box">${scoreBlock(sc, S.ctlMkt)}</div>`,
    ltabs("ctl-mkt", { KR: "국내", US: "미국" }, S.ctlMkt))}
  ${card("24시간 작업표 <span class='small dim'>실시간 수집 → 매시간 재분석 → 장 마감 복기 → 야간 평가 → 다음 날 적용</span>", `<div class="scroll"><table class="tight"><thead><tr><th>작업</th><th>주기</th><th>마지막</th><th>다음</th><th>상태</th></tr></thead><tbody>${jobs}</tbody></table></div>`)}`;
  document.querySelectorAll("#ctl-mkt button").forEach((b) => b.onclick = () => { S.ctlMkt = b.dataset.k; render(); });
  if (typeof fillAgentStrip === "function") fillAgentStrip();
  $("#lad-eval").onclick = async (e) => {
    e.target.disabled = true; e.target.textContent = "평가 중…";
    const r = await runAction("ladder");
    toast({ title: r.error ? "평가 실패" : r.result?.changed ? `단계 변경: ${r.result.stage}` : "단계 유지", body: r.error || (r.result?.reasons || []).join(" · "), level: r.error ? "warn" : "good", kind: "ladder" });
    render();
  };
}

// ================================================================= 홈: AI 24H 한 줄
async function fillLiveStrip() {
  const b = $("#live-strip");
  if (!b) return;
  try {
    const c = await api("/api/control");
    const f = c.feed[0];
    const sc = c.scorecard.KR.summary;
    b.innerHTML = `<a class="live-strip ${c.scheduler_alive ? "on" : ""}" href="#control"><span class="ls-dot"><i></i></span>
      <b>${c.scheduler_alive ? "AI 24시간 관찰 중" : "스케줄러 꺼짐"}</b>
      <span class="ls-it">누적 예측 <b class="num">${c.counts.predictions}</b></span>
      <span class="ls-it">결과 대기 <b class="num">${c.counts.pending}</b></span>
      <span class="ls-it">적중률 <b class="num">${sc.n ? (sc.hit_rate * 100).toFixed(0) + "%" : "-"}</b></span>
      ${f ? `<span class="ls-it ls-last">최근 예측 <b>${esc(f.name)}</b> ${badge(f.action)} 상승 ${(f.prob_up * 100).toFixed(0)}%${f.expected != null ? ` · 예상 ${pctS(f.expected)}` : ""}</span>` : ""}
      <span class="ls-go">관제실 ${ICONS.arrow}</span></a>`;
  } catch { b.innerHTML = ""; }
}

// ================================================================= 종목 화면: 커뮤니티 분위기
function pfCommunity(p) {
  const c = p.community;
  if (!c || !c.n) return "";
  const tot = (c.bull || 0) + (c.bear || 0);
  return `<div class="com-top"><div><div class="xs muted">${esc(c.source || "")} · 글 ${c.n}개 · ${c.at ? ago(c.at) : ""}</div>
      <div class="big">${esc(c.label || "-")}</div></div>
      <div class="com-bar">${tot ? `<span class="b" style="flex:${c.bull}">낙관 ${c.bull}</span><span class="r" style="flex:${c.bear}">비관 ${c.bear}</span>` : '<span class="n">분위기 표시 없음</span>'}</div></div>
    ${(c.posts || []).map((x) => `<div class="pfn"><div style="flex:1;min-width:0"><div class="t">${safeUrl(x.url) ? `<a href="${safeUrl(x.url)}" target="_blank" rel="noopener">${esc(x.title)}</a>` : esc(x.title)}</div></div>${x.sentiment != null ? sentChip(x.sentiment) : ""}</div>`).join("")}
    <div class="xs dim" style="margin-top:6px">참고용 · 신뢰도 낮음 · 과열은 역지표일 수 있습니다. AI 에게는 약한 참고로만 전달합니다.</div>`;
}

// ================================================================= 시작
// CSP(script-src 'self') 는 onclick="..." 같은 인라인 스크립트를 막는다 → data-* 속성 + 위임 이벤트로 처리
document.addEventListener("click", (e) => {
  const go = e.target.closest("[data-go]");
  if (go) { e.preventDefault(); location.hash = go.dataset.go; return; }
  const ask = e.target.closest("[data-ask]");
  if (ask && window.askAI) { e.preventDefault(); window.askAI(ask.dataset.ask); }
});

function initLive() {
  if (AL.started) return;
  AL.started = true;
  AL.seen = Number(safeGet("qa_alert_seen") || 0);
  toastBox();
  watchView();
  $("#bell-btn").onclick = (e) => {
    e.stopPropagation();
    const p = $("#bell-panel");
    p.classList.toggle("open");
    if (p.classList.contains("open")) { toastBox().replaceChildren(); renderBellPanel(); setTimeout(markRead, 1500); }
  };
  pollAlerts(true);
  pollQuotes();
  setInterval(() => { if (!document.hidden || alertPrefs().desktop) pollAlerts(); }, 15000);
  setInterval(() => { if (!document.hidden) pollQuotes(); }, 20000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) { pollAlerts(); pollQuotes(); } });
}
