"""
Author: anonymous
Date: 2026.10.13
Description: 匠心忠华小程序签到
Cron: 5 9,12,20 * * *
----------------------------------------------------------------------------------------------
匠心忠华小程序自动任务 v1.0.0

功能：自动执行匠心忠华小程序每日签到和看视频广告获取积分任务，支持多账号执行。

配置说明：
1. 微信 code 网关：（适配应用宝协议，ck 自动获取）
   wx_server_url                                   必填，自建授权服务器地址
   - 示例：http://127.0.0.1:8000
   - 脚本会自动拼接 /wxapp/getCode
   - 请求格式：POST {网关}/wxapp/getCode
   - 请求体：{"app_id": "<小程序appid>", "ref": "账号openid"}

2. 账号变量：
   jxzh_openid                                     推荐，匠心忠华专属账号变量
   - 多账号支持使用 &、英文逗号、中文逗号或换行分隔
   - 示例：openid_a&openid_b 或 openid_a,openid_b

3. 代理变量（可选，适配品赞代理）：
   proxy_api_url                                   品赞代理 API 地址，开启后每个账号自动获取代理
   - 代理接口返回格式支持：纯 IP:PORT，或带账号密码的 IP:PORT ACCOUNT PASSWORD（品赞格式）
   - 单账号固定代理：在账号后追加 #proxy=IP:PORT 可指定该账号专用代理

4. 青龙任务建议：
   名称：匠心忠华自动任务
   命令：task jxzh.py
   定时：每天运行 1 - 3 次即可，具体时间自行调整
----------------------------------------------------------------------------------------------
"""

import os
import random
import sys
import re
import time
import hashlib
import hmac
import base64
import json
import urllib.parse
from datetime import datetime, timedelta

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.asymmetric.ec import ECDH, SECP256R1

# Windows 控制台默认 GBK 无法编码 emoji/特殊字符，强制 stdout/stderr 为 UTF-8
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass
from typing import Dict, List, Optional, Any, Tuple
from threading import Lock
import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

# ==================== 配置区域 ====================
ENABLE_DAILY_TASK = True         # 日常任务

# 统一变量名称与默认值映射
JXZH_WX_SERVER = (os.environ.get("wx_server_url") or "").strip().rstrip("/")
JXZH_WX_APPID = "wxddaa0832e6acc5f1"                                               # 匠心忠华小程序 appid
JXZH_VERSION = "1.3.06"                                                            # 小程序版本号
JXZH_OPENIDS = os.environ.get("jxzh_openid") or ""                                 # 匠心忠华专属 openid

# 匠心忠华网关域名
JXZH_BASE = "https://api.quwayouxuan.com"
# 登录换 ck 接口
JXZH_LOGIN_PATH = "/mini_program/get_openid.do"

# 代理模块：由环境变量 proxy_api_url 驱动
PROXY_API_URL = os.getenv("proxy_api_url", "")
PROXY_TYPE = os.getenv("jxzh_proxy_type", "socks5")
PROXY_TIMEOUT = 15
MAX_PROXY_RETRIES = 5
print_lock = Lock()


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


# ==================== 签名（key = SHA1 拼参签名） ====================
# 算法：参数按 key 升序拼接为 k=v&k=v，追加固定密钥 superjing，URL 编码后 SHA1 小写。
JXZH_SIGN_SECRET = "superjing"


def generate_key(params: Dict[str, str]) -> str:
    """按服务端规则生成 key：sorted(k=v) 拼接 + 密钥，quote 后 SHA1。

    注意：params 不应包含 key 自身。
    """
    ordered = sorted(params.keys())
    raw = "".join(f"{k}={params[k]}" for k in ordered)
    raw = (raw + JXZH_SIGN_SECRET).replace(" ", "")
    quoted = urllib.parse.quote(raw, safe='')
    quoted = re.sub(r"[!|'|\(|\)|\~|\*]", lambda m: "%{:02X}".format(ord(m.group(0))), quoted)
    return hashlib.sha1(quoted.encode('utf-8')).hexdigest().lower()


# ==================== 加密通信层（ECDH + AES-GCM） ====================
JXZH_DYNAMIC_KEY_PATH = "/dynamic_key/getServerPublicKey.do"   # 密钥协商
JXZH_GET_REQUEST_ID = "/task/task/getRequestId.do"             # 取 request_id
JXZH_VERIFY_REQUEST_ID = "/task/task/verifyRequestId.do"       # 验证广告观看（前置）


def gen_keypair():
    """生成 P256 密钥对，返回 (priv_obj, pub_uncompressed_bytes(65))。"""
    priv = ec.generate_private_key(SECP256R1())
    pub = priv.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return priv, pub


