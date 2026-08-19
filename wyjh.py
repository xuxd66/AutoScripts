"""
无忧计划 - 自动任务脚本
功能:
  1. 登录
  2. 每日签到
  3. 看广告赚金币 (每天最多7次)
  4. 领取任务奖励

环境变量:
  WY_ACCOUNT: 账号信息，格式 账号#密码#device_id，多账号用&分隔
    device_id 为账号已绑定的设备
    示例: WY_ACCOUNT=13800138000#abc123#1786867167261-qp23rrepcah
  WY_PROXY_API: 代理提取API地址（可选）
    
注册链接:https://dgccvi.com/#/register?ref=6M6UEBG
邀请码：6M6UEBG
"""

import requests
import json
import re
import random
import time
import os
import uuid
import sys
import hmac
import hashlib
from datetime import datetime
from urllib.parse import urlparse

# Windows 控制台默认 GBK 编码，无法输出 emoji，强制 UTF-8
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 关闭 SSL 警告
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "https://api.dgccvi.com"
LOGIN_URL = f"{BASE_URL}/api/app/auth/login"
DAILY_TASKS_URL = f"{BASE_URL}/api/app/daily-tasks"
CHECKIN_URL = f"{BASE_URL}/api/app/checkin"
CHECKIN_CLAIM_URL = f"{BASE_URL}/api/app/daily-tasks/daily_checkin/claim"
ADS_LIST_URL = f"{BASE_URL}/api/app/alliance-ads"
ADS_SESSION_START_URL = f"{BASE_URL}/api/app/alliance-ads/session/start"
ADS_HEARTBEAT_URL = f"{BASE_URL}/api/app/alliance-ads/session/heartbeat"
ADS_COMPLETE_URL = f"{BASE_URL}/api/app/alliance-ads/session/complete"
ME_URL = f"{BASE_URL}/api/app/me"
USER_DEVICES_URL = f"{BASE_URL}/api/app/user-devices"
TASKS_LIST_URL = f"{BASE_URL}/api/app/tasks"

# App 接口鉴权说明（纯 Python 实现）：
# 1) POST /api/app/attest {integrity_token:"", device_id, ts, nonce, native_proof}
#    native_proof = HMAC-SHA256(ATTEST_SECRET, "attest\n{ts}\n{nonce}\n{device_id}")
#    服务端返回 {session_id, session_secret, expires_in:1800}（integrity_token 传空即可）
# 2) 之后每个请求带 X-App-Session/Ts/Nonce/Sign 头：
#    sign = HMAC-SHA256(session_secret, "{METHOD}\n{path}\n{ts}\n{nonce}\n{sha256hex(body)}")
#    path 不含 query；GET/空 body 的 body hash 为 sha256("")
#    body 为 JSON.stringify 紧凑格式（无空格、不转义非ASCII）
ATTEST_SECRET = "aac0ab40d0612c8549f88e87e476751a348f910156e9e73590ddaece2a4288d5"

HEADERS = {
    "accept": "application/json, text/plain, */*",
    "content-type": "application/json",
    "origin": "https://localhost",
    "x-requested-with": "com.dgccvi.app",
    "referer": "https://localhost/",
    "accept-encoding": "gzip, deflate, br, zstd",
    "accept-language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "sec-fetch-site": "cross-site",
    "sec-fetch-mode": "cors",
    "sec-fetch-dest": "empty",
    # WebView Client Hints（App 请求强制携带，缺失会被判定非官方 App）
    "sec-ch-ua-platform": "\"Android\"",
    "sec-ch-ua": "\"Not;A=Brand\";v=\"8\", \"Chromium\";v=\"150\", \"Android WebView\";v=\"150\"",
    "sec-ch-ua-mobile": "?1",
    "priority": "u=1, i",
}

# 常见 Android WebView User-Agent 池，随机切换降低被封风险
USER_AGENTS = [
    "Mozilla/5.0 (Linux; Android 14; SM-S918B Build/UP1A.231005.007; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/120.0.6099.230 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; SM-G991B Build/TP1A.220624.014; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/110.0.5481.153 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 12; Pixel 6 Build/SQ1D.220205.003; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/105.0.5195.136 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 11; Redmi K40 Build/RKQ1.200826.002; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/96.0.4664.104 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 10; V2031A Build/QP1A.190711.020; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/91.0.4472.114 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 9; ASUS_AI2401_A Build/PQ3B.190801.07131748; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/91.0.4472.114 Mobile Safari/537.36",
]


