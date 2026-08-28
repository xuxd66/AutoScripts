/*
------------------------------------------
Author: anonymous
Date: 2026.08.28
Description: 美的会员小程序签到
Cron: 22 9,12,20 * * *
------------------------------------------
美的会员 v1.0.0

功能：自动执行美的会员小程序每日签到，支持多账号执行。

配置说明：
1. 微信 code 网关：（适配应用宝协议，ck自动获取）
   wx_server_url 或 WX_SERVER_URL                必填，自建授权服务器地址
   - 示例：http://127.0.0.1:8000
   - 脚本会自动拼接 /wxapp/getCode
   - 请求格式：POST {网关}/wxapp/getCode
   - 请求头：Content-Type: application/json
   - 请求体：{"app_id":"wx49a622805968d156","ref":"账号openid"}

2. 账号变量：
   midea_openid 或 MIDEA_OPENID                  推荐，美的会员专属账号变量
   - 多账号支持使用 &、英文逗号、中文逗号或换行分隔
   - 示例：openid_a&openid_b 或 openid_a,openid_b

3. 青龙任务建议：
   名称：美的会员签到
   命令：task mdhy.js
   定时：每天运行 1 - 3 次即可，具体时间自行调整
------------------------------------------
*/
const axios = require("axios");

// ============ 核心变量获取 ============
const APPID = "wx49a622805968d156";
const MIDEA_OPENID_RAW = process.env.midea_openid || process.env.MIDEA_OPENID || "";
const WX_SERVER_URL = process.env.wx_server_url || process.env.WX_SERVER_URL || "";

const REQUEST_TIMEOUT = 30000;
const LOGIN_APP_ID = "ee07f27990db48109efcccd322d3a873";
const LOGIN_APP_SECRET = "2646746f07bb46199aff49002e6dce81";
const LOGIN_API_KEY = "b6db9d5cf2d449538d3a0dd5d77b2e35";
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a13) UnifiedPCWindowsWechat(0xf2541938) XWEB/19823";

// ── 签到相关常量 ──
const SIGN_HOST = "mcsp-api.midea.com";
const SIGN_ACTV_ID = "401670810827462661";
const SIGN_ROOT_CODE = "DJ";
const SIGN_APP_CODE = "DJ_WX";
const SIGN_GAME_ID = "28";
const MINI_APP_VERSION = "3.0.334";

// 活动中心通用鉴权头（ucAccessToken 与 userKey 均为登录 token）
function buildImHeaders(token) {
    return {
        Host: SIGN_HOST,
        Connection: "keep-alive",
        "Content-Type": "application/json",
        appId: LOGIN_APP_ID,
        xweb_xhr: "1",
        ucAccessToken: token,
        appsecret: LOGIN_APP_SECRET,
        "User-Agent": UA,
        apikey: LOGIN_API_KEY,
        userKey: token,
        miniAppVersion: MINI_APP_VERSION,
        Accept: "*/*",
        Referer: `https://servicewechat.com/${APPID}/560/page-frame.html`,
        "Accept-Language": "zh-CN,zh;q=0.9",
    };
}

// 活动中心统一 POST（自动拼接 apikey）
async function imPost(path, bodyObj, token) {
    const url = `https://${SIGN_HOST}/api/cms_api/activity-center-im-service/im-svr/${path}?apikey=${LOGIN_API_KEY}`;
    const config = { method: "POST", url, headers: buildImHeaders(token), data: bodyObj };
    return requestWithProxy(config);
}

// 通用 BFF(.do) 接口：与活动中心共用同一套鉴权头，但走 mcsp.midea.com
async function bffPost(path, bodyObj, token) {
    const url = `https://mcsp.midea.com${path}`;
    const headers = buildImHeaders(token);
    headers.Host = "mcsp.midea.com";
    const config = { method: "POST", url, headers, data: bodyObj };
    return requestWithProxy(config);
}

