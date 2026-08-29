"""
Author: anonymous
Date: 2026.08.29
Description: 飞鹤小程序签到 + 浏览任务
Cron: 5 9,12,20 * * *
----------------------------------------------------------------------------------------------
飞鹤小程序自动任务 v1.0.0

功能：自动执行飞鹤小程序每日签到和浏览任务获取积分，支持多账号执行。

配置说明：
1. 微信 code 网关：（适配应用宝协议，ck 自动获取）
   wx_server_url                                   必填，自建授权服务器地址
   - 示例：http://127.0.0.1:8000
   - 脚本会自动拼接 /wxapp/getCode
   - 请求格式：POST {网关}/wxapp/getCode
   - 请求体：{"app_id": "<小程序appid>", "ref": "账号openid"}

2. 账号变量：
   fh_openid                                       推荐，飞鹤小程序专属账号变量
   - 多账号支持使用 &、英文逗号、中文逗号或换行分隔
   - 示例：openid_a&openid_b 或 openid_a,openid_b

3. 代理变量（可选，适配品赞代理）：
   proxy_api_url                                   品赞代理 API 地址，开启后每个账号自动获取代理
   - 代理接口返回格式支持：纯 IP:PORT，或带账号密码的 IP:PORT ACCOUNT PASSWORD（品赞格式）
   - 单账号固定代理：在账号后追加 #proxy=IP:PORT 可指定该账号专用代理

4. 青龙任务建议：
   名称：飞鹤小程序签到
   命令：task fh.py
   定时：每天运行 1 - 3 次即可，具体时间自行调整
----------------------------------------------------------------------------------------------
"""

import os
import random
import sys
import re
import time
import hashlib
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
ENABLE_DAILY_TASK = True         # 日常任务（登录态验证 + 签到 + 浏览任务）

# 统一变量名称与默认值映射
FH_WX_SERVER = (os.environ.get("wx_server_url") or "").strip().rstrip("/")
FH_WX_APPID = "wx4205ec55b793245e"                                               # 飞鹤小程序 appid
FH_OPENIDS = os.environ.get("fh_openid") or ""                                   # 飞鹤小程序专属 openid

# 飞鹤星妈优选网关域名
FH_BASE = "https://www.feihevip.com"
FH_APP_ID = "xmyx"
FH_APP_KEY = "TwUQ01lKS1Km5zlV2f7amsZc5EQYkTbv"

# 代理模块：由环境变量 proxy_api_url 驱动
PROXY_API_URL = os.getenv("proxy_api_url", "")
PROXY_TYPE = os.getenv("fh_proxy_type", "socks5")
PROXY_TIMEOUT = 20
MAX_PROXY_RETRIES = 5
print_lock = Lock()

