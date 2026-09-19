/*
------------------------------------------
Author: anonymous
Date: 2026.09.20
Description: 顾家会员小程序签到
Cron: 20 8 * * *
------------------------------------------
顾家会员小程序签到 v1.1.3

功能：自动执行顾家会员（顾家家居）小程序每日签到并查询积分/会员信息，支持多账号执行。

配置说明：
1. 微信 code 网关：（适配应用宝协议，ck自动获取）
   wx_server_url                                       必填，自建授权服务器地址
   - 示例：http://127.0.0.1:8000
   - 脚本会自动拼接 /wxapp/getCode
   - 请求格式：POST {网关}/wxapp/getCode
   - 请求体：{"app_id": "wx0770280d160f09fe", "ref": "openid"}

2. 账号变量：
   gjjj_openid                                         推荐，顾家小程序专属账号变量
   - 多账号支持使用 &、英文逗号、中文逗号或换行分隔
   - 示例：openid_a&openid_b 或 openid_a,openid_b

3. 依赖安装：
   axios 
   - 安装命令：npm install axios
   - 若使用青龙面板，在依赖管理处添加 axios 安装即可。

4. 青龙任务建议：
   名称：顾家会员小程序签到
   命令：task gjjj.js
   定时：每天运行 1 次即可，具体时间自行调整
------------------------------------------
*/

const axios = require("axios");
const crypto = require("crypto");

const MINI_APP_ID = "wx0770280d160f09fe";
const PAGE_VERSION = "293";
const API_BASE = "https://mc.kukahome.com/club-server";
const INTEGRAL_BASE = "https://mc.kukahome.com/integral-server";
const BRAND_CODE = "K001";
const SMALL_APPLICATION_ID = "667516";
const SMALL_CRYPTO = "FH3yRrHG2RfexND8";
const VERSION_NUMBER = "2.0.184";
const USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) MicroMessenger/3.9.12 MiniProgramEnv/Windows WindowsWechat/WMPF";
const wx_server_url = process.env.wx_server_url;
if (!wx_server_url) {
  console.log("未配置环境变量 [wx_server_url]，请先配置微信 code 网关地址");
  process.exit(1);
}

const CK_NAME = "gjjj_openid";

// ---- 运行环境 ----
class Env {
  constructor(name) {
    this.name = name;
    this.userIdx = 1;
    this.userList = [];
    this.startTime = Date.now();
    this.log(`============ ${name} ============`);
  }

  log(...args) {
    console.log(args.join(" "));
  }

  checkEnv(name) {
    const raw = process.env[name] || "";
    this.userList = raw
      .split(/[\n&,，]/)
      .map((s) => s.trim())
      .filter(Boolean);
    if (!this.userList.length) {
      this.log(`未找到环境变量 [${name}]，请先配置账号`);
      process.exit(0);
    }
    this.log(`共 ${this.userList.length} 个账号`);
  }

  done() {
    const cost = ((Date.now() - this.startTime) / 1000).toFixed(2);
    this.log(`============ ${this.name} 执行结束，耗时 ${cost}s ============`);
  }
}

const $ = new Env("顾家小程序签到");

function md5(input) {
  return crypto.createHash("md5").update(String(input)).digest("hex");
}

function isObject(val) {
  return Object.prototype.toString.call(val) === "[object Object]";
}

function buildParameterBase(data) {
  if (!data) return null;
  if (Array.isArray(data) || typeof data === "string") return null;
  if (!isObject(data)) return null;
  const keys = Object.keys(data).sort((a, b) => {
    const ac = [...a].map((ch) => ch.charCodeAt(0));
    const bc = [...b].map((ch) => ch.charCodeAt(0));
    for (let i = 0; i < Math.min(ac.length, bc.length); i++) {
      if (ac[i] !== bc[i]) return ac[i] - bc[i];
    }
    return ac.length - bc.length;
  });
  const pairs = [];
  for (const key of keys) {
    const value = data[key];
    if (value === null || value === undefined || value === "") continue;
    if (Array.isArray(value)) continue;
    if (typeof value === "object" && value !== null) {
      pairs.push(`${key}=${JSON.stringify(value)}`);
      continue;
    }
    if (typeof value === "number" && value === 0) {
      pairs.push(`${key}=0`);
      continue;
    }
    pairs.push(`${key}=${value}`);
  }
  return pairs.length ? pairs.join("&") : null;
}