// 查询会员总积分（getMultipleAccountScore.do）
async function getPoints(uid, token) {
    if (!token) return "-";
    try {
        const { data } = await bffPost(
            "/api/cms_bff/mcsp-uc-mvip-bff/integral/getMultipleAccountScore.do",
            { restParams: { uid: uid || "", accountBrandList: [1, 3, 2, 5] } },
            token);
        if (data?.code === "000000") {
            const s = data?.data?.score;
            return (s === undefined || s === null || s === "") ? "-" : String(s);
        }
        return "-";
    } catch (e) {
        return "-";
    }
}

// 全局精简数据缓存
const GLOBAL_NOTIFY_BUFFERS = [];

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

function random(min, max) {
    return Math.floor(Math.random() * (max - min + 1)) + min;
}

function parseAccounts(raw) {
    return String(raw || "").replace(/，/g, ",").replace(/,/g, "&").replace(/&/g, "\n").split("\n").map(i => i.trim()).filter(Boolean);
}

function mask(value) {
    value = String(value || "");
    if (value.length <= 12) return value;
    return `${value.slice(0, 6)}...${value.slice(-6)}`;
}

// 手机号脱敏
function maskMobile(value) {
    value = String(value || "");
    if (!/^\d{11}$/.test(value)) return value;
    return `${value.slice(0, 3)}****${value.slice(-4)}`;
}

function preview(value, limit = 800) {
    try {
        return JSON.stringify(value).slice(0, limit);
    } catch (e) {
        return String(value).slice(0, limit);
    }
}

async function requestWithProxy(config) {
    return await axios({
        timeout: REQUEST_TIMEOUT,
        proxy: false,
        ...config,
    });
}

async function getCode(openid) {
    if (!WX_SERVER_URL) {
        console.log("❌ [授权] 未配置环境变量 wx_server_url");
        return null;
    }
    const baseUrl = WX_SERVER_URL.trim().replace(/\/$/, '');
    const url = `${baseUrl}/wxapp/getCode`;
    try {
        const res = await axios.post(url, {
            app_id: APPID,
            ref: openid,
        }, {
            headers: { "Content-Type": "application/json" },
            timeout: 20000,
            proxy: false,
        });
        // 返回格式：{code:0,msg:"success",data:{openid,result:{code,errMsg}}}
        const code = res.data?.data?.result?.code;
        if (res.data?.code === 0 && code) {
            return code;
        }
        console.log(`❌ [授权] code 获取失败: ${JSON.stringify(res.data)}`);
        return null;
    } catch (e) {
        console.log(`❌ [授权] code 获取异常: ${e.message}`);
        return null;
    }
}

// ── 痛点修复：模糊/忽略大小写深度提取接口字段 ──
function findValueDeep(obj, keys) {
    if (!obj || typeof obj !== "object") return null;
    const lowerKeys = keys.map(k => k.toLowerCase());
    
    // 优先遍历当前层
    for (const [key, value] of Object.entries(obj)) {
        if (lowerKeys.includes(key.toLowerCase()) && value !== undefined && value !== null && value !== "") {
            return value;
        }
    }
    // 递归子层
    for (const value of Object.values(obj)) {
        if (value && typeof value === "object") {
            const found = findValueDeep(value, keys);
            if (found) return found;
        }
    }
    return null;
}

function extractCookies(headers) {
    const setCookie = headers?.["set-cookie"];
    if (!setCookie) return "";

    const arr = Array.isArray(setCookie) ? setCookie : [setCookie];
    const parts = [];
    for (const item of arr) {
        const first = String(item).split(";")[0];
        if (/^(uid|sukey)=/i.test(first)) {
            parts.push(first);
        }
    }
    return parts.length ? parts.join("; ") + ";" : "";
}

