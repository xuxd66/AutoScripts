"""
Author: anonymous
Date: 2026.08.31
Description: 汤星球小程序签到
Cron: 5 9,12,20 * * *
----------------------------------------------------------------------------------------------
汤星球小程序自动任务 v1.0.0

功能：自动完成汤星球小程序签到+抽奖任务，支持多账号执行。

配置说明：
1. 微信 code 网关：（适配应用宝协议，ck 自动获取）
   wx_server_url                                   必填，自建授权服务器地址
   - 示例：http://127.0.0.1:8000
   - 脚本会自动拼接 /wxapp/getCode
   - 请求格式：POST {网关}/wxapp/getCode
   - 请求体：{"app_id": "<小程序appid>", "ref": "账号openid"}

2. 账号变量：
   txq_openid                                     推荐，汤星球小程序专属账号变量
   - 多账号支持使用 &、英文逗号、中文逗号或换行分隔
   - 示例：openid_a&openid_b 或 openid_a,openid_b

3. 代理变量（可选，适配品赞代理）：
   proxy_api_url                                   品赞代理 API 地址，开启后每个账号自动获取代理
   - 代理接口返回格式支持：纯 IP:PORT，或带账号密码的 IP:PORT ACCOUNT PASSWORD（品赞格式）
   - 单账号固定代理：在账号后追加 #proxy=IP:PORT 可指定该账号专用代理

4. 青龙任务建议：
   名称：汤星球小程序签到
   命令：task txq.py
   定时：每天运行 1 - 3 次即可，具体时间自行调整
----------------------------------------------------------------------------------------------
"""

import os
import random
import re
import sys
import time
import json
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from threading import Lock
import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

# Windows 控制台默认 GBK 无法编码 emoji/特殊字符，强制 stdout/stderr 为 UTF-8
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

# ==================== 配置区域 ====================
ENABLE_DAILY_TASK = True         # 日常任务

# 统一变量名称与默认值映射
TXQ_WX_SERVER = (os.environ.get("wx_server_url") or "").strip().rstrip("/")
TXQ_WX_APPID = "wx9bb6d5ac457bd69d"                                              # 汤星球小程序 appid
TXQ_OPENIDS = os.environ.get("txq_openid") or ""                                 # 汤星球小程序专属 openid

# 汤星球业务域名
TXQ_BASE = "https://vip.by-health.com"

# 代理模块：由环境变量 proxy_api_url 驱动
PROXY_API_URL = os.getenv("proxy_api_url", "")
PROXY_TYPE = os.getenv("txq_proxy_type", "socks5")
PROXY_TIMEOUT = 20
print_lock = Lock()

# 默认 User-Agent（Windows 微信小程序环境）
TXQ_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/144.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781(0x6700143B) "
    "NetType/WIFI MiniProgramEnv/Windows WindowsWechat/WMPF "
    "WindowsWechat(0x63090a13) UnifiedPCWindowsWechat(0xf2541d0c) XWEB/25510"
)

# Referer 模板
TXQ_REFERER = f"https://servicewechat.com/{TXQ_WX_APPID}/112/page-frame.html"


class Logger:
    def _log(self, icon: str, msg: str):
        line = f"{icon} {msg}" if icon else msg
        with print_lock:
            print(line)

    def info(self, msg): self._log('📝', msg)
    def debug(self, msg): self._log('🐞', msg)
    def raw(self, msg): self._log('', msg)
    def success(self, msg): self._log('✨', msg)
    def warning(self, msg): self._log('⚠️', msg)
    def error(self, msg): self._log('❌', msg)
    def task(self, msg): self._log('🎯', msg)
    def task_skip(self, msg): self._log('⏭️', msg)
    def task_complete(self, msg): self._log('✅', msg)
    def points(self, pts, prefix="当前积分"): self._log('💰', f"{prefix}: 【{pts}】")


def _log_global(msg: str):
    line = msg
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(line.encode(encoding, errors="ignore").decode(encoding, errors="ignore"), flush=True)


def parse_env_accounts(raw: str) -> List[str]:
    normalized = (raw or "").replace("，", ",").replace(",", "&").replace("\n", "&")
    return [item.strip() for item in normalized.split("&") if item.strip()]


def mask_account(value: Any) -> str:
    value = str(value or "")
    if len(value) <= 12:
        return value
    return f"{value[:6]}...{value[-4:]}"


