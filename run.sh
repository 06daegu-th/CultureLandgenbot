#!/usr/bin/env bash
# Quant AI 실행 도우미
#
#   ./run.sh                  ★ 전부 자동: 설치 → DB 확인 → 데이터 → 점검 → 대시보드 + 24시간 운영
#                               (KIS 모의투자 키가 있으면 모의계좌로 자동매매, 없으면 가상매매)
#
#   개별 명령
#   ./run.sh setup            설치만 (가상환경 · 패키지 · .env)
#   ./run.sh data             KRX 주가 데이터 받기/갱신 + DB 적재
#   ./run.sh doctor [--ai --kis --notify]   점검 (키·DB·데이터·AI·증권사·알림)
#   ./run.sh kis-check        KIS 연결 + 모의 1주 주문→취소 (모의투자에서만)
#   ./run.sh cycle            코어 1회 실행
#   ./run.sh serve            대시보드만 http://127.0.0.1:8050
#   ./run.sh orders --cash 30000000 --holdings my.csv   수동 매매용 주문표
#   ./run.sh checkup          전략 건강검진
#   ./run.sh cashflow 5000000 입금 기록 (출금은 음수)
#   ./run.sh chat             터미널 채팅 AI (대시보드 오른쪽 아래 'AI 에게 묻기' 와 같은 두뇌)
#   ./run.sh proof [--market US]   증명 체인 6단계 (시점 정확 → 확률 → AI 추가수익 → 비용 → 실주문 → 반복)
#   ./run.sh guardian         자동 킬스위치 10개 조건 점검
#   ./run.sh us               미국 장부 (일봉 받기 + 코어·AI 가상매매 한 사이클)
#   ./run.sh warmup           빈 화면 채우기 (뉴스·공시·거시 · AI 판단 1회)
#   ./run.sh db-clean [--yes] DB 정리 (기본 미리보기 · 주문·판단 기록은 보존)
#   ./run.sh up | down | logs Docker 로 상시 운영 (PostgreSQL + 스케줄러 + 대시보드)
#   ./run.sh test             테스트 + 린트
#   ./run.sh <그 외>           quant-ai <그 외> 로 그대로 전달 (예: ./run.sh kill on)
set -euo pipefail

cd "$(dirname "$0")"
ROOT="$(pwd)"
VENV="$ROOT/.venv"
DATA_DIR="${DATA_DIR:-$ROOT/data}"
LOG_DIR="$ROOT/logs"
PORT="${QUANT_WEB_PORT:-8050}"
SQLITE_URL="sqlite:///quant_ai.db"

say()  { printf '\033[1;34m▶ %s\033[0m\n' "$*"; }
ok()   { printf '\033[1;32m✔ %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m⚠ %s\033[0m\n' "$*" >&2; }
die()  { printf '\033[1;31m✖ %s\033[0m\n' "$*" >&2; exit 1; }

find_python() {
  for py in python3.13 python3.12 python3.11 python3; do
    if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
      echo "$py"; return 0
    fi
  done
  return 1
}

# ------------------------------------------------------------------ .env 읽기/쓰기
env_get() {  # .env 에서 값 읽기 (따옴표·줄 끝 주석 제거). 셸 환경변수가 있으면 그것이 우선
  local key="$1" val="${!1:-}"
  if [[ -z "$val" && -f .env ]]; then
    val="$(grep -E "^${key}=" .env | tail -1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$//; s/^["'\'']//; s/["'\'']$//' || true)"
  fi
  printf '%s' "$val"
}

env_replace() {  # KEY 값을 바꾼다 (없으면 추가). 권한(600) 유지
  local key="$1" val="$2" tmp
  tmp="$(mktemp)"
  awk -v k="$key" -v v="$val" 'BEGIN { done = 0 }
    index($0, k "=") == 1 { if (!done) { print k "=" v; done = 1 }; next } { print }
    END { if (!done) print k "=" v }' .env > "$tmp"
  cat "$tmp" > .env && rm -f "$tmp"
}

env_set() {  # .env 의 KEY= 가 비어 있을 때만 채운다 (사용자가 넣은 값은 건드리지 않음)
  local key="$1" val="$2"
  [[ -f .env ]] || return 0
  if grep -qE "^${key}=$" .env || ! grep -qE "^${key}=" .env; then
    env_replace "$key" "$val"
  fi
}

# ------------------------------------------------------------------ 설치 (필요할 때만)
ensure_env() {
  if [[ ! -f .env ]]; then
    cp .env.example .env
    chmod 600 .env
    ok ".env 생성 (권한 600) — 키는 여기에만. 발급처: docs/ENV_KEYS.md"
  fi
}

