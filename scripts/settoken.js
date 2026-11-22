/*
 * settoken.js — 一次性任务：把 GitHub PAT 写进 QX 本地存储
 *
 * 用法：在 [task_local] 里配一次，跑完后把该行 enabled 改为 false 或删除
 *
 * 两种方式任选：
 *   A) 直接在下面 TOKEN 里粘贴（最省事，但文件会上传到仓库）
 *   B) 放在 URL 参数里：.../settoken.js?token=ghp_xxx
 *      （日志里会出现，但仓库里是干净的）
 */

const TOKEN = '';   // ← 方式 A：在这里粘贴你的 token

function fromQuery() {
  try {
    const m = ($request && $request.url || '').match(/[?&]token=([^&]+)/);
    if (m) return decodeURIComponent(m[1]);
  } catch (e) {}
  return '';
}

const token = (TOKEN || fromQuery() || '').trim();

if (!token) {
  console.log('settoken: 没有提供 token，跳过');
  $done();
} else {
  $prefs.setValueForKey(token, 'qxctl_token');
  const back = $prefs.valueForKey('qxctl_token');
  console.log(`settoken: 已写入，长度 ${back ? back.length : 0}，前缀 ${back ? back.slice(0, 11) : '-'}`);
  // 不打印完整 token
  $notify && $notify('qxctl', 'Token 已保存', `长度 ${back ? back.length : 0}`);
  $done();
}
