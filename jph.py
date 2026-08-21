"""
Author: anonymous
Date: 2026.08.22
Description: 君品荟小程序签到
Cron: 0 9,12,21 * * *
----------------------------------------------------------------------------------------------
君品荟小程序签到 v1.0.0

功能：自动执行君品荟小程序每日签到日常任务，支持多账号执行。

配置说明：
1. 微信 code 网关：（适配应用宝协议，ck 自动获取）
   wx_server_url                                   必填，自建授权服务器地址
   - 示例：http://127.0.0.1:8000
   - 脚本会自动拼接 /wxapp/getCode
   - 请求格式：POST {网关}/wxapp/getCode
   - 请求体：{"app_id": "<小程序appid>", "ref": "账号openid"}

2. 账号变量：
   jph_openid                                      推荐，君品荟专属账号变量
   - 多账号支持使用 &、英文逗号、中文逗号或换行分隔
   - 示例：openid_a&openid_b 或 openid_a,openid_b

3. 代理变量（可选，适配品赞代理）：
   proxy_api_url                                   品赞代理 API 地址，开启后每个账号自动获取代理
   - 代理接口返回格式支持：纯 IP:PORT，或带账号密码的 IP:PORT ACCOUNT PASSWORD（品赞格式）
   - 单账号固定代理：在账号后追加 #proxy=IP:PORT 可指定该账号专用代理

4. 青龙任务建议：
   名称：君品荟小程序签到
   命令：task jph.py
   定时：每天运行 1 - 3 次即可，具体时间自行调整
----------------------------------------------------------------------------------------------
"""

import os
import sys
import time
import json
import base64
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple

# AES-ECB 加密（滑块验证码 pointJson 用），依赖 pycryptodome
try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad as _aes_pad
    _HAS_CRYPTO = True
except Exception:
    _HAS_CRYPTO = False

# 滑块缺口识别 OCR 服务
# 把滑块图+背景图 POST /capcode → 返回 {result: 缺口x像素}，本地不识图，依赖该服务
OCR_SERVER = (os.environ.get("OCR_SERVER") or "http://ocr.fj.us.ci").rstrip("/")


def _aes_ecb_encrypt(data: str, key: str) -> str:
    """AES-ECB-PKCS7 加密（用于 captcha pointJson），返回 base64"""
    if not _HAS_CRYPTO:
        raise RuntimeError("未安装 pycryptodome，无法加密验证码 pointJson")
    cipher = AES.new(key.encode("utf-8"), AES.MODE_ECB)
    padded = _aes_pad(data.encode("utf-8"), AES.block_size)
    return base64.b64encode(cipher.encrypt(padded)).decode("utf-8")

# Windows 控制台默认 GBK 无法编码 emoji/特殊字符，强制 stdout/stderr 为 UTF-8
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass
try:
    import brotli
    _HAS_BROTLI = True
except Exception:
    _HAS_BROTLI = False
import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

# ==================== 配置区域 ====================
ENABLE_DAILY_TASK = True         # 日常任务

# 统一变量名称与默认值映射
JPH_WX_SERVER = (os.environ.get("wx_server_url") or "").strip().rstrip("/")
JPH_WX_APPID = "wx8d41cdc44c8aeaab"                                                # 君品荟小程序 appid
JPH_VERSION = "1.7"                                                                # 小程序版本号
JPH_OPENIDS = os.environ.get("jph_openid") or ""                                   # 君品荟专属 openid

# 君品荟业务域名
JPH_BASE = "https://fm.exijiu.com"
# 会员/签到业务域名（与 fm 域共用同一会员 token）
# 会员/签到业务域名（与 fm 域共用同一会员 token，签到接口实测在此域）
JPH_MALL_BASE = JPH_BASE
JPH_CHANNEL_CODE = "xj_mall_wx_applet"
# 静默登录换 ck 接口
JPH_LOGIN_PATH = "/api/v2/login/wxMiniSilentLogin"

# 静默登录专用头
JPH_FIXED_HEADERS = {
    "AppID": JPH_WX_APPID,
    # "wechat:wechat_secret" 的 Basic 鉴权
    "Authorization": "Basic d2VjaGF0OndlY2hhdF9zZWNyZXQ=",
    "App-Version": JPH_VERSION,
    "Content-Type": "application/json",
}

