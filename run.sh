#!/usr/bin/env bash
# Quant AI 실행 도우미 — 설치부터 매일 운영까지 한 곳에서.
#
#   ./run.sh setup            처음 한 번: 가상환경 · 패키지 설치 · .env 만들기
#   ./run.sh data             KRX 주가 데이터 받기/갱신 (FinanceData/marcap) + DB 적재
#   ./run.sh doctor [--ai --kis --notify]   실행 전 점검 (키·DB·데이터·AI·증권사·알림)
#   ./run.sh kis-check        KIS 연결 점검 + 모의 1주 주문→취소 (모의투자에서만)
#   ./run.sh cycle            코어(-위성) 1회 실행 (QUANT_BROKER=kis 면 KIS 로 주문)
#   ./run.sh start            24시간 스케줄러 (장중 매매 · 장외 데이터 갱신 · 복기 · 건강검진)
#   ./run.sh serve            대시보드 http://127.0.0.1:8050
#   ./run.sh orders --cash 30000000 --holdings my.csv   수동 매매용 주문표
#   ./run.sh checkup          전략 건강검진
#   ./run.sh cashflow 5000000 입금 기록 (출금은 음수)
#   ./run.sh up | down | logs Docker 로 상시 운영 (PostgreSQL + 스케줄러 + 대시보드)
#   ./run.sh test             테스트 + 린트
#   ./run.sh <그 외>           quant-ai <그 외> 로 그대로 전달 (예: ./run.sh kill on)
set -euo pipefail

cd "$(dirname "$0")"
ROOT="$(pwd)"
VENV="$ROOT/.venv"
DATA_DIR="${DATA_DIR:-$ROOT/data}"

say()  { printf '\033[1;34m▶ %s\033[0m\n' "$*"; }
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

qa() {
  if [[ -x "$VENV/bin/quant-ai" ]]; then "$VENV/bin/quant-ai" "$@"
  elif command -v quant-ai >/dev/null 2>&1; then quant-ai "$@"
  else die "quant-ai 가 설치되지 않았습니다 → ./run.sh setup"
  fi
}

env_get() {  # .env 에서 값 읽기 (따옴표·줄 끝 주석 제거). 셸 환경변수가 있으면 그것이 우선
  local key="$1" val="${!1:-}"
  if [[ -z "$val" && -f .env ]]; then
    val="$(grep -E "^${key}=" .env | tail -1 | cut -d= -f2- | sed -E 's/[[:space:]]+#.*$//; s/^["'\'']//; s/["'\'']$//')"
  fi
  printf '%s' "$val"
}

env_set() {  # .env 의 KEY= 가 비어 있을 때만 채운다 (사용자가 넣은 값은 건드리지 않음)
  local key="$1" val="$2"
  [[ -f .env ]] || return 0
  if grep -qE "^${key}=$" .env; then
    local tmp; tmp="$(mktemp)"
    awk -v k="$key" -v v="$val" 'BEGIN{FS=OFS="="} $1==k && $2=="" {print k "=" v; next} {print}' .env > "$tmp"
    cat "$tmp" > .env && rm -f "$tmp"
  elif ! grep -qE "^${key}=" .env; then
    printf '%s=%s\n' "$key" "$val" >> .env
  fi
}

cmd_setup() {
  local py; py="$(find_python)" || die "Python 3.11 이상이 필요합니다 (https://www.python.org/downloads/)"
  say "가상환경: $VENV ($($py --version))"
  [[ -d "$VENV" ]] || "$py" -m venv "$VENV"
  "$VENV/bin/pip" install -q --upgrade pip
  local extras="ai,dev"
  if [[ "$(env_get DATABASE_URL)" == postgres* ]]; then extras="$extras,postgres"; fi
  say "패키지 설치 (.[${extras}])"
  "$VENV/bin/pip" install -q -e ".[${extras}]"
  if [[ ! -f .env ]]; then
    cp .env.example .env
    chmod 600 .env
    # 처음에는 PostgreSQL 없이 바로 돌 수 있게 SQLite 로 시작
    sed -i.bak -E 's#^DATABASE_URL=.*#DATABASE_URL=sqlite:///quant_ai.db#' .env && rm -f .env.bak
    say ".env 생성 (권한 600). 키 발급처는 docs/ENV_KEYS.md"
  else
    say ".env 이미 있음 — 그대로 둡니다"
  fi
  cat <<'EOF'

다음 순서:
  1) .env 에 키 입력 (최소: 모의투자 KIS 키 3개 · 무료 AI 는 선택)
  2) ./run.sh data       # KRX 데이터 (처음 몇 분)
  3) ./run.sh doctor     # 점검
  4) ./run.sh kis-check  # 장중에: KIS 연결 + 모의 주문·취소
  5) ./run.sh cycle      # 코어 1회
  6) ./run.sh start      # 24시간 (또는 ./run.sh up 으로 Docker)