function buildParameterSign(data, timestamp) {
  const base = buildParameterBase(data);
  if (!base) return "";
  const salt = String(timestamp).substring(4, 10);
  return md5(md5(base) + salt);
}

class Task {
  constructor(openid) {
    this.index = $.userIdx++;
    this.openid = String(openid || "").trim();
    this.tmpToken = "";
    this.accessToken = "";
    this.memberId = "";
    this.userInfo = {};
  }

  applyToken(data = {}) {
    this.accessToken = data.accessToken || data.token || this.accessToken;
    this.memberId = String(data.memberId || this.memberId || "");
  }

  async request({ method = "POST", url, data = {}, params = {}, withAuth = true, withTmpToken = true }) {
    const timestamp = Date.now();
    const sign = md5(`${SMALL_APPLICATION_ID}${SMALL_CRYPTO}${timestamp}`).toLowerCase();
    const bodyForSign = method.toUpperCase() === "GET" ? params : data;
    const parameterSign = buildParameterSign(bodyForSign, timestamp);
    const headers = {
      "User-Agent": USER_AGENT,
      Referer: `https://servicewechat.com/${MINI_APP_ID}/${PAGE_VERSION}/page-frame.html`,
      Accept: "application/json, text/plain, */*",
      "Content-Type": "application/json",
      "X-Customer": this.memberId || "",
      brandCode: BRAND_CODE,
      appid: SMALL_APPLICATION_ID,
      "E-Opera": "",
      "xweb_xhr": "1",
      sign,
      timestamp,
      versionNumber: VERSION_NUMBER,
    };
    if (parameterSign) headers.parameterSign = parameterSign;
    if (withAuth && this.accessToken) headers.AccessToken = this.accessToken;
    if (withTmpToken && this.tmpToken) headers.tmpToken = this.tmpToken;

    const res = await axios.request({
      method,
      url,
      data,
      params,
      headers,
      timeout: 20000,
      validateStatus: () => true,
    });

    if (res.status !== 200) {
      throw new Error(`HTTP ${res.status}: ${JSON.stringify(res.data)}`);
    }
    const result = res.data || {};
    if (result.code !== undefined && ![0, 401, 402, 515].includes(Number(result.code))) {
      const err = new Error(result.message || result.msg || JSON.stringify(result));
      err.rawResponse = result;
      throw err;
    }
    return result;
  }

  async getWxCode() {
    const url = `${wx_server_url}/wxapp/getCode`;
    const res = await axios.request({
      method: "POST",
      url,
      data: { app_id: MINI_APP_ID, ref: this.openid },
      headers: { "Content-Type": "application/json" },
      timeout: 20000,
      validateStatus: () => true,
    });
    if (res.status !== 200) {
      throw new Error(`getCode HTTP ${res.status}: ${JSON.stringify(res.data)}`);
    }
    const body = res.data || {};
    if (Number(body.code) !== 0) {
      throw new Error(`getCode失败: ${body.msg || JSON.stringify(body)}`);
    }
    const code = body?.data?.result?.code || body?.data?.code;
    if (!code) throw new Error(`wx_server 未返回 code: ${JSON.stringify(body)}`);
    return code;
  }

