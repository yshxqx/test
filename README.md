# qxctl — 让 Agent 通过仓库操控 Quantumult X

没有桌面端、容器又连不上局域网，所以用 GitHub 仓库当**信箱**：
Agent 投递指令 → QX 定时拉取执行 → 结果写回仓库 → Agent 读取。

```
Agent                    GitHub 仓库                  Quantumult X
  │  push json/cmd.json ────────>│                          │
  │                              │  cron 拉取脚本 ─────────>│
  │                              │                          │ 执行
  │                              │<── 回写 json/out/result ─│
  │  read out/result ◄───────────│                          │
```

## 部署（一次性）

### 1. 生成 PAT

GitHub → Settings → Developer settings → Personal access tokens → **Fine-grained**
- Repository access：只勾选 `qxctl` 这个仓库
- Permissions：**Contents → Read and write**（其余全空）

### 2. 把 PAT 存进 QX

QX 的 JS 环境用 `$prefs.valueForKey('qxctl_token')` 读取。
在 QX 里建一个**定时任务**，第一段执行：

```javascript
$prefs.setValueForKey('你的PAT', 'qxctl_token');
$done();
```

只需执行一次，之后可删。

> 也可以直接在 `qxctl.js` 顶部把 `CFG.token` 改成真实值，
> 但脚本会被 QX 缓存，PAT 会留在缓存里，安全性差一些。

### 3. 配置远程脚本

QX 设置 → 定时任务（Cron），新增一条：

```
*/5 * * * *  https://raw.githubusercontent.com/<owner>/qxctl/main/scripts/qxctl.js
```

`*/5` 是 5 分钟一次。GitHub raw 有缓存，建议加 `?t=` 版本号，或者用 jsDelivr。
**频率别太高**，否则会触发 GitHub API 限流（未认证 60 次/小时；带 PAT 是 5000 次/小时）。

### 4. 改脚本里的仓库信息

把 `scripts/qxctl.js` 顶部改成：

```javascript
const CFG = {
  owner: '你的用户名',
  repo: 'qxctl',
  ...
};
```

推上去。

## 日常使用

在 Minis 这边（环境变量 `QX_REPO` 和 `GITHUB_TOKEN` 配好）：

```sh
python3 qxctl.py push --ops 'policy.get'
python3 qxctl.py wait --timeout 600 --poll 20
```

或直接发指令：

```sh
python3 qxctl.py push --ops 'policy.set group=Proxy node="香港 01"'
python3 qxctl.py result
```

## 支持的指令

| 指令 | 说明 | 状态 |
|---|---|---|
| `query.status` | 读取运行模式 | 已实现 |
| `policy.get` | 列出策略组与当前节点 | 已实现 |
| `policy.set group=X node=Y` | 切换节点 | 已实现 |
| `capture.set enabled=true` | 开/关抓包 | **受限**，见下 |
| `capture.dump limit=50` | 导出请求记录 | **受限**，见下 |

## 已知限制（重要）

1. **抓包开关不可控。** `$configuration.sendMessage` 没有公开的 HTTP Analyzer 开关接口，
   `capture.set` 目前只是探测，不真正生效。
2. **抓不到历史请求。** QX 的 JS 环境无法枚举 HTTP Analyzer 的历史记录。
   要分析流量，仍然得你在 QX 里导出 HAR 给我。
3. **延迟。** 受拉取间隔限制，指令最快也要等 1~5 分钟才执行。

也就是说：**这个通道能操控 QX 的配置（节点、策略、规则），但操控不了抓包本身。**
分析抓包这件事，走「导出 HAR → harx 分析」那条路更快。

## 协议字段

```json
{
  "version": 1,
  "seq": 12,              // 递增，防止旧指令重放
  "id": "cmd-12-1732000000",
  "issued_at": "...",
  "expires_at": "...",    // 过期不执行
  "ack": false,           // 执行后置 true，防重复
  "commands": [...]
}
```

幂等保证：`ack=true` 或已过期 → 直接跳过，不写仓库。已用模拟环境验证（10 项测试全过）。
