/**
----------------------------------------------------------------------------------------------
战马能量星球 小程序自动任务
Author: anonymous
Date: 2026.08.28
Description: 战马能量星球小程序签到
Cron: 12 8 * * *
----------------------------------------------------------------------------------------------
战马能量星球签到code本

功能：自动执行战马能量星球小程序签到和完成日常任务，支持多账号执行。

配置说明：
1. 微信 code 网关（应用宝协议）：
   wx_server_url        必填，自建授权服务器地址，如 http://127.0.0.1:8000
   - 脚本会自动拼接 /wxapp/getCode
   - 请求格式：POST {网关}/wxapp/getCode
   - 请求体：{"app_id": "wx10bc773e0851aedd", "ref": "openid"}

2. 账号变量：
   zm_openid            必填，战马能量星球小程序专属账号变量
   - 多账号支持使用 & 、英文逗号、中文逗号或换行分隔
   - 示例：owNAX6...&owNAX6...

3. 代理变量（可选，适配品赞代理）：
   proxy_api_url                                      代理 API 地址，开启后每个账号自动获取代理
   - 代理接口返回格式支持：纯 IP:PORT，或带账号密码的 IP:PORT ACCOUNT PASSWORD（品赞格式）
   - 示例：http://your-proxy-host:port/get
   - 仅在配置了本变量时启用 API 代理，未配置则不使用代理
   - 单账号固定代理：在账号后追加 #proxy=IP:PORT 可指定该账号专用代理

4. 青龙任务建议：
   名称：战马能量星球小程序签到
   命令：task zmnlxq.js
   定时：每天运行 1 - 3 次即可，具体时间自行调整
----------------------------------------------------------------------------------------------
 */

'use strict';

const fs = require('fs');
const https = require('https');
const http = require('http');
const { URL } = require('url');

// Windows 控制台默认 GBK 无法编码部分字符，强制 stdout/stderr 为 UTF-8
try {
  if (process.stdout.isTTY !== undefined && process.stdout.setEncoding) {
    process.stdout.setEncoding('utf-8');
    process.stderr.setEncoding('utf-8');
  }
} catch (e) {
  /* ignore */
}

// ==================== 配置区域 ====================
const ZM_ENABLE_DAILY_TASK = true; // 日常任务

// 统一变量名称与默认值映射
const ZM_WX_SERVER = (process.env.wx_server_url || "").trim().replace(/\/+$/, "");
const ZM_WX_APPID = "wx94dca6ef07a54c55"; // 战马能量星球小程序 appid
const ZM_OPENID = (process.env.zm_openid || "").trim(); // 环境变量

// 战马业务域名
const ZM_BASE = "https://warhorsechina.cojoy.com.cn";
const ZM_API = "/app/api/custom";
const ZM_LOGIN_URL = "/app/api/wxphonelogin";
const ZM_TOKEN_HEADER = "cGvnZetrWSWfLcdYaN40mLdFx6ObkRltdZmhS5hQkgDbuZd9bLcQevwBVEjx-war-horse-zm-2025";

// 代理模块：由环境变量 proxy_api_url 驱动
const ZM_PROXY_API_URL = process.env.proxy_api_url || "";
const ZM_PROXY_TYPE = process.env.zm_proxy_type || "socks5";
const ZM_PROXY_TIMEOUT = 20;
const ZM_MAX_PROXY_RETRIES = 5;

// 品牌活动：每日品牌浏览计数（systemScoreLog.scorelog_brand）的完成上限。
// 实测服务端每日最多计 3 次（zm03 抓包 2→3 即"已完成"，今日账号 3 后浏览不再增长），
// 与可浏览品牌页数量（list.length）无关。若服务端调整上限请修改此值。
const ZM_BRAND_DAILY_LIMIT = 3;

// 默认 User-Agent（Android 微信小程序环境）
const ZM_UA = "Mozilla/5.0 (Linux; Android 10; MI 8 Build/QKQ1.190828.002; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/86.0.4240.99 XWEB/3235 MMWEBSDK/20220204 Mobile Safari/537.36 MMWEBID/6242 MicroMessenger/8.0.20.2080(0x28001435) Process/appbrand0 WeChat/arm64 Weixin NetType/WIFI Language/zh_CN ABI/arm64 miniProgram/wx532ecb3bdaaf92f9";

// 全局锁，避免并发打印错乱
const printLock = { locked: false };
function withPrintLock(fn) {
  fn();
}

// ==================== Logger ====================
class Logger {
  _log(icon, msg) {
    const line = icon ? `${icon} ${msg}` : msg;
    withPrintLock(() => console.log(line));
  }
  info(msg) { this._log('📝', msg); }
  debug(msg) { this._log('🐞', msg); }
  raw(msg) { this._log('', msg); }
  success(msg) { this._log('✨', msg); }
  warning(msg) { this._log('⚠️', msg); }
  error(msg) { this._log('❌', msg); }
  task(msg) { this._log('🎯', msg); }
  task_skip(msg) { this._log('⏭️', msg); }
  task_complete(msg) { this._log('✅', msg); }
  points(pts, prefix = "当前能量") { this._log('💰', `${prefix}: 【${pts}】`); }
}

