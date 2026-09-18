#!/bin/bash
# 知识库收件箱 - 启动脚本
# 用法: ./start_inbox.sh [start|stop|status|restart|install|uninstall]

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$SCRIPT_DIR/.knowledge_inbox.pid"
LOG_FILE="$SCRIPT_DIR/.knowledge_inbox.log"
PLIST_NAME="com.winter.knowledge-inbox"
PLIST_PATH="$HOME/Library/LaunchAgents/${PLIST_NAME}.plist"

start() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "知识库收件箱服务已在运行 (PID: $(cat $PID_FILE))"
        return 0
    fi
    
    echo "启动知识库收件箱服务..."
    nohup python3 -u "$SCRIPT_DIR/knowledge_inbox.py" >> "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    echo "服务已启动 (PID: $!)"
    echo "日志: tail -f $LOG_FILE"
}

stop() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID" 2>/dev/null
            # 也杀掉子进程 (lark-cli event consumer)
            pkill -P "$PID" 2>/dev/null
            sleep 1
            kill -9 "$PID" 2>/dev/null
            echo "服务已停止"
        else
            echo "服务未在运行"
        fi
        rm -f "$PID_FILE"
    else
        echo "服务未在运行"
    fi
}

status() {
    if [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "知识库收件箱服务正在运行 (PID: $(cat $PID_FILE))"
        echo "日志最后5行:"
        tail -5 "$LOG_FILE" 2>/dev/null
    else
        echo "知识库收件箱服务未在运行"
    fi
}

install_autostart() {
    echo "安装开机自启..."
    # 先卸载旧的
    launchctl bootout "gui/$(id -u)/$PLIST_NAME" 2>/dev/null
    
    # 加载新的
    launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH" 2>/dev/null
    if [ $? -eq 0 ]; then
        echo "开机自启已启用。每次登录 macOS 后服务自动启动。"
        echo "plist: $PLIST_PATH"
    else
        echo "安装失败，请手动运行: launchctl bootstrap gui/$(id -u) $PLIST_PATH"
    fi
}

uninstall_autostart() {
    echo "卸载开机自启..."
    launchctl bootout "gui/$(id -u)/$PLIST_NAME" 2>/dev/null
    rm -f "$PLIST_PATH"
    echo "开机自启已卸载。服务仍可手动运行: $0 start"
}

case "${1:-start}" in
    start)     start ;;
    stop)      stop ;;
    status)    status ;;
    restart)   stop; sleep 1; start ;;
    install)   install_autostart ;;
    uninstall) uninstall_autostart ;;
    *) echo "用法: $0 {start|stop|status|restart|install|uninstall}" ;;
esac