class ProxyManager:
    """代理管理器（环境变量 proxy_api_url；未配置则不走代理）"""
    def __init__(self, api_url: str):
        self.api_url = api_url

    def get_proxy(self) -> Optional[Dict[str, str]]:
        try:
            if not self.api_url:
                return None
            response = requests.get(self.api_url, timeout=10)
            if response.status_code == 200:
                proxy_text = response.text.strip()
                parts = proxy_text.split()
                if len(parts) == 3:
                    ip_port = parts[0]
                    account = parts[1]
                    password = parts[2]
                    proxy_text = f"http://{account}:{password}@{ip_port}"
                if ':' in proxy_text:
                    if proxy_text.startswith('http://') or proxy_text.startswith('https://'):
                        proxy = proxy_text
                    else:
                        proxy = f'http://{proxy_text}'
                    display_proxy = proxy
                    if '@' in proxy:
                        seg = proxy.split('@')
                        if len(seg) == 2:
                            display_proxy = f"http://***:***@{seg[1]}"
                    _log_global(f"✅ 成功获取代理: {display_proxy}")
                    return {'http': proxy, 'https': proxy}
            _log_global(f"❌ 获取代理失败: {response.text}")
            return None
        except Exception as e:
            _log_global(f"❌ 获取代理异常: {str(e)}")
            return None


# 模块级代理管理器单例
proxy_manager = ProxyManager(PROXY_API_URL)


def parse_fixed_proxy(fixed_proxy: str) -> Optional[Dict[str, str]]:
    if not fixed_proxy:
        return None
    if '://' not in fixed_proxy:
        fixed_proxy = f'{PROXY_TYPE}://{fixed_proxy}'
    return {'http': fixed_proxy, 'https': fixed_proxy}


# ==================== AutoCookieManager ====================
class AutoCookieManager:
    """通过应用宝网关 /wxapp/getCode 获取微信 code，再走 /vip-api/auth/ma/login 换取登录态 token。"""
    def __init__(self, wx_server: str = None, fixed_proxy: str = ""):
        self.wx_server = (wx_server or TXQ_WX_SERVER).strip().rstrip("/")
        self.session = requests.Session()
        self.session.verify = False
        self._fixed_proxy = parse_fixed_proxy(fixed_proxy) if fixed_proxy else None

    def _get_wx_code(self, wxid: str, appid: str = None, max_retries: int = 3) -> Optional[str]:
        """通过 POST /wxapp/getCode 获取微信 code

        请求体: {"app_id": appid, "ref": wxid/openid}
        成功响应: {"code":0,"msg":"success","data":{"openid":"...","result":{"code":"...","errMsg":"login:ok"}}}
        """
        if not self.wx_server:
            _log_global("❌ 未配置 wx_server_url，无法请求 /wxapp/getCode")
            return None
        target_appid = appid or TXQ_WX_APPID
        url = f"{self.wx_server}/wxapp/getCode"

        for attempt in range(max_retries):
            try:
                payload = {"app_id": target_appid, "ref": wxid}
                headers = {
                    "Content-Type": "application/json",
                    "User-Agent": "Mozilla/5.0 MicroMessenger/8.0.50",
                }

                r = self.session.post(url, json=payload, headers=headers, timeout=30)
                j = r.json()

                if j.get("code") == 0:
                    data = j.get("data") or {}
                    result = data.get("result") if isinstance(data, dict) else {}
                    if isinstance(result, dict) and result.get("code"):
                        _log_global(f"🔑 获取code成功: {str(result['code'])[:10]}***")
                        return str(result["code"])

                data = j.get("Data") or j.get("data") or {}
                code = ""
                if isinstance(data, dict):
                    result = data.get("result") or {}
                    if isinstance(result, dict):
                        code = result.get("code") or ""
                    if not code:
                        nested_code = data.get("code")
                        if nested_code not in (None, "", 0):
                            code = nested_code
                if not code:
                    code = j.get("wx_code") or ""

                if not code:
                    if attempt < max_retries - 1:
                        wait = (attempt + 1) * 3
                        _log_global(f"⚠️ {wxid[:12]}***: code为空，{wait}s后重试({attempt+1}/{max_retries})")
                        time.sleep(wait)
                        continue
                    _log_global(f"❌ {wxid[:12]}***: 获取code失败 appid={target_appid} resp={str(j)[:160]}")
                    return None
                _log_global(f"🔑 获取code成功: {str(code)[:10]}***")
                return str(code)
            except Exception as e:
                if attempt < max_retries - 1:
                    wait = (attempt + 1) * 3
                    _log_global(f"⚠️ {wxid[:12]}***: code异常 {str(e)[:60]}，{wait}s后重试({attempt+1}/{max_retries})")
                    time.sleep(wait)
                    continue
                _log_global(f"❌ {wxid[:12]}***: 获取code异常 appid={target_appid} err={str(e)[:80]}")
                return None
        return None

    def _auth_by_code(self, code: str) -> Optional[Dict]:
        """调用 /vip-api/auth/ma/login 用 code 换取登录态 token。

        请求: POST /vip-api/auth/ma/login
        body: {"appId":"<appid>","code":"<code>"}
        成功响应: {"code":0/200,"data":{"token":"...","openid":"...",...}}
        """
        try:
            url = f"{TXQ_BASE}/vip-api/auth/ma/login"
            body = {"appId": TXQ_WX_APPID, "code": code}
            headers = {
                "Content-Type": "application/json;charset=UTF-8",
                "User-Agent": TXQ_UA,
                "Referer": TXQ_REFERER,
            }
            r = self.session.post(url, json=body, headers=headers, timeout=25,
                                 proxies=self._fixed_proxy or None)
            j = r.json()

            # 兼容多种响应格式
            # 格式: {"success":true,"data":{"rspCode":"00","rspMsg":"success","result":{"token":"...","unionId":"..."}}}
            data = j.get("data") or {}
            result = data.get("result") if isinstance(data, dict) else {}
            token = (result.get("token") if result else "") or data.get("token") or j.get("token") or ""
            if not token:
                token = j.get("accessToken") or ""
            if token:
                _log_global(f"✅ 登录成功, token:{str(token)[:16]}...")
                return result if result else (data if data else {"token": token})

            _log_global(f"⚠️ login 返回非预期: {str(j)[:160]}")
            return None
        except Exception as e:
            _log_global(f"❌ login 异常: {str(e)[:100]}")
            return None

    def get_token_for_wxid(self, wxid: str) -> Optional[Dict]:
        """通过 /wxapp/getCode 拿到 code 后，走 /vip-api/auth/ma/login 换取登录态 token。"""
        code = self._get_wx_code(wxid, TXQ_WX_APPID)
        if not code:
            return None

        result = self._auth_by_code(code)
        if not result:
            _log_global(f"❌ {wxid[:10]}*** login 换 token 失败")
            return None

        token = result.get("token", "")
        if not token:
            _log_global(f"❌ {wxid[:10]}*** login 未返回 token")
            return None

        # 查询用户信息
        nick_name = ""
        try:
            mi_headers = self._build_headers(token)
            mi_url = f"{TXQ_BASE}/vip-api/member/info"
            mi_r = self.session.get(mi_url, headers=mi_headers, timeout=25,
                                   proxies=self._fixed_proxy or None)
            mi_j = mi_r.json()
            mi_result = _extract_result(mi_j)
            if mi_result:
                nick_name = mi_result.get("nickName") or mi_result.get("nickname") or ""
            if nick_name:
                _log_global(f"👤 用户：{nick_name}")
            else:
                phone = mi_result.get("mobile") or mi_result.get("phone") or "" if mi_result else ""
                if phone:
                    masked_phone = phone[:3] + "****" + phone[-4:] if len(phone) >= 7 else phone
                    _log_global(f"👤 用户：{masked_phone}")
                else:
                    _log_global("👤 用户：未知")
        except Exception:
            pass

        return {
            "token": token,
            "openid": result.get("openid", wxid),
            "nick_name": nick_name,
        }

    @staticmethod
    def _build_headers(token: str) -> Dict[str, str]:
        """构造业务请求头（Authorization = 裸 token）。"""
        headers = {
            "Authorization": token,
            "Content-Type": "application/json",
            "User-Agent": TXQ_UA,
            "Referer": TXQ_REFERER,
        }
        return headers


