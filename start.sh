#!/usr/bin/env bash
#
# Start the Rox pipeline locally: FastAPI on :8000, Next.js on :3000.
#
#   ./start.sh              # both services
#   ./start.sh --backend    # API only
#   ./start.sh --frontend   # UI only (expects the API already running)
#   ./start.sh --types      # regenerate the typed client, then both
#
# Ctrl-C stops everything it started.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
API_PORT=8000
UI_PORT=3000

RUN_BACKEND=1
RUN_FRONTEND=1
REGEN_TYPES=0

for arg in "$@"; do
  case "$arg" in
    --backend)  RUN_FRONTEND=0 ;;
    --frontend) RUN_BACKEND=0 ;;
    --types)    REGEN_TYPES=1 ;;
    -h|--help)  sed -n '3,11p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg (try --help)" >&2; exit 2 ;;
  esac
done

bold() { printf '\033[1m%s\033[0m\n' "$1"; }
warn() { printf '\033[33m%s\033[0m\n' "$1" >&2; }
die()  { printf '\033[31merror: %s\033[0m\n' "$1" >&2; exit 1; }

# ── shutdown ────────────────────────────────────────────────────────────────
# Kill only what this script started, and only once — a second Ctrl-C during
# teardown would otherwise re-enter the trap.
PIDS=()

# Signal the whole process *group*, not the direct child. `uvicorn --reload`
# runs a reloader parent plus a worker, and `npm run dev` forks `next dev`;
# killing only the pid we launched leaves those grandchildren holding the
# ports. `set -m` below puts each background job in its own group so the
# negative-pid form reaches all of them.
stop_children() {
  [ ${#PIDS[@]} -eq 0 ] && return
  echo
  bold "stopping…"
  for pid in "${PIDS[@]}"; do
    kill -TERM -"$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
  done
  # Give them a moment to close listeners, then insist.
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    pgrep -g "${PIDS[0]}" >/dev/null 2>&1 || break
    sleep 0.3
  done
  for pid in "${PIDS[@]}"; do
    kill -KILL -"$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}

# Preserve the failing status. Exiting 0 here would make a failed preflight
# look like a clean start to anything chaining on `./start.sh && …`.
on_exit() {
  local code=$?
  trap - INT TERM EXIT
  stop_children
  exit "$code"
}

# Ctrl-C is a deliberate stop, not a failure.
on_interrupt() {
  trap - INT TERM EXIT
  stop_children
  exit 0
}

trap on_exit EXIT
trap on_interrupt INT TERM

port_busy() { lsof -i ":$1" -sTCP:LISTEN -t >/dev/null 2>&1; }

# Poll rather than sleep a fixed amount: a cold Next build takes far longer
# than a warm one, and a fixed sleep is either too slow or a race.
wait_for() {
  local url="$1" name="$2" tries=${3:-90}
  for _ in $(seq 1 "$tries"); do
    if curl -sf -o /dev/null "$url" 2>/dev/null; then return 0; fi
    sleep 1
  done
  die "$name did not come up at $url — check the log above"
}

# ── preflight ───────────────────────────────────────────────────────────────
if [ "$RUN_BACKEND" = 1 ]; then
  [ -x "$BACKEND/.venv/bin/uvicorn" ] || die \
    "backend venv missing. Run:
       cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"

  if [ ! -f "$BACKEND/.env" ]; then
    die "backend/.env missing. Run: cp backend/.env.example backend/.env  (then set ROX_API_TOKEN)"
  fi
  # The app starts without a token but every Rox call 401s, which surfaces as
  # empty research rather than an obvious failure — so say so up front.
  grep -qE '^ROX_API_TOKEN=.+' "$BACKEND/.env" || warn \
    "warning: ROX_API_TOKEN is unset in backend/.env — research and prospecting will fail"

  port_busy "$API_PORT" && die "port $API_PORT is already in use (another uvicorn?)"
fi

if [ "$RUN_FRONTEND" = 1 ]; then
  # nvm is a shell function, not a binary, so it has to be sourced.
  if [ -s "${NVM_DIR:-$HOME/.nvm}/nvm.sh" ]; then
    # shellcheck disable=SC1091
    . "${NVM_DIR:-$HOME/.nvm}/nvm.sh"
    (cd "$FRONTEND" && nvm use >/dev/null 2>&1) || true
    [ -f "$FRONTEND/.nvmrc" ] && nvm use "$(cat "$FRONTEND/.nvmrc")" >/dev/null 2>&1 || true
  fi

  command -v node >/dev/null || die "node not found on PATH"
  NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
  # Next 16 needs 18.18+; this repo pins 22 in frontend/.nvmrc.
  [ "$NODE_MAJOR" -ge 18 ] || die \
    "node $(node -v) is too old for Next.js — run 'nvm use' in frontend/ (needs $(cat "$FRONTEND/.nvmrc" 2>/dev/null || echo 18+))"

  [ -d "$FRONTEND/node_modules" ] || die "frontend deps missing. Run: cd frontend && npm install"
  [ -f "$FRONTEND/.env.local" ] || warn \
    "warning: frontend/.env.local missing — defaulting NEXT_PUBLIC_API_URL to http://localhost:$API_PORT"

  port_busy "$UI_PORT" && die "port $UI_PORT is already in use (another next dev?)"
fi

# ── start ───────────────────────────────────────────────────────────────────
# Job control, so each service below lands in its own process group and can be
# torn down as a unit.
set -m
if [ "$RUN_BACKEND" = 1 ]; then
  bold "starting API on :$API_PORT"
  ( cd "$BACKEND" && exec .venv/bin/uvicorn app.main:app --port "$API_PORT" --reload ) &
  PIDS+=($!)
  wait_for "http://localhost:$API_PORT/health" "API"
  echo "  API      http://localhost:$API_PORT  (docs at /docs)"
fi

if [ "$REGEN_TYPES" = 1 ]; then
  # Needs the API up — it reads the live spec, not a checked-in copy.
  bold "regenerating typed client from the live spec"
  ( cd "$FRONTEND" && npm run api:types )
fi

if [ "$RUN_FRONTEND" = 1 ]; then
  bold "starting UI on :$UI_PORT"
  ( cd "$FRONTEND" && exec npm run dev ) &
  PIDS+=($!)
  wait_for "http://localhost:$UI_PORT" "UI" 120
  echo "  UI       http://localhost:$UI_PORT"
fi

echo
bold "ready — Ctrl-C to stop"
wait
