# PingMe 脚本分析与 Python 直连版

来源：`https://raw.githubusercontent.com/ZenmoFeiShi/Qx/refs/heads/main/PingMe.js`（作者：怎么肥事，2026/06/29）
端口：`PingMe.js`（原版，已下载）→ `pingme.py`（本目录，Python 直连版）

## 一、脚本干什么

QX 双模式脚本：

| 模式 | 触发 | 行为 |
|---|---|---|
| rewrite | 命中 `^https://api\.pingmeapp\.net/app/queryBalanceAndBonus` | 把这次请求的 **URL 参数 + 全部请求头** 存进 `$prefs`（key `pingme_accounts_v1`），账号指纹 = 去掉易变参数后的 MD5 前 12 位 |
| task | cron `30 8,20 * * *` | 遍历已存账号：查余额 → `checkIn` → `videoBonus` ×5（间隔 8s）→ 再查余额 |

命中「已被注销」的账号会自动从库里删除。

## 二、协议要点（已实测验证）

- 域名：`https://api.pingmeapp.net/app/<path>`，全部 **GET**
- 路径：`queryBalanceAndBonus` / `checkIn` / `videoBonus`
- 签名：

```
secret = 0fOiukQq7jXZV2GRi9LGlO
params = 抓包里的全部参数（去掉 sign、signDate）
params.signDate = UTC "YYYY-MM-DD HH:MM:SS"
sign = md5( join(sorted(k=v, '&'), '&') + secret )
```

- 请求时参数值再走一遍 `encodeURIComponent`（所以 email 里的 `%40` 会变成 `%2540`，原脚本行为，已原样保留）
- `videoBonus` 会把 `uniquedeviceid` 换成本次运行随机生成的 `XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXXPingMeIOS`
- UA 按账号 uaSeed 轮换 iOS 版本 / Scale / 机型 / CFNetwork / Darwin，规避设备指纹

**实测**（2026-11-23，无账号的假参数）：

| 请求 | retcode | 含义 |
|---|---|---|
| 正确签名 | `100029` | Please register or login（签名通过，只是账号不存在） |
| 随便写 sign | `100008` | You're not authorized（签名错） |

→ 说明 SECRET 与签名算法当前有效。

## 三、Python 直连版

```bash
# 1) 导入账号：在 PingMe App 里触发一次请求，QX 抓包后「拷贝为 cURL」
python3 pingme.py add --curl '...'
# 或者直接给 URL（可加 --header 'User-Agent: ...' 保留原 UA 模板）
python3 pingme.py add --url 'https://api.pingmeapp.net/app/queryBalanceAndBonus?...'

python3 pingme.py list
python3 pingme.py run               # 查余额→签到→5 视频→再查余额
python3 pingme.py run --dry         # 只打印将要请求的 URL，不发包
python3 pingme.py run --video 0     # 只签到
python3 pingme.py remove 账号1
```

账号库默认 `./pingme_accounts.json`，可用 `--store` 指定。

### 校验

`js_ref.js` 在 node vm 里加载原脚本，冻结时间后导出签名/URL 参考值；
`pingme.py` 的 `js_md5 / fingerprint / build_url` 与其逐字节一致（含非 ASCII 的 charCodeAt 语义）。

## 四、坑

1. **本机所有 HTTPS 被 Quantumult X MITM**，证书链只有叶子证书（QX 根 CA 在 iOS 钥匙串，shell 读不到）→ `curl` 要 `-k`，Python 默认用不校验上下文（`--verify` 现在必然失败）。想彻底解决：QX 里导出 CA 证书存到共享目录，再 `SSL_CERT_FILE` 指向它。
2. 原脚本对参数值二次编码，若某接口服务端只解一次，email 会被当成 `xx%40yy.com`；真出现「查不到账号」优先怀疑这里。
3. 每次运行都换假设备号，可能触发风控（同一账号设备频繁变化）。
4. 抓包只要 `queryBalanceAndBonus` 这一条，但**请求头要完整**（鉴权信息在头里）。
