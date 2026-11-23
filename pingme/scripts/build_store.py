#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在 GitHub Actions 里把 Secrets 还原成 pingme_accounts.json。

支持两种 Secret（任填其一或同时填）：
  PINGME_ACCOUNTS  完整账号库 JSON（pingme.py 的 store 格式，可含自定义请求头）
  PINGME_URLS      每行一条 queryBalanceAndBonus 的 URL，或整段 `curl '...' -H '...'`
可选：
  PINGME_UA        没有抓到 UA 时使用的兜底 User-Agent
"""
import json
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pingme  # noqa: E402


def add_capture(store, url, headers=None, alias=None):
    params = pingme.parse_raw_query(url)
    if not params:
        return False
    fp = pingme.fingerprint(params)
    existed = fp in store["accounts"]
    headers = headers or {}
    base_ua = next((v for k, v in headers.items() if k.lower() == "user-agent"), "")
    email = params.get("email", "")
    try:
        email = urllib.parse.unquote(email)
    except Exception:
        pass
    seed = store["accounts"][fp]["uaSeed"] if existed else len(store["order"])
    store["accounts"][fp] = {
        "id": fp,
        "alias": alias or f"账号{len(store['order']) + 1}",
        "email": email,
        "uaSeed": seed,
        "baseUA": base_ua or os.environ.get("PINGME_UA", "").strip(),
        "params": params,
        "headers": headers,
    }
    if not existed:
        store["order"].append(fp)
    return True


def main():
    store = {"version": 1, "accounts": {}, "order": []}
    raw = os.environ.get("PINGME_ACCOUNTS", "").strip()
    if raw:
        try:
            loaded = json.loads(raw)
            if isinstance(loaded, dict) and "accounts" in loaded:
                store = loaded
                store.setdefault("order", list(store["accounts"].keys()))
        except json.JSONDecodeError as e:
            sys.exit(f"PINGME_ACCOUNTS 不是合法 JSON: {e}")

    added = 0
    for line in os.environ.get("PINGME_URLS", "").splitlines():
        line = line.strip()
        if not line:
            continue
        if line.lower().startswith("curl"):
            url, headers = pingme.parse_curl(line)
        else:
            url, headers = line, {}
        if "pingmeapp.net" not in url:
            sys.exit(f"跳过非法条目（不是 api.pingmeapp.net 的请求）: {url[:60]}")
        if add_capture(store, url, headers):
            added += 1

    if not store["accounts"]:
        sys.exit("没有可用账号：请在仓库 Settings → Secrets and variables → Actions 里配置 PINGME_URLS 或 PINGME_ACCOUNTS")

    with open("pingme_accounts.json", "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)
    print(f"已载入 {len(store['order'])} 个账号（本次由 PINGME_URLS 新增/刷新 {added} 个）")
    for aid in store["order"]:
        a = store["accounts"][aid]
        print(f"  - {a.get('alias')} id={aid} email={a.get('email') or '-'} 参数={','.join(sorted(a.get('params', {})))}")


if __name__ == "__main__":
    main()
