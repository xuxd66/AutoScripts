/*
------------------------------------------
Author: anonymous
Date: 2026.08.31
Description: 影视飓风小程序签到
Cron: 20 8 * * *
------------------------------------------
影视飓风小程序签到 v1.0.0

功能：自动执行影视飓风小程序签到，支持多账号执行。

配置说明：
1. 微信 code 网关：（自建微信 code 网关，适配应用宝/微信代理协议，ck自动获取）
   wx_server_url                                       必填，自建授权服务器地址
   - 示例：http://127.0.0.1:8000
   - 脚本会自动拼接 /wxapp/getCode
   - 请求格式：POST {网关}/wxapp/getCode
   - 请求体：{"app_id": "wx92782ef90ebc836d", "ref": "openid"}

2. 账号变量：
   ysjf_openid                                         推荐，影视飓风专属账号变量
   - 多账号支持使用 & 或换行分隔
   - 示例：openid_a&openid_b 或 openid_a,openid_b

3. 依赖安装：
   axios 
   - 安装命令：npm install axios
   - 若使用青龙面板，在依赖管理处添加 axios 安装即可。

4. 青龙任务建议：
   名称：影视飓风小程序签到
   命令：node ysjf.js
   定时：每天运行 1 次即可，具体时间自行调整
------------------------------------------
*/
// ---- 运行环境----
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
            .split(/[&\n,，]/)
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

const $ = new Env("影视飓风小程序签到");
const axios = require("axios");
// 微信 code 网关客户端
// 端点：POST {url}/wxapp/getCode
// 请求体：{"app_id": "...", "ref": "openid"}
// 响应：{"code":0,"msg":"success","data":{"openid":"...","result":{"code":"...","errMsg":"..."}}}
class WeChatServer {
    constructor({ url, appid, auth = "" } = {}) {
        this.url = (url || "").replace(/\/+$/, "");
        this.appid = appid;
        this.auth = auth;
    }

    async getCode(ref) {
        const resp = await axios.request({
            method: "POST",
            url: `${this.url}/wxapp/getCode`,
            headers: {
                "Content-Type": "application/json",
                ...(this.auth ? { Authorization: this.auth } : {}),
            },
            timeout: 15000,
            data: { app_id: this.appid, ref },
        });
        const body = resp.data;
        if (!body || body.code !== 0) {
            throw new Error(`网关返回异常: ${JSON.stringify(body)}`);
        }
        return body;
    }
}

const MINI_APP_ID = "wx92782ef90ebc836d";
const CLIENT_ID = "4d65249d377b2c3ed8";
const CLIENT_SECRET = "1cdc05151d64f3a4a6ebd0e9de64422a";
const GRANT_TYPE = "yz_union";
const CLIENT_BIZ = "weapp_wsc";
const KDT_ID = "149536603";
const USER_VERSION = "2.226.7.101";
const PAGE_VERSION = "17";
const API_BASE = "https://h5.youzan.com";
const USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) MicroMessenger/3.9.12 MiniProgramEnv/Windows WindowsWechat/WMPF";

let ckName = "ysjf_openid";

const wechat = new WeChatServer({
    url: process.env.wx_server_url,
    appid: MINI_APP_ID,
    auth: process.env.wx_auth || "",
});

function maskPhone(phone = "") {
    return String(phone).replace(/^(\d{3})\d{4}(\d{4})$/, "$1****$2");
}

function pickToken(data = {}) {
    return data.accessToken || data.access_token || "";
}

function isTokenError(message) {
    return /access_token|token|登录|授权|invalid session|session/i.test(String(message || ""));
}

class Task {
    constructor(openid) {
        this.index = $.userIdx++;
        this.openid = String(openid || "").trim();
        this.maskOpenid = this.openid.slice(0, 6) + "***" + this.openid.slice(-3);
        this.token = "";
        this.sessionId = "";
        this.cookie = "";
        this.kdtId = KDT_ID;
        this.userInfo = {};
        this.checkinId = "";
    }

    getLabel() {
        const nick = this.userInfo?.nick_name || this.userInfo?.nickName || "";
        const mobile = maskPhone(this.userInfo?.mobile);
        const who = nick ? `${nick} ${mobile}` : this.maskOpenid;
        return `👤 用户${this.index} [${who}]`;
    }

    async run() {
        try {
            await this.loginByWxCode();
            if (!this.token) return;

            const startPoints = await this.getPoints();
            if (startPoints !== undefined) {
                $.log(`${this.getLabel()} 💰 当前积分: 【${startPoints}】`);
            }

            await this.showCheckinPage();
            await this.doCheckin();

            const endPoints = await this.getPoints();
            if (startPoints !== undefined && endPoints !== undefined) {
                const delta = endPoints - startPoints;
                if (delta === 0) {
                    $.log(`${this.getLabel()} 💰 积分: 【${endPoints}】`);
                } else {
                    $.log(`${this.getLabel()} 💰 积分: 【${startPoints}】→【${endPoints}】 本次 ${delta >= 0 ? "+" : ""}${delta}积分`);
                }
            } else {
                $.log(`${this.getLabel()} ⚠️ 积分查询失败，无法汇总变化`);
            }
        } catch (e) {
            $.log(`${this.getLabel()} ❌ 执行失败: ${e.message || e}`);
        }
    }

