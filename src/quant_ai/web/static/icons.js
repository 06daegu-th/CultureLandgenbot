/* Quant AI 아이콘 세트 — 외부 요청 없이 인라인 SVG (다크/라이트 테마 공통) */
"use strict";

const _s = (d, extra = "") => `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" ${extra}>${d}</svg>`;

const ICONS = {
  logo: `<svg viewBox="0 0 32 32" width="30" height="30"><defs><linearGradient id="lg-logo" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#60a5fa"/><stop offset="1" stop-color="#6366f1"/></linearGradient></defs>
    <path d="M16 2.5 28 9.5v13L16 29.5 4 22.5v-13Z" fill="url(#lg-logo)"/><path d="M9.5 19.5 14 14l3.5 3 5.5-6.5" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/><circle cx="23" cy="10.5" r="1.9" fill="#fff"/></svg>`,
  shield: _s('<path d="M12 3 4 6v6c0 4.5 3.4 8.3 8 9 4.6-.7 8-4.5 8-9V6Z"/><path d="m9 12 2 2 4-4"/>'),
  compare: _s('<path d="M4 18 9 12l4 3 7-9"/><path d="M4 8l5 3 4-5 7 4" stroke-dasharray="2 2"/>'),
  home: _s('<path d="M3 11 12 4l9 7"/><path d="M5 10v10h14V10"/><path d="M10 20v-6h4v6"/>'),
  market: _s('<path d="M4 20V10"/><path d="M10 20V4"/><path d="M16 20v-7"/><path d="M22 20H2"/>'),
  ai: _s('<path d="M12 3v2M12 19v2M3 12h2M19 12h2"/><rect x="6" y="6" width="12" height="12" rx="3"/><path d="M9.5 14.5 12 9l2.5 5.5M10.3 12.8h3.4"/>'),
  score: _s('<path d="M8 21h8M12 17v4"/><path d="M7 4h10v5a5 5 0 0 1-10 0Z"/><path d="M17 5h3v2a3 3 0 0 1-3 3M7 5H4v2a3 3 0 0 0 3 3"/>'),
  portfolio: _s('<path d="M21 12a9 9 0 1 1-9-9v9Z"/><path d="M13 3.1A9 9 0 0 1 20.9 11H13Z"/>'),
  risk: _s('<path d="M12 3 4 6v6c0 4.5 3.4 8 8 9 4.6-1 8-4.5 8-9V6Z"/><path d="M12 8v5M12 16.5v.01"/>'),
  auto: _s('<rect x="4" y="7" width="16" height="12" rx="3"/><path d="M12 7V4M9 12v1M15 12v1M9.5 16h5"/><circle cx="12" cy="3.5" r="1"/>'),
  orders: _s('<path d="M8 6h13M8 12h13M8 18h13"/><path d="M3 6h.01M3 12h.01M3 18h.01"/>'),
  sheet: _s('<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9Z"/><path d="M14 3v6h6M8 13h8M8 17h5"/>'),
  journal: _s('<path d="M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3Z"/><path d="M5 17a3 3 0 0 1 3-3h11M9 8h6"/>'),
  evidence: _s('<circle cx="6" cy="6" r="2.2"/><circle cx="18" cy="6" r="2.2"/><circle cx="12" cy="18" r="2.2"/><path d="M8 6h8M7.2 7.9l3.7 8.2M16.8 7.9l-3.7 8.2"/>'),
  news: _s('<path d="M4 5h13v14H6a2 2 0 0 1-2-2Z"/><path d="M17 8h3v9a2 2 0 0 1-2 2"/><path d="M7 9h7M7 13h7M7 16h4"/>'),
  review: _s('<path d="M4 20h16"/><path d="M6 16V9M11 16V5M16 16v-4"/><path d="m18 4 2 2-6 6"/>'),
  research: _s('<path d="M9 3h6M10 3v6L4.5 18.5A1.7 1.7 0 0 0 6 21h12a1.7 1.7 0 0 0 1.5-2.5L14 9V3"/><path d="M7.5 15h9"/>'),
  models: _s('<path d="M12 3 20 7.5v9L12 21 4 16.5v-9Z"/><path d="M12 12 20 7.5M12 12 4 7.5M12 12v9"/>'),
  ops: _s('<path d="M3 12h4l3-7 4 14 3-7h4"/>'),
  settings: _s('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 0 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 0 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 0 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 0 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z"/>'),
  search: _s('<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>'),
  control: _s('<circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M5.6 18.4l2.1-2.1M16.3 7.7l2.1-2.1"/>'),
  pulse: _s('<path d="M3 12h4l2-6 4 12 2-6h6"/>'),
  ladder: _s('<path d="M7 3v18M17 3v18M7 7h10M7 12h10M7 17h10"/>'),
  up: _s('<path d="M12 19V5M5 12l7-7 7 7"/>'),
  down: _s('<path d="M12 5v14M5 12l7 7 7-7"/>'),
  volume: _s('<path d="M4 9v6h4l5 4V5L8 9H4Z"/><path d="M16 9a4 4 0 0 1 0 6M18.5 6.5a8 8 0 0 1 0 11"/>'),
  target: _s('<circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="4.5"/><circle cx="12" cy="12" r="1"/>'),
  star: _s('<path d="m12 3.5 2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 16.9l-5.2 2.7 1-5.8-4.3-4.1 5.9-.9Z"/>'),
  calendar: _s('<rect x="4" y="5.5" width="16" height="14.5" rx="2"/><path d="M4 10h16M8.5 3.5v4M15.5 3.5v4"/>'),
  bell: _s('<path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15Z"/><path d="M10 20a2 2 0 0 0 4 0"/>'),
  sun: _s('<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>'),
  moon: _s('<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5Z"/>'),
  stop: _s('<circle cx="12" cy="12" r="9"/><rect x="9" y="9" width="6" height="6" rx="1"/>'),
  play: _s('<circle cx="12" cy="12" r="9"/><path d="m10 8.5 5.5 3.5-5.5 3.5Z"/>'),
  data: _s('<ellipse cx="12" cy="5" rx="7" ry="2.5"/><path d="M5 5v6c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5V5"/><path d="M5 11v6c0 1.4 3.1 2.5 7 2.5s7-1.1 7-2.5v-6"/>'),
  engine: _s('<path d="M4 7h16v10H4z"/><path d="M8 7V4h8v3M8 17v3M16 17v3M4 12H2M22 12h-2"/>'),
  learn: _s('<path d="M3 8 12 4l9 4-9 4Z"/><path d="M7 10v4.5c1.5 1.4 3.2 2 5 2s3.5-.6 5-2V10"/>'),
  arrow: _s('<path d="M5 12h14M13 6l6 6-6 6"/>'),
  check: _s('<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>', 'class="ic-check"'),
  x: _s('<circle cx="12" cy="12" r="9"/><path d="m9 9 6 6M15 9l-6 6"/>'),
  close: _s('<path d="M6 6l12 12M18 6 6 18"/>'),
  expand: _s('<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>'),
};

