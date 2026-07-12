# -*- coding: utf-8 -*-
"""获取 UPX 压缩工具(优先 GitHub 镜像), 供 PyInstaller 打包时压缩体积。
已存在则跳过; 全部镜像失败返回 1(不影响打包, 仅体积稍大)。"""
import urllib.request, zipfile, io, os, sys

DEST = "upx_tool"
URLS = [
    "https://ghproxy.net/https://github.com/upx/upx/releases/download/v4.2.4/upx-4.2.4-win64.zip",
    "https://mirror.ghproxy.com/https://github.com/upx/upx/releases/download/v4.2.4/upx-4.2.4-win64.zip",
    "https://gh-proxy.com/https://github.com/upx/upx/releases/download/v4.2.4/upx-4.2.4-win64.zip",
    "https://github.com/upx/upx/releases/download/v4.2.4/upx-4.2.4-win64.zip",
]

for r, _, fs in os.walk(DEST):
    for f in fs:
        if f.lower() == "upx.exe":
            print("UPX 已存在:", os.path.join(r, f))
            sys.exit(0)

os.makedirs(DEST, exist_ok=True)
for u in URLS:
    try:
        req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
        data = urllib.request.urlopen(req, timeout=60).read()
        zipfile.ZipFile(io.BytesIO(data)).extractall(DEST)
        print("UPX 下载成功:", u.split("/")[2])
        sys.exit(0)
    except Exception as e:
        print("  失败 %s: %s" % (u.split("/")[2], str(e)[:60]))
print("UPX 获取失败, 将跳过压缩(不影响功能, 仅产物体积稍大)")
sys.exit(1)
