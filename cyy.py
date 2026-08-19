"""
Author: anonymous
Date: 2026.08.20
Description: 草原云自动阅读code本
Cron: 5 9,12,20 * * *
----------------------------------------------------------------------------------------------
草原云(内蒙古日报) 每日阅读任务 自动完成脚本

功能：自动完成草原云每日签到 + 每日阅读任务。

配置说明：
1. 微信 code 网关：
   wx_server_url                                   必填，自建授权服务器地址
   - 示例：http://127.0.0.1:8000
   - 自动拼接 /wxapp/getCode
   - 请求体：{"app_id": "<小程序appid>", "ref": "账号openid"}

2. 账号变量：
   cyy_openid                                      推荐，草原云专属 openid
   - 多账号支持使用 &、英文逗号、中文逗号或换行分隔
   - 示例：openid_a&openid_b 或 openid_a,openid_b

3. 代理变量（可选，适配品赞代理）：
   proxy_api_url                                   品赞代理 API 地址
   - 代理接口返回格式支持：纯 IP:PORT，或带账号密码的 IP:PORT ACCOUNT PASSWORD

4. 青龙任务建议：
   名称：草原云自动阅读
   命令：task cyy.py
   定时：每天运行 1 - 3 次即可
----------------------------------------------------------------------------------------------
"""

import os
import re
import sys
import json
import time
import math
import base64
import random
import string
import hashlib
import secrets
import urllib.parse
import tempfile
import datetime
from io import BytesIO
from typing import Dict, List, Optional, Any, Tuple
from threading import Lock

import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

# 滑块验证码可选依赖（pillow/numpy/pycryptodome，缺失时跳过自动破解）
try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad, unpad
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

try:
    from PIL import Image
    import numpy as np
    HAS_PIL = True
except ImportError:
    HAS_PIL = False

# Windows 控制台默认 GBK 无法编码 emoji/特殊字符，强制 stdout/stderr 为 UTF-8
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

# ==================== 配置区域 ====================
ENABLE_DAILY_TASK = True        # 日常任务开关

# 微信 code 网关
CYY_WX_SERVER = (os.environ.get("wx_server_url") or "").strip().rstrip("/")
CYY_WX_APPID = "wxcf78d47051628692"                # 草原云小程序 appid

# 账号列表
CYY_OPENIDS = os.environ.get("cyy_openid") or ""

# iyunxh 业务网关（H5 内核 + 业务 API）
CYY_API_BASE = "https://ya.iyunxh.com"
CYY_STATIC_BASE = "https://static2-ya.iyunxh.com"
CYY_H5_DOMAIN = "https://nmgrb.y-h5.iyunxh.com"
CYY_BASE = "https://cyy.nmgcyy.com.cn"
CYY_APP_ID = "nmgrb"
CYY_ACTIVITY_ID = "11106660"        # 阅读领红包第六期正式版
CYY_MODULE_ID = "41603"             # 每日阅读任务列表 的 module_id（兜底值，实际从接口 m_id 获取）

# 业务头
CYY_USER_AGENT = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                  "AppleWebKit/605.1.15 (KHTML, like Gecko) TMAppName/caoyuanyun "
                  "TMAppName/caoyuanyun tm_language/zh-cn")
CYY_APP_USER_AGENT = "TMProject/4.7.0 (iPhone; iOS 17.0; Scale/3.00)"

# 任务行为控制
CYY_READ_SECONDS = int(os.environ.get("CYY_READ_SECONDS", "3"))
CYY_MAX_TASKS = int(os.environ.get("CYY_MAX_TASKS", "0"))
CYY_NO_SIGNIN = os.environ.get("CYY_NO_SIGNIN", "0") == "1"

# 代理模块
PROXY_API_URL = os.getenv("proxy_api_url", "")
PROXY_TYPE = os.getenv("cyy_proxy_type", "socks5")
PROXY_TIMEOUT = 15
print_lock = Lock()


class AlreadySignedIn(Exception):
    """今日已签到（服务端返回 1007 或 '已签到' 提示）"""
    pass

# 滑块验证码 AES 密钥（config pro，逆向自前端 yundian-slide-captcha）
CAPTCHA_AES_KEY = "7Pf0cfZPHy1L7PS2PfCfP8r2BGi461LG".encode()
CAPTCHA_AES_IV = "8RsVKSCH8mQ4l7cu".encode()


