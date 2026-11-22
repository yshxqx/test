/*
 * qxctl.js — Quantumult X 指令执行器（v2，无 token 版）
 *
 * 原理：
 *   QX 定时拉取本脚本 → 从公开仓库读 json/cmd.json → 执行指令
 *   → 把结果 POST 到匿名上报通道 → Agent 直接读取
 *
 * 为什么不用 GitHub token：
 *   1. GitHub raw 会 404 屏蔽 ?token= 参数名
 *   2. QX 定时任务里 $request 是 undefined，读不到 URL 参数
 *   3. 结果写回仓库必须有 token，而 token 无处安放
 *   → 改成匿名上报，彻底不碰凭据
 *
 * 去重：用 $prefs 记住最后执行过的 cmd.id，避免 cron 每轮重复执行
 */

const CFG = {
  owner: 'yshxqx',
  repo: 'test',
  branch: 'main',
  cmdPath: 'json/cmd.json',

  // 匿名上报通道（ntfy.sh）。主题名相当于密码，别外泄。
  reportUrl: 'https://ntfy.sh/qxctl-q6j2anbmhj50wey661809tbn',

  timeout: 12000,
};

// ---------------------------------------------------------------- 基础

function log(...a) {
  try { console.log(a.map(String).join(' ')); } catch (_) {}
}

function notify(title, body, sub) {
  try {
    if (typeof $notify !== 'undefined') $notify(title, sub || '', body || '');
  } catch (_) {}
}

async function fetchText(opts, label) {
  let r;
  try {
    r = await $task.fetch(opts);
  } catch (e) {
    const m = (e && (e.message || e.error)) || String(e) || '未知网络错误';
    throw new Error(`${label} 请求异常: ${m}`);
  }
  if (!r) throw new Error(`${label} 无响应`);
  if (r.error) throw new Error(`${label} 网络失败: ${JSON.stringify(r.error).slice(0, 200)}`);
  if (typeof r.statusCode === 'undefined') {
    throw new Error(`${label} 响应缺少 statusCode`);
  }
  return r;
}

async function readCmd() {
  // jsDelivr 支持主动清缓存（raw 的 CDN 缓存换 query 也刷不掉）
  const bases = [
    `https://cdn.jsdelivr.net/gh/${CFG.owner}/${CFG.repo}@${CFG.branch}/${CFG.cmdPath}`,
    `https://raw.githubusercontent.com/${CFG.owner}/${CFG.repo}/${CFG.branch}/${CFG.cmdPath}`,
  ];
  let lastErr = null;
  for (const base of bases) {
    try {
      const r = await fetchText({
        url: `${base}?t=${Date.now()}`,
        method: 'GET',
        opts: { timeout: CFG.timeout },
      }, '读取指令');
      if (r.statusCode === 404) { lastErr = `指令文件不存在: ${CFG.cmdPath}`; continue; }
      if (r.statusCode !== 200) { lastErr = `HTTP ${r.statusCode}`; continue; }
      try {
        return JSON.parse(r.body);
      } catch (e) {
        lastErr = '指令 JSON 解析失败: ' + String(r.body).slice(0, 120);
      }
    } catch (e) {
      lastErr = (e && e.message) || String(e);
    }
  }
  throw new Error(lastErr || '读取指令失败');
}

async function report(payload) {
  const body = JSON.stringify(payload);
  try {
    const r = await fetchText({
      url: CFG.reportUrl,
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Title': 'qxctl' },
      body,
      opts: { timeout: CFG.timeout },
    }, '上报结果');
    if (r.statusCode >= 300) throw new Error(`HTTP ${r.statusCode}`);
    return true;
  } catch (e) {
    log('上报失败: ' + (e && e.message ? e.message : String(e)));
    log('完整结果: ' + body);
    return false;
  }
}

// ---------------------------------------------------------------- QX 能力

function sendMessage(msg) {
  return new Promise((resolve) => {
    try {
      $configuration.sendMessage(msg).then(
        (r) => resolve({ ok: true, ret: r && typeof r.ret !== 'undefined' ? r.ret : r }),
        (e) => resolve({ ok: false, error: String((e && e.message) || e) }),
      );
    } catch (e) {
      resolve({ ok: false, error: String(e) });
    }
  });
}

const OPS = {
  'query.status': async () => {
    const mode = await sendMessage({ action: 'get_running_mode' });
    return { running_mode: mode };
  },

  'policy.get': async () => {
    const r = await sendMessage({ action: 'get_customized_policy' });
    return { groups: r };
  },

  'policy.set': async (args) => {
    if (!args || !args.group || !args.node) {
      return { error: '需要 group 与 node 两个参数' };
    }
    const r = await sendMessage({
      action: 'set_policy_state',
      content: { [args.group]: args.node },
    });
    return { applied: { group: args.group, node: args.node }, result: r };
  },
};

async function runCommand(c) {
  const key = c.target ? `${c.op}.${c.target}` : c.op;
  const fn = OPS[key];
  if (!fn) return { op: key, ok: false, error: `未实现: ${key}` };
  try {
    const out = await fn(c.args || {});
    return { op: key, ok: !(out && out.error), result: out };
  } catch (e) {
    return { op: key, ok: false, error: String((e && e.message) || e) };
  }
}

// ---------------------------------------------------------------- 主流程

async function main() {
  const cmd = await readCmd();

  // 去重：同一个 id 只执行一次（$prefs 无则忽略）
  let lastId = null;
  try { lastId = $prefs.valueForKey('qxctl_last'); } catch (_) {}
  if (lastId && lastId === cmd.id) {
    return { skipped: 'already-run', id: cmd.id };
  }
  if (cmd.ack === true) return { skipped: 'ack', id: cmd.id };
  if (cmd.expires_at) {
    const exp = Date.parse(cmd.expires_at);
    if (!isNaN(exp) && Date.now() > exp) return { skipped: 'expired', id: cmd.id };
  }

  const results = [];
  for (const c of cmd.commands || []) {
    results.push(await runCommand(c));
  }

  const payload = {
    cmd_id: cmd.id,
    seq: cmd.seq,
    executed_at: new Date().toISOString(),
    results,
  };

  const sent = await report(payload);

  try { $prefs.setValueForKey(cmd.id, 'qxctl_last'); } catch (_) {}

  const okCount = results.filter((r) => r.ok).length;
  notify(
    `qxctl ${okCount}/${results.length} 成功`,
    results.map((r) => `${r.ok ? '✓' : '✗'} ${r.op}`).join('\n'),
    sent ? '已上报' : '上报失败，见日志',
  );

  log('qxctl done: ' + JSON.stringify(payload).slice(0, 500));
  return payload;
}

main()
  .then((r) => {
    log('qxctl exit: ' + JSON.stringify(r).slice(0, 300));
    $done({});
  })
  .catch((e) => {
    let msg = (e && e.message) || (typeof e === 'string' ? e : '');
    if (!msg) { try { msg = JSON.stringify(e); } catch (_) { msg = String(e); } }
    if (!msg || msg === '{}') msg = '未知错误';
    log('qxctl error: ' + msg);
    notify('qxctl 执行失败', msg.slice(0, 300));
    $done({});
  });
