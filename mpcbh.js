/*
------------------------------------------
Author: anonymous
Date: 2026.08.21
Description: 毛铺草本荟小程序签到
Cron: 5 9,12,20 * * *
------------------------------------------
毛铺草本荟小程序签到 v1.0.0

功能：自动执行毛铺草本荟小程序每日签到并完成日常任务，支持多账号执行。

配置说明：
1. 微信 code 网关：（适配应用宝协议，自动通过code获取ck）
   wx_server_url                                       必填，自建授权服务器地址
   - 示例：http://127.0.0.1:8000
   - 脚本会自动拼接 /wxapp/getCode
   - 请求格式：POST {网关}/wxapp/getCode
   - 请求体：{"app_id": "wxefd0fe341e06b815", "ref": "openid"}

2. 账号变量：
   mpcbh_openid                                         推荐，毛铺草本荟专属账号变量
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
const WORKER_BASE = `${API_BASE}/proxy-he/worker/api`;  // 游戏 Worker（BlzLonglActivity 通道）

// ==================== 工具函数 ====================
function ts() { return new Date().toLocaleTimeString('zh-CN', { hour12: false }); }
// 全局日志（启动/汇总，不带账号前缀）
function log(msg) { console.log(`[${ts()}] ${msg}`); }
// 带文字的标题行：左右补 =，使显示宽度与 50 个 = 的分割线一致
const SEP_W = 50;
function titleLine(text) {
  const w = [...text].reduce((a, c) => {
    const cp = c.codePointAt(0);
    return a + (cp > 0x2e80 || cp > 0xffff ? 2 : 1);
  }, 0);
  const pad = Math.max(0, SEP_W - w);
  const l = Math.floor(pad / 2), r = pad - l;
  return '='.repeat(l) + text + '='.repeat(r);
}
function md5(s, upper = false) {
  const h = createHash('md5').update(s, 'utf8').digest('hex');
  return upper ? h.toUpperCase() : h;
}
// openid 脱敏：保留前6后4，中间 ***
function maskWxid(id) {
  if (!id || id.length < 11) return id || '未知';
  return id.slice(0, 6) + '***' + id.slice(-4);
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

  /** 查询用户积分 */
  async queryPoints(label = '执行前') {
    if (!this.token) { this.log('未登录'); return null; }
    const result = await this.api('/proxy-he/api/user?is_jifen_clear_data=1', { method: 'GET' });
    if (result.code === 0) {
      const d = result.data;
      this.log(`💰 ${label}积分：${d.point}`);
      return d.point;
    }
    this.log(`❌ 积分查询失败：${result.message || JSON.stringify(result).slice(0, 100)}`);
    return null;
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

  // ==================== 通用游戏活动（代谢研究所/草本实验室/识草寻源，走 worker BlzLonglActivity 通道） ====================
  // 配置：label 前缀 → 活动名
  // 配置：label 前缀 → { 活动名, 是否带 use_type:'free' }
  // 代谢研究所单独不带 use_type（服务端返回 80888），草本实验室/识草寻源需带
  get activityConfig() {
    return {
      daixieyanjiusuo: { name: '代谢研究所', useType: false },
      caobenshiyanshi: { name: '草本实验室', useType: true },
      shicaoxunyuan: { name: '识草寻源', useType: true },
    };
  }

  async commonStart(label, name, useType = true) {
    if (!this.token) return;
    // 1) 查次数
    this.log(`🎮 开始 ${name} ...`);
    const mains = await this.api(`${WORKER_BASE}/BlzLonglActivity/${label}UserMains`, { method: 'POST', data: {}, paramOrder: [] });
    if (mains.code !== 0) { this.log(`⚠️ ${name} 查看活动次数失败：${mains.message}`); return; }
    const times = mains.data?.today_play_num_can;
    if (!times || times <= 0) { this.log(`⏭️ ${name} 没次数啦`); return; }
    const activityId = String(mains.data?.activity?.activity_id || '');

    for (let n = 0; n < times; n++) {
      // 2) 开始（代谢研究所不带 use_type，其余带 use_type:'free'）
      let start;
      for (let attempt = 0; attempt < 2; attempt++) {
        const startData = { activity_id: activityId, play_time_start: Math.round(Date.now() / 1000) };
        if (useType) startData.use_type = 'free';
        const paramOrder = useType
          ? ['activity_id', 'play_time_start', 'use_type']
          : ['activity_id', 'play_time_start'];
        start = await this.api(`${WORKER_BASE}/BlzLonglActivity/${label}UserDrawGet`, {
          method: 'POST', data: startData, paramOrder,
        });
        if (start.code === 0 && start.data?.user_record_id) break;
        if (attempt === 0) { this.log(`${name} 开始活动重试（${start.message}）`); await sleep(3000); }
      }
      if (start.code !== 0 || !start.data?.user_record_id) { this.log(`${name} 开始活动失败：${start.message}`); return; }
      const recordId = start.data?.user_record_id;

      await sleep(3000 + Math.floor(Math.random() * 5000));

      // 3) 结束
      const endData = {
        activity_id: activityId,
        play_time_finish: Math.round(Date.now() / 1000),
        user_record_id: recordId,
      };
      const end = await this.api(`${WORKER_BASE}/BlzLonglActivity/${label}UserDraws`, {
        method: 'POST', data: endData, paramOrder: ['activity_id', 'play_time_finish', 'user_record_id'],
      });
      if (end.code === 0) {
        const award = end.data?.title || end.data?.awardLocal?.title || '未识别';
        this.log(`✅ ${name}成功，获得${award}`);
      } else {
        this.log(`${name} 结束活动失败：${end.message}`);
      }
      await sleep(2000);
    }
  }

  // ==================== 草本寻轻记（春，走主站 BlzLongcaobenActivity 通道） ====================
  get cbxqjConfig() {
    return {
      qingxing: '春·万物清醒',
      chunye: '春·春野探秘',
    };
  }

  async cbxqjStart(label, name) {
    if (!this.token) return;
    // 1) 查次数（参考：body={}，依据 activity_status==="Can" 且 activity.activity_id 判断）
    this.log(`🎮 开始 ${name} ...`);
    const mains = await this.api(`${BASE}/BlzLongcaobenActivity/${label}UserMains`, { method: 'POST', data: {}, paramOrder: [] });
    if (mains.code !== 0) { this.log(`⚠️ ${name} 获取次数失败，原因：${mains.message}`); return; }
    const statusOk = mains.data?.activity_status === 'Can' || mains.data?.activity?.activity_status === 'Can';
    if (!statusOk || !mains.data?.activity?.activity_id) {
      this.log(`⏭️ ${name} 没次数啦`);
      return;
    }
    const activityId = String(mains.data.activity.activity_id);

    // 2) 开始
    const start = await this.api(`${BASE}/BlzLongcaobenActivity/${label}UserStarts`, {
      method: 'POST', data: { activity_id: activityId }, paramOrder: ['activity_id'], signData: {},
    });
    if (start.code !== 0) { this.log(`${name} 获取信息失败，原因：${start.message}`); return; }

    // 3) 抽记录（参考：body={activity_id, play_finish_is:-1}，user_record_id 在此步返回）
    const draw = await this.api(`${BASE}/BlzLongcaobenActivity/${label}UserDrawGet`, {
      method: 'POST', data: { activity_id: activityId, play_finish_is: -1 }, paramOrder: ['activity_id', 'play_finish_is'], signData: {},
    });
    if (draw.code !== 0) { this.log(`${name} 开始失败，原因：${draw.message}`); return; }
    const recordId = draw.data?.user_record_id;
    if (!recordId) { this.log(`${name} user_record_id获取失败`); return; }
    this.log(`🎯 获取到游戏记录id：${recordId}`);

    await sleep(3000 + Math.floor(Math.random() * 5000));

    // 4) 结束
    const end = await this.api(`${BASE}/BlzLongcaobenActivity/${label}UserDraws`, {
      method: 'POST', data: { user_record_id: recordId }, paramOrder: ['user_record_id'], signData: {},
    });
    if (end.code === 0) {
      const award = end.data?.award?.AwardName || end.data?.awardLocal?.title || '未识别';
      this.log(`✅ ${name} 成功，获得${award}`);
    } else {
      this.log(`${name} 结束失败，原因：${end.message}`);
    }
  }

  // ==================== 夏活动（美食配对/解救草本/夏日足球赛） ====================
  get xiaConfig() {
    return {
      '100000': '美食配对-线上常规版',
      '101030': '解救草本',
      '100014': '夏日轻松足球赛',
    };
  }

  async xiaStart(activityId, name) {
    if (!this.token) return;
    const aid = String(activityId);
    // 1) 活动详情
    this.log(`🎮 开始 ${name} ...`);
    const details = await this.api(`${BASE}/opactivity/ccncommon/activityDetails`, {
      method: 'POST', data: { activity_id: aid }, paramOrder: [], signData: { activity_id: aid },
    });
    if (details.code !== 0) { this.log(`⚠️ ${name} 获取次数失败，原因：${details.message}`); return; }
    if (!details.data?.activity?.activity_id) { this.log(`⏭️ ${name} 没次数啦`); return; }
    const innerId = String(details.data.activity.activity_id);

    // 2) 主页次数（body={activity_id, latitude, longitude}）
    const mains = await this.api(`${BASE}/opactivity/ccncommon/dateUserMains`, {
      method: 'POST', data: { activity_id: innerId, latitude: '', longitude: '' }, paramOrder: ['activity_id'], signData: { activity_id: aid },
    });
    if (mains.code !== 0) { this.log(`⚠️ ${name} 获取信息失败，原因：${mains.message}`); return; }
    if (mains.data?.activity_status !== 'Can' || !mains.data?.activity?.activity_id) {
      this.log(`⏭️ ${name} 今天没次数啦`);
      return;
    }

    // 3) 开始
    const start = await this.api(`${BASE}/opactivity/ccncommon/userStarts`, {
      method: 'POST', data: { activity_id: innerId }, paramOrder: ['activity_id', 'play_finish_is'], signData: { activity_id: aid },
    });
    if (start.code !== 0) { this.log(`${name} 开始失败，原因：${start.message}`); return; }

    // 4) 完成（user_play_id 在此步返回）
    const finish = await this.api(`${BASE}/opactivity/ccncommon/userFinishs`, {
      method: 'POST',
      data: { activity_id: aid, latitude: '', longitude: '', province: '', city: '', district: '', play_data_json: '', play_finish_is: 1 },
      paramOrder: ['activity_id', 'play_finish_is'], signData: { activity_id: aid },
    });
    if (finish.code !== 0) { this.log(`${name} 结束失败，原因：${finish.message}`); return; }
    const playId = finish.data?.user_play_id;
    const recordYear = finish.data?.user_record_year;
    if (!playId) { this.log(`${name} user_play_id获取失败`); return; }
    this.log(`🎯 获取到游戏记录id：${playId}`);

    await sleep(3000 + Math.floor(Math.random() * 5000));

    // 5) 抽奖
    const draw = await this.api(`${BASE}/opactivity/ccncommon/datelUserDraws`, {
      method: 'POST', data: { activity_id: aid, user_play_id: playId, year: recordYear }, paramOrder: ['activity_id', 'user_play_id'], signData: { activity_id: aid },
    });
    if (draw.code === 0) {
      const award = draw.data?.award?.AwardName || draw.data?.awardLocal?.title || '未识别';
      this.log(`✅ ${name} 成功，获得${award}`);
    } else {
      this.log(`${name} 结束失败，原因：${draw.message}`);
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

  // ==================== 每日调研 ====================
  // 逻辑：先调 tikuPaperDetails 拉取当日问卷。
  // - 若当天已完成（返回 questionList 为空或 todayIs=1），直接跳过（今日已完成调研）。
  // - 否则用 paper.questions 中的当日题号，从 questionList 题库中映射出要答的题目，
  //   每题取第一个选项作答，再提交 tikuPaperCreates（questions 为题目数组，optionList 只含选中项）。
  // 这样无论当天派发的题目是什么，都能自动作答领积分。
  async taskDailySurvey(activityCode = 'task_paper_tiku') {
    if (!this.token) return;

    // 1) 拉取当日问卷（当天未完成才返回 questionList 题库）
    const details = await this.api(`${BASE}/opactivity/paperActivity/tikuPaperDetails`, {
      method: 'POST', data: { activity_code: activityCode }, paramOrder: [],
    });
    if (details.code !== 0) { this.log(`⚠️ 每日调研拉取失败：${details.message || '未知'}`); return; }
    const d = details.data || {};
    const bank = d.questionList || [];
    // todayIs=1 表示今日已提交（前端 hasSubmittedToday = todayIs !== -1）
    if (d.todayIs === 1 || !bank.length) { this.log(`⏭️ 今日已完成调研`); return; }
    const paperId = d.paper?.paper_id;

    // 2) 当日题号：解析 paper.questions(JSON)，映射到题库；为空则用整个题库
    let todays;
    try {
      const ids = JSON.parse(d.paper?.questions || '[]').map(q => String(q.question_id));
      const map = new Map(bank.map(q => [String(q.question_id), q]));
      todays = ids.length ? ids.map(id => map.get(id)).filter(Boolean) : bank;
    } catch { todays = bank; }

    // 3) 每题作答：单选/多选取第一个选项；文本/日期题填默认文本（日期题需合理生日，否则报"日期题结果提交错误"）
    const DEFAULT_BIRTHDAY = '2000-12-05';
    const questions = todays.map(q => {
      const qtype = String(q.question_type || '').toLowerCase();
      const isText = qtype === 'basic_text' || qtype === 'basic_date';
      const chosen = isText ? [] : (q.optionList || []).slice(0, 1);
      return {
        question_id: q.question_id,
        question_code: q.question_code || '',
        question_type: q.question_type || 'basic_radio',
        question_tags: q.question_tags || '',
        question_title: q.question_title || '',
        question_result: isText ? (qtype === 'basic_date' ? DEFAULT_BIRTHDAY : '') : '',
        optionList: chosen.map(o => ({ option_id: o.option_id, option_title: o.option_title })),
      };
    });
    if (!questions.length) { this.log(`⚠️ 每日调研：无题目可答`); return; }

    const submit = await this.api(`${BASE}/opactivity/paperActivity/tikuPaperCreates`, {
      method: 'POST',
      data: { paper_id: paperId, questions: JSON.stringify(questions), activity_code: activityCode },
      paramOrder: [],
    });
    if (submit.code === 0) {
      const point = submit.data?.point ?? submit.data?.activity?.jifens;
      this.log(`✅ 每日调研成功${point ? `，获得${point}积分` : ''}`);
    } else if (submit.message?.includes('今日已完成') || submit.message?.includes('已提交')) {
      this.log(`⏭️ 今日已完成调研`);
    } else {
      this.log(`⚠️ 每日调研失败：${submit.message}`);
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

// ==================== 主流程 ====================
async function main() {
  const wxids = parseAccounts(MPCBH_OPENIDS);
  if (!wxids.length) { log('未配置 mpcbh_openid（多账号用 & 或换行分隔）'); return 1; }
  if (!WX_SERVER) { log('未配置 wx_server_url'); return 1; }

  log(titleLine('毛铺草本荟签到'));
  log(`📱 共配置 ${wxids.length} 个账号`);

  const proxyMgr = new ProxyManager(PROXY_API_URL);
  let okCount = 0;
  const results = [];

  for (let i = 0; i < wxids.length; i++) {
    const wxid = wxids[i];
    log(`${'='.repeat(50)}`);
    log(`>>> 账号 ${i + 1}/${wxids.length} : ${maskWxid(wxid)}`);

    const client = new MpcbhClient(wxid);
    if (PROXY_API_URL) {
      client.proxy = await proxyMgr.getProxy();
      if (client.proxy) log(`🌐 已启用代理: ${client.proxy.replace(/\/\/[^@]+@/, '//***:***@')}`);
      else log('🌐 代理获取失败，直连');
    }

    const ok = await client.login();
    if (ok) {
      okCount++;
      const sum = client.summary();
      results.push(sum);

      // 查询积分 + 用户信息
      await client.queryUserInfo();
      const beforePoint = await client.queryPoints('执行前');
      await sleep(2000);

      // 每日签到
      client.log(`✍️ 开始 签到...`);
      await client.signIn();
      await sleep(2000);

      // 通用游戏活动（代谢研究所/草本实验室/识草寻源，走 worker BlzLonglActivity 通道）
      for (const [label, cfg] of Object.entries(client.activityConfig)) {
        await client.commonStart(label, cfg.name, cfg.useType);
        await sleep(3000);
      }

      // 草本寻轻记（春，走主站 BlzLongcaobenActivity 通道）
      for (const [label, name] of Object.entries(client.cbxqjConfig)) {
        await client.cbxqjStart(label, name);
        await sleep(3000);
      }

      // 夏活动
      for (const [aid, name] of Object.entries(client.xiaConfig)) {
        await client.xiaStart(aid, name);
        await sleep(3000);
      }

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

      // 每日调研
      client.log(`📝 开始 每日调研..`);
      await client.taskDailySurvey();
      await sleep(2000);

      // 周五专属
      await client.memberdayStart();
      await sleep(2000);

      // 最终积分
      const afterPoint = await client.queryPoints('执行后');
      if (beforePoint != null && afterPoint != null) {
        client.log(`💰 总积分: ${afterPoint}（本次 +${afterPoint - beforePoint}）`);
      }
    } else {
      client.log(`❌ 授权已过期或ck无效，请重新获取`);
    }
    if (i < wxids.length - 1) await sleep(2000);
  }

  log(titleLine(`🎉 完成 ${okCount} / 共 ${wxids.length} 账号`));
  return okCount === wxids.length ? 0 : 1;
}

main().then(code => process.exit(code)).catch(e => { console.error(e); process.exit(1); });