  async login() {
    const code = await this.getWxCode();
    const identify = await this.request({
      method: "POST",
      url: `${API_BASE}/api/user/identify`,
      params: { code },
      withAuth: false,
      withTmpToken: false,
    });
    if (identify.code !== 0 || !identify.data) {
      throw new Error(`identify失败: ${identify.message || JSON.stringify(identify)}`);
    }
    if (Number(identify.data.status) !== 4) {
      throw new Error(`登录状态异常: status=${identify.data.status}`);
    }
    this.tmpToken = identify.data.token || "";
    if (!this.tmpToken) throw new Error("identify未返回tmpToken");

    const auth = await this.request({
      method: "POST",
      url: `${API_BASE}/api/user/authorizeLogin`,
      data: { source: "顾家小程序", contentName: "" },
      withAuth: false,
      withTmpToken: true,
    });
    if (auth.code !== 0 || !auth.data?.token) {
      throw new Error(`authorizeLogin失败: ${auth.message || JSON.stringify(auth)}`);
    }
    this.accessToken = auth.data.token;
    this.memberId = String(auth.data.memberId || "");
    this.tmpToken = "";
  }

  async getUserInfo() {
    const info = await this.request({
      method: "POST",
      url: `${API_BASE}/api/user/info`,
      data: {},
      withAuth: true,
      withTmpToken: false,
    });
    if (!info.data) throw new Error("user/info返回为空");
    this.userInfo = info.data;
    this.applyToken(info.data);
    const openidMask = this.openid.length >= 12
      ? this.openid.slice(0, 6) + "..." + this.openid.slice(-6)
      : this.openid;
    const nick = this.userInfo.nickName || this.userInfo.name || "";
    const mobile = this.userInfo.mobile || "";
    let label = `【${openidMask}】`;
    if (nick) label += `(${nick})`;
    if (mobile) label += ` 手机：${mobile}`;
    $.log(`账号[${this.index}] 👤 用户: ${label}`);
  }

  async ensureLogin() {
    await this.login();
    await this.getUserInfo();
    $.log(`账号[${this.index}] ✅ 登录成功`);
  }

  async getPoints() {
    const ret = await this.request({
      method: "POST",
      url: `${API_BASE}/front/member/personalCenter`,
      data: { t: Date.now() },
      withAuth: true,
      withTmpToken: false,
    });
    if (!ret || ret.point === undefined) throw new Error("查询积分失败");
    return Number(ret.point || 0);
  }

  async getSignCalendar() {
    const ret = await this.request({
      method: "GET",
      url: `${INTEGRAL_BASE}/user/sign/calendar`,
      params: {},
      withAuth: true,
      withTmpToken: false,
    });
    if (ret.code !== 0) throw new Error(ret.message || ret.msg || "查询签到日历失败");
    return ret.data || {};
  }

  async sign() {
    let ret;
    try {
      ret = await this.request({
        method: "POST",
        url: `${INTEGRAL_BASE}/scenePoint/scene/point`,
        data: {
          scene: "sign",
          brandCode: BRAND_CODE,
        },
        withAuth: true,
        withTmpToken: false,
      });

      if (ret.code === 0) {
        const gain = typeof ret.data === "number" ? ret.data : null;
        return { status: "success", ret, gain };
      }

      // request() 对 0/401/402/515 不抛异常，但仍需处理非 0 的情况
      const msg = ret.message || ret.msg || JSON.stringify(ret);
      if (/已签|重复|already|今日/.test(msg)) {
        return { status: "already", ret, gain: null };
      }

      throw new Error(msg);
    } catch (e) {
      // request() 对非 0/401/402/515 的 code 会直接 throw，需要在这里兜底判断"已签到"
      const msg = e.message || String(e);
      if (/已签|重复|already|今日/.test(msg)) {
        return { status: "already", ret: e.rawResponse, gain: null };
      }
      $.log(`账号[${this.index}] ❌ 签到接口返回失败: ${msg}`);
      throw e;
    }
  }

  // ---- 做任务：社区互动（点赞/收藏/分享晒家帖子赚积分）----
  async getTaskList() {
    const ret = await this.request({
      method: "GET",
      url: `${API_BASE}/front/member/selectPointTask`,
      params: { brandCode: BRAND_CODE },
      withAuth: true,
      withTmpToken: false,
    });
    if (ret.code === 0 && Array.isArray(ret.data) && ret.data.length) {
      const names = ret.data
        .map((t) => t.taskName || t.name || t.title || JSON.stringify(t))
        .join("、");
      $.log(`账号[${this.index}] 📋 可接任务(${ret.data.length}): ${names}`);
    } else {
      $.log(`账号[${this.index}] 📋 无可用任务列表或查询失败`);
    }
    return ret.data || [];
  }

