/* v21: 화면 말투 — 개발 용어를 우리말로, 상태 이모지 대신 색 점 하나. 모든 화면이 같은 말을 쓰게 여기 한 곳에서만 정한다. */
"use strict";

const KO_ACT = { BUY: "매수", SELL: "매도", HOLD: "관망", NO_TRADE: "쉬어가기", "NO TRADE": "쉬어가기", 기권: "의견 없음", 통과: "통과", 거부: "거부" };
const KO_ROLE = { News: "뉴스", Macro: "경제", Earnings: "실적", Quant: "차트·통계", Regime: "시장 흐름", Risk: "위험 점검", Challenger: "비교 모델" };
const ACT_LV = { BUY: "buy", SELL: "sell", HOLD: "idle", NO_TRADE: "idle", "NO TRADE": "idle" };  // v23: 매수 빨강 · 매도 파랑 (국내 관례)
const KO_RISK = { HIGH: "높음", MEDIUM: "보통", LOW: "낮음", UNKNOWN: "모름" };

function koAct(a) { return KO_ACT[a] || a || "-"; }
function koRole(r) { return KO_ROLE[r] || r || ""; }
function koRisk(r) { return KO_RISK[r] || r || "-"; }
// 상태 점: good(초록) · warn(노랑) · bad(빨강) · idle(회색) — 🟢🟡🔴⚪ 대신
function lvDot(lv) { return `<i class="lv-dot lv-${String(lv || "idle").replace(/[^a-z_]/g, "")}" aria-hidden="true"></i>`; }
const EMO_LV = { "🟢": "good", "🟡": "warn", "🔴": "bad", "⚪": "idle", "🟠": "warn" };
// 서버가 보낸 문장 앞의 상태 이모지(🟢 …)를 색 점으로 바꾼다 — 나머지 글자는 그대로
function dotText(s) {
  const t = String(s ?? "");
  const m = t.match(/^\s*(🟢|🟡|🔴|⚪|🟠)\s*/u);
  const e = (x) => (typeof esc === "function" ? esc(x) : x);
  return m ? `${lvDot(EMO_LV[m[1]])}${e(t.slice(m[0].length))}` : e(t);
}
// 문장 속 영어 판단 단어를 우리말로 (BUY/SELL/HOLD/NO TRADE) — 화면 표시용
function koText(s) {
  return String(s ?? "").replace(/NO[ _]TRADE/g, "쉬어가기").replace(/\bBUY\b/g, "매수").replace(/\bSELL\b/g, "매도").replace(/\bHOLD\b/g, "관망")
    .replace(/RISK[ _]ON/g, "위험 선호").replace(/RISK[ _]OFF/g, "위험 회피").replace(/\bSIDEWAYS\b/g, "횡보")
    .replace(/\bBULL(ISH)?\b/g, "상승").replace(/\bBEAR(ISH)?\b/g, "하락").replace(/\bCRISIS\b/g, "위기");
}

// 일정 종류 → 작은 색 점 (이모지 21개 대신 5가지 색: 경제지표 · 실적 · 배당 · 휴장 · 만기/지수)
const CAL_CAT = { bok: "macro", fomc: "macro", cpi: "macro", nfp: "macro", gdp: "macro", pce: "macro", retail: "macro", export: "macro",
  earnings: "earn", peer_earnings: "earn", ex_div: "div", div_pay: "div", holiday: "off", half_day: "off",
  options_expiry: "exp", quad_witching: "exp", index_rebalance: "exp", disclosure: "doc", lockup: "doc", unknown: "doc", custom: "doc" };
function calDot(kind) { return `<i class="cal-dot cal-${CAL_CAT[kind] || "doc"}" aria-hidden="true"></i>`; }

// 쉬운 화면: keep 선택자 뒤의 전문가용 카드들을 '펼치기' 하나로 접는다 (지표 이름이 낯선 사람을 위해)
function foldPro(root, keepSel, label) {
  const keep = root.querySelector(keepSel);
  const rest = [...root.children].filter((c) => c !== keep && !c.contains(keep));
  if (!rest.length) return;
  const d = document.createElement("details");
  d.className = "pro-fold";
  d.innerHTML = `<summary>${label}</summary>`;
  rest.forEach((c) => d.appendChild(c));
  root.appendChild(d);
}

