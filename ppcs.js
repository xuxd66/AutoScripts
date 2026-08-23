/*
Author: anonymous
Date: 2026.08.21
Description: 朴朴超市签到code本
Cron: 20 8,12,20 * * *
----------------------------------------------------------------------------------------------
朴朴超市签到code本 v1.0.0

功能：自动执行朴朴超市小程序每日签到和互相助力任务，支持多账号执行。

配置说明：
1. 微信 code 网关：（适配应用宝协议，ck 自动获取）
   wx_server_url                                   必填，自建授权服务器地址
   - 示例：http://127.0.0.1:8000
   - 脚本会自动拼接 /wxapp/getCode
   - 请求格式：POST {网关}/wxapp/getCode
   - 请求体：{"app_id": "<小程序appid>", "ref": "账号openid"}

2. 账号变量：
   ppcs_openid                                     推荐，朴朴超市专属账号变量
   - 多账号支持使用 &、英文逗号、中文逗号或换行分隔
   - 示例：openid_a&openid_b 或 openid_a,openid_b

3. 青龙任务建议：
   名称：朴朴超市签到
   命令：task ppcs.js
   定时：每天运行 1 - 3 次即可，具体时间自行调整
----------------------------------------------------------------------------------------------
*/

// ==================== 常量定义 ====================
const CommonUtils = createCommonUtils("朴朴超市");
const fs = require("fs");
const got = require("got");
const PROJECT_NAME = "pupu";
const DEFAULT_CITY_CODE = "991";
const REQUEST_TIMEOUT = 20000;
const MAX_RETRY_COUNT = 3;
const SCRIPT_VERSION = 1.01;
const SCRIPT_KEY = "pupu";
const VERSION_CHECK_URL = "https://leafxcy.coding.net/api/user/leafxcy/project/validcode/shared-depot/validCode/git/blob/master/code.json";
const USER_AGENT = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_1_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 MicroMessenger/8.0.46(0x18002e2c) NetType/WIFI Language/zh_CN miniProgram/wx122ef876a7132eb4";
const RETRY_WAIT_TIME = 2000;
const MAX_VERSION_CHECK_RETRY = 5;

// ==================== 应用宝网关登录常量 ====================
const PPCS_APPID = "wx122ef876a7132eb4";
const WX_SERVER_URL = process.env.wx_server_url || "";
const PPCS_OPENID = process.env.ppcs_openid || "";
const PPCS_VERSION = "2026081723";
const PPCS_REFERER = `https://servicewechat.com/${PPCS_APPID}/797/page-frame.html`;
const PPCS_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
  + "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 "
  + "MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI "
  + "MiniProgramEnv/Windows WindowsWechat/WMPF "
  + "WindowsWechat(0x63090a13) UnifiedPCWindowsWechat(0xf2541c37) XWEB/25364";

// ==================== 基础请求类 ====================
class BaseRequest {
  constructor(skipIndex = false) {
    this.index = skipIndex ? -1 : CommonUtils.userIdx++;
    this.name = "";
    this.valid = false;
    
    // 默认请求配置
    this.defaultHeaders = { "Connection": "keep-alive" };
    
    // 兼容不同版本的got
    if (typeof got.extend === 'function') {
      // 旧版got (v11及以下)
      const requestConfig = {
        retry: { limit: 0 },
        timeout: REQUEST_TIMEOUT,
        followRedirect: false,
        headers: this.defaultHeaders
      };
      this.got = got.extend(requestConfig);
      this.isOldGot = true;
    } else {
      // 新版got (v12+)
      this.got = got;
      this.isOldGot = false;
    }
  }

  // 获取日志前缀
  get_prefix(options = {}) {
    return "";
  }

  // 日志输出
  log(message, options = {}) {
    let prefix = this.get_prefix();
    CommonUtils.log(prefix + message, options);
  }

  // 扩展got配置（兼容不同版本）
  extendGot(newConfig) {
    if (this.isOldGot) {
      this.got = this.got.extend(newConfig);
    } else {
      // 新版本got，合并headers
      if (newConfig.headers) {
        Object.assign(this.defaultHeaders, newConfig.headers);
      }
    }
  }

