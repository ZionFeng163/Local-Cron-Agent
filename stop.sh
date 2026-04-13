#!/bin/bash
echo "🛑 正在为您安全关闭 Local-Cron-Agent 系统..."

# 1. 停止后端
if [ -f backend.pid ]; then
    PID=$(cat backend.pid)
    # 给 Uvicorn 发送平滑结信令
    kill -SIGTERM $PID 2>/dev/null
    rm backend.pid
    echo "    [✔] Backend (PID: $PID) 已优雅关闭."
else
    echo "    [!] 未找到 backend.pid，前端或因直接退出而未记录."
fi

# 2. 停止前端
if [ -f frontend.pid ]; then
    PID=$(cat frontend.pid)
    kill -SIGTERM $PID 2>/dev/null
    rm frontend.pid
    echo "    [✔] Frontend (PID: $PID) 已关闭."
else
    echo "    [!] 未找到 frontend.pid，您可以尝试使用 'lsof -i :5173' 手动排查."
fi

echo ""
echo "👋 系统已彻底休眠，期待下次见面！"