def random_headers():
    """返回带随机 User-Agent 的请求头（每次调用随机切换，防封禁）"""
    headers = dict(HEADERS)
    headers["user-agent"] = random.choice(USER_AGENTS)
    return headers


class ProxyManager:
    """从代理提取 API 获取并维护一个当前代理（参考速看任务实现）"""

    def __init__(self, api_url: str):
        self.api_url = api_url
        self.current_proxy = None

    def _extract_proxy(self, text: str):
        if "://" in text:
            return text.strip()
        match = re.search(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d{1,5})', text)
        return match.group(1) if match else None

    def refresh(self):
        if not self.api_url:
            return None
        try:
            resp = requests.get(self.api_url, timeout=5)
            content = resp.text
            proxy_str = None

            if "socks" in content or "://" in content:
                proxy_str = self._extract_proxy(content)

            if not proxy_str:
                try:
                    data = resp.json()
                    if isinstance(data, dict):
                        if 'data' in data and isinstance(data['data'], list) and data['data']:
                            item = data['data'][0]
                            proxy_str = f"{item.get('ip', item.get('IP'))}:{item.get('port', item.get('PORT'))}"
                        elif 'ip' in data and 'port' in data:
                            proxy_str = f"{data['ip']}:{data['port']}"
                except ValueError:
                    pass

            if not proxy_str:
                proxy_str = self._extract_proxy(content)

            if proxy_str:
                if "://" in proxy_str:
                    self.current_proxy = {'http': proxy_str, 'https': proxy_str}
                else:
                    self.current_proxy = {'http': f'http://{proxy_str}', 'https': f'http://{proxy_str}'}
                return self.current_proxy
            return None
        except Exception:
            return None

# ==================== 广告联盟 ====================