  // 通用请求方法
  async request(requestOptions) {
    const REQUEST_ERROR_TYPES = ["RequestError"];
    const TIMEOUT_ERROR_TYPES = ["TimeoutError"];
    
    let options = CommonUtils.copy(requestOptions);
    let response = {};
    
    try {
      let result = null;
      let retryCount = 0;
      let functionName = options.fn || options.url;
      let validStatusCodes = options.valid_code || [200];
      
      // 处理form数据
      if (options.form) {
        for (let key in options.form) {
          if (typeof options.form[key] === "object") {
            options.form[key] = JSON.stringify(options.form[key]);
          }
        }
      }
      
      options.method = options?.method?.toUpperCase() || "GET";
      
      // 处理查询参数
      if (options.searchParams) {
        for (let key in options.searchParams) {
          if (typeof options.searchParams[key] === "object") {
            options.searchParams[key] = JSON.stringify(options.searchParams[key]);
          }
        }
      }
      
      if (options.debug_in) {
        console.log(options);
      }
      
      // 重试逻辑
      while (retryCount < MAX_RETRY_COUNT) {
        if (retryCount > 0) {
          await CommonUtils.wait(RETRY_WAIT_TIME * retryCount);
          
          let retryer = CommonUtils.get(options, "retryer", null);
          if (retryer) {
            let retryerOptions = CommonUtils.get(options, "retryer_opt", {});
            await retryer(options, retryerOptions);
          }
        }
        
        retryCount++;
        let error = null;
        
        try {
          let timeout = Number(options?.timeout?.request || options?.timeout || REQUEST_TIMEOUT);
          let isTimeout = false;
          let startTime = Date.now();
          
          // 构建请求Promise
          let requestPromise;
          if (this.isOldGot) {
            // 旧版got
            let gotClient = options.got_client || this.got;
            requestPromise = gotClient(options);
          } else {
            // 新版got - 需要构建完整的请求选项
            const method = (options.method || 'GET').toLowerCase();
            const requestUrl = options.url;
            const requestOpts = {
              method: options.method,
              headers: Object.assign({}, this.defaultHeaders, options.headers || {}),
              timeout: { request: timeout },
              retry: { limit: 0 },
              followRedirect: options.followRedirect !== undefined ? options.followRedirect : false,
              throwHttpErrors: false
            };
            
            // 添加请求体
            if (options.json) {
              requestOpts.json = options.json;
            }
            if (options.form) {
              requestOpts.form = options.form;
            }
            if (options.body) {
              requestOpts.body = options.body;
            }
            if (options.searchParams) {
              requestOpts.searchParams = options.searchParams;
            }
            
            // 新版got使用方法调用
            let gotInstance = this.got;
            
            // 处理ES6模块的default导出
            if (gotInstance.default && typeof gotInstance.default === 'function') {
              gotInstance = gotInstance.default;
            }
            
            if (typeof gotInstance === 'function') {
              // got本身是函数（某些版本）
              requestPromise = gotInstance(requestUrl, requestOpts);
            } else if (typeof gotInstance[method] === 'function') {
              // got有对应的方法（get, post, put等）
              requestPromise = gotInstance[method](requestUrl, requestOpts);
            } else {
              console.error('Got debug info:', {
                typeofGot: typeof this.got,
                typeofGotInstance: typeof gotInstance,
                hasMethod: !!gotInstance[method],
                method: method,
                availableMethods: Object.keys(gotInstance).filter(k => typeof gotInstance[k] === 'function')
              });
              throw new Error('Unsupported got version - method: ' + method);
            }
          }
          
          let timeoutHandle = setTimeout(() => {
            isTimeout = true;
            if (requestPromise.cancel) {
              requestPromise.cancel();
            }
          }, timeout);
          
          await requestPromise.then(
            successResponse => { result = successResponse; },
            errorResponse => { 
              error = errorResponse; 
              result = errorResponse.response;
            }
          ).catch(err => {
            // 捕获取消或其他错误
            error = err;
            result = err.response;
          }).finally(() => clearTimeout(timeoutHandle));
          
          let endTime = Date.now();
          let duration = endTime - startTime;
          let statusCode = result?.statusCode || null;
          
          if (isTimeout || TIMEOUT_ERROR_TYPES.includes(error?.name)) {
            let errorInfo = "";
            if (error?.code) {
              errorInfo += "(" + error.code;
              if (error?.event) {
                errorInfo += ":" + error.event;
              }
              errorInfo += ")";
            }
            this.log("⏳ [" + functionName + "]请求超时" + errorInfo + "(" + duration + "ms)，重试第" + retryCount + "次");
          } else if (REQUEST_ERROR_TYPES.includes(error?.name)) {
            this.log("⚠️ [" + functionName + "]请求错误(" + error.code + ")(" + duration + "ms)，重试第" + retryCount + "次");
          } else {
            if (statusCode) {
              if (error && !validStatusCodes.includes(statusCode)) {
            this.log("⚠️ 请求[" + functionName + "]返回[" + statusCode + "]");
              }
            } else {
              let { code = "unknown", name = "unknown" } = error || {};
              this.log("⚠️ 请求[" + functionName + "]错误[" + code + "][" + name + "]");
            }
            break;
          }
        } catch (exception) {
          this.log("⚠️ [" + functionName + "]请求错误(" + exception.message + ")，重试第" + retryCount + "次");
        }
      }
      
      if (result === null || result === undefined) {
        return { statusCode: -1, headers: null, result: null };
      }
      
      let { statusCode, headers, body } = result;
      let shouldDecodeJson = CommonUtils.get(options, "decode_json", true);
      
      if (body && shouldDecodeJson) {
        try {
          body = JSON.parse(body);
        } catch {}
      }
      
      response = { statusCode, headers, result: body };
      
      if (options.debug_out) {
        console.log(response);
      }
    } catch (exception) {
      console.log(exception);
    } finally {
      return response;
    }
  }
}

