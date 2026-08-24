"""
Author: anonymous
Date: 2026.08.24
Description: OPPO商城小程序签到
Cron: 5 9,12,20 * * *
------------------------------------------
OPPO商城小程序签到 v1.0.0

功能：自动执行OPPO商城小程序签到和积分日常任务，支持多账号执行。

配置说明：
1. 微信 code 网关：（自建微信 code 网关，适配应用宝协议，ck自动获取）
   wx_server_url                                       必填，自建授权服务器地址
   - 示例：http://127.0.0.1:8000
   - 脚本会自动拼接 /wxapp/getCode（YYB 代理服务）
   - 请求格式：POST {网关}/wxapp/getCode
   - 请求体：{"app_id": "wxe705c556754a1de2", "ref": "<微信 openid>"}
   - 响应示例：{"code":0,"msg":"success","data":{"openid":"...","result":{"code":"0f...","errMsg":"login:ok"}}}

2. 账号变量：
   oppo_openid                                         推荐，OPPO账号 openid 变量
   - 多账号支持使用 & 或换行分隔
   - 示例：openid_a&openid_b

3. 青龙任务建议：
   名称：OPPO商城小程序签到
   命令：python oppo.py
   定时：每天运行 1 - 3 次即可，具体时间自行调整
------------------------------------------
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error

# Windows 控制台默认 GBK，无法输出 emoji/特殊符号，强制 stdout/stderr 用 utf-8
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

APPID = "wxe705c556754a1de2"  # OPPO 商城小程序 appid
WX_SERVER = os.environ.get("wx_server_url", "").rstrip("/")
WXID = os.environ.get("oppo_openid", "")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/144.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI "
      "MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a13) XWEB/25297")

MINI_API = "https://omoapplet-api-cn.heytap.com"
MSEC_API = "https://msec.opposhop.cn"
REFERER = f"https://servicewechat.com/{APPID}/361/page-frame.html"

# ---- OPPO 业务接口（签到）----
HD_BASE = "https://hd.opposhop.cn"
API_CREDIT = f"{MSEC_API}/users/web/member/infoDetail"
API_SIGN_DETAIL = f"{HD_BASE}/api/cn/oapi/marketing/cumulativeSignIn/getSignInDetail"
API_SIGN_IN = f"{HD_BASE}/api/cn/oapi/marketing/cumulativeSignIn/signIn"
API_DRAW_CUMULATIVE = f"{HD_BASE}/api/cn/oapi/marketing/cumulativeSignIn/drawCumulativeAward"
API_GOODS_DETAIL = "https://msec.opposhop.cn/cms-business/goods/detail"

# 默认活动 ID（落地页解析失败时回退）
# 主用 ID（已验证有效）；备用 ID 来自社区脚本，主用失效时自动回退
SIGN_ACTIVITY_IDS = [
    os.environ.get("oppo_sign_activity_id", "2083099953777090560"),
    "2061050217641549824",
]
CREDITS_ADD_ACTION_ID = os.environ.get("oppo_credits_action_id", "1788913e6d9e4683b8b9ab0088733560")
TASK_ACTIVITY_ID = os.environ.get("oppo_task_activity_id", "1919591795180969984")

# 任务接口
API_TASK_LIST = f"{HD_BASE}/api/cn/oapi/marketing/task/queryTaskList"
API_TASK_REPORT = f"{HD_BASE}/api/cn/oapi/marketing/taskReport/signInOrShareTask"
API_TASK_RECEIVE = f"{HD_BASE}/api/cn/oapi/marketing/task/receiveAward"

# 任务状态枚举
TASK_STATUS_TODO = 1
TASK_STATUS_CLAIMABLE = 2
TASK_STATUS_DONE = 3
TASK_TYPE_BROWSE = 1
TASK_TYPE_GOODS = 3   # 浏览商品/加购类

SIMULATE_WAIT = True   # 是否真实等待浏览秒数
BROWSE_TIMEOUT = 20    # 请求超时(秒)


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        print(line.encode("utf-8", "replace").decode("utf-8", "replace"), flush=True)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _http(method, url, headers=None, body=None, timeout=20):
    h = {"User-Agent": UA, "Accept": "*/*", "Accept-Language": "zh-CN,zh;q=0.9"}
    if headers:
        h.update(headers)
    data = None
    if body is not None:
        if isinstance(body, dict):
            data = json.dumps(body).encode("utf-8")
            h.setdefault("Content-Type", "application/json")
        elif isinstance(body, str):
            data = body.encode("utf-8")
            h.setdefault("Content-Type", "application/json")
        else:
            data = body
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        resp = _opener.open(req, timeout=timeout)
        status, rhdrs, rbody = resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as e:
        status, rhdrs, rbody = e.code, e.headers, e.read()
    return status, rhdrs, rbody


def get_code():
    """从应用宝网关获取微信 code"""
    url = WX_SERVER + "/wxapp/getCode"
    payload = {"app_id": APPID, "ref": WXID}
    status, _, rbody = _http("POST", url, body=payload, timeout=30)
    j = json.loads(rbody.decode("utf-8"))
    
    code = None
    if j.get("code") == 0:
        data = j.get("data") or {}
        result = data.get("result") if isinstance(data, dict) else {}
        if isinstance(result, dict) and result.get("code"):
            code = str(result["code"])
    if not code:
        raise RuntimeError(f"getCode 失败: {json.dumps(j, ensure_ascii=False)[:200]}")
    return code


def pre_auth_login(code):
    """POST /user/pre/auth 换 ck """
    url = MINI_API + "/user/pre/auth"
    body = {"code": code}
    headers = {"Content-Type": "application/json", "Referer": REFERER}
    status, rhdrs, rbody = _http("POST", url, headers=headers, body=body)
    j = json.loads(rbody.decode("utf-8"))
    
    if str(j.get("ret")) != "1":
        raise RuntimeError(f"pre/auth 失败: ret={j.get('ret')} errMsg={j.get('errMsg')}")
    
    data = j.get("data", {})
    result = {
        "openId": data.get("openId", ""),
        "sessionId": data.get("sessionId", ""),
        "encryptedSession": data.get("encryptedSession", ""),
    }
    return result


def get_member_info(encrypted_session, open_id):
    """GET /users/web/member/info 验证 ck + 获取用户信息"""
    url = MSEC_API + "/users/web/member/info"
    headers = {
        "Content-Type": "application/json",
        "Referer": REFERER,
        "NEWOPPOSID": encrypted_session,
        "openid": open_id,
        "source_type": "503",
        "s_channel": "program_wx",
        "s_version": "80457",
        "sa_distinct_id": open_id,
    }
    status, rhdrs, rbody = _http("GET", url, headers=headers)
    j = json.loads(rbody.decode("utf-8"))
    if status == 200 and j.get("code") == 200:
        data = j.get("data", {})
        log(f"👤 账号: {data.get('userName')}")
        return data
    raise RuntimeError(f"member/info 失败: code={j.get('code')} msg={j.get('message', '')[:60]}")


def get_ck():
    """获取 ck """
    code = get_code()
    result = pre_auth_login(code)
    get_member_info(result["encryptedSession"], result["openId"])

    ck_data = {
        "appid": APPID,
        "wxid": WXID,
        "openId": result["openId"],
        "sessionId": result["sessionId"],
        "encryptedSession": result["encryptedSession"],
    }
    log("🔑 登录成功")
    return ck_data


def make_headers(ck_data, extra=None):
    """根据 ck 构造业务请求头"""
    h = {
        "Content-Type": "application/json",
        "Referer": REFERER,
        "NEWOPPOSID": ck_data["encryptedSession"],
        "openid": ck_data["openId"],
        "source_type": "503",
        "s_channel": "program_wx",
        "s_version": "80457",
        "sa_distinct_id": ck_data["openId"],
        "User-Agent": UA,
    }
    if extra:
        h.update(extra)
    return h


class OppoClient:
    """OPPO 业务客户端（签到），基于已登录的 ck_data"""

    def __init__(self, ck_data):
        self.ck = ck_data
        self.session_id = ck_data.get("sessionId", "")
        self.encrypted_session = ck_data.get("encryptedSession", "")
        self.openid = ck_data.get("openId", "")

    def _biz_headers(self):
        h = {
            "Content-Type": "application/json",
            "Referer": REFERER,
            "NEWOPPOSID": self.encrypted_session,
            "openid": self.openid,
            "source_type": "503",
            "s_channel": "program_wx",
            "s_version": "80457",
            "sa_distinct_id": self.openid,
            "User-Agent": UA,
        }
        return h

    def credit_info(self):
        """查询会员积分，返回 userCredit（总积分），失败返回 None"""
        status, _, rbody = _http("GET", API_CREDIT, headers=self._biz_headers())
        try:
            j = json.loads(rbody.decode("utf-8"))
        except Exception:
            return None
        if status == 200 and j.get("code") == 200:
            return (j.get("data") or {}).get("userCredit")
        return None

    def sign_detail(self, activity_id=None):
        aid = str(activity_id or SIGN_ACTIVITY_IDS[0])
        url = API_SIGN_DETAIL + "?activityId=" + aid
        status, _, rbody = _http("GET", url, headers=self._biz_headers())
        try:
            j = json.loads(rbody.decode("utf-8"))
        except Exception:
            return {}
        if status == 200 and j.get("code") == 200:
            return j.get("data", {})
        return {}

    def do_sign(self):
        """执行签到，返回 (ok, msg, award)"""
        # 选定有效的签到活动 ID（主用失效时回退备用）
        detail = None
        used_aid = None
        for aid in SIGN_ACTIVITY_IDS:
            detail = self.sign_detail(aid)
            if detail:
                used_aid = aid
                break
        if not detail:
            return False, "签到详情获取失败", None

        if detail.get("todaySignIn") is True:
            days = detail.get("signInDayNum")
            return True, f"今日已签到，本周累计【{days}】天", 0

        body = {
            "activityId": str(used_aid),
            "creditsAddActionId": str(CREDITS_ADD_ACTION_ID),
            "business": 1,
        }
        status, _, rbody = _http("POST", API_SIGN_IN, headers=self._biz_headers(), body=body)
        try:
            data = json.loads(rbody.decode("utf-8"))
        except Exception:
            return False, f"签到响应解析失败: {rbody[:80]}", None

        if data.get("code") != 200:
            msg = str(data.get("message") or data.get("errorMessage") or data)
            if any(k in msg for k in ("已签", "重复", "已经签到", "今日已")):
                return True, f"今日已签到（{msg}）", 0
            return False, f"签到失败: {msg}", None

        info = data.get("data") or {}
        if isinstance(info, dict) and info.get("receiveStatus") is False:
            fail = info.get("receiveFailMsg") or "领取失败"
            if any(k in str(fail) for k in ("已签", "重复", "今日")):
                return True, f"今日已签到（{fail}）", 0
            return False, f"签到领取失败: {fail}", None

        award = None
        try:
            award = int(info.get("awardValue") or 0)
        except Exception:
            award = None

        detail2 = self.sign_detail(used_aid)
        days = detail2.get("signInDayNum") or detail.get("signInDayNum") or "?"
        if award is not None:
            return True, f"签到成功，获得【{award}】积分，累计【{days}】天", award
        return True, f"签到成功，累计【{days}】天", 0

    def draw_cumulative_award(self):
        """领取累计签到奖励（如连续7天额外积分），返回 (ok, msg, award)"""
        # 与 do_sign 一致：选定有效签到活动 ID，避免 activityId 不匹配
        detail = None
        used_aid = None
        for aid in SIGN_ACTIVITY_IDS:
            detail = self.sign_detail(aid)
            if detail:
                used_aid = aid
                break
        if not detail:
            return False, "跳过累计奖励(详情获取失败)", 0
        day_num = detail.get("signInDayNum")
        awards = detail.get("cumulativeAwards") or []
        if not awards or day_num is None:
            return False, "无累计奖励可领", 0

        # 找到 signDayNum == 当前连续天数的奖励
        target = None
        for a in awards:
            try:
                if int(a.get("signDayNum") or 0) == int(day_num):
                    target = a
                    break
            except Exception:
                continue
        if not target:
            return False, f"累计奖励未达领取条件(当前{day_num}天)", 0
        if target.get("receiveStatus") is True:
            return False, f"累计奖励已领取(连续{day_num}天)", 0

        award_id = target.get("awardId")
        body = {"activityId": str(used_aid), "awardId": str(award_id)}
        status, _, rbody = _http("POST", API_DRAW_CUMULATIVE, headers=self._biz_headers(), body=body)
        try:
            data = json.loads(rbody.decode("utf-8"))
        except Exception:
            return False, "累计奖励响应解析失败", 0

        if data.get("code") != 200:
            msg = str(data.get("message") or data.get("errorMessage") or data)
            return False, f"累计奖励领取失败: {msg}", 0
        info = data.get("data") or {}
        award = 0
        try:
            award = int(info.get("awardValue") or 0)
        except Exception:
            award = 0
        return True, f"累计签到(连续{day_num}天)奖励领取成功，+{award}积分", award

    # ---- 浏览任务 ----
    @staticmethod
    def _xml_to_dict(node):
        """将 XML 节点递归转为 dict（文本节点值为字符串）"""
        result = {}
        for child in node:
            tag = child.tag
            if len(child) == 0:
                result[tag] = child.text or ""
            else:
                result[tag] = OppoClient._xml_to_dict(child)
        return result

    def task_list(self):
        url = API_TASK_LIST + "?activityId=" + str(TASK_ACTIVITY_ID) + "&source=c"
        status, _, rbody = _http("GET", url, headers=self._biz_headers())
        try:
            text = rbody.decode("utf-8")
        except Exception:
            return []

        # 优先尝试 JSON，失败则尝试 XML（该接口实际返回 XML）
        try:
            j = json.loads(text)
            if status == 200 and j.get("code") == 200:
                return list((j.get("data") or {}).get("taskDTOList") or [])
            return []
        except Exception:
            pass

        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(text)
            data = root.find("data")
            if data is None:
                return []
            container = data.find("taskDTOList")
            if container is None:
                return []
            tasks = []
            for t in container.findall("taskDTOList"):
                tasks.append(self._xml_to_dict(t))
            return tasks
        except Exception as e:
            log(f"⚠️ 任务列表解析失败: {e}")
            return []

    @staticmethod
    def _parse_common_result(text):
        """解析 OPPO 营销接口响应（兼容 JSON 与 XML），返回 (code, message, data_dict)"""
        # 先尝试 JSON
        try:
            j = json.loads(text)
            code = j.get("code")
            msg = str(j.get("message") or j.get("errorMessage") or "")
            data = j.get("data") or {}
            return code, msg, data
        except Exception:
            pass
        # 回退 XML: <CommonResult><code/><message/><errorMessage/><data>...</data>
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(text)
            code_el = root.find("code")
            code = int(code_el.text) if code_el is not None and code_el.text else None
            msg_el = root.find("message")
            if msg_el is None:
                msg_el = root.find("errorMessage")
            msg = msg_el.text if msg_el is not None else ""
            data_el = root.find("data")
            data = OppoClient._xml_to_dict(data_el) if data_el is not None else {}
            return code, msg or "", data
        except Exception:
            return None, "响应解析失败", {}

    def report_browse(self, task):
        # 必须用任务真实的 taskType（如 1=浏览页面、3=浏览商品），服务端据此校验
        params = {
            "taskId": str(task.get("taskId") or ""),
            "activityId": str(task.get("activityId") or TASK_ACTIVITY_ID),
            "taskType": str(task.get("taskType") or TASK_TYPE_BROWSE),
        }
        url = API_TASK_REPORT + "?" + "&".join(f"{k}={params[k]}" for k in params)
        status, _, rbody = _http("GET", url, headers=self._biz_headers())
        try:
            text = rbody.decode("utf-8")
        except Exception:
            return False
        code, _, _ = self._parse_common_result(text)
        return code == 200

    def receive_award(self, task):
        params = {
            "taskId": str(task.get("taskId") or ""),
            "activityId": str(task.get("activityId") or TASK_ACTIVITY_ID),
            "creditsAddActionId": str(CREDITS_ADD_ACTION_ID),
            "business": "1",
        }
        url = API_TASK_RECEIVE + "?" + "&".join(f"{k}={params[k]}" for k in params)
        status, _, rbody = _http("GET", url, headers=self._biz_headers())
        try:
            text = rbody.decode("utf-8")
        except Exception:
            return False, 0, "响应解析失败"
        code, msg, data = self._parse_common_result(text)
        if code != 200:
            return False, 0, msg
        if isinstance(data, dict) and data.get("receiveStatus") is False:
            return False, 0, str(data.get("receiveFailMsg") or "领奖失败")
        points = 0
        if isinstance(data, dict):
            try:
                points = int(data.get("awardValue") or 0)
            except Exception:
                points = 0
        return True, points, f"+{points}" if points else "ok"

    def browse_products(self, goods_num):
        """浏览商品任务(taskType=3)：抓取若干 SKU 详情页模拟浏览"""
        sku_ids = self.get_sku_ids()
        if not sku_ids:
            log("⚠️ 未解析到可浏览商品 SKU")
            return False
        import random
        random.shuffle(sku_ids)
        ok = 0
        for sku_id in sku_ids[:max(1, int(goods_num or 1))]:
            try:
                url = (API_GOODS_DETAIL +
                       f"?interfaceVersion=v2&pageCode=skuDetail"
                       f"&modelCode=OnePlus%20PJZ110&skuId={sku_id}")
                status, _, rbody = _http("GET", url, headers=self._biz_headers())
                try:
                    j = json.loads(rbody.decode("utf-8"))
                except Exception:
                    j = {}
                if status == 200 and (j.get("code") == 200 or j.get("data")):
                    ok += 1
                time.sleep(1.0)
            except Exception:
                pass
        return ok > 0

    def get_sku_ids(self):
        """从首页配置提取可浏览的商品 SKU 列表"""
        try:
            url = "https://msec.opposhop.cn/configs/web/advert/220031"
            status, _, rbody = _http("GET", url, headers=self._biz_headers())
            try:
                j = json.loads(rbody.decode("utf-8"))
            except Exception:
                return []
            from urllib.parse import urlparse, parse_qs
            sku_ids = set()
            for module in (j.get("data") or []):
                for detail in (module.get("details") or []):
                    link = detail.get("link", "") or ""
                    if "skuId=" in link:
                        q = parse_qs(urlparse(link).query)
                        sid = q.get("skuId", [None])[0]
                        if sid:
                            sku_ids.add(sid)
                    hz = detail.get("hotZone", {}) or {}
                    for sub in (hz.get("hotZoneSubscribe") or []):
                        if sub.get("skuId"):
                            sku_ids.add(sub.get("skuId"))
                    gf = detail.get("goodsForm", {}) or {}
                    if gf.get("skuId"):
                        sku_ids.add(gf.get("skuId"))
            return list(sku_ids)
        except Exception:
            return []

    @staticmethod
    def browse_seconds(task):
        cfg = task.get("attachConfigOne") or {}
        try:
            sec = int(cfg.get("browseTime") or 5)
        except Exception:
            sec = 5
        return max(1, min(sec, 30))

    @staticmethod
    def task_award_points(task):
        cfg = task.get("awardAttachConfig") or {}
        pts = cfg.get("pointsNum")
        return str(pts) if pts is not None else "?"

    @staticmethod
    def _print_task_list(tasks, skip_states=None):
        """打印任务清单；skip_states 中的状态不打印（待完成任务执行时实时展示）"""
        skip = skip_states or set()
        for t in tasks:
            name = str(t.get("taskName") or t.get("taskId") or "未知任务")
            ttype = int(t.get("taskType") or -1)
            status = int(t.get("taskStatus") or 0)
            award = OppoClient.task_award_points(t)
            if status == TASK_STATUS_DONE:
                icon, state = "✅", "已完成"
            elif status == TASK_STATUS_CLAIMABLE:
                continue  # 待领奖不预列，执行时实时领
            elif ttype in (TASK_TYPE_BROWSE, TASK_TYPE_GOODS):
                if status in skip:
                    continue  # 待完成不预列，执行时实时做
                icon, state = "👀", "待完成"
            else:
                icon, state = "⏭️", "需人工"
            log(f"  {icon} {name}  （{state}，积分{award}）")

    def do_browse_tasks(self, simulate_wait=True):
        done = 0
        awarded = 0
        skipped = 0
        failed = 0
        points = 0
        details = []
        manual_hint = False

        tasks = self.task_list()
        if not tasks:
            log("⚠️ 任务列表为空")
            return {"done": done, "awarded": awarded, "skipped": skipped,
                    "failed": failed, "points": points, "details": details,
                    "manual_hint": manual_hint}

        auto_types = (TASK_TYPE_BROWSE, TASK_TYPE_GOODS)
        browse = [t for t in tasks if int(t.get("taskType") or -1) in auto_types]
        other = [t for t in tasks if int(t.get("taskType") or -1) not in auto_types]
        log(f"📋 任务总数 {len(tasks)}，可自动 {len(browse)}，需人工 {len(other)}")

        # 清单只列已完成 + 需人工；待完成/待领奖在执行时实时打印
        self._print_task_list(tasks, skip_states={TASK_STATUS_TODO, TASK_STATUS_CLAIMABLE})

        for t in other:
            status = int(t.get("taskStatus") or 0)
            if status == TASK_STATUS_CLAIMABLE:
                # 非浏览类但服务端已标记可领（如填写收货地址）：直接领奖
                name = str(t.get("taskName") or t.get("taskId"))
                award = self.task_award_points(t)
                ok, pts, msg = self.receive_award(t)
                if ok:
                    awarded += 1
                    points += pts
                    if pts:
                        details.append(f"{name}+{pts}")
                        log(f"🎁 [{name}] +{pts}积分")
                    else:
                        details.append(f"{name}领奖成功")
                    log(f"✅ {name}  （已完成，积分{award}）")
                elif any(k in msg for k in ("已领", "已完成", "重复", "已经")):
                    skipped += 1
                    log(f"✅ {name}  （已完成，积分{award}）")
                else:
                    failed += 1
                    details.append(f"{name}失败:{msg}")
                    log(f"❌ [{name}] 领奖失败: {msg}")
                time.sleep(0.6)
            else:
                skipped += 1
                manual_hint = True

        total = len(browse)
        idx = 0
        for task in browse:
            name = str(task.get("taskName") or task.get("taskId"))
            status = int(task.get("taskStatus") or 0)
            task_id = str(task.get("taskId") or "")
            award = self.task_award_points(task)

            if not task_id:
                skipped += 1
                continue

            if status == TASK_STATUS_DONE:
                skipped += 1
                continue

            idx += 1
            if status == TASK_STATUS_TODO:
                ttype = int(task.get("taskType") or 0)
                if ttype == TASK_TYPE_GOODS:
                    goods_num = int((task.get("attachConfigOne") or {}).get("goodsNum") or 1)
                    log(f"👀 浏览 [{name}] 商品 x{goods_num}")
                    self.browse_products(goods_num)
                    time.sleep(0.8)
                else:
                    wait_s = self.browse_seconds(task)
                    if simulate_wait:
                        log(f"👀 浏览 [{name}] {wait_s}s")
                        time.sleep(wait_s + 0.5)
                if not self.report_browse(task):
                    failed += 1
                    log(f"❌ [{name}] 提交失败")
                    continue
                done += 1
                time.sleep(0.8)
                status = TASK_STATUS_CLAIMABLE

            ok, pts, msg = self.receive_award(task)
            if ok:
                awarded += 1
                points += pts
                if pts:
                    details.append(f"{name}+{pts}")
                    log(f"🎁 [{name}] +{pts}积分")
                else:
                    details.append(f"{name}领奖成功")
                log(f"✅ {name}  （已完成，积分{award}）")
            else:
                if any(k in msg for k in ("已领", "已完成", "重复", "已经")):
                    skipped += 1
                    log(f"✅ {name}  （已完成，积分{award}）")
                else:
                    failed += 1
                    details.append(f"{name}失败:{msg}")
                    log(f"❌ [{name}] 领奖失败: {msg}")
            time.sleep(0.6)

        return {"done": done, "awarded": awarded, "skipped": skipped,
                "failed": failed, "points": points, "details": details,
                "manual_hint": manual_hint}


def mask_openid(openid):
    """脱敏 openid：保留前6后4，中间用 *** 替代"""
    if not openid or len(openid) <= 10:
        return openid
    return f"{openid[:6]}***{openid[-4:]}"


def run_account(openid, index=1, total=1):
    """执行单个账号的完整流程"""
    global WXID
    WXID = openid

    log(f">>> 账号 {index}/{total} : {mask_openid(openid)}")
    try:
        ck = get_ck()
    except Exception as e:
        log(f"❌ 登录失败: {e}")
        return 1

    client = OppoClient(ck)

    # 执行前积分
    before = client.credit_info()
    if before is not None:
        log(f"💰 执行前积分: {before}")

    log("──── 签到 ────")
    ok, msg, award = client.do_sign()
    if ok:
        log(f"✅ {msg}" + (f" (+{award} 积分)" if award else ""))
    else:
        log(f"❌ {msg}")

    ok2, msg2, _ = client.draw_cumulative_award()
    if ok2:
        log(f"🎁 累计奖励: {msg2}")
    else:
        log(f"ℹ️ 累计奖励: {msg2}")

    log("──── 日常任务 ────")
    stats = client.do_browse_tasks(simulate_wait=SIMULATE_WAIT)
    if stats.get("manual_hint"):
        log("ℹ️ 提示: 非浏览类任务需人工完成")
    log(f"📊 提交 {stats['done']} | 领奖 {stats['awarded']} | "
        f"跳过 {stats['skipped']} | 失败 {stats['failed']} | "
        f"积分 +{stats['points']}")

    # 执行后积分
    after = client.credit_info()
    if after is not None:
        delta = (after - before) if before is not None else None
        if delta is not None:
            log(f"💰 总积分: {after}（本次 +{delta}）")
    return 0


def _split_accounts(raw):
    """按 & 或换行 或逗号 或空格 拆分多账号"""
    if not raw:
        return []
    parts = []
    for sep in ("&", "\n", ",", " "):
        raw = raw.replace(sep, "\n")
    for line in raw.split("\n"):
        line = line.strip()
        if line:
            parts.append(line)
    # 去重保序
    seen = set()
    uniq = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def main():
    if not WX_SERVER:
        log("❌ 未配置 wx_server_url 环境变量 (微信 code 网关地址)")
        return 1
    if not WXID:
        log("❌ 未配置 oppo_openid 环境变量 (微信 openid)")
        return 1

    log("====== OPPO 商城签到 ======")
    accounts = _split_accounts(WXID)
    log(f"📱 共配置 {len(accounts)} 个账号")
    failed = 0
    for i, openid in enumerate(accounts, 1):
        try:
            rc = run_account(openid, index=i, total=len(accounts))
            if rc != 0:
                failed += 1
        except Exception as e:
            failed += 1
            log(f"❌ 账号 {openid} 执行异常: {e}")
    if failed:
        log(f"⚠️ {failed}/{len(accounts)} 个账号执行失败")
    return 0


if __name__ == "__main__":
    sys.exit(main())