EOF
}

cmd_data() {
  command -v git >/dev/null 2>&1 || die "git 이 필요합니다"
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
    say "marcap 받기 → $repo (최근 5년만, 수백 MB)"
    mkdir -p "$(dirname "$repo")"
    git clone --depth 1 --filter=blob:none --sparse https://github.com/FinanceData/marcap.git "$repo"
  else
    say "marcap 갱신 (git pull)"
    git -C "$repo" pull --ff-only -q || warn "git pull 실패 — 기존 데이터로 계속"
  fi
  git -C "$repo" sparse-checkout set --no-cone "${files[@]}"
  say "DB 적재 (시가총액 상위 100, 최근 3년)"
  QUANT_MARCAP_DIR="$mdir" qa collect krx --marcap-dir "$mdir" --years 3 --top 100
}

cmd_cycle() {
  local mode="paper"
  [[ "$(env_get QUANT_BROKER)" == "kis" ]] && mode="live"
  local extra=()
  [[ "$(env_get QUANT_CORE_ONLY)" == "true" ]] && extra+=(--core-only)
  say "코어 사이클: --mode $mode ${extra[*]:-}"
  qa cycle --mode "$mode" ${extra[@]+"${extra[@]}"} "$@"
}

cmd_start() {
  local mode; mode="$(env_get QUANT_MODE)"; mode="${mode:-paper}"
  [[ "$(env_get QUANT_BROKER)" == "kis" && "$mode" != "live" ]] && warn "QUANT_BROKER=kis 인데 QUANT_MODE=$mode — KIS 로 주문하려면 QUANT_MODE=live"
  qa doctor || die "점검 실패 — 위 ❌ 항목을 먼저 해결하세요"
  say "스케줄러 시작 (--mode $mode). 종료: Ctrl+C"
  qa run --mode "$mode"
}

compose() {
  if docker compose version >/dev/null 2>&1; then docker compose "$@"
  elif command -v docker-compose >/dev/null 2>&1; then docker-compose "$@"
  else die "Docker 가 필요합니다 (https://docs.docker.com/get-docker/)"
  fi
}

cmd_up() {
  [[ -f .env ]] || die ".env 없음 → ./run.sh setup"
  [[ "$(env_get POSTGRES_PASSWORD)" == "change-me" || -z "$(env_get POSTGRES_PASSWORD)" ]] && \
    die ".env 의 POSTGRES_PASSWORD 를 바꾸세요 (change-me 금지)"
  compose up -d --build
  say "대시보드: http://127.0.0.1:8050  ·  로그: ./run.sh logs"
}

cmd_test() {
  [[ -x "$VENV/bin/pytest" ]] || die "개발 도구 없음 → ./run.sh setup"
  "$VENV/bin/ruff" check src tests
  "$VENV/bin/pytest" -q "$@"
}

usage() {  # 파일 첫머리 주석 블록만 출력 (코드 줄은 제외)
  awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$0"
  if [[ ! -d "$VENV" ]]; then
    printf '\n처음이면: ./run.sh setup\n'
  elif [[ ! -f .env ]]; then
    printf '\n다음: cp .env.example .env 후 키 입력 (docs/ENV_KEYS.md)\n'
  fi
}

main() {
  local cmd="${1:-help}"; shift || true
  case "$cmd" in
    setup)      cmd_setup ;;
    data)       cmd_data ;;
    doctor)     qa doctor "$@" ;;
    kis-check)  qa kis-check --test-order "$@" ;;
    cycle)      cmd_cycle "$@" ;;
    start)      cmd_start ;;
    serve)      qa serve "$@" ;;
    orders)     qa orders "$@" ;;
    checkup)    qa checkup "$@" ;;
    cashflow)   qa cashflow "$@" ;;
    up)         cmd_up ;;
    down)       compose down ;;
    logs)       compose logs -f --tail 200 "$@" ;;
    test)       cmd_test "$@" ;;
    help|-h|--help) usage ;;
    *)          qa "$cmd" "$@" ;;
  esac
}

main "$@"
