#!/bin/bash
echo "🚀 正在为您唤醒 Local-Cron-Agent 系统..."

# 1. 启动后端
echo "[-] 启动 FastAPI Backend..."
nohup /Users/zanestear/miniconda3/envs/Local-Cron-Agent/bin/python -m uvicorn server:app --host 0.0.0.0 --port 8000 > backend.log 2>&1 &
echo $! > backend.pid
echo "    [✔] Backend 正在端口 8000 运行. (PID $(cat backend.pid))"

# 2. 启动前端
echo "[-] 启动 Vue3 Frontend..."
cd frontend
nohup npm run dev > ../frontend.log 2>&1 &
echo $! > ../frontend.pid
echo "    [✔] Frontend 正在端口 5173 运行. (PID $(cat ../frontend.pid))"
cd ..

echo ""
echo "🎉 全系统启动完毕！"
echo "🌐 请在浏览器中访问管理面: http://localhost:5173"
echo "💡 (如果想停止全部服务，请输入 ./stop.sh)"