# ==================== HTTP 客户端 ====================
class TxqHttpClient:
    def __init__(self, token: str, openid: str, fixed_proxy: str = ""):
        self.session = requests.Session()
        self.session.verify = False
        self.proxy_display = '无代理'
        self._setup_proxy(fixed_proxy)
        self.token = token
        self.openid = openid

    def _setup_proxy(self, fixed_proxy: str):
        if fixed_proxy:
            proxy_dict = parse_fixed_proxy(fixed_proxy)
            if proxy_dict:
                self.session.proxies = proxy_dict
                display = fixed_proxy
                if '@' in fixed_proxy:
                    parts = fixed_proxy.split('@')
                    display = f"***@{parts[-1]}"
                self.proxy_display = display
                return
        proxy = proxy_manager.get_proxy()
        if proxy:
            self.session.proxies = proxy
            self.proxy_display = "API代理"

    def _headers(self) -> Dict[str, str]:
        return AutoCookieManager._build_headers(self.token)

    def request(self, method: str, path: str, params: Optional[Dict] = None,
                body: Any = None) -> Optional[Dict]:
        """统一请求入口。"""
        url = TXQ_BASE + path
        headers = self._headers()
        kwargs = {"headers": headers, "timeout": PROXY_TIMEOUT}
        if params:
            url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
        if method == "POST":
            kwargs["json"] = body if body is not None else {}
        try:
            r = self.session.request(method, url, **kwargs)
            return r.json()
        except Exception as e:
            _log_global(f"❌ 请求异常 {path}: {str(e)[:80]}")
            return None

    def get(self, path: str, params: Optional[Dict] = None) -> Optional[Dict]:
        return self.request("GET", path, params=params)

    def post(self, path: str, body: Any = None) -> Optional[Dict]:
        return self.request("POST", path, body=body)


