/*
 * qxctl.js — Quantumult X 指令执行器
 *
 * 作为 QX 远程资源定时拉取（cron）。每轮：
 *   1. 拉取 cmd.json（指令箱）
 *   2. 若未执行过（seq 递增 且 ack=false 且未过期）→ 逐条执行
 *   3. 结果 POST 回 out/result.json，并把 cmd.json 标记 ack=true
 *
 * 配置：在 QX 设置中把下面三个常量改成你的值，或通过 Rewrite 注入。
 * 依赖：GitHub PAT（Contents: Read and write）
 */

const CFG = {
  owner: 'yshxqx',
  repo: 'test',
  branch: 'main',
  token: '$GITHUB_TOKEN_QX',   // 建议写成读取自 $prefs 的方式，见下方 loadToken()
  cmdPath: 'json/cmd.json',
  outPath: 'json/out/result.json',
  timeout: 12000,
};

// ---------------------------------------------------------------- 基础设施

function loadToken() {
  // 优先从 QX 本地存储读取，避免 token 硬编码在脚本里被 remote 缓存泄露
  try {
    const t = $prefs.valueForKey('qxctl_token');
    if (t) return t;
  } catch (e) {}
  return CFG.token;
}

function apiUrl(path) {
  return `https://api.github.com/repos/${CFG.owner}/${CFG.repo}/contents/${path}`;
}

function ghHeaders(token) {
  return {
    'Authorization': `Bearer ${token}`,
    'Accept': 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': 'qxctl',
    'Content-Type': 'application/json',
  };
}

function b64decode(s) {
  // QX 环境没有 atob 时用 Buffer（NOS 提供）
  s = (s || '').replace(/\n/g, '');
  try { return Buffer.from(s, 'base64').toString('utf8'); } catch (e) {}
  try { return decodeURIComponent(escape(atob(s))); } catch (e) {}
  return '';
}

function b64encode(str) {
  try { return Buffer.from(str, 'utf8').toString('base64'); } catch (e) {}
  return btoa(unescape(encodeURIComponent(str)));
}

async function ghFetch(opts, label) {
  let r;
  try {
    r = await $task.fetch(opts);
  } catch (e) {
    const m = (e && (e.message || e.error || e.reason)) || String(e) || '未知网络错误';
    throw new Error(`${label} 请求异常: ${m}`);
  }
  // QX 在失败时可能返回 { error: ... } 而不抛异常
  if (!r) throw new Error(`${label} 无响应`);
  if (r.error) throw new Error(`${label} 网络失败: ${JSON.stringify(r.error).slice(0, 200)}`);
  if (typeof r.statusCode === 'undefined') {
    throw new Error(`${label} 响应缺少 statusCode: ${String(r.body || JSON.stringify(r)).slice(0, 200)}`);
  }
  return r;
}

async function ghGet(path, token) {
  const r = await ghFetch({
    url: apiUrl(path),
    method: 'GET',
    headers: ghHeaders(token),
    opts: { timeout: CFG.timeout },
  }, `GET ${path}`);

  if (r.statusCode === 404) return null;
  if (r.statusCode === 401) throw new Error('GET 401：token 无效或权限不足（需要 Contents: Read）');
  if (r.statusCode === 403) throw new Error('GET 403：可能触发了限流，或 token 对该仓库无权限');
  if (r.statusCode !== 200) {
    throw new Error(`GET ${path} -> ${r.statusCode}: ${String(r.body || '').slice(0, 160)}`);
  }

  let j;
  try {
    j = JSON.parse(r.body);
  } catch (e) {
    throw new Error(`GET ${path} 返回非 JSON: ${String(r.body || '').slice(0, 160)}`);
  }
  return { content: b64decode(j.content), sha: j.sha, raw: j };
}

async function ghPut(path, contentStr, token, sha, message) {
  const body = {
    message: message || `qxctl: update ${path}`,
    content: b64encode(contentStr),
    branch: CFG.branch,
  };
  if (sha) body.sha = sha;
  const r = await ghFetch({
    url: apiUrl(path),
    method: 'PUT',
    headers: ghHeaders(token),
    body: JSON.stringify(body),
    opts: { timeout: CFG.timeout },
  }, `PUT ${path}`);

  if (r.statusCode === 401) throw new Error('PUT 401：token 需要 Contents: Read and write');
  if (r.statusCode === 409) {
    // sha 冲突：说明文件同时被改过，重读一次即可
    throw new Error(`PUT ${path} 409：版本冲突，稍后重试`);
  }
  if (r.statusCode >= 300) {
    throw new Error(`PUT ${path} -> ${r.statusCode}: ${String(r.body || '').slice(0, 160)}`);
  }
  return true;
}

