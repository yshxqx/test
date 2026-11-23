// 在 vm 里加载 PingMe.js，导出与原脚本完全一致的签名/URL 结果，用于校验 Python 移植
const vm = require('vm');
const fs = require('fs');
const path = process.argv[2] || 'PingMe.js';

const FixedDate = class extends Date {
  constructor(...a) { if (a.length === 0) super('2026-11-23T08:00:00Z'); else super(...a); }
  static now() { return Date.UTC(2026, 10, 23, 8, 0, 0); }
};

const ctx = {
  console, Math, JSON, Promise, Date: FixedDate, setTimeout, encodeURIComponent, decodeURIComponent,
  $prefs: { valueForKey: () => null, setValueForKey: () => {} },
  $notify: () => {}, $done: () => {}, $task: { fetch: () => Promise.reject(new Error('stub')) },
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(path, 'utf8'), ctx);

const capture = {
  url: 'https://api.pingmeapp.net/app/queryBalanceAndBonus?email=tester%40example.com&uniquedeviceid=AAAA-BBBB&version=1.0.2&platform=ios&sign=deadbeef&signDate=2026-11-01%2000:00:00',
  paramsRaw: {
    email: 'tester%40example.com',
    uniquedeviceid: 'AAAA-BBBB',
    version: '1.0.2',
    platform: 'ios',
    sign: 'deadbeef',
    signDate: '2026-11-01 00:00:00',
  },
  headers: { 'User-Agent': 'PingMe/1.0.0 (iPhone14,3; iOS 17.5.1; Scale/3.00)', 'Accept': 'application/json' },
};

const out = {
  md5: ['', 'abc', 'The quick brown fox jumps over the lazy dog', 'sign=中文测试&b=2', 'a'.repeat(200)].map(s => [s, ctx.MD5(s)]),
  signDate: ctx.getUTCSignDate(),
  fingerprint: ctx.fingerprintOf(capture.paramsRaw),
  url_plain: ctx.buildUrl('queryBalanceAndBonus', capture, null),
  url_fake: ctx.buildUrl('videoBonus', capture, 'FIXED-0000-1111-2222-333333333333'),
  params_plain: ctx.buildSignedParamsRaw(capture, null),
};
console.log(JSON.stringify(out, null, 2));
