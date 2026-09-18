#!/bin/bash
# ============================================
# 将工作台打包为 macOS .app 应用
# 用法：cd ~/Documents/工作台 && bash build_app.sh
# 生成：工作台.app（可拖到 Applications 或登录项）
# ============================================

set -e

SOURCE_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="工作台"
APP_DIR="$SOURCE_DIR/${APP_NAME}.app"

echo "📦 正在打包 ${APP_NAME}.app ..."

# 清理旧的
rm -rf "$APP_DIR"
mkdir -p "$APP_DIR/Contents/MacOS"
mkdir -p "$APP_DIR/Contents/Resources"

# --- 1. 可执行启动脚本 ---
cat > "$APP_DIR/Contents/MacOS/${APP_NAME}" << 'LAUNCHER'
#!/bin/bash
# 工作台.app 启动器

REAL_DIR="$HOME/Documents/工作台"
if [ -d "$REAL_DIR" ]; then
    cd "$REAL_DIR"
else
    echo "错误：找不到工作台目录"
    exit 1
fi

PORT=8765
NODE_BIN="$HOME/.local/node/bin"
[ -d "$NODE_BIN" ] && export PATH="$NODE_BIN:$PATH"

# 绕过代理
export HTTP_PROXY="" HTTPS_PROXY="" http_proxy="" https_proxy=""
export ALL_PROXY="" all_proxy="" NO_PROXY="*" no_proxy="*"
export LARKSUITE_CLI_NO_UPDATE_NOTIFIER=1
export LARKSUITE_CLI_NO_SKILLS_NOTIFIER=1

# 停止旧进程
if [ -f .server.pid ] && kill -0 "$(cat .server.pid)" 2>/dev/null; then
    kill "$(cat .server.pid)" 2>/dev/null
    sleep 1
fi
if lsof -i :$PORT >/dev/null 2>&1; then
    kill "$(lsof -ti :$PORT)" 2>/dev/null
    sleep 1
fi

# 启动服务器
nohup python3 server.py > .server.log 2>&1 &
echo $! > .server.pid
sleep 1.5

# 启动知识库收件箱
if [ -f knowledge_inbox.py ]; then
    if [ -f .knowledge_inbox.pid ] && kill -0 "$(cat .knowledge_inbox.pid)" 2>/dev/null; then
        : # 已在运行
    else
        nohup python3 -u knowledge_inbox.py >> .knowledge_inbox.log 2>&1 &
        echo $! > .knowledge_inbox.pid
    fi
fi

# 等待服务器就绪
for i in $(seq 1 10); do
    if curl -s "http://localhost:$PORT/api/health" 2>/dev/null | grep -q '"ok": true'; then
        break
    fi
    sleep 0.5
done

# 打开浏览器
open "http://localhost:$PORT"
LAUNCHER

chmod +x "$APP_DIR/Contents/MacOS/${APP_NAME}"

# --- 2. Info.plist ---
cat > "$APP_DIR/Contents/Info.plist" << PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>工作台</string>
    <key>CFBundleDisplayName</key>
    <string>工作台</string>
    <key>CFBundleIdentifier</key>
    <string>com.winter.workbench</string>
    <key>CFBundleVersion</key>
    <string>1.0</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleExecutable</key>
    <string>${APP_NAME}</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>LSMinimumSystemVersion</key>
    <string>10.13</string>
    <key>LSUIElement</key>
    <true/>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
PLIST

# --- 3. 生成图标 ---
python3 << 'ICON'
from PIL import Image, ImageDraw, ImageFont
import os, subprocess

iconset = "/tmp/AppIcon.iconset"
os.makedirs(iconset, exist_ok=True)

sizes = [16, 32, 64, 128, 256, 512]
for size in sizes:
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    margin = int(size * 0.08)
    radius = int(size * 0.22)
    draw.rounded_rectangle(
        [margin, margin, size - margin, size - margin],
        radius=radius,
        fill=(58, 120, 255, 255)
    )
    try:
        font = ImageFont.truetype("/System/Library/Fonts/PingFang.ttc", int(size * 0.5))
    except:
        font = ImageFont.load_default()
    text = "工"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (size - tw) // 2 - bbox[0]
    y = (size - th) // 2 - bbox[1]
    draw.text((x, y), text, fill=(255, 255, 255, 255), font=font)
    img.save(f"/tmp/icon_{size}.png")

mappings = [
    (16, "icon_16x16"), (32, "icon_16x16@2x"),
    (32, "icon_32x32"), (64, "icon_32x32@2x"),
    (128, "icon_128x128"), (256, "icon_128x128@2x"),
    (256, "icon_256x256"), (512, "icon_256x256@2x"),
    (512, "icon_512x512"),
]
for size, name in mappings:
    src = f"/tmp/icon_{min(size, 512)}.png"
    subprocess.run(["cp", src, f"{iconset}/{name}.png"], check=True)

app_dir = os.environ.get('APP_DIR', '')
icon_path = os.path.join(app_dir, 'Contents', 'Resources', 'AppIcon.icns')
result = subprocess.run(["iconutil", "-c", "icns", iconset, "-o", icon_path], capture_output=True)
if result.returncode != 0:
    subprocess.run(["cp", "/tmp/icon_256.png", os.path.join(app_dir, 'Contents', 'Resources', 'AppIcon.png')])
    print("icon: fallback png")
else:
    print("icon: icns ok")
ICON

# --- 4. 完成 ---
echo ""
echo "✅ 打包完成！"
echo ""
echo "   📁 应用位置: $APP_DIR"
echo ""
echo "   双击「工作台.app」即可启动"
echo "   拖到 Applications 文件夹可当作正常 App 使用"
echo "   系统设置 → 通用 → 登录项 → 添加  可设为开机自启"
echo ""

# 验证
[ -x "$APP_DIR/Contents/MacOS/${APP_NAME}" ] && echo "   ✅ 可执行文件就绪"
[ -f "$APP_DIR/Contents/Info.plist" ] && echo "   ✅ Info.plist 就绪"
[ -f "$APP_DIR/Contents/Resources/AppIcon.icns" ] && echo "   ✅ 图标就绪 (icns)"
[ -f "$APP_DIR/Contents/Resources/AppIcon.png" ] && echo "   ✅ 图标就绪 (png)"
echo ""
echo "💡 首次双击如果提示「无法打开」，右键 → 打开 → 确认即可。"