/* ------------------------------------------------------------------ AI 공급자·역할 아이콘 (브랜드 색 + 단순 기호) */
const AI_BRAND = {
  Gemini: { bg: "linear-gradient(135deg,#1e3a8a,#312e81)", svg: `<svg viewBox="0 0 24 24" width="20" height="20"><defs><linearGradient id="gm" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#60a5fa"/><stop offset=".55" stop-color="#a78bfa"/><stop offset="1" stop-color="#f472b6"/></linearGradient></defs><path d="M12 2c.6 5.3 4.7 9.4 10 10-5.3.6-9.4 4.7-10 10-.6-5.3-4.7-9.4-10-10 5.3-.6 9.4-4.7 10-10Z" fill="url(#gm)"/></svg>` },
  NVIDIA: { bg: "#1a2e05", svg: `<svg viewBox="0 0 24 24" width="20" height="20"><path d="M9 7.2c4.5-.4 8.2 1.6 11 4.8-2.8 3.2-6.5 5.2-11 4.8" fill="none" stroke="#76b900" stroke-width="2.2" stroke-linecap="round"/><path d="M9 9.8c2.6-.2 4.8.8 6.4 2.2-1.6 1.4-3.8 2.4-6.4 2.2" fill="#76b900"/><rect x="3" y="5" width="3.2" height="14" rx="1" fill="#76b900"/></svg>` },
  Groq: { bg: "#3b1208", svg: `<svg viewBox="0 0 24 24" width="20" height="20"><circle cx="11.5" cy="10.5" r="5.3" fill="none" stroke="#f55036" stroke-width="2.4"/><path d="M16.8 10.5v4.2a5 5 0 0 1-8.6 3.5" fill="none" stroke="#f55036" stroke-width="2.4" stroke-linecap="round"/></svg>` },
  Cloudflare: { bg: "#3a1d05", svg: `<svg viewBox="0 0 24 24" width="20" height="20"><path d="M6.5 17.5h11.2a3.3 3.3 0 0 0 .4-6.6 5 5 0 0 0-9.6-1.3 3.9 3.9 0 0 0-2 7.9Z" fill="#f38020"/><path d="M10 17.5h8.4a2.3 2.3 0 0 0-1.6-3.9" fill="none" stroke="#fbad41" stroke-width="1.6"/></svg>` },
  Claude: { bg: "#3a1a10", svg: `<svg viewBox="0 0 24 24" width="20" height="20"><g stroke="#d97757" stroke-width="2.2" stroke-linecap="round"><path d="M12 3v18M3 12h18M5.6 5.6l12.8 12.8M18.4 5.6 5.6 18.4"/></g></svg>` },
  "Quant 모델": { bg: "#0b2447", svg: `<svg viewBox="0 0 24 24" width="20" height="20"><rect x="4" y="12" width="3.4" height="7" rx="1" fill="#60a5fa"/><rect x="10.3" y="7" width="3.4" height="12" rx="1" fill="#3b82f6"/><rect x="16.6" y="4" width="3.4" height="15" rx="1" fill="#93c5fd"/></svg>` },
  "국면 엔진": { bg: "#062f2f", svg: `<svg viewBox="0 0 24 24" width="20" height="20"><circle cx="12" cy="12" r="8.2" fill="none" stroke="#2dd4bf" stroke-width="1.8"/><path d="m15.5 8.5-2.2 4.8-4.8 2.2 2.2-4.8Z" fill="#2dd4bf"/></svg>` },
  "리스크 규칙": { bg: "#3b0a0a", svg: `<svg viewBox="0 0 24 24" width="20" height="20"><path d="M12 3 5 6v5.5c0 4.2 3 7.6 7 8.5 4-.9 7-4.3 7-8.5V6Z" fill="#ef4444"/><path d="M12 8v5M12 16v.01" stroke="#fff" stroke-width="2.2" stroke-linecap="round"/></svg>` },
  "휴리스틱": { bg: "#1e293b", svg: `<svg viewBox="0 0 24 24" width="20" height="20"><rect x="6" y="6" width="12" height="12" rx="2.5" fill="none" stroke="#94a3b8" stroke-width="1.8"/><path d="M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3" stroke="#94a3b8" stroke-width="1.6"/><circle cx="12" cy="12" r="2" fill="#94a3b8"/></svg>` },
  Challenger: { bg: "#2e1065", svg: `<svg viewBox="0 0 24 24" width="20" height="20"><path d="M9 3h6M10 3v6l-5 9a1.8 1.8 0 0 0 1.6 2.6h10.8A1.8 1.8 0 0 0 19 18l-5-9V3" fill="none" stroke="#a78bfa" stroke-width="1.8"/><path d="M7.5 15h9" stroke="#a78bfa" stroke-width="1.8"/></svg>` },
};
const ROLE_ICON = { primary: "Gemini", nvidia: "NVIDIA", panel: "Cloudflare", risk: "리스크 규칙", quant: "Quant 모델", regime: "국면 엔진", challenger: "Challenger" };

