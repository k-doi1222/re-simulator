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

case "${1:-status}" in
  start)
    if is_up; then echo "すでに起動しています → http://localhost:$PORT"; exit 0; fi
    cd "$APP_DIR" || exit 1
    # uv 経由だと依存解決が走って更に遅くなるので、venv の python を直接呼ぶ。
    # OneDrive 上のフォルダなので、同期中は import に数分かかることがある。
    nohup "$APP_DIR/.venv/bin/python" -m streamlit run app.py \
          --server.port "$PORT" --server.headless true \
          > "$LOG" 2>&1 &
    echo $! > "$PID_FILE"
    disown
    # ここで起動完了を待たないこと。待つとこのスクリプトを呼んだシェルが居座り、
    # Claude Code のセッションが「作業中」の表示のままになってしまう。
    # 立ち上がったかは `./serve.sh status` で確認する。
    echo "起動を指示しました → http://localhost:$PORT"
    echo "（OneDrive同期中は立ち上がるまで数分かかります。'./serve.sh status' で確認）"
    ;;
  stop)
    pkill -f "streamlit run app.py" 2>/dev/null
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