ensure_installed() {
  local py; py="$(find_python)" || die "Python 3.11 이상이 필요합니다.
  macOS: brew install python@3.12   (Homebrew: https://brew.sh)  또는 https://www.python.org/downloads/
  설치 후 새 터미널에서 다시 ./run.sh"
  [[ -x "$VENV/bin/python" ]] || { say "가상환경 만들기 ($($py --version))"; "$py" -m venv "$VENV"; }
  # 이전에 중단된 pip 업그레이드가 남긴 깨진 폴더(~ip…) 정리 → "Ignoring invalid distribution" 경고 제거
  find "$VENV"/lib/python*/site-packages -maxdepth 1 -name '~*' -exec rm -rf {} + 2>/dev/null || true
  local extras="ai,dev,yahoo"  # yahoo: 해외 일봉 (브라우저 흉내로 429 차단을 피하는 yfinance)
  [[ "$(env_get DATABASE_URL)" == postgres* ]] && extras="$extras,postgres"
  local hash stamp need=0 e
  hash="$("$VENV/bin/python" -c 'import hashlib; print(hashlib.sha1(open("pyproject.toml","rb").read()).hexdigest())')"
  stamp="$(cat "$VENV/.installed" 2>/dev/null || true)"  # "해시:설치한extras"
  if [[ "${stamp%%:*}" != "$hash" ]]; then need=1
  else
    for e in ${extras//,/ }; do [[ ",${stamp#*:}," == *",$e,"* ]] || need=1; done  # 이미 더 많이 설치돼 있으면 건너뜀
    [[ $need -eq 0 ]] || extras="${stamp#*:},${extras}"
  fi
  local want="$hash:$extras"
  if [[ $need -eq 1 ]]; then
    say "패키지 설치 (.[${extras}]) — 처음 한 번 1~2분"
    "$VENV/bin/python" -m pip install -q --upgrade pip
    "$VENV/bin/python" -m pip install -q -e ".[${extras}]"
    printf '%s' "$want" > "$VENV/.installed"
    ok "설치 완료"
  fi
}

qa() {
  [[ -x "$VENV/bin/quant-ai" ]] || { ensure_env; ensure_installed; }
  "$VENV/bin/quant-ai" "$@"
}

# ------------------------------------------------------------------ DB: PostgreSQL 이 없으면 SQLite 로
ensure_db() {
  ensure_env
  local url; url="$(env_get DATABASE_URL)"
  if [[ -z "$url" ]]; then env_replace DATABASE_URL "$SQLITE_URL"; return 0; fi
  [[ "$url" == postgres* ]] || return 0
  local st=0
  qa db-ping >/dev/null 2>&1 || st=$?
  [[ $st -eq 0 || $st -eq 3 ]] && return 0
  if [[ -n "${DATABASE_URL:-}" ]]; then
    die "셸 환경변수 DATABASE_URL 의 PostgreSQL 에 연결할 수 없습니다 → unset DATABASE_URL 후 다시 실행"
  fi
  cp .env ".env.bak.$(date +%Y%m%d%H%M%S)"
  env_replace DATABASE_URL "$SQLITE_URL"
  warn "PostgreSQL(${url##*@}) 이 실행 중이 아니어서 설치가 필요 없는 SQLite(quant_ai.db) 로 전환했습니다 (.env 백업 남김)"
}

# ------------------------------------------------------------------ 데이터
cmd_data() {
  command -v git >/dev/null 2>&1 || die "git 이 필요합니다 (macOS: xcode-select --install)"
  local mdir; mdir="$(env_get QUANT_MARCAP_DIR)"
  if [[ -z "$mdir" ]]; then
    mdir="$DATA_DIR/marcap/data"
    env_set QUANT_MARCAP_DIR "$mdir"
  fi
  local repo; repo="$(dirname "$mdir")"
  local y; y="$(date +%Y)"
  local files=()
  for ((i = y - 4; i <= y; i++)); do files+=("/data/marcap-$i.parquet"); done  # 최근 5년 (모멘텀 12개월 + 200일선 + 여유)
  if [[ ! -d "$repo/.git" ]]; then
    say "KRX 주가 데이터 받기 → $repo (최근 5년만, 약 100MB)"
    mkdir -p "$(dirname "$repo")"
    git clone -q --depth 1 --filter=blob:none --sparse https://github.com/FinanceData/marcap.git "$repo"
  else
    say "KRX 주가 데이터 갱신"
    git -C "$repo" pull --ff-only -q || warn "git pull 실패 — 기존 데이터로 계속"
  fi
  git -C "$repo" sparse-checkout set --no-cone "${files[@]}"
  say "DB 적재 (시가총액 상위 100, 최근 3년) — 1~2분"
  QUANT_MARCAP_DIR="$mdir" qa collect krx --marcap-dir "$mdir" --years 3 --top 100
  mkdir -p "$DATA_DIR" && touch "$DATA_DIR/.last_sync"
}

ensure_data() {
  local st=0
  qa db-ping >/dev/null 2>&1 || st=$?
  if [[ $st -eq 3 ]]; then cmd_data; return; fi
  [[ $st -eq 0 ]] || die "DB 연결 실패 — ./run.sh doctor 로 확인"
  # 6시간 넘게 갱신하지 않았으면 새로 받기 (marcap 은 매일 장 마감 후 갱신)
  if [[ ! -f "$DATA_DIR/.last_sync" ]] || [[ -n "$(find "$DATA_DIR/.last_sync" -mmin +360 2>/dev/null)" ]]; then
    cmd_data
  fi
}

# ------------------------------------------------------------------ 모드 결정
kis_ready() {
  local acct; acct="$(env_get KIS_ACCOUNT)"
  [[ -n "$(env_get KIS_APP_KEY)" && -n "$(env_get KIS_APP_SECRET)" && -n "$acct" && "$acct" != "12345678-01" ]]
}

decide_mode() {  # 출력: paper / live (안내 메시지는 stderr)
  local env broker
  env="$(env_get KIS_ENV)"; env="${env:-demo}"; broker="$(env_get QUANT_BROKER)"
  if ! kis_ready; then
    [[ "$broker" == "kis" ]] && warn "QUANT_BROKER=kis 인데 KIS 키가 비어 있어 가상매매(PAPER)로 실행합니다"
    echo paper; return
  fi
  if [[ "$env" == "real" ]]; then
    if [[ "$broker" == "kis" && -t 0 ]]; then
      warn "KIS_ENV=real — 실제 돈으로 주문합니다. 계속하려면 '실전' 을 입력하세요 (다른 입력 = 가상매매)"
      local ans=""; read -r ans || true
      [[ "$ans" == "실전" ]] && { echo live; return; }
    fi
    echo paper; return
  fi
  echo live  # 모의투자 (KIS_ENV=demo) — 실제 돈 아님
}

wait_http() {
  local i
  for ((i = 0; i < 30; i++)); do
    curl -fsS -m 2 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 && return 0
    sleep 1
  done
  return 1
}

open_browser() {
  [[ -n "${QUANT_NO_BROWSER:-}" || ! -t 1 ]] && return 0
  if command -v open >/dev/null 2>&1; then open "$1" >/dev/null 2>&1 || true
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$1" >/dev/null 2>&1 || true
  fi
}

WEB_PID=""
cleanup() {
  if [[ -n "$WEB_PID" ]] && kill -0 "$WEB_PID" 2>/dev/null; then kill "$WEB_PID" 2>/dev/null || true; fi
  if [[ -n "${WARM_PID:-}" ]] && kill -0 "$WARM_PID" 2>/dev/null; then kill "$WARM_PID" 2>/dev/null || true; fi
}

cmd_auto() {
  say "1/5 설치 확인"
  ensure_env
  ensure_installed
  say "2/5 DB 확인"
  ensure_db
  say "3/5 주가 데이터"
  ensure_data
  local mode; mode="$(decide_mode)"
  export QUANT_MODE="$mode"
  if [[ "$mode" == "live" ]]; then
    export QUANT_BROKER=kis
    [[ "$(env_get KIS_ENV)" == "real" ]] || export KIS_ENV=demo
  fi
  say "4/5 점검"
  local dargs=()
  [[ "$mode" == "live" ]] && dargs+=(--kis)
  qa doctor ${dargs[@]+"${dargs[@]}"} || die "점검 실패 — 위 ❌ 항목을 해결한 뒤 다시 ./run.sh"
  say "5/5 대시보드 + 24시간 운영 시작"
  mkdir -p "$LOG_DIR"
  trap cleanup EXIT INT TERM
  if curl -fsS -m 2 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
    warn "포트 $PORT 에 이미 대시보드가 떠 있어 그대로 씁니다"
  else
    qa serve --port "$PORT" >"$LOG_DIR/web.log" 2>&1 &
    WEB_PID=$!
    wait_http || warn "대시보드 시작 확인 실패 — logs/web.log 확인"
  fi
  ok "대시보드: http://127.0.0.1:$PORT"
  open_browser "http://127.0.0.1:$PORT"
  # 빈 화면 채우기 (뉴스·공시·거시 수집 · AI 판단 1회 · 미국 장부) — 대시보드는 바로 쓰고 뒤에서 진행
  ( qa warmup; [[ "$(env_get QUANT_US)" == "false" ]] || qa us ) >"$LOG_DIR/warmup.log" 2>&1 &
  WARM_PID=$!
  ok "뒤에서 데이터 채우는 중 (뉴스·AI 판단·미국 장부) — 진행: logs/warmup.log · 대시보드 '시작 체크리스트'"
  if [[ "$mode" == "paper" ]]; then
    say "가상매매(PAPER) 코어 사이클 1회 (리밸런싱 날이 아니면 주문 없음)"
    cmd_cycle --mode paper || warn "사이클 실패 — 위 메시지 확인"
  fi
  if [[ "$mode" == "live" ]]; then
    if [[ "$(env_get KIS_ENV)" == "real" ]]; then ok "KIS 실전 계좌로 운영 — 장중(09:10~15:10) 매시간 자동 매매"
    else ok "KIS 모의투자 계좌로 운영 (실제 돈 아님) — 장중(09:10~15:10) 매시간 자동 매매"; fi
  else
    ok "가상매매(PAPER)로 운영 — KIS 모의투자 키를 .env 에 넣으면 다음 실행부터 모의계좌로 자동매매"
  fi
  printf '   장외에는 데이터 갱신 · 복기 · 건강검진만 합니다. 종료: Ctrl+C  (대시보드 로그: logs/web.log)\n\n'
  qa run --mode "$mode"
}

# ------------------------------------------------------------------ 개별 명령
cmd_setup() {
  ensure_env
  ensure_installed
  ensure_db
  cat <<'EOF'

설치 완료. 이제 ./run.sh 만 실행하면 데이터 → 점검 → 대시보드 + 24시간 운영까지 자동으로 진행합니다.
(KIS 모의투자 키를 .env 에 넣으면 모의계좌로 자동매매 · 발급처: docs/ENV_KEYS.md)
EOF
}

cmd_cycle() {
  local mode="" args=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --mode) mode="$2"; shift 2 ;;
      *) args+=("$1"); shift ;;
    esac
  done
  if [[ -z "$mode" ]]; then
    mode="paper"
    [[ "$(env_get QUANT_BROKER)" == "kis" ]] && kis_ready && mode="live"
  fi
  [[ "$(env_get QUANT_CORE_ONLY)" == "true" ]] && args+=(--core-only)
  qa cycle --mode "$mode" ${args[@]+"${args[@]}"}
}