  // 获取帖子列表（newWaterfall），翻页查找第一个未点赞/未收藏的帖子
  async findPost() {
    for (let pageNum = 1; pageNum <= 5; pageNum++) {
      const ret = await this._safe("帖子列表", () =>
        this.request({
          method: "POST",
          url: `${API_BASE}/applet/waterfall/newWaterfall`,
          data: { source: 1, pageNum, pageSize: 6 },
          withAuth: true,
          withTmpToken: false,
        })
      );
      if (!ret || ret.code !== 0) break;
      const items = Array.isArray(ret.data) ? ret.data : (ret.data && ret.data.list) || [];
      if (!items.length) break;
      for (const post of items) {
        const detail = await this._safe("帖子详情", () =>
          this.request({
            method: "POST",
            url: `${API_BASE}/front/postOrder/postOrderDetail`,
            data: { id: Number(post.id) },
            withAuth: true,
            withTmpToken: false,
          })
        );
        if (detail && detail.code === 0 && detail.data) {
          const likeStatus = detail.data.likeStatus;
          const collectStatus = detail.data.collectStatus;
          const untouched =
            (likeStatus == null && collectStatus == null) ||
            (String(likeStatus) === "0" && String(collectStatus) === "0");
          if (untouched) {
            const show = (post.title || "").toString().replace(/[\r\n]+/g, " ").trim();
            $.log(`账号[${this.index}] 🔍 找到可互动帖子: 「${show}」`);
            post.__fromFind = true;
            return post;
          }
        }
      }
    }
    return null;
  }

  async _safe(marker, fn) {
    try {
      return await fn();
    } catch (e) {
      $.log(`账号[${this.index}] ⚠️ ${marker}: ${e.message || e}`);
      return null;
    }
  }

  // 事件上报（晒家-点赞/收藏/分享）
  async pushEvent(eventId, content, targetId, targetName, businessId, businessName) {
    return this._safe("事件上报", () =>
      this.request({
        method: "POST",
        url: `${API_BASE}/front/member/pushEvent`,
        data: { eventId, content, targetId, targetName, businessId, businessName },
        withAuth: true,
        withTmpToken: false,
      })
    );
  }

  // 行为埋点上报
  async insertFootPoint(buriedPointLogo, subordinateTerminal, businessName, businessCode, currentPageLink) {
    return this._safe("埋点上报", () =>
      this.request({
        method: "POST",
        url: `${API_BASE}/front/foot/point/insertFootPoint`,
        data: {
          brandCode: BRAND_CODE,
          buriedPointLogo,
          subordinateTerminal,
          businessName: businessName || "",
          businessCode: businessCode || "",
          currentPageLink: currentPageLink || "",
        },
        withAuth: true,
        withTmpToken: false,
      })
    );
  }

  // 送积分（点赞/收藏/分享共用；网上脚本中积分动作在互动动作之后发送）
  // 注意：此接口是真正发放社区积分的地方，必须看到它的返回，不能吞错误。
  async likeSendPoint(postOrderId, triggerType, content, forwardType) {
    const data = { postOrderId: Number(postOrderId), triggerType, content };
    if (forwardType !== undefined) data.forwardType = forwardType;
    try {
      const ret = await this.request({
        method: "POST",
        url: `${API_BASE}/front/member/likeSendPoint`,
        data,
        withAuth: true,
        withTmpToken: false,
      });
      if (Number(ret.code) !== 0) {
        $.log(`账号[${this.index}] ⚠️ 送积分未成功: ${content} (code=${ret.code}, msg=${ret.message || ret.msg || ""})`);
      }
      return ret;
    } catch (e) {
      $.log(`账号[${this.index}] ❌ 送积分异常: ${content} (${e.message || e})`);
      return null;
    }
  }

