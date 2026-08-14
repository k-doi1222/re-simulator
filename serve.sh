#!/bin/bash
# 手元でアプリを動かすための起動・停止スクリプト。
#
# Claude Code のバックグラウンドタスクとして起動すると、サーバーが動いている間ずっと
# セッションが「作業中」の表示のままになる。それを避けるため、ここでは nohup + disown で
# 完全に切り離したプロセスとして起動する。ターミナルを閉じても動き続ける。
#
#   ./serve.sh start    起動
#   ./serve.sh stop     停止
#   ./serve.sh status   稼働確認
#   ./serve.sh log      ログの末尾を表示

set -u
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
PORT=8501
LOG="$APP_DIR/.streamlit_server.log"
PID_FILE="$APP_DIR/.streamlit_server.pid"

is_up() { curl -sf -o /dev/null "http://localhost:$PORT/_stcore/health"; }

# venv の python の場所はOSで違う（macOS/Linux は bin/、Windows は Scripts/）。
# どちらでも動くよう、実在する方を使う。
venv_python() {
  for p in "$APP_DIR/.venv/bin/python" "$APP_DIR/.venv/Scripts/python.exe"; do
    if [ -x "$p" ]; then echo "$p"; return 0; fi
  done
  return 1
}

case "${1:-status}" in
  start)
    if is_up; then echo "すでに起動しています → http://localhost:$PORT"; exit 0; fi
    cd "$APP_DIR" || exit 1
    # uv 経由だと依存解決が走って更に遅くなるので、venv の python を直接呼ぶ。
    PY=$(venv_python) || {
      echo ".venv が見つかりません。先に 'uv sync' を実行してください"
      exit 1
    }
    nohup "$PY" -m streamlit run app.py \
          --server.port "$PORT" --server.headless true \
          > "$LOG" 2>&1 &
    echo $! > "$PID_FILE"
    disown
    # ここで起動完了を待たないこと。待つとこのスクリプトを呼んだシェルが居座り、
    # Claude Code のセッションが「作業中」の表示のままになってしまう。
    # 立ち上がったかは `./serve.sh status` で確認する。
    echo "起動を指示しました → http://localhost:$PORT"
    echo "（'./serve.sh status' で確認）"
    ;;
  stop)
    # まず起動時に記録したPIDを狙う。Windows(Git Bash)の pkill は
    # プロセスのコマンドラインまで見られないことがあるため、PIDでの停止を優先する。
    if [ -f "$PID_FILE" ] && kill "$(cat "$PID_FILE")" 2>/dev/null; then
      :
    else
      pkill -f "streamlit run app.py" 2>/dev/null
    fi
    rm -f "$PID_FILE"
    echo "停止しました"
    ;;
  status)
    if is_up; then echo "稼働中 → http://localhost:$PORT"; else echo "停止中"; fi
    ;;
  log)
    tail -"${2:-30}" "$LOG"
    ;;
  *)
    echo "使い方: $0 {start|stop|status|log}"; exit 1
    ;;
esac
