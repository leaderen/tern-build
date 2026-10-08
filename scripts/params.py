#!/usr/bin/env python3
"""打包参数:校验、打码、存到工作区外,再按需交给各个步骤。

这是公开仓库,日志登录用户都能看,所以:
  · 参数只从事件文件读,不经过步骤的 env(那会被原样打印);
  · 能认出品牌的值一律不进 $GITHUB_ENV —— 写进去的变量会出现在之后每一步的日志开头,
    而打码对两三个字的应用名不可靠;它们存在 $RUNNER_TEMP/params.json 里,
    各步骤用 `eval "$(python3 params.py env 名字…)"` 取到 shell 变量里(这行命令本身不含任何值);
  · 报错只说哪一项不对,不把值打出来。

用法:
  params.py init            读事件 → 校验 → 写 params.json / 签名文件 → 打码 → 只把不敏感的几项写进 $GITHUB_ENV
  params.py env A B C       打印 export A=… 供 eval
  params.py get A           打印一项的值(给 Python 步骤用)
"""
import base64
import json
import os
import re
import shlex
import sys
from urllib.parse import urlparse

ABIS = ("arm64-v8a", "armeabi-v7a", "x86_64", "x86")
URL = r"https://[^\s,\"'<>\\`$]{4,400}"

# 名字 → (默认值, 校验用的正则;None = 另外处理)
SPEC = {
    "app_name": ("", None),
    "package_name": ("", r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+"),
    "version_name": ("1.0.0", r"\d+(\.\d+){0,3}"),
    "version_code": ("1", r"[1-9]\d{0,8}"),
    "panel_urls": ("", None),
    "api_prefix": ("/api/v1", r"(/[A-Za-z0-9._\-]+)+"),
    "config_urls": ("", None),
    "config_xor_key": ("", r"[A-Za-z0-9_\-]{0,64}"),
    "logo_url": ("", rf"({URL})?"),
    "logo_file_id": ("", r"[A-Za-z0-9_\-]{0,200}"),
    "brand_color": ("", r"(#?[0-9a-fA-F]{6})?"),
    "user_agent": ("", r"[\x20-\x7E]{0,120}"),
    "default_config_b64": ("", r"[A-Za-z0-9+/=]{0,20000}"),
    "abis": ("arm64-v8a,armeabi-v7a", None),
    "notify_chat_id": ("", r"(-?\d{1,20})?"),
    "source_ref": ("main", r"[A-Za-z0-9._/\-]{1,100}"),
    "key_alias": ("tern", r"[A-Za-z0-9_\-]{1,32}"),
    "cert_sha256": ("", r"([0-9a-f]{64})?"),
}

# 能认出是哪个品牌的项:打码,且不进 $GITHUB_ENV。
IDENTIFYING = ("app_name", "package_name", "panel_urls", "config_urls", "config_xor_key", "logo_url", "logo_file_id",
               "notify_chat_id", "user_agent", "default_config_b64", "cert_sha256", "api_prefix")


def path() -> str:
    return os.path.join(os.environ["RUNNER_TEMP"], "params.json")


def fail(field: str, why: str):
    # 只说哪一项、什么问题,不带值。
    raise SystemExit(f"参数 {field} 不对:{why}")


def mask(value: str):
    """让日志里出现这个值的地方变成 ***。命令里的 % 和换行要转义,否则登记的掩码和真实文本对不上。"""
    if len(value) >= 2:
        print("::add-mask::" + value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A"))


def url_list(field: str, raw: str, required: bool) -> str:
    urls = [u for u in re.split(r"[,\s]+", raw) if u]
    if required and not urls:
        fail(field, "不能为空")
    for u in urls:
        if not re.fullmatch(URL, u):
            fail(field, "每个地址都要是 https:// 开头的完整地址")
    return ",".join(urls)


def init():
    event = json.load(open(os.environ["GITHUB_EVENT_PATH"]))
    # 机器人的参数在 client_payload.params 里(GitHub 限制 client_payload 顶层最多 10 个字段)。
    raw = {**(event.get("inputs") or {}), **((event.get("client_payload") or {}).get("params") or {})}
    p = {}
    for name, (default, pattern) in SPEC.items():
        v = str(raw.get(name) or default).strip()
        # 任何参数都不许有换行和控制字符:会让打码漏掉后半截,也是往别的文件里夹带内容的入口。
        if re.search(r"[\x00-\x1f\x7f]", v):
            fail(name, "不能有换行或控制字符")
        if pattern is not None and not re.fullmatch(pattern, v):
            fail(name, "格式不对")
        p[name] = v

    name = p["app_name"]
    if not 1 <= len(name) <= 30:
        fail("app_name", "长度要在 1~30 个字符")
    if re.search(r"[&<>\"'\\$`]", name):
        fail("app_name", "不能有 & < > \" ' \\ $ ` 这些字符")
    if name != " ".join(name.split()) or name[0] in "@?":
        fail("app_name", "不能有连续空格,不能以 @ 或 ? 开头")
    p["panel_urls"] = url_list("panel_urls", p["panel_urls"], required=True)
    p["config_urls"] = url_list("config_urls", p["config_urls"], required=False)
    abis = [a for a in re.split(r"[,\s]+", p["abis"]) if a]
    if not abis or any(a not in ABIS for a in abis):
        fail("abis", "只能是 " + " / ".join(ABIS))
    p["abis"] = ",".join(dict.fromkeys(abis))
    if p["default_config_b64"]:
        try:
            json.loads(base64.b64decode(p["default_config_b64"]))
        except Exception:
            fail("default_config_b64", "不是合法的 JSON")
    if p["brand_color"]:
        p["brand_color"] = "#" + p["brand_color"].lstrip("#").upper()

    # 先登记掩码,再做任何可能打印东西的事。
    for key in IDENTIFYING:
        v = p[key]
        if v == SPEC[key][0]:
            continue   # 默认值谁都一样,打码只会把日志里无关的地方弄花
        mask(v)
        for part in v.split(","):
            mask(part)
            host = urlparse(part).hostname if part.startswith("https://") else None
            if host:
                mask(host)

    # 机器人给的每品牌签名:写到工作区外,密码打码。两项必须同时有。
    ks = str(raw.get("keystore_b64") or "").strip()
    pw = str(raw.get("keystore_password") or "").strip()
    if bool(ks) != bool(pw):
        fail("keystore", "签名文件和密码必须同时提供")
    if pw:
        if re.search(r"[\x00-\x1f\x7f]", pw):
            fail("keystore_password", "不能有换行或控制字符")
        mask(pw)
        ks_path = os.path.join(os.environ["RUNNER_TEMP"], "brand.p12")
        with open(os.open(ks_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as f:
            f.write(base64.b64decode(ks))
        p["keystore_file"], p["keystore_password"] = ks_path, pw
    else:
        p["keystore_file"], p["keystore_password"] = "", ""

    with open(os.open(path(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(p, f)

    # 只有这几项进 $GITHUB_ENV(会出现在日志里):都认不出是哪个品牌。
    with open(os.environ["GITHUB_ENV"], "a") as f:
        f.write(f"P_SOURCE_REF={p['source_ref']}\n")
        f.write(f"P_ABIS={p['abis']}\n")
        f.write(f"P_ABIS_KEY={p['abis'].replace(',', '_')}\n")   # 缓存 key 里不能有逗号
        f.write(f"P_BOT={'1' if p['notify_chat_id'] else '0'}\n")
    print("abis:", p["abis"], "| logo:", "有" if (p["logo_url"] or p["logo_file_id"]) else "默认",
          "| 签名:", "本次参数" if pw else "仓库 secret 或无", "| 来源:", "机器人" if p["notify_chat_id"] else "手动")


def load() -> dict:
    return json.load(open(path()))


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "init":
        init()
    elif cmd == "env":
        p = load()
        for name in sys.argv[2:]:
            print(f"export P_{name.upper()}={shlex.quote(p[name])}")
    elif cmd == "get":
        sys.stdout.write(load()[sys.argv[2]])
    else:
        raise SystemExit(__doc__)