  // 计分接口 likeSendPoint 用的随机帖子 id（仅占位，服务端去重维度=账号+triggerType，与帖子无关）
  _randPostId() {
    return Math.floor(Math.random() * 900000) + 100000;
  }

  // 点赞流程：事件上报 → (已赞则先取消) → 点赞 → 足迹 → 送积分
  // 设计：点赞接口为 toggle（已赞=1 再调一次变 0 取消）。为保证"每天可重复执行且每次都能计新分"，
  // 若帖子当前已点赞，先取消再点赞；未点赞则直接点赞。送分接口对点赞不做按天去重，
  // 故重复执行会重复计分（符合用户"每天可多次执行"的预期）。
  async likePost(post) {
    const postId = Number(post.id);
    const title = (post.title || post.id).toString().replace(/[\r\n]+/g, " ").trim();
    // 查当前点赞态，已赞则先取消（toggle 成 0），确保后续点赞能重新计新分
    const cur = await this._safe("帖子详情", () =>
      this.request({ method: "POST", url: `${API_BASE}/front/postOrder/postOrderDetail`, data: { id: postId }, withAuth: true, withTmpToken: false })
    );
    if (cur && cur.data && String(cur.data.likeStatus) === "1") {
      await this._safe("取消点赞", () =>
        this.request({ method: "POST", url: `${API_BASE}/front/postOrder/like`, data: { id: postId }, withAuth: true, withTmpToken: false })
      );
    }
    $.log(`账号[${this.index}] 👍 点赞: 「${title}」`);
    await this.pushEvent("c_showhome_like", "晒家-点赞", "300001", "晒家-点赞", String(postId), title);
    const likeRet = await this._safe("点赞", () =>
      this.request({ method: "POST", url: `${API_BASE}/front/postOrder/like`, data: { id: postId }, withAuth: true, withTmpToken: false })
    );
    if (!likeRet || Number(likeRet.code) !== 0) {
      $.log(`账号[${this.index}] ⚠️ 点赞接口未返回成功: ${JSON.stringify(likeRet && likeRet.data !== undefined ? likeRet.data : likeRet)}`);
    }
    await this.insertFootPoint("do_good_btn", "会员小程序", "", "", "");
    // 计分接口 postOrderId 用随机生成（仅占位，去重维度=账号+triggerType，与真实帖子无关）
    await this.likeSendPoint(this._randPostId(), 1, "点赞");
  }

  // 收藏流程：收藏 → (已藏则先取消) → 足迹 → 送积分（与点赞同理，toggle 可取消重做）
  async collectPost(post) {
    const postId = Number(post.id);
    const title = (post.title || post.id).toString().replace(/[\r\n]+/g, " ").trim();
    const cur = await this._safe("帖子详情", () =>
      this.request({ method: "POST", url: `${API_BASE}/front/postOrder/postOrderDetail`, data: { id: postId }, withAuth: true, withTmpToken: false })
    );
    if (cur && cur.data && String(cur.data.collectStatus) === "1") {
      await this._safe("取消收藏", () =>
        this.request({ method: "POST", url: `${API_BASE}/front/postOrder/collect`, data: { id: postId }, withAuth: true, withTmpToken: false })
      );
    }
    $.log(`账号[${this.index}] ⭐ 收藏: 「${title}」`);
    const collectRet = await this._safe("收藏", () =>
      this.request({ method: "POST", url: `${API_BASE}/front/postOrder/collect`, data: { id: postId }, withAuth: true, withTmpToken: false })
    );
    if (!collectRet || Number(collectRet.code) !== 0) {
      $.log(`账号[${this.index}] ⚠️ 收藏接口未返回成功: ${JSON.stringify(collectRet && collectRet.data !== undefined ? collectRet.data : collectRet)}`);
    }
    await this.insertFootPoint("buriedPointLogo", "会员小程序", "", "", "");
    // 计分接口 postOrderId 用随机生成（仅占位，去重维度=账号+triggerType，与真实帖子无关）
    await this.likeSendPoint(this._randPostId(), 2, "收藏");
  }