# ==================== 日常任务执行器 ====================
# 任务相关端点
TXQ_MEMBER_INFO = "/vip-api/member/info"                            # 会员信息
TXQ_MEMBER_ASSETS = "/vip-api/member/assets"                        # 会员资产（积分）
TXQ_SIGN_DETAIL = "/vip-api/sign/activity/detail"                   # 签到活动详情
TXQ_SIGN_CALENDER = "/vip-api/sign/activity/calender"               # 签到日历
TXQ_SIGN_DAILY = "/vip-api/sign/daily/create"                       # 执行每日签到
TXQ_SIGN_DRAW = "/vip-api/sign/daily/draw"                          # 领取签到宝箱奖励
TXQ_RAFFLE_INFO = "/vip-api/raffle/activity/info"                   # 抽奖活动信息
TXQ_RAFFLE_DRAW = "/vip-api/raffle/record/draw/submit"              # 执行抽奖
TXQ_RAFFLE_RECORD = "/vip-api/raffle/record/query/personal"         # 查询中奖记录
TXQ_RAFFLE_PRIZE = "/vip-api/raffle/record/prize/draw"              # 领取奖品（入账）
TXQ_AUTH_STATUS = "/vip-api/wechat/transfer/authorization/status"  # 微信转账授权状态
TXQ_QUICK_ENTRANCE = "/vip-api/quick/entrance/list"                 # 首页快捷入口（含抽奖跳转链接+活动ID）
TXQ_RAFFLE_ACTIVITY_ID = int(os.environ.get("txq_raffle_id", "0"))  # 抽奖活动ID（0=自动从首页入口获取）


def _extract_result(resp: Optional[Dict]) -> Optional[Dict]:
    """统一提取响应中的业务数据。

    汤星球 API 统一响应格式：
    {"success":true, "data":{"rspCode":"00","rspMsg":"success","result":{...}}}
    或 {"code":0/200, "data":{"result":{...}}}
    兼容旧格式 data 本身就是业务数据的情况。
    """
    if not resp or not isinstance(resp, dict):
        return None
    data = resp.get("data")
    if not isinstance(data, dict):
        return None
    # 优先取 data.result
    result = data.get("result")
    if isinstance(result, (dict, list)):
        return result
    # 兼容 data 本身就是业务数据
    if data.get("rspCode") or data.get("rspMsg") or "point" in data or "nickName" in data:
        return data
    return data