# 代理模块：由环境变量 proxy_api_url 驱动
PROXY_API_URL = os.getenv("proxy_api_url", "")
PROXY_TYPE = os.getenv("jph_proxy_type", "http")
PROXY_TIMEOUT = 15
MAX_PROXY_RETRIES = 5
print_lock = __import__("threading").Lock()


class Logger:
    def _log(self, icon: str, msg: str):
        line = f"{icon} {msg}" if icon else msg
        with print_lock:
            print(line, flush=True)

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


# 模块级全局 logger（供类方法引用，如 JPHHttpClient 的 debug 打印）
logger = Logger()


def _log_global(msg: str):
    line = msg
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(line.encode(encoding, errors="ignore").decode(encoding, errors="ignore"), flush=True)


def parse_env_accounts(raw: str) -> List[str]:
    """解析账号列表（支持 &、逗号、换行分隔），并剥离 #proxy= 后缀。"""
    normalized = (raw or "").replace("，", ",").replace(",", "&").replace("\n", "&")
    out = []
    for item in normalized.split("&"):
        item = item.strip()
        if not item:
            continue
        # 剥离单账号固定代理后缀 #proxy=IP:PORT
        if "#proxy=" in item:
            item = item.split("#proxy=")[0].strip()
        if item:
            out.append(item)
    return out


def get_account_proxy(raw_account: str) -> str:
    """从单个账号串中提取 #proxy=IP:PORT 指定的固定代理。"""
    if "#proxy=" in raw_account:
        return raw_account.split("#proxy=", 1)[1].strip()
    return ""


def mask_account(value: str) -> str:
    value = str(value or "")
    if len(value) <= 12:
        return value
    return f"{value[:6]}...{value[-4:]}"


def _decode_response(resp: requests.Response) -> dict:
    """君品荟响应可能为 br 压缩，做一次解码兜底。"""
    raw = resp.content
    text = None
    ce = resp.headers.get("Content-Encoding", "").lower()
    if ce == "br" and _HAS_BROTLI:
        try:
            text = brotli.decompress(raw).decode("utf-8", errors="replace")
        except Exception:
            text = None
    if text is None:
        text = resp.text
    try:
        return json.loads(text)
    except Exception:
        return {"_raw": text[:500]}