// ==================== 全局请求实例 ====================
let globalRequest = new BaseRequest(true);

// ==================== 应用宝网关登录 ====================
// 通过网关 getCode → silent_login 获取新 token
async function silentLoginViaGateway(ref, logFn, index) {
  let log = logFn || CommonUtils.log;
  if (!WX_SERVER_URL) {
    log("缺少环境变量 wx_server_url，无法通过网关登录");
    return null;
  }
  if (!ref) {
    log("缺少 ppcs_openid，无法通过网关登录");
    return null;
  }

  // Step 1: getCode
  log(">>> 账号 " + (index + 1) + "/" + CommonUtils.userCount + " : " + (ref.length > 10 ? ref.substring(0, 6) + "..." + ref.slice(-4) : ref));
  let codeRes = await globalRequest.request({
    fn: "getCode",
    method: "post",
    url: WX_SERVER_URL + "/wxapp/getCode",
    json: { app_id: PPCS_APPID, ref: ref }
  });

  let codeData = codeRes?.result;
  if (!codeData || codeData.code !== 0) {
    log("❌ getCode 失败: " + JSON.stringify(codeData));
    return null;
  }
  let wxCode = codeData.data?.result?.code;
  if (!wxCode) {
    log("❌ getCode 返回无 code: " + JSON.stringify(codeData));
    return null;
  }
  log("🔑 获取code成功：code=" + wxCode.substring(0, 20) + "...");

  // Step 2: silent_login
  let loginRes = await globalRequest.request({
    fn: "silent_login",
    method: "post",
    url: "https://cauth.pupuapi.com/clientauth/user/society/miniapp/silent_login",
    headers: {
      "User-Agent": PPCS_UA,
      "Accept": "application/json",
      "Content-Type": "application/json",
      "pp-version": PPCS_VERSION,
      "pp-os": "0",
      "Referer": PPCS_REFERER
    },
    json: { code: wxCode }
  });

  let loginData = loginRes?.result;
  if (!loginData || loginData.errcode !== 0) {
    log("❌ silent_login 失败: " + JSON.stringify(loginData));
    return null;
  }

  let d = loginData.data;
  log("✅ 登录成功  token=" + (d.token || "").substring(0, 16) + "...");
  log("👤 用户: " + (d.nick_name || "未知"));
  return d;
}

// ==================== 朴朴用户类 ====================
class PupuUser extends BaseRequest {
  constructor(cookieString, ref) {
    super();
    
    let parts = (cookieString || "").split("#");
    this.refresh_token = parts[0] || "";
    this.remark = parts?.[1] || "";
    this.ref = ref || "";
    this.open_id = "";
    this.suid = "";
    this.team_id = "";
    this.team_need_help = false;
    this.team_can_help = true;
    this.team_max_help = 0;
    this.team_helped_count = 0;
    
    this.extendGot({
      headers: { "User-Agent": USER_AGENT }
    });
  }

  // 登录（直接走网关 silent_login）
  async user_refresh_token(options = {}) {
    return await this.silent_login();
  }

  // 网关 silent_login（getCode → silent_login → 设置 token/refresh_token/open_id/suid）
  async silent_login() {
    let success = false;
    
    try {
      let d = await silentLoginViaGateway(this.ref, (msg) => this.log(msg), this.index);
      if (!d) {
        return false;
      }
      
      this.valid = true;
      this.access_token = d.token;
      this.refresh_token = d.refresh_token;
      this.open_id = d.open_id || "";
      this.suid = d.suid || "";
      this.user_id = d.user_id;
      this.name = this.remark || d.nick_name || "";
      
      this.extendGot({
        headers: {
          "User-Agent": PPCS_UA,
          "pp-version": PPCS_VERSION,
          "pp-os": "0",
          "Referer": PPCS_REFERER,
          "Authorization": "Bearer " + d.token,
          "pp-userid": String(d.user_id),
          "open-id": d.open_id || "",
          "pp-suid": d.suid || ""
        }
      });
      
      success = true;
      await this.user_info();
    } catch (exception) {
      this.log("❌ 登录异常: " + exception.message);
    } finally {
      return success;
    }
  }

