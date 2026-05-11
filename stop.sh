#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

BACKEND_PORT=8000
FRONTEND_PORT=5173

is_listening() {
  local port="$1"
  lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
}

listener_pid() {
  local port="$1"
  lsof -nP -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | head -n 1
}

stop_pidfile() {
  local name="$1"
  local pidfile="$2"
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
      echo "    [✔] $name (PID: $pid) 已关闭."
    else
      echo "    [!] $name pid 文件存在但进程不在: $pid"
    fi
    rm -f "$pidfile"
  else
    echo "    [!] 未找到 $pidfile"
  fi
}

stop_port_listener() {
  local name="$1"
  local port="$2"
  local pid
  pid="$(listener_pid "$port" || true)"
  if [ -n "${pid:-}" ]; then
    kill -TERM "$pid" 2>/dev/null || true
    sleep 0.3
    if is_listening "$port"; then
      kill -KILL "$pid" 2>/dev/null || true
    fi
    echo "    [✔] 已清理 $name 端口占用 :$port (PID $pid)"
  fi
}

echo "🛑 正在为您安全关闭 Local-Cron-Agent 系统..."

stop_pidfile "Backend" "$ROOT_DIR/backend.pid"
stop_pidfile "Frontend" "$ROOT_DIR/frontend.pid"

stop_port_listener "Backend" "$BACKEND_PORT"
stop_port_listener "Frontend" "$FRONTEND_PORT"

echo ""
echo "👋 系统已彻底休眠，期待下次见面！"