# ==================== 代理管理器（环境变量 proxy_api_url；未配置则不走代理） ====================
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
    """通过应用宝网关 /wxapp/getCode 获取微信 code，再走 wxMiniSilentLogin 换取登录态 ck（token）。

    代理策略：_get_wx_code（取微信 code）不走代理；_auth_by_code（登录换 ck）走代理。
    """
    def __init__(self, wx_server: str = None, fixed_proxy: str = ""):
        self.wx_server = (wx_server or JPH_WX_SERVER).strip().rstrip("/")
        self.session = requests.Session()
        self.session.verify = False
        # 复用外部传入的固定代理（每账号只取一次，避免重复获取）
        self._fixed_proxy = parse_fixed_proxy(fixed_proxy) if fixed_proxy else None

    def _get_wx_code(self, wxid: str, appid: str = None, max_retries: int = 3) -> Optional[str]:
        """通过 POST /wxapp/getCode 获取微信 code（不走代理）

        请求体: {"app_id": appid, "ref": wxid/openid}
        成功响应: {"code":0,"msg":"success","data":{"openid":"...","result":{"code":"...","errMsg":"login:ok"}}}
        """
        if not self.wx_server:
            _log_global("❌ 未配置 wx_server_url，无法请求 /wxapp/getCode")
            return None
        target_appid = appid or JPH_WX_APPID
        url = f"{self.wx_server}/wxapp/getCode"

        for attempt in range(max_retries):
            try:
                payload = {"app_id": target_appid, "ref": wxid}
                headers = {
                    "Content-Type": "application/json",
                    "User-Agent": "Mozilla/5.0 MicroMessenger/8.0.50",
                }

                # 取 code 不使用代理，直连网关
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

                # 兜底：遍历其它常见字段
                code = ""
                data = j.get("Data") or j.get("data") or {}
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
        """调用 wxMiniSilentLogin 用 code 换取登录态 ck（token，走代理）。

        请求体(json): {"code":"..."}
        成功响应: {"code":"10000","success":true,"data":{"token","openId","unionId","phone",...}}
        """
        try:
            url = f"{JPH_BASE}{JPH_LOGIN_PATH}"
            body = {"code": code}
            headers = dict(JPH_FIXED_HEADERS)
            headers["X-Access-Token"] = ""
            r = self.session.post(url, json=body, headers=headers, timeout=25,
                                  proxies=self._fixed_proxy or None)
            j = _decode_response(r)
            # 该接口 success==true 且 data.token 存在表示成功
            if j.get("success") and j.get("data") and j["data"].get("token"):
                return j["data"]
            _log_global(f"⚠️ wxMiniSilentLogin 返回非预期: {str(j)[:160]}")
            return None
        except Exception as e:
            _log_global(f"❌ wxMiniSilentLogin 异常: {str(e)[:100]}")
            return None

    def get_token_for_wxid(self, wxid: str) -> Optional[Dict]:
        """通过 /wxapp/getCode 拿到 code 后，走 wxMiniSilentLogin 换取登录态 ck。"""
        code = self._get_wx_code(wxid, JPH_WX_APPID)
        if not code:
            return None

        result = self._auth_by_code(code)
        if not result:
            _log_global(f"❌ {wxid[:10]}*** wxMiniSilentLogin 换 ck 失败")
            return None

        token = result.get("token", "")
        openid = result.get("openId", "")
        mobile = result.get("phone", "")
        user_id = result.get("userId", "")
        unionid = result.get("unionId", "")
        nickname = result.get("nickname", "") or mobile
        if not token:
            _log_global(f"❌ {wxid[:10]}*** wxMiniSilentLogin 未返回 token")
            return None

        masked_mobile = mobile[:3] + "****" + mobile[7:] if len(mobile) >= 7 else mobile
        _log_global(f"✅ 登录成功, token:{token[:16]}...")
        return {
            "code": code,
            "token": token,
            "openid": openid,
            "mobile": mobile,
            "userId": user_id,
            "unionid": unionid,
            "nickname": nickname,
        }

    def get_tokens_for_wxids(self, wxids: List[str] = None) -> Dict[str, Dict]:
        if not wxids:
            wxids = parse_env_accounts(JPH_OPENIDS)

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
class JPHHttpClient:
    def __init__(self, token: str, fixed_proxy: str = ""):
        self.session = requests.Session()
        self.session.verify = False
        self.proxy_display = '无代理'
        self._setup_proxy(fixed_proxy)
        self.token = token
        self.headers = dict(JPH_FIXED_HEADERS)
        self.headers["X-Access-Token"] = token

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

    def get(self, path: str, **kw):
        return self.session.get(JPH_BASE + path, headers=self.headers, timeout=15, **kw)

    def post(self, path: str, json_body: dict = None, **kw):
        return self.session.post(JPH_BASE + path, json=json_body or {},
                                 headers=self.headers, timeout=15, **kw)

    # ==================== 签到====================
    def _mall_headers(self) -> dict:
        """签到/会员域请求头（fm 域登录态用 X-Access-Token 鉴权）。"""
        return {
            "AppID": JPH_WX_APPID,
            "X-Access-Token": self.token,
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://servicewechat.com/wx8d41cdc44c8aeaab/",
        }

    def sign_check_today(self, sign_date: str = None) -> dict:
        """检查今日是否已签到。POST /api/customer/daily/checkTodaySignIn
        body 为 {}（空对象），不可传 signDateStr。
        """
        url = f"{JPH_MALL_BASE}/api/customer/daily/checkTodaySignIn"
        r = self.session.post(url, json={},
                               headers=self._mall_headers(), timeout=15)
        try:
            return r.json()
        except Exception:
            return {"_raw": r.text[:300]}

    def sign_get_captcha(self) -> dict:
        """获取滑块验证码 POST /api/captcha/get（body captchaType=blockPuzzle）

        返回 data.repData：{ jigsawImageBase64, originalImageBase64, secretKey, token }
        """
        url = f"{JPH_MALL_BASE}/api/captcha/get"
        r = self.session.post(url, json={"captchaType": "blockPuzzle"},
                              headers=self._mall_headers(), timeout=15)
        try:
            return r.json()
        except Exception:
            return {"_raw": r.text[:300]}

    def _ocr_slider_x(self, jigsaw_b64: str, original_b64: str) -> Optional[int]:
        """调用 OCR 服务识别滑块缺口 x 坐标"""
        if not OCR_SERVER:
            return None
        try:
            resp = requests.post(
                f"{OCR_SERVER}/capcode",
                json={"slidingImage": jigsaw_b64, "backImage": original_b64},
                headers={"Content-Type": "application/json"},
                timeout=20,
            )
            data = resp.json()
            x = data.get("result")
            return int(x) if x is not None else None
        except Exception:
            return None

    def sign_check_captcha(self, captcha_token: str, point: dict, secret_key: str) -> dict:
        """校验滑块验证码。POST /api/captcha/check

        point = {"x":..., "y":...}，用服务端 secretKey 做 AES-ECB 加密。
        """
        point_str = json.dumps(point, separators=(",", ":"))
        data = {
            "captchaType": "blockPuzzle",
            "token": captcha_token,
            "pointJson": _aes_ecb_encrypt(point_str, secret_key),
        }
        url = f"{JPH_MALL_BASE}/api/captcha/check"
        r = self.session.post(url, json=data, headers=self._mall_headers(), timeout=15)
        try:
            return r.json()
        except Exception:
            return {"_raw": r.text[:300]}

    def query_user_info(self) -> dict:
        """查询用户信息 POST /api/customer/queryById/token

        body={"channel":"h5"}，
        响应 data.nickname / data.headUrl / data.phone / data.sex 等。
        """
        url = f"{JPH_BASE}/api/customer/queryById/token"
        r = self.session.post(url, json={"channel": "h5"},
                              headers=self._mall_headers(), timeout=15)
        try:
            return r.json()
        except Exception:
            return {"_raw": r.text[:300]}

    def query_points(self) -> dict:
        """查询当前积分。POST /api/customer/accoutInter/token

        body={"checkLevelExist":true}，
        响应 data.points 为当前可用积分（字符串）。
        """
        url = f"{JPH_MALL_BASE}/api/customer/accoutInter/token"
        r = self.session.post(url, json={"checkLevelExist": True},
                              headers=self._mall_headers(), timeout=15)
        try:
            return r.json()
        except Exception:
            return {"_raw": r.text[:300]}

    def sign_do(self, code: str, sign_date: str = None) -> dict:
        """执行签到"""
        if not sign_date:
            sign_date = datetime.now().strftime("%Y-%m-%d")
        url = f"{JPH_MALL_BASE}/api/customer/daily/fillSignIn"
        data = {
            "code": code,
            "channelCode": JPH_CHANNEL_CODE,
            "signInDate": sign_date,
        }
        r = self.session.post(url, json=data, headers=self._mall_headers(), timeout=15)
        try:
            return r.json()
        except Exception:
            return {"_raw": r.text[:300]}


