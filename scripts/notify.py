#!/usr/bin/env python3
"""把打包结果发回机器人所在的会话。只在机器人触发的打包里运行(always(),成功失败都通知)。

发不出去(包超过 Telegram 机器人接口的 50MB 上限、网络错误、没配 token)时,往 $GITHUB_ENV 写 P_KEEP_ARTIFACT=1,
让后面一步把安装包留成 artifact,并尽量发一条带链接的文字消息 —— 否则这次打包就白跑了。
"""
import json
import os
import re
import sys
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import params  # noqa: E402

TG_LIMIT = 49 * 1024 * 1024   # 官方上限 50MB,留一点余量


def keep_artifact():
    with open(os.environ["GITHUB_ENV"], "a") as f:
        f.write("P_KEEP_ARTIFACT=1\n")


def main():
    p = params.load()
    token = os.environ.get("BOT_TOKEN", "")
    run_url = os.environ["RUN_URL"]
    ok = os.environ["JOB_STATUS"] == "success"
    apk = Path("dist/app.apk")
    title = f"{p['app_name']} {p['version_name']} ({p['version_code']})"
    if not token:
        if ok and apk.exists():
            keep_artifact()
        raise SystemExit("仓库没有配置 TELEGRAM_BOT_TOKEN,没法把安装包发回去")
    api = f"https://api.telegram.org/bot{token}"

    def post(method, fields, file=None):
        boundary = uuid.uuid4().hex
        body = b""
        for k, v in fields.items():
            body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
        if file:
            fname, data = file
            body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"document\"; filename=\"{fname}\"\r\n"
                     f"Content-Type: application/vnd.android.package-archive\r\n\r\n").encode() + data + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        req = urllib.request.Request(f"{api}/{method}", data=body,
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.loads(r.read())
        except Exception as e:  # noqa: BLE001
            # 异常信息里带着含 token 的地址,不能原样抛出去进日志。
            raise RuntimeError(f"Telegram {method} 失败: {type(e).__name__}") from None

    def say(text):
        try:
            post("sendMessage", {"chat_id": p["notify_chat_id"], "text": text})
        except RuntimeError as e:
            print(e)

    if not ok or not apk.exists():
        say(f"❌ {title} 打包失败\n{run_url}")
        return
    size = apk.stat().st_size
    filename = (re.sub(r"[^\w.-]+", "_", p["app_name"]).strip("_") or "app") + f"-{p['version_name']}.apk"
    if size <= TG_LIMIT:
        try:
            post("sendDocument", {"chat_id": p["notify_chat_id"], "caption": f"✅ {title} 打包完成"}, (filename, apk.read_bytes()))
            print("已发送")
            return
        except RuntimeError as e:
            print(e)
            reason = "发送失败"
    else:
        reason = f"安装包 {size / 1048576:.0f}MB,超过 Telegram 机器人 50MB 的上限"
    keep_artifact()
    say(f"✅ {title} 打包完成,但{reason}。\n请在一天内到下面的页面底部 Artifacts 处下载:\n{run_url}")
    raise SystemExit(reason)


if __name__ == "__main__":
    main()
