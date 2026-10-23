/*
------------------------------------------
Author: anonymous
Date: 2026.09.08
Description: 毛铺草本荟小程序签到
Cron: 5 9,12,20 * * *
------------------------------------------
毛铺草本荟小程序签到 v1.0.1

功能：自动执行毛铺草本荟小程序每日签到并完成日常任务，支持多账号执行。

配置说明：
1. 微信 code 网关：（适配应用宝协议，自动通过code获取ck）
   wx_server_url                                       必填，自建授权服务器地址
   - 示例：http://127.0.0.1:8000
   - 脚本会自动拼接 /wxapp/getCode
   - 请求格式：POST {网关}/wxapp/getCode
   - 请求体：{"app_id": "wxefd0fe341e06b815", "ref": "openid"}

2. 账号变量：
   mpcbh_openid                                        必填，毛铺草本荟专属账号变量
   - 多账号支持使用 &、英文逗号、中文逗号或换行分隔
   - 示例：openid_a&openid_b 或 openid_a,openid_b

3. 代理变量（可选，适配品赞代理）：
   proxy_api_url                                   品赞代理 API 地址，开启后每个账号自动获取代理
   - 代理接口返回格式支持：纯 IP:PORT，或带账号密码的 IP:PORT ACCOUNT PASSWORD（品赞格式）
   - 示例：http://your-proxy-host:port/get
   - 仅在配置了本变量时启用 API 代理，未配置则不使用代理
   - 单账号固定代理：在账号后追加 #proxy=IP:PORT 可指定该账号专用代理

4. 青龙任务建议：
   名称：毛铺草本荟小程序签到
   命令：node mpcbh.js
   定时：每天运行 1 - 3 次即可，具体时间自行调整
------------------------------------------
*/

const { env } = require('node:process');
const { createHash } = require('node:crypto');
const axios = require('axios');
const { HttpsProxyAgent } = require('https-proxy-agent');

// ==================== 配置 ====================
const WX_SERVER = (env.wx_server_url || '').trim().replace(/\/+$/, '');
const MPCBH_OPENIDS = env.mpcbh_openid || '';
const PROXY_API_URL = (env.proxy_api_url || '').trim();
const SIGN_SECRET = 'DYSHJS^M&.YXZRGS';  // 毛铺草本荟 appsign 密钥

const WX_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a1b)XWEB/14185';
const REFERER = `https://servicewechat.com/wxefd0fe341e06b815/741/page-frame.html`;
const API_BASE = 'https://mpb.jingjiu.com';
const LOGIN_URL = `${API_BASE}/proxy-he/jp/api/loginauto`;
const BASE = `${API_BASE}/proxy-he/api`;            // 主站业务
const WORKER_BASE = `${API_BASE}/proxy-he/worker/api`;  // 游戏/活动 Worker 通道
const POINT_LOG_URL = `${BASE}/FlanUser/logPointList`;  // 积分明细（余额 my_point + 当月收支明细）

// 草本秋日轻松图鉴卡牌（id 与名称，抽中后随 userFinishs 提交）
const QIURITUJIAN_CARDS = [
  { id: 101, name: '桑叶' }, { id: 102, name: '桑叶' }, { id: 103, name: '桑叶' }, { id: 104, name: '桑叶' }, { id: 105, name: '桑叶' }, { id: 106, name: '桑叶' },
  { id: 201, name: '葛根' }, { id: 202, name: '葛根' }, { id: 203, name: '葛根' }, { id: 204, name: '葛根' }, { id: 205, name: '葛根' }, { id: 206, name: '葛根' },
  { id: 301, name: '苦荞麦' }, { id: 302, name: '苦荞麦' }, { id: 303, name: '苦荞麦' }, { id: 304, name: '苦荞麦' }, { id: 305, name: '苦荞麦' }, { id: 306, name: '苦荞麦' },
  { id: 401, name: '枸杞子' }, { id: 402, name: '枸杞子' }, { id: 403, name: '枸杞子' }, { id: 404, name: '枸杞子' }, { id: 405, name: '枸杞子' }, { id: 406, name: '枸杞子' },
  { id: 501, name: '针叶樱桃果' }, { id: 502, name: '针叶樱桃果' }, { id: 503, name: '针叶樱桃果' }, { id: 504, name: '针叶樱桃果' }, { id: 505, name: '针叶樱桃果' }, { id: 506, name: '针叶樱桃果' },
];

