#!/bin/bash
# ============================================
# 个人工作台 - 一键启动脚本
# 集成：服务器 + 知识库收件箱 + 环境配置
# 用法：./workbench.sh [start|stop|restart|status]
# ============================================

set -e

# --- 路径与常量 ---
DIR="$(cd "$(dirname "$0")" && pwd)"
PORT=8765
SERVER_LOG="$DIR/.server.log"
INBOX_LOG="$DIR/.knowledge_inbox.log"
SERVER_PID_FILE="$DIR/.server.pid"
INBOX_PID_FILE="$DIR/.knowledge_inbox.pid"

# --- 环境配置 ---
# Node.js / lark-cli 路径（本地安装的 Node.js 二进制包）
NODE_BIN="$HOME/.local/node/bin"
if [ -d "$NODE_BIN" ]; then
    export PATH="$NODE_BIN:$PATH"
fi

# 绕过代理（lark-cli 直连飞书 API）
export HTTP_PROXY=""
export HTTPS_PROXY=""
export http_proxy=""
export https_proxy=""
export ALL_PROXY=""
export all_proxy=""
export NO_PROXY="*"
export no_proxy="*"
export LARKSUITE_CLI_NO_UPDATE_NOTIFIER=1
export LARKSUITE_CLI_NO_SKILLS_NOTIFIER=1

# --- 辅助函数 ---
is_running() {
    local pid_file="$1"
    [ -f "$pid_file" ] && kill -0 "$(cat "$pid_file")" 2>/dev/null
}

port_in_use() {
    lsof -i :$PORT >/dev/null 2>&1
}

# --- 启动服务器 ---
start_server() {
    if port_in_use; then
        echo "  ✅ 服务器已在运行 (端口 :$PORT)"
        return 0
    fi
    cd "$DIR"
    nohup python3 server.py > "$SERVER_LOG" 2>&1 &
    echo $! > "$SERVER_PID_FILE"
    sleep 1.5
    if port_in_use; then
        echo "  ✅ 服务器已启动 (PID: $(cat "$SERVER_PID_FILE"))"
    else
        echo "  ❌ 服务器启动失败，查看日志：$SERVER_LOG"
        return 1
    fi
}

# --- 启动知识库收件箱 ---
start_inbox() {
    if is_running "$INBOX_PID_FILE"; then
        echo "  ✅ 知识库收件箱已在运行 (PID: $(cat "$INBOX_PID_FILE"))"
        return 0
    fi
    cd "$DIR"
    nohup python3 -u knowledge_inbox.py >> "$INBOX_LOG" 2>&1 &
    echo $! > "$INBOX_PID_FILE"
    sleep 0.5
    if is_running "$INBOX_PID_FILE"; then
        echo "  ✅ 知识库收件箱已启动 (PID: $(cat "$INBOX_PID_FILE"))"
    else
        echo "  ❌ 知识库收件箱启动失败，查看日志：$INBOX_LOG"
    fi
}

# --- 停止服务 ---
stop_service() {
    local name="$1"
    local pid_file="$2"
    if is_running "$pid_file"; then
        local pid=$(cat "$pid_file")
        kill "$pid" 2>/dev/null
        sleep 1
        if kill -0 "$pid" 2>/dev/null; then
            kill -9 "$pid" 2>/dev/null
            sleep 0.5
        fi
        echo "  🛑 $name 已停止 (PID: $pid)"
        rm -f "$pid_file"
    else
        echo "  ⚪ $name 未在运行"
        rm -f "$pid_file" 2>/dev/null
    fi
}

# --- 查看状态 ---
show_status() {
    echo "📊 工作台状态"
    echo "─────────────────────────────"
    # 服务器
    if port_in_use; then
        _pid=$(cat "$SERVER_PID_FILE" 2>/dev/null || echo "?")
        _health=$(curl -s "http://localhost:$PORT/api/health" 2>/dev/null)
        if echo "$_health" | grep -q '"ok": true'; then
            echo "  服务器:        ✅ 运行中 (PID: $_pid)"
        else
            echo "  服务器:        ⚠️  端口占用但健康检查失败"
        fi
    else
        echo "  服务器:        ⚪ 未运行"
    fi
    # 知识库收件箱
    if is_running "$INBOX_PID_FILE"; then
        echo "  知识库收件箱:  ✅ 运行中 (PID: $(cat "$INBOX_PID_FILE"))"
    else
        echo "  知识库收件箱:  ⚪ 未运行"
    fi
    # lark-cli
    if command -v lark-cli >/dev/null 2>&1; then
        echo "  lark-cli:      ✅ $(lark-cli --version 2>/dev/null || echo '已安装')"
    else
        echo "  lark-cli:      ❌ 未安装（日历功能不可用）"
    fi
    echo "─────────────────────────────"
    echo "  工作台地址:    http://localhost:$PORT"
}

# --- 主逻辑 ---
case "${1:-start}" in

    start)
        echo "🚀 启动个人工作台..."
        echo ""
        start_server
        start_inbox
        echo ""
        # 健康检查
        if port_in_use; then
            _health=$(curl -s "http://localhost:$PORT/api/health" 2>/dev/null)
            if echo "$_health" | grep -q '"ok": true'; then
                echo "  ✅ 健康检查通过"
            fi
        fi
        echo ""
        echo "📌 工作台地址: http://localhost:$PORT"
        echo "📌 按 Ctrl+C 退出（后台服务不受影响）"
        echo ""
        # 打开浏览器
        open "http://localhost:$PORT" 2>/dev/null || true
        ;;

    stop)
        echo "🛑 停止个人工作台..."
        stop_service "服务器" "$SERVER_PID_FILE"
        stop_service "知识库收件箱" "$INBOX_PID_FILE"
        echo ""
        echo "  已全部停止。"
        ;;

    restart)
        echo "🔄 重启个人工作台..."
        "$0" stop
        sleep 1
        echo ""
        "$0" start
        ;;

    status)
        show_status
        ;;

    *)
        echo "用法: $0 {start|stop|restart|status}"
        echo ""
        echo "  start    启动工作台（服务器 + 知识库收件箱 + 打开浏览器）"
        echo "  stop     停止所有服务"
        echo "  restart  重启所有服务"
        echo "  status   查看运行状态"
        exit 1
        ;;

esac
