"""
Author: anonymous
Date: 2026.08.27
Description: 星妈会小程序签到
Cron: 5 9,12,20 * * *
----------------------------------------------------------------------------------------------
星妈会小程序签到 v1.0.0

功能：自动执行星妈会小程序每日签到和完成任务获取积分，支持多账号执行。

配置说明：
1. 微信 code 网关：（适配应用宝协议，ck 自动获取）
   wx_server_url                                   必填，自建授权服务器地址
   - 示例：http://127.0.0.1:8000
   - 脚本会自动拼接 /wxapp/getCode
   - 请求格式：POST {网关}/wxapp/getCode
   - 请求体：{"app_id": "<小程序appid>", "ref": "账号openid"}

2. 账号变量：
   xmh_openid                                      推荐，星妈会小程序专属账号变量
   - 多账号支持使用 &、英文逗号、中文逗号或换行分隔
   - 示例：openid_a&openid_b 或 openid_a,openid_b

3. 代理变量（可选，适配品赞代理）：
   proxy_api_url                                   品赞代理 API 地址，开启后每个账号自动获取代理
   - 代理接口返回格式支持：纯 IP:PORT，或带账号密码的 IP:PORT ACCOUNT PASSWORD（品赞格式）
   - 单账号固定代理：在账号后追加 #proxy=IP:PORT 可指定该账号专用代理

4. 青龙任务建议：
   名称：星妈会小程序签到
   命令：task xmh.py
   定时：每天运行 1 - 3 次即可，具体时间自行调整
----------------------------------------------------------------------------------------------
"""

import os
import random
import sys
import re
import time
import json
import string
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
ENABLE_DAILY_TASK = True         # 日常任务（登录 + 积分查询 + 做任务）

# 统一变量名称与默认值映射
XMH_WX_SERVER = (os.environ.get("wx_server_url") or "").strip().rstrip("/")
XMH_WX_APPID = "wxc83b55d61c7fc51d"                                              # 星妈会小程序 appid
XMH_OPENIDS = os.environ.get("xmh_openid") or ""                            # 星妈会小程序专属 openid

# 星妈会网关域名
XMH_BASE = "https://momclub.feihe.com"

# 代理模块：由环境变量 proxy_api_url 驱动
PROXY_API_URL = os.getenv("proxy_api_url", "")
PROXY_TYPE = os.getenv("xmh_proxy_type", "socks5")
PROXY_TIMEOUT = 20
MAX_PROXY_RETRIES = 5
print_lock = Lock()

