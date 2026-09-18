#!/bin/bash
# 飞书日程创建脚本
# 通过 lark-cli 在飞书日历中创建私人日程
#
# 使用方式：
#   ./create_event.sh "日程标题" "2026-08-03T14:00+08:00" "2026-08-03T15:00+08:00" "描述"
#
# 或交互式：
#   ./create_event.sh

# 绕过代理
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

SUMMARY="${1:-}"
START="${2:-}"
END="${3:-}"
DESCRIPTION="${4:-}"

# 交互式输入
if [ -z "$SUMMARY" ]; then
    echo "===== 创建飞书日程 ====="
    echo ""
    read -p "日程标题: " SUMMARY
    read -p "开始时间 (如 2026-08-03T14:00+08:00): " START
    read -p "结束时间 (如 2026-08-03T15:00+08:00): " END
    read -p "日程描述 (可选): " DESCRIPTION
    echo ""
fi

# 参数校验
if [ -z "$SUMMARY" ] || [ -z "$START" ] || [ -z "$END" ]; then
    echo "错误: 缺少必要参数"
    echo "用法: $0 <标题> <开始时间> <结束时间> [描述]"
    echo "示例: $0 \"产品评审\" \"2026-08-03T14:00+08:00\" \"2026-08-03T15:00+08:00\" \"需求评审会议\""
    exit 1
fi

# 构建命令
CMD="lark-cli calendar +create --summary \"$SUMMARY\" --start \"$START\" --end \"$END\" --as user"

if [ -n "$DESCRIPTION" ]; then
    CMD="$CMD --description \"$DESCRIPTION\""
fi

echo "正在创建日程: $SUMMARY"
echo "时间: $START → $END"
echo ""

# 执行
eval $CMD --format json 2>&1

if [ $? -eq 0 ]; then
    echo ""
    echo "✅ 日程创建成功！"
else
    echo ""
    echo "❌ 日程创建失败，请检查参数和授权状态"
    echo "提示: 运行 lark-cli auth status --json --verify 检查授权"
fi