// ---------------------------------------------------------------- QX 能力封装

function sendMessage(msg) {
  return new Promise((resolve) => {
    try {
      $configuration.sendMessage(msg).then(
        (r) => resolve({ ok: !!(r && r.ret !== undefined ? r.ret === true || r.ret !== null : true), data: r }),
        (e) => resolve({ ok: false, error: String(e) }),
      );
    } catch (e) {
      resolve({ ok: false, error: String(e) });
    }
  });
}

// ---------------------------------------------------------------- 指令实现

const OPS = {
  async 'query.status'() {
    const r = await sendMessage({ action: 'get_running_mode' });
    return { running: r };
  },

  async 'policy.get'(args) {
    const r = await sendMessage({ action: 'get_customized_policy' });
    return { groups: r };
  },

  async 'policy.set'(args) {
    if (!args.group || !args.node) return { error: '需要 group 与 node' };
    const r = await sendMessage({
      action: 'set_policy_state',
      content: { [args.group]: args.node },
    });
    return { applied: args, result: r };
  },

  async 'capture.set'(args) {
    const r = await sendMessage({ action: 'get_running_mode' });
    return {
      note: 'HTTP Analyzer 开关在 QX 中没有公开的 sendMessage 接口',
      requested: !!args.enabled,
      probe: r,
    };
  },

  async 'capture.dump'(args) {
    return {
      note: '需配合 HTTP Analyzer 导出；QX JS 环境无法直接枚举历史请求',
      requested: args,
    };
  },
};

async function runCommand(c) {
  const key = c.target ? `${c.op}.${c.target}` : c.op;
  const fn = OPS[key];
  if (!fn) return { op: key, ok: false, error: `未实现的操作: ${key}` };
  try {
    const out = await fn(c.args || {});
    return { op: key, ok: !(out && out.error), result: out };
  } catch (e) {
    return { op: key, ok: false, error: String(e) };
  }
}

// ---------------------------------------------------------------- 主流程

async function main() {
  const token = loadToken();
  if (!token || token.startsWith('$')) throw new Error('未配置 token（$prefs qxctl_token）');

  const cmdFile = await ghGet(CFG.cmdPath, token);
  if (!cmdFile) throw new Error(`指令文件不存在: ${CFG.cmdPath}`);
  let cmd;
  try { cmd = JSON.parse(cmdFile.content); } catch (e) { throw new Error('指令 JSON 解析失败'); }

  // 幂等：已 ack / 已过期 → 直接返回
  if (cmd.ack === true) return { skipped: 'ack', seq: cmd.seq };
  if (cmd.expires_at) {
    const exp = Date.parse(cmd.expires_at);
    if (!isNaN(exp) && Date.now() > exp) return { skipped: 'expired', seq: cmd.seq };
  }

  const results = [];
  for (const c of cmd.commands || []) {
    results.push(await runCommand(c));
  }

  const now = new Date().toISOString();
  const out = {
    version: 1,
    cmd_id: cmd.id,
    seq: cmd.seq,
    executed_at: now,
    results,
  };

  // 回写结果
  const outExisting = await ghGet(CFG.outPath, token);
  await ghPut(CFG.outPath, JSON.stringify(out, null, 2), token,
    outExisting ? outExisting.sha : null, `qxctl: result for ${cmd.id}`);

  // 回写 ack，防止重复执行
  cmd.ack = true;
  cmd.acked_at = now;
  await ghPut(CFG.cmdPath, JSON.stringify(cmd, null, 2), token,
    cmdFile.sha, `qxctl: ack ${cmd.id}`);

  return out;
}

main()
  .then((r) => {
    console.log('qxctl done: ' + JSON.stringify(r).slice(0, 400));
    $done({});
  })
  .catch((e) => {
    // QX 里 stack 常为空，优先输出可读的 message
    let msg;
    if (e && e.message) msg = e.message;
    else if (e && e.error) msg = typeof e.error === 'string' ? e.error : JSON.stringify(e.error);
    else if (typeof e === 'string') msg = e;
    else {
      try { msg = JSON.stringify(e); } catch (_) { msg = String(e); }
    }
    if (!msg || msg === 'undefined' || msg === '{}') msg = '未知错误（无错误信息）';
    console.log('qxctl error: ' + msg);
    if (typeof $notify !== 'undefined') {
      try { $notify('qxctl 执行失败', msg.slice(0, 200)); } catch (_) {}
    }
    $done({});
  });