def _pure_aes_cbc_encrypt(plaintext: bytes) -> bytes:
    """纯 python AES-256-CBC（无 pycryptodome 时兜底，兼容 CryptoJS）"""
    SBOX = [
        0x63,0x7c,0x77,0x7b,0xf2,0x6b,0x6f,0xc5,0x30,0x01,0x67,0x2b,0xfe,0xd7,0xab,0x76,
        0xca,0x82,0xc9,0x7d,0xfa,0x59,0x47,0xf0,0xad,0xd4,0xa2,0xaf,0x9c,0xa4,0x72,0xc0,
        0xb7,0xfd,0x93,0x26,0x36,0x3f,0xf7,0xcc,0x34,0xa5,0xe5,0xf1,0x71,0xd8,0x31,0x15,
        0x04,0xc7,0x23,0xc3,0x18,0x96,0x05,0x9a,0x07,0x12,0x80,0xe2,0xeb,0x27,0xb2,0x75,
        0x09,0x83,0x2c,0x1a,0x1b,0x6e,0x5a,0xa0,0x52,0x3b,0xd6,0xb3,0x29,0xe3,0x2f,0x84,
        0x53,0xd1,0x00,0xed,0x20,0xfc,0xb1,0x5b,0x6a,0xcb,0xbe,0x39,0x4a,0x4c,0x58,0xcf,
        0xd0,0xef,0xaa,0xfb,0x43,0x4d,0x33,0x85,0x45,0xf9,0x02,0x7f,0x50,0x3c,0x9f,0xa8,
        0x51,0xa3,0x40,0x8f,0x92,0x9d,0x38,0xf5,0xbc,0xb6,0xda,0x21,0x10,0xff,0xf3,0xd2,
        0xcd,0x0c,0x13,0xec,0x5f,0x97,0x44,0x17,0xc4,0xa7,0x7e,0x3d,0x64,0x5d,0x19,0x73,
        0x60,0x81,0x4f,0xdc,0x22,0x2a,0x90,0x88,0x46,0xee,0xb8,0x14,0xde,0x5e,0x0b,0xdb,
        0xe0,0x32,0x3a,0x0a,0x49,0x06,0x24,0x5c,0xc2,0xd3,0xac,0x62,0x91,0x95,0xe4,0x79,
        0xe7,0xc8,0x37,0x6d,0x8d,0xd5,0x4e,0xa9,0x6c,0x56,0xf4,0xea,0x65,0x7a,0xae,0x08,
        0xba,0x78,0x25,0x2e,0x1c,0xa6,0xb4,0xc6,0xe8,0xdd,0x74,0x1f,0x4b,0xbd,0x8b,0x8a,
        0x70,0x3e,0xb5,0x66,0x48,0x03,0xf6,0x0e,0x61,0x35,0x57,0xb9,0x86,0xc1,0x1d,0x9e,
        0xe1,0xf8,0x98,0x11,0x69,0xd9,0x8e,0x94,0x9b,0x1e,0x87,0xe9,0xce,0x55,0x28,0xdf,
        0x8c,0xa1,0x89,0x0d,0xbf,0xe6,0x42,0x68,0x41,0x99,0x2d,0x0f,0xb0,0x54,0xbb,0x16,
    ]
    RCON = [0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1b, 0x36]

    def _xtime(a):
        a = (a << 1) & 0xFF
        return a ^ 0x1B if a & 0x100 else a

    def _expand_key(key):
        nk, nr = 8, 14
        w = [[key[4*i+j] for j in range(4)] for i in range(nk)]
        for i in range(nk, 4*(nr+1)):
            temp = w[i-1][:]
            if i % nk == 0:
                temp = temp[1:] + temp[:1]
                temp = [SBOX[b] for b in temp]
                temp[0] ^= RCON[i//nk - 1]
            elif nk > 6 and i % nk == 4:
                temp = [SBOX[b] for b in temp]
            w.append([w[i-nk][j] ^ temp[j] for j in range(4)])
        return w

    def _block_encrypt(block, w):
        state = [[block[4*j+i] for j in range(4)] for i in range(4)]
        def ark(r):
            for i in range(4):
                for j in range(4):
                    state[i][j] ^= w[r*4+j][i]
        def sb():
            for i in range(4):
                for j in range(4):
                    state[i][j] = SBOX[state[i][j]]
        def sr():
            for i in range(1, 4):
                state[i] = state[i][i:] + state[i][:i]
        def mc():
            for j in range(4):
                col = [state[i][j] for i in range(4)]
                state[0][j] = _xtime(col[0]) ^ (_xtime(col[1]) ^ col[1]) ^ col[2] ^ col[3]
                state[1][j] = col[0] ^ _xtime(col[1]) ^ (_xtime(col[2]) ^ col[2]) ^ col[3]
                state[2][j] = col[0] ^ col[1] ^ _xtime(col[2]) ^ (_xtime(col[3]) ^ col[3])
                state[3][j] = (_xtime(col[0]) ^ col[0]) ^ col[1] ^ col[2] ^ _xtime(col[3])
        w = _expand_key(CAPTCHA_AES_KEY)
        ark(0)
        for rnd in range(1, 14):
            sb(); sr(); mc(); ark(rnd)
        sb(); sr(); ark(14)
        return b"".join(bytes([state[i][j] for j in range(4)]) for i in range(4))

    pad_len = 16 - (len(plaintext) % 16)
    data = plaintext + bytes([pad_len]) * pad_len
    w = _expand_key(CAPTCHA_AES_KEY)
    out = b""
    iv = CAPTCHA_AES_IV
    for i in range(0, len(data), 16):
        block = bytes(data[i+j] ^ iv[j] for j in range(16))
        enc = _block_encrypt(block, w)
        out += enc
        iv = enc
    return out


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
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
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(msg.encode(encoding, errors="ignore").decode(encoding, errors="ignore"), flush=True)


def parse_env_accounts(raw: str) -> List[str]:
    normalized = (raw or "").replace("，", ",").replace(",", "&").replace("\n", "&")
    return [item.strip() for item in normalized.split("&") if item.strip()]


def mask_account(value: Any) -> str:
    value = str(value or "")
    if len(value) <= 12:
        return value
    return f"{value[:6]}...{value[-4:]}"


def mask_mobile(mobile: str) -> str:
    if not mobile or len(mobile) < 7:
        return mobile or "-"
    return f"{mobile[:3]}****{mobile[7:]}"


# ---------------------------------------------------------------------------
# 签名 / 通用工具
# ---------------------------------------------------------------------------
def md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def guid32() -> str:
    """前端 $u.guid(32,false): 32位随机字符串"""
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    return "".join(random.choice(alphabet) for _ in range(32))


def get_aas_key(appkey: str) -> str:
    """getAASkey(appkey) = md5(偶数位 + 奇数位)"""
    if not appkey:
        return ""
    even = "".join(appkey[i] for i in range(0, len(appkey), 2))
    odd = "".join(appkey[i] for i in range(1, len(appkey), 2))
    return md5(even + odd)


def urlencode_component(value) -> str:
    """前端 urlencode: encodeURIComponent 后把 !'()* 转 %XX, 空格转 +"""
    s = urllib.parse.quote(str(value), safe="~")
    s = s.replace("!", "%21").replace("'", "%27").replace("(", "%28").replace(")", "%29") \
         .replace("*", "%2A").replace("%20", "+")
    return s


def ksort(params: Dict[str, Any]) -> Dict[str, Any]:
    return {k: params[k] for k in sorted(params.keys())}


def obj_to_query(params: Dict[str, Any], leading_qmark: bool = False) -> str:
    """前端 objToQueryParams: a=b&c=d（按 key 升序、urlencode 后的值）"""
    parts = []
    for k in sorted(params.keys()):
        parts.append(k + "=" + urlencode_component(params[k]))
    q = "&".join(parts)
    return ("?" if leading_qmark else "") + q


# ---------------------------------------------------------------------------
# 代理
# ---------------------------------------------------------------------------
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
                    ip_port, account, password = parts
                    proxy_text = f"http://{account}:{password}@{ip_port}"
                if ':' in proxy_text:
                    if not (proxy_text.startswith('http://') or proxy_text.startswith('https://')):
                        proxy_text = f'http://{proxy_text}'
                    display = proxy_text
                    if '@' in proxy_text:
                        seg = proxy_text.split('@')
                        if len(seg) == 2:
                            display = f"http://***:***@{seg[1]}"
                    _log_global(f"✅ 成功获取代理: {display}")
                    return {'http': proxy_text, 'https': proxy_text}
            _log_global(f"❌ 获取代理失败: {response.text[:80]}")
            return None
        except Exception as e:
            _log_global(f"❌ 获取代理异常: {str(e)[:60]}")
            return None


proxy_manager = ProxyManager(PROXY_API_URL)


def parse_fixed_proxy(fixed_proxy: str) -> Optional[Dict[str, str]]:
    if not fixed_proxy:
        return None
    if '://' not in fixed_proxy:
        fixed_proxy = f'{PROXY_TYPE}://{fixed_proxy}'
    return {'http': fixed_proxy, 'https': fixed_proxy}


# ===========================================================================
# AutoCookieManager: 应用宝网关 → Miniapp/login 拿登录态 token
# ===========================================================================
class AutoCookieManager:
    """通过应用宝网关 /wxapp/getCode 获取微信 code，再走 /fcpublic/Miniapp/login 换 token。"""
    def __init__(self, wx_server: str = None, fixed_proxy: str = ""):
        self.wx_server = (wx_server or CYY_WX_SERVER).strip().rstrip("/")
        self.session = requests.Session()
        self.session.verify = False
        self._fixed_proxy = parse_fixed_proxy(fixed_proxy) if fixed_proxy else None

    def _get_wx_code(self, wxid: str, appid: str = None, max_retries: int = 3) -> Optional[str]:
        """POST /wxapp/getCode 获取微信 code
        请求体: {"app_id": appid, "ref": wxid/openid}
        成功: {"code":0,"msg":"success","data":{"openid":"...","result":{"code":"...","errMsg":"login:ok"}}}
        """
        if not self.wx_server:
            _log_global("❌ 未配置 wx_server_url，无法请求 /wxapp/getCode")
            return None
        target_appid = appid or CYY_WX_APPID
        url = f"{self.wx_server}/wxapp/getCode"

        for attempt in range(max_retries):
            try:
                payload = {"app_id": target_appid, "ref": wxid}
                headers = {"Content-Type": "application/json",
                           "User-Agent": "Mozilla/5.0 MicroMessenger/8.0.50"}
                r = self.session.post(url, json=payload, headers=headers, timeout=30)
                j = r.json()

                code = ""
                if j.get("code") == 0:
                    data = j.get("data") or {}
                    if isinstance(data, dict):
                        result = data.get("result") if isinstance(data.get("result"), dict) else {}
                        code = result.get("code") or ""
                        if not code and data.get("code") not in (None, "", 0):
                            code = data.get("code")
                if not code:
                    data = j.get("Data") or j.get("data") or {}
                    if isinstance(data, dict):
                        result = data.get("result") if isinstance(data.get("result"), dict) else {}
                        code = result.get("code") or ""
                        if not code and data.get("code") not in (None, "", 0):
                            code = data.get("code")
                if not code:
                    code = j.get("wx_code") or ""

                if not code:
                    if attempt < max_retries - 1:
                        wait = (attempt + 1) * 3
                        _log_global(f"⚠️ {mask_account(wxid)}: code为空，{wait}s后重试({attempt+1}/{max_retries})")
                        time.sleep(wait)
                        continue
                    _log_global(f"❌ {mask_account(wxid)}: 获取code失败 appid={target_appid} resp={str(j)[:160]}")
                    return None
                _log_global(f"🔑 获取code成功 {str(code)[:10]}***")
                return str(code)
            except Exception as e:
                if attempt < max_retries - 1:
                    wait = (attempt + 1) * 3
                    _log_global(f"⚠️ {mask_account(wxid)}: code异常 {str(e)[:60]}，{wait}s后重试")
                    time.sleep(wait)
                    continue
                _log_global(f"❌ {mask_account(wxid)}: 获取code异常 appid={target_appid} err={str(e)[:80]}")
                return None
        return None

    def _auth_by_code(self, code: str) -> Optional[Dict]:
        """调用 /fcpublic/Miniapp/login 用 code 换取登录态 token (app_user_token)"""
        url = f"{CYY_BASE}/fcpublic/Miniapp/login"
        body = {"code": code, "nickname": "微信用户",
                "headimgurl": ("https://thirdwx.qlogo.cn/mmopen/vi_32/"
                               "POgEwh4mIHO4nibH0KlMECNjjGxQUq24ZEaGT4poC6icRiccVGKSyXwibcPq4BWmiaIGuG1icwxaQX6grC9VemZoJ8rg/132")}
        headers = {
            "Host": "cyy.nmgcyy.com.cn",
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 "
                           "MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI MiniProgramEnv/Windows "
                           "WindowsWechat/WMPF WindowsWechat(0x63090a13) UnifiedPCWindowsWechat(0xf2541c1a) XWEB/25297"),
            "xweb_xhr": "1",
            "Content-Type": "application/json",
            "Accept": "*/*",
            "Referer": f"https://servicewechat.com/{CYY_WX_APPID}/18/page-frame.html",
        }
        try:
            r = self.session.post(url, json=body, headers=headers, timeout=25,
                                  proxies=self._fixed_proxy or None)
            j = r.json()
            if j.get("code") == 200 and isinstance(j.get("data"), dict) and j["data"].get("token"):
                return j["data"]
            _log_global(f"⚠️ Miniapp/login 返回非预期: {str(j)[:160]}")
            return None
        except Exception as e:
            _log_global(f"❌ Miniapp/login 异常: {str(e)[:100]}")
            return None

    def get_token_for_wxid(self, wxid: str) -> Optional[Dict]:
        """通过 /wxapp/getCode 拿到 code 后，走 Miniapp/login 换取登录态 token。"""
        code = self._get_wx_code(wxid, CYY_WX_APPID)
        if not code:
            return None
        data = self._auth_by_code(code)
        if not data:
            return None
        member = data.get("member_info") or {}
        token = data.get("token")
        openid = wxid
        mobile = member.get("mobile") or ""
        user_id = data.get("user_id") or member.get("member_id") or 0
        member_id = member.get("member_id") or 0
        nickname = member.get("member_nickname") or ""
        point = member.get("point") or 0
        _log_global(f"✅ 登录成功, token:{str(token)[:16]}...")
        return {
            "token": token,
            "openid": openid,
            "mobile": mobile,
            "userId": user_id,
            "memberId": member_id,
            "nickname": nickname,
            "point": point,
        }


