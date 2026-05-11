#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

BACKEND_PORT=8000
FRONTEND_PORT=5173
BACKEND_LOG="$ROOT_DIR/backend.log"
FRONTEND_LOG="$ROOT_DIR/frontend.log"
STARTUP_TIMEOUT_SEC=60

is_listening() {
  local port="$1"
  lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
}

listener_pid() {
  local port="$1"
  lsof -nP -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | head -n 1
}

kill_pidfile_if_alive() {
  local pidfile="$1"
  if [ -f "$pidfile" ]; then
    local pid
    pid="$(cat "$pidfile" 2>/dev/null || true)"
    if [ -n "${pid:-}" ] && ps -p "$pid" >/dev/null 2>&1; then
      kill -TERM "$pid" 2>/dev/null || true
      for _ in {1..15}; do
        if ps -p "$pid" >/dev/null 2>&1; then
          sleep 0.2
        else
          break
        fi
      done
      if ps -p "$pid" >/dev/null 2>&1; then
        kill -KILL "$pid" 2>/dev/null || true
      fi
    fi
    rm -f "$pidfile"
  fi
}

kill_port_listener() {
  local port="$1"
  local lp
  lp="$(listener_pid "$port" || true)"
  if [ -n "${lp:-}" ]; then
    kill -TERM "$lp" 2>/dev/null || true
    sleep 0.3
    if is_listening "$port"; then
      kill -KILL "$lp" 2>/dev/null || true
    fi
  fi
}

echo "🚀 正在为您唤醒 Local-Cron-Agent 系统..."

echo "[-] 清理旧进程与占用端口..."
kill_pidfile_if_alive "$ROOT_DIR/backend.pid"
kill_pidfile_if_alive "$ROOT_DIR/frontend.pid"
kill_port_listener "$BACKEND_PORT"
kill_port_listener "$FRONTEND_PORT"

echo "[-] 启动 FastAPI Backend..."
nohup /Users/zanestear/miniconda3/envs/Local-Cron-Agent/bin/python -m uvicorn server:app --host 0.0.0.0 --port "$BACKEND_PORT" > "$BACKEND_LOG" 2>&1 &
for _ in $(seq 1 "$STARTUP_TIMEOUT_SEC"); do
  if is_listening "$BACKEND_PORT"; then
    break
  fi
  sleep 1
done
if ! is_listening "$BACKEND_PORT"; then
  echo "    [✘] Backend 启动失败，最近日志如下："
  tail -n 40 "$BACKEND_LOG" || true
  exit 1
fi
BACKEND_PID="$(listener_pid "$BACKEND_PORT")"
echo "$BACKEND_PID" > "$ROOT_DIR/backend.pid"
echo "    [✔] Backend 正在端口 $BACKEND_PORT 运行. (PID $BACKEND_PID)"

echo "[-] 启动 Vue3 Frontend..."
cd "$ROOT_DIR/frontend"
nohup npm run dev -- --host 0.0.0.0 --port "$FRONTEND_PORT" > "$FRONTEND_LOG" 2>&1 &
cd "$ROOT_DIR"
for _ in $(seq 1 20); do
  if is_listening "$FRONTEND_PORT"; then
    break
  fi
  sleep 0.5
done
if ! is_listening "$FRONTEND_PORT"; then
  echo "    [✘] Frontend 启动失败，最近日志如下："
  tail -n 40 "$FRONTEND_LOG" || true
  exit 1
fi
FRONTEND_PID="$(listener_pid "$FRONTEND_PORT")"
echo "$FRONTEND_PID" > "$ROOT_DIR/frontend.pid"
echo "    [✔] Frontend 正在端口 $FRONTEND_PORT 运行. (PID $FRONTEND_PID)"

echo ""
echo "🎉 全系统启动完毕！"
echo "🌐 请在浏览器中访问管理面: http://localhost:$FRONTEND_PORT"
echo "💡 (如果想停止全部服务，请输入 ./stop.sh)"