compose() {
  if docker compose version >/dev/null 2>&1; then docker compose "$@"
  elif command -v docker-compose >/dev/null 2>&1; then docker-compose "$@"
  else die "Docker 가 필요합니다 (https://docs.docker.com/get-docker/)"
  fi
}

cmd_up() {
  ensure_env
  local pw; pw="$(env_get POSTGRES_PASSWORD)"
  if [[ -z "$pw" || "$pw" == "change-me" ]]; then
    pw="$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 24 || true)"
    env_replace POSTGRES_PASSWORD "$pw"
    ok "POSTGRES_PASSWORD 를 무작위로 생성해 .env 에 저장했습니다"
  fi
  compose up -d --build
  ok "대시보드: http://127.0.0.1:$PORT  ·  로그: ./run.sh logs"
}

cmd_test() {
  ensure_installed
  "$VENV/bin/ruff" check src tests
  "$VENV/bin/pytest" -q "$@"
}

usage() {  # 파일 첫머리 주석 블록만 출력 (코드 줄은 제외)
  awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$0"
}

main() {
  local cmd="${1:-auto}"; shift || true
  case "$cmd" in
    auto|start) cmd_auto ;;
    setup)      cmd_setup ;;
    data)       ensure_env; ensure_installed; ensure_db; cmd_data ;;
    doctor)     ensure_db; qa doctor "$@" ;;
    kis-check)  qa kis-check --test-order "$@" ;;
    cycle)      ensure_db; cmd_cycle "$@" ;;
    serve)      ensure_db; qa serve "$@" ;;
    orders)     qa orders "$@" ;;
    checkup)    qa checkup "$@" ;;
    cashflow)   qa cashflow "$@" ;;
    proof)      qa net-alpha "$@" ;;
    up)         cmd_up ;;
    down)       compose down ;;
    logs)       compose logs -f --tail 200 "$@" ;;
    test)       cmd_test "$@" ;;
    help|-h|--help) usage ;;
    *)          qa "$cmd" "$@" ;;
  esac
}

main "$@"