# 默认 User-Agent（Windows 微信小程序环境，与抓包一致）
XMH_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/144.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI "
    "MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a13) "
    "UnifiedPCWindowsWechat(0xf2541d0c) XWEB/25510"
)


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
    """通过应用宝网关 /wxapp/getCode 获取微信 code，再走 /capis/social/ma 换取登录态 token。"""
    def __init__(self, wx_server: str = None, fixed_proxy: str = ""):
        self.wx_server = (wx_server or XMH_WX_SERVER).strip().rstrip("/")
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
        target_appid = appid or XMH_WX_APPID
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
        """调用 POST /capis/social/ma 用 code 换取登录态 token。

        请求: POST /capis/social/ma
        请求体: "<code>"（JSON 字符串形式，Content-Type: application/json）
        成功响应: {"code":"00000","data":{"tempUid":"...","tokenInfo":{"accessToken":"...","expiresIn":604800}}}
        同时 Set-Cookie: authorization=<token>; Max-Age=604800
        """
        try:
            url = f"{XMH_BASE}/capis/social/ma"
            headers = {
                "Content-Type": "application/json",
                "User-Agent": XMH_UA,
                "Authorization": "",
                "locale": "zh_CN",
                "Referer": f"https://servicewechat.com/{XMH_WX_APPID}/163/page-frame.html",
            }
            # body 是 JSON 字符串形式（code 用双引号包裹）
            body = json.dumps(code)
            r = self.session.post(url, data=body, headers=headers, timeout=25,
                                  proxies=self._fixed_proxy or None)
            j = r.json()
            if j.get("code") == "00000" or j.get("success"):
                token_info = (j.get("data") or {}).get("tokenInfo") or {}
                token = token_info.get("accessToken")
                if token:
                    return {
                        "token": token,
                        "tempUid": (j.get("data") or {}).get("tempUid", ""),
                        "expiresIn": token_info.get("expiresIn", 604800),
                    }
            _log_global(f"⚠️ /capis/social/ma 返回非预期: {str(j)[:160]}")
            return None
        except Exception as e:
            _log_global(f"❌ /capis/social/ma 异常: {str(e)[:100]}")
            return None

    def get_token_for_wxid(self, wxid: str) -> Optional[Dict]:
        """通过 /wxapp/getCode 拿到 code 后，走 /capis/social/ma 换取登录态 token。"""
        code = self._get_wx_code(wxid, XMH_WX_APPID)
        if not code:
            return None

        result = self._auth_by_code(code)
        if not result:
            _log_global(f"❌ {wxid[:10]}*** /capis/social/ma 换 token 失败")
            return None

        token = result.get("token", "")
        if not token:
            _log_global(f"❌ {wxid[:10]}*** /capis/social/ma 未返回 token")
            return None

        _log_global(f"✅ 登录成功, token:{token[:16]}...")

        # 查询用户信息
        nick_name = ""
        mobile = ""
        try:
            ui_url = f"{XMH_BASE}/capis/p/user/userInfo"
            ui_headers = self._build_headers(token)
            ui_r = self.session.get(ui_url, headers=ui_headers, timeout=25,
                                    proxies=self._fixed_proxy or None)
            ui_j = ui_r.json()
            if ui_j.get("code") == "00000":
                data = ui_j.get("data") or {}
                nick_name = data.get("nickName") or ""
                mobile = data.get("userMobile") or data.get("mobile") or ""
                if nick_name:
                    _log_global(f"👤 用户：{nick_name}")
                elif mobile:
                    masked_phone = mobile[:3] + "****" + mobile[-4:] if len(mobile) >= 7 else mobile
                    _log_global(f"👤 用户：{masked_phone}")
                else:
                    _log_global("👤 用户：未知")
        except Exception:
            pass

        return {
            "token": token,
            "openid": wxid,
            "nick_name": nick_name,
            "mobile": mobile,
        }

    def get_tokens_for_wxids(self, wxids: List[str] = None) -> Dict[str, Dict]:
        if not wxids:
            wxids = parse_env_accounts(XMH_OPENIDS)

        results = {}
        for i, wxid in enumerate(wxids):
            try:
                info = self.get_token_for_wxid(wxid)
                if info:
                    results[wxid] = info
            except Exception:
                pass
            if i < len(wxids) - 1:
                time.sleep(2)
        return results

    @staticmethod
    def _build_headers(token: str) -> Dict[str, str]:
        """构造星妈会业务请求头"""
        return {
            "Authorization": token,
            "Content-Type": "application/json",
            "User-Agent": XMH_UA,
            "locale": "zh_CN",
            "Referer": f"https://servicewechat.com/{XMH_WX_APPID}/163/page-frame.html",
            "xweb_xhr": "1",
            "Accept": "*/*",
        }


# ==================== HTTP 客户端 ====================
class XMHHttpClient:
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

    def request(self, method: str, path: str, params: Optional[Dict] = None,
                body: Any = None) -> Optional[Dict]:
        """统一请求入口：自动构造 Authorization 头。"""
        url = XMH_BASE + path
        headers = AutoCookieManager._build_headers(self.token)
        kwargs = {"headers": headers, "timeout": PROXY_TIMEOUT}
        if params:
            url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
        if method == "POST":
            if body is not None:
                kwargs["data"] = json.dumps(body)
            else:
                kwargs["data"] = "{}"
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
XMH_USER_INFO = "/capis/p/user/userInfo"                        # 用户信息（含积分/手机号）
XMH_MEMBER_INFO = "/capis/c/user/memberInfo"                    # 会员信息（含积分/等级）
XMH_USER_INDEX = "/capis/c/user/index"                          # 用户首页（含 memberId/openId）
XMH_TODO_QUERY = "/capis/c/activity/todo/queryTodoResult"       # 待办任务
XMH_SCORE_CARD = "/capis/p/score/scoreLevel/cardDetail"         # 积分卡详情
XMH_ORIGIN_INVITE = "/capis/c/activity/origin_invite/home"      # 邀请活动首页
XMH_TODO_LIST = "/capis/c/activity/todo/list"                   # 签到任务列表（含签到状态）
XMH_CHECK_IN = "/capis/c/activity/todo/checkIn"                 # 执行签到
XMH_TODO_COMPLETE = "/capis/c/activity/todo/complete"           # 完成浏览任务
XMH_TODO_RECEIVE = "/capis/c/activity/todo/receive"             # 领取任务积分