class DailyTaskExecutor:
    def __init__(self, http: TxqHttpClient, logger: Logger):
        self.http = http
        self.logger = logger
        self._before_points: Optional[int] = None

    def _query_points(self, force: bool = False) -> Optional[int]:
        """查询当前积分（重试 1 次，间隔 3 秒）。force=True 时强制刷新缓存。"""
        for attempt in range(2):
            try:
                data = self.http.get(TXQ_MEMBER_ASSETS, {"forceRefresh": "1" if force else "0"})
                result = _extract_result(data)
                if result:
                    pt = result.get("point")
                    if pt is not None:
                        return int(pt)
            except Exception:
                pass
            if attempt == 0:
                time.sleep(3)
        return None

    def check_login(self) -> Tuple[bool, Optional[int]]:
        """用会员资产接口验证登录态并取当前积分。返回 (登录有效, 当前积分)。"""
        data = self.http.get(TXQ_MEMBER_ASSETS, {"forceRefresh": "0"})
        result = _extract_result(data)
        if result:
            pt = result.get("point")
            pts = int(pt) if pt is not None else None
            self.logger.raw(f"💰 当前积分：{pts if pts is not None else '?'}")
            return True, pts
        self.logger.warning(f"[登录态校验] 失败：{str(data)[:120]}")
        return False, None

    def query_member_info(self) -> None:
        """查询会员信息。"""
        data = self.http.get(TXQ_MEMBER_INFO)
        result = _extract_result(data)
        if result:
            nick = result.get("nickName") or result.get("nickname") or ""
            level = result.get("gradeName") or result.get("level") or result.get("memberLevel") or ""
            phone = result.get("mobile") or result.get("phone") or ""
            if phone:
                masked = phone[:3] + "****" + phone[-4:] if len(phone) >= 7 else phone
                self.logger.raw(f"📱 [手机] {masked}")
        else:
            self.logger.warning(f"[会员信息] 查询失败：{str(data)[:80]}")
    def sign_in(self) -> None:
        """每日签到。"""
        # 1. 获取签到活动详情（取 activityId + signFlag 判断是否已签到）
        detail_data = self.http.post(TXQ_SIGN_DETAIL, {})
        detail_result = _extract_result(detail_data)
        activity_id = None
        already_signed = False
        if detail_result:
            activity_id = detail_result.get("activityId") or detail_result.get("id")
            if not activity_id:
                # 兼容嵌套
                activity_list = detail_result.get("list") or detail_result.get("activities") or []
                if activity_list and isinstance(activity_list, list):
                    activity_id = (activity_list[0] or {}).get("activityId") or (activity_list[0] or {}).get("id")
            # signFlag=1 表示今日已签到
            sign_flag = detail_result.get("signFlag")
            if sign_flag is not None and int(sign_flag) == 1:
                already_signed = True

        if not activity_id:
            self.logger.warning(f"📅 [每日签到] 未获取到活动ID，跳过签到：{str(detail_data)[:120]}")
            return

        if already_signed:
            self.logger.raw("📅 [每日签到] 今日已签到")
            return

        # 2. 查询签到日历（仿真人行为）
        _random_sleep(1)
        now = datetime.now()
        start_date = now.strftime("%Y-%m-01")
        end_date = now.strftime("%Y-%m-%d")
        self.http.post(TXQ_SIGN_CALENDER, {"startDate": start_date, "endDate": end_date})

        # 3. 执行签到
        self.logger.raw(f"📅 [每日签到] 执行签到...")
        sign_data = self.http.post(TXQ_SIGN_DAILY, {"activityId": int(activity_id)})

        # 判断签到结果
        rsp_code = ""
        rsp_msg = ""
        if sign_data:
            sign_data_inner = sign_data.get("data") or {}
            if isinstance(sign_data_inner, dict):
                rsp_code = str(sign_data_inner.get("rspCode") or "")
                rsp_msg = str(sign_data_inner.get("rspMsg") or "")

        if "ALREADY_DONE" in rsp_code or "已签" in rsp_msg or "已打卡" in rsp_msg:
            self.logger.raw("📅 [每日签到] 今日已签到（重复签到）")
            self.logger.raw(f"   {str(sign_data)}")
        elif sign_data and sign_data.get("success"):
            sign_result = _extract_result(sign_data)
            award = sign_result.get("dailyPointReward") or ""
            streak_days = sign_result.get("accumulateDay") or ""
            msg = "📅 [每日签到] 签到成功"
            if award:
                msg += f"，获得 {award} 积分"
            if streak_days:
                msg += f"，连续签到 {streak_days} 天"
            self.logger.raw(msg)
        else:
            self.logger.warning(f"📅 [每日签到] 签到失败：{str(sign_data)[:120]}")

    def claim_sign_rewards(self) -> None:
        """领取签到宝箱奖励。

        签到活动详情的 rewardList 中，finishFlag=1 且 drawnFlag=0 的宝箱可领取。
        调用 POST /vip-api/sign/daily/draw {"rewardRecordId": <id>} 领取。
        """
        detail_data = self.http.post(TXQ_SIGN_DETAIL, {})
        detail_result = _extract_result(detail_data)
        if not detail_result:
            self.logger.raw(f"📦 [宝箱领取] 获取签到详情失败：{str(detail_data)[:120]}")
            return

        reward_list = detail_result.get("rewardList") or []
        if not isinstance(reward_list, list):
            self.logger.raw("📦 [宝箱领取] 无宝箱数据")
            return

        # 筛选可领取的宝箱：finishFlag=1（已达成）且 drawnFlag=0（未领取）且 rewardRecordId 不为空
        claimable = []
        for item in reward_list:
            if not isinstance(item, dict):
                continue
            finish_flag = item.get("finishFlag")
            drawn_flag = item.get("drawnFlag")
            record_id = item.get("rewardRecordId")
            if (finish_flag is not None and int(finish_flag) == 1
                    and drawn_flag is not None and int(drawn_flag) == 0
                    and record_id):
                claimable.append(item)

        if not claimable:
            return

        for item in claimable:
            record_id = item.get("rewardRecordId")
            day = item.get("day", "?")
            reward_name = item.get("rewardName") or f"{day}天宝箱"
            self.logger.raw(f"📦 [宝箱领取] 尝试领取【{reward_name}】(id:{record_id})")
            _random_sleep(1)
            draw_data = self.http.post(TXQ_SIGN_DRAW, {"rewardRecordId": int(record_id)})

            if draw_data and draw_data.get("success"):
                draw_inner = draw_data.get("data") or {}
                rsp_code = draw_inner.get("rspCode") or ""
                if rsp_code == "00":
                    result_obj = draw_inner.get("result") or {}
                    msg = result_obj.get("msg") or "领取成功"
                    self.logger.raw(f"📦 [宝箱领取] {reward_name} 领取成功：{msg}")
                else:
                    rsp_msg = draw_inner.get("rspMsg") or ""
                    self.logger.raw(f"📦 [宝箱领取] {reward_name} 领取异常：{rsp_code} {rsp_msg}")
            else:
                self.logger.raw(f"📦 [宝箱领取] {reward_name} 领取失败：{str(draw_data)[:120]}")

    def _get_raffle_activity_id(self) -> Optional[int]:
        """从首页快捷入口 quick/entrance/list 动态获取抽奖活动ID。

        响应中 jumpUrl 含 activityType=<id> 的入口即"每日抽奖"。
        环境变量 txq_raffle_id 可手动覆盖（非0时跳过查询）。
        """
        if TXQ_RAFFLE_ACTIVITY_ID and TXQ_RAFFLE_ACTIVITY_ID > 0:
            return TXQ_RAFFLE_ACTIVITY_ID

        data = self.http.get(TXQ_QUICK_ENTRANCE)
        result = _extract_result(data)
        if not result:
            self.logger.raw(f"🎁 [每日抽奖] 获取首页入口失败：{str(data)[:120]}")
            return None

        items = result if isinstance(result, list) else result.get("list", [])
        for item in items:
            if not isinstance(item, dict):
                continue
            jump_url = item.get("jumpUrl") or ""
            if "activityType=" in jump_url:
                # 提取 activityType 参数值（兼容 ? 和 & 分隔）
                m = re.search(r'[?&]activityType=(\d+)', jump_url)
                if m:
                    return int(m.group(1))
        self.logger.raw("🎁 [每日抽奖] 未在首页入口中找到抽奖活动ID")
        return None

    def lucky_draw(self) -> None:
        """每日幸运转一转抽奖。"""
        # 1. 动态获取抽奖活动ID
        activity_id = self._get_raffle_activity_id()
        if not activity_id:
            self.logger.raw("🎁 [每日抽奖] 未获取到活动ID，跳过抽奖")
            return

        # 2. 查询抽奖活动信息
        info_data = self.http.get(TXQ_RAFFLE_INFO, {"activityId": str(activity_id)})
        info_result = _extract_result(info_data)
        if not info_result:
            self.logger.raw(f"🎁 [每日抽奖] 获取活动信息失败：{str(info_data)[:120]}")
            return

        run_flag = info_result.get("runFlag")
        if run_flag is not None and int(run_flag) != 1:
            self.logger.raw(f"🎁 [每日抽奖] 活动未开始或已结束（runFlag={run_flag}）")
            return

        # freeTimes=免费抽奖次数（用完后服务端返回固定"6积分"占位但不实际发奖）
        free_times = info_result.get("freeTimes")
        if free_times is not None and int(free_times) <= 0:
            self.logger.raw("🎁 [每日抽奖] 今日免费抽奖次数已用完")
            self._claim_prizes(activity_id)
            return

        # 2. 执行抽奖
        self.logger.raw(f"🎁 [每日抽奖] 执行抽奖...")
        draw_data = self.http.post(TXQ_RAFFLE_DRAW, {"id": activity_id, "orderNo": ""})

        if draw_data and draw_data.get("success"):
            draw_result = _extract_result(draw_data)
            if draw_result:
                prize_name = draw_result.get("prizeName") or "未知奖品"
                win_flag = draw_result.get("winFlag")
                if win_flag is not None and int(win_flag) == 1:
                    self.logger.raw(f"🎁 [每日抽奖] 抽奖结果：{prize_name}（中奖）")
                else:
                    self.logger.raw(f"🎁 [每日抽奖] 抽奖结果：{prize_name}（未中奖）")
            else:
                self.logger.raw("🎁 [每日抽奖] 抽奖完成（未获取到结果）")
        else:
            rsp_msg = ""
            try:
                rsp_msg = (draw_data.get("data") or {}).get("rspMsg", "")
            except Exception:
                pass
            if "次数" in str(rsp_msg) or "次数" in str(draw_data):
                self.logger.raw("🎁 [每日抽奖] 今日抽奖次数已用完")
            else:
                self.logger.raw(f"🎁 [每日抽奖] 抽奖失败：{str(draw_data)[:120]}")

        # 3. 抽奖后自动领取积分奖品
        self._claim_prizes(activity_id)

    def _claim_prizes(self, activity_id: int = None) -> None:
        """查询未领取的中奖记录并自动领取积分奖品(prizeType=1)和红包(prizeType=5)。

        - 积分(1)：直接调 prize/draw 领取，自动到账
        - 红包(5)：需先检查微信收款授权，已授权才调 prize/draw 领取
        """
        aid = activity_id or TXQ_RAFFLE_ACTIVITY_ID
        record_data = self.http.get(TXQ_RAFFLE_RECORD, {
            "activityId": str(aid), "page": "1", "rows": "20"
        })
        record_result = _extract_result(record_data)
        if not record_result:
            return

        records = record_result.get("results") or record_result.get("list") or []
        if not records:
            return

        claimed = 0
        for rec in records:
            is_drawn = rec.get("isDrawn")
            prize_type = rec.get("prizeType")
            record_id = rec.get("id")
            # 只领取积分(1)和红包(5)类奖品且未领取的
            if (is_drawn is not None and int(is_drawn) == 0
                    and prize_type is not None and int(prize_type) in (1, 5) and record_id):
                prize_name = rec.get("prizeName") or ""
                pt = int(prize_type)

                # 红包奖品需先检查微信转账授权
                if pt == 5:
                    if not self._ensure_transfer_authorization():
                        self.logger.raw(f"🧧 [奖品领取] {prize_name} 需收款授权，授权未完成")
                        continue

                _random_sleep(1)
                claim_data = self.http.post(TXQ_RAFFLE_PRIZE, {"recordId": int(record_id), "jumpFlag": 0})
                if claim_data and claim_data.get("success"):
                    claim_result = _extract_result(claim_data)
                    if claim_result:
                        msg = claim_result.get("msg") or ""
                        value = claim_result.get("prizeValue") or ""
                        self.logger.raw(f"{'🧧' if pt == 5 else '🎁'} [奖品领取] {prize_name} 入账成功（{msg}）")
                    else:
                        self.logger.raw(f"{'🧧' if pt == 5 else '🎁'} [奖品领取] {prize_name} 领取成功")
                    claimed += 1
                else:
                    self.logger.raw(f"{'🧧' if pt == 5 else '🎁'} [奖品领取] {prize_name} 领取失败：{str(claim_data)[:120]}")

        if claimed == 0:
            # 检查是否有未领取的非积分/红包奖品
            unclaimed = []
            for r in records:
                if not isinstance(r, dict):
                    continue
                r_drawn = r.get("isDrawn")
                r_type = r.get("prizeType")
                if r_drawn is not None and int(r_drawn) == 0:
                    try:
                        if int(r_type or 0) not in (1, 5):
                            unclaimed.append(r)
                    except (ValueError, TypeError):
                        pass
            if unclaimed:
                for r in unclaimed:
                    self.logger.raw(f"🎁 [奖品领取] 非积分/红包奖品未领取（需手动）：{r.get('prizeName')}")

    def _ensure_transfer_authorization(self) -> bool:
        """检查微信收款授权状态（红包领取前置）。

        未授权时仅提示，不自动发起 init（需用户在微信内手动签约）。
        """
        try:
            status_data = self.http.get(TXQ_AUTH_STATUS)
            status_result = _extract_result(status_data)
            if status_result and status_result.get("authorized"):
                return True

            state = (status_result or {}).get("state", "") if status_result else ""
            self.logger.raw(f"🧧 [收款授权] 微信收款未授权（state={state}），需在小程序内完成授权后领取")
            return False
        except Exception as e:
            self.logger.raw(f"🧧 [收款授权] 异常：{str(e)[:80]}")
            return False

    def run(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {"login_ok": False, "points": None,
                                   "points_before": None, "sign_ok": False, "info_ok": False, "task_count": 0}
        login_ok, pts = self.check_login()
        result["login_ok"] = login_ok
        result["points"] = pts
        result["points_before"] = pts
        if not login_ok:
            return result

        self._before_points = pts

        # 签到
        _random_sleep(1)
        self.sign_in()
        result["sign_ok"] = True

        # 签到宝箱领取（签到后重新查详情，看是否有可领取的宝箱）
        _random_sleep(1)
        self.claim_sign_rewards()

        # 每日抽奖
        _random_sleep(1)
        self.lucky_draw()
        result["lucky_draw_ok"] = True

        # 会员信息
        _random_sleep(1)
        self.query_member_info()
        result["info_ok"] = True

        # 查询最终积分（积分到账有延迟，等待后强制刷新查询一次）
        _random_sleep(2)
        after_pts = self._query_points()
        if after_pts is not None and self._before_points is not None and after_pts == self._before_points:
            # 积分未变化，等待 12 秒后强制刷新查询
            _random_sleep(12)
            after_pts = self._query_points(force=True)
        result["points"] = after_pts
        if after_pts is not None and self._before_points is not None:
            gained = after_pts - self._before_points
            self.logger.raw(f"💰 执行后积分：{after_pts}（本次 {'+' if gained >= 0 else ''}{gained}）")
        elif after_pts is not None:
            self.logger.raw(f"💰 执行后积分：{after_pts}")
        return result


# ==================== 辅助函数 ====================
def _random_sleep(base: float):
    """base 上下 30% 浮动的随机等待（仿真人）。"""
    time.sleep(random.uniform(base * 0.7, base * 1.3))


# ==================== 核心处理器 ====================
def run_account(account_info: Dict[str, Any], index: int, proxy_str: str = "") -> Dict[str, Any]:
    logger = Logger()
    token = account_info.get("token", "")
    openid = account_info.get("openid", "")

    http = TxqHttpClient(token, openid, fixed_proxy=proxy_str)

    masked = mask_account(openid)
    nick_name = account_info.get("nick_name", "")
    result = {'success': True, 'phone': masked, 'index': index, 'daily': {}, 'nickname': nick_name}

    if ENABLE_DAILY_TASK:
        logger.task("开始执行日常任务")
        daily = DailyTaskExecutor(http, logger)
        result['daily'] = daily.run()

    return result


def dispatch_summary(logger: Logger, results: List[Dict[str, Any]]) -> None:
    total = len(results)
    success = sum(1 for r in results if r.get("success"))
    failed = total - success

    lines = [
        "==============================",
        f"🕑 执行时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"📊 统计数据：成功 {success} / 总计 {total}",
        f"✅ 成功账号：{success} 个",
        f"❌ 失败账号：{failed} 个",
    ]

    for idx, r in enumerate(results, 1):
        ok = bool(r.get("success"))
        account_icon = "👤"
        account = r.get("phone") or "未知账号"
        nick = r.get("nickname") or ""
        account_label = f"{account}（{nick}）" if nick else account
        lines.extend([
            f"{account_icon} 【账号{idx}】{account_label}",
            f"{'✅' if ok else '❌'} 状态：{'执行成功' if ok else '执行失败'}",
        ])
        if ok:
            daily = r.get("daily") or {}
            if daily.get("login_ok"):
                before = daily.get("points_before")
                after = daily.get("points")
                if after is not None and before is not None:
                    gained = after - before
                    lines.append(f"💰 总积分: {after}（本次 +{gained}）")
                elif after is not None:
                    lines.append(f"💰 总积分: {after}")
        else:
            lines.append(f"⚠️ 原因：{r.get('error') or '登录失效'}")

    lines.append(f"======🎉 完成 {success} / 共 {total} 账号=======")
    print("\n[执行报表]\n" + "\n".join(lines))


def main():
    wxids = parse_env_accounts(TXQ_OPENIDS)
    print("==============================")
    print("🚀 汤星球小程序签到")
    print(f"📱 共配置 {len(wxids)} 个账号")
    print("==============================")

    if not TXQ_WX_SERVER:
        print("❌ 未配置 wx_server_url（应用宝网关地址），无法获取微信 code")
        return 1

    results: List[Dict[str, Any]] = []
    for index, wxid in enumerate(wxids, 1):
        _log_global(f">>> 账号 {index}/{len(wxids)} : {wxid[:6] + '***' + wxid[-4:] if len(wxid) >= 10 else wxid}")
        # 每账号只取一次代理，换 token 与业务请求共用同一代理 IP
        _proxy_dict = proxy_manager.get_proxy()
        _proxy_str = ""
        if _proxy_dict:
            _proxy_str = list(_proxy_dict.values())[0]
            _disp = _proxy_str.split('@')[-1] if '@' in _proxy_str else _proxy_str
            _log_global(f"🌐 代理: 启用***@{_disp}")
        mgr = AutoCookieManager(fixed_proxy=_proxy_str)
        try:
            info = mgr.get_token_for_wxid(wxid)
        except Exception as exc:
            info = None
            _log_global(f"❌ 账号[{index}] {mask_account(wxid)} 自动获取 token 异常：{str(exc)[:80]}")

        if not (info and info.get("token")):
            _log_global(f"❌ 账号[{index}] {mask_account(wxid)} 自动获取 token 失败")
            _log_global("   请检查该微信是否在线、是否已授权汤星球小程序")
            results.append({'success': False, 'phone': mask_account(wxid),
                            'error': '登录失败', 'index': index, 'daily': {}})
            if index < len(wxids):
                time.sleep(2)
            continue

        result = run_account(info, index, _proxy_str)
        if not result.get('success'):
            result.setdefault('error', result.get('phone') or '登录失效')
        results.append(result)
        if index < len(wxids):
            time.sleep(2)

    if not results:
        print("❌ 未获取到在线汤星球账号，请检查 wx_server_url / txq_openid")
        return 1

    dispatch_summary(Logger(), results)
    total_failed = sum(1 for r in results if not r.get("success"))
    return 0 if total_failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