function extractLoginInfo(data, headers) {
    const ucAccessToken = findValueDeep(data, ["ucAccessToken", "accessToken", "token", "userToken", "access_token"]);
    let uid = findValueDeep(data, ["uid", "userId", "userCode", "uidCookie"]);
    let sukey = findValueDeep(data, ["sukey", "suKey", "sukeyCookie"]);
    const c4aUid = findValueDeep(data, ["c4aUid", "c4aUid", "cuid"]);

    const cookieFromHeader = extractCookies(headers);
    let cookie = cookieFromHeader;

    // 兜底策略1：uid+sukey 拼装
    if (!cookie && uid && sukey) {
        cookie = `uid=${uid}; sukey=${sukey};`;
    }
    // 兜底策略2：新接口不再返回 uid/sukey，改用 c4aUid 构造 cookie
    if (!cookie && c4aUid) {
        cookie = `c4aUid=${c4aUid};`;
    }

    return {
        ucAccessToken: ucAccessToken ? String(ucAccessToken) : "",
        cookie,
        uid: uid ? String(uid) : "",
        sukey: sukey ? String(sukey) : "",
        c4aUid: c4aUid ? String(c4aUid) : "",
    };
}

async function loginByCode(code) {
    const config = {
        method: "POST",
        url: "https://mcsp.midea.com/api/cms_bff/mcsp-uc-mvip-bff/app/login/wx/mini/getLoginInfo.do",
        headers: {
            Host: "mcsp.midea.com",
            appId: LOGIN_APP_ID,
            xweb_xhr: "1",
            appsecret: LOGIN_APP_SECRET,
            "User-Agent": UA,
            "Content-Type": "application/json",
            userKey: "",
            miniAppVersion: "3.0.269",
            apikey: LOGIN_API_KEY,
            Accept: "*/*",
            Referer: `https://servicewechat.com/${APPID}/554/page-frame.html`,
            "Accept-Language": "zh-CN,zh;q=0.9",
        },
        data: {
            jsCode: code,
            loginMode: 1,
            platformType: "WX_MEIDIDAOJIA_MINI",
            _timeStamp: Date.now(),
        },
    };

    try {
        const res = await requestWithProxy(config);
        const data = res.data;
        const info = extractLoginInfo(data, res.headers);
        return {
            ...info,
            raw: data,
            headers: res.headers,
        };
    } catch (e) {
        return { ucAccessToken: "", cookie: "", uid: "", sukey: "", raw: null, headers: null };
    }
}

async function getUserInfo(token) {
    // 用 tokenLogin/register 取手机号/昵称/uid
    if (!token) return { success: false, mobile: "-", points: "-", nickName: "", uid: "", raw: null };
    try {
        const { data } = await imPost("im/tokenLogin/register",
            { restParams: { actvId: SIGN_ACTV_ID, rootCode: SIGN_ROOT_CODE, appCode: SIGN_APP_CODE } },
            token);
        if (data?.code === "000000" || data?.code === 0) {
            const d = data?.data || {};
            return { success: true, mobile: d.phone || "-", points: "-", nickName: d.nickName || "", uid: d.uid || "", raw: data };
        }
        return { success: false, mobile: "-", points: "-", nickName: "", uid: "", raw: data };
    } catch (e) {
        return { success: false, mobile: "-", points: "-", nickName: "", uid: "", raw: null };
    }
}

async function signIn(token) {
    if (!token) return { success: false, message: "⚠️ 跳过每日签到(无Token)" };
    try {
        const { data } = await imPost("im/game/page/sign",
            { restParams: { gameId: SIGN_GAME_ID, actvId: SIGN_ACTV_ID, rootCode: SIGN_ROOT_CODE, appCode: SIGN_APP_CODE } },
            token);
        if (data?.code === "000000") {
            const d = data?.data || {};
            if (d.result === true) {
                let reward = d.dailyRewardInfo?.name
                    || (d.dailyRewardInfo?.points ? `+${d.dailyRewardInfo.points}积分` : "成功");
                if (/\d/.test(reward) && !reward.startsWith("+")) {
                    reward = `+${reward}`;
                }
                return { success: true, message: `✅ 签到成功(${reward})`, raw: data };
            }
            return { success: true, message: "⚠️ 今日已签到过", raw: data };
        }
        const msg = data?.msg || preview(data);
        return { success: false, message: `❌ 失败 (${msg})`, raw: data };
    } catch (e) {
        return { success: false, message: `❌ 异常 (${e.message})`, raw: null };
    }
}