  // 获取用户信息
  async user_info(options = {}) {
    try {
      const requestConfig = {
        fn: "user_info",
        method: "get",
        url: "https://cauth.pupuapi.com/clientauth/user/info"
      };
      
      let { result, statusCode } = await this.request(requestConfig);
      let errorCode = CommonUtils.get(result, "errcode", statusCode);
      
      if (errorCode === 0) {
        let { phone, invite_code } = result?.data;
        this.phone = phone;
        this.name = this.remark || this.name || phone || "";
        this.invite_code = invite_code;
      } else {
        let errorMessage = CommonUtils.get(result, "errmsg", "");
        this.log("❌ 查询用户信息失败[" + errorCode + "]: " + errorMessage);
      }
    } catch (exception) {
      console.log(exception);
    }
  }

  // 根据城市选择附近位置
  async near_location_by_city(options = {}) {
    try {
      let requestConfig = {
        fn: "near_location_by_city",
        method: "get",
        url: "https://j1.pupuapi.com/client/store/place/near_location_by_city/v2",
        searchParams: {
          lng: "119.31" + CommonUtils.randomString(4, CommonUtils.ALL_DIGIT),
          lat: "26.06" + CommonUtils.randomString(4, CommonUtils.ALL_DIGIT)
        }
      };
      
      let { result, statusCode } = await this.request(requestConfig);
      let errorCode = CommonUtils.get(result, "errcode", statusCode);
      
      if (errorCode === 0) {
        let locationList = result?.data;
        this.location = CommonUtils.randomList(locationList);
        
        let { service_store_id, city_zip, lng_x, lat_y } = this.location;
        this.store_id = service_store_id;
        this.zip = city_zip;
        this.lng = lng_x;
        this.lat = lat_y;
        
        this.extendGot({
          headers: {
            "pp_storeid": service_store_id,
            "pp-cityzip": city_zip
          }
        });
      } else {
        let errorMessage = CommonUtils.get(result, "errmsg", "");
        this.log("❌ 选取随机地点失败[" + errorCode + "]: " + errorMessage);
      }
    } catch (exception) {
      console.log(exception);
    }
  }

  // 查询签到状态
  async sign_index(options = {}) {
    try {
      const requestConfig = {
        fn: "sign_index",
        method: "get",
        url: "https://j1.pupuapi.com/client/game/sign/v2/index"
      };
      
      let { result, statusCode } = await this.request(requestConfig);
      let errorCode = CommonUtils.get(result, "errcode", statusCode);
      
      if (errorCode === 0) {
        let { is_signed } = result?.data;
        
        if (is_signed) {
          this.log("📅 [每日签到] 今天已签到");
        } else {
          await this.do_sign();
        }
      } else {
        let errorMessage = CommonUtils.get(result, "errmsg", "");
        this.log("📅 [每日签到] 查询签到信息失败[" + errorCode + "]: " + errorMessage);
      }
    } catch (exception) {
      console.log(exception);
    }
  }

  // 执行签到
  async do_sign(options = {}) {
    try {
      const requestConfig = {
        fn: "do_sign",
        method: "post",
        url: "https://j1.pupuapi.com/client/game/sign/v2",
        searchParams: { supplement_id: "" }
      };
      
      let { result, statusCode } = await this.request(requestConfig);
      let errorCode = CommonUtils.get(result, "errcode", statusCode);
      
      if (errorCode === 0) {
        let { daily_sign_coin, coupon_list = [] } = result?.data;
        let rewards = [];
        
        rewards.push(daily_sign_coin + "积分");
        
        for (let coupon of coupon_list) {
          let conditionAmount = (coupon.condition_amount / 100).toFixed(2);
          let discountAmount = (coupon.discount_amount / 100).toFixed(2);
          rewards.push("满" + conditionAmount + "减" + discountAmount + "券");
        }
        
        this.log("📅 [每日签到] 签到成功: " + rewards.join(", "));
      } else {
        let errorMessage = CommonUtils.get(result, "errmsg", "");
        this.log("📅 [每日签到] 签到失败[" + errorCode + "]: " + errorMessage);
      }
    } catch (exception) {
      console.log(exception);
    }
  }