class DailyTaskExecutor:
    def __init__(self, http: XMHHttpClient, logger: Logger):
        self.http = http
        self.logger = logger
        self._before_points: Optional[int] = None

    def _query_points(self) -> Optional[int]:
        """查询当前积分（多源兜底，重试 1 次，间隔 3 秒）。

        星妈会各接口积分字段分散：任务积分(credits)可能落在 userInfo / scoreCard，
        memberInfo.points 未必含活动积分。依次尝试更可靠的积分来源：
          1. userInfo（用户信息，含积分）
          2. scoreCard（积分卡详情，含成长值/积分）
          3. memberInfo.points（兜底）
        命中任一有效积分即返回。
        """
        def _extract_from(endpoint: str, keys) -> Optional[int]:
            try:
                resp = self.http.get(endpoint)
                if not (resp and resp.get("code") == "00000"):
                    return None
                data = resp.get("data") or {}
                for k in keys:
                    v = data.get(k)
                    if v is not None and str(v).strip().isdigit():
                        return int(v)
            except Exception:
                pass
            return None

        sources = [
            (XMH_USER_INFO, ("integral", "points", "credits", "score", "growthValue")),
            (XMH_SCORE_CARD, ("integral", "points", "credits", "score", "growthValue", "levelValue")),
            (XMH_MEMBER_INFO, ("points", "integral", "credits")),
        ]
        for attempt in range(2):
            for endpoint, keys in sources:
                pt = _extract_from(endpoint, keys)
                if pt is not None:
                    return pt
            if attempt == 0:
                time.sleep(3)
        return None

    def check_login(self) -> Tuple[bool, Optional[int]]:
        """用会员信息接口验证登录态并取当前积分。返回 (登录有效, 当前积分)。"""
        data = self.http.get(XMH_MEMBER_INFO)
        if data and data.get("code") == "00000" and isinstance(data.get("data"), dict):
            d = data["data"]
            for k in ("points", "integral", "credits"):
                pt = d.get(k)
                if pt is not None and str(pt).isdigit():
                    return True, int(pt)
            return True, None
        self.logger.warning(f"[登录态校验] 失败：{str(data)[:120]}")
        return False, None

    def sign_in(self) -> int:
        """每日签到。先查签到状态，未签到则执行签到。返回本次签到获得的积分。"""
        todo = self.http.get(XMH_TODO_LIST, {"mockTime": int(time.time() * 1000)})
        if not todo or todo.get("code") != "000000":
            self.logger.warning(f"📅 [每日签到] 获取签到状态失败：{str(todo)[:80]}")
            return 0

        check_in_todo = (todo.get("data") or {}).get("checkInTodo") or {}
        join_record = (check_in_todo.get("checkInExtra") or {}).get("joinRecord") or []
        today_done = any(r.get("today") and r.get("joined") for r in join_record)
        if today_done:
            self.logger.raw("📅 [每日签到] 今日已签到")
            return 0

        activity_id = check_in_todo.get("id") or 1111
        self.logger.raw("📅 [每日签到] 今日未签到，执行签到...")
        data = self.http.post(XMH_CHECK_IN, {
            "activityId": activity_id,
            "mockTime": int(time.time() * 1000),
        })
        credits = 0
        if data and isinstance(data.get("data"), dict):
            cv = data["data"].get("credits")
            if isinstance(cv, int):
                credits = cv
            elif str(cv).isdigit():
                credits = int(cv)
        self.logger.raw(f"📅 [每日签到] 签到成功（{credits or '?'} 积分）" if data else "📅 [每日签到] 签到成功")
        return credits

    def do_tasks(self) -> int:
        """做任务赚积分。从 todo/list 的 taskTodo 中筛选 BROWSE_PAGE 类型任务，
        对 completeCount < completeLimit 的执行 receive + complete 。
        返回本次任务实际领取到的积分总额（credits 累加）。"""
        todo = self.http.get(XMH_TODO_LIST, {"mockTime": int(time.time() * 1000)})
        if not todo or todo.get("code") != "000000":
            self.logger.warning(f"🎯 [做任务赚积分] 获取任务列表失败：{str(todo)[:80]}")
            return 0

        tasks = (todo.get("data") or {}).get("taskTodo") or []
        if not tasks:
            self.logger.raw("🎯 [做任务赚积分] 任务列表为空")
            return 0

        browse_tasks = [t for t in tasks
                       if (t.get("taskTodoExtra") or {}).get("type") == "BROWSE_PAGE"]
        completable = [t for t in browse_tasks
                       if (t.get("taskTodoExtra") or {}).get("completeCount", 0) <
                          (t.get("taskTodoExtra") or {}).get("completeLimit", 1)]
        if not completable:
            self.logger.raw(f"🎯 [做任务赚积分] 浏览任务全部完成（共 {len(browse_tasks)} 个）")
            return 0

        self.logger.raw(f"🎯 [做任务赚积分] 发现 {len(completable)} 个可做的浏览任务")
        done = 0
        earned = 0
        for task in completable:
            task_id = task.get("id")
            extra = task.get("taskTodoExtra") or {}
            name = extra.get("title") or task.get("name", "")
            credits = extra.get("credits", "?")
            self.logger.raw(f"  ▸ 开始任务：{name}（+{credits}积分）")
            enter_wait = random.uniform(2, 3)
            self.logger.raw(f"    进入任务页，模拟等待 {enter_wait:.1f} 秒...")
            time.sleep(enter_wait)  # 列表页点进去（约 2.8 秒）
            # 真实顺序：先 receive（发起领取意图）→ 浏览停留 → complete（上报已完成）
            recv = self.http.post(XMH_TODO_RECEIVE, {
                "activityId": task_id,
                "mockTime": int(time.time() * 1000),
            })
            if not recv or recv.get("code") != "000000":
                self.logger.warning(f"    领取发起失败：{str(recv)[:80]}")
                _random_sleep(2)
                continue
            browse_wait = random.uniform(8, 15)
            self.logger.raw(f"    正在浏览中，模拟停留 {browse_wait:.1f} 秒...")
            time.sleep(browse_wait)  # 浏览停留（ receive→complete 之间 8~15 秒）
            self.logger.raw(f"    已完成浏览")
            comp = self.http.post(XMH_TODO_COMPLETE, {
                "activityId": task_id,
                "mockTime": int(time.time() * 1000),
            })
            if not comp or comp.get("code") != "000000":
                self.logger.warning(f"    完成失败：{str(comp)[:80]}")
                _random_sleep(2)
                continue
            self.logger.raw(f"    领取成功：+{credits} 积分")
            done += 1
            if isinstance(credits, int):
                earned += credits
            elif str(credits).isdigit():
                earned += int(credits)
            _random_sleep(2)
        if done:
            self.logger.raw(f"🎯 [做任务赚积分] 共完成 {done} 个任务，获得 {earned} 积分")
        return earned

    def run(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {"login_ok": False, "points": None,
                                   "points_before": None, "sign_ok": False}
        login_ok, pts = self.check_login()
        result["login_ok"] = login_ok
        result["points"] = pts
        result["points_before"] = pts
        if not login_ok:
            return result

        self._before_points = pts
        if pts is not None:
            self.logger.points(pts, "当前积分")

        # 签到（sign_earned 仅用于流程判断，统计实际积分改用前后差值，不依赖它）
        _random_sleep(1)
        sign_earned = self.sign_in()
        result["sign_ok"] = True  # 执行到此处即表示已尝试签到（含今日已签到）

        # 做任务赚积分（do_tasks 内部按 receive 成功即计任务配置积分，仅用于展示完成数）
        _random_sleep(1)
        task_earned = self.do_tasks()

        # 查询最终积分（多接口兜底，优先取活动积分来源，仅用于展示总积分）
        _random_sleep(1)
        after_pts = self._query_points()
        result["points"] = after_pts

        # 本次获得积分 = 领取前后真实查询差值（统计口径）
        gained = None
        if after_pts is not None and self._before_points is not None:
            gained = max(0, after_pts - self._before_points)
        result["gained"] = gained  # 本次实际获得（前后差值），供报表直接使用

        if after_pts is not None:
            g_txt = gained if gained is not None else "?"
            self.logger.raw(f"💰 执行后积分：{after_pts}（本次 +{g_txt}）")
        else:
            self.logger.raw(f"💰 本次获得积分：{gained}（接口未返回总积分）")
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

    http = XMHHttpClient(token, openid, fixed_proxy=proxy_str)

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
        f"🕒 执行时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
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
                after = daily.get("points")
                gained = daily.get("gained")
                if after is not None and gained is not None:
                    lines.append(f"💰 总积分: {after}（本次 +{gained}）")
                elif after is not None:
                    lines.append(f"💰 总积分: {after}")
        else:
            lines.append(f"⚠️ 原因：{r.get('error') or '登录失效'}")

    lines.append(f"======🎉 完成 {success} / 共 {total} 账号=======")
    print("\n[执行报表]\n" + "\n".join(lines))


def main():
    wxids = parse_env_accounts(XMH_OPENIDS)
    print("==============================")
    print("🚀 星妈会小程序签到")
    print(f"📱 共配置 {len(wxids)} 个账号")
    print("==============================")

    if not XMH_WX_SERVER:
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
            _log_global("   请检查该微信是否在线、是否已授权星妈会小程序")
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
        print("❌ 未获取到在线星妈会账号，请检查 wx_server_url / xmh_openid")
        return 1

    dispatch_summary(Logger(), results)
    total_failed = sum(1 for r in results if not r.get("success"))
    return 0 if total_failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