  // 分享流程：分享 → 足迹 → 送积分
  async sharePost(post) {
    const postId = Number(post.id);
    const title = (post.title || post.id).toString().replace(/[\r\n]+/g, " ").trim();
    $.log(`账号[${this.index}] 🔁 分享: 「${title}」`);
    const shareRet = await this._safe("分享", () =>
      this.request({ method: "POST", url: `${API_BASE}/front/postOrder/share`, data: { id: postId }, withAuth: true, withTmpToken: false })
    );
    if (!shareRet || Number(shareRet.code) !== 0) {
      $.log(`账号[${this.index}] ⚠️ 分享接口未返回成功: ${JSON.stringify(shareRet && shareRet.data !== undefined ? shareRet.data : shareRet)}`);
    }
    await this.insertFootPoint("share_friend_btn", "会员小程序", "", "", "");
    // 计分接口 postOrderId 用随机生成（仅占位，去重维度=账号+triggerType，与真实帖子无关）
    await this.likeSendPoint(this._randPostId(), 3, "微信好友转发", 2);
  }

  async communityTasks() {
    // 可在环境变量设置 GJJJ_COMMUNITY=0 关闭社区互动
    if (String(process.env.GJJJ_COMMUNITY) === "0") {
      $.log(`账号[${this.index}] ℹ️ 已关闭社区互动(GJJJ_COMMUNITY=0)`);
      return;
    }
    // GJJJ_COMMUNITY_FORCE=1：跳过"找未互动帖子"逻辑，直接取列表第一条做取消重做（每天多次执行都计新分）
    const force = String(process.env.GJJJ_COMMUNITY_FORCE) === "1";
    try {
      let post = null;
      if (!force) {
        post = await this.findPost(); // 优先取未点赞/未收藏的自然帖子
      }
      if (!post) {
        // 找不到未互动帖子（或全部已互动）→ 取 newWaterfall 第一条，由 likePost/collectPost 内部
        // 先做"取消"再重做，保证本次仍能计新分；分享由服务端按天去重天然幂等。
        const ret = await this._safe("帖子列表", () =>
          this.request({ method: "POST", url: `${API_BASE}/applet/waterfall/newWaterfall`, data: { source: 1, pageNum: 1, pageSize: 6 }, withAuth: true, withTmpToken: false })
        );
        const items = ret && ret.code === 0 ? (Array.isArray(ret.data) ? ret.data : (ret.data && ret.data.list) || []) : [];
        post = items[0] || null;
        if (post) $.log(`账号[${this.index}] 📝 无未互动帖子，取首条做取消重做: 「${(post.title || post.id).toString().slice(0, 30)}」`);
      }
      if (!post) {
        $.log(`账号[${this.index}] ⚠️ 社区无可用帖子`);
        return;
      }
      const title = (post.title || post.id).toString().replace(/[\r\n]+/g, " ").trim();
      if (!force && post.__fromFind) $.log(`账号[${this.index}] 📝 社区互动帖子: 「${title}」`);

      await this.likePost(post);
      await this.collectPost(post);
      await this.sharePost(post);

      $.log(`账号[${this.index}] 🎉 社区互动结束 (赞1/藏1/享1)`);
    } catch (e) {
      $.log(`账号[${this.index}] ❌ 社区互动异常: ${e.message || e}`);
    }
  }