  // 获取组队码(2026-08 接口升级为 v3, 返回 data.team_id)
  async get_team_code(options = {}) {
    try {
      const requestConfig = {
        fn: "get_team_code",
        method: "post",
        url: "https://j1.pupuapi.com/client/game/coin_share/team/v3",
        json: {}
      };

      let { result, statusCode } = await this.request(requestConfig);
      let errorCode = CommonUtils.get(result, "errcode", statusCode);

      if (errorCode === 0) {
        let data = result?.data;
        // 兼容: 新版返回 {success, team_id}, 老版直接返回队伍码字符串
        this.team_id = (typeof data === "object" && data !== null) ? (data.team_id || "") : (data || "");

        if (!this.team_id) {
          this.log("🤝 [组队状态] 获取组队码失败: 未返回team_id: " + JSON.stringify(data));
          return;
        }

        await this.check_my_team();
      } else {
        let errorMessage = CommonUtils.get(result, "errmsg", "");
        this.log("🤝 [组队状态] 获取组队码失败[" + errorCode + "]: " + errorMessage);
      }
    } catch (exception) {
      console.log(exception);
    }
  }

  // 检查我的队伍
  async check_my_team(options = {}) {
    try {
      const requestConfig = {
        fn: "check_my_team",
        method: "get",
        url: "https://j1.pupuapi.com/client/game/coin_share/teams/" + this.team_id
      };
      
      let { result, statusCode } = await this.request(requestConfig);
      let errorCode = CommonUtils.get(result, "errcode", statusCode);
      
      if (errorCode === 0) {
        let { status, target_team_member_num, current_team_member_num, current_user_reward_coin } = result?.data;
        
        switch (status) {
          case 10: // 组队中
            this.team_need_help = true;
            this.team_max_help = target_team_member_num;
            this.team_helped_count = current_team_member_num;
            this.log("🤝 [组队状态] 组队中: " + current_team_member_num + "/" + target_team_member_num);
            break;
          case 30: // 组队完成
            this.log("🤝 [组队状态] 组队完成, 获得" + current_user_reward_coin + "积分");
            break;
          default:
            this.log("🤝 [组队状态] 状态[" + status + "]");
            this.log("🤝 [组队状态] " + JSON.stringify(result?.data));
        }
      } else {
        let errorMessage = CommonUtils.get(result, "errmsg", "");
        this.log("🤝 [组队状态] 查询组队信息失败[" + errorCode + "]: " + errorMessage);
      }
    } catch (exception) {
      console.log(exception);
    }
  }

  // 加入队伍
  async join_team(targetUser, options = {}) {
    try {
      const requestConfig = {
        fn: "join_team",
        method: "post",
        url: "https://j1.pupuapi.com/client/game/coin_share/teams/" + targetUser.team_id + "/join"
      };
      
      let { result, statusCode } = await this.request(requestConfig);
      let errorCode = CommonUtils.get(result, "errcode", statusCode);
      
      if (errorCode === 0) {
        this.team_can_help = false;
        targetUser.team_helped_count += 1;
        
        let userCountLength = CommonUtils.userCount.toString().length;
        let targetPrefix = "账号[" + CommonUtils.padStr(targetUser.index + 1, userCountLength) + "]";
        
        if (targetUser.name) {
          targetPrefix += "[" + targetUser.name + "]";
        }
        
        this.log("👥 [组队瓜分朴分活动] 加入" + targetPrefix + "队伍: " + targetUser.team_helped_count + "/" + targetUser.team_max_help);
        
        if (targetUser.team_helped_count >= targetUser.team_max_help) {
          targetUser.team_need_help = false;
          targetUser.log("👥 [组队瓜分朴分活动] 组队已满");
        }
      } else {
        let errorMessage = CommonUtils.get(result, "errmsg", "");
        
        let userCountLength = CommonUtils.userCount.toString().length;
        let targetPrefix = "账号[" + CommonUtils.padStr(targetUser.index + 1, userCountLength) + "]";
        
        if (targetUser.name) {
          targetPrefix += "[" + targetUser.name + "]";
        }
        
        this.log("👥 [组队瓜分朴分活动] 加入" + targetPrefix + "队伍失败[" + errorCode + "]: " + errorMessage);
        
        switch (errorCode) {
          case 100007: // 队伍已满
            targetUser.team_need_help = false;
            break;
          case 100008: // 无法加入自己的队伍
            break;
          case 100009: // 今日已助力
            this.team_can_help = false;
            break;
        }
      }
    } catch (exception) {
      console.log(exception);
    }
  }

  // 查询朴分
  async query_coin(options = {}) {
    try {
      const requestConfig = {
        fn: "query_coin",
        method: "get",
        url: "https://j1.pupuapi.com/client/coin"
      };
      
      let { result, statusCode } = await this.request(requestConfig);
      let errorCode = CommonUtils.get(result, "errcode", statusCode);
      
      if (errorCode === 0) {
        let { balance, expiring_coin, expire_time } = result?.data;
        
        let diff = balance - (this.coin_before ?? balance);
        let diffStr = "（本次 " + (diff > 0 ? "+" : "") + diff + "）";
        this.log("💰 总朴分: " + balance + diffStr);
        
        if (expiring_coin && expire_time) {
          let expireDate = CommonUtils.time("yyyy-MM-dd", expire_time);
          this.log("⏰ 朴分到期提醒：" + expiring_coin + "朴分将于" + expireDate + "过期，请尽快使用");
        }
      } else {
        let errorMessage = CommonUtils.get(result, "errmsg", "");
        this.log("❌ 查询朴分失败[" + errorCode + "]: " + errorMessage);
      }
    } catch (exception) {
      console.log(exception);
    }
  }