# ==================== 缓存 ====================
def load_cache() -> dict:
    cache_file = "jph_ck_cache.json"
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_cache(cache: dict) -> None:
    cache_file = "jph_ck_cache.json"
    try:
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        _log_global(f"❌ 缓存写入失败: {str(e)[:80]}")


# ==================== 账号运行 ====================
def run_account(account_info: dict, index: int, proxy_str: str = "", wxid: str = "") -> dict:
    token = account_info.get("token", "")
    mobile = account_info.get("mobile", "")
    nickname = account_info.get("nickname", "")

    masked = mask_account(mobile) if mobile else mask_account(token)
    result = {"success": True, "mobile": masked, "index": index, "nickname": nickname}

    if ENABLE_DAILY_TASK:
        try:
            http = JPHHttpClient(token, fixed_proxy=proxy_str)
            result["login_ok"] = True

            # ---- 查询用户信息，打印昵称 ----
            try:
                user_info = http.query_user_info()
                user_data = user_info.get("data") or {}
                nick = user_data.get("nickname") or ""
                if nick:
                    nickname = nick
                    result["nickname"] = nick
                    logger.raw(f"👤 用户：{nick}")
                else:
                    logger.warning(f"账号{index} 未获取到昵称")
            except Exception as e:
                logger.warning(f"账号{index} 查询用户信息异常: {str(e)[:80]}")

            # ---- 签到----
            logger.raw(f"🎯 开始执行日常任务")

            # ---- 查询当前积分 ----
            try:
                pts = http.query_points()
                pts_data = pts.get("data") or {}
                cur_pts = pts_data.get("points") or ""
                if cur_pts:
                    logger.raw(f"💰 当前积分：{cur_pts}")
                    result["points_before"] = cur_pts
                else:
                    logger.warning(f"未获取到当前积分")
            except Exception as e:
                logger.warning(f"查询积分异常: {str(e)[:80]}")
            try:
                status = http.sign_check_today()
                signed = status.get("data") is True
                if signed:
                    result["sign_ok"] = True
                    logger.raw(f"📅 [每日签到] 今日已签到，跳过")
                else:
                    # 滑块验证码：OCR 自动识别缺口，失败也继续签到
                    try:
                        cap = http.sign_get_captcha()
                        rep_data = (cap.get("data") or {}).get("repData") or {}
                        if rep_data:
                            slider_img = rep_data.get("jigsawImageBase64")
                            back_img = rep_data.get("originalImageBase64")
                            secret_key = rep_data.get("secretKey")
                            cap_token = rep_data.get("token")
                            if slider_img and back_img and secret_key and cap_token:
                                predicted_x = http._ocr_slider_x(slider_img, back_img) or 50
                                predicted_y = 5
                                logger.raw(f"📅 [每日签到] 滑块缺口坐标 x={predicted_x}, y={predicted_y}")
                                chk = http.sign_check_captcha(
                                    cap_token, {"x": predicted_x, "y": predicted_y}, secret_key
                                )
                                # 滑块验证通过标志在 data.repData.result（对齐 captcha/check 真实响应）
                                chk_data = chk.get("data") or {}
                                passed = bool((chk_data.get("repData") or {}).get("result")) or bool(chk_data.get("success"))
                                if passed:
                                    logger.raw(f"📅 [每日签到] 滑块验证通过")
                                else:
                                    logger.raw(f"📅 [每日签到] 滑块验证未通过，继续尝试签到")
                            else:
                                logger.raw(f"📅 [每日签到] 未获取到验证码参数，跳过滑块")
                        else:
                            logger.raw(f"📅 [每日签到] 未获取到验证码，跳过滑块")
                    except Exception as e:
                        logger.raw(f"📅 [每日签到] 滑块处理异常，继续签到: {str(e)[:80]}")

                    # 无论滑块是否通过都执行签到
                    code = account_info.get("code", "")
                    ret = http.sign_do(code)
                    if ret.get("success") or str(ret.get("code", "")) in ("10000", "0", "200"):
                        result["sign_ok"] = True
                        # 提取签到详情（fillSignIn 响应）
                        sign_data = ret.get("data") or {}
                        extra = (sign_data.get("extraMap") or {}).get("extra") or {}
                        cont_days = (sign_data.get("extraMap") or {}).get("continuousSignDays")
                        point = sign_data.get("pointValue")
                        result_type = sign_data.get("resultType")
                        # 展示：连续签到天数 / 重复签到
                        if result_type == 4:
                            logger.raw(f"📅 [每日签到] 今日已签到（重复），跳过")
                        else:
                            if cont_days is not None:
                                logger.raw(f"📅 [每日签到] 签到成功，连续签到 {cont_days} 天")
                            else:
                                logger.raw(f"📅 [每日签到] 签到成功")
                    else:
                        msg = ret.get("message") or ret.get("msg") or str(ret)[:120]
                        logger.raw(f"📅 [每日签到] 签到未成功: {msg}")
            except Exception as e:
                logger.raw(f"📅 [每日签到] 签到异常: {str(e)[:100]}")

            # ---- 签到后查询积分，对比变化 ----
            try:
                # 签到成功时服务端可能延迟加分，等 1.5s 再查
                if result.get("sign_ok"):
                    time.sleep(1.5)
                pts2 = http.query_points()
                pts2_data = pts2.get("data") or {}
                cur_pts2 = pts2_data.get("points") or ""
                if cur_pts2:
                    result["points_after"] = cur_pts2
                    before = result.get("points_before")
                    if before is not None:
                        try:
                            diff = int(cur_pts2) - int(before)
                            logger.raw(f"💰 执行后积分：{cur_pts2}")
                            logger.raw(f"💰 总积分: {cur_pts2}（本次 {'+' if diff >= 0 else ''}{diff}）")
                        except (ValueError, TypeError):
                            logger.raw(f"💰 执行后积分：{cur_pts2}")
                            logger.raw(f"💰 总积分: {cur_pts2}")
                    else:
                        logger.raw(f"💰 执行后积分：{cur_pts2}")
                        logger.raw(f"💰 总积分: {cur_pts2}")
            except Exception as e:
                logger.warning(f"查询执行后积分异常: {str(e)[:80]}")
        except Exception as e:
            logger.error(f"账号{index} 日常任务异常: {str(e)[:100]}")
            result["success"] = False
    else:
        result["login_ok"] = True

    return result


