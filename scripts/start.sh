#!/usr/bin/env bash
# Start the API and the web UI together and open the admin and player screens.
# Stop both with Ctrl+C (or by closing the terminal window).
#
#   ./scripts/start.sh          normal start
#   ./scripts/start.sh --dev    restart the API automatically when Python code changes
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
API_PORT="${COMMENTATOR_API_PORT:-8000}"
WEB_PORT="${COMMENTATOR_WEB_PORT:-5173}"
export COMMENTATOR_API_PORT="$API_PORT"  # the web dev server proxies /api here
RELOAD=()
[[ "${1:-}" == "--dev" ]] && RELOAD=(--reload)

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\n\033[31m%s\033[0m\n' "$*"; exit 1; }

# --- prerequisites -------------------------------------------------------
[[ -x .venv/bin/uvicorn ]] || fail "Python の環境がありません。README の「初回セットアップ」を実行してください。"
[[ -d node_modules ]] || fail "Node のパッケージがありません。README の「初回セットアップ」（npm install）を実行してください。"

if ! node -v 2>/dev/null | grep -q '^v22\.'; then
  # Switch to Node 22 with nvm when the shell has another version active.
  if [[ -s "${NVM_DIR:-$HOME/.nvm}/nvm.sh" ]]; then
    # shellcheck disable=SC1091
    . "${NVM_DIR:-$HOME/.nvm}/nvm.sh"
    nvm use >/dev/null || fail "Node 22 に切り替えられません。'nvm install' を実行してください。"
  else
    fail "Node 22 が必要です（今は $(node -v 2>/dev/null || echo 'なし')）。"
  fi
fi

port_in_use() { lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; }

open_screens() {
  [[ "${COMMENTATOR_NO_OPEN:-}" == 1 ]] && return 0
  open "http://127.0.0.1:${WEB_PORT}/"
  open "http://127.0.0.1:${WEB_PORT}/player"
}

if port_in_use "$API_PORT" && port_in_use "$WEB_PORT"; then
  say "すでに起動しています。画面だけ開きます。"
  open_screens
  exit 0
fi
port_in_use "$API_PORT" && fail "ポート ${API_PORT} を別のプログラムが使っています。前に起動したターミナルを閉じてから、もう一度実行してください。"
port_in_use "$WEB_PORT" && fail "ポート ${WEB_PORT} を別のプログラムが使っています。前に起動したターミナルを閉じてから、もう一度実行してください。"

# --- start ---------------------------------------------------------------
PIDS=()
cleanup() {
  trap - INT TERM HUP EXIT
  say "停止しています…"
  for pid in ${PIDS[@]+"${PIDS[@]}"}; do kill "$pid" 2>/dev/null || true; done
  wait 2>/dev/null || true
  echo "停止しました。"
}
trap cleanup INT TERM HUP EXIT

say "API を起動しています（ポート ${API_PORT}）…"
.venv/bin/uvicorn commentator.main:create_production_app --factory \
  --app-dir apps/server/src --port "$API_PORT" --no-access-log ${RELOAD[@]+"${RELOAD[@]}"} &
PIDS+=($!)

say "画面（Web UI）を起動しています（ポート ${WEB_PORT}）…"
(cd apps/web && exec "$ROOT/node_modules/.bin/vite" --port "$WEB_PORT" --strictPort >/dev/null) &
PIDS+=($!)

# Wait until both answer, then open the screens.
for _ in $(seq 1 60); do
  if curl -fs "http://127.0.0.1:${API_PORT}/health" >/dev/null 2>&1 \
    && curl -fs "http://127.0.0.1:${WEB_PORT}/" >/dev/null 2>&1; then
    say "起動しました。管理者画面とプレイヤー画面を開きます。"
    echo "  管理者画面:     http://127.0.0.1:${WEB_PORT}/"
    echo "  プレイヤー画面: http://127.0.0.1:${WEB_PORT}/player"
    echo
    echo "終了するときは、このウィンドウで Ctrl+C を押すか、ウィンドウを閉じてください。"
    open_screens
    wait
    exit 0
  fi
  for pid in "${PIDS[@]}"; do
    kill -0 "$pid" 2>/dev/null || fail "起動に失敗しました。上に出ているエラーを確認してください。"
  done
  sleep 0.5
done
fail "30秒たっても起動しませんでした。上に出ているエラーを確認してください。"