  // 用户任务
  async userTask(options = {}) {
    await this.user_info();
    await this.near_location_by_city();
    this.log("🎯 开始执行日常任务");
    // 查询执行前朴分
    await this._query_coin_before();
    await this.sign_index();
    await this.get_team_code();
  }

  // 查询执行前朴分
  async _query_coin_before() {
    try {
      const requestConfig = {
        fn: "query_coin_before",
        method: "get",
        url: "https://j1.pupuapi.com/client/coin"
      };
      let { result, statusCode } = await this.request(requestConfig);
      let errorCode = CommonUtils.get(result, "errcode", statusCode);
      if (errorCode === 0) {
        let balance = result?.data?.balance;
        this.coin_before = balance;
        this.log("💰 当前朴分：" + balance);
      }
    } catch (exception) {
      console.log(exception);
    }
  }

  // 查询执行后朴分（延迟几秒等积分同步）
  async _query_coin_after() {
    try {
      await new Promise(resolve => setTimeout(resolve, 3000));
      const requestConfig = {
        fn: "query_coin_after",
        method: "get",
        url: "https://j1.pupuapi.com/client/coin"
      };
      let { result, statusCode } = await this.request(requestConfig);
      let errorCode = CommonUtils.get(result, "errcode", statusCode);
      if (errorCode === 0) {
        let balance = result?.data?.balance;
        this.log("💰 执行后朴分：" + balance);
      }
    } catch (exception) {
      console.log(exception);
    }
  }
}

// ==================== 账号加载 ====================
// 从环境变量 ppcs_openid 加载网关账号（逗号分隔支持多账号）
function loadAccounts() {
  if (!PPCS_OPENID) {
    CommonUtils.log("❌ 未设置 ppcs_openid，无法登录");
    return false;
  }
  if (!WX_SERVER_URL) {
    CommonUtils.log("❌ 未设置 wx_server_url，无法登录");
    return false;
  }
  
  let refs = PPCS_OPENID.split(/[,，&\n]/).map(r => r.trim()).filter(r => r);
  for (let ref of refs) {
    CommonUtils.userList.push(new PupuUser("", ref));
  }
  
  CommonUtils.userCount = CommonUtils.userList.length;
  return true;
}

// ==================== 主流程 ====================
(async () => {
  if (!loadAccounts()) return;
  
  console.log("=".repeat(30));
  console.log("🚀 朴朴超市签到");
  console.log("📱 共配置 " + CommonUtils.userCount + " 个账号");
  console.log("=".repeat(30));
  
  
  let validUsers = [];
  
  // 逐个账号：登录 → 任务 → 执行后朴分 → 总朴分
  for (let user of CommonUtils.userList) {
    let loginSuccess = await user.user_refresh_token();
    if (!loginSuccess) {
      user.log("❌ 登录失败，跳过");
      continue;
    }
    validUsers.push(user);
    await user.userTask();
    await user._query_coin_after();
    await user.query_coin();
  }
  
  if (validUsers.length === 0) {
    CommonUtils.log("❌ 没有有效的账号，程序结束");
    return;
  }
  
  // 互相助力
  if (!validUsers.some(u => u.team_need_help)) {
    CommonUtils.log(">>> [组队瓜分朴分活动] 没有账号处于组队中状态，跳过互相助力");
  } else {
  CommonUtils.log(">>> [组队瓜分朴分活动] 开始互相组队助力");
  for (let needHelpUser of validUsers.filter(u => u.team_need_help)) {
    for (let helperUser of validUsers.filter(u => u.team_can_help && u.index !== needHelpUser.index)) {
      if (!needHelpUser.team_need_help) break;
      await helperUser.join_team(needHelpUser);
    }
  }
  }
})()
  .catch(error => CommonUtils.log(error))
  .finally(() => CommonUtils.exitNow());

