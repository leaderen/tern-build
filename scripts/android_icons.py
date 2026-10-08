#!/usr/bin/env python3
"""把品牌 logo 和品牌色写进客户端的 res 目录。

用法:
  android_icons.py --res <res 目录> [--logo-url URL | --logo-file-id TG文件ID | --logo-path 文件] [--brand-color #RRGGBB]

logo 先经 brand_logo.normalize 整理成铺满的方图(和 Flutter 版打包一套规则),再生成:
  · 自适应图标前景 mipmap-*/ic_launcher_foreground.png + 背景色
  · 旧式圆角图标 mipmap-*/ic_launcher.png(安卓 7)
  · 通知栏 / 快捷开关的白色剪影 drawable-xxxhdpi/ic_stat.png(剪影做不出来就保留默认电源图标)
没给 logo 时只改品牌色。只依赖 Pillow。
"""
import argparse
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import brand_logo as bl  # noqa: E402

DENSITIES = {"mdpi": 1.0, "hdpi": 1.5, "xhdpi": 2.0, "xxhdpi": 3.0, "xxxhdpi": 4.0}


MAX_LOGO_BYTES = 20 * 1024 * 1024


def fetch(url, retries=4):
    # 只走 https:urllib 还认 file:// 和 ftp://,不能让一个"logo 地址"去读构建机上的文件。
    if not url.startswith("https://"):
        raise SystemExit("logo 地址必须是 https://")
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "tern-build/1.0"})
            deadline = time.time() + 120          # 整个下载的时限;timeout 只管单次读写
            chunks, size = [], 0
            with urllib.request.urlopen(req, timeout=30) as r:
                while True:
                    chunk = r.read(256 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_LOGO_BYTES or time.time() > deadline:
                        raise SystemExit("logo 文件太大或下载太慢(上限 20MB / 2 分钟)")
                    chunks.append(chunk)
            return b"".join(chunks)
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001
            # 不打印异常正文:里面可能带着地址(Telegram 的地址里有 token)。
            print(f"[icons] 下载失败({i + 1}/{retries}): {type(e).__name__}")
            time.sleep(2 * (i + 1))
    raise SystemExit("logo 下载失败")


def telegram_file(file_id):
    token = os.environ.get("BOT_TOKEN", "")
    if not token:
        raise SystemExit("logo 是 Telegram 文件,但没有配置 TELEGRAM_BOT_TOKEN")
    info = json.loads(fetch(f"https://api.telegram.org/bot{token}/getFile?file_id={urllib.parse.quote(file_id)}"))
    if not info.get("ok"):
        raise SystemExit("Telegram getFile 失败(logo 可能已失效,请在机器人里重新上传)")
    return fetch(f"https://api.telegram.org/file/bot{token}/{info['result']['file_path']}")


def write_colors(res, brand=None, launcher_bg=None):
    """改 values/colors.xml 里的 brand / ic_launcher_background。"""
    path = res / "values" / "colors.xml"
    text = path.read_text(encoding="utf-8") if path.exists() else '<?xml version="1.0" encoding="utf-8"?>\n<resources>\n</resources>\n'

    def put(name, value):
        nonlocal text
        line = f'    <color name="{name}">{value}</color>'
        if re.search(rf'<color name="{name}">', text):
            text = re.sub(rf'    <color name="{name}">[^<]*</color>', line, text)
        else:
            text = text.replace("</resources>", line + "\n</resources>")

    if brand:
        put("brand", brand)
    if launcher_bg:
        put("ic_launcher_background", launcher_bg)
    path.write_text(text, encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--res", required=True)
    ap.add_argument("--logo-url", default="")
    ap.add_argument("--logo-file-id", default="")
    ap.add_argument("--logo-path", default="")
    ap.add_argument("--brand-color", default="")
    a = ap.parse_args()
    res = Path(a.res)
    if not (res / "values").is_dir():
        raise SystemExit(f"不是 res 目录: {res}")

    brand = a.brand_color.strip()
    if brand:
        if not re.fullmatch(r"#?[0-9a-fA-F]{6}", brand):
            raise SystemExit("品牌色要是 #RRGGBB: " + brand)
        brand = "#" + brand.lstrip("#").upper()

    data = None
    if a.logo_path:
        data = Path(a.logo_path).read_bytes()
    elif a.logo_file_id.strip():
        data = telegram_file(a.logo_file_id.strip())
    elif a.logo_url.strip():
        data = fetch(a.logo_url.strip())

    if data is None:
        write_colors(res, brand=brand or None)
        print("[icons] 没有 logo,保留默认图标" + (",已设置品牌色" if brand else ""))
        return

    try:
        # 只认这三种常见格式,别把 Pillow 里所有冷门解码器都暴露给外来文件。
        img = Image.open(io.BytesIO(data), formats=["PNG", "JPEG", "WEBP"])
        img.load()
    except Exception:  # noqa: BLE001
        raise SystemExit("logo 不是能识别的图片,请用 PNG / JPG / WebP")
    sq, info = bl.normalize(img, brand_color=brand or None)
    print(f"[icons] logo {img.size} → 方图 {sq.size},类型 {info['kind']}")

    for d, scale in DENSITIES.items():
        folder = res / f"mipmap-{d}"
        folder.mkdir(exist_ok=True)
        bl.android_adaptive_foreground(sq, int(108 * scale)).save(folder / "ic_launcher_foreground.png")
        bl.rounded(sq, int(48 * scale), 0.2).save(folder / "ic_launcher.png")

    (res / "mipmap-anydpi-v26").mkdir(exist_ok=True)
    (res / "mipmap-anydpi-v26" / "ic_launcher.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">\n'
        '    <background android:drawable="@color/ic_launcher_background" />\n'
        '    <foreground android:drawable="@mipmap/ic_launcher_foreground" />\n'
        "</adaptive-icon>\n",
        encoding="utf-8",
    )
    # 默认的矢量图标和旧式 layer-list 要删掉,否则和同名 png 冲突。
    for stale in ["drawable/ic_launcher_foreground.xml", "mipmap/ic_launcher.xml"]:
        p = res / stale
        if p.exists():
            p.unlink()

    tile = bl.tile_icon(sq, 96)
    if tile is not None:
        (res / "drawable-xxxhdpi").mkdir(exist_ok=True)
        tile.save(res / "drawable-xxxhdpi" / "ic_stat.png")
        p = res / "drawable" / "ic_stat.xml"
        if p.exists():
            p.unlink()
        print("[icons] 通知栏图标用 logo 剪影")
    else:
        print("[icons] logo 做不出剪影,通知栏保留默认图标")

    write_colors(res, brand=brand or None, launcher_bg=bl.android_background_hex(sq))
    print("[icons] 图标已写入" + (",已设置品牌色" if brand else ""))


if __name__ == "__main__":
    main()