class WuYouPlan:
    APP_VERSION = "1.0.8"
    ATTEST_URL = f"{BASE_URL}/api/app/attest"
    # 会话有效期 1800 秒，预留余量提前 300 秒续期
    SESSION_TTL = 1800 - 300

    def __init__(self, account, password):
        self.account = account
        self.password = password
        self.session = requests.Session()
        self.session.verify = False
        # 初始化随机 User-Agent，后续所有请求复用该会话（含 App 伪装头）
        self.session.headers.update(random_headers())
        # 代理管理
        self.proxy_api = os.environ.get("WY_PROXY_API", "").strip()
        self.proxy_mgr = ProxyManager(self.proxy_api)
        # device_id：统一由 WY_ACCOUNT 的第三段（账号#密码#device_id）提供，
        # 账号已绑定设备时必须用绑定的那台（否则 device_limit）。实例默认留空，
        # 由 main() 解析 WY_ACCOUNT 后回填。
        self.device_id = ""
        # attest 会话（__init__ 时握手）
        self.session_id = ""
        self.session_secret = ""
        self._attest_time = 0
        self.token = None
        self.user_id = None
        self.user_info = None
        self.total_coins_earned = 0
        # 包装 session.request，自动为每个请求注入 x-app-* 签名头
        self._orig_request = self.session.request
        self.session.request = self._request_with_app_headers

    def log(self, msg):
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {msg}")

    # ==================== App 签名（attest 握手 + HMAC-SHA256） ====================

    @staticmethod
    def _hmac_hex(key, msg):
        return hmac.new(key.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()

    def _attest(self):
        """App 完整性握手：拿 session_id + session_secret（服务端不校验 integrity_token）"""
        ts = str(int(time.time()))
        nonce = uuid.uuid4().hex
        native_proof = self._hmac_hex(ATTEST_SECRET, f"attest\n{ts}\n{nonce}\n{self.device_id}")
        payload = {
            "integrity_token": "",
            "device_id": self.device_id,
            "ts": ts,
            "nonce": nonce,
            "native_proof": native_proof,
        }
        resp = self._orig_request("POST", self.ATTEST_URL, headers=random_headers(),
                                  json=payload, proxies=self.proxy_mgr.current_proxy, timeout=15)
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"attest 握手失败: {data}")
        self.session_id = data["session_id"]
        self.session_secret = data["session_secret"]
        self._attest_time = time.time()
        self.log(f"🔐 attest 握手成功 | session={self.session_id[:8]}... | 有效期{data.get('expires_in')}秒")

    def _ensure_attest(self):
        """会话不存在或临近过期时重新握手"""
        if not self.session_secret or (time.time() - self._attest_time) > self.SESSION_TTL:
            self._attest()

    def _sign_headers(self, method, path, body_str):
        """生成 x-app-* 签名头"""
        ts = str(int(time.time()))
        nonce = uuid.uuid4().hex
        body_hash = hashlib.sha256(body_str.encode("utf-8")).hexdigest()
        payload = f"{method.upper()}\n{path}\n{ts}\n{nonce}\n{body_hash}"
        sign = self._hmac_hex(self.session_secret, payload)
        return {
            "x-app-session": self.session_id,
            "x-app-ts": ts,
            "x-app-nonce": nonce,
            "x-app-sign": sign,
        }

    def _request_with_app_headers(self, method, url, **kwargs):
        """requests.Session.request 的包装：统一 body 格式并自动附加 x-app-* 签名头。"""
        self._ensure_attest()
        # 统一 JSON body 为 JSON.stringify 紧凑格式（与 App 端一致），保证 body 与签名自洽
        body_str = ""
        if "json" in kwargs and kwargs["json"] is not None:
            body_str = json.dumps(kwargs.pop("json"), separators=(",", ":"), ensure_ascii=False)
            kwargs["data"] = body_str.encode("utf-8")
        # path 不含 query，去尾部斜杠（对齐 App 端 ts() 逻辑）
        path = urlparse(url).path or "/"
        if len(path) > 1:
            path = path.rstrip("/") or "/"
        # 多数 GET 接口需要 device_id / platform / app_version 查询参数
        if method.upper() == "GET":
            sep = "&" if "?" in url else "?"
            url = url + sep + f"device_id={self.device_id}&platform=android&app_version={self.APP_VERSION}"
        headers = dict(kwargs.get("headers") or {})
        headers.update(self._sign_headers(method, path, body_str))
        kwargs["headers"] = headers
        retried = kwargs.pop("_attest_retried", False)
        resp = self._orig_request(method, url, **kwargs)
        # 对齐 App 端行为：收到 app_required（会话失效/被风控）时重新 attest 并重试一次
        if resp.status_code == 403 and not retried:
            try:
                code = resp.json().get("code", "")
            except Exception:
                code = ""
            if code == "app_required":
                self._attest_time = 0  # 强制过期
                self._ensure_attest()
                headers.update(self._sign_headers(method, path, body_str))
                kwargs["headers"] = headers
                resp = self._orig_request(method, url, **kwargs)
        return resp

    # ==================== 登录 ====================

    def login(self):
        """登录获取 token（需携带完整 App 伪装头，否则服务端返回 app_required）"""
        payload = {
            "account": self.account,
            "password": self.password,
            "device_id": self.device_id,
            "platform": "android",
            "app_version": self.APP_VERSION
        }
        # 签名头由 _request_with_app_headers 自动注入（body 也由其统一为 JSON.stringify 紧凑格式）
        resp = self.session.post(LOGIN_URL, headers=random_headers(), json=payload, proxies=self.proxy_mgr.current_proxy)
        data = resp.json()

        token = data.get("token")
        if token:
            self.token = token
            self.user_info = data.get("user", {})
            self.user_id = self.user_info.get("id")
            self.session.headers.update({
                "authorization": f"Bearer {self.token}"
            })
            self.log(f"✅ 登录成功 | 用户ID: {self.user_id}")
            return True
        else:
            # 兼容部分接口用 error 字段返回错误信息
            err = data.get("error") or data.get("message") or data
            self.log(f"❌ 登录失败: {err}")
            return False

    # ==================== 每日任务 ====================

    def get_user_info(self):
        """查询用户信息（含金币余额）"""
        resp = self.session.get(ME_URL, proxies=self.proxy_mgr.current_proxy)
        data = resp.json()
        return data.get("user", {})

    def get_user_devices(self):
        """查询当前账号绑定的设备列表，并把服务端 device_id 回填到 self.device_id 供广告等请求使用"""
        resp = self.session.get(USER_DEVICES_URL, proxies=self.proxy_mgr.current_proxy)
        data = resp.json()
        devices = data.get("devices", [])
        device_ids = [d.get("device_id", "") for d in devices]
        # 优先取当前设备，否则取第一个，回填到 self.device_id
        chosen = next((d for d in devices if d.get("is_current")), None) or (devices[0] if devices else None)
        if chosen and chosen.get("device_id"):
            self.device_id = chosen.get("device_id")
        self.log(f"📱 查询设备 | device_id: {device_ids}")
        return data

    def get_daily_tasks(self):
        """获取每日任务列表"""
        resp = self.session.get(DAILY_TASKS_URL, proxies=self.proxy_mgr.current_proxy)
        data = resp.json()
        return data

    def show_tasks(self, data):
        """展示任务信息"""
        tasks = data.get("tasks", [])
        today = data.get("today", "")
        pending = data.get("pending_claim", 0)

        self.log(f"📅 日期: {today} | 待领取: {pending}个")
        print("-" * 60)

        total_daily = 0
        total_weekly = 0
        for task in tasks:
            icon = task.get("icon", "📌")
            title = task.get("title", "")
            reward = task.get("reward_coins", 0)
            progress = task.get("current_progress", 0)
            target = task.get("condition_value", 0)
            completed = task.get("is_completed", False)
            claimed = task.get("is_claimed", False)
            period = task.get("period_type", "")
            task_key = task.get("task_key", "")

            if claimed:
                status = "✅ 已领取"
            elif completed:
                status = "🎁 可领取"
            else:
                status = f"⏳ {progress}/{target}"

            print(f"  {icon} {title} | {status} | +{reward}金币 | [{period}] [{task_key}]")

            if period == "daily":
                total_daily += reward
            else:
                total_weekly += reward

        print("-" * 60)
        self.log(f"💰 每日奖励合计: {total_daily}金币 | 每周奖励合计: {total_weekly}金币")

    # ==================== 每日签到 ====================

    def checkin(self):
        """每日签到 + 领取签到奖励"""
        self.log("📅 执行每日签到...")
        resp = self.session.post(CHECKIN_URL, proxies=self.proxy_mgr.current_proxy)
        data = resp.json()
        if data.get("error") == "今天已签到":
            self.log("   ℹ️ 今天已签到，跳过")
            return data
        coins = data.get("coins_awarded", 0)
        day = data.get("day_number", 0)
        msg = data.get("message", "")
        self.total_coins_earned += coins
        self.log(f"   {msg} | 连续第{day}天 | +{coins}金币")

        # 领取签到奖励
        self.log("🎁 领取签到奖励...")
        resp2 = self.session.post(CHECKIN_CLAIM_URL, proxies=self.proxy_mgr.current_proxy)
        data2 = resp2.json()
        if data2.get("ok"):
            claim_coins = data2.get("coins", 0)
            claim_msg = data2.get("message", "")
            self.total_coins_earned += claim_coins
            self.log(f"   {claim_msg} | +{claim_coins}金币")
        elif data2.get("error") == "奖励已领取":
            self.log("   ℹ️ 签到奖励今日已领取，跳过")
        else:
            self.log(f"   ⚠️ 领取签到奖励失败: {data2}")
        return data

    def claim_task(self, task_key):
        """领取任务奖励"""
        url = f"{BASE_URL}/api/app/daily-tasks/{task_key}/claim"
        resp = self.session.post(url, proxies=self.proxy_mgr.current_proxy)
        data = resp.json()
        if data.get("ok"):
            coins = data.get("coins", 0)
            msg = data.get("message", "")
            self.total_coins_earned += coins
            self.log(f"   {msg} | +{coins}金币")
        else:
            self.log(f"   ⚠️ 领取失败 ({task_key}): {data}")
        return data

    # ==================== 广告联盟 ====================

    def get_ads_info(self):
        """查询广告配置（device_id 查询参数由请求包装统一追加）"""
        self.log(f"📡 [广告] 请求配置 | device_id={self.device_id!r}")
        self.log(f"📡 [广告] GET {ADS_LIST_URL}")
        resp = self.session.get(ADS_LIST_URL, proxies=self.proxy_mgr.current_proxy)
        data = resp.json()
        self.log(f"📡 [广告] 配置响应 | enabled={data.get('enabled')} | max_views_per_day={data.get('max_views_per_day')} | items={len(data.get('items', []))}")
        return data

    def start_ad_session(self):
        """开始广告会话"""
        payload = {
            "device_id": self.device_id,
            "client": "app"
        }
        self.log(f"📡 [广告] 开始会话 | device_id={self.device_id!r} | payload={payload}")
        resp = self.session.post(ADS_SESSION_START_URL, json=payload, proxies=self.proxy_mgr.current_proxy)
        data = resp.json()
        self.log(f"📡 [广告] 会话响应 | ok={data.get('ok')} | message={data.get('message')}")
        return data

    def send_heartbeat(self, play_token, progress_seconds):
        """发送心跳（上报观看进度）"""
        payload = {
            "play_token": play_token,
            "progress_seconds": progress_seconds
        }
        resp = self.session.post(ADS_HEARTBEAT_URL, json=payload, proxies=self.proxy_mgr.current_proxy)
        data = resp.json()
        return data

    def complete_ad_session(self, play_token, total_seconds):
        """完成广告观看"""
        payload = {
            "play_token": play_token,
            "progress_seconds": total_seconds
        }
        resp = self.session.post(ADS_COMPLETE_URL, json=payload, proxies=self.proxy_mgr.current_proxy)
        data = resp.json()
        return data

    def watch_ads(self):
        """完整广告观看流程"""
        # 1. 查询广告配置
        self.log(f"📺 开始广告流程 | 当前 device_id={self.device_id!r}")
        self.log("📺 查询广告列表...")
        ads_info = self.get_ads_info()

        enabled = ads_info.get("enabled", False)
        max_views = ads_info.get("max_views_per_day", 7)
        items = ads_info.get("items", [])

        if not enabled:
            self.log("   ⚠️ 广告功能未启用")
            return

        self.log(f"   广告已启用 | 每天最多 {max_views} 次 | 共 {len(items)} 个广告可选")
        self.log(f"   广告请求间隔: {ads_info.get('request_interval_min_seconds', 30)}-{ads_info.get('request_interval_max_seconds', 90)}秒")

        if not items:
            self.log("   今日观看次数已达上限，跳过观看流程")
            return

        # 2. 循环观看广告
        success_count = 0
        fail_count = 0

        for i in range(max_views):
            self.log(f"\n{'─' * 50}")
            self.log(f"📺 第 {i+1}/{max_views} 个广告")

            # 开始会话
            session_data = self.start_ad_session()
            if not session_data.get("ok"):
                msg = session_data.get("message", "启动失败")
                self.log(f"   ❌ 启动广告会话失败: {msg}")
                fail_count += 1
                break

            session = session_data.get("session", {})
            play_token = session.get("play_token")
            duration = session.get("duration_seconds", 30)
            reward = session.get("reward_coins", 0)
            heartbeat_interval = session.get("heartbeat_interval", 30)
            ad_info = session.get("ad", {})

            self.log(f"   📱 {ad_info.get('title', '未知')} | {ad_info.get('description', '')[:20]}")
            self.log(f"   ⏱️ 时长: {duration}秒 | 💰 奖励: {reward}金币")

            # 模拟观看过程（发送心跳）
            elapsed = 0
            heartbeat_count = 0
            while elapsed < duration:
                # 每次心跳间隔随机微调
                step = min(random.uniform(1.0, 3.0), duration - elapsed)
                time.sleep(step)
                elapsed += step
                elapsed = min(elapsed, duration)

                if elapsed < duration:
                    heartbeat_count += 1
                    self.send_heartbeat(play_token, round(elapsed, 2))
                    self.log(f"   💓 心跳 [{heartbeat_count}] | 进度: {round(elapsed, 2)}/{duration}秒")

            # 完成观看
            self.log(f"   🏁 完成观看，领取奖励...")
            complete_data = self.complete_ad_session(play_token, round(duration, 2))

            if complete_data.get("ok"):
                coins = complete_data.get("gold_coins", 0)
                msg = complete_data.get("message", "")
                self.total_coins_earned += coins
                success_count += 1
                self.log(f"   ✅ {msg} | +{coins}金币 | 累计: {self.total_coins_earned}金币")
            else:
                err_msg = complete_data.get("message", "领取失败")
                self.log(f"   ❌ {err_msg}")
                fail_count += 1

            # 等待下次广告请求间隔
            if i < max_views - 1:
                interval = complete_data.get("request_interval_seconds", random.randint(30, 90))
                next_available = complete_data.get("next_request_available_in", interval)
                self.log(f"   ⏳ 等待 {next_available} 秒后请求下一个广告...")
                time.sleep(next_available)

        self.log(f"\n{'─' * 50}")
        self.log(f"📊 广告观看汇总: 成功 {success_count} 次 | 失败 {fail_count} 次 | 今日累计 +{self.total_coins_earned}金币")

    # ==================== 主流程 ====================

    def run(self):
        """主入口"""
        self.log(f"🚀 无忧计划 - 账号: {self.account}")

        # 加载代理（配置了代理API时）
        if self.proxy_api:
            self.proxy_mgr.refresh()

        if not self.login():
            return

        # 0. 查询当前金币余额
        user = self.get_user_info()
        nickname = user.get("nickname", "")
        wallet = user.get("wallet", {})
        start_coins = wallet.get("gold_coins", 0)
        self.log(f"👤 {nickname} | 当前金币: {start_coins}")

        # 1. 获取每日任务
        self.log("📋 获取任务列表...")
        tasks_data = self.get_daily_tasks()
        self.show_tasks(tasks_data)

        # 2. 每日签到
        self.checkin()

        # 3. 查询设备绑定信息
        try:
            devices_data = self.get_user_devices()
            max_devices = devices_data.get("max_devices", 0)
            used = devices_data.get("devices_used", 0)
            phone_masked = devices_data.get("phone_masked", "")
            self.log(f"📱 设备: {used}/{max_devices} | 手机号: {phone_masked}")
            for d in devices_data.get("devices", []):
                mark = "★当前" if d.get("is_current") else " 其他"
                self.log(f"   {mark} {d.get('device_id')} | 平台: {d.get('platform')} | 最近: {d.get('last_seen_at')}")
            tip = devices_data.get("tip", "")
            if tip:
                self.log(f"   💡 {tip}")
        except Exception as e:
            self.log(f"   ⚠️ 查询设备信息失败: {e}")

        # 5. 看广告赚金币
        self.watch_ads()

        # 5. 遍历任务，领取可领取的奖励
        tasks = tasks_data.get("tasks", [])
        for task in tasks:
            task_key = task.get("task_key", "")
            if task.get("is_completed") and not task.get("is_claimed"):
                self.claim_task(task_key)

        # 5. 查询最终金币余额
        user2 = self.get_user_info()
        end_coins = user2.get("wallet", {}).get("gold_coins", 0)
        earned = end_coins - start_coins
        self.log(f"✨ 任务执行完毕 | 本次获得: {earned}金币 | 总金币: {end_coins}")