# ===========================================================================
# 业务客户端：草原云 iyunxh 体系（签到、任务、打卡、阅读模拟）
# ===========================================================================
class CaoyuanClient:
    """草原云 iyunxh 业务客户端：签到、任务、阅读打卡、阅读模拟。"""

    def __init__(self, app_user_token: str, openid: str, fixed_proxy: str = ""):
        self.app_user_token = app_user_token
        self.openid = openid
        self.phone = ""
        self.user_id = 0
        self.member_id = 0
        self.access_token = ""
        self.device_token = ""
        self.tenant = None
        self.access_api_dt = ""
        self.captcha_token = ""       # 滑块验证码通过后的 token
        self.afs_tokenid = ""         # 智能验证 tokenid
        self.session = requests.Session()
        self.session.verify = False
        if fixed_proxy:
            proxy_dict = parse_fixed_proxy(fixed_proxy)
            if proxy_dict:
                self.session.proxies = proxy_dict
        self.common_headers = {
            "User-Agent": CYY_USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh-Hans;q=0.9",
            "Origin": CYY_H5_DOMAIN,
            "Referer": CYY_H5_DOMAIN + "/",
        }

    # ---------------- 租户初始化 ----------------
    def tenant_init(self) -> Dict:
        """GET /api/aosbase/_auth_h5init/{app_id}.json -> 拿 appid/appkey"""
        url = f"{CYY_STATIC_BASE}/api/aosbase/_auth_h5init/{CYY_APP_ID}.json"
        r = self.session.get(url, headers={"Access-T-Id": "2433", "Access-T-Id-In": "2433"},
                             timeout=20)
        try:
            j = r.json()
        except Exception:
            raise RuntimeError("租户初始化响应非 JSON: HTTP %d" % r.status_code)
        if j.get("code") not in ("0", 0):
            raise RuntimeError("租户初始化失败: %s" % j.get("msg"))
        import base64
        raw = base64.b64decode(j["data"]).decode("utf-8")
        self.tenant = json.loads(raw)
        return self.tenant

    # ---------------- 签名 ----------------
    def make_signature(self) -> str:
        t = self.tenant
        aaskey = get_aas_key(t["appkey"])
        nonce = guid32()
        ts = int(time.time() * 1000)
        sig = md5(t["appid"] + nonce + str(ts) + aaskey)
        return f"{t['appid']};{nonce};{ts};{sig}"

    # ---------------- 设备令牌缓存 ----------------
    def _dt_cache_file(self) -> str:
        custom = os.environ.get("CYY_AD_DT_FILE", "")
        if custom:
            cache_dir = custom
        else:
            cache_dir = os.path.join(tempfile.gettempdir(), "cyy_dt")
        try:
            os.makedirs(cache_dir, exist_ok=True)
        except Exception:
            cache_dir = os.path.dirname(os.path.abspath(__file__))
        name = "dt_%s.txt" % (self.phone or str(self.user_id) or "default")
        return os.path.join(cache_dir, name)

    def fetch_device_dt(self, force: bool = False) -> str:
        """GET /api/aosbase/_auth_dt -> token[32:68]，按账号缓存当天有效"""
        if not self.tenant:
            self.tenant_init()
        if not force and self.access_api_dt:
            return self.access_api_dt
        t = self.tenant
        device_id = self.device_token or str(int(time.time() * 1000)) + str(random.randint(10 ** 8, 10 ** 9 - 1))[:9]
        url = CYY_API_BASE + "/api/aosbase/_auth_dt"
        try:
            r = self.session.get(url, headers={
                "Access-T-Id": str(t.get("t_id", 2433)),
                "Access-T-Id-In": str(t.get("t_id_in", 2433)),
                "Access-Api-Unique-Token": "1",
                "Access-Api-Dt": device_id,
                "User-Agent": CYY_USER_AGENT,
            }, timeout=20)
            j = r.json()
        except Exception as e:
            raise RuntimeError("获取设备令牌异常: %s" % e)
        if j.get("code") not in ("0", 0):
            raise RuntimeError("获取设备令牌失败: %s" % j.get("msg"))
        token = j.get("data") or ""
        if len(token) < 68:
            raise RuntimeError("设备令牌异常(长度 %d)" % len(token))
        self.access_api_dt = token[32:68]
        try:
            with open(self._dt_cache_file(), "w", encoding="utf-8") as f:
                f.write(self.access_api_dt)
        except Exception:
            pass
        return self.access_api_dt

    # ---------------- app_user_token → access_token ----------------
    def login_by_app_user_token(self, phone: str, user_id: int = 0,
                                user_name: str = "",
                                portrait_url: str = "/images/default/head.jpg") -> Dict:
        """用 app_user_token 换 iyunxh access_token（与原抓包一致）。"""
        self.tenant_init()
        t = self.tenant
        params = {
            "app_user_token": self.app_user_token,
            "appid": t["appid"],
            "noncestr": guid32(),
            "phone": phone,
            "portrait_url": portrait_url,
            "timestamp": int(time.time()),
            "user_id": user_id,
            "user_name": user_name,
            "wx_openid": "",
            "wx_unionid": "",
        }
        params = ksort(params)
        sign_src = obj_to_query(params, leading_qmark=False) + "&appkey=" + t["appkey"]
        params["signature"] = md5(sign_src)
        url = CYY_API_BASE + "/api/aosbase/_auth_appuserinit"
        # _auth_appuserinit 需带签名头 Access-T-Id / Access-T-Id-In / Access-Api-Signature，
        # 否则服务端返回 code=1007 "没有企业信息"
        headers = {
            "Access-T-Id": str(t.get("t_id", 2433)),
            "Access-T-Id-In": str(t.get("t_id_in", 2433)),
            "Access-Api-Unique-Token": "1",
            "Access-Wxclient-Type": "wx_app",
            "Access-Api-Signature": self.make_signature(),
            "Content-Type": "application/json",
            "User-Agent": CYY_USER_AGENT,
            "Origin": CYY_H5_DOMAIN,
            "Referer": CYY_H5_DOMAIN + "/",
        }
        r = self.session.post(url, json=params, headers=headers, timeout=20)
        try:
            j = r.json()
        except Exception:
            raise RuntimeError("appuserinit 响应非 JSON: HTTP %d" % r.status_code)
        if j.get("code") not in ("0", 0):
            raise RuntimeError("appuserinit 失败: %s" % j.get("msg"))
        data = j.get("data") or {}
        self.access_token = data.get("access_token", "")
        info = data.get("data") or {}
        self.user_id = info.get("user_id") or self.user_id or user_id
        return data

    # ---------------- 业务头 ----------------
    def api_headers(self) -> Dict[str, str]:
        t = self.tenant or {}
        h = {
            "Access-Token": self.access_token,
            "Access-User-Id": str(self.user_id),
            "Access-T-Id": str(t.get("t_id", 2433)),
            "Access-T-Id-In": str(t.get("t_id_in", 2433)),
            "Access-Api-Unique-Token": "1",
            "Access-Wxclient-Type": "wx_app",
            "Access-Api-Signature": self.make_signature(),
            "Access-Api-Dt": self.access_api_dt,
            "Origin": CYY_H5_DOMAIN,
            "Referer": CYY_H5_DOMAIN + "/",
        }
        return h

    # ---------------- 通用请求 ----------------
    def api_get(self, path: str, params: Optional[Dict] = None) -> Any:
        url = CYY_API_BASE + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        r = self.session.get(url, headers=self.api_headers(), timeout=20)
        return self._check(r)

    def api_post(self, path: str, body: Dict) -> Any:
        r = self.session.post(CYY_API_BASE + path, json=body,
                              headers=self.api_headers(), timeout=20)
        return self._check(r)

    def _check(self, r: requests.Response) -> Any:
        try:
            j = r.json()
        except Exception:
            raise RuntimeError("响应非 JSON: HTTP %d, %s" % (r.status_code, r.text[:200]))
        if j.get("code") not in ("0", 0):
            raise RuntimeError("%s: %s" % (j.get("code"), j.get("msg")))
        return j.get("data")

    # ---------------- 签到 ----------------
    def signin(self, activity_id: int = 10609026) -> Dict:
        """POST /api/aossignin/ac_sub"""
        headers = self.api_headers()
        headers["X-Requested-With"] = "com.innermongoliadaily.activity"
        r = self.session.post(CYY_API_BASE + "/api/aossignin/ac_sub", json={
            "id": activity_id, "afs_tokenid": "", "collect_info": "",
            "longitude": 0, "latitude": 0,
        }, headers=headers, timeout=20)
        try:
            j = r.json()
        except Exception:
            raise RuntimeError("签到响应非 JSON: %s" % r.text[:200])
        if j.get("code") in ("0", 0):
            return j.get("data") or {}
        # 1007 等"签到失败"通常是当日已签到（重复签到被服务端拒绝）
        if str(j.get("code")) == "1007" or "已签到" in str(j.get("msg", "")):
            raise AlreadySignedIn("今日已签到")
        raise RuntimeError("签到失败: %s" % (j.get("msg") or j.get("code")))

    def signin_times(self, activity_id: int = 10609026) -> Dict:
        """GET /api/aossignin/user_times"""
        headers = self.api_headers()
        headers["X-Requested-With"] = "com.innermongoliadaily.activity"
        url = CYY_API_BASE + "/api/aossignin/user_times?" + urllib.parse.urlencode(
            {"activity_id": activity_id})
        r = self.session.get(url, headers=headers, timeout=20)
        return self._check(r)

    # ---------------- 任务 / 用户信息 ----------------
    def get_user_info(self) -> Dict:
        return self.api_get("/api/aosbase/user_info")

    def get_gold(self) -> int:
        """全局账户金币（来自 user_info 的 gold 字段，与抓包 user_info_i 的 gold:55 一致）"""
        try:
            user = self.get_user_info()
            return int(user.get("gold") or 0)
        except Exception:
            return 0

    def get_optionp_list(self) -> List[Dict]:
        return self.api_get("/api/aoslearnfoot/_optionp_list",
                            {"activity_id": CYY_ACTIVITY_ID})

    def get_optionp_detail(self, option_id) -> Dict:
        return self.api_get("/api/aoslearnfoot/optionp_detail", {"id": option_id})

    def get_task_list(self, module_id, activity_id, offset: int = 0, count: int = 30) -> List[Dict]:
        return self.api_get("/api/aosbasemodule/_task_list", {
            "offset": offset, "count": count,
            "module_id": module_id, "activity_id": activity_id,
        })

    def task_create(self, task_id) -> Dict:
        return self.api_post("/api/aosbasemodule/task_create", {"task_id": task_id})

    def task_done(self, task_record_id: str, afs_tokenid: str = "", collect_info: str = "") -> Dict:
        return self.api_post("/api/aosbasemodule/task_done", {
            "task_record_id": task_record_id,
            "collect_info": collect_info,
            "afs_tokenid": afs_tokenid,
            "device_token": self.device_token,
        })

    # ---------------- 滑块验证码（自动破解） ----------------
    def solve_captcha_auto(self) -> str:
        """自动滑块验证码求解：返回 afs_tokenid
        逆向自前端 yundian-slide-captcha 组件：
          _captcha_get -> 拼图缺口识别 -> _captcha_check -> intelverifcode_check
        """
        if not HAS_PIL:
            raise RuntimeError("需要 pillow/numpy 才能自动求解滑块(pip install pillow numpy)")

        def _aes_cbc_encrypt(plaintext: bytes) -> bytes:
            if HAS_CRYPTO:
                cipher = AES.new(CAPTCHA_AES_KEY, AES.MODE_CBC, CAPTCHA_AES_IV)
                return cipher.encrypt(pad(plaintext, 16))
            return _pure_aes_cbc_encrypt(plaintext)

        def _aes_encrypt_b64(obj, url_quote: bool = False) -> str:
            text = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            b = base64.b64encode(_aes_cbc_encrypt(text)).decode()
            return urllib.parse.quote(b) if url_quote else b

        def _random_string(n: int) -> str:
            alphabet = "twxyz2345678"
            return "".join(random.choice(alphabet) for _ in range(n))

        def _find_gap(bg_img, block_img):
            """返回缺口左上角 x（自然像素坐标）：NCC 模板匹配 + 亮度低谷"""
            bg = np.asarray(bg_img.convert("RGB"), dtype=np.float64)
            rgba = np.asarray(block_img.convert("RGBA"), dtype=np.float64)
            alpha = rgba[:, :, 3:4] / 255.0
            piece = rgba[:, :, :3]
            bw = rgba.shape[1]
            h, W = bg.shape[0], bg.shape[1]
            scores = []
            for x in range(0, W - bw + 1):
                win = bg[:, x:x + bw]
                m = alpha
                num = (m * piece * win).sum()
                d1 = (m * piece * piece).sum()
                d2 = (m * win * win).sum()
                scores.append(num / math.sqrt(d1 * d2) if d1 > 0 and d2 > 0 else 0.0)
            x_max = int(np.argmax(scores))
            colmean = bg.mean(axis=(0, 2))
            from numpy.lib.stride_tricks import sliding_window_view
            sw = sliding_window_view(colmean, min(bw, 20)).mean(axis=1)
            x_bright = int(np.argmin(sw))
            return x_max, x_bright, scores

        def _build_track(target_x: float, width: int):
            """生成类人手拖拽轨迹：每 100ms 采样，长度 <= 100"""
            track = []
            x = 0.0
            accel = random.uniform(1.4, 1.8)
            decel_start = target_x * 0.72
            while x < target_x - 0.5 and len(track) < 99:
                if x < decel_start:
                    v = accel + random.uniform(-0.2, 0.2)
                    x += v * 1.0
                else:
                    x += max(0.2, accel * (target_x - x) / (target_x - decel_start + 1)) + random.uniform(-0.15, 0.15)
                track.append({"x": round(x, 1), "y": 0, "time": 100})
            if track:
                track[-1]["x"] = target_x
            else:
                track.append({"x": target_x, "y": 0, "time": 100})
            return track

        referer = CYY_H5_DOMAIN + "/module-study/pass-detail/pass-detail"
        now = int(time.time())
        params = {"once": _random_string(10), "referer": referer,
                  "timestamp": now, "type": "1"}
        sig = _aes_encrypt_b64(params, url_quote=True)
        url = CYY_API_BASE + "/api/basemodule/_captcha_get?" + urllib.parse.urlencode({
            "once": params["once"], "referer": referer,
            "timestamp": now, "type": "1", "signature": sig})

        headers = self.api_headers()
        captcha_headers = {
            "Access-Token": headers["Access-Token"],
            "Access-User-Id": headers["Access-User-Id"],
            "Access-T-Id": headers["Access-T-Id"],
            "Access-T-Id-In": headers["Access-T-Id-In"],
            "Access-Api-Unique-Token": "1",
            "Access-Wxclient-Type": "wx_app",
            "Access-Api-Signature": headers["Access-Api-Signature"],
            "Access-Api-Dt": headers["Access-Api-Dt"],
        }

        r = self.session.get(url, headers=captcha_headers, timeout=30)
        j = r.json()
        if j.get("code") not in ("0", 0):
            raise RuntimeError("获取验证码失败: %s" % j)
        data = j["data"]
        token = data["token"]
        bg_url = data["background"]
        blk_url = data["block"]

        bg = Image.open(BytesIO(self.session.get(bg_url, timeout=30).content))
        blk = Image.open(BytesIO(self.session.get(blk_url, timeout=30).content))

        x_max, x_bright, scores = _find_gap(bg, blk)
        bg_w = bg.width
        width = bg_w

        candidates = [("ncc_max", x_max), ("bright_min", x_bright),
                      ("ncc_min", int(np.argmin(scores)))]
        for name, x in candidates:
            if x + 60 > bg_w - 1:
                continue
            _log_global(f"    [*] 尝试 {name}: x={x}")
            track = _build_track(x, width)
            payload = {"x": x, "width": width, "track": track}
            enc = _aes_encrypt_b64(payload)
            check_body = {"token": token, "data": enc,
                          "referer": referer, "type": "1"}
            try:
                rc = self.session.post(CYY_API_BASE + "/api/basemodule/_captcha_check",
                                       json=check_body, headers=captcha_headers, timeout=30)
                r = rc.json()
            except Exception as e:
                _log_global(f"    [warn] check异常: {e}")
                continue
            if r.get("code") in ("0", 0) and r.get("data", {}).get("result"):
                validate = r["data"]["token"]
                _log_global(f"    [OK] 滑块验证通过, validate={validate[:24]}...")
                iv = self.session.post(CYY_API_BASE + "/api/aosbasemodule/intelverifcode_check",
                                       json={"validate": validate, "verif_type": 3,
                                             "afs_uuid": "", "source": "yundian"},
                                       headers=captcha_headers, timeout=30).json()
                if iv.get("code") in ("0", 0):
                    tokenid = iv["data"]["tokenid"]
                    _log_global(f"    [OK] 智能验证通过, afs_tokenid={tokenid}")
                    return tokenid
                else:
                    _log_global(f"    [warn] intelverifcode_check: {iv}")
            else:
                _log_global(f"    [x] {r.get('msg') or r}")
        raise RuntimeError("全部候选位置验证失败")

    # ---------------- 打卡（cyy 草原云侧） ----------------
    def add_footprint(self, article_id, title: str, news_url: str,
                      article_type: str = "1") -> Dict:
        """POST /fcpublic/Memberfootprint/addfootprint"""
        if not self.app_user_token:
            raise RuntimeError("缺少 app_user_token, 无法打卡")
        param = {"h5url": news_url, "type": 1}
        body = {
            "app_id": "fcinformation", "native": 1,
            "src": "NewsI004WKDetailViewController",
            "paramStr": json.dumps(param, ensure_ascii=False),
            "title": title, "pic": "",
            "member_id": self.member_id, "intro": " ",
            "article_id": article_id, "article_type": article_type,
            "wwwFolder": "wwwFolder",
        }
        ts = int(time.time())
        key = secrets.token_hex(16)
        nonce = secrets.token_hex(8)
        headers = {
            "token": self.app_user_token,
            "User-Agent": "4.7.0 TMProject",
            "tmencrypt": "1",
            "tmencryptkey": key, "tmencryptkeynew": key,
            "tmrandomnum": nonce, "tmrandomnumnew": nonce,
            "tmtimestamp": str(ts), "tmtimestampnew": str(ts),
        }
        r = self.session.post(CYY_BASE + "/fcpublic/Memberfootprint/addfootprint",
                              json=body, headers=headers, timeout=20)
        try:
            j = r.json()
        except Exception:
            raise RuntimeError("打卡响应异常: HTTP %d" % r.status_code)
        if j.get("code") == 500 and "重复" in (j.get("msg") or ""):
            return {"footprint_id": ""}
        if j.get("code") != 200:
            raise RuntimeError("打卡失败: %s" % j.get("msg"))
        return j.get("data") or {}

    def complete_reading(self, article_id) -> Dict:
        """POST /fcpublic/yundian/complateReading"""
        if not self.app_user_token:
            raise RuntimeError("缺少 app_user_token, 无法上报阅读完成")
        body = {"content_id": article_id, "member_id": self.member_id}
        import uuid as _uuid
        headers = {
            "token": self.app_user_token,
            "guid": str(_uuid.uuid4()),
            "User-Agent": "4.7.0 TMProject",
        }
        r = self.session.post(CYY_BASE + "/fcpublic/yundian/complateReading",
                              json=body, headers=headers, timeout=20)
        try:
            j = r.json()
        except Exception:
            raise RuntimeError("上报阅读完成响应异常: HTTP %d" % r.status_code)
        if j.get("code") != 200:
            raise RuntimeError("上报阅读完成失败: %s" % (j.get("msg") or j))
        return j.get("data") or {}

    # ---------------- 阅读模拟 ----------------
    @staticmethod
    def parse_rule(rule) -> Dict:
        """解析任务 rule -> {news_url, article_id, title, action}"""
        try:
            rule = rule if isinstance(rule, dict) else json.loads(rule or "{}")
        except Exception:
            return {}
        news_url = (rule.get("news_id") or "").strip()
        m = re.search(r"ArticleDetail(\d+)", news_url)
        article_id = int(m.group(1)) if m else 0
        title = (rule.get("content_info") or {}).get("title") or ""
        return {"news_url": news_url, "article_id": article_id,
                "title": title, "action": rule.get("action") or ""}

    def simulate_read(self, rule, read_seconds: int = 0) -> None:
        """访问任务文章 H5 页（触发页面 JS 倒计时上报），再 sleep read_seconds"""
        try:
            rule = rule if isinstance(rule, dict) else json.loads(rule)
        except Exception:
            return
        news_id = (rule.get("news_id") or "").strip()
        action = rule.get("action") or ""
        m = re.search(r"second=(\d+)", news_id)
        need = read_seconds or random.randint(60, 120)
        if news_id and news_id.startswith("http"):
            try:
                base = news_id.split("#")[0]
                fragment = news_id.split("#")[1] if "#" in news_id else ""
                if fragment:
                    frag = re.sub(r"(ArticleDetail/\d+)/(undefined|\d+)",
                                  r"\1/%s" % self.member_id, fragment)
                    if "fc_token=" not in frag:
                        sep = "&" if "?" in frag else "?"
                        frag += f"{sep}fc_token={self.app_user_token or ''}"
                    full_url = base + "#" + frag
                else:
                    full_url = base
                self.session.get(full_url, headers={
                    "User-Agent": CYY_APP_USER_AGENT,
                    "Accept-Language": "zh-Hans-CN;q=1",
                }, timeout=15)
                _log_global(f"  [*] 已打开文章页: {full_url[:120]}")
            except Exception as e:
                _log_global(f"  [warn] 文章页访问失败: {e}")
        _log_global(f"  [*] 阅读文章 {need} 秒 (action={action})...")
        step = 10
        while need > 0:
            time.sleep(min(step, need))
            need -= step
            _log_global(f"  [*] 已阅读, 剩余约 {max(need, 0)} 秒")