    applyToken(data = {}) {
        this.token = pickToken(data);
        this.sessionId = data.sessionId || data.session_id || "";
        this.kdtId = String(data.kdtId || data.kdt_id || KDT_ID);
        this.cookie = data.cookie || "";
    }

    getHeaders(extra = {}) {
        const headers = {
            "User-Agent": USER_AGENT,
            "Referer": `https://servicewechat.com/${MINI_APP_ID}/${PAGE_VERSION}/page-frame.html`,
            "Accept": "*/*",
            "Extra-Data": JSON.stringify({
                sid: this.sessionId || "",
                version: USER_VERSION,
                clientType: "weapp-miniprogram",
                client: "weapp",
                bizEnv: "wsc",
            }),
            ...extra,
        };
        if (this.cookie) headers.Cookie = this.cookie;
        return headers;
    }

    getBaseParams(params = {}) {
        return {
            app_id: MINI_APP_ID,
            kdt_id: this.kdtId,
            access_token: this.token,
            ...params,
        };
    }

    async request({ method = "GET", path: apiPath, params = {}, data = {}, skipToken = false }) {
        const options = {
            method,
            url: `${API_BASE}${apiPath.startsWith("/") ? apiPath : `/${apiPath}`}`,
            headers: this.getHeaders(method === "POST" ? { "Content-Type": "application/json" } : {}),
            timeout: 15000,
            validateStatus: () => true,
        };
        options.params = skipToken ? params : this.getBaseParams(params);
        if (method !== "GET") options.data = data;

        const { data: result, status, headers } = await axios.request(options);
        if (headers["set-cookie"]) {
            this.cookie = headers["set-cookie"].map((item) => item.split(";")[0]).join("; ");
        }
        if (status !== 200) throw new Error(`HTTP ${status}: ${JSON.stringify(result)}`);
        if (!result || result.code !== 0) throw new Error(result?.msg || JSON.stringify(result));
        return result.data;
    }

    async getLoginCode() {
        const body = await wechat.getCode(this.openid);
        const code = body?.data?.result?.code;
        if (!code) throw new Error(`wx_server_url 未返回 code: ${JSON.stringify(body)}`);
        return code;
    }

    async authorize(code, data) {
        return this.request({
            method: "POST",
            path: "/wscshop/weapp/authorize.json",
            skipToken: true,
            data: { ...data, code },
        });
    }

    async loginByWxCode() {
        try {
            const code = await this.getLoginCode();
            let data;
            try {
                data = await this.authorize(code, {
                    appId: MINI_APP_ID,
                    clientId: CLIENT_ID,
                    clientSecret: CLIENT_SECRET,
                    grantType: GRANT_TYPE,
                });
            } catch (e) {
                data = await this.authorize(code, {
                    appId: MINI_APP_ID,
                    clientBiz: CLIENT_BIZ,
                });
            }
            this.applyToken(data);
            this.userInfo = data || {};
            $.log(`${this.getLabel()} ✅ 登录成功`);
        } catch (e) {
            $.log(`${this.getLabel()} ❌ 登录失败: ${e.message || e}`);
        }
    }

    async showCheckinPage() {
        try {
            const data = await this.request({ path: "/wscump/checkin/show_checkin_page_v2.json" });
            this.checkinId = data?.checkinId;
        } catch (e) {
            $.log(`${this.getLabel()} ⚠️ 获取签到活动失败: ${e.message || e}`);
            if (isTokenError(e.message || e)) {
                this.token = "";
                this.sessionId = "";
                this.cookie = "";
            }
        }
    }

    async doCheckin() {
        if (!this.checkinId) {
            $.log(`${this.getLabel()} ℹ️ 未获取到 checkinId，跳过签到`);
            return;
        }
        try {
            const data = await this.request({
                path: "/wscump/checkin/checkinV2.json",
                params: { checkinId: this.checkinId },
            });
            const awards = (data?.list || []).map((item) => item?.infos?.title).filter(Boolean).join(", ");
            const desc = data?.desc ? ` ${data.desc}` : "";
            $.log(`${this.getLabel()} 📝 每日签到： 🎉 签到成功${desc}${awards ? ` 奖励:${awards}` : ""}`);
        } catch (e) {
            const message = String(e.message || e);
            if (/已达最大参与次数|已签到|重复签到/.test(message)) {
                $.log(`${this.getLabel()} 📅 每日签到： ⚠️ 今日已签到`);
                return;
            }
            $.log(`${this.getLabel()} ❌ 签到失败: ${message}`);
            if (isTokenError(message)) {
                this.token = "";
                this.sessionId = "";
                this.cookie = "";
            }
        }
    }

    async getPoints() {
        try {
            const data = await this.request({ path: "/wscump/integral/user_points.json" });
            const points = data?.current_points ?? data?.real_points;
            return points;
        } catch (e) {
            $.log(`${this.getLabel()} ⚠️ 查询积分失败: ${e.message || e}`);
            return undefined;
        }
    }
}

!(async () => {
    if (!process.env.wx_server_url) {
        console.log("未配置环境变量 wx_server_url，请手动设置微信 code 网关地址后重试");
        return;
    }
    $.checkEnv(ckName);
    for (const openid of $.userList) {
        await new Task(openid).run();
    }
})()
    .catch((e) => $.log(e.message || e))
    .finally(() => $.done());
    