def main():
    # 多账号格式: 账号1#密码1[#device_id]&账号2#密码2[#device_id]
    # device_id 可选：账号已绑定设备时必须用绑定的那台（否则 device_limit）
    env_accounts = os.environ.get("WY_ACCOUNT", "").strip()

    accounts = []
    for acc_str in env_accounts.split("&"):
        parts = acc_str.strip().split("#")
        if len(parts) >= 2:
            accounts.append({
                "account": parts[0],
                "password": parts[1],
                "device_id": parts[2] if len(parts) >= 3 and parts[2] else ""
            })

    if not accounts:
        print("❌ 未配置账号，请在环境变量 WY_ACCOUNT 中设置，格式: 账号#密码#device_id (多账号用 & 分隔)")
        print("   示例: WY_ACCOUNT=13800138000#abc123#1786867167261-qp23rrepcah")
        return

    for i, acc in enumerate(accounts):
        if not acc.get("device_id"):
            print(f"❌ 账号 {acc['account']} 未提供 device_id：WY_ACCOUNT 格式为 账号#密码#device_id，"
                  f"device_id 为账号已绑定的设备ID")
            return

        print(f"\n{'='*50}")
        print(f"执行第 {i+1}/{len(accounts)} 个账号")
        print(f"{'='*50}")

        app = WuYouPlan(acc["account"], acc["password"])
        app.device_id = acc["device_id"]
        app.run()

        if i < len(accounts) - 1:
            time.sleep(random.uniform(2, 5))


if __name__ == "__main__":
    main()