def ecdh_shared_secret(priv, server_pub_uncompressed: bytes) -> bytes:
    """返回 32 字节 X 坐标共享密钥。"""
    srv = ec.EllipticCurvePublicKey.from_encoded_point(SECP256R1(), server_pub_uncompressed)
    return priv.exchange(ECDH(), srv)


def hkdf_expand(prk: bytes, info: bytes, length: int = 32) -> bytes:
    """HKDF-Expand：prk = HMAC-SHA256(salt, ikm)；逐块 T(n)=HMAC(prk, T(n-1)||info||n)。"""
    t = b""
    out = b""
    i = 1
    while len(out) < length:
        t = hmac.new(prk, t + info + bytes([i]), hashlib.sha256).digest()
        out += t
        i += 1
    return out[:length]


def derive_session_key(client_priv, server_pub_uncompressed: bytes, salt: bytes,
                       device: str, token: str) -> bytes:
    shared = ecdh_shared_secret(client_priv, server_pub_uncompressed)
    info = json.dumps({"device": device, "token": token}, separators=(",", ":")).encode("utf-8")
    return hkdf_expand(hmac.new(salt, shared, hashlib.sha256).digest(), info, 32)


def aesgcm_encrypt(session_key: bytes, payload: dict) -> str:
    """加密业务参数为 X-Param 字符串。"""
    nonce = os.urandom(12)
    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ct = AESGCM(session_key).encrypt(nonce, plaintext, None)
    return base64.b64encode(nonce + ct).decode("ascii")


def aesgcm_decrypt(session_key: bytes, data_b64: str) -> dict:
    """解密响应 data 字段为 dict。"""
    raw = base64.b64decode(data_b64)
    nonce = raw[:12]
    ct = raw[12:]
    plain = AESGCM(session_key).decrypt(nonce, ct, None)
    return json.loads(plain.decode("utf-8"))


def random32() -> str:
    """X-Request-Id：32 位字母数字随机串。"""
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(alphabet[ord(os.urandom(1)) % 62] for _ in range(32))


# ==================== AutoCookieManager ====================
class AutoCookieManager:
    """通过应用宝网关 /wxapp/getCode 获取微信 code，再走 get_openid.do 换取登录态 ck（token）。"""
    def __init__(self, wx_server: str = None, fixed_proxy: str = ""):
        self.wx_server = (wx_server or JXZH_WX_SERVER).strip().rstrip("/")
        self.session = requests.Session()
        self.session.verify = False
        # 复用外部传入的固定代理（每账号只取一次，避免重复获取）
        self._fixed_proxy = parse_fixed_proxy(fixed_proxy) if fixed_proxy else None

    def _get_wx_code(self, wxid: str, appid: str = None, max_retries: int = 3) -> Optional[str]:
        """通过 POST /wxapp/getCode 获取微信 code

        请求体: {"app_id": appid, "ref": wxid/openid}
        成功响应: {"code":0,"msg":"success","data":{"openid":"...","result":{"code":"...","errMsg":"login:ok"}}}
        """
        if not self.wx_server:
            _log_global("❌ 未配置 wx_server_url，无法请求 /wxapp/getCode")
            return None
        target_appid = appid or JXZH_WX_APPID
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
                    if isinstance(data, dict):
                        nested_code = data.get("code")
                        if nested_code not in (None, "", 0):
                            _log_global(f"🔑 获取code成功: {str(nested_code)[:10]}***")
                            return str(nested_code)

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
        """调用 get_openid.do 用 code 换取登录态 ck（token）。

        请求体(form): code=&current_time=&os=miniProgram&deviceabout=miniProgram&version=&miniprogram_os=Windows&key=
        成功响应: {"code":1,"message":"  ","data":{"token","mobile","userID","openid","unionid",...}}
        """
        try:
            url = f"{JXZH_BASE}{JXZH_LOGIN_PATH}"
            body = {
                "code": code,
                "current_time": str(int(time.time() * 1000)),
                "os": "miniProgram",
                "deviceabout": "miniProgram",
                "version": JXZH_VERSION,
                "miniprogram_os": "Android",
            }
            body["key"] = generate_key(body)
            headers = {
                "Host": "api.quwayouxuan.com",
                "User-Agent": (
                    "Mozilla/5.0 (Linux; Android 12; M2007J17C Build/SKQ1.211006.001; wv) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/150.0.7871.189 "
                    "Mobile Safari/537.36 XWEB/1500117 MMWEBSDK/20260604 MMWEBID/8139 "
                    "MicroMessenger/8.0.77.3160(0x28004D35) WeChat/arm64 Weixin NetType/WIFI "
                    "Language/zh_CN ABI/arm64 MiniProgramEnv/android"
                ),
                "xweb_xhr": "1",
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Program": "Android",
                "Accept": "*/*",
                "Referer": f"https://servicewechat.com/{JXZH_WX_APPID}/138/page-frame.html",
            }
            r = self.session.post(url, data=body, headers=headers, timeout=25,
                                  proxies=self._fixed_proxy or None)
            j = r.json()
            # 该接口 code==1 表示成功
            if j.get("code") == 1 and j.get("data") and j["data"].get("token"):
                return j["data"]
            _log_global(f"⚠️ get_openid.do 返回非预期: {str(j)[:160]}")
            return None
        except Exception as e:
            _log_global(f"❌ get_openid.do 异常: {str(e)[:100]}")
            return None

    def get_token_for_wxid(self, wxid: str) -> Optional[Dict]:
        """通过 /wxapp/getCode 拿到 code 后，走 get_openid.do 换取登录态 ck。"""
        code = self._get_wx_code(wxid, JXZH_WX_APPID)
        if not code:
            return None

        result = self._auth_by_code(code)
        if not result:
            _log_global(f"❌ {wxid[:10]}*** get_openid 换 ck 失败")
            return None

        token = result.get("token", "")
        openid = result.get("openid", "")
        mobile = result.get("mobile", "")
        user_id = result.get("userID", "")
        nickname = result.get("username", "")
        if not token:
            _log_global(f"❌ {wxid[:10]}*** get_openid 未返回 token")
            return None

        masked_mobile = mobile[:3] + "****" + mobile[7:] if len(mobile) >= 7 else mobile
        _log_global(f"✅ 登录成功, token:{token[:16]}...")
        return {
            "token": token,
            "openid": openid,
            "mobile": mobile,
            "userId": user_id,
            "unionid": result.get("unionid", ""),
            "nickname": nickname,
        }

    def get_tokens_for_wxids(self, wxids: List[str] = None) -> Dict[str, Dict]:
        if not wxids:
            wxids = parse_env_accounts(JXZH_OPENIDS)

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