function providerOf(model, provider) {
  if (provider) return { gemini: "Gemini", nvidia: "NVIDIA", groq: "Groq", cloudflare: "Cloudflare", claude: "Claude", anthropic: "Claude" }[provider] || provider;
  const m = String(model || "").toLowerCase();
  if (!m) return null;
  if (m.includes("gemini")) return "Gemini";
  if (m.startsWith("@cf/")) return "Cloudflare";
  if (m.startsWith("nvidia/")) return "NVIDIA";
  if (m.includes("claude")) return "Claude";
  if (m.includes("gpt-oss") || m.includes("llama")) return "Groq";
  if (m.startsWith("sklearn")) return "Quant 모델";
  if (m.startsWith("regime")) return "국면 엔진";
  if (m.startsWith("rules")) return "리스크 규칙";
  if (m.startsWith("heuristic")) return "휴리스틱";
  if (m.startsWith("signals2")) return "Quant 모델";
  return null;
}

/* aiIcon("Gemini") 또는 aiIcon(null, "risk", "rules") */
const LLM_ROLES = new Set(["primary", "nvidia", "panel"]);
function aiIcon(provider, role, model, size = 30) {
  // LLM 역할인데 공급자가 없으면 = 키 미설정 → 휴리스틱 (브랜드 아이콘을 잘못 보여주지 않는다)
  const name = provider || providerOf(model) || (String(model || "").startsWith("heuristic") || LLM_ROLES.has(role) ? "휴리스틱" : ROLE_ICON[role]) || "휴리스틱";
  const b = AI_BRAND[name] || AI_BRAND["휴리스틱"];
  return `<span class="ai-ic" style="width:${size}px;height:${size}px;background:${b.bg}" title="${name}">${b.svg}</span>`;
}

/* ------------------------------------------------------------------ 뉴스 분류 이모지 · 국기 */
const CAT_EMOJI = { 정책: "🏛️", 실적: "📊", 공시: "📄", 원자재: "🛢️", 환율: "💱", 기업: "🏢", 거시: "🌐", 시장: "📈", 이벤트: "⚡", 공급: "🚚" };
const catIcon = (c) => `<span class="cat-ic" title="${c || ""}">${CAT_EMOJI[c] || "📰"}</span>`;
const FLAGS = { US: "🇺🇸", KR: "🇰🇷", JP: "🇯🇵", CN: "🇨🇳", EU: "🇪🇺", GB: "🇬🇧" };
// v24: 국기 이모지 대신 작은 글자 표시 (KR · US …) — 이모지 없이도 나라를 알아볼 수 있게
const flag = (c) => c ? `<span class="flag cc" title="${c}">${String(c).slice(0, 2).toUpperCase()}</span>` : "";

window.ICONS = ICONS; window.aiIcon = aiIcon; window.providerOf = providerOf; window.catIcon = catIcon; window.flag = flag;