async function runAccount(openid) {
    console.log(`\n🔄 正在处理 openid: ${mask(openid)}`);
    const summary = { openid, mobile: "-", nickName: "", before: "-", after: "-", sign1: "未执行" };

    await sleep(random(1000, 3000));

    const code = await getCode(openid);
    if (!code) {
        summary["sign1"] = "❌ 获取 code 失败";
        GLOBAL_NOTIFY_BUFFERS.push(summary);
        return;
    }

    const login = await loginByCode(code);
    if (!login.cookie && !login.ucAccessToken) {
        summary["sign1"] = "❌ 换绑账户失败";
        GLOBAL_NOTIFY_BUFFERS.push(summary);
        return;
    }

    if (login.ucAccessToken) {
        const before = await getUserInfo(login.ucAccessToken);
        summary.mobile = before.mobile;
        summary.nickName = before.nickName;
        summary.before = await getPoints(before.uid, login.ucAccessToken);
        summary.after = summary.before;
    }

    await sleep(random(1500, 3000));
    const s1 = await signIn(login.ucAccessToken);
    summary.sign1 = s1.message;

    if (login.ucAccessToken && s1.success) {
        await sleep(random(1500, 3000));
        const after = await getUserInfo(login.ucAccessToken);
        summary.after = await getPoints(after.uid, login.ucAccessToken);
    }

    GLOBAL_NOTIFY_BUFFERS.push(summary);
}

// ============ 程序入口主逻辑 ============
(async () => {
    console.log("==================================================");
    console.log("🔷 美的会员小程序签到启动...");
    console.log("==================================================");

    if (!MIDEA_OPENID_RAW) {
        console.log("❌ 未找到有效 midea_openid 账户配置！");
        return;
    }

    const openids = parseAccounts(MIDEA_OPENID_RAW);
    console.log(`📱 共加载 ${openids.length} 个美的会员账户`);

    for (const openid of openids) {
        try {
            await runAccount(openid);
            await sleep(random(2000, 4000));
        } catch (e) {
            console.log(`❌ 账户 ${openid} 发生未知错误: ${e.message}`);
        }
    }

    if (GLOBAL_NOTIFY_BUFFERS.length > 0) {
        const success = GLOBAL_NOTIFY_BUFFERS.filter(i => !String(i.sign1).startsWith('❌')).length;
        const failed = openids.length - success;
        const desp_lines = [
            "==============================",
            `🕒 执行时间：${new Date().toLocaleString("zh-CN", { hour12: false })}`,
            `📊 统计数据：成功 ${success} / 总计 ${openids.length}`,
            `✅ 成功账号：${success} 个`,
            `❌ 失败账号：${failed} 个`,
            "=============================="
        ];

        for (const item of GLOBAL_NOTIFY_BUFFERS) {
            const ok = !String(item.sign1).startsWith("❌");
            desp_lines.push(`${ok ? "🧑‍💻" : "🧟"} 【${mask(item.openid)}】${item.nickName ? "(" + item.nickName + ")" : ""} 手机：${maskMobile(item.mobile)}`);
            desp_lines.push(`💰 签到前积分: 【${item.before}】`);
            desp_lines.push(`📝 每日签到： ${item.sign1}`);
            desp_lines.push(`💰 签到后积分: 【${item.after}】`);
            desp_lines.push(`💰 总积分: 【${item.after}】`);
            desp_lines.push("------------------------------");
        }

        const final_desp = desp_lines.join("\n");
        console.log("\n[执行结果报表]\n" + final_desp);
    }
})().catch(e => {
    console.log(`❌ [全局异常] ${e.message}`);
});