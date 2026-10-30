#!/usr/bin/env python3
"""秒取本地服务：静态托管 + PWA 所需的正确 MIME，不收集任何数据。

用法：
    python3 serve.py            # 默认 8770 端口
    python3 serve.py 8800       # 指定端口
"""

import mimetypes
import os
import socket
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("image/png", ".png")
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("video/mp4", ".mp4")

ROOT = os.path.dirname(os.path.abspath(__file__))


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=ROOT, **kwargs)

    def end_headers(self):
        path = self.path.split("?")[0]
        if path.endswith((".html", ".js", ".css", ".webmanifest")) or path in ("", "/"):
            self.send_header("Cache-Control", "no-cache, must-revalidate")
        else:
            self.send_header("Cache-Control", "public, max-age=3600")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    def log_message(self, fmt, *args):
        sys.stderr.write("  %s\n" % (fmt % args))


def lan_ip():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(0.4)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except OSError:
        return "127.0.0.1"


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8770
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print("\n  秒取 · 短视频下载 已启动\n")
    print("  本机：    http://127.0.0.1:%d/" % port)
    print("  手机：    http://%s:%d/   （需同一 Wi-Fi）" % (lan_ip(), port))
    print("\n  手机端打开后在浏览器菜单里「添加到主屏幕」，即可像 App 一样使用。")
    print("  Ctrl+C 停止\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  已停止\n")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