# ===========================================================================
# 任务执行器
# ===========================================================================
class DailyTaskExecutor:
    """草原云每日任务执行器：登录态校验 → 签到 → 拉任务 → 阅读打卡 → task_done。"""

    def __init__(self, client: CaoyuanClient, logger: Logger):
        self.client = client
        self.logger = logger

    def check_login(self) -> Tuple[bool, int]:
        """用 user_info 验证登录态，用 module-point/self 查当前活动金币。"""
        try:
            user = self.client.get_user_info()
            self.client.user_id = user.get("user_id") or self.client.user_id
            if not self.client.member_id:
                try:
                    self.client.member_id = int(user.get("ori_user_id") or 0)
                except Exception:
                    self.client.member_id = 0
            pts = self.client.get_gold()
            self.logger.raw(f"💰 当前金币: {pts}")
            return True, pts
        except Exception as e:
            self.logger.warning(f"[登录态校验] 失败: {e}")
            return False, 0

    def _do_signin(self) -> Optional[int]:
        """每日签到（含 user_times 查询）"""
        if CYY_NO_SIGNIN:
            self.logger.raw("⏭️ 已配置 CYY_NO_SIGNIN=1，跳过签到")
            return None
        try:
            times = self.client.signin_times()
            remain = (times or {}).get("day_remain")
            if not remain:
                self.logger.raw("📅 day_remain=0 (可能已签), 尝试签到...")
        except Exception as e:
            self.logger.raw(f"📅 签到次数查询失败(可忽略): {e}")
        try:
            r = self.client.signin()
            self.logger.raw(f"✅ 签到成功! 金币+{r.get('gold')} 积分+{r.get('integral')} "
                            f"(第{r.get('times')}次)")
            return r.get("integral") or 60
        except AlreadySignedIn as e:
            self.logger.raw(f"📅 {e}")
            return 0
        except Exception as e:
            self.logger.raw(f"📅 {e} (已签到则忽略)")
            return None

    def _do_tasks(self) -> int:
        """拉任务 → 阅读打卡 → task_done；返回完成数。"""
        opts = self.client.get_optionp_list()
        if not opts:
            self.logger.raw("⚠️ 当前没有进行中的任务活动")
            return 0
        opt = opts[0]
        opt_id = opt["id"]
        module_id = opt.get("m_id") or CYY_MODULE_ID
        self.logger.raw(f"🎯 任务活动: {opt.get('title')} "
                        f"(option_id={opt_id}, 任务数={opt.get('task_num')})")

        try:
            detail = self.client.get_optionp_detail(opt_id)
            done_n = detail.get("user_done_num") or 0
            undone_n = detail.get("user_undone_num") or 0
            self.logger.raw(f"📊 今日已完成 {done_n}/{done_n + undone_n}")
        except Exception as e:
            self.logger.raw(f"⚠️ 任务详情查询失败(可忽略): {e}")

        try:
            tasks = self.client.get_task_list(module_id, opt_id)
        except Exception as e:
            self.logger.warning(f"[任务列表] 获取失败: {e}")
            return 0
        undone = [t for t in (tasks or []) if not t.get("user_done")]
        self.logger.raw(f"📋 共 {len(tasks or [])} 个任务, 未完成 {len(undone)} 个")
        if not undone:
            self.logger.raw("✅ 今日任务已全部完成, 收工")
            return 0

        limit = CYY_MAX_TASKS or len(undone)
        done_count = 0
        for idx, task in enumerate(undone[:limit], 1):
            task_id = task.get("id")
            title = task.get("title") or ""
            self.logger.raw(f"\n[+] 任务 #{task_id}: {title}")
            try:
                rule = task.get("rule") or {}
                if isinstance(rule, str):
                    rule = json.loads(rule)
            except Exception:
                rule = {}

            # 1. 创建任务
            record_id = ""
            try:
                created = self.client.task_create(task_id)
                record_id = (created or {}).get("task_record_id") or ""
                self.logger.raw(f"  任务记录: {record_id}")
            except Exception as e:
                self.logger.warning(f"  任务创建失败: {e}")
                continue

            # 2. 阅读足迹打卡
            info = self.client.parse_rule(rule)
            if info.get("article_id"):
                try:
                    r = self.client.add_footprint(info["article_id"],
                                                  info.get("title") or title,
                                                  info.get("news_url", ""))
                    self.logger.raw(f"  打卡成功 footprint_id={r.get('footprint_id')}")
                except Exception as e:
                    self.logger.warning(f"  打卡失败: {e}")

            # 3. 模拟阅读
            self.client.simulate_read(rule, CYY_READ_SECONDS)

            # 3.5 上报阅读完成（60秒倒计时信号）
            if info.get("article_id"):
                try:
                    self.client.complete_reading(info["article_id"])
                    self.logger.raw("  阅读完成上报成功")
                except Exception as e:
                    self.logger.warning(f"  阅读完成上报失败: {e}")

            # 4. 完成任务（需要 afs_tokenid 滑块验证；失败时自动破解重试）
            if not record_id:
                self.logger.raw("  [OK] 无任务记录, 阅读打卡完成视为任务完成")
                done_count += 1
                continue
            afs = self.client.afs_tokenid
            try:
                result = self.client.task_done(record_id, afs_tokenid=afs)
                option = result.get("option") or {}
                self.logger.raw(f"  [OK] 完成任务! 奖励: "
                                f"{option.get('goods_title') or option.get('title') or '-'}")
                done_count += 1
            except Exception as e:
                if "验证" in str(e) or "4001" in str(e):
                    self.logger.raw("  [*] 任务完成需滑块验证, 尝试自动破解...")
                    try:
                        tokenid = self.client.solve_captcha_auto()
                    except Exception as ce:
                        self.logger.warning(f"  自动破解失败: {ce}")
                        tokenid = ""
                    if tokenid:
                        self.client.afs_tokenid = tokenid
                        try:
                            result = self.client.task_done(record_id, afs_tokenid=tokenid)
                            option = result.get("option") or {}
                            self.logger.raw(f"  [OK] 破解验证码后完成任务! 奖励: "
                                            f"{option.get('goods_title') or option.get('title') or '-'}")
                            done_count += 1
                        except Exception as e2:
                            self.logger.warning(f"  破解后仍失败: {e2}")
                    else:
                        self.logger.warning("  [x] 自动验证失败, 跳过该任务")
                else:
                    self.logger.warning(f"  任务完成失败: {e}")

        self.logger.raw(f"\n✅ 完成 {done_count}/{min(limit, len(undone))} 个任务")
        return done_count

    def run(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {"login_ok": False, "points": 0, "sign_ok": False,
                                  "tasks_done": 0}
        ok, pts = self.check_login()
        result["login_ok"] = ok
        result["points"] = pts
        if not ok:
            return result
        result["points_before"] = pts

        if self._do_signin() is not None:
            result["sign_ok"] = True

        result["tasks_done"] = self._do_tasks()

        # 重拉金币（全局账户金币，来自 user_info.gold）
        try:
            p = self.client.get_gold()
            if p == 0:
                p = pts
            result["points"] = p
        except Exception:
            pass

        gained = result["points"] - result["points_before"]
        self.logger.raw(f"💰 总金币: {result['points']}（本次 +{gained}）")
        return result


# ===========================================================================
# 核心处理器
# ===========================================================================
def run_account(account_info: Dict[str, Any], index: int, proxy_str: str = "") -> Dict[str, Any]:
    logger = Logger()
    token = account_info.get("token", "")
    openid = account_info.get("openid", "")
    mobile = account_info.get("mobile", "")
    user_id = account_info.get("userId", 0)
    member_id = account_info.get("memberId", 0)
    nickname = account_info.get("nickname", "")
    masked = mask_mobile(mobile)
    result = {'success': True, 'phone': masked, 'index': index,
              'daily': {}, 'nickname': nickname, 'error': ''}

    if not token or not openid:
        result['success'] = False
        result['error'] = '登录态缺失'
        return result

    client = CaoyuanClient(app_user_token=token, openid=openid, fixed_proxy=proxy_str)
    client.member_id = member_id
    client.user_id = user_id
    client.phone = mobile

    try:
        # 用 token 换 iyunxh access_token（阅读体系令牌）
        try:
            client.login_by_app_user_token(phone=mobile or "", user_id=user_id or 0,
                                           user_name=nickname or "")
        except Exception as e:
            _log_global(f"   [warn] app_user_token 换 access_token 失败: {e}")
            result['success'] = False
            result['error'] = f"换 token 失败: {e}"
            return result

        # 设备令牌
        try:
            if not client.access_api_dt:
                today = datetime.date.today().strftime("%m%d")
                cache_file = client._dt_cache_file()
                if os.path.exists(cache_file):
                    with open(cache_file, "r", encoding="utf-8") as f:
                        cached = f.read().strip()
                    if cached and cached.startswith(today) and len(cached) == 36:
                        client.access_api_dt = cached
                if not client.access_api_dt:
                    _log_global("   [*] 获取设备令牌 Access-Api-Dt ...")
                    client.fetch_device_dt()
                    _log_global(f"   Access-Api-Dt: {client.access_api_dt}")
        except Exception as e:
            _log_global(f"   [warn] 获取设备令牌失败: {e}")

        if ENABLE_DAILY_TASK:
            logger.task("开始执行日常任务")
            daily = DailyTaskExecutor(client, logger)
            result['daily'] = daily.run()
        return result
    except Exception as e:
        result['success'] = False
        result['error'] = str(e)[:120]
        return result


def dispatch_summary(results: List[Dict[str, Any]]) -> None:
    total = len(results)
    success = sum(1 for r in results if r.get("success"))
    failed = total - success

    lines = [
        "==============================",
        f"🕒 执行时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"📊 统计数据：成功 {success} / 总计 {total}",
        f"✅ 成功账号：{success} 个",
        f"❌ 失败账号：{failed} 个",
    ]
    for idx, r in enumerate(results, 1):
        ok = bool(r.get("success"))
        account = r.get("phone") or r.get("nickname") or "未知账号"
        lines.append(f"👤 【账号{idx}】{account}")
        lines.append(f"{'✅' if ok else '❌'} 状态：{'执行成功' if ok else '执行失败'}")
        if ok:
            daily = r.get("daily") or {}
            if daily.get("login_ok"):
                after = daily.get("points", 0)
                before = daily.get("points_before", after)
                gained = after - before
                lines.append(f"💰 总金币: {after}（本次 +{gained}）")
        else:
            lines.append(f"⚠️ 原因：{r.get('error') or '登录失效'}")

    lines.append(f"======🎉 完成 {success} / 共 {total} 账号=======")
    print("\n[执行报表]\n" + "\n".join(lines))


def main():
    openids = parse_env_accounts(CYY_OPENIDS)
    print("==============================")
    print("📦 草原云小程序自动阅读")
    print(f"📱 共配置 {len(openids)} 个账号")
    print("==============================")

    if not CYY_WX_SERVER:
        print("❌ 未配置 wx_server_url（应用宝网关地址），无法获取微信 code")
        return 1

    results: List[Dict[str, Any]] = []
    for index, wxid in enumerate(openids, 1):
        _log_global(f">>> 账号 {index}/{len(openids)} : {mask_account(wxid)}")
        _proxy_dict = proxy_manager.get_proxy()
        _proxy_str = ""
        if _proxy_dict:
            _proxy_str = list(_proxy_dict.values())[0]
            _disp = _proxy_str.split('@')[-1] if '@' in _proxy_str else _proxy_str
            _log_global(f"🌐 代理: 启用 ***@{_disp}")

        mgr = AutoCookieManager(fixed_proxy=_proxy_str)
        try:
            info = mgr.get_token_for_wxid(wxid)
        except Exception as exc:
            info = None
            _log_global(f"❌ 账号[{index}] {mask_account(wxid)} 自动获取 token 异常: {str(exc)[:80]}")

        if not (info and info.get("token")):
            _log_global(f"❌ 账号[{index}] {mask_account(wxid)} 自动获取 token 失败")
            _log_global("   请检查该微信是否在线、是否已授权草原云小程序")
            results.append({'success': False, 'phone': mask_account(wxid),
                            'error': '登录失败', 'index': index, 'daily': {}})
            if index < len(openids):
                time.sleep(2)
            continue

        if info.get("nickname"):
            _log_global(f"👤 用户: {info.get('nickname')}")
        if info.get("mobile"):
            _log_global(f"📱 手机号: {mask_mobile(info.get('mobile'))}")

        result = run_account(info, index, _proxy_str)
        if not result.get('success'):
            result.setdefault('error', result.get('phone') or '登录失效')
        results.append(result)

        if index < len(openids):
            time.sleep(2)

    dispatch_summary(results)
    success = sum(1 for r in results if r.get("success"))
    return 0 if success == len(openids) else 1


if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except Exception as e:
        _log_global(f"❌ 程序异常: {e}")
        sys.exit(1)