// ==================== 工具函数 ====================
function ts() { return new Date().toLocaleTimeString('zh-CN', { hour12: false }); }
// 全局日志（启动/汇总，不带账号前缀）
function log(msg) { console.log(`[${ts()}] ${msg}`); }
function md5(s, upper = false) {
  const h = createHash('md5').update(s, 'utf8').digest('hex');
  return upper ? h.toUpperCase() : h;
}
// openid 脱敏：保留前6后4，中间 ***
function maskWxid(id) {
  if (!id || id.length < 11) return id || '未知';
  return id.slice(0, 6) + '***' + id.slice(-4);
}
/** 报表用脱敏：owNAX6...i6OM */
function maskOpenidForReport(id) {
  const s = String(id || '');
  if (!s) return '未知账号';
  if (s.length <= 10) return s;
  return `${s.slice(0, 6)}...${s.slice(-4)}`;
}
function parseAccounts(raw) {
  const n = (raw || '').replace(/，/g, ',').replace(/,/g, '&').replace(/\n/g, '&').replace(/@/g, '&');
  return n.split('&').map(s => s.trim()).filter(Boolean);
}
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// 北京时间(UTC+8)日期，格式 YYYY-MM-DD（避免跨天凌晨 UTC 日期错位导致签到被拒）
function beijingDate(d = new Date()) {
  const bj = new Date(d.getTime() + 8 * 3600 * 1000);
  const p = n => String(n).padStart(2, '0');
  return `${bj.getUTCFullYear()}-${p(bj.getUTCMonth() + 1)}-${p(bj.getUTCDate())}`;
}
// 北京时间(UTC+8)年月（积分明细按月查询，月不带前导0）
function beijingYearMonth(d = new Date()) {
  const bj = new Date(d.getTime() + 8 * 3600 * 1000);
  return { year: bj.getUTCFullYear(), month: bj.getUTCMonth() + 1 };
}

// ==================== 代理 ====================
class ProxyManager {
  constructor(apiUrl) { this.apiUrl = apiUrl; }
  async getProxy() {
    if (!this.apiUrl) return null;
    try {
      const resp = await fetch(this.apiUrl, { signal: AbortSignal.timeout(10000) });
      if (!resp.ok) { log(`🌐 代理获取失败: ${resp.status}`); return null; }
      let t = (await resp.text()).trim();
      const parts = t.split(/\s+/);
      if (parts.length === 3) t = `http://${parts[1]}:${parts[2]}@${parts[0]}`;
      if (!t.startsWith('http')) t = 'http://' + t;
      const disp = t.includes('@') ? t.replace(/\/\/[^@]+@/, '//***:***@') : t;
      log(`🌐 代理获取成功: ${disp}`);
      return t;
    } catch (e) { log(`🌐 代理异常: ${e.message}`); return null; }
  }
}

// ==================== 签名 ====================
function getAppSign(data, authToken, paramOrder = []) {
  const apptime = String(Math.round(Date.now() / 1000));
  let signStr = apptime;
  for (const key of paramOrder) {
    if (key in data) signStr += `${key}${data[key]}`;
  }
  signStr += SIGN_SECRET + authToken;
  const appsign = md5(signStr, true).slice(-10);
  return { apptime, appsign };
}

// ==================== 登录客户端 ====================
class MpcbhClient {
  constructor(wxid) {
    this.wxid = wxid;
    this.token = '';
    this.userInfo = null;
  }

  // 账号级日志：保留时间戳前缀
  log(msg) { console.log(`[${ts()}] ${msg}`); }

  _headers(extra = {}) {
    const h = {
      'Accept': '*/*',
      'Content-Type': 'application/json',
      'Referer': REFERER,
      'User-Agent': WX_UA,
      'x-version': '0.0.1',
      'xweb_xhr': '1',
      'Authorization': this.token ? this.token : '',
    };
    return { ...h, ...extra };
  }

