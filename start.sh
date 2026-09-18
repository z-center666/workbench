#!/bin/bash
# 个人工作台启动脚本
# 启动本地服务器并自动打开浏览器

DIR="$(cd "$(dirname "$0")" && pwd)"
PORT=8765

# 检查端口是否被占用
if lsof -i :$PORT >/dev/null 2>&1; then
    echo "工作台服务器已在运行"
    open "http://localhost:$PORT"
    exit 0
fi

echo "正在启动个人工作台..."

# 后台启动服务器
cd "$DIR"
python3 server.py &
SERVER_PID=$!

# 等待服务器启动
sleep 1

# 检查服务器是否启动成功
if kill -0 $SERVER_PID 2>/dev/null; then
    echo "服务器已启动 (PID: $SERVER_PID)"
    echo "正在打开浏览器..."
    open "http://localhost:$PORT"
    echo ""
    echo "工作台地址: http://localhost:$PORT"
    echo "按 Ctrl+C 停止服务器"
    echo ""
    wait $SERVER_PID
else
    echo "服务器启动失败，请检查 python3 是否可用"
    exit 1
fi