// ==================== 版本检查 ====================
async function checkVersion(retryCount = 0) {
  let success = false;
  
  try {
    const requestConfig = {
      fn: "auth",
      method: "get",
      url: VERSION_CHECK_URL,
      timeout: 20000
    };
    
    let { statusCode, result } = await globalRequest.request(requestConfig);
    
    if (statusCode !== 200) {
      if (retryCount < MAX_VERSION_CHECK_RETRY) {
        success = await checkVersion(retryCount + 1);
      }
      return success;
    }
    
    if (result?.code === 0) {
      result = JSON.parse(result.data.file.data);
      
      if (result?.commonNotify && result.commonNotify.length > 0) {
        CommonUtils.log(result.commonNotify.join("\n") + "\n");
      }
      
      if (result?.commonMsg && result.commonMsg.length > 0) {
        CommonUtils.log(result.commonMsg.join("\n") + "\n");
      }
      
      if (result[SCRIPT_KEY]) {
        let scriptInfo = result[SCRIPT_KEY];
        
        if (scriptInfo.status === 0) {
          if (SCRIPT_VERSION >= scriptInfo.version) {
            success = true;
            CommonUtils.log(scriptInfo.msg[scriptInfo.status]);
            CommonUtils.log(scriptInfo.updateMsg);
            CommonUtils.log("📋 当前脚本版本：" + SCRIPT_VERSION + "，最新版本：" + scriptInfo.latestVersion);
          } else {
            CommonUtils.log(scriptInfo.versionMsg);
          }
        } else {
          CommonUtils.log(scriptInfo.msg[scriptInfo.status]);
        }
      } else {
        CommonUtils.log(result.errorMsg);
      }
    } else if (retryCount < MAX_VERSION_CHECK_RETRY) {
      success = await checkVersion(retryCount + 1);
    }
  } catch (exception) {
    CommonUtils.log(exception);
  } finally {
    return success;
  }
}