  async run() {
    try {
      await this.ensureLogin();
      const pStart = await this.getPoints().catch(() => null);
      if (pStart !== null) $.log(`账号[${this.index}] 💰 当前积分: 【${pStart}】`);

      const cal = await this.getSignCalendar().catch(() => null);
      if (cal === null) {
        $.log(`账号[${this.index}] ⚠️ 签到日历查询失败，将尝试执行签到`);
      }

      let signRes = null;
      let cal2 = cal;
      const signed = cal ? !!cal.isTodaySigned : null;
      if (signed !== true) {
        // 日历未显示已签到才调用签到接口（正式运行行为）
        signRes = await this.sign();
        // 签到后重新查日历，用最新的 isTodaySigned 验证结果、signCount 展示天数
        cal2 = await this.getSignCalendar().catch(() => null);
      }

      // 以接口真实返回为准：接口已签到 / 或日历已签且未调用接口，均判定为今日已签到
      if ((signRes && signRes.status === "already") || (signed === true && !signRes)) {
        $.log(`账号[${this.index}] 📝 每日签到： ⚠️ 今日已签到`);
      } else {
        // 接口返回签到成功，再用日历 isTodaySigned 做一次确认
        const signConfirmed = cal2 ? !!cal2.isTodaySigned : true;
        if (!signConfirmed) {
          if (cal2 === null) {
            $.log(`账号[${this.index}] 📝 每日签到： ⚠️ 签到后日历查询失败，无法确认是否生效`);
          } else {
            $.log(`账号[${this.index}] 📝 每日签到： ❌ 签到接口返回成功但日历未确认`);
          }
        } else if (signRes && typeof signRes.gain === "number" && signRes.gain > 0) {
          // 本次获得积分取自签到接口返回的 data 字段
          $.log(`账号[${this.index}] 📝 每日签到： 🎉 签到成功 (+${signRes.gain}积分)`);
        } else {
          $.log(`账号[${this.index}] 📝 每日签到： 🎉 签到成功`);
        }
      }

      if (cal2 && cal2.signCount !== undefined && cal2.signCount !== null) {
        $.log(`账号[${this.index}] 📅 累计签到: 【${cal2.signCount}】天`);
      }

      // 做任务：查询任务列表 + 社区互动（点赞/收藏/分享）
      await this.getTaskList().catch(() => {});
      await this.communityTasks().catch(() => {});

      // 结尾再查积分，给出本次总变化。
      // 总积分一律以查分接口(getPoints)为准，不做任何本地累加。
      // personalCenter 有缓存，社区刚加的分可能延迟到账；因此轮询到「连续两次读数一致且已变化」才停止，
      // 避免出现“签到+10 让积分一变动就 break、社区分还没刷出来就被截断”的情况。
      let pEnd = pStart;
      let lastP = pStart;
      let stable = 0;
      for (let i = 0; i < 8; i++) {
        if (i > 0) await new Promise((r) => setTimeout(r, 2500));
        const pTry = await this.getPoints().catch(() => null);
        if (pTry === null) continue;
        pEnd = pTry;
        if (pTry === lastP) {
          stable++;
          if (pTry !== pStart && stable >= 2) break; // 已变化且连续两次稳定
          if (stable >= 3) break; // 始终未变化，已达稳定上限
        } else {
          stable = 0;
          lastP = pTry;
        }
      }
      if (pStart !== null) {
        // 签到积分取自签到接口返回；社区积分 = 查分总差额 - 签到分（全部来自查分接口，不依赖 likeSendPoint 返回）
        const delta = pEnd - pStart;
        const signGain = signRes && typeof signRes.gain === "number" ? signRes.gain : 0;
        const communityGain = delta - signGain;
        const parts = [];
        if (signGain !== 0) parts.push(`签到+${signGain}积分`);
        if (communityGain !== 0) parts.push(`社区+${communityGain}积分`);
        const detail = parts.length ? `（${parts.join("，")}）` : "";
        const arrow = delta === 0 ? `【${pEnd}】` : `【${pStart}】→【${pEnd}】 本次 +${delta}积分${detail}`;
        $.log(`账号[${this.index}] 💰 积分: ${arrow}`);
      }
    } catch (e) {
      const msg = e.message || String(e);
      $.log(`账号[${this.index}] 执行失败: ${msg}`);
    }
  }
}

!(async () => {
  $.checkEnv(CK_NAME);
  for (const openid of $.userList) {
    await new Task(openid).run();
  }
})()
  .catch((e) => $.log(e.message || e))
  .finally(() => $.done());