  /** 通过应用宝网关获取微信 code */
  async _getWxCode(retries = 3) {
    if (!WX_SERVER) { this.log('未设置 wx_server_url'); return null; }
    for (let i = 0; i < retries; i++) {
      try {
        const resp = await fetch(`${WX_SERVER}/wxapp/getCode`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0 MicroMessenger/8.0.50' },
          body: JSON.stringify({ app_id: 'wxefd0fe341e06b815', ref: this.wxid }),
          signal: AbortSignal.timeout(30000),
        });
        const data = await resp.json();
        if (data.code === 0) {
          const code = data.data?.result?.code || data.data?.code;
          if (code) return String(code);
        }
        this.log(`获取code失败: ${data.msg || '未知'} (${i + 1}/${retries})`);
      } catch (e) {
        this.log(`获取code异常: ${e.message.slice(0, 60)} (${i + 1}/${retries})`);
      }
      if (i < retries - 1) await sleep(3000);
    }
    return null;
  }

  /** code 换 access_token（loginauto） */
  async _codeToToken(code) {
    const now = Math.floor(Date.now() / 1000);
    const body = {
      code, unionid: '', user_id: '', user_sources: '0',
      system: {
        albumAuthorized: true, benchmarkLevel: -1, bluetoothEnabled: false,
        brand: 'microsoft', cameraAuthorized: true, fontSizeSetting: 15,
        language: 'zh_CN', locationAuthorized: true, locationEnabled: true,
        microphoneAuthorized: true, model: 'microsoft', notificationAuthorized: true,
        notificationSoundEnabled: true, pixelRatio: 1, platform: 'windows', power: 100,
        safeArea: { bottom: 780, height: 780, left: 0, right: 414, top: 0, width: 414 },
        screenHeight: 780, screenWidth: 414, statusBarHeight: 20,
        system: 'Windows 11 x64', theme: 'light', version: '3.9.10', wifiEnabled: true,
        windowHeight: 780, windowWidth: 414, SDKVersion: '3.10.3', enableDebug: false,
        host: { appId: '', env: 'WeChat' }, appName: 'wechat', devicePixelRatio: 1,
      },
      itime: now,
      isource: md5(`${this.wxid}${now}${Math.random()}`).toUpperCase(),
    };
    const cfg = { method: 'POST', url: LOGIN_URL, headers: this._headers(), data: body, timeout: 15000 };
    if (this.proxy) cfg.httpsAgent = new HttpsProxyAgent(this.proxy);
    const resp = await axios(cfg);
    const data = resp.data;
    if (data.code === 0 && data.data?.access_token) {
      return data.data;
    }
    this.log(`loginauto失败: ${data.message || JSON.stringify(data).slice(0, 100)}`);
    return null;
  }

  /** 完整登录流程 */
  async login(retries = 3) {
    for (let i = 0; i < retries; i++) {
      const code = await this._getWxCode();
      if (!code) { this.log(`获取code失败 (${i + 1}/${retries})`); await sleep(2000); continue; }
      this.log(`✨ 获取code成功: ${code.slice(0, 8)}***`);

      const loginData = await this._codeToToken(code);
      if (!loginData) { this.log(`code换token失败 (${i + 1}/${retries})`); await sleep(2000); continue; }

      this.token = loginData.access_token;
      this.userInfo = loginData;
      this.log(`✅ 登录成功`);
      return true;
    }
    return false;
  }

  /** 通用业务接口调用（自动带 appsign 签名）
   *  signData: 参与 appsign 计算的参数源（默认等于 data；某些接口签名源与请求体不一致，需单独指定）
   */
  async api(path, { method = 'POST', data = {}, paramOrder = [], signData = null } = {}) {
    const { apptime, appsign } = getAppSign(signData === null ? data : signData, this.token, paramOrder);
    const headers = {
      ...this._headers(),
      'apptime': apptime,
      'appsign': appsign,
      'Referer': `https://servicewechat.com/wxefd0fe341e06b815/508/page-frame.html`,
      'Referrer-Policy': 'unsafe-url',
    };
    const url = path.startsWith('http') ? path : `${API_BASE}${path}`;
    const cfg = { method, url, headers, timeout: 15000 };
    if (method === 'POST') cfg.data = data;
    if (this.proxy) cfg.httpsAgent = new HttpsProxyAgent(this.proxy);
    let resp;
    try {
      resp = await axios(cfg);
    } catch (e) {
      const text = (e.response && e.response.data) ? JSON.stringify(e.response.data) : (e.message || '');
      return { code: -1, message: text.slice(0, 200), _raw: text };
    }
    return resp.data;
  }

  // ==================== 积分查询 ====================
  // 端点：GET /proxy-he/api/FlanUser/logPointList
  //   参数：year/month（北京时间，月不带前导0）/type（空=全部）/pageNo/pageSize（服务端单页上限 300）
  //   返回：data.my_point 当前积分余额；data.data[] 当月明细，按 create_time 倒序，
  //         每条含 value（积分值）与 crm（recordType：SEND=入账 / DEDUCT=支出，changeTime：北京时间字符串）
  // 今日收支：逐页拉取当月明细，本地按 crm.changeTime 过滤当天，遇到早于当天的记录即停止翻页

  /** 拉取一页积分明细 */
  _pointLogPage(pageNo) {
    const { year, month } = beijingYearMonth();
    const q = `year=${year}&month=${month}&type=&pageNo=${pageNo}&pageSize=300`;
    return this.api(`${POINT_LOG_URL}?${q}`, { method: 'GET' });
  }

  /** 查询当前总积分，成功返回数值字符串，失败返回 null */
  async getIntegral() {
    try {
      const r = await this._pointLogPage(1);
      if (r && r.code === 0 && r.data && r.data.my_point !== undefined) {
        return String(r.data.my_point);
      }
      this.log(`⚠️ 积分查询失败: ${(r || {}).message || '无响应'}`);
      return null;
    } catch (e) {
      this.log(`⚠️ 积分查询异常: ${e.message.slice(0, 80)}`);
      return null;
    }
  }

  /**
   * 查询今日积分收支，返回 { total, count, outTotal, outCount }，失败返回 null
   *   total/count       = 今日入账合计与笔数
   *   outTotal/outCount = 今日支出合计与笔数
   */
  async getTodayIncome() {
    const day = beijingDate();
    // unix 时间戳 → 北京时间字符串（crm.changeTime 缺失时兜底）
    const fmtTime = (t) => {
      const d = new Date((Number(t) || 0) * 1000 + 8 * 3600 * 1000);
      const p = (n) => String(n).padStart(2, '0');
      return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())} ` +
        `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}`;
    };
    try {
      let total = 0, count = 0, outTotal = 0, outCount = 0;
      for (let pageNo = 1; pageNo <= 10; pageNo++) {
        const r = await this._pointLogPage(pageNo);
        if (!r || r.code !== 0 || !r.data) {
          if (pageNo === 1) {
            this.log(`⚠️ 今日积分明细查询失败: ${(r || {}).message || '无响应'}`);
            return null;
          }
          break;
        }
        const rows = r.data.data || [];
        let reachedOld = false;
        for (const x of rows) {
          const crm = x.crm || {};
          const changeTime = crm.changeTime || fmtTime(x.create_time);
          if (!String(changeTime).startsWith(day)) { reachedOld = true; break; }
          const v = Math.abs(Number(x.value) || 0);
          if (crm.recordType === 'DEDUCT' || (Number(x.value) || 0) < 0) {
            outTotal += v; outCount++;
          } else {
            total += v; count++;
          }
        }
        if (reachedOld || rows.length < 300) break;
      }
      const round = (n) => Math.round(n * 100) / 100;
      return { total: round(total), count, outTotal: round(outTotal), outCount };
    } catch (e) {
      this.log(`⚠️ 今日积分明细查询异常: ${e.message.slice(0, 80)}`);
      return null;
    }
  }

  /** 查询用户信息 */
  async queryUserInfo() {
    if (!this.token) { this.log('未登录'); return null; }
    const result = await this.api('/proxy-he/api/BlzAppletIndex/userInfoV2025', { method: 'POST', data: {} });
    if (result.code === 0) {
      const show = result.data?.user_show || {};
      this.log(`👤 用户：${show.name}`);
      return result.data;
    }
    this.log(`用户信息查询失败：${result.message || JSON.stringify(result).slice(0, 100)}`);
    return null;
  }

  /** 每日签到 */
  async signIn() {
    if (!this.token) { this.log('未登录'); return null; }
    const data = { date: beijingDate() };
    const result = await this.api('/proxy-he/api/FlanSignInDaily/adds', {
      method: 'POST', data, paramOrder: ['date'],
    });
    if (result.code === 0) {
      if (result.data?.is_first === -1) {
        this.log(`⏭️ 今日已签到`);
      } else {
        this.log(`✅ 签到成功，获得${result.data?.point_today}积分，明天将获得${result.data?.point_tomorrow}积分`);
      }
      return result.data;
    }
    this.log(`签到失败：${result.message || JSON.stringify(result).slice(0, 100)}`);
    return null;
  }

  // ==================== ccncommon 活动通用结算 ====================
  // userFinishs(playData) → datelUserDraws，返回奖品名，失败返回 false
  async _ccnFinish(aid, name, playData) {
    const finish = await this.api(`${WORKER_BASE}/opactivity/ccncommon/userFinishs`, {
      method: 'POST',
      data: {
        activity_id: aid, latitude: '', longitude: '', province: '', city: '', district: '',
        play_data_json: JSON.stringify(playData), play_finish_is: 1,
      },
      paramOrder: ['activity_id', 'play_finish_is'], signData: { activity_id: aid },
    });
    if (finish.code !== 0) { this.log(`${name} 结束失败：${finish.message}`); return false; }

    const draw = await this.api(`${WORKER_BASE}/opactivity/ccncommon/datelUserDraws`, {
      method: 'POST',
      data: { activity_id: aid, user_play_id: finish.data?.user_play_id || '', year: finish.data?.user_record_year || '' },
      paramOrder: ['activity_id', 'user_play_id'], signData: { activity_id: aid },
    });
    if (draw.code !== 0) { this.log(`${name} 抽奖失败：${draw.message}`); return false; }
    return draw.data?.awardLocal?.title || '未识别';
  }

  // ==================== 草本秋日轻松图鉴（摇一摇抽草本签） ====================
  // 流程：userMains 查次数 → userStarts → userNodeLists 查已收集图鉴 →
  //       随机选未收集卡牌 → userFinishs → datelUserDraws 抽奖
  // activity_id=771122，每日 1 次，累计 30 次
  async qiuritujianStart() {
    if (!this.token) return;
    const name = '草本秋日轻松图鉴';
    const aid = '771122';

    // 1) 查次数
    this.log(`🎮 开始 ${name} ...`);
    const mains = await this.api(`${WORKER_BASE}/offlinaectivity/ccncom26qiuritujian/userMains`, {
      method: 'POST', data: { activity_id: aid, latitude: '', longitude: '' },
      paramOrder: ['activity_id'], signData: { activity_id: aid },
    });
    if (mains.code !== 0) { this.log(`⚠️ ${name} 查看活动次数失败：${mains.message}`); return; }
    const times = mains.data?.today_play_num_can;
    if (!times || times <= 0) { this.log(`⏭️ ${name} 没次数啦`); return; }

    for (let n = 0; n < times; n++) {
      // 2) 开始
      const start = await this.api(`${WORKER_BASE}/opactivity/ccncommon/userStarts`, {
        method: 'POST', data: { activity_id: aid, latitude: '', longitude: '' },
        paramOrder: ['activity_id'], signData: { activity_id: aid },
      });
      if (start.code !== 0) { this.log(`${name} 开始失败：${start.message}`); return; }

      // 3) 已收集图鉴 + 随机选卡（未收集优先，全集收集后从全集随机）
      const nodes = await this.api(`${WORKER_BASE}/offlinaectivity/ccncom26qiuritujian/userNodeLists`, {
        method: 'POST', data: { activity_id: aid },
        paramOrder: ['activity_id'], signData: { activity_id: aid },
      });
      const done = ((nodes.code === 0 && nodes.data?.nodeFinishList) || []).map(x => x.id);
      const pool = QIURITUJIAN_CARDS.filter(x => !done.includes(x.id));
      const from = pool.length ? pool : QIURITUJIAN_CARDS;
      const card = from[Math.floor(Math.random() * from.length)];

      // 4) 完成 + 抽奖
      const award = await this._ccnFinish(aid, name, card);
      if (!award) return;
      this.log(`✅ ${name}成功，抽中【${card.name}】签，获得${award}`);
      if (n < times - 1) await sleep(3000);
    }
  }

  // ==================== 登高望远（10 层台阶登顶抽奖） ====================
  // 流程：dateUserMains 查次数 → userStarts → userFinishs（直接提交登顶 10/10 层）→ datelUserDraws 抽奖
  // activity_id=772122，每日 1 次，累计 30 次
  async denggaoStart() {
    if (!this.token) return;
    const name = '登高望远';
    const aid = '772122';

    // 1) 查次数
    this.log(`🎮 开始 ${name} ...`);
    const mains = await this.api(`${WORKER_BASE}/opactivity/ccncommon/dateUserMains`, {
      method: 'POST', data: { activity_id: aid, latitude: '', longitude: '' },
      paramOrder: ['activity_id'], signData: { activity_id: aid },
    });
    if (mains.code !== 0) { this.log(`⚠️ ${name} 查看活动次数失败：${mains.message}`); return; }
    const times = mains.data?.today_play_num_can;
    if (!times || times <= 0) { this.log(`⏭️ ${name} 没次数啦`); return; }

    for (let n = 0; n < times; n++) {
      // 2) 开始
      const start = await this.api(`${WORKER_BASE}/opactivity/ccncommon/userStarts`, {
        method: 'POST', data: { activity_id: aid, latitude: '', longitude: '' },
        paramOrder: ['activity_id'], signData: { activity_id: aid },
      });
      if (start.code !== 0) { this.log(`${name} 开始失败：${start.message}`); return; }

      // 3) 完成（登顶 10/10 层）+ 抽奖
      const award = await this._ccnFinish(aid, name, { level: 10, total: 10 });
      if (!award) return;
      this.log(`✅ ${name}成功，获得${award}`);
      if (n < times - 1) await sleep(3000);
    }
  }

  // ==================== 观看视频 ====================
  async taskViewVideoView() {
    if (!this.token) return;
    const data = { video_id: 'video-117' };
    const result = await this.api(`${BASE}/BlzAppletIndex/taskViewVideoView`, { method: 'POST', data, paramOrder: [] });
    if (result.code === 0) {
      if (result.data?.point === 0) { this.log('⏭️ 今日已观看过视频'); return; }
      this.log(`✅ 观看视频成功，${result.data?.task?.description || '未识别'}`);
    } else {
      this.log(`观看视频失败：${result.message}`);
    }
  }

  // ==================== 订阅消息 ====================
  // 订阅配置：tag 对应服务端活动标识，label 为日志展示名
  get subscribeConfig() {
    return [
      { tag: 'subscribe_message_202410', label: '订阅超级会员日' },
      { tag: 'subscribe_message_applet', label: '订阅毛铺草本荟小程序' },
      { tag: 'subscribe_message_suyuan', label: '订阅草本寻轻记' },
    ];
  }

  // tag 分别对应：subscribe_message_202410(订阅超级会员日) / subscribe_message_applet(订阅毛铺草本荟小程序) / subscribe_message_suyuan(订阅草本寻轻记)
  async taskSubscribeMessage(tag, label) {
    if (!this.token) return;
    const data = { tag };
    const result = await this.api(`${BASE}/BlzAppletIndex/taskSubscribeMessage`, { method: 'POST', data, paramOrder: [] });
    if (result.code === 0) {
      if (result.data?.point === 0) { this.log(`⏭️ 今日已${label}`); return; }
      this.log(`✅ ${label}成功，${result.data?.task?.description || '未识别'}`);
    } else {
      this.log(`⚠️ ${label}失败：${result.message}`);
    }
  }

  // ==================== 周五专属（周五 8:00-22:00） ====================
  isAfterFriday8AM() {
    const d = new Date();
    if (d.getDay() !== 5) return false;
    const total = d.getHours() * 60 + d.getMinutes();
    return total >= 480 && total <= 1320;
  }

  async memberdayStart() {
    if (!this.token) return;
    if (!this.isAfterFriday8AM()) { this.log('⚠️ 非周五8:00-22:00时间段，不执行周五俱乐部'); return; }
    this.log(`🎰 开始 周五俱乐部...`);
    const mains = await this.api(`${BASE}/BlzWeekActivity/memberdayUserMains`, { method: 'POST', data: {}, paramOrder: [] });
    if (mains.code !== 0) { this.log(`⚠️ 周五俱乐部查询失败：${mains.message}`); return; }
    if (!mains.data?.is_draw) { this.log('⏭️ 周五俱乐部无可用次数'); return; }
    if (!mains.data?.draw_ticket) { this.log('周五俱乐部获取ticket失败'); return; }
    this.log(`📊 周五俱乐部剩余次数：${mains.data.is_draw}`);
    await sleep(10000 + Math.floor(Math.random() * 10000));
    const draw = await this.api(`${BASE}/BlzWeekActivity/memberdayUserDraws`, {
      method: 'POST', data: { draw_ticket: mains.data.draw_ticket }, paramOrder: ['draw_ticket'],
    });
    if (draw.code === 0) {
      const award = draw.data?.AwardName || draw.data?.awardLocal?.title || '未识别';
      this.log(`✅ 周五俱乐部成功，获得${award}`);
    } else {
      this.log(`周五俱乐部抽奖失败：${draw.message}`);
    }
  }

  summary() {
    if (!this.userInfo) return null;
    return {
      access_token: this.token,
      user_id: String(this.userInfo.user_id),
      mobile: this.userInfo.mobile,
      name: this.userInfo.name,
      unionid: this.userInfo.unionid,
      openid: this.userInfo.openid,
      avatar: this.userInfo.avatar,
      point: this.userInfo.point,
      level: this.userInfo.level_box?.current?.name || '',
      updated_at: new Date().toISOString(),
    };
  }
}

// ==================== 执行报表 ====================
/** 执行报表 */
function printReport(results) {
  const total = results.length;
  const success = results.filter(r => r.success).length;
  const failed = total - success;
  const now = new Date();
  const p = (n) => String(n).padStart(2, '0');
  const stamp = `${now.getFullYear()}-${p(now.getMonth() + 1)}-${p(now.getDate())} ` +
    `${p(now.getHours())}:${p(now.getMinutes())}:${p(now.getSeconds())}`;

  const lines = [
    '==============================',
    `🕒 执行时间：${stamp}`,
    `📊 统计数据：成功 ${success} / 总计 ${total}`,
    `✅ 成功账号：${success} 个`,
    `❌ 失败账号：${failed} 个`,
  ];

  results.forEach((r, idx) => {
    lines.push(`👤 【账号${idx + 1}】${r.account}${r.user ? `（${r.user}）` : ''}`);
    lines.push(`${r.success ? '✅' : '❌'} 状态：${r.success ? '执行成功' : '执行失败'}`);
    if (r.success) {
      const d = r.daily || {};
      if (d.after !== null && d.after !== undefined) {
        if (d.before !== null && d.before !== undefined) {
          const diff = Number(d.after) - Number(d.before);
          lines.push(`💰 总积分: ${d.after}（本次 ${diff >= 0 ? '+' : ''}${diff}）`);
        } else {
          lines.push(`💰 总积分: ${d.after}`);
        }
      }
      const inc = d.income;
      if (inc) {
        lines.push(`💰 今日入账积分: ${inc.total} （${inc.count}笔）`);
        if (inc.outCount > 0) lines.push(`💸 今日支出积分: ${inc.outTotal} （${inc.outCount}笔）`);
      }
    } else {
      lines.push(`⚠️ 原因：${r.error || '登录失效 / 授权已过期'}`);
    }
  });

  lines.push(`======🎉 完成 ${success} / 共 ${total} 账号=======`);
  log('');   // 与上方账号日志空一行分隔
  log('[执行报表]');
  for (const line of lines) log(line);
}

// ==================== 主流程 ====================
async function main() {
  const wxids = parseAccounts(MPCBH_OPENIDS);
  if (!wxids.length) { log('未配置 mpcbh_openid（多账号用 & 或换行分隔）'); return 1; }
  if (!WX_SERVER) { log('未配置 wx_server_url'); return 1; }

  log('='.repeat(30));
  log('🚀 毛铺草本荟签到');
  log(`📱 共配置 ${wxids.length} 个账号`);
  log('='.repeat(30));

  const proxyMgr = new ProxyManager(PROXY_API_URL);
  let okCount = 0;
  const results = [];

  for (let i = 0; i < wxids.length; i++) {
    const wxid = wxids[i];
    if (i > 0) log(`${'='.repeat(30)}`);
    log(`>>> 账号 ${i + 1}/${wxids.length} : ${maskWxid(wxid)}`);

    const client = new MpcbhClient(wxid);
    if (PROXY_API_URL) {
      client.proxy = await proxyMgr.getProxy();
      if (client.proxy) log(`🌐 已启用代理: ${client.proxy.replace(/\/\/[^@]+@/, '//***:***@')}`);
      else log('🌐 代理获取失败，直连');
    }

    const ok = await client.login();
    let nick = '';
    if (ok) {
      okCount++;

      // 用户信息 + 执行前积分
      const info = await client.queryUserInfo();
      nick = (info && info.user_show && info.user_show.name) || '';
      const before = await client.getIntegral();
      if (before !== null) client.log(`💰 执行前积分：${before}`);
      await sleep(2000);

      // 每日签到
      client.log(`✍️ 开始 签到...`);
      await client.signIn();
      await sleep(2000);

      // 草本秋日轻松图鉴 + 登高望远
      await client.qiuritujianStart();
      await sleep(3000);
      await client.denggaoStart();
      await sleep(3000);

      // 观看视频
      client.log(`📺 开始 观看视频...`);
      await client.taskViewVideoView();
      await sleep(2000);

      // 订阅消息（配置见 subscribeConfig：超级会员日 + 毛铺草本荟小程序 + 草本寻轻记）
      client.log(`📩 开始 订阅消息..`);
      for (const sub of client.subscribeConfig) {
        await client.taskSubscribeMessage(sub.tag, sub.label);
        await sleep(2000);
      }

      // 周五专属
      await client.memberdayStart();
      await sleep(2000);

      // 执行后积分 + 今日收支
      const after = await client.getIntegral();
      if (after !== null) {
        client.log(`💰 执行后积分：${after}`);
        if (before !== null) {
          const diff = Number(after) - Number(before);
          client.log(`💰 总积分: ${after}（本次 ${diff >= 0 ? '+' : ''}${diff}）`);
        }
      }
      const income = await client.getTodayIncome();
      if (income) {
        client.log(`💰 今日入账积分: ${income.total}（${income.count}笔）`);
        if (income.outCount > 0) client.log(`💰 今日支出积分: ${income.outTotal}（${income.outCount}笔）`);
      }
      client.reportData = { before, after, income };
    } else {
      client.log(`❌ 授权已过期或ck无效，请重新获取`);
    }
    results.push({
      success: !!ok,
      account: maskOpenidForReport(wxid),
      user: nick,
      daily: client.reportData || {},
      error: ok ? '' : '登录失败：授权已过期或ck无效',
    });
    if (i < wxids.length - 1) await sleep(2000);
  }

  printReport(results);
  return okCount === wxids.length ? 0 : 1;
}

if (require.main === module) {
  main().then(code => process.exit(code)).catch(e => { console.error(e); process.exit(1); });
}

module.exports = { MpcbhClient, getAppSign, API_BASE, BASE, md5 };