// ==================== 通用工具类 ====================
function createCommonUtils(scriptName) {
  return new class {
    constructor(name) {
      this.name = name;
      this.startTime = Date.now();
      this.userIdx = 0;
      this.userList = [];
      this.userCount = 0;
      
      this.default_timestamp_len = 13;
      this.default_wait_interval = 1000;
      this.default_wait_limit = 3600000;
      this.default_wait_ahead = 0;
      
      this.ALL_DIGIT = "0123456789";
      this.ALL_ALPHABET = "qwertyuiopasdfghjklzxcvbnm";
      this.ALL_CHAR = this.ALL_DIGIT + this.ALL_ALPHABET + this.ALL_ALPHABET.toUpperCase();
    }

    // 日志输出
    log(message, options = {}) {
      const defaultOptions = { console: true };
      Object.assign(defaultOptions, options);
      
      if (defaultOptions.time) {
        let timeFormat = defaultOptions.fmt || "hh:mm:ss";
        message = "[" + this.time(timeFormat) + "]" + message;
      }
      
      if (defaultOptions.console) {
        console.log(message);
      }
    }

    // 获取对象属性
    get(obj, key, defaultValue = "") {
      let value = defaultValue;
      if (obj?.hasOwnProperty(key)) {
        value = obj[key];
      }
      return value;
    }

    // 弹出对象属性
    pop(obj, key, defaultValue = "") {
      let value = defaultValue;
      if (obj?.hasOwnProperty(key)) {
        value = obj[key];
        delete obj[key];
      }
      return value;
    }

    // 复制对象
    copy(obj) {
      return Object.assign({}, obj);
    }

    // 从环境变量读取
    read_env(UserClass) {
      let envValues = ckNames.map(name => process.env[name]);
      
      for (let envValue of envValues.filter(v => !!v)) {
        for (let cookie of envValue.split(envSplitor).filter(c => !!c)) {
          this.userList.push(new UserClass(cookie));
        }
      }
      
      this.userCount = this.userList.length;
      
      if (!this.userCount) {
        this.log("❌ 未找到变量，请检查变量" + ckNames.map(n => "[" + n + "]").join("或"));
        return false;
      }
      
      this.log("📊 共找到 " + this.userCount + " 个账号");
      return true;
    }

    // 时间格式化
    time(format, timestamp = null) {
      let date = timestamp ? new Date(timestamp) : new Date();
      let dateObj = {
        "M+": date.getMonth() + 1,
        "d+": date.getDate(),
        "h+": date.getHours(),
        "m+": date.getMinutes(),
        "s+": date.getSeconds(),
        "q+": Math.floor((date.getMonth() + 3) / 3),
        "S": this.padStr(date.getMilliseconds(), 3)
      };
      
      if (/(y+)/.test(format)) {
        format = format.replace(RegExp.$1, (date.getFullYear() + "").substr(4 - RegExp.$1.length));
      }
      
      for (let key in dateObj) {
        if (new RegExp("(" + key + ")").test(format)) {
          format = format.replace(
            RegExp.$1,
            RegExp.$1.length === 1 ? dateObj[key] : ("00" + dateObj[key]).substr(("" + dateObj[key]).length)
          );
        }
      }
      
      return format;
    }

    // 字符串填充
    padStr(str, length, options = {}) {
      let padding = options.padding || "0";
      let mode = options.mode || "l";
      let result = String(str);
      let padLength = length > result.length ? length - result.length : 0;
      let padString = "";
      
      for (let i = 0; i < padLength; i++) {
        padString += padding;
      }
      
      if (mode === "r") {
        result = result + padString;
      } else {
        result = padString + result;
      }
      
      return result;
    }

    // JSON转字符串
    json2str(obj, separator, encode = false) {
      let pairs = [];
      
      for (let key of Object.keys(obj).sort()) {
        let value = obj[key];
        if (value && encode) {
          value = encodeURIComponent(value);
        }
        pairs.push(key + "=" + value);
      }
      
      return pairs.join(separator);
    }

    // 字符串转JSON
    str2json(str, decode = false) {
      let obj = {};
      
      for (let pair of str.split("&")) {
        if (!pair) continue;
        
        let equalIndex = pair.indexOf("=");
        if (equalIndex === -1) continue;
        
        let key = pair.substr(0, equalIndex);
        let value = pair.substr(equalIndex + 1);
        
        if (decode) {
          value = decodeURIComponent(value);
        }
        
        obj[key] = value;
      }
      
      return obj;
    }

    // 随机模式
    randomPattern(pattern, charset = "abcdef0123456789") {
      let result = "";
      
      for (let char of pattern) {
        if (char === "x") {
          result += charset.charAt(Math.floor(Math.random() * charset.length));
        } else if (char === "X") {
          result += charset.charAt(Math.floor(Math.random() * charset.length)).toUpperCase();
        } else {
          result += char;
        }
      }
      
      return result;
    }

    // 随机UUID
    randomUuid() {
      return this.randomPattern("xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx");
    }

    // 随机字符串
    randomString(length, charset = "abcdef0123456789") {
      let result = "";
      
      for (let i = 0; i < length; i++) {
        result += charset.charAt(Math.floor(Math.random() * charset.length));
      }
      
      return result;
    }

    // 随机列表元素
    randomList(list) {
      let randomIndex = Math.floor(Math.random() * list.length);
      return list[randomIndex];
    }

    // 等待
    wait(milliseconds) {
      return new Promise(resolve => setTimeout(resolve, milliseconds));
    }

    // 退出
    async exitNow() {
      let endTime = Date.now();
      let duration = (endTime - this.startTime) / 1000;
      
      let validCount = CommonUtils.userList.filter(u => u.valid).length;
      console.log("======🎉 完成 " + validCount + " / 共 " + CommonUtils.userCount + " 账号======");
      
      process.exit(0);
    }

    // 标准化时间戳
    normalize_time(timestamp, options = {}) {
      let targetLength = options.len || this.default_timestamp_len;
      timestamp = timestamp.toString();
      let currentLength = timestamp.length;
      
      while (currentLength < targetLength) {
        timestamp += "0";
        currentLength++;
      }
      
      if (currentLength > targetLength) {
        timestamp = timestamp.slice(0, 13);
      }
      
      return parseInt(timestamp);
    }

    // 等待到指定时间
    async wait_until(targetTime, options = {}) {
      let logger = options.logger || this;
      let interval = options.interval || this.default_wait_interval;
      let limit = options.limit || this.default_wait_limit;
      let ahead = options.ahead || this.default_wait_ahead;
      
      if (typeof targetTime === "string" && targetTime.includes(":")) {
        if (targetTime.includes("-")) {
          targetTime = new Date(targetTime).getTime();
        } else {
          let today = this.time("yyyy-MM-dd ");
          targetTime = new Date(today + targetTime).getTime();
        }
      }
      
      let normalizedTime = this.normalize_time(targetTime) - ahead;
      let timeString = this.time("hh:mm:ss.S", normalizedTime);
      let now = Date.now();
      
      if (now > normalizedTime) {
        normalizedTime += 86400000; // 加一天
      }
      
      let waitTime = normalizedTime - now;
      
      if (waitTime > limit) {
        logger.log("离目标时间[" + timeString + "]大于" + limit / 1000 + "秒,不等待", { time: true });
      } else {
        logger.log("离目标时间[" + timeString + "]还有" + waitTime / 1000 + "秒,开始等待", { time: true });
        
        while (waitTime > 0) {
          let sleepTime = Math.min(waitTime, interval);
          await this.wait(sleepTime);
          now = Date.now();
          waitTime = normalizedTime - now;
        }
        
        logger.log("已完成等待", { time: true });
      }
    }

    // 等待间隔
    async wait_gap_interval(lastTime, interval) {
      let elapsed = Date.now() - lastTime;
      if (elapsed < interval) {
        await this.wait(interval - elapsed);
      }
    }
  }(scriptName);
}