// v22: 화면 어디에 남아 있든 그림 이모지를 정리한다 — 상태 색 이모지(🔴🟠🟡🟢⚪⛔⚠)는 색 점으로,
// 장식용 그림(📰🤖🎯🧪🔒…)은 지운다. ★☆✓✕ 같은 기호는 그대로 둔다. 화면을 그리는 코드 수백 곳을 하나하나 고치지 않고
// 그려진 뒤 한 번 거른다 (입력칸·코드 블록은 손대지 않음).
const EMO_DOT = { "🔴": "bad", "⛔": "bad", "🛑": "bad", "❌": "bad", "🟠": "warn", "🟡": "warn", "⚠": "warn", "🟢": "good", "✅": "good", "⚪": "idle" };
const EMO_RE = /(🔴|⛔|🛑|❌|🟠|🟡|⚠|🟢|✅|⚪|[\u{1F1E6}-\u{1F1FF}]{1,2}|[\u{1F300}-\u{1FAFF}]|[\u{2600}-\u{26FF}](?<![★☆]))️?\s?/gu;
const EMO_KEEP = new Set(["★", "☆", "☀", "☁"]);
function deEmojiNode(t) {
  const s = t.nodeValue;
  if (!s || !EMO_RE.test(s)) return;
  EMO_RE.lastIndex = 0;
  const p = t.parentNode;
  if (!p || p.closest?.("input,textarea,pre,code,script,style,[data-keep-emoji]")) return;
  const frag = document.createDocumentFragment();
  let last = 0;
  for (const m of s.matchAll(EMO_RE)) {
    const ch = m[1];
    if (EMO_KEEP.has(ch)) continue;
    if (m.index > last) frag.append(s.slice(last, m.index));
    const lv = EMO_DOT[ch];
    if (lv) { const i = document.createElement("i"); i.className = `lv-dot lv-${lv}`; i.setAttribute("aria-hidden", "true"); frag.append(i); }
    last = m.index + m[0].length;
  }
  if (last === 0) return;
  if (last < s.length) frag.append(s.slice(last));
  p.replaceChild(frag, t);
}
// v24: 화면 어디든 종목 링크(#analysis/종목)에 로고가 없으면 작게 붙인다 — 뷰마다 따로 챙기지 않아도 같은 규칙
function logoLinks(root) {
  if (!root || root.nodeType !== 1 || typeof stockLogo !== "function") return;
  const links = root.matches?.('a[href^="#analysis/"]') ? [root] : root.querySelectorAll('a[href^="#analysis/"]');
  links.forEach((a) => {
    if (a.dataset.lg || a.querySelector("img.lg") || a.closest(".co-id, .side, .tabbar, .tabs, nav, .sh-act, .no-logo, button")
      || a.classList.contains("btn") || a.classList.contains("btn-sm") || a.previousElementSibling?.matches?.("img.lg")
      || a.parentElement?.querySelector(":scope > img.lg")) return;
    const sym = decodeURIComponent((a.getAttribute("href") || "").split("/")[1] || "");
    if (!/^[0-9A-Z][0-9A-Z.\-]{0,11}$/.test(sym) || !(a.textContent || "").trim()) return;
    a.dataset.lg = "1";
    a.insertAdjacentHTML("afterbegin", stockLogo(sym, a.textContent.trim().slice(0, 30), 16) + " ");
  });
}
// v26: 개발·트레이딩 도구 같은 영어 꼬리표(PAPER·SHADOW·Champion·HALTED…)를 우리말로 — 짧은 라벨만 (기사·대화·입력칸은 건드리지 않음)
const JARGON = [
  [/\bLIVE TRADING\b/g, "실전 거래"], [/\bTRADING BLOCKED\b/g, "매매 차단"], [/\bNOT READY\b/g, "준비 안 됨"], [/\bDATA HEALTH\b/g, "데이터 상태"],
  [/\bTrading Readiness\b/g, "매매 준비 점검"], [/\bMarket Regime Engine\b/g, "시장 국면 판단"], [/\bRisk Gate\b/g, "위험 점검"], [/\bAction Center\b/g, "할 일"],
  [/\bAI Lab\b/g, "AI 연구실"], [/\bPortfolio OS\b/g, "자산 자세히"], [/\bFail-Closed\b/g, "장애 시 안전 정지"], [/\bNet Alpha\b/g, "지수 대비 순수익"],
  [/\bus-paper\b/g, "미국 모의"], [/\b(PAPER|paper)\b/g, "모의"], [/\b(SHADOW|Shadow|shadow)\b/g, "그림자"], [/\bLIVE\b/g, "실계좌"], [/\bmanual\b/g, "수동"],
  [/\bHALTED\b/g, "자동 정지"], [/\bBACKTEST\b/g, "과거 시험"], [/\bBLOCKED\b/g, "차단"], [/\b(CHAMPION|Champion|champion)\b/g, "현재 모델"], [/\b(Challenger|challenger)\b/g, "도전 모델"],
  [/\b(Candidate|candidate)\b/g, "후보"], [/\bRollback\b/g, "되돌리기"], [/\bResearch\b/g, "연구"], [/\bEnsemble\b/g, "종합"], [/\bSentinel\b/g, "자동 감시"],
  [/\bReliability\b/g, "신뢰도"], [/\b(REJECTED|rejected)\b/g, "탈락"], [/\bMODEL\b/g, "모델"], [/\bDRIFT\b/g, "데이터 변화"], [/\bCALIBRATION\b/g, "확률 보정"],
  [/\bBROKER\b/g, "증권사"], [/\bRISK\b/g, "위험"], [/\bEVENT\b/g, "일정"], [/\bDATA\b/g, "데이터"],
  [/\bbull_quiet\b/g, "안정적 상승"], [/\bbull_volatile\b/g, "변동성 상승"], [/\bbear_quiet\b/g, "완만한 하락"], [/\bbear_volatile\b/g, "변동성 하락"],
  [/\bsideways\b/g, "횡보"], [/\bcrisis\b/g, "위기"], [/\bregime\b/g, "시장 국면"], [/\bnvidia\b/g, "경제·시장 AI"], [/\bprimary\b/g, "뉴스 AI"],
  [/\bquant-logistic\b/g, "통계 모델"], [/\bCORE\b/g, "코어"], [/\bbuy\b/g, "매수"], [/\bsell\b/g, "매도"], [/\bDOWN\b/g, "하락"], [/\bUP\b/g, "상승"],
  [/\bHIGH\b/g, "높음"], [/\bMEDIUM\b/g, "보통"], [/\bLOW\b/g, "낮음"], [/\bPORTFOLIO\b/g, "포트폴리오"], [/\bOPERATIONS\b/g, "운영"], [/\bSAFETY\b/g, "안전"],
  [/\bstable\b/g, "안정"], [/\bwarn\b/g, "주의"], [/\bFINAL\b/g, "최종"], [/\bRejected\b/g, "탈락"],
  [/\bNO[ _]TRADE\b/g, "쉬어가기"], [/\bHOLD\b/g, "관망"], [/\bBUY\b/g, "매수"], [/\bSELL\b/g, "매도"],
];
const JARGON_ANY = /\b(LIVE|TRADING|READY|HEALTH|Readiness|Regime|Gate|Center|Lab|OS|Fail|Alpha|paper|PAPER|Shadow|SHADOW|shadow|manual|HALTED|BACKTEST|BLOCKED|CHAMPION|Champion|champion|Challenger|challenger|Candidate|candidate|Rollback|Research|Ensemble|Sentinel|Reliability|REJECTED|rejected|MODEL|DRIFT|CALIBRATION|BROKER|RISK|EVENT|DATA|TRADE|HOLD|BUY|SELL|bull_quiet|bull_volatile|bear_quiet|bear_volatile|sideways|crisis|regime|nvidia|primary|quant-logistic|CORE|buy|sell|DOWN|UP|HIGH|MEDIUM|LOW|PORTFOLIO|OPERATIONS|SAFETY|stable|warn|FINAL|Rejected)\b/;
const JARGON_SKIP = "input,textarea,pre,code,kbd,script,style,option[value],[data-news],[data-disc],.dt,.t-art,.t-text,.msg,.chat-in,.pfn,.no-ko,a[target=_blank]";
function deJargonNode(t) {
  const s = t.nodeValue;
  if (!s || s.length > 140 || !JARGON_ANY.test(s)) return;
  const p = t.parentNode;
  if (!p || p.closest?.(JARGON_SKIP)) return;
  let out = s;
  for (const [re, ko] of JARGON) out = out.replace(re, ko);
  if (out !== s) t.nodeValue = out;
}
function deEmoji(root) {
  if (!root) return;
  if (root.nodeType === 3) { deEmojiNode(root); if (root.parentNode) deJargonNode(root); return; }
  if (root.nodeType !== 1) return;
  const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const list = [];
  while (w.nextNode()) list.push(w.currentNode);
  list.forEach(deEmojiNode);
  const w2 = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  while (w2.nextNode()) deJargonNode(w2.currentNode);
}
if (typeof MutationObserver !== "undefined" && typeof document !== "undefined") {
  const start = () => {
    deEmoji(document.body);
    new MutationObserver((ms) => ms.forEach((m) => { m.addedNodes.forEach((n) => { deEmoji(n); logoLinks(n); }); if (m.type === "characterData") { deEmojiNode(m.target); if (m.target.parentNode) deJargonNode(m.target); } }))
      .observe(document.body, { childList: true, subtree: true, characterData: true });
  };
  if (document.body) start(); else document.addEventListener("DOMContentLoaded", start);
}