# ==================== 汇总 ====================
def dispatch_summary(logger: Logger, results: list) -> None:
    total = len(results)
    success = sum(1 for r in results if r.get("success"))
    lines = [
        "==============================",
        f"🕒 执行时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"📊 统计数据：成功 {success} / 总计 {total}",
    ]
    for idx, r in enumerate(results, 1):
        ok = bool(r.get("success"))
        mobile = r.get("mobile") or ""
        masked = (mobile[:3] + "****" + mobile[7:]) if len(mobile) >= 7 else (mobile or "未知")
        nick = r.get("nickname") or ""
        pts_after = r.get("points_after") or ""
        pts_before = r.get("points_before")
        lines.append(f"👤 【账号{idx}】{masked}")
        lines.append(f"✅ 状态：{'执行成功' if ok else '执行失败'}")
        if pts_after:
            if pts_before is not None:
                try:
                    diff = int(pts_after) - int(pts_before)
                    lines.append(f"💰 总积分: {pts_after}（本次 {'+' if diff >= 0 else ''}{diff}）")
                except (ValueError, TypeError):
                    lines.append(f"💰 总积分: {pts_after}")
            else:
                lines.append(f"💰 总积分: {pts_after}")
    lines.append(f"======🎉 完成 {success} / 共 {total} 账号=======")
    _log_global("\n".join(lines))


