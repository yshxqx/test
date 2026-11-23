#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PingMe 自动签到 + 视频奖励（Python 直连版）
移植自 QX 脚本: https://raw.githubusercontent.com/ZenmoFeiShi/Qx/refs/heads/main/PingMe.js
作者原脚本: 怎么肥事

差异说明：
  - 账号保存在本地 JSON（默认 ./pingme_accounts.json），不再依赖 QX $prefs
  - 抓包入口改为手工导入：add --curl '...'  或  add --url '...' --header 'K: V'
  - 其余签名算法、接口顺序、UA 轮换、假设备号、重试逻辑与原脚本 1:1

用法：
  python3 pingme.py add --curl "$(pbpaste)"        # 从「拷贝为 cURL」导入账号
  python3 pingme.py add --url 'https://api.pingmeapp.net/app/queryBalanceAndBonus?...'
  python3 pingme.py list
  python3 pingme.py run                # 全账号：查余额 → 签到 → 5 个视频 → 再查余额
  python3 pingme.py run --video 0      # 只签到不刷视频
  python3 pingme.py run --dry          # 只打印将请求的 URL，不发包
  python3 pingme.py remove <id|alias>
"""
import argparse
import json
import os
import random
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

SCRIPT_NAME = "PingMe"
SECRET = "0fOiukQq7jXZV2GRi9LGlO"
BASE = "https://api.pingmeapp.net/app/"
DEFAULT_STORE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pingme_accounts.json")
MAX_VIDEO = 5
VIDEO_DELAY = 8.0
ACCOUNT_GAP = 3.5
TIMEOUT = 25

IOS_VERSIONS = ["17.5.1", "17.6.1", "17.4.1", "17.2.1", "16.7.8", "17.6", "17.3.1", "18.0.1", "17.1.2", "16.6.1"]
IOS_SCALES = ["2.00", "3.00", "3.00", "2.00", "3.00"]
IPHONE_MODELS = ["iPhone14,3", "iPhone13,3", "iPhone15,3", "iPhone16,1", "iPhone14,7", "iPhone13,2", "iPhone15,2", "iPhone12,1"]
CFN_VERS = ["1410.0.3", "1494.0.7", "1568.100.1", "1209.1", "1474.0.4", "1568.200.2"]
DARWIN_VERS = ["22.6.0", "23.5.0", "23.6.0", "24.0.0", "22.4.0"]

# ---------- JS 兼容层 ----------

_JS_SAFE = "-_.!~*'()"


def js_encode_uri_component(s):
    return urllib.parse.quote(str(s), safe=_JS_SAFE)


_K = [int(abs(__import__("math").sin(i + 1)) * (2 ** 32)) & 0xFFFFFFFF for i in range(64)]
_SHIFTS = ([7, 12, 17, 22] * 4) + ([5, 9, 14, 20] * 4) + ([4, 11, 16, 23] * 4) + ([6, 10, 15, 21] * 4)
_M = [(0, 1, 2, 3), (1, 2, 3, 0), (2, 3, 0, 1), (3, 0, 1, 2)]


def js_md5(text):
    """忠实复刻原脚本的 MD5：按 charCodeAt 组装 32bit 字，非 ASCII 也与 JS 结果一致。"""
    codes = [ord(c) for c in text]
    n = len(codes)
    total_words = (n + 8 - (n + 8) % 64) // 64 + 1
    total_words *= 16
    words = [0] * total_words
    for i, ch in enumerate(codes):
        words[i >> 2] |= (ch << ((i % 4) * 8)) & 0xFFFFFFFF
    words[n >> 2] |= (0x80 << ((n % 4) * 8)) & 0xFFFFFFFF
    words[total_words - 2] = (n * 8) & 0xFFFFFFFF
    words[total_words - 1] = (n * 8) >> 32

    def rl(x, c):
        x &= 0xFFFFFFFF
        return ((x << c) | (x >> (32 - c))) & 0xFFFFFFFF

    a0, b0, c0, d0 = 0x67452301, 0xEFCDAB89, 0x98BADCFE, 0x10325476
    for k in range(0, len(words), 16):
        A, B, C, D = a0, b0, c0, d0
        for i in range(64):
            if i < 16:
                f, g = (B & C) | (~B & D), i
            elif i < 32:
                f, g = (D & B) | (~D & C), (5 * i + 1) % 16
            elif i < 48:
                f, g = B ^ C ^ D, (3 * i + 5) % 16
            else:
                f, g = C ^ (B | (~D & 0xFFFFFFFF)), (7 * i) % 16
            f = (f + A + _K[i] + words[k + g]) & 0xFFFFFFFF
            A, D, C = D, C, B
            B = (B + rl(f, _SHIFTS[i])) & 0xFFFFFFFF
        a0 = (a0 + A) & 0xFFFFFFFF
        b0 = (b0 + B) & 0xFFFFFFFF
        c0 = (c0 + C) & 0xFFFFFFFF
        d0 = (d0 + D) & 0xFFFFFFFF

    out = ""
    for v in (a0, b0, c0, d0):
        for i in range(4):
            out += "%02x" % ((v >> (i * 8)) & 0xFF)
    return out


# ---------- 签名 ----------
DROP_FP_KEYS = {"sign", "signDate", "timestamp", "ts", "nonce", "random", "reqTime", "reqId", "requestId"}


def utc_sign_date():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


def fingerprint(params_raw):
    base = "&".join(f"{k}={params_raw[k]}" for k in sorted(params_raw) if k not in DROP_FP_KEYS)
    return js_md5(base)[:12]


def signed_params(capture_params, override_device=None):
    params = {k: v for k, v in capture_params.items() if k not in ("sign", "signDate")}
    if override_device and "uniquedeviceid" in params:
        params["uniquedeviceid"] = override_device
    params["signDate"] = utc_sign_date()
    base = "&".join(f"{k}={params[k]}" for k in sorted(params))
    params["sign"] = js_md5(base + SECRET)
    return params


def build_url(path, capture_params, override_device=None):
    params = signed_params(capture_params, override_device)
    qs = "&".join(f"{k}={js_encode_uri_component(params[k])}" for k in params)
    return f"{BASE}{path}?{qs}"


# ---------- UA / 设备号 ----------
def pick(arr, seed):
    return arr[seed % len(arr)]


def build_ua(base_ua, seed):
    ios, scale = pick(IOS_VERSIONS, seed), pick(IOS_SCALES, seed + 1)
    model, cfn = pick(IPHONE_MODELS, seed + 2), pick(CFN_VERS, seed + 3)
    darwin = pick(DARWIN_VERS, seed + 4)
    if base_ua:
        ua, changed = base_ua, False
        for pat, rep in (
            (r"iOS \d+(\.\d+){0,2}", f"iOS {ios}"),
            (r"Scale/\d+(\.\d+)?", f"Scale/{scale}"),
            (r"iPhone\d+,\d+", model),
            (r"CFNetwork/[\d.]+", f"CFNetwork/{cfn}"),
            (r"Darwin/[\d.]+", f"Darwin/{darwin}"),
        ):
            if re.search(pat, ua):
                ua = re.sub(pat, rep, ua, count=1)
                changed = True
        if changed:
            return ua
    return f"PingMe/1.0.0 ({model}; iOS {ios}; Scale/{scale}) CFNetwork/{cfn} Darwin/{darwin}"


def rand_hex(n):
    return "".join(random.choice("0123456789ABCDEF") for _ in range(n))


def gen_fake_device_id():
    return f"{rand_hex(8)}-{rand_hex(4)}-{rand_hex(4)}-{rand_hex(4)}-{rand_hex(12)}PingMeIOS"


# ---------- 存储 ----------
def load_store(path):
    if not os.path.exists(path):
        return {"version": 1, "accounts": {}, "order": []}
    with open(path, "r", encoding="utf-8") as f:
        obj = json.load(f)
    obj.setdefault("accounts", {})
    obj.setdefault("order", list(obj["accounts"].keys()))
    return obj


def save_store(path, store):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def account_email(acc):
    raw = acc.get("email") or ""
    try:
        return urllib.parse.unquote(raw)
    except Exception:
        return raw


# ---------- HTTP ----------
def make_opener(verify_ssl):
    if verify_ssl:
        return urllib.request.build_opener()
    ctx = ssl._create_unverified_context()
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx))


def clean_headers(capture_headers, ua):
    drop = {"content-length", "connection", "proxy-connection", "keep-alive", "host",
            ":authority", ":method", ":path", ":scheme"}
    headers = {k: v for k, v in (capture_headers or {}).items() if k.lower() not in drop}
    headers["Host"] = "api.pingmeapp.net"
    headers.setdefault("Accept", "application/json")
    headers["User-Agent"] = ua
    headers["Connection"] = "close"
    return headers


def http_get(opener, url, headers, retries=3):
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers, method="GET")
            with opener.open(req, timeout=TIMEOUT) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if 400 <= e.code < 500:
                return e.code, body
            last = f"HTTP {e.code}"
        except Exception as e:  # noqa: BLE001
            last = f"{type(e).__name__}: {e}"
            if not re.search(r"SSL|SSLSessionState|timeout|timed out|reset|connection|network|stream closed|closed|EOF|Remote end", str(e), re.I):
                break
        if attempt < retries - 1:
            time.sleep(1.5)
    raise RuntimeError(last)


def parse_json(body):
    try:
        return json.loads(body)
    except Exception:
        return None


def is_deregistered(msg):
    return isinstance(msg, str) and "已被注销" in msg


# ---------- 单账号流程 ----------
def run_account(acc, index, total, opener, video_count, dry=False):
    email = account_email(acc)
    tag = f"[账号{index + 1}/{total} {acc.get('alias') or acc.get('id')}]"
    ua = build_ua(acc.get("baseUA", ""), acc.get("uaSeed", index))
    headers = clean_headers(acc.get("headers", {}), ua)
    params = acc.get("params", {})
    fake_id = gen_fake_device_id()
    msgs = [tag + (f"\n📧 {email}" if email else "")]

    def call(path, use_fake=False):
        url = build_url(path, params, fake_id if use_fake else None)
        if dry:
            return None, {"url": url}
        if video_count <= 0 and path == "videoBonus":
            return None, {}
        status, body = http_get(opener, url, headers)
        return status, parse_json(body) or {"raw": body, "http": status}

    st, d = call("queryBalanceAndBonus")
    if dry:
        msgs.append("GET " + d["url"])
    elif d.get("retcode") == 0:
        msgs.append(f"💰 余额：{d.get('result', {}).get('balance')} Coins")
    else:
        msgs.append(f"⚠️ 查询：{d.get('retmsg') or d}")
        if is_deregistered(d.get("retmsg")):
            return "\n".join(msgs), True

    st, d = call("checkIn")
    if dry:
        msgs.append("GET " + d["url"])
    elif d.get("retcode") == 0:
        hint = (d.get("result", {}).get("bonusHint") or d.get("retmsg") or "").replace("\n", " ")
        msgs.append(f"✅ 签到：{hint}")
    else:
        msgs.append(f"⚠️ 签到：{d.get('retmsg') or d}")
        if is_deregistered(d.get("retmsg")):
            return "\n".join(msgs), True

    for i in range(1, video_count + 1):
        if not dry:
            time.sleep(1.5 if i == 1 else VIDEO_DELAY)
        try:
            st, d = call("videoBonus", use_fake=True)
        except RuntimeError as e:
            msgs.append(f"❌ 视频{i}：{e}")
            break
        if dry:
            msgs.append("GET " + d["url"])
            continue
        if d.get("retcode") == 0:
            bonus = d.get("result", {}).get("bonus") or "?"
            msgs.append(f"🎬 视频{i}：+{bonus} Coins")
        else:
            msgs.append(f"⏸ 视频{i}：{d.get('retmsg') or d}")
            break

    if not dry:
        try:
            st, d = call("queryBalanceAndBonus")
            if d.get("retcode") == 0:
                msgs.append(f"💰 最新余额：{d.get('result', {}).get('balance')} Coins")
        except RuntimeError:
            pass
    return "\n".join(msgs), False


# ---------- 子命令 ----------
def parse_curl(text):
    """解析浏览器/抓包工具 'Copy as cURL' 文本，返回 (url, headers)。"""
    text = text.strip().replace("\\\n", " ").replace("\r", "")
    url = ""
    m = re.search(r"curl\s+(?:'([^']+)'|\"([^\"]+)\")", text)
    if m:
        url = m.group(1) or m.group(2)
    headers = {}
    for hm in re.finditer(r"(?:-H|--header)\s+(?:'([^']*)'|\"([^\"]*)\")", text):
        raw = hm.group(1) or hm.group(2)
        if ":" in raw:
            k, v = raw.split(":", 1)
            headers[k.strip()] = v.strip()
    if not url:
        for um in re.finditer(r"(?:--url)\s+(?:'([^']*)'|\"([^\"]*)\")", text):
            url = um.group(1) or um.group(2)
            break
    if not url:  # 兜底：文本里任意 http(s) 链接
        um = re.search(r"https?://[^\s'\"]+", text)
        if um:
            url = um.group(0)
    return url, headers


def parse_raw_query(url):
    query = url.split("?", 1)[1].split("#")[0] if "?" in url else ""
    out = {}
    for pair in query.split("&"):
        if not pair or "=" not in pair:
            continue
        k, v = pair.split("=", 1)
        out[k] = v
    return out


def cmd_add(args, store, path):
    url, headers = (args.url or ""), {}
    for h in args.header or []:
        if ":" in h:
            k, v = h.split(":", 1)
            headers[k.strip()] = v.strip()
    if args.curl:
        url, curl_headers = parse_curl(args.curl)
        headers = {**curl_headers, **headers}
    if not url or "pingmeapp.net" not in url:
        sys.exit("需要 --url 或 --curl，且必须是 api.pingmeapp.net 的 queryBalanceAndBonus 请求")
    params = parse_raw_query(url)
    if not params:
        sys.exit("URL 里没有查询参数，抓错了请求？")
    fp = fingerprint(params)
    existed = fp in store["accounts"]
    base_ua = next((v for k, v in headers.items() if k.lower() == "user-agent"), "")
    email = params.get("email", "")
    try:
        email = urllib.parse.unquote(email)
    except Exception:
        pass
    seed = store["accounts"][fp]["uaSeed"] if existed else len(store["order"])
    alias = args.alias or (store["accounts"][fp]["alias"] if existed else f"账号{len(store['order']) + 1}")
    store["accounts"][fp] = {
        "id": fp,
        "alias": alias,
        "email": email,
        "uaSeed": seed,
        "baseUA": base_ua,
        "params": params,
        "headers": headers,
        "updatedAt": int(time.time()),
    }
    if not existed:
        store["order"].append(fp)
    save_store(path, store)
    print(f"{'🔄 已更新' if existed else '✅ 已入库'} {alias}  id={fp}  email={email}")


def cmd_list(args, store, path):
    if not store["order"]:
        print("（空）先用 add 导入一个抓包 URL")
        return
    for i, aid in enumerate(store["order"], 1):
        a = store["accounts"].get(aid, {})
        keys = ",".join(sorted(a.get("params", {}).keys()))
        print(f"{i}. {a.get('alias')}  id={aid}  email={a.get('email') or '-'}\n   参数: {keys}")


def cmd_remove(args, store, path):
    target = args.target
    hit = [aid for aid in store["order"] if aid == target or store["accounts"].get(aid, {}).get("alias") == target]
    if not hit:
        sys.exit(f"找不到 {target}")
    for aid in hit:
        store["accounts"].pop(aid, None)
        store["order"].remove(aid)
    save_store(path, store)
    print(f"🗑 已删除 {len(hit)} 个账号，剩余 {len(store['order'])} 个")


def cmd_run(args, store, path):
    ids = [i for i in store["order"] if i in store["accounts"]]
    if not ids:
        sys.exit("没有账号，先 add")
    opener = make_opener(args.verify)
    results, dead, failed = [], [], 0
    for idx, aid in enumerate(ids):
        acc = store["accounts"][aid]
        try:
            text, dereg = run_account(acc, idx, len(ids), opener, args.video, args.dry)
        except Exception as e:  # noqa: BLE001
            text, dereg = f"[{acc.get('alias')}] ❌ 异常：{type(e).__name__}: {e}", False
        if "❌ 异常：" in text or "❌ 查询：" in text:
            failed += 1
        print(text)
        print("———")
        results.append(text)
        if dereg:
            dead.append(aid)
        if idx < len(ids) - 1 and not args.dry:
            time.sleep(args.gap)
    if dead:
        for aid in dead:
            store["accounts"].pop(aid, None)
            store["order"].remove(aid)
        save_store(path, store)
        print(f"🗑 已移除注销账号 {len(dead)} 个，剩余 {len(store['order'])} 个")
    if failed:
        sys.exit(f"{failed}/{len(ids)} 个账号执行失败")


def main():
    ap = argparse.ArgumentParser(description="PingMe 自动签到 + 视频奖励（Python 直连版）")
    ap.add_argument("--store", default=DEFAULT_STORE)
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="导入抓包请求")
    a.add_argument("--url")
    a.add_argument("--curl")
    a.add_argument("--header", action="append")
    a.add_argument("--alias")
    a.set_defaults(func=cmd_add)

    l = sub.add_parser("list", help="列出账号")
    l.set_defaults(func=cmd_list)

    r = sub.add_parser("remove", help="删除账号")
    r.add_argument("target")
    r.set_defaults(func=cmd_remove)

    run = sub.add_parser("run", help="执行签到")
    run.add_argument("--video", type=int, default=MAX_VIDEO)
    run.add_argument("--gap", type=float, default=ACCOUNT_GAP)
    run.add_argument("--dry", action="store_true")
    run.add_argument("--verify", action="store_true", help="严格校验证书（QX MITM 环境会失败）")
    run.set_defaults(func=cmd_run)

    args = ap.parse_args()
    store = load_store(args.store)
    args.func(args, store, args.store)


if __name__ == "__main__":
    main()