# ==================== HTTP 客户端（含 ECDH + AES-GCM 加密通信） ====================
class JXZHHttpClient:
    def __init__(self, token: str, openid: str, fixed_proxy: str = ""):
        self.session = requests.Session()
        self.session.verify = False
        self.proxy_display = '无代理'
        self._setup_proxy(fixed_proxy)
        self.token = token
        self.openid = openid
        self.session_key = None          # ECDH 协商后的会话密钥（None 表示尚未握手）
        self.priv = None                 # 本地 ECDH 私钥
        self.headers = {
            'Host': 'api.quwayouxuan.com',
            'User-Agent': (
                "Mozilla/5.0 (Linux; Android 12; M2007J17C Build/SKQ1.211006.001; wv) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/150.0.7871.189 "
                "Mobile Safari/537.36 XWEB/1500117 MMWEBSDK/20260604 MMWEBID/8139 "
                "MicroMessenger/8.0.77.3160(0x28004D35) WeChat/arm64 Weixin NetType/WIFI "
                "Language/zh_CN ABI/arm64 MiniProgramEnv/android"
            ),
            'xweb_xhr': '1',
            'Content-Type': 'application/x-www-form-urlencoded',
            'X-Program': 'Android',
            'Accept': '*/*',
            'Referer': f'https://servicewechat.com/{JXZH_WX_APPID}/138/page-frame.html',
        }

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

    def _base_params(self, data: Optional[Dict] = None) -> Dict[str, str]:
        """业务接口统一基础参数（不含 key）。"""
        body = dict(data or {})
        body.setdefault("token", self.token)
        body.setdefault("current_time", str(int(time.time() * 1000)))
        body.setdefault("os", "miniProgram")
        body.setdefault("deviceabout", "miniProgram")
        body.setdefault("version", JXZH_VERSION)
        body.setdefault("miniprogram_os", "Android")
        return body

    # ---------- ECDH 密钥协商 ----------
    def ensure_session_key(self) -> bool:
        """执行一次 ECDH 握手获取 sessionKey；已存在则直接返回。"""
        if self.session_key is not None:
            return True
        try:
            self.priv, client_pub = gen_keypair()
            salt = os.urandom(32)
            timestamp = str(int(time.time()))
            payload = {
                "clientPublicKey": urllib.parse.quote(base64.b64encode(client_pub).decode()),
                "timestamp": timestamp,
                "salt": base64.b64encode(salt).decode(),
                "device": self.openid,
                "token": self.token,
            }
            payload = self._base_params(payload)
            payload["key"] = generate_key(payload)

            headers = dict(self.headers)
            headers["X-Request-Id"] = random32()
            url = f"{JXZH_BASE}{JXZH_DYNAMIC_KEY_PATH}"
            r = self.session.post(url, data=payload, headers=headers, timeout=PROXY_TIMEOUT)
            j = r.json()
            if j.get("code") != 1 or not (j.get("data") or {}).get("publicKey"):
                _log_global(f"❌ ECDH 握手失败：{str(j)[:160]}")
                return False
            server_pub = base64.b64decode(j["data"]["publicKey"])
            self.session_key = derive_session_key(self.priv, server_pub, salt, self.openid, self.token)
            return True
        except Exception as e:
            _log_global(f"❌ ECDH 异常：{str(e)[:100]}")
            return False

    # ---------- 加密请求 ----------
    def secure_request(self, path: str, data: Optional[Dict] = None) -> Optional[Dict]:
        """握手后的业务请求：body={}，参数 AES-GCM 加密进 X-Param，响应 data 解密。"""
        if not self.ensure_session_key():
            return None
        url = f"{JXZH_BASE}{path}"
        # 合并基础参数与业务参数，并补 key 签名
        params = self._base_params(data)
        params["key"] = generate_key(params)
        x_param = aesgcm_encrypt(self.session_key, params)

        headers = dict(self.headers)
        headers.pop("Content-Type", None)
        headers.update({
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Token": self.token,
            "X-Request-Id": random32(),
            "X-Device-Id": self.openid,
            "X-Param": x_param,
        })
        try:
            r = self.session.post(url, data="{}", headers=headers, timeout=PROXY_TIMEOUT)
            j = r.json()
            # 响应 data 为密文时需解密
            if isinstance(j, dict) and j.get("data") and isinstance(j["data"], str) and self.session_key:
                try:
                    j["data"] = aesgcm_decrypt(self.session_key, j["data"])
                except Exception:
                    pass
            return j
        except Exception as e:
            _log_global(f"❌ secure_request 异常 {path}：{str(e)[:80]}")
            return None

    def request(self, path: str, data: Optional[Dict] = None, method: str = "POST") -> Optional[Dict]:
        """兼容旧调用：任务类接口统一走加密通道。"""
        return self.secure_request(path, data)


