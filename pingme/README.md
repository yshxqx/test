# pingme-checkin

PingMe 每日自动签到 + 视频奖励，纯 Python，无第三方依赖，可直接跑在 GitHub Actions。

移植自 QX 脚本 [`ZenmoFeiShi/Qx/PingMe.js`](https://github.com/ZenmoFeiShi/Qx/blob/main/PingMe.js)（作者：怎么肥事），
签名算法、接口顺序、UA 轮换、假设备号逻辑与原文 1:1，并已用 node vm 加载原脚本逐字节比对通过（`scripts/js_ref.js`）。

## 它做什么

每个账号依次：`queryBalanceAndBonus` → `checkIn` → `videoBonus` ×5（间隔 8s）→ 再查余额。
返回「已被注销」的账号会被自动从库中移除；`videoBonus` 每次运行使用随机 `uniquedeviceid`。

接口：`https://api.pingmeapp.net/app/{queryBalanceAndBonus,checkIn,videoBonus}`（GET）

```
secret = 0fOiukQq7jXZV2GRi9LGlO
signDate = UTC "YYYY-MM-DD HH:MM:SS"
sign = md5("&".join("k=v" for k in sorted(params)) + secret)   # 参数去掉 sign、signDate
```

## GitHub Actions 用法

1. 在 PingMe App 里触发一次请求，用抓包工具拿到 `queryBalanceAndBonus` 的 cURL / URL
2. 仓库 → Settings → Secrets and variables → Actions → New repository secret
   - `PINGME_URLS`：每行一条 `queryBalanceAndBonus` 的 URL，或者整段 `curl '...' -H '...'`（多账号多行）
   - 或 `PINGME_ACCOUNTS`：本地 `pingme.py list` 用的那份 `pingme_accounts.json` 全文（含自定义请求头）
   - 可选 `PINGME_UA`：没抓到 UA 时的兜底 UA
3. Actions → **PingMe 每日签到** → Run workflow，先把 `dry_run` 打开确认参数正确

默认排程：`30 0,12 * * *`（UTC）= 北京时间 **08:30 / 20:30**。GitHub 的 cron 在高峰期可能延迟几分钟到十几分钟。

## 本地用法

```bash
python3 pingme.py add --curl '抓包里的拷贝为 cURL'
python3 pingme.py list
python3 pingme.py run --dry      # 只打印 URL
python3 pingme.py run            # 查余额 → 签到 → 5 视频
python3 pingme.py run --video 0  # 只签到
python3 pingme.py remove 账号1
```

账号库默认 `./pingme_accounts.json`（已在 `.gitignore` 中），可用 `--store` 指定。

## 注意

- **账号参数等同凭据**，不要提交进仓库；只放在 Secrets 里。
- 脚本会按账号轮换 iOS 版本 / 机型 / CFNetwork / Darwin，并每次更换设备号，风控行为与原始脚本一致。
- 原脚本对参数值二次编码（`%40` → `%2540`），此处保持原样；若服务端报「账号不存在」优先怀疑这里。
- 本仓库不含任何账号数据，也不内置任何代理/上报。

## 目录

```
pingme.py                 主程序
scripts/build_store.py    Actions 里从 Secrets 还原账号库
scripts/js_ref.js         用 node vm 加载原 QX 脚本导出参考值（校验移植）
docs/analysis.md          原脚本逐行分析 + 实测记录
.github/workflows/pingme.yml
```