# 默认 User-Agent（Android 微信小程序环境）
FH_UA = (
    "Mozilla/5.0 (Linux; Android 15; PKX110 Build/AP3A.240617.008; wv) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/146.0.7680.178 "
    "Mobile Safari/537.36 XWEB/1460249 MMWEBSDK/20240301 MMWEBID/8694 "
    "MicroMessenger/8.0.48.2580(0x28003035) WeChat/arm64 Weixin NetType/4G "
    "Language/zh_CN ABI/arm64 MiniProgramEnv/android"
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


# ==================== fhSign 签名（逆向核心） ====================
def _d(obj: Dict) -> Dict[str, str]:
    """值转字符串：null->'', 对象->JSON, 其他->String"""
    r = {}
    for k, v in obj.items():
        if v is None:
            r[k] = ""
        elif isinstance(v, (dict, list)):
            r[k] = json.dumps(v)
        else:
            r[k] = str(v)
    return r


def fh_sign(method: str, params: Optional[Dict] = None, body: Any = None,
            token: str = "") -> Tuple[Dict[str, str], str]:
    """生成请求头（含 fhSign 签名）。GET 用 params 拼 query，POST 用 body。

    签名算法：
      1. 构造三键 map: {fhAppid, fhNonceStr, fhTimestamp}
      2. body 串：对象 -> JSON.stringify，空 -> ""
      3. keys = 三键 + [body串]，排序
      4. 合并 map：三键 + body 对象字段（如果是 dict）
      5. 拼接：遍历 keys，value 非空则 key+value，空则 key
      6. fhSign = MD5(拼接串 + APP_KEY).upper()
    """
    ts = str(int(time.time()))
    nonce = ''.join(random.choices(string.ascii_letters + string.digits, k=16))

    headers = {
        "referrercrm": "",
        "orderupdate": "1",
        "charset": "utf-8",
        "fhappid": FH_APP_ID,
        "User-Agent": FH_UA,
        "source": "1",
        "token": token,
        "content-type": "application/json",
        "visit": "false",
        "Referer": f"https://servicewechat.com/{FH_WX_APPID}/427/page-frame.html",
    }

    # ---- 构造签名串 ----
    f = {"fhAppid": FH_APP_ID, "fhNonceStr": nonce, "fhTimestamp": ts}
    # body 串
    if isinstance(body, (dict, list)):
        body_str = json.dumps(body)
    elif body:
        body_str = str(body)
    else:
        body_str = ""
    # keys = 三键 + [body串] 排序
    keys = list(f.keys()) + [body_str]
    keys.sort()
    # 合并 map：三键 headers + body（body 为对象时并入）
    merged = dict(f)
    if isinstance(body, dict) and body:
        for k, v in body.items():
            merged[k] = v
    l = _d(merged)
    # reduce 拼接
    A = ""
    for k in keys:
        v = l.get(k)
        if v:
            A += k + v
        else:
            A += str(k)
    fhsign = hashlib.md5((A + FH_APP_KEY).encode()).hexdigest().upper()

    headers["fhsign"] = fhsign
    headers["fhtimestamp"] = ts
    headers["fhnoncestr"] = nonce
    return headers, fhsign


# ==================== AutoCookieManager ====================
class AutoCookieManager:
    """通过应用宝网关 /wxapp/getCode 获取微信 code，再走 getUserToken 换取登录态 token。"""
    def __init__(self, wx_server: str = None, fixed_proxy: str = ""):
        self.wx_server = (wx_server or FH_WX_SERVER).strip().rstrip("/")
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
        target_appid = appid or FH_WX_APPID
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
        """调用 /api/starMember/getUserToken 用 code 换取登录态 token。

        请求: GET /api/starMember/getUserToken?code=<code>
        成功响应: {"code":"200","data":{"token":"...","openid":"...",...}}
        """
        try:
            headers, _ = fh_sign("GET", params={"code": code}, token="")
            url = f"{FH_BASE}/api/starMember/getUserToken?code={code}"
            r = self.session.get(url, headers=headers, timeout=25,
                                 proxies=self._fixed_proxy or None)
            j = r.json()
            tok = (j.get("data") or {}).get("token") or j.get("token")
            if tok:
                return j.get("data") or {"token": tok}
            _log_global(f"⚠️ getUserToken 返回非预期: {str(j)[:160]}")
            return None
        except Exception as e:
            _log_global(f"❌ getUserToken 异常: {str(e)[:100]}")
            return None

    def get_token_for_wxid(self, wxid: str) -> Optional[Dict]:
        """通过 /wxapp/getCode 拿到 code 后，走 getUserToken 换取登录态 token。"""
        code = self._get_wx_code(wxid, FH_WX_APPID)
        if not code:
            return None

        result = self._auth_by_code(code)
        if not result:
            _log_global(f"❌ {wxid[:10]}*** getUserToken 换 token 失败")
            return None

        token = result.get("token", "")
        if not token:
            _log_global(f"❌ {wxid[:10]}*** getUserToken 未返回 token")
            return None

        _log_global(f"✅ 登录成功, token:{token[:16]}...")

        # 查询用户信息
        nick_name = ""
        try:
            mi_headers, _ = fh_sign("POST", body={}, token=token)
            mi_url = f"{FH_BASE}/api/starMember/getMemberInfo"
            mi_r = self.session.post(mi_url, json={}, headers=mi_headers, timeout=25,
                                     proxies=self._fixed_proxy or None)
            mi_j = mi_r.json()
            base_info = (mi_j.get("data") or {}).get("baseInfo") or {}
            nick_name = base_info.get("nickName") or ""
            if nick_name:
                _log_global(f"👤 用户：{nick_name}")
            else:
                phone = base_info.get("fullName") or base_info.get("mobile") or ""
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

    def get_tokens_for_wxids(self, wxids: List[str] = None) -> Dict[str, Dict]:
        if not wxids:
            wxids = parse_env_accounts(FH_OPENIDS)

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


# ==================== HTTP 客户端 ====================
class FHHttpClient:
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
        """统一请求入口：自动构造 fhSign 签名头。"""
        url = FH_BASE + path
        headers, _ = fh_sign(method, params, body, self.token)
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
FH_GET_SIGN_INFO = "/api/member/signin/getSignInfo"                       # 查询签到状态 + 积分
FH_SIGN = "/api/member/signin/sign"                                       # 执行签到
FH_TASK_LIST = "/api/member/signin/getTaskList"                           # 浏览任务列表
FH_TASK_DETAIL = "/api/member/signin/taskDetail"                          # 任务详情
FH_TO_FINISH = "/api/member/signin/tofinish"                              # 触发浏览
FH_COMPLETE_TASK = "/api/member/signin/completeTask"                      # 完成任务领积分
FH_QUERY_COMPLETE_RESULT = "/api/member/signin/queryTaskCompleteResult"   # 查询完成结果


class DailyTaskExecutor:
    def __init__(self, http: FHHttpClient, logger: Logger):
        self.http = http
        self.logger = logger
        self._before_points: Optional[int] = None

    def _query_points(self) -> Optional[int]:
        """查询当前积分（重试 1 次，间隔 3 秒）。"""
        for attempt in range(2):
            try:
                info = self.http.get(FH_GET_SIGN_INFO, {"signType": "1"})
                if info and isinstance(info.get("data"), dict):
                    pt = (info["data"].get("memberDetail") or {}).get("totalPoint")
                    if pt is not None:
                        return int(pt)
            except Exception:
                pass
            if attempt == 0:
                time.sleep(3)
        return None

    def check_login(self) -> Tuple[bool, Optional[int]]:
        """用签到信息接口验证登录态并取当前积分。返回 (登录有效, 当前积分)。"""
        data = self.http.get(FH_GET_SIGN_INFO, {"signType": "1"})
        if data and str(data.get("code")) == "200" and isinstance(data.get("data"), dict):
            pt = (data["data"].get("memberDetail") or {}).get("totalPoint")
            pts = int(pt) if pt is not None and str(pt).isdigit() else None
            self.logger.raw(f"💰 当前积分：{pts if pts is not None else '?'}")
            return True, pts
        self.logger.warning(f"[登录态校验] 失败：{str(data)[:120]}")
        return False, None

    def sign_in(self) -> None:
        """每日签到。"""
        sign_info = self.http.get(FH_GET_SIGN_INFO, {"signType": "1"})
        signed = bool(sign_info and sign_info.get("data") and
                      sign_info["data"].get("isSignedInToday"))
        if signed:
            self.logger.raw("📅 [每日签到] 今日已签到")
            return
        self.logger.raw("📅 [每日签到] 今日未签到，执行签到...")
        data = self.http.post(FH_SIGN, {})
        if data and str(data.get("code")) == "200":
            self.logger.raw("📅 [每日签到] 签到成功")
        elif data and "已签" in str(data.get("msg") or ""):
            self.logger.raw("📅 [每日签到] 签到成功（已签到）")
        else:
            self.logger.warning(f"📅 [每日签到] 签到失败：{str(data or '未知')[:80]}")

    def do_browse_tasks(self) -> None:
        """浏览任务：遍历未完成的浏览任务，模拟浏览时长后领取积分。"""
        tasks = self.http.get(FH_TASK_LIST)
        if not tasks or str(tasks.get("code")) != "200":
            self.logger.warning(f"[浏览任务] 获取任务列表失败：{str(tasks)[:120]}")
            return

        task_list = tasks.get("data") or []
        todo_tasks = []
        for t in task_list:
            ttype = (t.get("taskType") or "").strip()
            if not ttype:
                continue
            is_comp = t.get("isCompleted")
            tname = t.get("taskName") or ttype
            # 已完成任务跳过
            if is_comp or t.get("completedCount", 0) >= t.get("totalCount", 1):
                continue
            # 加群任务（type=6）需手动，跳过
            if ttype == "6" or "进群" in (tname or ""):
                self.logger.task_skip(f"[浏览任务] 跳过(需手动): {tname} ({ttype})")
                continue
            todo_tasks.append(t)

        self.logger.raw(f"📺 [浏览任务] 待处理: {len(todo_tasks)} 个")

        done_cnt = 0
        task_pts = 0
        failed_tasks: List[str] = []

        for t in todo_tasks:
            try:
                ttype = (t.get("taskType") or "").strip()
                tname = t.get("taskName") or ttype
                dur = t.get("completeTaskDuration")
                wait = int(dur) if dur else 5
                self.logger.raw(f"📺 [浏览任务] {tname} ({ttype})，需浏览 {wait} 秒")

                # taskDetail
                self.http.get(FH_TASK_DETAIL, {"taskType": ttype})
                _random_sleep(1)
                # tofinish 触发浏览
                self.http.get(FH_TO_FINISH, {"taskType": ttype})
                self.logger.raw(f"⏳ [浏览任务] 浏览中... 等待 {wait}-{int(wait * 1.4)} 秒")
                time.sleep(random.uniform(wait * 1.0, wait * 1.4))

                # completeTask 领积分（重试 1 次）
                ok_task = False
                for _try in range(2):
                    cd = self.http.get(FH_COMPLETE_TASK, {"taskType": ttype})
                    _cd_data = (cd or {}).get("data") or {}
                    _comp = _cd_data.get("complete")
                    _comp_ok = _comp in (True, 1, "1", "true", "True")
                    if _comp_ok:
                        ok_task = True
                        pts = _cd_data.get("awardSendPoints")
                        self.logger.raw(f"📺 [浏览任务] {tname} 完成！获得 {pts} 积分")
                        try:
                            task_pts += int(pts or 0)
                        except Exception:
                            pass
                        break
                    if _try == 0:
                        _random_sleep(2)

                if ok_task:
                    done_cnt += 1
                else:
                    self.logger.warning(f"📺 [浏览任务] {tname} completeTask 失败")
                    failed_tasks.append(tname)
            except Exception as e:
                failed_tasks.append(t.get("taskName") or "?")
                self.logger.warning(f"📺 [浏览任务] 异常: {str(e)[:60]}")
            _random_sleep(1)

        if done_cnt > 0:
            self.logger.task_complete(f"[浏览任务] 完成 {done_cnt} 个（+{task_pts}积分）")
        elif failed_tasks:
            self.logger.warning(f"[浏览任务] 全部失败（{len(failed_tasks)} 个）")
        else:
            self.logger.raw("📺 [浏览任务] 无待完成浏览任务")

    def run(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {"login_ok": False, "points": None,
                                   "points_before": None, "sign_ok": False, "task_count": 0}
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

        # 浏览任务
        _random_sleep(1)
        self.do_browse_tasks()

        # 查询最终积分
        _random_sleep(1)
        after_pts = self._query_points()
        result["points"] = after_pts
        if after_pts is not None and self._before_points is not None:
            gained = after_pts - self._before_points
            self.logger.raw(f"💰 执行后积分：{after_pts}（本次 +{gained}）")
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

    http = FHHttpClient(token, openid, fixed_proxy=proxy_str)

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
    wxids = parse_env_accounts(FH_OPENIDS)
    print("==============================")
    print("🚀 飞鹤小程序签到")
    print(f"📱 共配置 {len(wxids)} 个账号")
    print("==============================")

    if not FH_WX_SERVER:
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
            _log_global("   请检查该微信是否在线、是否已授权飞鹤星妈优选小程序")
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
        print("❌ 未获取到在线飞鹤星妈优选账号，请检查 wx_server_url / fh_openid")
        return 1

    dispatch_summary(Logger(), results)
    total_failed = sum(1 for r in results if not r.get("success"))
    return 0 if total_failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