# ==================== 主流程 ====================
def main():
    wxids = parse_env_accounts(JPH_OPENIDS)
    print("==============================")
    print("📦 君品荟小程序签到")
    print(f"📱 共配置 {len(wxids)} 个账号")
    print("==============================")

    if not JPH_WX_SERVER:
        print("❌ 未配置 wx_server_url（应用宝网关地址），无法获取微信 code")
        return 1
    if not wxids:
        print("❌ 未配置 jph_openid，请在环境变量中填写君品荟账号 openid")
        return 1

    # 缓存读取
    cache = load_cache()
    results = []
    # 保留原始账号串（含 #proxy= 后缀）以便解析单账号代理
    raw_accounts = [a.strip() for a in (JPH_OPENIDS or "").replace("，", ",").replace("\n", ",").split(",") if a.strip()]
    raw_accounts = [a for a in raw_accounts]  # & 也作为分隔
    raw_all = (JPH_OPENIDS or "").replace("，", "&").replace(",", "&").replace("\n", "&")
    raw_accounts = [a.strip() for a in raw_all.split("&") if a.strip()]

    for index, raw_account in enumerate(raw_accounts, 1):
        wxid = parse_env_accounts(raw_account)[0] if parse_env_accounts(raw_account) else raw_account
        _log_global(f">>> 账号 {index}/{len(raw_accounts)} : {mask_account(wxid)}")

        # 每账号只取一次代理：优先单账号 #proxy= 固定代理，否则用 API 代理
        _proxy_dict = None
        _fixed = get_account_proxy(raw_account)
        if _fixed:
            _proxy_dict = parse_fixed_proxy(_fixed)
            _disp = _fixed.split('@')[-1] if '@' in _fixed else _fixed
            _log_global(f"🌐 代理: 启用专用代理 ***@{_disp}")
        else:
            _proxy_dict = proxy_manager.get_proxy()
            if _proxy_dict:
                _disp = list(_proxy_dict.values())[0].split('@')[-1] if '@' in list(_proxy_dict.values())[0] else list(_proxy_dict.values())[0]
                _log_global(f"🌐 代理: 启用 API 代理 ***@{_disp}")

        _proxy_str = ""
        if _proxy_dict:
            _proxy_str = list(_proxy_dict.values())[0]

        mgr = AutoCookieManager(fixed_proxy=_proxy_str)
        info = mgr.get_token_for_wxid(wxid)
        if not info:
            _log_global(f"❌ 账号[{index}] {mask_account(wxid)} 获取登录态失败")
            results.append({"success": False, "mobile": mask_account(wxid),
                            "error": "登录失败", "index": index})
            if index < len(raw_accounts):
                time.sleep(2)
            continue

        # 写缓存
        cache[wxid] = {
            "token": info["token"],
            "openId": info.get("openid"),
            "unionId": info.get("unionid"),
            "mobile": info.get("mobile"),
            "ts": int(time.time()),
        }
        save_cache(cache)

        results.append(run_account(info, index, _proxy_str, wxid))
        if index < len(raw_accounts):
            time.sleep(2)

    if not results:
        print("❌ 未获取到君品荟登录态，请检查 wx_server_url / jph_openid")
        return 1

    dispatch_summary(Logger(), results)
    total_failed = sum(1 for r in results if not r.get("success"))
    return 0 if total_failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
