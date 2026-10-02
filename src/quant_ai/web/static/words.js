/* v21: 화면 말투 — 개발 용어를 우리말로, 상태 이모지 대신 색 점 하나. 모든 화면이 같은 말을 쓰게 여기 한 곳에서만 정한다. */
"use strict";

const KO_ACT = { BUY: "매수", SELL: "매도", HOLD: "관망", NO_TRADE: "쉬어가기", "NO TRADE": "쉬어가기", 기권: "의견 없음", 통과: "통과", 거부: "거부" };
const KO_ROLE = { News: "뉴스", Macro: "경제", Earnings: "실적", Quant: "차트·통계", Regime: "시장 흐름", Risk: "위험 점검", Challenger: "비교 모델" };
const ACT_LV = { BUY: "good", SELL: "bad", HOLD: "warn", NO_TRADE: "idle", "NO TRADE": "idle" };
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