function logGlobal(msg) {
  try {
    console.log(msg);
  } catch (e) {
    console.log(String(msg).replace(/[^\x00-\x7F]/g, '?'));
  }
}

// ==================== 工具函数 ====================
function parse_env_accounts(raw) {
  return String(raw || "")
    .replace(/，/g, ",")
    .replace(/,/g, "&")
    .replace(/\n/g, "&")
    .split("&")
    .map(x => x.trim())
    .filter(x => x.length > 0);
}

// ==================== 账号加载 ====================
// 所有账号统一使用 wx_server_url 环境变量作为网关地址
function loadAccounts() {
  const raw = ZM_OPENID;

  if (!ZM_WX_SERVER) {
    logGlobal("❌ 未设置 wx_server_url 环境变量，无法登录");
    return [];
  }
  if (!raw) {
    logGlobal("❌ 未设置 zm_openid 环境变量，无法登录");
    return [];
  }

  const refs = parse_env_accounts(raw);
  const accounts = [];
  for (const ref of refs) {
    if (ref) accounts.push({ openid: ref, server: ZM_WX_SERVER });
  }
  return accounts;
}

function mask_account(value) {
  value = String(value || "");
  if (value.length <= 12) return value;
  return `${value.slice(0, 6)}...${value.slice(-4)}`;
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function random_sleep(base) {
  const low = base * 0.7;
  const high = base * 1.3;
  return sleep(Math.floor(low + Math.random() * (high - low)));
}

// ==================== ProxyManager ====================
class ProxyManager {
  constructor(apiUrl) {
    this.apiUrl = apiUrl;
  }

  get_proxy() {
    if (!this.apiUrl) return null;
    try {
      const resp = httpGetSync(this.apiUrl, {}, ZM_PROXY_TIMEOUT * 1000);
      if (resp && resp.statusCode === 200) {
        let proxyText = (resp.body || "").trim();
        const parts = proxyText.split(/\s+/);
        let proxy = "";
        if (parts.length === 3) {
          const ipPort = parts[0];
          const account = parts[1];
          const password = parts[2];
          proxyText = `http://${account}:${password}@${ipPort}`;
          proxy = proxyText;
        } else if (proxyText.includes(':')) {
          proxy = proxyText.startsWith('http://') || proxyText.startsWith('https://')
            ? proxyText
            : `http://${proxyText}`;
        }
        if (proxy) {
          let display = proxy;
          if (proxy.includes('@')) {
            const seg = proxy.split('@');
            if (seg.length === 2) display = `http://***:***@${seg[1]}`;
          }
          logGlobal(`✅ 成功获取代理: ${display}`);
          return { http: proxy, https: proxy };
        }
      }
      logGlobal(`❌ 获取代理失败: ${(resp && resp.body) || ''}`);
      return null;
    } catch (e) {
      logGlobal(`❌ 获取代理异常: ${String(e).slice(0, 80)}`);
      return null;
    }
  }
}

const proxy_manager = new ProxyManager(ZM_PROXY_API_URL);

function parse_fixed_proxy(fixedProxy) {
  if (!fixedProxy) return null;
  if (!fixedProxy.includes('://')) fixedProxy = `${ZM_PROXY_TYPE}://${fixedProxy}`;
  return { http: fixedProxy, https: fixedProxy };
}

// ==================== 原生 HTTP 请求 ====================
function requestSync(method, urlStr, options = {}) {
  return new Promise((resolve, reject) => {
    let parsed;
    try {
      parsed = new URL(urlStr);
    } catch (e) {
      return reject(e);
    }
    const isHttps = parsed.protocol === 'https:';
    const lib = isHttps ? https : http;
    const port = parsed.port || (isHttps ? 443 : 80);

    const headers = Object.assign({}, options.headers || {});
    const timeoutMs = options.timeout || 30000;

    const reqOptions = {
      method: method.toUpperCase(),
      hostname: parsed.hostname,
      port: port,
      path: parsed.pathname + (parsed.search || ''),
      headers: headers,
    };

    if (options.proxy) {
      const p = new URL(options.proxy.http || options.proxy.https);
      reqOptions.hostname = p.hostname;
      reqOptions.port = p.port || (p.protocol === 'https:' ? 443 : 80);
      reqOptions.path = urlStr;
      // 走代理时若代理是 http，用 http 模块；https 走 CONNECT 较复杂，这里仅支持 http 代理
      const proxyLib = (p.protocol === 'https:' ? https : http);
      // 对于 http 代理，直接把完整 url 作为 path 即可（简单 GET/POST 透传）
      doRequest(proxyLib, reqOptions, options, timeoutMs, resolve, reject, true);
      return;
    }

    doRequest(lib, reqOptions, options, timeoutMs, resolve, reject, false);
  });
}

function doRequest(lib, reqOptions, options, timeoutMs, resolve, reject, isProxy) {
  const req = lib.request(reqOptions, (res) => {
    let data = '';
    res.setEncoding('utf-8');
    res.on('data', chunk => { data += chunk; });
    res.on('end', () => {
      resolve({
        statusCode: res.statusCode,
        headers: res.headers,
        body: data,
      });
    });
  });

  req.on('error', (err) => reject(err));
  req.setTimeout(timeoutMs, () => {
    req.destroy(new Error('请求超时'));
  });

  if (options.body !== undefined && options.body !== null) {
    req.write(options.body);
  }
  req.end();
}

// 简易同步风格封装（返回 Promise）
function httpGetSync(url, headers, timeout) {
  return requestSync('GET', url, { headers: headers || {}, timeout: timeout || 30000 });
}

// ==================== AutoCookieManager ====================
class AutoCookieManager {
  constructor(wxServer, fixedProxy = "") {
    this.wxServer = (wxServer || ZM_WX_SERVER).trim().replace(/\/+$/, "");
    this.fixedProxy = fixedProxy ? parse_fixed_proxy(fixedProxy) : null;
  }

  _get_wx_code(wxid, appid, maxRetries = 3) {
    const targetAppid = appid || ZM_WX_APPID;
    const url = `${this.wxServer}/wxapp/getCode`;
    const payload = JSON.stringify({ app_id: targetAppid, ref: String(wxid) });
    const headers = {
      'Content-Type': 'application/json',
      'User-Agent': 'Mozilla/5.0 MicroMessenger/8.0.50',
    };

    const attempt = (n) => new Promise((resolve) => {
      requestSync('POST', url, {
        headers, body: payload, timeout: 30000, proxy: this.fixedProxy || undefined,
      }).then((resp) => {
        try {
          const j = JSON.parse(resp.body);
          if (j.code === 0) {
            const data = j.data || {};
            const result = data.result || {};
            if (result.code) {
              logGlobal(`🔑 获取code成功: ${String(result.code).slice(0, 10)}***`);
              return resolve(String(result.code));
            }
          }
          if (n < maxRetries - 1) {
            logGlobal(`⚠️ ${mask_account(wxid)}: code为空，${(n + 1) * 3}s后重试(${n + 1}/${maxRetries})`);
            sleep((n + 1) * 3).then(() => resolve(attempt(n + 1)));
          } else {
            logGlobal(`❌ ${mask_account(wxid)}: 获取code失败 resp=${String(resp.body).slice(0, 160)}`);
            resolve(null);
          }
        } catch (e) {
          if (n < maxRetries - 1) {
            logGlobal(`⚠️ ${mask_account(wxid)}: code异常 ${String(e).slice(0, 60)}，${(n + 1) * 3}s后重试`);
            sleep((n + 1) * 3).then(() => resolve(attempt(n + 1)));
          } else {
            logGlobal(`❌ ${mask_account(wxid)}: 获取code异常 err=${String(e).slice(0, 80)}`);
            resolve(null);
          }
        }
      }).catch((e) => {
        if (n < maxRetries - 1) {
          logGlobal(`⚠️ ${mask_account(wxid)}: code请求错误 ${String(e).slice(0, 60)}，${(n + 1) * 3}s后重试`);
          sleep((n + 1) * 3).then(() => resolve(attempt(n + 1)));
        } else {
          logGlobal(`❌ ${mask_account(wxid)}: 获取code请求错误 ${String(e).slice(0, 80)}`);
          resolve(null);
        }
      });
    });

    return attempt(0);
  }

  _get_wx_phone(wxid, appid, maxRetries = 3) {
    const targetAppid = appid || ZM_WX_APPID;
    const url = `${this.wxServer}/wxapp/getPhoneNumber`;
    const payload = JSON.stringify({ app_id: targetAppid, ref: String(wxid) });
    const headers = {
      'Content-Type': 'application/json',
      'User-Agent': 'Mozilla/5.0 MicroMessenger/8.0.50',
    };

    const attempt = (n) => new Promise((resolve) => {
      requestSync('POST', url, {
        headers, body: payload, timeout: 30000, proxy: this.fixedProxy || undefined,
      }).then((resp) => {
        try {
          const j = JSON.parse(resp.body);
          if (j.code === 0) {
            const data = j.data || {};
            const result = data.result || {};
            if (result.encryptedData && result.iv) {
              return resolve({ encryptedData: result.encryptedData, iv: result.iv });
            }
          }
          if (n < maxRetries - 1) {
            sleep((n + 1) * 3).then(() => resolve(attempt(n + 1)));
          } else {
            logGlobal(`❌ ${mask_account(wxid)}: 获取手机加密数据失败 resp=${String(resp.body).slice(0, 160)}`);
            resolve(null);
          }
        } catch (e) {
          if (n < maxRetries - 1) {
            sleep((n + 1) * 3).then(() => resolve(attempt(n + 1)));
          } else {
            logGlobal(`❌ ${mask_account(wxid)}: 获取手机加密数据异常 ${String(e).slice(0, 80)}`);
            resolve(null);
          }
        }
      }).catch((e) => {
        if (n < maxRetries - 1) {
          sleep((n + 1) * 3).then(() => resolve(attempt(n + 1)));
        } else {
          logGlobal(`❌ ${mask_account(wxid)}: 获取手机加密数据请求错误 ${String(e).slice(0, 80)}`);
          resolve(null);
        }
      });
    });

    return attempt(0);
  }

  _login(code, encryptedData, iv) {
    const url = ZM_BASE + ZM_LOGIN_URL;
    const payload = JSON.stringify({ profile: {} });
    const headers = {
      'host': 'warhorsechina.cojoy.com.cn',
      'x-wx-code': code,
      'x-wx-encrypted-data': encryptedData,
      'x-wx-iv': iv,
      [ZM_TOKEN_HEADER]: ZM_TOKEN_HEADER,
      'customappid': ZM_WX_APPID,
      'referer': `https://servicewechat.com/${ZM_WX_APPID}/182/page-frame.html`,
      'user-agent': ZM_UA,
      'Content-Type': 'application/json',
    };

    return requestSync('POST', url, {
      headers, body: payload, timeout: 30000, proxy: this.fixedProxy || undefined,
    }).then((resp) => {
      try {
        const j = JSON.parse(resp.body);
        if (j.status === 'ok' && j.desc && j.desc.data && j.desc.data.f1safe) {
          return { safe: j.desc.data.f1safe, skey: j.desc.data.skey || '' };
        }
        logGlobal(`⚠️ 战马登录失败: ${String(resp.body).slice(0, 160)}`);
        return null;
      } catch (e) {
        logGlobal(`❌ 登录解析错误: ${String(e)} 原始=${String(resp.body).slice(0, 120)}`);
        return null;
      }
    });
  }

  get_token_for_wxid(wxid, appid) {
    const targetAppid = appid || ZM_WX_APPID;
    return this._get_wx_code(wxid, targetAppid).then((code) => {
      if (!code) return null;
      return this._get_wx_phone(wxid, targetAppid).then((phoneData) => {
        if (!phoneData) return null;
        return this._login(code, phoneData.encryptedData, phoneData.iv).then((result) => {
          if (result && result.safe) {
            logGlobal(`✅ 登录成功, safe:${result.safe.slice(0, 12)}...`);
            return result;
          }
          return null;
        });
      });
    });
  }
}

// ==================== ZMNLXQClient ====================
class ZMNLXQClient {
  constructor(safe, skey, fixedProxy = "") {
    this.safe = safe;
    this.skey = skey || ZM_TOKEN_HEADER;
    this.proxy = fixedProxy ? parse_fixed_proxy(fixedProxy) : null;
  }

  _common_headers() {
    return {
      'host': 'warhorsechina.cojoy.com.cn',
      [ZM_TOKEN_HEADER]: this.skey || ZM_TOKEN_HEADER,
      'customappid': ZM_WX_APPID,
      'referer': `https://servicewechat.com/${ZM_WX_APPID}/182/page-frame.html`,
      'user-agent': ZM_UA,
    };
  }

  _url(api) {
    return `${ZM_BASE}${ZM_API}${api}?safe=${encodeURIComponent(this.safe)}`;
  }

  get(api, extraParams = "", timeout = 8000) {
    const url = extraParams ? `${this._url(api)}&${extraParams}` : this._url(api);
    return requestSync('GET', url, {
      headers: this._common_headers(), timeout, proxy: this.proxy || undefined,
    }).then((resp) => {
      try {
        return JSON.parse(resp.body);
      } catch (e) {
        logGlobal(`⚠️ JSON解析失败: ${String(resp.body).slice(0, 120)}`);
        return null;
      }
    });
  }

  // POST JSON（用于 /app/api/system 等需要 JSON body 的接口，鉴权走 header 中的 skey）
  post(api, payload, timeout = 20000) {
    const url = `${ZM_BASE}${api}`;
    const headers = Object.assign(this._common_headers(), { 'Content-Type': 'application/json' });
    return requestSync('POST', url, {
      headers, body: JSON.stringify(payload), timeout, proxy: this.proxy || undefined,
    }).then((resp) => {
      try {
        return JSON.parse(resp.body);
      } catch (e) {
        logGlobal(`⚠️ JSON解析失败: ${String(resp.body).slice(0, 120)}`);
        return null;
      }
    });
  }

  // 验证 safe 是否有效
  test_safe_valid(timeout = 5000) {
    return this.get('/getusercenter', '', timeout).then((json) => {
      if (!json) return true; // 网络异常，暂时认为有效
      if (json.status == 1) return true;
      logGlobal(`safe ${this.safe} 无效: ${json.msg || '未知错误'}`);
      return false;
    });
  }
}

// ==================== DailyTaskExecutor ====================
const GANTA = 1;        // 是否执行饲料互助

class DailyTaskExecutor {
  constructor(http, logger) {
    this.http = http;
    this.logger = logger;
    this.frinds = [];
    this.totalAccountsfrinds = [];
    this.ok = 0;
    this.msg = '';
    this.startScore = null; // 执行前积分
    this.endScore = null;   // 执行后积分
  }

  // 统一任务输出格式：emoji [任务名] 内容
  task_log(icon, taskName, msg) {
    this.logger.raw(`${icon} [${taskName}] ${msg}`);
  }

  async getuser() {
    const result = await this.http.get('/getusercenter');
    if (result && result.status == 1) {
      this.ok = 1;
      this.startScore = Number(result.nowscore) || 0;
      this.logger.raw(`💰 当前积分：${result.nowscore}`);
      if (result.isgzhkl == 0) {
        this.task_log('📢', '公众号口令', '未完成，开始完成');
        await this.gzhkl();
      }
      if (result.isinfo == 0) {
        this.task_log('📝', '完善资料', '未完成，开始完成');
        const tel = await this.getTel();
        if (tel) {
          const now = new Date();
          const birthday = `${now.getFullYear()}-${now.getMonth() + 1}-${now.getDate()}`;
          await this.saveuserinfo(result.headimgurl, result.nickname, Math.floor(Math.random() * 10), birthday, tel);
        } else {
          this.task_log('📝', '完善资料', '未授权手机号，无法完善');
        }
      }
      return true;
    }
    this.logger.warning('信息获取失败');
    return false;
  }

  async getTel() {
    const result = await this.http.get('/getuserinfo');
    if (result) {
      this.task_log('📝', '完善资料', result.msg || '');
      return result.tel;
    }
    return null;
  }

  async checkin() {
    const r = await this.http.get('/checkin');
    this.task_log('📅', '每日签到', r ? (r.msg || '') : '签到异常');
  }

  async gettiku() {
    const result = await this.http.get('/getquesbackstatus');
    if (result && result.status == 1) {
      this.task_log('📚', '答题题库', result.msg || '');
      await sleep(2000);
      await this.getques();
      this.task_log('📚', '答题题库', '开始答题（固定答案）');
      await sleep(2000);
      await this.ques1();
      await sleep(2000);
      await this.ques2();
      await sleep(2000);
      await this.ques3();
    } else {
      this.task_log('📚', '答题题库', result ? (result.msg || '') : '查询题库异常');
    }
  }

  async getques() {
    const r = await this.http.get('/getques');
    this.task_log('📚', '答题题库', r ? (r.msg || '') : '刷新题目异常');
  }

  async ques1() { return this.subques(126, 'C'); }
  async ques2() { return this.subques(138, 'C'); }
  async ques3() { return this.subques(119, 'A'); }

  async subques(qid, val) {
    const r = await this.http.get('/subques', `qid=${qid}&val=${val}`);
    if (r) this.task_log('📚', '答题题库', r.msg || '');
  }

  async getshare() {
    const r = await this.http.get('/share');
    this.task_log('🔗', '分享任务', r ? (r.msg || '') : '分享任务异常');
  }

  async joinxcx() {
    const result = await this.http.get('/joinxcx');
    if (result && result.status == 1) this.task_log('🏆', '排行榜', '加入成功');
    else this.task_log('🏆', '排行榜', '加入：' + (result ? result.msg : '异常'));
  }

  async getmaer() {
    const r = await this.http.get('/starthorse');
    this.task_log('🐎', '领取小马儿', r ? (r.msg || '') : '领取小马儿异常');
  }

  async getmoyimo() {
    const r = await this.http.get('/strokehorse');
    this.task_log('🐎', '摸一摸', r ? (r.msg || '') : '摸一摸异常');
  }

  async getweima() {
    const result = await this.http.get('/horseeat');
    if (result && result.status != 0) {
      await sleep(2000);
      return this.getweima();
    }
    this.task_log('🍞', '喂马', result ? (result.msg || '') : '喂马异常');
  }

  async checkslgift() {
    const r = await this.http.get('/checkslgift');
    this.task_log('🎁', '马儿分享', r ? (r.msg || '') : '马儿分享任务异常');
  }

  async saveuserinfo(avatar, nickname, sex, birthday, tel) {
    const params = `avatar=${encodeURIComponent(avatar)}&nickname=${encodeURIComponent(nickname)}&uname=${encodeURIComponent(nickname)}&sex=${sex}&birthday=${birthday}&tel=${tel}`;
    const r = await this.http.get('/saveuserinfo', params);
    this.task_log('📝', '完善资料', r ? (r.msg || '') : '完善资料异常');
  }

  async gzhkl() {
    const kl = encodeURIComponent('有能量 当燃战马！');
    const r = await this.http.get('/gzhkl', `kl=${kl}`);
    this.task_log('📢', '公众号口令', r ? (r.msg || '') : '公众号口令异常');
  }

  // addFriend=true 时：用其他账号的 safe 作为 fromsafe，执行加好友（互加模式）
  async getranklist(addFriend = false, fromsafe = '') {
    const params = `type=1&fromsafe=${encodeURIComponent(fromsafe)}`;
    const result = await this.http.get('/getranklist', params);
    if (result && result.status == 1) {
      if (addFriend) {
        this.task_log('🤝', '互助好友', '添加好友成功');
        return;
      }
      this.frinds = (result.data || []).filter(item => item && item.ismy != 1 && item.id != 0);
      // 只收集非自己（ismy != 1）的好友，避免互助时对自己偷/送
      this.totalAccountsfrinds = this.totalAccountsfrinds.concat(
        (result.data || []).filter(item => item && item.ismy != 1 && item.id != 0)
      );
      const notLiked = this.frinds.filter(item => item.iszan == 0);
      for (const f of notLiked) {
        const reachedLimit = await this.like(f.id);
        if (reachedLimit) break;
        await this.getotherhorseinfo(f.id);
        await sleep(500);
      }
    } else {
      this.task_log('🤝', '互助好友', '获取好友失败：' + (result ? result.msg : '异常'));
    }
  }

  async getotherhorseinfo(likeUserId) {
    await this.http.get('/getotherhorseinfo', `friendid=${likeUserId}`);
  }

  async like(likeUserId) {
    const result = await this.http.get('/subrank', `id=${likeUserId}&type=1`);
    return result && result.msg && result.msg.includes('已到上限');
  }

  async tousiliao(num2, friends) {
    const friend = (friends || this.totalAccountsfrinds)[num2];
    if (!friend) return false;
    const result = await this.http.get('/subhorseplayer', `friendid=${friend.id}&type=1`);
    return result && result.msg && result.msg.includes('已到上限');
  }

  async songsiliao(num2, friends) {
    const friend = (friends || this.totalAccountsfrinds)[num2];
    if (!friend) return false;
    const result = await this.http.get('/subhorseplayer', `friendid=${friend.id}&type=2`);
    return result && result.msg && result.msg.includes('已到上限');
  }

  // 品牌活动：拉取品牌活动列表（pageModule / template_brand）
  async getbrandlist() {
    const payload = {
      action: 'pageModule',
      moduleid: ['template_brand'],
      modulekey: ['home_modules', 'home_lbs', 'home_windows'],
      modulemenu: ['template_menu'],
      modulewindow: ['template_rule', 'template_sign', 'template_run'],
      modulestyle: ['template_brand', 'template_window'],
      moduledate: 'all',
      pagesize: 100,
    };
    const r = await this.http.post('/app/api/system', payload, 20000);
    const now = Date.now();
    const list = (r && r.status === 'ok' && r.desc && r.desc.list ? r.desc.list : [])
      .filter(item => item && item.appgotourl && typeof item.appgotourl === 'string'
        && item.appgotourl.startsWith('https://mp.weixin.qq.com')
        && (!item.w_time1 || now >= Number(item.w_time1))
        && (!item.w_time2 || now <= Number(item.w_time2)));
    this.task_log('🎬', '品牌活动', `获取到${list.length}个可浏览品牌活动`);
    return list;
  }

  // 品牌活动：查询当前品牌浏览完成计数（systemScoreLog.scorelog_brand）
  async brandScore() {
    const r = await this.http.post('/app/api/system', { action: 'systemScoreLog' }, 20000);
    if (r && r.status === 'ok') return Number(r.desc.scorelog_brand) || 0;
    return -1;
  }

  // 品牌活动：上报浏览完成（systemBrandViewIn，模拟浏览 30 秒）
  async brandViewIn(url) {
    const endtime = Date.now();
    const starttime = endtime - 30000;
    const payload = {
      action: 'systemBrandViewIn',
      url: url.split('?')[0],
      run: 30,
      runtime: (endtime - starttime) / 1000,
      realtime: endtime - starttime,
      endtime,
      starttime,
    };
    const r = await this.http.post('/app/api/system', payload, 20000);
    if (r && r.status === 'ok') this.task_log('🎬', '品牌活动', `浏览完成：${url.slice(0, 48)}`);
    else this.task_log('🎬', '品牌活动', r ? `浏览上报失败：${r.msg || JSON.stringify(r).slice(0, 80)}` : '品牌活动上报异常');
    return r;
  }

  // 品牌活动主入口：先查完成状态，已完成则跳过；逐个浏览并复查计数，不增长即视为完成
  async run_brand() {
    const list = await this.getbrandlist();
    if (!list.length) return;
    let cnt = await this.brandScore();
    if (cnt < 0) {
      this.task_log('🎬', '品牌活动', '查询完成状态失败，跳过');
      return;
    }
    if (cnt >= ZM_BRAND_DAILY_LIMIT) {
      this.task_log('🎬', '品牌活动', `已完成(${cnt}/${ZM_BRAND_DAILY_LIMIT})，跳过`);
      return;
    }
    this.task_log('🎬', '品牌活动', `待完成，当前 ${cnt}/${ZM_BRAND_DAILY_LIMIT}`);
    for (const item of list) {
      if (cnt >= ZM_BRAND_DAILY_LIMIT) break;
      await this.brandViewIn(item.appgotourl);
      await sleep(2000);
      const n = await this.brandScore();
      if (n < 0) break;
      if (n > cnt) {
        cnt = n;
        this.task_log('🎬', '品牌活动', `进度 ${cnt}/${ZM_BRAND_DAILY_LIMIT}`);
      } else {
        this.task_log('🎬', '品牌活动', `浏览「${item.appname}」后计数未增长(${cnt})，已达上限，视为完成`);
        break;
      }
    }
    this.task_log('🎬', '品牌活动', `结束，最终计数 ${cnt}`);
  }

  async run() {
    const result = { login_ok: false, points: null };

    // 获取信息
    const got = await this.getuser();
    if (!got) return result;

    result.login_ok = true;
    await sleep(2000);

    // 签到
    await this.checkin();
    await sleep(2000);

    // 查询题库（答题）
    await this.gettiku();
    await sleep(2000);

    // 分享任务
    await this.getshare();
    await sleep(2000);

    // 加入排行榜
    await this.joinxcx();
    await sleep(2000);

    // 领取小马儿
    await this.getmaer();
    await sleep(2000);

    // 摸一摸
    await this.getmoyimo();
    await sleep(2000);

    // 马儿分享任务
    await this.checkslgift();
    await sleep(2000);

    // 去喂马
    await this.getweima();
    await sleep(2000);

    // 去点赞
    await this.getranklist();
    await sleep(2000);

    // 品牌活动
    await this.run_brand();
    await sleep(2000);

    // 起始积分交由 main 在所有任务（含互助）完成后统一核算
    result.startScore = this.startScore;
    result.friends = this.totalAccountsfrinds.slice();
    return result;
  }

  // 饲料互助：加好友已在 getranklist(addFriend=true) 阶段完成，
  // 这里仅对好友列表逐个偷/送一次，避免重复请求与重复日志
  async run_friend_help(friends, otherSafeList) {
    let stealDone = false, giftDone = false;
    this.task_log('🤝', '互助好友', `获取到${friends.length}个好友`);
    for (let j = 0; j < friends.length; j++) {
      const friend = friends[j];
      if (!friend) continue;
      if (!stealDone) {
        const reached = await this.tousiliao(j, friends);
        if (reached) stealDone = true;
        await sleep(2000);
      }
      if (!giftDone) {
        const reached = await this.songsiliao(j, friends);
        if (reached) giftDone = true;
        await sleep(1000);
      }
      if (stealDone && giftDone) break;
    }
    this.task_log('👍', '点赞', stealDone ? '今日点赞已到上限' : '点赞完成');
    this.task_log('🌾', '偷饲料', stealDone ? '今日偷饲料已到上限' : '偷饲料完成');
    this.task_log('🌾', '送饲料', giftDone ? '今日送饲料已到上限' : '送饲料完成');
  }
}

// ==================== 核心处理器 ====================
async function run_account(account_info, index, proxy_str = "") {
  const logger = new Logger();
  const safe = account_info.safe;
  const skey = account_info.skey;

  const http = new ZMNLXQClient(safe, skey, proxy_str);
  const masked = mask_account(account_info.openid || safe);
  const result = { success: true, phone: masked, index, daily: {}, openid: account_info.openid || '' };

  if (ZM_ENABLE_DAILY_TASK) {
    logger.task('开始执行日常任务');
    const executor = new DailyTaskExecutor(http, logger);
    try {
      result.daily = await executor.run();
    } catch (e) {
      logger.error(`日常任务异常: ${String(e).slice(0, 80)}`);
      result.success = false;
    }
  }
  return result;
}

// ==================== dispatch_summary ====================
function dispatch_summary(logger, results) {
  const total = results.length;
  const success = results.filter(r => r.success).length;
  const failed = total - success;

  const lines = [
    "==============================",
    `🕒 执行时间：${new Date().toLocaleString()}`,
    `📊 统计数据：成功 ${success} / 总计 ${total}`,
    `✅ 成功账号：${success} 个`,
    `❌ 失败账号：${failed} 个`,
  ];

  results.forEach((r, idx) => {
    const ok = !!r.success;
    const account = r.phone || "未知账号";
    lines.push(`👤 【账号${idx + 1}】${account}`);
    lines.push(`${ok ? '✅' : '❌'} 状态：${ok ? '执行成功' : '执行失败'}`);
    if (ok) {
      const daily = r.daily || {};
      if (daily.points != null) {
        const diff = daily.points_diff != null ? daily.points_diff : 0;
        const sign = diff >= 0 ? '+' : '';
        lines.push(`💰 总积分：${daily.points}（本次 ${sign}${diff}）`);
      }
    } else {
      lines.push(`⚠️ 原因：${r.error || '登录失效'}`);
    }
  });

  lines.push(`======🎉 完成 ${success} / 共 ${total} 账号=======`);
  console.log("\n[执行报表]\n" + lines.join("\n"));
}

// ==================== 凭证获取 ====================
async function get_or_refresh_safe(openid, mgr) {
  const result = await mgr.get_token_for_wxid(openid);
  if (result && result.safe) {
    const valid = await (new ZMNLXQClient(result.safe, result.skey, mgr.fixedProxy ? proxyUrlOf(mgr.fixedProxy) : "")).test_safe_valid();
    if (valid) {
      let nickname = '';
      try {
        const uc = await (new ZMNLXQClient(result.safe, result.skey, mgr.fixedProxy ? proxyUrlOf(mgr.fixedProxy) : "")).get('/getusercenter');
        if (uc && uc.nickname) nickname = uc.nickname;
      } catch (e) { /* ignore */ }
      logGlobal(`👤 用户：${nickname || mask_account(openid)}`);
      return { safe: result.safe, skey: result.skey, openid };
    }
    logGlobal(`账号 ${mask_account(openid)} 新 safe 无效`);
  }
  return null;
}

function proxyUrlOf(proxyDict) {
  if (!proxyDict) return "";
  return proxyDict.http || proxyDict.https || "";
}

// ==================== main ====================
async function main() {
  const accounts = loadAccounts();
  console.log("==============================");
  console.log("🚀 战马能量星球小程序签到");
  console.log(`📱 共配置 ${accounts.length} 个账号`);
  console.log("==============================");

  if (accounts.length === 0) {
    return 1;
  }

  const results = [];
  const validCredentials = [];

  for (let i = 0; i < accounts.length; i++) {
    const { openid: wxid, server: wxServer } = accounts[i];
    logGlobal(`>>> 账号 ${i + 1}/${accounts.length} : ${mask_account(wxid)}`);

    // 每账号只取一次代理，换 token 与业务请求共用同一代理 IP
    let proxyStr = "";
    if (ZM_PROXY_API_URL) {
      const pd = proxy_manager.get_proxy();
      if (pd) {
        proxyStr = pd.http || pd.https || "";
        const disp = proxyStr.split('@').pop();
        logGlobal(`🌐 代理: 启用***@${disp}`);
      }
    }

    const mgr = new AutoCookieManager(wxServer, proxyStr);
    let cred = null;
    try {
      cred = await get_or_refresh_safe(wxid, mgr);
    } catch (exc) {
      logGlobal(`❌ 账号[${i + 1}] ${mask_account(wxid)} 自动获取凭证异常：${String(exc).slice(0, 80)}`);
    }

    if (!cred) {
      logGlobal(`❌ 账号[${i + 1}] ${mask_account(wxid)} 自动获取凭证失败`);
      logGlobal("   请检查该微信是否在线、是否已授权战马能量星球小程序");
      results.push({ success: false, phone: mask_account(wxid), error: '登录失败', index: i + 1, daily: {} });
      if (i < accounts.length - 1) await sleep(2000);
      continue;
    }

    cred.openid = wxid;
    validCredentials.push({ cred, proxyStr });
    results.push(await run_account(cred, i + 1, proxyStr));

    // 品牌活动任务完成后，立即核算并打印该账号总积分
    const res = results[results.length - 1];
    if (res && res.success) {
      const startScore = (res.daily && res.daily.startScore != null) ? res.daily.startScore : 0;
      let endScore = startScore;
      try {
        const uc = await (new ZMNLXQClient(cred.safe, cred.skey, proxyStr)).get('/getusercenter');
        if (uc && uc.nowscore !== undefined) endScore = Number(uc.nowscore) || 0;
      } catch (e) { /* ignore */ }
      const diff = endScore - startScore;
      const sign = diff >= 0 ? '+' : '';
      logGlobal(`💰 总积分：${endScore}（本次 ${sign}${diff}）`);
      res.daily = res.daily || {};
      res.daily.points = endScore;
      res.daily.points_diff = diff;
    }

    if (i < accounts.length - 1) await sleep(2000);
  }

  // 饲料互助（多账号间互相偷/送）
  if (GANTA && validCredentials.length > 0) {
    // 阶段一：多账号互加好友（getranklist 带 fromsafe 参数才是加好友）
    logGlobal('\n========= 多账号互加好友 =========');
    for (let i = 0; i < validCredentials.length; i++) {
      const { cred, proxyStr } = validCredentials[i];
      logGlobal(`>>> 账号 ${i + 1}/${validCredentials.length} 添加互助好友`);
      const http = new ZMNLXQClient(cred.safe, cred.skey, proxyStr);
      const executor = new DailyTaskExecutor(http, new Logger());
      for (let j = 0; j < validCredentials.length; j++) {
        if (i !== j) {
          await executor.getranklist(true, validCredentials[j].cred.safe);
          await sleep(2000);
        }
      }
    }

    // 阶段二：加好友后重新拉取排行榜，收集可偷/可送的好友
    for (let i = 0; i < validCredentials.length; i++) {
      const { cred, proxyStr } = validCredentials[i];
      logGlobal(`\n========= 开始【第 ${i + 1} 个账号】好友互助=========`);
      const http = new ZMNLXQClient(cred.safe, cred.skey, proxyStr);
      const executor = new DailyTaskExecutor(http, new Logger());
      await executor.getranklist();
      await sleep(2000);
      const friends = executor.totalAccountsfrinds.slice();
      if (friends.length === 0) {
        logGlobal('🤝 互助好友：拉取好友列表为空，跳过');
        continue;
      }
      await executor.run_friend_help(friends, validCredentials.map(c => c.cred.safe));
      await sleep(2000);
    }
  }

  if (results.length === 0) {
    console.log("❌ 未获取到在线战马能量星球账号，请检查 wx_server_url / zm_openid");
    return 1;
  }

  dispatch_summary(new Logger(), results);
  const totalFailed = results.filter(r => !r.success).length;
  return totalFailed === 0 ? 0 : 1;
}

if (require.main === module) {
  main()
    .then(code => process.exit(code || 0))
    .catch(e => { console.log(e); process.exit(1); });
}

module.exports = {
  Logger, ProxyManager, AutoCookieManager, ZMNLXQClient, DailyTaskExecutor,
  parse_env_accounts, loadAccounts, main, run_account,
};