# ==================== 日常任务执行器 ====================
# 任务相关端点
JXZH_TASK_LIST = "/task/task/taskList.do"          # 任务列表 / 校验登录态 + 积分
JXZH_TASK_SUCCESS = "/task/task/taskSuccrss.do"    # 完成任务（签到 taskid=1 / 看视频 taskid=40）
JXZH_SIGN_LIST = "/task/api/signList.do"           # 查询日历签到状态（今日是否已签）
SIGN_TASK_ID = "1"                                 # 每日看视频签到任务 id（运行时 taskList 确认: name=每日看视频签到 id=1）
SIGN_TASK_ID_EXTRA = "40"                          # 每日获取更多积分任务 id（name=每日获取更多积分 id=40）


class DailyTaskExecutor:
    def __init__(self, http: JXZHHttpClient, logger: Logger):
        self.http = http
        self.logger = logger
        self._before_points: int = 0

    def check_login(self) -> Tuple[bool, int]:
        """用任务列表接口验证登录态并取当前积分。返回 (登录有效, 当前积分)。"""
        resp = self.http.secure_request(JXZH_TASK_LIST, {"source": "5"})
        if resp and resp.get("code") == 1 and resp.get("data", {}).get("userinfo"):
            pts = resp.get("data", {}).get("userinfo", {}).get("points", "0")
            self.logger.raw(f"💰 当前积分：{pts}")
            return True, int(pts) if str(pts).isdigit() else 0
        self.logger.warning(f"[登录态校验] 失败：{str(resp)[:120]}")
        return False, 0

    def _get_request_id(self) -> Optional[str]:
        """取本次任务操作的 request_id（真实调用传空参数）。"""
        resp = self.http.secure_request(JXZH_GET_REQUEST_ID, {})
        if resp and resp.get("code") == 1 and resp.get("data"):
            d = resp["data"]
            rid = d.get("request_id") or d.get("requestId") or d.get("id")
            if rid:
                return str(rid)
        self.logger.warning(f"[request_id] 获取失败：{str(resp)[:120]}")
        return None

    def _do_sign(self, task: Dict[str, Any]) -> Tuple[bool, str, int]:
        """每日看视频签到专属流程（taskid=1）。

        与 _do_task 同构（getRequestId → verify(1/2) → taskSuccrss），
        但成功回执文案独立取自 data.drawTip（如"积分已发放至积分账户"），
        不影响「每日获取更多积分」的通用文案。
        """
        task_id = str(task.get("id") or task.get("taskid") or SIGN_TASK_ID)
        subtask_id = str(task.get("subtask_id") or "0")
        ad_tag = "[每日看视频签到] "
        rid = self._get_request_id()
        if not rid:
            return False, "无 request_id", 0
        ad_wait = int(os.environ.get("JXZH_AD_WAIT", "0"))
        if ad_wait <= 0:
            ad_wait = random.randint(30, 60)
        self.http.secure_request(
            JXZH_VERIFY_REQUEST_ID,
            {"type": 1, "request_id": rid, "status": 1},
        )
        self.logger.raw(f"📺 {ad_tag}已弹出，开始模拟观看广告…")
        self.logger.raw(f"⏳ {ad_tag}模拟观看中，等待 {ad_wait} 秒 …")
        time.sleep(ad_wait)
        self.logger.raw(f"✅ {ad_tag}模拟观看 {ad_wait} 秒结束，提交看完状态")
        self.http.secure_request(
            JXZH_VERIFY_REQUEST_ID,
            {"type": 1, "request_id": rid, "status": 2},
        )
        time.sleep(0.3)
        resp = self.http.secure_request(
            JXZH_TASK_SUCCESS,
            {"taskid": task_id, "subtask_id": subtask_id, "request_id": rid, "source": 5},
        )
        if not resp:
            return False, "无响应", 0
        code = resp.get("code")
        data = resp.get("data") or {}
        msg = resp.get("message", "完成")
        pts = (data.get("rice") or data.get("points") or str(data.get("draw", "")).lstrip("+") or 60) if isinstance(data, dict) else 60
        p = int(pts) if str(pts).isdigit() else 60
        if code == 1:
            tip = (data.get("drawTip") if isinstance(data, dict) else None) or msg
            return True, tip, p
        if any(k in msg for k in ["已", "完成", "上限", "已达", "超出", "不能重复", "已领取"]):
            return False, msg, 0
        if "验证错误" in msg or "请稍后" in msg:
            return False, f"{msg}（可尝试增大 JXZH_AD_WAIT 或稍后重试）", 0
        return False, msg, 0

    def _do_task(self, task: Dict[str, Any], task_name: str = "") -> Tuple[bool, str, int]:
        """调用 taskSuccrss.do 完成指定任务，返回 (是否计入成功, 提示信息, 获得积分)。

        真实流程（基于服务端接口的状态机）：
          1. getRequestId.do 取本次 request_id（空参，返回 data.request_id）
          2. 广告弹出成功后 verifyRequestId.do({type:1,request_id,status:1})
          3. 广告完整看完 → verifyRequestId.do({...status:2})
             然后 300ms 后 taskSuccrss.do({taskid,subtask_id,request_id,source:5})
        task 来自 taskList，含动态 id / subtask_id。

        实测验证：
        微信激励视频广告无服务端回调（开发者仅凭客户端 isEnded 发奖），
        服务端唯一防刷手段是 verify(2)-verify(1) 的时长校验（真机 30~33s）。
        间隔 30s 时 taskSuccrss 返回 code:1 +60积分；间隔 2s 时返回 100003 请稍后请求。
        """
        task_id = str(task.get("id") or task.get("taskid") or SIGN_TASK_ID)
        subtask_id = str(task.get("subtask_id") or "0")
        ad_tag = f"[{task_name}] " if task_name else ""
        rid = self._get_request_id()
        if not rid:
            return False, "无 request_id", 0
        # 对齐真机状态机：status:1（广告弹出）→ 等待广告时长 → status:2（完整看完）
        ad_wait = int(os.environ.get("JXZH_AD_WAIT", "0"))  # 如果显式指定则使用固定值
        if ad_wait <= 0:
            ad_wait = random.randint(30, 60)  # 随机 30-60s，模拟真实观看时长
        self.http.secure_request(
            JXZH_VERIFY_REQUEST_ID,
            {"type": 1, "request_id": rid, "status": 1},
        )
        self.logger.raw(f"📺 {ad_tag}已弹出，开始模拟观看广告…")
        self.logger.raw(f"⏳ {ad_tag}模拟观看中，等待 {ad_wait} 秒 …")
        time.sleep(ad_wait)
        self.logger.raw(f"✅ {ad_tag}模拟观看 {ad_wait} 秒结束，提交看完状态")
        self.http.secure_request(
            JXZH_VERIFY_REQUEST_ID,
            {"type": 1, "request_id": rid, "status": 2},
        )
        time.sleep(0.3)  # 真机 onClose 后 300ms 提交 taskSuccrss
        resp = self.http.secure_request(
            JXZH_TASK_SUCCESS,
            {"taskid": task_id, "subtask_id": subtask_id, "request_id": rid, "source": 5},
        )
        if not resp:
            return False, "无响应", 0
        code = resp.get("code")
        msg = resp.get("message", "完成")
        data = resp.get("data") or {}
        pts = (data.get("rice") or data.get("points") or str(data.get("draw", "")).lstrip("+") or 60) if isinstance(data, dict) else 60
        p = int(pts) if str(pts).isdigit() else 60
        if code == 1:
            return True, msg, p
        # 广告类任务常见业务校验失败文案
        if any(k in msg for k in ["已", "完成", "上限", "已达", "超出", "不能重复", "已领取"]):
            return False, msg, 0
        # 验证错误 / 请稍后请求：通常为观看时长不足（增大 JXZH_AD_WAIT）或服务端频控
        if "验证错误" in msg or "请稍后" in msg:
            return False, f"{msg}（可尝试增大 JXZH_AD_WAIT 或稍后重试）", 0
        return False, msg, 0

    def find_sign_task(self) -> Optional[Dict[str, Any]]:
        """从 taskList 动态识别"每日看视频签到"任务，返回含 id/subtask_id 的任务对象。

        任务标识（运行时 taskList 确认）：
          - 「每日看视频签到」name=每日看视频签到, id=1, skiptype=1
          - 「每日获取更多积分」name=每日获取更多积分, id=40, skiptype=1
        taskSuccrss 的 taskid 即任务对象的 id，subtask_id 取 t.subtask_id。
        """
        resp = self.http.secure_request(JXZH_TASK_LIST, {"source": "5"})
        if not resp or resp.get("code") != 1:
            self.logger.warning(f"[任务列表] 获取失败：{str(resp)[:120]}")
            return None
        data = resp.get("data") or {}
        # 任务列表结构：data.tasklist.day（每日）/ data.tasklist.new（新手）
        tasklist = data.get("tasklist") or {}
        tasks: List[Dict] = []
        if isinstance(tasklist, dict):
            for key in ("day", "new"):
                sub = tasklist.get(key) or []
                if isinstance(sub, list):
                    tasks.extend([t for t in sub if isinstance(t, dict)])
        if not tasks:
            # 兜底：直接取扁平列表
            for k in ("list", "tasks"):
                sub = data.get(k) or []
                if isinstance(sub, list):
                    tasks.extend([t for t in sub if isinstance(t, dict)])
        # 按 name 关键词精确匹配（每日看视频签到=id1，每日获取更多积分=id40）
        for t in tasks:
            name = str(t.get("name") or t.get("title") or "")
            if "看视频签到" in name:
                return t
        # 次选：直接按 id 匹配
        for t in tasks:
            if str(t.get("id")) == SIGN_TASK_ID:
                return t
        if tasks:
            self.logger.warning(f"[任务列表] 未匹配签到任务，任务总数={len(tasks)}，将用默认 id={SIGN_TASK_ID}")
        return None

    def find_task_by_id(self, task_id: str) -> Optional[Dict[str, Any]]:
        """从 taskList 按 id 精确取任务对象（用于做额外的看视频任务，如 id=40）。"""
        resp = self.http.secure_request(JXZH_TASK_LIST, {"source": "5"})
        if not resp or resp.get("code") != 1:
            return None
        data = resp.get("data") or {}
        tasklist = data.get("tasklist") or {}
        for key in ("day", "new"):
            sub = (tasklist.get(key) or []) if isinstance(tasklist, dict) else []
            if isinstance(sub, list):
                for t in sub:
                    if isinstance(t, dict) and str(t.get("id")) == task_id:
                        return t
        return None

    def check_sign_status(self) -> Optional[bool]:
        """查询今日是否已签到。返回 True=已签, False=未签, None=查询失败。

        以 taskList.do（任务列表）的签到任务状态为准：
          - 每日看视频签到任务（id=1）的 status 字段 "2"=已签、"0"=未签，
            statusTxt 对应 "已签到"/"签到"（实测确认）。
        signList.do（签到日历）的 sign_status 语义不稳定（实测出现已签却显示 4 的情况），
        故仅作 taskList 查询失败时的兜底参考，不作为主判定。
        """
        resp = self.http.secure_request(JXZH_TASK_LIST, {"source": "5"})
        if resp and resp.get("code") == 1:
            task = self.find_sign_task()
            if task:
                st = str(task.get("status") or "")
                # status=="2" 或 statusTxt 含"已签到" 即视为已签
                if st == "2" or "已签到" in str(task.get("statusTxt") or ""):
                    return True
                if st == "0" or "签到" in str(task.get("statusTxt") or ""):
                    return False
                # 其他未知状态，继续走下方兜底
        # 兜底：signList 日历（仅当 taskList 不可用）
        sl = self.http.secure_request(JXZH_SIGN_LIST, {})
        if sl and sl.get("code") == 1:
            date_list = (sl.get("data") or {}).get("date_list") or []
            today = datetime.now().strftime("%Y-%m-%d")
            for item in date_list:
                if item.get("date") == today:
                    return item.get("sign_status") == 2
        return None

    def sign_in(self) -> Optional[int]:
        """每日看视频签到（taskid 动态取自 taskList，确认 id=1）。

        完成逻辑：id=1 的 skiprule.type="ad"，
        id=40 的 skiprule.type="ad_for_points" —— 两者本质都是「观看激励视频广告」任务，
        走完全相同的流程：getRequestId → getAd().show() → verifyRequestId(status:1/2) → taskSuccrss。
        服务端仅校验 verify(1)→verify(2) 观看时长（真机 30~60s，脚本等 JXZH_AD_WAIT 秒即可），
        微信广告无服务端回调（官方文档实证），故脚本可正常完成签到与领积分。

        固定执行「每日获取更多积分」(id=40) 任务（看视频广告，每次 +60 积分，
        循环次数取服务端剩余次数 last_count）。
        """
        st = self.check_sign_status()
        if st is True:
            self.logger.raw("📅 [每日看视频签到] 今日已签到")
        else:
            task = self.find_sign_task()
            if not task:
                self.logger.warning("📅 [每日看视频签到] 未找到签到任务，使用默认 id")
                task = {"id": SIGN_TASK_ID, "subtask_id": "0"}
            self.logger.raw("📺 [每日看视频签到] 今日未签到，开始执行签到...")
            ok, msg, pts = self._do_sign(task)
            if ok:
                self.logger.raw(f"📅 [每日看视频签到] 签到成功：+{pts}积分（{msg}）")
            else:
                self.logger.raw(f"📅 [每日看视频签到] {msg}")

        # 固定执行「每日获取更多积分」(id=40) —— 看视频广告，循环按服务端剩余次数封顶
        self.do_extra_sign()

    def do_extra_sign(self) -> None:
        """做「每日获取更多积分」(id=40)：观看视频广告领 60 积分，次数取服务端剩余次数 last_count。

        该任务走标准三端点流程（与签到同构）：
          getRequestId.do → verifyRequestId.do({type:1,request_id,status:2}) → taskSuccrss.do({taskid:40,...})
        每轮独立取 request_id 并各自提交；当服务端返回"已达上限/已完成"类文案时提前停止。

        注意：该任务 skiprule.type=ad_for_points（广告类），但服务端仅校验 verify(1)→verify(2)
        的观看时长（见 _do_task 注释），脚本等待 JXZH_AD_WAIT 秒即可正常领取积分。
        """
        extra = self.find_task_by_id(SIGN_TASK_ID_EXTRA)
        if not extra:
            self.logger.warning("📺 [每日获取更多积分] 未找到该任务(id=40)")
            return
        # 读取服务端返回的"今日剩余次数"（taskList 解密后 last_count 字段）
        try:
            rem = int(extra.get("last_count") or 0)
        except (ValueError, TypeError):
            rem = 0
        self.logger.raw(f"📺 [每日获取更多积分] 今日剩余可看次数：{rem}")
        # 起点积分由 run() 在签到前记录（self._before_points），此处不再重复拉取，
        # 以保证「本次 +」能同时计入签到积分与看视频领的积分。
        # 实际循环次数 = 服务端剩余次数（last_count）
        max_times = 0 if rem <= 0 else rem
        if rem <= 0:
            self.logger.raw("📺 [每日获取更多积分] 今日已完成")
        done = 0
        for i in range(1, max_times + 1):
            self.logger.raw(f"📺 [每日获取更多积分] 第 {i}/{rem} 个广告")
            ok, msg, pts = self._do_task(extra, "每日获取更多积分")
            if ok:
                done += 1
                self.logger.raw(f"📺 [每日获取更多积分] 第{done}次成功：+{pts}积分（{msg}）")
                # 防封逻辑：每轮之间严格挂机 160~200 秒（模拟真实观看广告时长）
                if i < max_times:
                    wait = random.randint(160, 200)
                    self.logger.raw(f"🛡️ [防封] 冷却中，{wait} 秒后继续…")
                    time.sleep(wait)
            else:
                # 已达上限/完成/业务拒绝：停止循环
                self.logger.raw(f"📺 [每日获取更多积分] 第{i}次：{msg}（停止）")
                break
        if done:
            self.logger.raw(f"📺 [每日获取更多积分] 本日共完成 {done} 次")
        # 查询执行后积分：重拉任务列表取最新积分
        after_pts = 0
        try:
            resp = self.http.secure_request(JXZH_TASK_LIST, {"source": "5"})
            if resp and resp.get("code") == 1:
                ui = (resp.get("data") or {}).get("userinfo") or {}
                p = ui.get("points", "0")
                after_pts = int(p) if str(p).isdigit() else 0
        except Exception:
            after_pts = 0
        gained = after_pts - (self._before_points or after_pts)
        self.logger.raw(f"💰 执行后积分：{after_pts}")
        self.logger.raw(f"💰 总积分: {after_pts}（本次 +{gained}）")
        return after_pts

    def run(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {"login_ok": False, "points": 0,
                                  "sign_ok": False}
        login_ok, pts = self.check_login()
        result["login_ok"] = login_ok
        result["points"] = pts
        if not login_ok:
            return result

        # 记录执行前（签到前）积分，作为本次增量的起点，确保签到+看视频积分都计入
        self._before_points = pts
        result["points_before"] = pts

        self.sign_in()
        result["sign_ok"] = True

        # 执行结束后重拉最终积分，计算本次总增量（签到 + 看视频领积分）
        try:
            resp = self.http.secure_request(JXZH_TASK_LIST, {"source": "5"})
            if resp and resp.get("code") == 1:
                ui = (resp.get("data") or {}).get("userinfo") or {}
                p = ui.get("points", "0")
                final_pts = int(p) if str(p).isdigit() else 0
                result["points"] = final_pts
        except Exception:
            pass
        return result


# ==================== 核心处理器 ====================
def run_account(account_info: Dict[str, Any], index: int, proxy_str: str = "") -> Dict[str, Any]:
    logger = Logger()
    token = account_info.get("token", "")
    openid = account_info.get("openid", "")
    mobile = account_info.get("mobile", "")

    http = JXZHHttpClient(token, openid, fixed_proxy=proxy_str)

    masked = mobile[:3] + "****" + mobile[7:] if len(mobile) >= 7 else mobile

    nickname = account_info.get("nickname", "")
    result = {'success': True, 'phone': masked, 'index': index, 'daily': {}, 'nickname': nickname}

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
        account = (r.get("phone") or r.get("nickname") or "未知账号")
        lines.extend([
            f"{account_icon} 【账号{idx}】{account}",
            f"{'✅' if ok else '❌'} 状态：{'执行成功' if ok else '执行失败'}",
        ])
        if ok:
            daily = r.get("daily") or {}
            if daily.get("login_ok"):
                after = daily.get("points", 0)
                before = daily.get("points_before", after)
                gained = after - before
                lines.append(f"💰 总积分: {after}（本次 +{gained}）")
        else:
            lines.append(f"⚠️ 原因：{r.get('error') or '登录失效'}")

    lines.append(f"======🎉 完成 {success} / 共 {total} 账号=======")
    print("\n[执行报表]\n" + "\n".join(lines))


def main():
    wxids = parse_env_accounts(JXZH_OPENIDS)
    print("==============================")
    print("📦 匠心忠华小程序自动任务")
    print(f"📱 共配置 {len(wxids)} 个账号")
    print("==============================")

    if not JXZH_WX_SERVER:
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
            _log_global("   请检查该微信是否在线、是否已授权匠心忠华小程序")
            results.append({'success': False, 'phone': mask_account(wxid),
                            'error': '登录失败', 'index': index, 'daily': {}})
            if index < len(wxids):
                time.sleep(2)
            continue

        nickname = info.get("nickname", "")
        _log_global(f"👤 用户：{nickname}")

        result = run_account(info, index, _proxy_str)
        if not result.get('success'):
            result.setdefault('error', result.get('phone') or '登录失效')
        results.append(result)
        if index < len(wxids):
            time.sleep(2)

    if not results:
        print("❌ 未获取到在线匠心忠华账号，请检查 wx_server_url / jxzh_openid")
        return 1

    dispatch_summary(Logger(), results)
    total_failed = sum(1 for r in results if not r.get("success"))
    return 0 if total_failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
