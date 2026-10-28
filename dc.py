"""
Author: anonymous
Date: 2026.08.26
Description: 大潮App签到
Cron: 30 10,14,18 * * *
----------------------------------------------------------------------------------------------
大潮App签到

功能：自动执行大潮App登录、签到、阅读、看视频、玩游戏抽奖任务，支持多账号执行。

配置方式：
  环境变量 dc_ck ，格式：手机号#密码明文
  多账号用 & 分割，例如：
  13800000000#MyPassw0rd&13800000001#MyPassw0rd123

红包自动兑换：
  大潮的现金红包（抽奖 type=3）只能在微信端领取：兑奖码必须带着【微信公众号会员】
  去请求 POST https://m.aihoge.com/api/lotteryhy/api/client/cj/send/pak，
  App 会员（source=xsb_hn）通常会被判 424/427 无领取权限。

  环境变量：
    dc_openid     微信 openid（每个账号一个）
    wx_server_url 应用宝网关地址
  脚本会自动调用网关 /wxapp/getCode 获取微信授权 code，
  再走 /api/memberhy/h5/signature 换取微信会员身份，全程无需手动抓包。
  会员身份有效期约 10 天，脚本每次运行时自动获取新的。

  多账号按手机号绑定（推荐），dc_openid 每行一个或用 & 分割，格式：手机号|openid
  示例（换行分割）：
    dc_openid=13800000000|oFakeOpenidXXXXXXXXXXXXXXXXXX
    13800000001|oFakeOpenidYYYYYYYYYYYYYYYYYY
  示例（& 分割）：
    dc_openid=13800000000|oFakeOpenidXXXXXXXXXXXXXXXXXX&13800000001|oFakeOpenidYYYYYYYYYYYYYYYYYY
    wx_server_url=<应用宝网关地址>

  也支持 JSON 对象（key 为手机号）：
    dc_openid={"13800000000":"oFakeOpenidXXXXXXXXXXXXXXXXXX","13800000001":"oFake..."}

  未配置 dc_openid/wx_server_url 时，跳过兑换（App 会员无法兑换）。

大潮老友/银龄视界「看视频抽好礼」（可选，默认开启）：
  在「阅读有礼任务模块」之后执行。进入大潮APP「大潮老友/银龄视界」频道看视频，累计观看
  5/15/30/45/60 分钟各可开启 1 次抽奖机会，共 5 次。
  脚本会先按档位开通抽奖机会（服务端不校验时长时可直接开通），未达成的档位再
  通过 /aged/h5/agedDrama/watch/record/report 补报观看时长后重试。

  环境变量：
    DC_WATCH_ENABLE   0 关闭该模块（默认 1 开启）
    DC_WATCH_MAX_MIN  单日最多模拟观看的分钟数，默认 60（覆盖全部 5 档）
    DC_WATCH_CHUNK    单次上报的观看秒数，默认 12（与 App 端 apiRateLimit 一致）
    DC_WATCH_SLEEP    两次上报之间的真实等待秒数，默认 2
                      （设为 12 即等价于真实观看速度；设为 0 则快速跑完）

6. 游戏中心-玩游戏抽好礼模块（mode=all/read/watch/game）
  在「大潮老友/银龄视界-看视频抽好礼模块」之后执行。进入大潮APP「游戏中心」玩游戏，
  累计游戏时长 5/25/45 分钟各可获得 1 次抽奖机会，共 3 次。
  档位达成后服务端自动发放机会（无需开通），脚本按 remain_counts 抽奖。

  环境变量：
    DC_GAME_ENABLE    0 关闭该模块（默认 1 开启）
    DC_GAME_MAX_MIN   单日最多模拟游戏的分钟数，默认 45（覆盖全部 3 档）
    DC_GAME_CHUNK     单次心跳上报的秒数，默认 15（与 App 端节奏一致，服务端原样采信）
    DC_GAME_SLEEP     两次心跳之间的真实等待秒数，默认 16
                      （调小可加速，但服务端会标记 is_frequent_heartbeat，有风控风险）

依赖：requests, cryptography, numpy, Pillow
----------------------------------------------------------------------------------------------
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import logging
import os
import random
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, quote, urlparse

import requests

# 兼容 Windows GBK 控制台：输出含 emoji/特殊字符时强制 UTF-8，避免
# 'gbk' codec can't encode character 崩溃（print 摘要/日志带 ✅❌🎰 等）
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

try:
    import numpy as np
except ImportError:
    np = None

try:
    from PIL import Image
except ImportError:
    Image = None

try:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
except ImportError:
    serialization = None
    padding = None
    Cipher = None
    algorithms = None
    modes = None

# ===========================================================================
# HTTP 调试日志
# ===========================================================================

logger_http = logging.getLogger("http_debug")

_SENSITIVE_KEYWORDS = (
    "password", "cookie", "token", "member", "code",
    "authorization", "btoken", "mtoken", "stoken", "x-auth-token", "yi-token",
)


def _is_sensitive_key(key: str) -> bool:
    k = str(key or "").lower()
    return any(word in k for word in _SENSITIVE_KEYWORDS)


def _mask_string(value: Any, *, keep_head: int = 4, keep_tail: int = 4) -> Any:
    if value is None:
        return None
    if isinstance(value, (int, float, bool)):
        return value
    s = str(value)
    if not s:
        return s
    if len(s) <= keep_head + keep_tail:
        return "*" * len(s)
    return f"{s[:keep_head]}...{s[-keep_tail:]}(len={len(s)})"


def redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for k, v in obj.items():
            if _is_sensitive_key(k):
                out[str(k)] = _mask_string(v)
            else:
                out[str(k)] = redact(v)
        return out
    if isinstance(obj, list):
        return [redact(x) for x in obj]
    if isinstance(obj, str):
        if len(obj) >= 120:
            return _mask_string(obj)
        return obj
    return obj


def _try_parse_json(text: str) -> Optional[Any]:
    if not text:
        return None
    t = text.lstrip()
    if not (t.startswith("{") or t.startswith("[")):
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


def log_http_exchange(
    *,
    account_name: str,
    method: str,
    url: str,
    headers: Dict[str, Any],
    params: Optional[Dict[str, Any]] = None,
    data: Any = None,
    json_body: Any = None,
    timeout: Any = None,
    response: requests.Response,
    elapsed_s: float,
) -> None:
    if not logger_http.isEnabledFor(logging.DEBUG):
        return
    req_payload: Dict[str, Any] = {
        "method": method,
        "url": url,
        "headers": redact(headers or {}),
        "params": redact(params or {}),
        "timeout": timeout,
    }
    if data is not None:
        if isinstance(data, bytes):
            req_payload["data"] = data.decode("utf-8", errors="replace") or "(empty)"
        else:
            req_payload["data"] = redact(data)
    if json_body is not None:
        req_payload["json"] = redact(json_body)
    resp_headers = dict(response.headers or {})
    try:
        resp_text = response.text or ""
    except Exception:
        resp_text = ""
    resp_json = _try_parse_json(resp_text)
    resp_payload: Dict[str, Any] = {
        "status_code": response.status_code,
        "elapsed_s": round(elapsed_s, 3),
        "headers": redact(resp_headers),
    }
    if resp_json is not None:
        resp_payload["json"] = redact(resp_json)
    else:
        resp_payload["text"] = (resp_text[:1500] + "...(truncated)") if len(resp_text) > 1500 else resp_text
    block = {"request": req_payload, "response": resp_payload}
    prefix = f"[{account_name}] " if account_name else ""
    logger_http.debug("%sHTTP 调试信息:\n%s", prefix, json.dumps(block, ensure_ascii=False, indent=2))


def request_json(
    session: requests.Session,
    *,
    method: str,
    url: str,
    headers: Dict[str, str],
    params: Optional[Dict[str, Any]] = None,
    data: Any = None,
    json_body: Any = None,
    timeout: Any = 30,
    account_name: str = "",
) -> Dict[str, Any]:
    start = time.time()
    resp = session.request(method=method, url=url, headers=headers, params=params, data=data, json=json_body, timeout=timeout)
    elapsed = time.time() - start
    log_http_exchange(
        account_name=account_name, method=method, url=url, headers=headers,
        params=params, data=data, json_body=json_body, timeout=timeout,
        response=resp, elapsed_s=elapsed,
    )
    resp.raise_for_status()
    return resp.json()


# ===========================================================================
# 滑块验证码偏移量计算
# ===========================================================================

logger_captcha = logging.getLogger("captcha")


def download_captcha_image(url: str) -> Optional["np.ndarray"]:
    if np is None or Image is None:
        logger_captcha.error("缺少 numpy 或 Pillow 依赖，无法处理验证码图片")
        return None
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        img = Image.open(BytesIO(response.content))
        if img.mode != "RGB":
            img = img.convert("RGB")
        return np.array(img)
    except Exception as e:
        logger_captcha.error(f"下载验证码图片失败: {e}")
        return None


def _calculate_offset_method1(part1_with_gap, part3_complete) -> Optional[int]:
    diff = np.abs(part1_with_gap.astype(int) - part3_complete.astype(int))
    diff_gray = np.sum(diff, axis=2) if len(diff.shape) == 3 else diff
    height = diff_gray.shape[0]
    edge_positions = []
    for row in range(height):
        row_diff = diff_gray[row, :]
        threshold = 30
        if np.max(row_diff) > threshold:
            significant = np.where(row_diff > threshold)[0]
            if len(significant) > 0:
                edge_positions.append(significant[0])
    if edge_positions:
        return int(np.median(edge_positions))
    return None


def _calculate_offset_method2(part2_slider, part3_complete, slider_width: int = 50) -> int:
    slider = part2_slider[:, 0:slider_width]
    min_diff = float("inf")
    best_x = 0
    img_width = part3_complete.shape[1]
    for x in range(0, img_width - slider_width):
        region = part3_complete[:, x : x + slider_width, :] if len(part3_complete.shape) == 3 else part3_complete[:, x : x + slider_width]
        diff = np.sum((slider.astype(int) - region.astype(int)) ** 2)
        if diff < min_diff:
            min_diff = diff
            best_x = x
    return best_x


def _calculate_offset_method3(part1_with_gap, part3_complete) -> Optional[int]:
    diff = np.abs(part1_with_gap.astype(int) - part3_complete.astype(int))
    col_diff = np.sum(diff, axis=(0, 2)) if len(diff.shape) == 3 else np.sum(diff, axis=0)
    threshold = np.mean(col_diff) + 0.8 * np.std(col_diff)
    gap_cols = np.where(col_diff > threshold)[0]
    if len(gap_cols) > 0:
        return int(gap_cols[0])
    return None


def calculate_slide_offset(image_url: str) -> Optional[int]:
    img_array = download_captcha_image(image_url)
    if img_array is None:
        return None
    height = img_array.shape[0]
    if height % 3 != 0:
        logger_captcha.warning("验证码图片高度不是3的倍数")
        return None
    part_height = height // 3
    part1_with_gap = img_array[0:part_height, :]
    part2_slider = img_array[part_height : part_height * 2, :]
    part3_complete = img_array[part_height * 2 : part_height * 3, :]
    results = []
    try:
        offset1 = _calculate_offset_method1(part1_with_gap, part3_complete)
        if offset1 is not None:
            results.append(offset1)
    except Exception as e:
        logger_captcha.debug(f"方法1计算失败: {e}")
    try:
        offset2 = _calculate_offset_method2(part2_slider, part3_complete)
        if offset2 is not None:
            results.append(offset2)
    except Exception as e:
        logger_captcha.debug(f"方法2计算失败: {e}")
    try:
        offset3 = _calculate_offset_method3(part1_with_gap, part3_complete)
        if offset3 is not None:
            results.append(offset3)
    except Exception as e:
        logger_captcha.debug(f"方法3计算失败: {e}")
    if not results:
        logger_captcha.warning("无法检测到缺口位置")
        return None
    final_offset = int(np.median(results))
    logger_captcha.info(f"验证码偏移量计算结果: tn_x = {final_offset}")
    return final_offset


# ===========================================================================
# 大潮 API 全流程
# ===========================================================================

logger_api = logging.getLogger("api")

DEFAULT_TIMEOUT = (10, 30)

REDEEM_ALREADY_RECEIVED_CODES = {"is_receive_packet"}

# POST /api/lotteryhy/api/client/cj/send/pak 业务错误码
REDEEM_ERROR_HINTS = {
    "422": "请在微信内参与（非微信会员身份，需配置 dc_openid + wx_server_url）",
    "423": "兑奖码已绑定其他应用，需解绑后重试",
    "424": "当前微信未绑定该App会员，需解绑后重试",
    "425": "兑奖码已被放弃领取",
    "426": "兑奖码已过期",
    "427": "当前微信号30天内已绑定其他App会员，无领取权限",
}

# 红包领取页（m.aihoge.com）使用的微信公众号 appid
WECHAT_APPID = "wx2e98fb1d45a98f73"

# 领取页强制要求微信环境（非微信直接提示"请在微信中领取红包"）
# 因此兑换请求必须带微信 WebView UA，不能用 App 的 vapp UA
DEFAULT_REDEEM_UA = (
    "Mozilla/5.0 (Linux; Android 12; M2007J17C Build/SKQ1.211006.001; wv) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Version/4.0 Chrome/150.0.7871.189 Mobile Safari/537.36 XWEB/1500117 "
    "MMWEBSDK/20260604 MMWEBID/8139 MicroMessenger/8.0.77.3160(0x28004D34) WeChat/arm64 "
    "Weixin NetType/WIFI Language/zh_CN ABI/arm64 miniProgram/wxf83b111bd87080bf"
)


def build_wechat_member_header(member_info: Dict[str, Any], *, nick_name: str = "", avatar: str = "") -> str:
    """把 /api/memberhy/h5/signature 返回的微信会员 JSON 拼成 member 请求头"""
    header = {
        "id": str(member_info.get("id") or ""),
        "appId": str(member_info.get("appId") or WECHAT_APPID),
        "black": int(member_info.get("black", 0) or 0),
        "btoken": str(member_info.get("btoken") or ""),
        "expire": int(member_info.get("expire", 0) or 0),
        "token": str(member_info.get("token") or ""),
        "source": str(member_info.get("source") or "wechat"),
        "mobile": str(member_info.get("mobile") or ""),
        "mark": str(member_info.get("mark") or ""),
        "mtoken": str(member_info.get("mtoken") or ""),
        "stoken": str(member_info.get("stoken") or ""),
        "nick_name": quote(str(member_info.get("nick_name") or nick_name), safe=""),
        "avatar": str(member_info.get("avatar") or avatar or ""),
    }
    return _safe_json_dumps(header)


def normalize_redeem_member(raw: str) -> str:
    """规范化配置的微信会员信息。

    - 传入 /api/memberhy/h5/signature 的响应 JSON（含 id/token/mtoken/stoken/btoken）→ 自动拼装 member 头
    - 传入已拼装好的 member 头字符串 → 原样返回
    """
    value = str(raw or "").strip()
    if not value:
        return ""
    if not value.startswith("{"):
        return value
    try:
        obj = json.loads(value)
    except Exception:
        return value
    if not isinstance(obj, dict):
        return value
    if "member" in obj and isinstance(obj["member"], dict):
        obj = obj["member"]
    if not any(obj.get(k) for k in ("id", "mtoken", "stoken", "token")):
        return value
    return build_wechat_member_header(obj)


def interpret_redeem_response(resp: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(resp, dict):
        return {"ok": False, "already_received": False, "code": "", "message": "invalid_response"}
    success = resp.get("success")
    if success in (True, 1, "1"):
        return {"ok": True, "already_received": False, "code": "success", "message": ""}
    if resp.get("code") == 0:
        return {"ok": True, "already_received": False, "code": "code=0", "message": str(resp.get("message") or "")}
    error_code = resp.get("error_code")
    error_code_str = str(error_code) if error_code is not None else ""
    error_message = str(resp.get("error_message") or resp.get("message") or resp.get("error") or "")
    if error_code_str in REDEEM_ALREADY_RECEIVED_CODES:
        return {"ok": True, "already_received": True, "code": error_code_str, "message": error_message or "已领取"}
    if error_code == 0 or error_code_str == "0":
        return {"ok": True, "already_received": False, "code": error_code_str, "message": error_message}
    hint = REDEEM_ERROR_HINTS.get(error_code_str, "")
    if not hint:
        message = error_message or "未知错误"
    elif not error_message or hint.startswith(error_message.rstrip(".。…")) or error_message in hint:
        message = hint
    else:
        message = f"{hint}（{error_message}）"
    return {"ok": False, "already_received": False, "code": error_code_str, "message": message}


def fetch_wechat_member_via_gateway(
    gateway_url: str, app_id: str, openid: str,
    *, redeem_code: str = "", account_name: str = "", timeout: int = 30,
) -> Tuple[str, Dict[str, Any]]:
    """通过应用宝网关自动获取微信会员身份。

    链路：
      1. POST {gateway_url}/wxapp/getCode  → 获取微信授权 code
      2. GET  /api/memberhy/h5/signature   → 用 code 换取微信会员 JSON

    Args:
        gateway_url:  应用宝网关地址，如 http://127.0.0.1:8000
        app_id:       大潮公众号 appid（wx2e98fb1d45a98f73）
        openid:       用户的微信 openid
        redeem_code:  兑奖码（用于 Referer，非必需）
        account_name: 日志标识
    Returns:
        (member_header_str, raw_member_info)
    """
    sess = requests.Session()
    sess.trust_env = False
    tag = f"[{account_name}] " if account_name else ""

    # Step 1: 获取微信授权 code
    resp = sess.post(
        f"{gateway_url.rstrip('/')}/wxapp/getCode",
        json={"app_id": app_id, "ref": openid},
        headers={"Content-Type": "application/json"},
        timeout=timeout,
    )
    resp.raise_for_status()
    gw = resp.json()
    if gw.get("code") != 0:
        raise RuntimeError(f"应用宝网关返回错误: {gw}")
    wx_code = (gw.get("data") or {}).get("result", {}).get("code", "")
    if not wx_code:
        raise RuntimeError("应用宝网关未返回 code")
    logger_api.info(f"{tag}应用宝网关获取微信 code 成功: {wx_code[:20]}...")

    # Step 2: 用 code 换取微信会员身份
    params = {
        "code": wx_code,
        "appid": app_id,
        "sign": "wechat",
        "scope": "snsapi_userinfo",
        "mark": "marketing",
    }
    referer_path = "drawRedPacket"
    if redeem_code:
        referer_path = f"drawRedPacket?CHECK_CODE={redeem_code}&code={wx_code}"
    sig_headers = {
        "Host": "m.aihoge.com",
        "Connection": "keep-alive",
        "member": "",
        "X-DEVICE-SIGN": "wechat",
        "X-DEVICE-ID": "000",
        "HTTP-X-H5-VERSION": "1",
        "Limit": "default",
        "User-Agent": DEFAULT_REDEEM_UA,
        "Accept": "application/json, text/plain, */*",
        "X-CLIENT-VERSION": "1314",
        "X-Requested-With": "com.tencent.mm",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Referer": f"https://m.aihoge.com/lottery/rotor/{referer_path}",
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    r2 = sess.get(
        f"https://m.aihoge.com/api/memberhy/h5/signature",
        params=params, headers=sig_headers, timeout=timeout,
    )
    r2.raise_for_status()
    member_info = r2.json()
    member_header = build_wechat_member_header(member_info)
    expire = int(member_info.get("expire", 0) or 0)
    expire_str = ""
    if expire:
        try:
            expire_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(expire))
        except Exception:
            expire_str = str(expire)
    logger_api.info(f"{tag}微信会员身份获取成功: openid={openid}，nick_name={member_info.get('nick_name','')}")
    return member_header, member_info


# aihoge 服务端用的 RSA 2048 位公钥
PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA0G25Cq2HxQQ+gX9H2dzb
6sbRtHzD8JbHRmOrAFzaWI2kdzbPuga4ZlqxOAyoAm8ucIAeKD4joUn+dN1wYC03
qCgloNU21KUJUls/Htp2RwxpmoncSIAOZvSQQ6Kl3vLPYlG6GetwYYN83sG85K+3
w4D89hBGHuYqKQyQsUvntxi5UVoNfo674QsCvqxHxZAuEXKoEagzUoSu8gWrDTuh
RK4aQcDpnCslwKycaO63UBvfTlBG0Jc7sqzXxapTArbqaA58XCM8dRIZdp7DR/V7
qucn/PwIOGJrOu09/cjndwIpeki8HXa9rvgWwiwZCy289vgRoxzIcLrQJ2oC1MK2
RwIDAQAB
-----END PUBLIC KEY-----"""

# vapp X-SIGNATURE 盐值
VAPP_SIGNATURE_SALT = "FR*r!isE5W"

# 随机 UA 数据池（真实手机型号 + 对应 Android 版本）
_UA_MODELS = [
    ("Xiaomi M2007J17C", "12"),   # Redmi Note 9 Pro
    ("Xiaomi M2004J19C", "13"),   # Redmi 10X 4G
    ("Xiaomi 22011211C", "13"),   # Redmi Note 12
    ("Xiaomi M2102J20SG", "13"),  # Redmi Note 11 Pro
    ("Xiaomi 23090RA98C", "14"),  # Redmi Note 13 Pro
    ("Xiaomi M2006C3LG", "10"),   # Redmi 9
    ("Xiaomi 2201117TI", "13"),   # Redmi K40 Pro
    ("Xiaomi M2012K11C", "12"),   # Redmi K40
    ("HUAWEI PCT-AL10", "10"),    # Honor V40
    ("HUAWEI ANA-AN00", "10"),    # Huawei P40
    ("HUAWEI NOH-AN00", "10"),    # Huawei Mate 40 Pro
    ("OPPO PGKM10", "12"),        # OPPO Reno6
    ("OPPO PEEM00", "13"),        # OPPO Reno8
    ("OPPO PJZ110", "14"),        # OPPO Find X6
    ("vivo V2227A", "13"),        # vivo S15
    ("vivo V2304A", "14"),        # vivo S17
    ("vivo V2240A", "14"),        # vivo X100
    ("samsung SM-G991B", "13"),   # Samsung Galaxy S21
    ("samsung SM-S908B", "14"),   # Samsung Galaxy S22 Ultra
    ("OnePlus NE2211", "13"),     # OnePlus Ace
    ("realme RMX3366", "12"),     # realme GT Neo
    ("realme RMX3835", "14"),     # realme GT3
]
_UA_APP_VERSIONS = ["14.1.16", "14.1.15", "14.1.14", "14.1.13", "14.0.9", "14.0.8"]
_UA_INTERNAL_VERSIONS = ["7.13.0", "7.12.0", "7.11.0", "7.10.0", "7.9.0"]


def _build_ua_pair(model: str, os_ver: str, app_ver: str, internal_ver: str, device_uuid: str) -> Tuple[str, str]:
    """根据同一套随机机型/版本生成 vapp UA 与 App WebView UA（两者必须一致）"""
    vapp_ua = f"{app_ver};{device_uuid};{model};Android;{os_ver};Release;{internal_ver}"
    webview_ua = (
        f"Mozilla/5.0 (Linux; Android {os_ver}; {model} Build/SKQ1.211006.001; wv) "
        f"AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/150.0.7871.124 "
        f"Mobile Safari/537.36;xsb_hn;xsb_hn;{app_ver};native_app;{internal_ver}"
    )
    return vapp_ua, webview_ua


def _generate_ua_pair() -> Tuple[str, str]:
    """随机生成 (vapp UA, WebView UA)：app_version;device_uuid;model;Android;os_version;Release;internal_version"""
    model, os_ver = random.choice(_UA_MODELS)
    app_ver = random.choice(_UA_APP_VERSIONS)
    internal_ver = random.choice(_UA_INTERNAL_VERSIONS)
    device_uuid = f"00000000-{uuid.uuid4().hex[4:8]}-{uuid.uuid4().hex[8:12]}-{uuid.uuid4().hex[12:16]}-{uuid.uuid4().hex[16:24]}"
    return _build_ua_pair(model, os_ver, app_ver, internal_ver, device_uuid)


def _generate_random_ua() -> str:
    """随机生成 vapp User-Agent：app_version;device_uuid;model;Android;os_version;Release;internal_version"""
    return _generate_ua_pair()[0]


def _generate_aged_device_id() -> str:
    """生成大潮老友/银龄视界(silver)端使用的 X-DEVICE-ID"""
    return f"00000000-{uuid.uuid4().hex[:4]}-{uuid.uuid4().hex[:4]}-0000-0000{uuid.uuid4().hex[:8]}"


def _mask_mobile(mobile: str) -> str:
    m = str(mobile or "")
    if len(m) < 7:
        return "*" * len(m)
    return f"{m[:3]}****{m[-4:]}"


def _env_int(name: str, default: int) -> int:
    raw = str(os.environ.get(name, "")).strip()
    if not raw:
        return default
    try:
        return int(raw)
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    raw = str(os.environ.get(name, "")).strip()
    if not raw:
        return default
    try:
        return float(raw)
    except Exception:
        return default


def _env_bool(name: str, default: bool = True) -> bool:
    raw = str(os.environ.get(name, "")).strip().lower()
    if not raw:
        return default
    return raw not in ("0", "false", "no", "off", "n")


def _mask_secret(value: str, keep_head: int = 4, keep_tail: int = 4) -> str:
    s = str(value or "")
    if not s:
        return ""
    if len(s) <= keep_head + keep_tail:
        return "*" * len(s)
    return f"{s[:keep_head]}...{s[-keep_tail:]}(len={len(s)})"


def _safe_json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _parse_tid_from_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    tid = (qs.get("tid") or [""])[0]
    return str(tid or "")


@dataclass
class NewDachaoAccountConfig:
    account_name: str
    phone_number: str
    password_encrypted: str
    tenant_id: str = "94"
    client_id: str = "10048"
    session_id: str = ""
    user_agent: str = ""
    webview_user_agent: str = ""
    passport_cookies: str = ""
    vapp_cookies: str = ""
    aihoge_cookies: str = ""
    redeem_member: str = ""
    redeem_cookies: str = ""
    redeem_user_agent: str = ""
    dc_openid: str = ""
    wx_server_url: str = ""
    passport_signature_salt: str = ""
    aihoge_signature_salt: str = ""
    sign_lottery_id: str = ""

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NewDachaoAccountConfig":
        phone_number = str(data.get("phone_number") or "").strip()
        account_name = (data.get("account_name") or phone_number or "未命名账号").strip()
        password_encrypted = str(data.get("password_encrypted") or data.get("password") or "").strip()
        user_agent, webview_user_agent = _generate_ua_pair()
        if not phone_number:
            raise ValueError(f"账号【{account_name}】缺少必填字段: phone_number")
        if not password_encrypted:
            raise ValueError(f"账号【{account_name}】缺少必填字段: password_encrypted")
        return cls(
            account_name=account_name,
            phone_number=phone_number,
            password_encrypted=password_encrypted,
            tenant_id=str(data.get("tenant_id") or "94"),
            client_id=str(data.get("client_id") or "10048"),
            session_id=str(data.get("session_id") or ""),
            user_agent=user_agent,
            webview_user_agent=webview_user_agent,
            passport_cookies=str(data.get("passport_cookies") or ""),
            vapp_cookies=str(data.get("vapp_cookies") or ""),
            aihoge_cookies=str(data.get("cookies") or data.get("aihoge_cookies") or ""),
            redeem_member=normalize_redeem_member(str(data.get("redeem_member") or data.get("withdraw_member") or "")),
            redeem_cookies=str(data.get("redeem_cookies") or ""),
            redeem_user_agent=str(data.get("redeem_user_agent") or ""),
            dc_openid=str(data.get("dc_openid") or "").strip(),
            wx_server_url=str(data.get("wx_server_url") or "").strip(),
            passport_signature_salt=str(data.get("passport_signature_salt") or ""),
            aihoge_signature_salt=str(data.get("aihoge_signature_salt") or ""),
            sign_lottery_id=str(data.get("sign_lottery_id") or ""),
        )


@dataclass
class DachaoLoginContext:
    session_id: str
    account_id: str
    tenant_id: str
    nick_name: str
    avatar_url: str
    mobile: str


class RsaEncryptor:
    def __init__(self) -> None:
        if serialization is not None:
            self._pubkey = serialization.load_pem_public_key(PUBLIC_KEY_PEM.encode("utf-8"))
        else:
            self._pubkey = None

    def encrypt_base64_pkcs1v15(self, plaintext: str) -> str:
        if self._pubkey is None or padding is None:
            raise RuntimeError("缺少依赖 cryptography，无法进行 RSA 加密；请先安装依赖。")
        data = plaintext.encode("utf-8")
        key_size_bytes = self._pubkey.key_size // 8
        max_len = key_size_bytes - 11
        if len(data) > max_len:
            raise ValueError(
                f"明文过长：{len(data)} 字节；当前 {self._pubkey.key_size} 位 RSA 在 PKCS#1 v1.5 下最多 {max_len} 字节。"
            )
        ct = self._pubkey.encrypt(data, padding.PKCS1v15())
        return base64.b64encode(ct).decode("ascii")


# 登录密码加密用的 RSA-1024 公钥（App 端 /web/oauth/credential_auth 专用）。
# 与上面 Aihoge 的 PUBLIC_KEY_PEM（2048 位）是两把不同的钥匙，不能混用。
LOGIN_RSA_PUBLIC_KEY_B64 = (
    "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQD6XO7e9YeAOs+cFqwa7ETJ+WXizPqQeXv68i5vqw9pFREsrqiBTRcg7wB0"
    "RIp3rJkDpaeVJLsZqYm5TW7FWx/iOiXFc+zCPvaKZric2dXCw27EvlH5rq+zwIPDAJHGAfnn1nmQH7wR3PCatEIb8pz5GFlTHMlluw4ZYmnOwg+thwIDAQAB"
)

_login_rsa_pubkey = None


def encrypt_login_password(plain_password: str) -> str:
    """把 dc_ck 里填写的【明文密码】用登录专用公钥做 RSA-PKCS#1 v1.5 加密。

    返回 base64 字符串，即 credential_auth 请求体里 password 字段的值。
    """
    global _login_rsa_pubkey
    if serialization is None or padding is None:
        raise RuntimeError("缺少依赖 cryptography，无法加密登录密码；请先安装依赖。")
    if _login_rsa_pubkey is None:
        _login_rsa_pubkey = serialization.load_der_public_key(
            base64.b64decode(LOGIN_RSA_PUBLIC_KEY_B64)
        )
    data = str(plain_password).encode("utf-8")
    max_len = _login_rsa_pubkey.key_size // 8 - 11
    if len(data) > max_len:
        raise ValueError(
            f"登录密码过长：{len(data)} 字节；当前 RSA-{_login_rsa_pubkey.key_size} "
            f"在 PKCS#1 v1.5 下最多 {max_len} 字节。"
        )
    ct = _login_rsa_pubkey.encrypt(data, padding.PKCS1v15())
    return base64.b64encode(ct).decode("ascii")


class VappSigner:
    def __init__(self, salt: str = VAPP_SIGNATURE_SALT):
        self.salt = salt

    @staticmethod
    def _request_id() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def _timestamp_ms() -> str:
        return str(int(time.time() * 1000))

    def signature(self, path: str, session_id: str, request_id: str, timestamp_ms: str, tenant_id: str) -> str:
        preimage = f"{path}&&{session_id}&&{request_id}&&{timestamp_ms}&&{self.salt}&&{tenant_id}"
        return hashlib.sha256(preimage.encode("utf-8")).hexdigest()


class TmuyunPassportClient:
    BASE_URL = "https://passport.tmuyun.com"

    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or requests.Session()
        self.session.trust_env = False

    @staticmethod
    def _build_signature_placeholder(path: str, request_id: str, salt: str = "") -> str:
        preimage = f"{path}&&{request_id}"
        if salt:
            preimage = f"{preimage}&&{salt}"
        return hashlib.sha256(preimage.encode("utf-8")).hexdigest()

    def credential_auth(
        self, phone_number: str, password_encrypted: str, client_id: str,
        user_agent: str, cookies: str = "", signature_salt: str = "",
        account_name: str = "", timeout: int = 30,
    ) -> str:
        path = "/web/oauth/credential_auth"
        url = f"{self.BASE_URL}{path}"
        request_id = str(uuid.uuid4())
        signature = self._build_signature_placeholder(path, request_id, salt=signature_salt)
        headers = {
            "User-Agent": user_agent,
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept-Language": "zh-CN,zh-Hans;q=0.9",
            "X-REQUEST-ID": request_id,
            "X-SIGNATURE": signature,
        }
        if cookies:
            headers["Cookie"] = cookies
        enc_password = encrypt_login_password(password_encrypted)
        data = {
            "client_id": client_id,
            "password": enc_password,
            "phone_number": phone_number,
        }
        payload = request_json(
            self.session, method="POST", url=url, headers=headers, data=data,
            timeout=timeout, account_name=account_name,
        )
        if payload.get("code") != 0:
            raise RuntimeError(f"credential_auth failed: {payload}")
        code = payload.get("data", {}).get("authorization_code", {}).get("code", "")
        if not code:
            raise RuntimeError(f"credential_auth missing authorization code: {payload}")
        return str(code)


class TmuyunVappClient:
    BASE_URL = "https://vapp.tmuyun.com"

    def __init__(self, session: Optional[requests.Session] = None, signer: Optional[VappSigner] = None):
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.signer = signer or VappSigner()

    @staticmethod
    def _random_session_seed() -> str:
        return uuid.uuid4().hex[:24]

    def _signed_headers(
        self, *, path: str, session_id: str, tenant_id: str,
        account_id: str = "", user_agent: str = "", cookies: str = "",
        extra: Optional[Dict[str, str]] = None,
    ) -> Dict[str, str]:
        request_id = self.signer._request_id()
        timestamp = self.signer._timestamp_ms()
        signature = self.signer.signature(path, session_id, request_id, timestamp, tenant_id)
        headers = {
            "User-Agent": user_agent,
            "X-TIMESTAMP": timestamp,
            "X-SESSION-ID": session_id,
            "X-SIGNATURE": signature,
            "X-TENANT-ID": tenant_id,
            "X-REQUEST-ID": request_id,
            "Accept-Language": "zh-Hans-CN;q=1, en-CN;q=0.9",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        if account_id:
            headers["X-ACCOUNT-ID"] = account_id
        if cookies:
            headers["Cookie"] = cookies
        if extra:
            headers.update(extra)
        return headers

    def account_init(
        self, tenant_id: str, user_agent: str,
        cookies: str = "", account_name: str = "", timeout: int = 30,
    ) -> str:
        """调用 /api/account/init 获取 session_id（vapp login 前置步骤）"""
        path = "/api/account/init"
        url = f"{self.BASE_URL}{path}"
        headers = self._signed_headers(
            path=path, session_id="", tenant_id=tenant_id, account_id="",
            user_agent=user_agent, cookies=cookies,
            extra={"X-Auth-Token": "", "YI-TOKEN": ""},
        )
        payload = request_json(
            self.session, method="POST", url=url, headers=headers,
            data=b"", timeout=timeout, account_name=account_name,
        )
        if payload.get("code") != 0:
            raise RuntimeError(f"account/init failed: {payload}")
        session_id = str((payload.get("data") or {}).get("session", {}).get("id") or "")
        if not session_id:
            raise RuntimeError(f"account/init missing session.id: {payload}")
        logger_api.info(f"[{account_name}] account/init 获取 session: {session_id}")
        return session_id

    def login_with_code(
        self, code: str, tenant_id: str, user_agent: str,
        cookies: str = "", session_seed: str = "", account_name: str = "", timeout: int = 30,
    ) -> DachaoLoginContext:
        path = "/api/zbtxz/login"
        url = f"{self.BASE_URL}{path}"
        seed = session_seed or self._random_session_seed()
        headers = self._signed_headers(
            path=path, session_id=seed, tenant_id=tenant_id, account_id="",
            user_agent=user_agent, cookies=cookies,
            extra={"X-Auth-Token": "", "YI-TOKEN": ""},
        )
        payload = request_json(
            self.session, method="POST", url=url, headers=headers,
            data={"code": code}, timeout=timeout, account_name=account_name,
        )
        if payload.get("code") != 0:
            raise RuntimeError(f"vapp login failed: {payload}")
        data = payload.get("data", {}) or {}
        session_info = data.get("session", {}) or {}
        account_info = data.get("account", {}) or {}
        session_id = str(session_info.get("id") or "")
        account_id = str(account_info.get("id") or "")
        nick_name = str(account_info.get("nick_name") or "")
        avatar_url = str(account_info.get("image_url") or "")
        mobile = str(account_info.get("mobile") or account_info.get("phone_number") or "")
        if not session_id or not account_id:
            raise RuntimeError(f"vapp login missing session/account: {payload}")
        return DachaoLoginContext(
            session_id=session_id, account_id=account_id, tenant_id=str(tenant_id),
            nick_name=nick_name, avatar_url=avatar_url, mobile=mobile,
        )

    def mypage_list(
        self, ctx: DachaoLoginContext, user_agent: str,
        cookies: str = "", account_name: str = "", timeout: int = 30,
    ) -> Dict[str, Any]:
        path = "/api/myPage/list"
        url = f"{self.BASE_URL}{path}"
        headers = self._signed_headers(
            path=path, session_id=ctx.session_id, tenant_id=ctx.tenant_id,
            account_id=ctx.account_id, user_agent=user_agent, cookies=cookies,
            extra={"X-Auth-Token": "", "YI-TOKEN": ""},
        )
        return request_json(
            self.session, method="GET", url=url, headers=headers,
            timeout=timeout, account_name=account_name,
        )

    def buoy_list(
        self, ctx: DachaoLoginContext, user_agent: str,
        cookies: str = "", account_name: str = "", timeout: int = 30,
    ) -> Dict[str, Any]:
        path = "/api/buoy/list"
        url = f"{self.BASE_URL}{path}"
        headers = self._signed_headers(
            path=path, session_id=ctx.session_id, tenant_id=ctx.tenant_id,
            account_id=ctx.account_id, user_agent=user_agent, cookies=cookies,
            extra={"X-Auth-Token": "", "YI-TOKEN": ""},
        )
        return request_json(
            self.session, method="GET", url=url, headers=headers,
            timeout=timeout, account_name=account_name,
        )

    def report_read_time(
        self, ctx: DachaoLoginContext, channel_article_id: str, read_time_ms: int,
        user_agent: str, cookies: str = "", account_name: str = "", timeout: int = 30,
        is_end: bool = True,
    ) -> Dict[str, Any]:
        path = "/api/article/read_time"
        url = f"{self.BASE_URL}{path}"
        params = {
            "channel_article_id": str(channel_article_id),
            "is_end": "true" if is_end else "false",
            "read_time": str(read_time_ms),
        }
        headers = self._signed_headers(
            path=path, session_id=ctx.session_id, tenant_id=ctx.tenant_id,
            account_id=ctx.account_id, user_agent=user_agent, cookies=cookies,
            extra={"X-Auth-Token": "", "YI-TOKEN": ""},
        )
        return request_json(
            self.session, method="GET", url=url, headers=headers, params=params,
            timeout=timeout, account_name=account_name,
        )

class AihogeClient:
    BASE_URL = "https://m.aihoge.com"

    def __init__(
        self, *, member_header: str, account_id: str, session_id: str,
        cookies: str = "", user_agent: str = "", redeem_member: str = "",
        redeem_cookies: str = "", redeem_user_agent: str = "", account_name: str = "",
        dc_openid: str = "", wx_server_url: str = "",
    ):
        self.session = requests.Session()
        self.session.trust_env = False
        self.member_header = member_header
        self.account_id = account_id
        self.session_id = session_id
        self.cookies = cookies
        self.user_agent = user_agent
        self.redeem_member = redeem_member
        self.redeem_cookies = redeem_cookies
        self.redeem_user_agent = redeem_user_agent
        self.account_name = account_name
        self.dc_openid = dc_openid
        self.wx_server_url = wx_server_url
        self._rsa = RsaEncryptor()
        self._captcha_verified = False
        self._redeem_fallback_logged = False
        self._gateway_member_cache: str = ""
        self._gateway_member_failures: int = 0
        self._redeemed_codes = set()  # type: ignore[var-annotated]

    def _common_headers(self, *, limit_id: str, referer_url: str) -> Dict[str, str]:
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json, text/plain, */*",
            "HTTP-X-H5-VERSION": "1",
            "X-CLIENT-VERSION": "1314",
            "X-DEVICE-SIGN": "xsb_hn",
            "X-DEVICE-ID": "000",
            "Limit": limit_id,
            "Referer": referer_url,
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Accept-Language": "zh-CN,zh-Hans;q=0.9",
            "accountId": self.account_id,
            "sessionId": self.session_id,
            "member": self.member_header,
        }
        if self.cookies:
            headers["Cookie"] = self.cookies
        return headers

    def sign_in(self, activity_id: str, sign_page_url: str) -> Dict[str, Any]:
        url = f"{self.BASE_URL}/api/signhy/client/actSign/actSign"
        payload = {"activity_id": activity_id, "timestamp": str(int(time.time()))}
        encrypted = self._rsa.encrypt_base64_pkcs1v15(_safe_json_dumps(payload))
        headers = self._common_headers(limit_id=activity_id, referer_url=sign_page_url)
        headers.update({"Content-Type": "application/json;charset=utf-8", "Origin": "https://m.aihoge.com"})
        return request_json(
            self.session, method="POST", url=url, headers=headers,
            json_body={"params": encrypted}, timeout=30, account_name=self.account_name,
        )

    def get_relation_lottery_list(self, sign_tid: str, sign_page_url: str) -> List[Dict[str, Any]]:
        """GET /api/signhy/client/actSign/getRelationCjList — 获取签到关联的抽奖活动列表

        返回 response 数组，每项含 hashid(抽奖活动ID)/title/mark/draw_num(剩余次数)。
        自动选取 draw_num > 0 的第一个抽奖活动 ID。
        """
        url = f"{self.BASE_URL}/api/signhy/client/actSign/getRelationCjList"
        params = {"activity_id": sign_tid}
        headers = self._common_headers(limit_id=sign_tid, referer_url=sign_page_url)
        resp = request_json(
            self.session, method="GET", url=url, headers=headers, params=params,
            timeout=30, account_name=self.account_name,
        )
        if resp.get("error_code") != 0:
            return []
        return resp.get("response") or []

    def get_sign_detail(self, sign_tid: str, sign_page_url: str) -> Dict[str, Any]:
        """GET /api/signhy/client/actSign/detail — 获取签到活动详情

        返回 response 字段，包含 member_sign_info（含 day_sign_num/continuous_sign_num）、
        member_draw_num（剩余抽奖次数）、relation_raffle 等。
        """
        url = f"{self.BASE_URL}/api/signhy/client/actSign/detail"
        params = {"activity_id": sign_tid}
        headers = self._common_headers(limit_id=sign_tid, referer_url=sign_page_url)
        resp = request_json(
            self.session, method="GET", url=url, headers=headers, params=params,
            timeout=30, account_name=self.account_name,
        )
        if resp.get("error_code") != 0:
            return {}
        return resp.get("response") or {}

    def get_news_list(self, news_tid: str, referer_url: str) -> Dict[str, Any]:
        url = f"{self.BASE_URL}/api/newshy/api/client/news/list/{news_tid}"
        headers = self._common_headers(limit_id=news_tid, referer_url=referer_url)
        return request_json(
            self.session, method="GET", url=url, headers=headers,
            timeout=30, account_name=self.account_name,
        )

    @staticmethod
    def extract_articles(news_list_resp: Dict[str, Any]) -> List[Dict[str, Any]]:
        data = news_list_resp.get("data", [])
        if not data:
            return []
        first = data[0] or {}
        limit = first.get("limit", {}) or {}
        column_set = limit.get("column_set", {}) or {}
        column_list = column_set.get("column_list", []) or []
        if not column_list:
            return []
        articles = (column_list[0] or {}).get("data", []) or []
        return [a for a in articles if isinstance(a, dict)]

    @staticmethod
    def extract_award_activity_id(news_list_resp: Dict[str, Any]) -> str:
        data = news_list_resp.get("data", [])
        if not data:
            return ""
        first = data[0] or {}
        limit = first.get("limit", {}) or {}
        rtc = limit.get("read_task_config", {}) or {}
        return str(rtc.get("awardActivityId") or "")

    def _read_article_internal(
        self, *, news_tid: str, item_id: str, referer_url: str,
        tn_x: Optional[int] = None, request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        url = f"{self.BASE_URL}/api/newshy/api/client/news/readArticle"
        payload: Dict[str, Any] = {"news_id": news_tid, "item_id": item_id, "timestamp": str(int(time.time()))}
        if tn_x is not None and request_id is not None:
            payload["tn_x"] = int(tn_x)
            payload["request_id"] = str(request_id)
        encrypted = self._rsa.encrypt_base64_pkcs1v15(_safe_json_dumps(payload))
        headers = self._common_headers(limit_id=news_tid, referer_url=referer_url)
        headers.update({
            "Content-Type": "application/json;charset=utf-8",
            "Origin": "https://m.aihoge.com",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        })
        return request_json(
            self.session, method="POST", url=url, headers=headers,
            json_body={"params": encrypted}, timeout=30, account_name=self.account_name,
        )

    def get_captcha(self, news_tid: str, referer_url: str) -> Dict[str, Any]:
        url = f"{self.BASE_URL}/api/newshy/api/client/news/getTnCode"
        headers = self._common_headers(limit_id=news_tid, referer_url=referer_url)
        headers["X-Requested-With"] = "XMLHttpRequest"
        return request_json(
            self.session, method="GET", url=url, headers=headers,
            params={"t": str(random.random())}, timeout=30, account_name=self.account_name,
        )

    def complete_read_task(self, news_tid: str, item_id: str, referer_url: str) -> Dict[str, Any]:
        if self._captcha_verified:
            return self._read_article_internal(news_tid=news_tid, item_id=item_id, referer_url=referer_url)
        result = self._read_article_internal(news_tid=news_tid, item_id=item_id, referer_url=referer_url)
        need_captcha = result.get("error_code") == "INVALID_CODE" or result.get("error_message") == "验证码错误"
        if not need_captcha:
            if result.get("success") == 1:
                self._captcha_verified = True
            return result
        if np is None or Image is None:
            logger_api.warning("缺少滑块验证码偏移量计算能力（numpy/Pillow），无法自动处理验证码")
            return result
        captcha = self.get_captcha(news_tid=news_tid, referer_url=referer_url)
        request_id = captcha.get("request_id")
        img_url = captcha.get("img")
        if not request_id or not img_url:
            return result
        tn_x = calculate_slide_offset(img_url)
        if tn_x is None:
            return result
        time.sleep(random.uniform(3, 5))
        result = self._read_article_internal(
            news_tid=news_tid, item_id=item_id, referer_url=referer_url, tn_x=tn_x, request_id=request_id
        )
        if result.get("success") == 1:
            self._captcha_verified = True
        return result

    @staticmethod
    def extract_channel_article_id(link: str) -> str:
        if not link:
            return ""
        try:
            parsed = urlparse(link)
            params = parse_qs(parsed.query)
            return str((params.get("id") or [""])[0] or "")
        except Exception:
            return ""

    def get_lottery_info(self, lottery_id: str, limit_id: str, referer_url: str) -> Dict[str, Any]:
        url = f"{self.BASE_URL}/api/lotteryhy/designh5/client/activity/{lottery_id}"
        headers = self._common_headers(limit_id=limit_id, referer_url=referer_url)
        return request_json(
            self.session, method="GET", url=url, headers=headers,
            timeout=30, account_name=self.account_name,
        )

    def get_remain_counts(self, lottery_id: str, limit_id: str, referer_url: str) -> int:
        data = self.get_lottery_info(lottery_id=lottery_id, limit_id=limit_id, referer_url=referer_url)
        try:
            return int((data.get("response") or {}).get("remain_counts") or 0)
        except Exception:
            return 0

    def draw_lottery(self, lottery_id: str, limit_id: str, referer_url: str) -> Dict[str, Any]:
        url = f"{self.BASE_URL}/api/lotteryhy/api/client/cj/awd/drw/{lottery_id}"
        headers = self._common_headers(limit_id=limit_id, referer_url=referer_url)
        headers.update({
            "Content-Type": "application/json;charset=utf-8",
            "Origin": "https://m.aihoge.com",
            "Content-Length": "0",
        })
        return request_json(
            self.session, method="POST", url=url, headers=headers,
            timeout=30, account_name=self.account_name,
        )

    def _ensure_redeem_member(self, code: str) -> str:
        """获取兑换用微信会员身份（通过应用宝网关自动获取）。"""
        if not self.dc_openid or not self.wx_server_url:
            if not self._redeem_fallback_logged:
                self._redeem_fallback_logged = True
                logger_api.warning(
                    f"[{self.account_name}] 未配置 dc_openid/wx_server_url，无法兑换红包；"
                    "请配置 dc_openid + wx_server_url 自动获取微信会员"
                )
            return ""
        if self._gateway_member_cache:
            return self._gateway_member_cache
        if self._gateway_member_failures >= 3:
            logger_api.warning(
                f"[{self.account_name}] 应用宝网关获取微信会员已连续失败 "
                f"{self._gateway_member_failures} 次，跳过自动获取"
            )
            return ""
        try:
            member_header, _info = fetch_wechat_member_via_gateway(
                self.wx_server_url, WECHAT_APPID, self.dc_openid,
                redeem_code=code, account_name=self.account_name,
            )
            self._gateway_member_cache = member_header
            self._gateway_member_failures = 0
            return member_header
        except Exception as e:
            self._gateway_member_failures += 1
            logger_api.warning(f"[{self.account_name}] 应用宝网关获取微信会员失败({self._gateway_member_failures}): {e}")
            return ""

    def redeem_red_packet(self, code: str) -> Dict[str, Any]:
        if not code:
            return {"success": False, "error": "missing_code"}
        # 红包领取页要求微信侧会员（source=wechat），通过应用宝网关自动获取
        member_header = self._ensure_redeem_member(code)
        if not member_header:
            return {"success": False, "error": "missing_redeem_member"}
        url = f"{self.BASE_URL}/api/lotteryhy/api/client/cj/send/pak"
        headers: Dict[str, str] = {
            "Accept": "application/json, text/plain, */*",
            "HTTP-X-H5-VERSION": "1",
            "Limit": "default",
            "X-DEVICE-SIGN": "wechat",
            "X-DEVICE-ID": "000",
            "X-CLIENT-VERSION": "1314",
            "Origin": "https://m.aihoge.com",
            "Content-Type": "application/json;charset=utf-8",
            "X-Requested-With": "com.tencent.mm",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Accept-Language": "zh-CN,zh-Hans;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Referer": (
                "https://m.aihoge.com/lottery/awardBonus/drawRedPacket"
                "?title=%E9%A2%86%E5%8F%96%E7%BA%A2%E5%8C%85"
                f"&code={code}"
            ),
            "User-Agent": self.redeem_user_agent or DEFAULT_REDEEM_UA,
            "member": member_header,
        }
        cookie = self.redeem_cookies or self.cookies
        if cookie:
            headers["Cookie"] = cookie
        return request_json(
            self.session, method="POST", url=url, headers=headers,
            json_body={"code": code}, timeout=30, account_name=self.account_name,
        )

    def redeem_red_packet_with_retry(
        self, code: str, *, max_attempts: int = 3,
        min_delay_s: float = 1.5, max_delay_s: float = 3.5,
    ) -> Dict[str, Any]:
        attempts = max(1, int(max_attempts))
        last_resp: Dict[str, Any] = {}
        for idx in range(1, attempts + 1):
            resp = self.redeem_red_packet(code)
            last_resp = resp
            meta = interpret_redeem_response(resp)
            if meta["ok"]:
                return resp
            code_text = str(meta.get("code") or "")
            message = str(meta.get("message") or "")
            should_retry = code_text == "428" or "无效兑换码" in message
            if not should_retry or idx >= attempts:
                return resp
            delay = random.uniform(min_delay_s, max_delay_s)
            if self.account_name:
                logger_api.info(f"[{self.account_name}] 兑换码暂不可用，{delay:.2f} 秒后重试({idx}/{attempts})")
            time.sleep(delay)
        return last_resp

    def get_my_prizes(self, lottery_id: str, limit_id: str, referer_url: str) -> List[Dict[str, Any]]:
        """GET /api/lotteryhy/api/client/cj/my/prize/info/{id} — 我的奖品（含未兑奖红包）"""
        url = f"{self.BASE_URL}/api/lotteryhy/api/client/cj/my/prize/info/{lottery_id}"
        headers = self._common_headers(limit_id=limit_id, referer_url=referer_url)
        resp = request_json(
            self.session, method="GET", url=url, headers=headers,
            params={"page": "1", "count": "100"}, timeout=30, account_name=self.account_name,
        )
        return resp.get("data") or []

    def redeem_prize_code(self, code: str, prize_desc: str) -> str:
        """兑换红包码，并把结果拼进奖品描述（同一兑换码本次运行只兑一次）"""
        code = str(code or "").strip()
        if not code:
            return f"{prize_desc} (无兑换码)"
        if code in self._redeemed_codes:
            if self.account_name:
                logger_api.info(f"[{self.account_name}] 兑换码本次已处理过，跳过重复兑换")
            return f"{prize_desc} (已兑换)"
        self._redeemed_codes.add(code)
        logger_api.info(f"[{self.account_name}] 获取到现金红包兑换码，准备自动兑换...")
        resp = self.redeem_red_packet_with_retry(code)
        meta = interpret_redeem_response(resp)
        if meta["ok"]:
            return f"{prize_desc} ({'已领取' if meta['already_received'] else '已兑换'})"
        err = str(meta.get("message") or "未知错误")
        code_text = str(meta.get("code") or "")
        suffix = f"{code_text}:{err}" if code_text else err
        return f"{prize_desc} (兑换失败: {suffix})"

    def redeem_pending_prizes(self, lottery_id: str, limit_id: str, referer_url: str) -> List[str]:
        """补兑历史未领取的现金红包（status=1 已抽中 且 type=3 且未过期）"""
        results: List[str] = []
        try:
            prizes = self.get_my_prizes(lottery_id=lottery_id, limit_id=limit_id, referer_url=referer_url)
        except Exception as e:
            logger_api.warning(f"[{self.account_name}] 查询未兑奖红包失败: {e}")
            return results
        now_ts = int(time.time())
        for item in prizes:
            if not isinstance(item, dict):
                continue
            if str(item.get("prize_type")) != "3" or str(item.get("status")) != "1":
                continue
            try:
                if int(item.get("end_time") or 0) < now_ts:
                    continue
            except Exception:
                pass
            info = item.get("prize_info") or {}
            code = str(info.get("code") or "").strip()
            if not code or code in self._redeemed_codes:
                continue
            desc = str(item.get("prize_content") or info.get("award_content") or "现金红包")
            try:
                results.append(f"补兑-{self.redeem_prize_code(code, desc)}")
            except Exception as e:
                results.append(f"补兑-{desc} (兑换失败: {e})")
            time.sleep(random.uniform(1.0, 2.5))
        return results

    @staticmethod
    def parse_lottery_result(result: Dict[str, Any]) -> str:
        if result.get("error"):
            return f"抽奖失败: {result.get('error')}"
        award_name = result.get("award_name", "未知奖品")
        award_content = result.get("award_content", "") or award_name
        award_type = result.get("type", 0)
        if award_type == 5:
            points = result.get("prize_integral", 0)
            return f"{award_content} (+{points}积分)"
        if award_type == 3:
            money = result.get("money", 0)
            return f"{award_content} (+{money}元)"
        return award_content


class AihogeMemberBuilder:
    BASE_URL = "https://m.aihoge.com"

    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or requests.Session()
        self.session.trust_env = False

    @staticmethod
    def _signature_placeholder(account_id: str, session_id: str, timestamp_ms: str, sign: str, salt: str = "") -> str:
        preimage = f"{account_id}&&{session_id}&&{timestamp_ms}&&{sign}"
        if salt:
            preimage = f"{preimage}&&{salt}"
        return hashlib.sha256(preimage.encode("utf-8")).hexdigest()

    def build_member(
        self, *, ctx: DachaoLoginContext, sign_tid: str, sign_page_url: str,
        user_agent: str, cookies: str = "", signature_salt: str = "",
        account_name: str = "", timeout: int = 30,
    ) -> Tuple[str, Dict[str, Any]]:
        url = f"{self.BASE_URL}/api/memberhy/tm/signature"
        timestamp_ms = str(int(time.time() * 1000))
        signature = self._signature_placeholder(
            account_id=ctx.account_id, session_id=ctx.session_id,
            timestamp_ms=timestamp_ms, sign="xsb_hn", salt=signature_salt,
        )
        payload = {
            "mobile": "1",
            "accountId": ctx.account_id,
            "user": {
                "id": ctx.account_id,
                "realname": "",
                "image_url": ctx.avatar_url,
                "nick_name": ctx.nick_name,
                "idcard": "",
                "is_face_verify": False,
            },
            "signature": signature,
            "sessionId": ctx.session_id,
            "timestamp": timestamp_ms,
            "login": "1",
            "sign": "xsb_hn",
        }
        headers = {
            "User-Agent": user_agent,
            "Accept": "application/json, text/plain, */*",
            "HTTP-X-H5-VERSION": "1",
            "Limit": sign_tid,
            "Referer": sign_page_url,
            "X-DEVICE-SIGN": "xsb_hn",
            "Origin": "https://m.aihoge.com",
            "X-DEVICE-ID": "000",
            "X-CLIENT-VERSION": "1314",
            "Accept-Language": "zh-CN,zh-Hans;q=0.9",
            "Content-Type": "application/json;charset=utf-8",
        }
        if cookies:
            headers["Cookie"] = cookies
        member_info = request_json(
            self.session, method="POST", url=url, headers=headers,
            json_body=payload, timeout=timeout, account_name=account_name,
        )
        member_header = {
            "id": member_info.get("id", ""),
            "black": int(member_info.get("black", 0) or 0),
            "btoken": member_info.get("btoken", ""),
            "expire": int(member_info.get("expire", 0) or 0),
            "token": member_info.get("token", ""),
            "source": member_info.get("source", "xsb_hn"),
            "mobile": member_info.get("mobile", ctx.mobile),
            "mark": member_info.get("mark", ctx.mobile),
            "mtoken": member_info.get("mtoken", ""),
            "stoken": member_info.get("stoken", ""),
            "nick_name": quote(str(member_info.get("nick_name") or ctx.nick_name), safe=""),
            "avatar": member_info.get("avatar", ctx.avatar_url),
        }
        return _safe_json_dumps(member_header), member_info


# ===========================================================================
# 大潮老友/银龄视界（aged.tmuyun.com/silver）—— 看视频抽好礼
#
# 活动规则：进入大潮APP「大潮老友/银龄视界」频道看视频，累计观看 5/15/30/45/60 分钟
#           各可开启 1 次抽奖机会（每档对应一个独立的 aihoge 抽奖活动）。
#
# 链路：
#   1. POST /aged/h5/tenant/host/info   -> tenantPromptConfig（5 档：duration/prompt/activityId/link）
#   2. POST /aged/h5/tenant/userInfo    -> 绑定 App 登录态（accountId + sessionId）
#   3. POST /aged/h5/agedDrama/video/detail         -> 取剧集信息 + userDayDuration（今日已看秒数）
#   4. POST /aged/h5/agedDrama/watch/record/report  -> 上报观看时长（body = {"data": AES-CBC(<明文>)}）
#   5. POST /aged/h5/activity/lottery/add {tenantPromptConfigIndex} -> {prompt, link, taskCompleted}
#   6. 用 link 里的 tid 走 aihoge 抽奖（remain_counts / awd/drw）
# ===========================================================================

AGED_BASE_URL = "https://aged.tmuyun.com"

# 观看时长上报的 AES-128-CBC 密钥/IV（逆向自 silver/assets/index-90a7a6eb.js 的 Wv()）
AGED_AES_KEY = b"0EoJUm6Ayw8W8jud"
AGED_AES_IV = b"0102530405460708"

# 单条上报的观看秒数（App 端按 apiRateLimit=12 秒攒够一次就上报）
DEFAULT_WATCH_CHUNK_SEC = 12
# 两次上报之间的真实等待秒数。
# 注意：aged.tmuyun.com 走阿里云 WAF，上报频率过高会被判定为机器人并返回拦截页
# （HTTP 200 但响应体是带 aliyun_waf_aa/aliyun_waf_bb 的 HTML，不是 JSON）。
# 实测 0.2~3 秒/次会被拦，12 秒/次（与 App 原生节奏一致）长期稳定。
DEFAULT_WATCH_SLEEP_SEC = 12
# 命中 WAF 拦截后的退避与重试
DEFAULT_WATCH_WAF_BACKOFF_SEC = 60
DEFAULT_WATCH_WAF_RETRY = 3
# 单档补看最多几轮（服务端计数有滞后/丢失，需按新缺口逐轮补）
WATCH_REFILL_ROUNDS = 5
# 第 2 轮起的上报余量系数：缺口按认账率 k 等比缩小，纯补看收敛不到目标，
# 需满足 m ≥ 1/k；实测 k≈0.8，故取 1.3 留安全边际。
WATCH_REFILL_MARGIN = 1.3
# 补看后等待服务端落库的秒数（先等一轮读，未达标再等一轮复读取最大值）
WATCH_SETTLE_SHORT_SEC = 8
WATCH_SETTLE_LONG_SEC = 10
# 单日最多模拟观看的分钟数（60 分钟 = 全部 5 档）
DEFAULT_WATCH_MAX_MINUTES = 60


def _is_waf_blocked(text: str) -> bool:
    """判断响应是否为阿里云 WAF 拦截页（HTTP 200 但返回 HTML 而非 JSON）"""
    head = (text or "").lstrip()[:300].lower()
    if not head:
        return True
    return "aliyun_waf" in head or head.startswith("<!doctype html") or head.startswith("<html")


def _aged_encrypt_report(plaintext: str) -> str:
    """把观看上报明文加密成 {"data": ...} 需要的 base64 字符串。

    明文格式（空格分隔，与 App 端一致）：
        {dramaId} {videoIndex} {progressSec} {durationSec} {timestampMs}
    算法：AES-128-CBC / PKCS7 / key=0EoJUm6Ayw8W8jud / iv=0102530405460708
    """
    if Cipher is None or algorithms is None or modes is None:
        raise RuntimeError("缺少依赖 cryptography，无法加密观看时长上报；请先安装依赖。")
    raw = plaintext.encode("utf-8")
    pad_len = 16 - (len(raw) % 16)
    encryptor = Cipher(algorithms.AES(AGED_AES_KEY), modes.CBC(AGED_AES_IV)).encryptor()
    ct = encryptor.update(raw + bytes([pad_len]) * pad_len) + encryptor.finalize()
    return base64.b64encode(ct).decode("ascii")


class AgedClient:
    """大潮老友/银龄视界 H5 接口客户端。

    鉴权：X-APP-TENANT-ID(94) + X-USER-ID(xsb_{tenantId}_{accountId})，无需 member 头。
    """

    BASE_URL = AGED_BASE_URL

    def __init__(
        self, *, tenant_id: str, account_id: str, session_id: str,
        user_agent: str, device_id: str = "", account_name: str = "",
    ):
        self.session = requests.Session()
        self.session.trust_env = False
        self.tenant_id = str(tenant_id)
        self.account_id = str(account_id)
        self.session_id = str(session_id)
        self.user_agent = user_agent
        self.device_id = device_id or _generate_aged_device_id()
        self.account_name = account_name
        self.version = "1.0.7"

    # ---------- 基础请求 ----------

    @property
    def home_url(self) -> str:
        return f"{self.BASE_URL}/silver/home?tenantId={self.tenant_id}&gaze_open=2"

    def play_url(self, drama_id: Any) -> str:
        return (
            f"{self.BASE_URL}/silver/swipePlayPage?id={drama_id}"
            f"&tenantId={self.tenant_id}&gaze_control=022&gaze_open=1"
        )

    def _headers(self, referer: str, *, json_content: bool = True) -> Dict[str, str]:
        headers = {
            "Host": "aged.tmuyun.com",
            "Connection": "keep-alive",
            "X-APP-TENANT-ID": self.tenant_id,
            "X-USER-ID": f"xsb_{self.tenant_id}_{self.account_id}",
            "X-DEVICE-ID": self.device_id,
            "User-Agent": self.user_agent,
            "Accept": "application/json, text/plain, */*",
            "Origin": self.BASE_URL,
            "X-Requested-With": "com.hoge.android.app.dachao",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Referer": referer,
            "Accept-Language": "zh-CN,zh;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        if json_content:
            headers["Content-Type"] = "application/json"
        return headers

    def host_info(self) -> Dict[str, Any]:
        """POST /aged/h5/tenant/host/info —— 租户配置（含 tenantPromptConfig 抽奖档位）"""
        url = f"{self.BASE_URL}/aged/h5/tenant/host/info"
        resp = request_json(
            self.session, method="POST", url=url, headers=self._headers(self.home_url),
            json_body={"appTenantId": self.tenant_id}, timeout=30, account_name=self.account_name,
        )
        data = resp.get("data") or {}
        if isinstance(data, dict):
            self.version = str(data.get("version") or self.version)
        return data

    def bind_user_info(self) -> Dict[str, Any]:
        """POST /aged/h5/tenant/userInfo —— 把 App 登录态同步给 H5"""
        url = f"{self.BASE_URL}/aged/h5/tenant/userInfo"
        return request_json(
            self.session, method="POST", url=url, headers=self._headers(self.home_url),
            json_body={"accountId": self.account_id, "sessionId": self.session_id},
            timeout=30, account_name=self.account_name,
        )

    def user_day_log(self, day: int = 1) -> List[Dict[str, Any]]:
        """POST /aged/h5/activity/userDayLog —— 每日观看分钟数"""
        url = f"{self.BASE_URL}/aged/h5/activity/userDayLog"
        resp = request_json(
            self.session, method="POST", url=url, headers=self._headers(self.home_url),
            json_body={"day": int(day)}, timeout=30, account_name=self.account_name,
        )
        return resp.get("data") or []

    def homepage_index(self) -> Dict[str, Any]:
        """POST /aged/h5/homepage/index —— 首页（含 favoriteList 剧集）"""
        url = f"{self.BASE_URL}/aged/h5/homepage/index"
        resp = request_json(
            self.session, method="POST", url=url, headers=self._headers(self.home_url),
            data=b"", timeout=30, account_name=self.account_name,
        )
        return resp.get("data") or {}

    def latest_online(self, size: int = 10) -> List[Dict[str, Any]]:
        """POST /aged/h5/homepage/latest/online —— 最新上线剧集列表"""
        url = f"{self.BASE_URL}/aged/h5/homepage/latest/online"
        resp = request_json(
            self.session, method="POST", url=url, headers=self._headers(self.home_url),
            json_body={"size": int(size)}, timeout=30, account_name=self.account_name,
        )
        return ((resp.get("data") or {}).get("list")) or []

    def video_detail(self, drama_id: Any) -> Dict[str, Any]:
        """POST /aged/h5/agedDrama/video/detail —— 剧集详情（含 userDayDuration 今日已看秒数）"""
        url = f"{self.BASE_URL}/aged/h5/agedDrama/video/detail"
        resp = request_json(
            self.session, method="POST", url=url,
            headers=self._headers(self.play_url(drama_id)),
            json_body={"dramaId": str(drama_id)}, timeout=30, account_name=self.account_name,
        )
        return resp.get("data") or {}

    def watch_report(
        self, drama_id: Any, video_index: int, progress_sec: int, duration_sec: int,
        *, timestamp_ms: Optional[int] = None, version: str = "",
        waf_retry: int = DEFAULT_WATCH_WAF_RETRY,
        waf_backoff: float = DEFAULT_WATCH_WAF_BACKOFF_SEC,
    ) -> Dict[str, Any]:
        """POST /aged/h5/agedDrama/watch/record/report —— 上报一段观看时长。

        该接口是高频接口，触发阿里云 WAF 风控时会返回拦截页（HTTP 200 + HTML）。
        这里识别拦截页并退避重试，避免整个任务因 JSONDecodeError 中断；
        重试仍失败时返回 {"code": -1, "message": "waf_blocked"}，由调用方决定是否继续。
        """
        url = f"{self.BASE_URL}/aged/h5/agedDrama/watch/record/report"
        ts = int(timestamp_ms or (time.time() * 1000))
        plaintext = f"{drama_id} {int(video_index)} {int(progress_sec)} {int(duration_sec)} {ts}"
        params = {"version": version or self.version}
        body = {"data": _aged_encrypt_report(plaintext)}
        backoff = max(1.0, float(waf_backoff))
        for attempt in range(max(0, int(waf_retry)) + 1):
            resp = self.session.post(
                url, headers=self._headers(self.play_url(drama_id)),
                params=params, json=body, timeout=30,
            )
            text = resp.text or ""
            if not _is_waf_blocked(text):
                return _try_parse_json(text) or {}
            if attempt >= waf_retry:
                break
            logger_api.warning(
                f"[{self.account_name}] 观看时长上报被 WAF 拦截，{backoff:.0f} 秒后重试"
                f"({attempt + 1}/{waf_retry})"
            )
            time.sleep(backoff)
            backoff = min(backoff * 1.5, 300.0)
        return {"code": -1, "message": "waf_blocked"}

    def lottery_add(self, index: int) -> Dict[str, Any]:
        """POST /aged/h5/activity/lottery/add —— 达成观看档位后开通抽奖机会

        返回 data: {prompt, link, taskCompleted}
        """
        url = f"{self.BASE_URL}/aged/h5/activity/lottery/add"
        resp = request_json(
            self.session, method="POST", url=url,
            headers=self._headers(self.play_url(0)),
            json_body={"tenantPromptConfigIndex": int(index)},
            timeout=30, account_name=self.account_name,
        )
        return resp.get("data") or {}

    def lottery_log(self, index: int, payload: Dict[str, Any]) -> None:
        """POST /aged/h5/activity/consecutiveDaysLottery/log —— App 端行为日志（失败不影响主流程）"""
        url = f"{self.BASE_URL}/aged/h5/activity/consecutiveDaysLottery/log"
        try:
            request_json(
                self.session, method="POST", url=url,
                headers=self._headers(self.play_url(0)),
                json_body={
                    "logTime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    "userId": self.account_id,
                    "data": _safe_json_dumps(payload),
                    "api": "/activity/lottery/add",
                },
                timeout=15, account_name=self.account_name,
            )
        except Exception:
            pass


def discover_sign_page_and_tid(mypage_resp: Dict[str, Any]) -> Tuple[str, str]:
    data = (mypage_resp.get("data") or {}) if isinstance(mypage_resp, dict) else {}
    new_list = (data.get("new_list") or {}) if isinstance(data, dict) else {}
    records = (new_list.get("records") or []) if isinstance(new_list, dict) else []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        if rec.get("list_type") == 9 or rec.get("list_title") == "签到" or rec.get("doc_title") == "签到":
            url = str(rec.get("url") or rec.get("web_view_url") or "")
            tid = _parse_tid_from_url(url)
            if url and tid:
                return url, tid
    return "", ""


def discover_news_read_tid(buoy_resp: Dict[str, Any]) -> Tuple[str, str]:
    data = (buoy_resp.get("data") or {}) if isinstance(buoy_resp, dict) else {}
    new_down = (data.get("new_down") or {}) if isinstance(data, dict) else {}
    icon_list = (new_down.get("icon_list") or []) if isinstance(new_down, dict) else []
    for icon in icon_list:
        if not isinstance(icon, dict):
            continue
        turn_to = icon.get("turn_to") or {}
        if not isinstance(turn_to, dict):
            continue
        entry = str(turn_to.get("entryLink") or turn_to.get("url") or "")
        tid = _parse_tid_from_url(entry)
        if entry and tid:
            return entry, tid
    return "", ""


def login_build_clients(
    cfg: NewDachaoAccountConfig, *, account_name: str = ""
) -> Tuple[str, DachaoLoginContext, str, str, str, AihogeClient]:
    passport = TmuyunPassportClient()
    vapp = TmuyunVappClient()
    name = account_name or cfg.account_name
    logger_api.info(f"[{name}] 开始登录：{_mask_mobile(cfg.phone_number)}")

    auth_code = passport.credential_auth(
        phone_number=cfg.phone_number, password_encrypted=cfg.password_encrypted,
        client_id=cfg.client_id, user_agent=cfg.user_agent,
        cookies=cfg.passport_cookies, signature_salt=cfg.passport_signature_salt,
        account_name=name,
    )
    anon_session = vapp.account_init(
        tenant_id=cfg.tenant_id, user_agent=cfg.user_agent,
        cookies=cfg.vapp_cookies, account_name=name,
    )
    ctx = vapp.login_with_code(
        code=auth_code, tenant_id=cfg.tenant_id, user_agent=cfg.user_agent,
        cookies=cfg.vapp_cookies, session_seed=anon_session, account_name=name,
    )
    mypage = vapp.mypage_list(ctx, user_agent=cfg.user_agent, cookies=cfg.vapp_cookies, account_name=name)
    sign_page_url, sign_tid = discover_sign_page_and_tid(mypage)
    if not sign_tid:
        raise RuntimeError(f"[{name}] 无法从 myPage/list 提取签到 tid: {mypage}")
    logger_api.info(f"[{name}] 获取签到活动tid: {sign_tid}")
    logger_api.info(f"[{name}] 签到页面: {sign_page_url}")

    buoy = vapp.buoy_list(ctx, user_agent=cfg.user_agent, cookies=cfg.vapp_cookies, account_name=name)
    news_entry_url, news_tid = discover_news_read_tid(buoy)
    if news_tid:
        logger_api.info(f"[{name}] 获取阅读有礼tid: {news_tid}")
        logger_api.info(f"[{name}] 阅读有礼入口: {news_entry_url}")
    else:
        logger_api.info(f"[{name}] 未找到阅读有礼入口（可能账号无该浮标/活动未开启）")

    member_builder = AihogeMemberBuilder()
    member_header, _raw_member = member_builder.build_member(
        ctx=ctx, sign_tid=sign_tid,
        sign_page_url=sign_page_url or f"https://m.aihoge.com/h5?mark=sign@designh5&tid={sign_tid}&path=preview",
        user_agent=cfg.user_agent, cookies=cfg.aihoge_cookies,
        signature_salt=cfg.aihoge_signature_salt, account_name=name,
    )
    try:
        member_obj = json.loads(member_header)
        member_id = _mask_secret(member_obj.get("id", ""))
        expire = member_obj.get("expire", "")
        logger_api.info(f"[{name}] 构建member成功: id={member_id} expire={expire}")
    except Exception:
        logger_api.info(f"[{name}] 构建member成功")

    aihoge = AihogeClient(
        member_header=member_header, account_id=ctx.account_id, session_id=ctx.session_id,
        cookies=cfg.aihoge_cookies, user_agent=cfg.user_agent,
        redeem_member=cfg.redeem_member, redeem_cookies=cfg.redeem_cookies,
        redeem_user_agent=cfg.redeem_user_agent, account_name=name,
        dc_openid=cfg.dc_openid, wx_server_url=cfg.wx_server_url,
    )
    return auth_code, ctx, sign_page_url, sign_tid, news_tid, aihoge


def run_sign_flow(aihoge: AihogeClient, *, sign_tid: str, sign_page_url: str) -> Dict[str, Any]:
    return aihoge.sign_in(activity_id=sign_tid, sign_page_url=sign_page_url)


def run_sign_lottery_flow(
    aihoge: AihogeClient, *, sign_tid: str, sign_page_url: str, sign_lottery_id: str,
) -> Dict[str, Any]:
    if not sign_lottery_id:
        return {"lottery_id": "", "lottery_count": 0, "lottery_results": []}
    referer_url = sign_page_url or f"https://m.aihoge.com/h5?mark=sign@designh5&tid={sign_tid}&path=index&autoSign=true"
    remain = 0
    try:
        remain = aihoge.get_remain_counts(lottery_id=sign_lottery_id, limit_id=sign_tid, referer_url=referer_url)
    except Exception:
        remain = 0
    results: List[str] = []
    # 先补兑历史未领红包，再抽奖：避免刚抽中的红包尚未落库又被补兑一次（同一码重复兑换）
    results.extend(aihoge.redeem_pending_prizes(
        lottery_id=sign_lottery_id, limit_id=sign_tid, referer_url=referer_url,
    ))
    for i in range(remain):
        try:
            res = aihoge.draw_lottery(lottery_id=sign_lottery_id, limit_id=sign_tid, referer_url=referer_url)
            prize_desc = aihoge.parse_lottery_result(res)
            if str(res.get("type")) == "3":
                prize_desc = aihoge.redeem_prize_code(str(res.get("code") or ""), prize_desc)
            results.append(prize_desc)
        except Exception as e:
            results.append(f"抽奖失败: {e}")
        if i < remain - 1:
            time.sleep(random.uniform(1.0, 3.0))
    return {"lottery_id": sign_lottery_id, "lottery_count": remain, "lottery_results": results}


def run_read_flow(
    aihoge: AihogeClient, vapp: TmuyunVappClient, ctx: DachaoLoginContext, *,
    news_tid: str, news_entry_url: str, vapp_user_agent: str, vapp_cookies: str = "",
    read_delay_range_s: Tuple[float, float] = (20.0, 30.0),
    sleep_enabled: bool = True, account_name: str = "",
) -> Dict[str, Any]:
    if not news_tid:
        return {"total": 0, "completed": 0, "lottery_id": "", "lottery_count": 0, "lottery_results": []}
    referer_url = news_entry_url or f"https://m.aihoge.com/h5?mark=news-read@designh5&tid={news_tid}&path=index"
    news_list = aihoge.get_news_list(news_tid=news_tid, referer_url=referer_url)
    articles = aihoge.extract_articles(news_list)
    award_activity_id = aihoge.extract_award_activity_id(news_list)
    if account_name and award_activity_id:
        logger_api.info(f"[{account_name}] 阅读抽奖活动ID: {award_activity_id}")
    candidates = []
    for a in articles:
        if a.get("is_read") == 1:
            continue
        candidates.append(a)
    completed = 0
    if account_name:
        if not candidates:
            logger_api.info(f"[{account_name}] 没有未完成的阅读任务")
        else:
            logger_api.info(f"[{account_name}] 发现 {len(candidates)} 个未完成的阅读任务")
    for idx, a in enumerate(candidates, 1):
        item_id = str(a.get("item_id") or "")
        title = str(a.get("title") or "")
        link = str(a.get("link") or a.get("link_url") or "")
        if not item_id:
            continue
        if account_name:
            logger_api.info(f"[{account_name}] 正在执行阅读任务 ({idx}/{len(candidates)}): {title[:30]}...")
        resp = aihoge.complete_read_task(news_tid=news_tid, item_id=item_id, referer_url=referer_url)
        if resp.get("success") == 1:
            completed += 1
            if account_name:
                task_turn = resp.get("task_turn")
                if task_turn is not None:
                    logger_api.info(f"[{account_name}] 阅读任务完成成功，当前进度: {task_turn}")
                else:
                    logger_api.info(f"[{account_name}] 阅读任务完成成功")
            delay_s = max(0.0, random.uniform(*read_delay_range_s))
            delay_ms = max(1000, int(delay_s * 1000))
            if sleep_enabled and delay_s > 0:
                if account_name:
                    logger_api.info(f"[{account_name}] 等待 {delay_s:.2f} 秒后上报阅读时间...")
                time.sleep(delay_s)
            else:
                delay_ms = 1000
            channel_article_id = aihoge.extract_channel_article_id(link)
            if channel_article_id:
                try:
                    vapp.report_read_time(
                        ctx=ctx, channel_article_id=channel_article_id, read_time_ms=delay_ms,
                        user_agent=vapp_user_agent, cookies=vapp_cookies, account_name=account_name,
                    )
                except Exception:
                    pass
        else:
            error_code = resp.get("error_code", "")
            delay = random.uniform(2.0, 5.0)
            if account_name and error_code == "click_quick":
                logger_api.info(f"[{account_name}] 请求过快，等待 {delay:.2f} 秒后继续...")
            time.sleep(delay)
        if idx < len(candidates):
            time.sleep(random.uniform(2.0, 5.0))
    lottery_results: List[str] = []
    lottery_count = 0
    if award_activity_id:
        try:
            remain = aihoge.get_remain_counts(lottery_id=award_activity_id, limit_id=news_tid, referer_url=referer_url)
        except Exception:
            remain = 0
        lottery_count = remain
        # 先补兑历史未领红包，再抽奖：避免刚抽中的红包尚未落库又被补兑一次（同一码重复兑换）
        lottery_results.extend(aihoge.redeem_pending_prizes(
            lottery_id=award_activity_id, limit_id=news_tid, referer_url=referer_url,
        ))
        for i in range(remain):
            try:
                res = aihoge.draw_lottery(lottery_id=award_activity_id, limit_id=news_tid, referer_url=referer_url)
                prize_desc = aihoge.parse_lottery_result(res)
                if str(res.get("type")) == "3":
                    prize_desc = aihoge.redeem_prize_code(str(res.get("code") or ""), prize_desc)
                lottery_results.append(prize_desc)
            except Exception as e:
                lottery_results.append(f"抽奖失败: {e}")
            if i < remain - 1:
                time.sleep(random.uniform(1.0, 3.0))
    return {
        "total": len(candidates), "completed": completed,
        "lottery_id": award_activity_id, "lottery_count": lottery_count,
        "lottery_results": lottery_results,
    }


def _pick_watch_drama(aged: AgedClient) -> Tuple[str, int, int]:
    """挑一个用于上报观看时长的剧集，返回 (dramaId, videoCount, 单集秒数)"""
    try:
        fav = aged.homepage_index().get("favoriteList") or []
        for item in fav:
            if isinstance(item, dict) and item.get("dramaId"):
                return str(item["dramaId"]), int(item.get("videoCount") or 0), int(item.get("videoDuration") or 0)
    except Exception:
        pass
    try:
        for item in aged.latest_online(size=10):
            if isinstance(item, dict) and item.get("dramaId"):
                return str(item["dramaId"]), int(item.get("videoCount") or 0), int(item.get("videoDuration") or 0)
    except Exception:
        pass
    return "", 0, 0


def _simulate_watch(
    aged: AgedClient, *, drama_id: str, video_count: int, video_duration: int,
    total_sec: int, chunk_sec: int, sleep_sec: float, sleep_enabled: bool,
    start_index: int = 1, start_progress: int = 0, base_reported: int = 0,
    account_name: str = "",
) -> Tuple[int, int, int]:
    """模拟观看：按 chunk_sec 分段上报，直到累计上报满 total_sec 秒。

    支持传入起始集数/进度（多轮补看时状态必须延续，否则上报明文会出现重复序列
    被服务端去重丢弃），返回 (本轮上报秒数, 结束集数, 结束进度)。
    """
    tag = f"[{account_name}] " if account_name else ""
    reported = 0
    progress = max(0, int(start_progress))
    index = max(1, int(start_index) or 1)
    per_video = int(video_duration) if int(video_duration) > 0 else 300
    chunk = max(1, int(chunk_sec))
    while reported < total_sec:
        step = min(chunk, int(total_sec - reported))
        if step <= 0:
            break
        try:
            resp = aged.watch_report(drama_id, index, progress, step)
        except Exception as e:
            if account_name:
                logger_api.warning(f"{tag}观看时长上报失败: {e}")
            return reported, index, progress
        if isinstance(resp, dict) and resp.get("message") == "waf_blocked":
            if account_name:
                logger_api.warning(f"{tag}上报持续被 WAF 拦截，停止本轮观看")
            return reported, index, progress
        reported += step
        progress += step
        while progress >= per_video:
            progress -= per_video
            index += 1
            if video_count and index > video_count:
                index = 1
        if account_name and (reported // max(1, chunk)) % 8 == 0:
            logger_api.info(
                f"{tag}观看视频中... 已上报 {base_reported + reported}/{base_reported + total_sec} 秒"
            )
        if sleep_enabled and sleep_sec > 0 and reported < total_sec:
            time.sleep(sleep_sec)
    return reported, index, progress


def _refresh_watch_progress(
    aged: AgedClient, *, drama_id: str, cur_sec: int, target: int, sleep_enabled: bool,
) -> int:
    """补看后刷新服务端累计时长。

    服务端 userDayDuration 存在滞后：上报完立刻读会偏小。这里先等一轮读一次，
    未达标再等一轮复读取最大值，给服务端落库时间。

    注意：返回值以服务端为准（不要取 max(本地, 服务端)）。本地值只是"已上报量"，
    若服务端真丢失了部分上报，取 max 会让本地虚高值掩盖真实进度，导致误判达标、
    白跑一次 lottery_add。以服务端为准时，未达标会在同一轮循环里继续按余量补。
    """
    if not sleep_enabled:
        try:
            real = int(aged.video_detail(drama_id).get("userDayDuration") or 0)
        except Exception:
            real = 0
        return real if real > 0 else cur_sec

    best = 0
    for wait in (WATCH_SETTLE_SHORT_SEC, WATCH_SETTLE_LONG_SEC):
        try:
            time.sleep(wait)
            real = int(aged.video_detail(drama_id).get("userDayDuration") or 0)
        except Exception:
            real = 0
        if real > best:
            best = real
        if best >= target:
            break
    return best if best > 0 else cur_sec


def run_watch_lottery_flow(
    aihoge: AihogeClient, aged: AgedClient, *,
    chunk_sec: int = DEFAULT_WATCH_CHUNK_SEC,
    sleep_sec: float = DEFAULT_WATCH_SLEEP_SEC,
    max_minutes: int = DEFAULT_WATCH_MAX_MINUTES,
    sleep_enabled: bool = True, account_name: str = "",
) -> Dict[str, Any]:
    """大潮老友/银龄视界「看视频抽好礼」主流程。

    读 tenant/host/info 拿到 5 个观看档位（5/15/30/45/60 分钟，各对应一个抽奖活动），
    按时长升序对每个档位走一次闭环：

        未达成 → 观看视频（最多 3 轮）→ 刷新进度 → lottery/add 开通 → 查 remain → 抽奖
        已达成 → 跳过观看 → 查 remain → 有次数直接抽；0 次则补调一次 lottery/add 兜底

    说明：lottery/add 的 taskCompleted=false 既可能是"未达成"也可能是"今日已发放过"，
    服务端不区分，因此不以它作为能否抽奖的依据 —— remain_counts 才是唯一可信判据。

    兜底的原因：脚本上报达标后若未在当次调用 lottery/add，机会会永久丢失。
    已实测对「已发放过」的档位 add 返回 false、不会重复发放，故兜底安全。
    """
    empty = {"total": 0, "reached": 0, "watched_sec": 0, "lottery_count": 0, "lottery_results": [], "error": ""}
    try:
        host = aged.host_info()
    except Exception as e:
        empty["error"] = f"获取大潮老友/银龄视界租户配置失败: {e}"
        return empty
    cfgs = [c for c in (host.get("tenantPromptConfig") or []) if isinstance(c, dict)]
    if not cfgs:
        empty["error"] = "大潮老友/银龄视界未配置看视频抽奖档位"
        return empty
    try:
        aged.bind_user_info()
    except Exception:
        pass

    result: Dict[str, Any] = {
        "total": len(cfgs), "reached": 0, "watched_sec": 0,
        "lottery_count": 0, "lottery_results": [], "error": "",
    }

    drama_id, video_count, video_duration = _pick_watch_drama(aged)
    cur_sec = 0
    # 单集时长要用 video_detail 的 videoDuration（_pick_watch_drama 取到的是整部剧总时长），
    # 否则 index 永远不递增、progress 会超过单集时长被服务端丢弃。
    per_video = 0
    watch_index = 1
    watch_progress = 0
    if drama_id:
        try:
            det = aged.video_detail(drama_id)
            cur_sec = int(det.get("userDayDuration") or 0)
            per_video = int(det.get("videoDuration") or 0)
            watch_index = max(1, int(det.get("watchVideoNum") or 1))
        except Exception:
            cur_sec = 0
    if not per_video:
        # 兜底：总时长明显是整部剧（过大）时退化成 300 秒
        per_video = int(video_duration) if 0 < int(video_duration) < 1800 else 300
    if account_name:
        logger_api.info(
            f"[{account_name}] 大潮老友/银龄视界，今日已观看 {cur_sec} 秒"
            f"（{cur_sec // 60} 分钟），共 {len(cfgs)} 个档位"
        )

    max_sec = int(max_minutes) * 60
    # 按观看时长升序，逐个档位走完「观看 -> 开通 -> 查剩余 -> 抽奖」闭环
    tiers = sorted(
        (
            (int(c.get("duration") or 0), idx, c, str(c.get("activityId") or ""))
            for idx, c in enumerate(cfgs)
        ),
        key=lambda x: x[0],
    )

    draw_results: List[str] = []
    draw_count = 0
    reached = 0

    for duration_min, idx, cfg, tid in tiers:
        if duration_min <= 0 or not tid:
            continue
        target = duration_min * 60
        tag = f"[{account_name}] " if account_name else ""
        if account_name:
            logger_api.info(f"{tag}── {duration_min} 分钟档 ──")
        if target > max_sec:
            if account_name:
                logger_api.info(
                    f"{tag}{duration_min} 分钟档：超出上限 (DC_WATCH_MAX_MIN={max_minutes})，跳过"
                )
            continue

        if cur_sec < target:
            # 未达成：先观看，达标后再开通（只有本轮观看过才需要调 lottery/add）
            if account_name:
                logger_api.info(
                    f"{tag}未达成（{cur_sec}s < {target}s），需观看 {target - cur_sec} 秒"
                )
            if not drama_id:
                if account_name:
                    logger_api.warning(f"{tag}未取到可观看剧集，无法观看")
                continue

            # 补看最多 WATCH_REFILL_ROUNDS 轮。
            # 服务端 userDayDuration 存在「滞后」与「丢失」两种情况，第 1 轮按缺口原值上报
            # 并等待二次确认以区分；若确认是丢失，第 2 轮起按 WATCH_REFILL_MARGIN 加余量
            # （缺口按认账率 k 等比缩小，纯补看数学上收敛不到目标，必须加余量）。
            for rnd in range(1, WATCH_REFILL_ROUNDS + 1):
                gap = target - cur_sec
                if gap <= 0:
                    break
                send = gap if rnd == 1 else int(gap * WATCH_REFILL_MARGIN) + 12
                watched, watch_index, watch_progress = _simulate_watch(
                    aged, drama_id=drama_id, video_count=video_count,
                    video_duration=per_video, total_sec=send,
                    chunk_sec=chunk_sec, sleep_sec=sleep_sec, sleep_enabled=sleep_enabled,
                    start_index=watch_index, start_progress=watch_progress,
                    base_reported=int(result["watched_sec"]),
                    account_name=account_name,
                )
                result["watched_sec"] = int(result["watched_sec"]) + watched
                cur_sec = _refresh_watch_progress(
                    aged, drama_id=drama_id, cur_sec=cur_sec + watched,
                    target=target, sleep_enabled=sleep_enabled,
                )
                if account_name:
                    logger_api.info(
                        f"{tag}第 {rnd}/{WATCH_REFILL_ROUNDS} 轮观看完成，"
                        f"上报 {watched} 秒，服务端累计 {cur_sec} 秒"
                    )
                if cur_sec >= target:
                    break

            if cur_sec < target:
                if account_name:
                    logger_api.info(f"{tag}观看后仍未达标（{cur_sec}s < {target}s），跳过本档")
                continue
            try:
                data = aged.lottery_add(idx)
            except Exception as e:
                if account_name:
                    logger_api.warning(f"{tag}抽奖档位[{idx}]开通请求失败: {e}")
                data = {}
            if data.get("taskCompleted"):
                if account_name:
                    logger_api.info(f"{tag}开通抽奖 → 成功")
                try:
                    aged.lottery_log(idx, dict(cfg, index=idx))
                except Exception:
                    pass
            else:
                if account_name:
                    logger_api.info(f"{tag}开通抽奖 → 失败（服务端未发放），跳过本档")
                continue
        else:
            # 已达成：不再观看，也不在此处调 add —— 若此前确已发放，add 会返回 false；
            # 若此前漏发，则由下方 remain=0 时的兜底 add 补上。
            if account_name:
                logger_api.info(f"{tag}已达成（{cur_sec}s ≥ {target}s），跳过观看与开通")
        reached += 1

        referer_url = f"https://m.aihoge.com/h5?mark=raffle@designh5&tid={tid}&path=index&gaze_open=1"
        # 先补兑历史未领红包，与剩余次数无关。
        # 注意：历史红包只可能出现在"已抽过"的档位，若把补兑放在 remain<=0 的
        # continue 之后，已抽过的档位会被跳过，补兑将永远不生效。
        draw_results.extend(aihoge.redeem_pending_prizes(
            lottery_id=tid, limit_id=tid, referer_url=referer_url,
        ))
        # 剩余次数是唯一可信判据：>0 才抽
        try:
            remain = aihoge.get_remain_counts(lottery_id=tid, limit_id=tid, referer_url=referer_url)
        except Exception:
            remain = 0
        if remain <= 0:
            # 已达成却无剩余次数，可能是此前漏发：脚本上报达标后若未在当次调用
            # lottery/add（旧版补看未达标会 continue、新版已达成会跳过 add），
            # 机会就永久丢失。这里补调一次 add 兜底。
            # 已实测：对「已发放过」的档位 add 返回 false，不会重复发放。
            try:
                retry = aged.lottery_add(idx)
            except Exception as e:
                if account_name:
                    logger_api.warning(f"{tag}补开抽奖机会请求失败: {e}")
                retry = {}
            if retry.get("taskCompleted"):
                if account_name:
                    logger_api.info(f"{tag}补开抽奖机会 → 成功")
                try:
                    aged.lottery_log(idx, dict(cfg, index=idx))
                except Exception:
                    pass
                try:
                    remain = aihoge.get_remain_counts(lottery_id=tid, limit_id=tid, referer_url=referer_url)
                except Exception:
                    remain = 0
            if remain <= 0:
                if account_name:
                    logger_api.info(f"{tag}剩余抽奖 0 次 → 今日已使用")
                continue
        if account_name:
            logger_api.info(f"{tag}剩余抽奖 {remain} 次 → 抽奖中...")
        for k in range(remain):
            try:
                res = aihoge.draw_lottery(lottery_id=tid, limit_id=tid, referer_url=referer_url)
                prize_desc = aihoge.parse_lottery_result(res)
                if str(res.get("type")) == "3":
                    prize_desc = aihoge.redeem_prize_code(str(res.get("code") or ""), prize_desc)
                draw_results.append(f"{duration_min}分钟档: {prize_desc}")
                draw_count += 1
                if account_name:
                    logger_api.info(f"{tag}中奖: {prize_desc}")
            except Exception as e:
                draw_results.append(f"{duration_min}分钟档: 抽奖失败: {e}")
                if account_name:
                    logger_api.warning(f"{tag}抽奖失败: {e}")
            if k < remain - 1:
                time.sleep(random.uniform(1.0, 3.0))
        time.sleep(random.uniform(1.0, 2.5))

    result["reached"] = reached
    result["lottery_count"] = draw_count
    result["lottery_results"] = draw_results
    return result


# ===========================================================================
# 游戏中心（active.hndachao.cn）—— 玩游戏抽好礼
#
# 活动规则：进入大潮APP「游戏中心」玩游戏，累计游戏时长 5/25/45 分钟
#           各可获得 1 次抽奖机会（每档对应一个独立的 aihoge 抽奖活动）。
#
# 链路（逆向自 ProxyPin9-17 抓包）：
#   1. POST /gamecenter/api/sdk/login {game_id:0, sdk_user_id, sdk_session_id, visitor_id}
#      -> data.token（Bearer，有效期 7200 秒）
#      其中 sdk_user_id = 大潮 account_id、sdk_session_id = 大潮 session_id
#   2. GET  /gamecenter/api/playtime/today-summary
#      -> data.total_active_seconds（今日累计游戏秒数）
#      -> data.reward_tier_progress[]（3 档：duration_seconds / lottery_url / completed）
#   3. POST /gamecenter/api/playtime/start {game_id, visitor_id, sdk_user_id, touch_signals}
#      -> data.session_id
#   4. POST /gamecenter/api/playtime/heartbeat {session_id, game_id, ..., seconds}
#      -> data.applied_seconds（服务端原样采信上报秒数）/ total_active_seconds
#   5. 档位达成后服务端自动发放机会（completion.delivery_status=delivered），
#      用 lottery_url 里的 tid 走 aihoge 抽奖（remain_counts / awd/drw）
#
# 与看视频模块的区别：
#   - 无需 lottery/add 开通，达标即自动发放（本活动没有 add 端点）
#   - 会员身份沿用 aihoge 的 tm/signature（source=xsb_hn），与现有 AihogeClient 一致，
#     因此抽奖直接复用 AihogeClient.get_remain_counts / draw_lottery
# ===========================================================================

GAMECENTER_BASE_URL = "https://active.hndachao.cn"
GAMECENTER_INDEX_URL = f"{GAMECENTER_BASE_URL}/open/xxdtest/gamecenter/index.php?gaze_control=011"
# 游戏页 Referer（对应 game_id=4 潮方块，抓包实测）
GAMECENTER_PLAY_REFERER = f"{GAMECENTER_BASE_URL}/open/L_game/chfk/tetris.php"

# 单次心跳上报的秒数（App 端每 15 秒上报 15 秒，服务端原样采信 applied_seconds）
DEFAULT_GAME_CHUNK_SEC = 15
# 两次心跳之间的真实等待秒数（与 App 节奏一致，避免 is_frequent_heartbeat 风控）
DEFAULT_GAME_SLEEP_SEC = 16
# 单日最多模拟游戏的分钟数（45 分钟 = 全部 3 档）
DEFAULT_GAME_MAX_MINUTES = 45
# 单档补时长最多几轮（服务端累计同样有滞后/丢失，按新缺口逐轮补）
GAME_REFILL_ROUNDS = 5
# 第 2 轮起的上报余量系数（与看视频模块同理，纯补缺口收敛不到目标）
GAME_REFILL_MARGIN = 1.3
# 补时长后等待服务端落库的秒数
GAME_SETTLE_SEC = 5
# 默认游戏（4=潮方块 elsifk，抓包实测使用）
DEFAULT_GAME_ID = 4
# 心跳连续失败多少次后放弃（避免死循环）
GAME_MAX_CONSECUTIVE_FAILS = 10
# 服务端判定心跳过于频繁（is_frequent_heartbeat）后的退避秒数
GAME_FREQUENT_BACKOFF_SEC = 15
# 普通失败后的重试等待秒数
GAME_FAIL_RETRY_SEC = 3


def _pt_sign(token: str, path: str, body: str) -> Dict[str, str]:
    """游戏中心 playtime 写端点的 PT 签名。

    服务端要求 /playtime/start、/playtime/heartbeat、/playtime/stop、
    /playtime/reward-click 这四个写端点必须携带以下三个头，否则返回 401：

        X-PT-Timestamp  秒级时间戳
        X-PT-Nonce      40 位随机字母数字
        X-PT-Signature  HMAC-SHA256(key=token, msg) 的十六进制

    msg = "POST\\n{path}\\n{ts}\\n{nonce}\\n{sha256(body)}"。
    注意两点：
      1) path 是相对路径（如 /playtime/start），**不含** /gamecenter/api 前缀；
      2) body 必须与最终发送的请求体字节完全一致，所以必须先把请求体 dumps
         成紧凑 JSON 字符串、算完签名后用 data= 原样发送，不能交给 requests
         重新序列化（否则字节变了会导致签名校验失败）。
    """
    ts = str(int(time.time()))
    nonce = "".join(
        c for c in f"{int(time.time() * 1000)}-{random.random()}-{random.random()}" if c.isalnum()
    )[:40]
    body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    sign_str = f"POST\n{path}\n{ts}\n{nonce}\n{body_hash}"
    sig = hmac.new(token.encode("utf-8"), sign_str.encode("utf-8"), hashlib.sha256).hexdigest()
    return {"X-PT-Timestamp": ts, "X-PT-Nonce": nonce, "X-PT-Signature": sig}


def _extract_lottery_tid(url: str) -> str:
    """从抽奖页 URL 中提取 tid（...?mark=raffle@designh5&tid=<tid>&path=index）"""
    if not url:
        return ""
    m = re.search(r"[?&]tid=([0-9a-zA-Z]+)", str(url))
    return m.group(1) if m else ""


class GameCenterClient:
    """大潮游戏中心（active.hndachao.cn）客户端：玩游戏累积时长、查询抽奖档位进度。"""

    def __init__(
        self, *, ctx: DachaoLoginContext, user_agent: str = "",
        account_name: str = "", timeout: int = 30,
    ) -> None:
        self.ctx = ctx
        self.account_name = account_name
        self.user_agent = user_agent
        self.timeout = timeout
        self.session = requests.Session()
        self.session.trust_env = False
        self.token = ""
        self.visitor_id = ctx.account_id
        self._play_session_id = ""
        self._touch_total = 0
        self._elapsed_ms = 0

    # -- 基础请求 ----------------------------------------------------------
    def _cookie(self) -> str:
        """游戏中心 Cookie：visitor_id + dachaogo/dachaogod（含 openid 与 platform）"""
        openid = self.ctx.account_id
        payload = quote(json.dumps({"openid": openid, "platform": 3}, ensure_ascii=False), safe="")
        return f"visitor_id={openid}; dachaogo={payload}; dachaogod={payload}"

    def _headers(self, referer: str = "", *, with_auth: bool = True) -> Dict[str, str]:
        headers = {
            "User-Agent": self.user_agent,
            "Content-Type": "application/json",
            "Accept": "*/*",
            "Origin": GAMECENTER_BASE_URL,
            "X-Requested-With": "com.hoge.android.app.dachao",
            "Referer": referer or GAMECENTER_INDEX_URL,
            "Cookie": self._cookie(),
        }
        if with_auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _touch_signals(self, seconds: int) -> Dict[str, Any]:
        """伪造触摸信号（服务端仅做记录，不影响时长累计，但保持与 App 一致）"""
        delta = max(1, int(seconds) * 4)
        self._touch_total += delta
        self._elapsed_ms += int(seconds) * 1000
        third = self._touch_total // 3
        return {
            "touch_supported": True,
            "total_touch_count": self._touch_total,
            "delta_touch_count": delta,
            "pointer_touch_count": third,
            "touch_start_count": third,
            "touch_end_count": third,
            "last_touch_elapsed_ms": random.randint(1000, 4000),
            "session_elapsed_ms": self._elapsed_ms,
        }

    # -- 端点 --------------------------------------------------------------
    def login(self) -> str:
        """POST /gamecenter/api/sdk/login —— 用大潮登录态换取游戏中心 token"""
        url = f"{GAMECENTER_BASE_URL}/gamecenter/api/sdk/login"
        body = {
            "game_id": DEFAULT_GAME_ID,
            "sdk_user_id": self.ctx.account_id,
            "sdk_session_id": self.ctx.session_id,
            "visitor_id": None,
        }
        resp = request_json(
            self.session, method="POST", url=url, headers=self._headers(with_auth=False),
            json_body=body, timeout=self.timeout, account_name=self.account_name,
        )
        data = resp.get("data") or {}
        self.token = str(data.get("token") or "")
        if data.get("visitor_id"):
            self.visitor_id = str(data["visitor_id"])
        if not self.token:
            raise RuntimeError(f"游戏中心登录失败: {resp}")
        return self.token

    def today_summary(self) -> Dict[str, Any]:
        """GET /gamecenter/api/playtime/today-summary —— 今日累计时长 + 三档进度"""
        url = f"{GAMECENTER_BASE_URL}/gamecenter/api/playtime/today-summary"
        resp = request_json(
            self.session, method="GET", url=url, headers=self._headers(),
            params={"sdk_user_id": self.ctx.account_id, "visitor_id": self.visitor_id},
            timeout=self.timeout, account_name=self.account_name,
        )
        return resp.get("data") or {}

    def cards_home(self) -> List[Dict[str, Any]]:
        """GET /gamecenter/api/cards/home —— 游戏列表（用于挑选 game_id）"""
        url = f"{GAMECENTER_BASE_URL}/gamecenter/api/cards/home"
        resp = request_json(
            self.session, method="GET", url=url, headers=self._headers(),
            timeout=self.timeout, account_name=self.account_name,
        )
        data = resp.get("data") or {}
        games: List[Dict[str, Any]] = []
        for card in (data.get("cards") or []):
            if isinstance(card, dict):
                for g in (card.get("games") or []):
                    if isinstance(g, dict) and g.get("id"):
                        games.append(g)
        return games

    def _signed_post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """对 playtime 写端点做 PT 签名后 POST。

        path 是相对路径（如 '/playtime/start'），既用于拼 URL 也参与签名。
        请求体先 dumps 成紧凑 JSON 字符串，用它算出签名，再用 data= 原样发送，
        保证「参与签名的字节」与「实际发送的字节」完全一致
        （若用 json= 交给 requests 重新序列化，字节变了会导致签名校验失败）。
        """
        if not self.token:
            raise RuntimeError("游戏中心未登录（token 为空），无法调用写端点")
        url = f"{GAMECENTER_BASE_URL}/gamecenter/api{path}"
        body = json.dumps(payload, separators=(",", ":"))
        headers = self._headers(referer=GAMECENTER_PLAY_REFERER)
        headers.update(_pt_sign(self.token, path, body))
        return request_json(
            self.session, method="POST", url=url, headers=headers, data=body,
            timeout=self.timeout, account_name=self.account_name,
        )

    def play_start(self, game_id: int) -> str:
        """POST /gamecenter/api/playtime/start —— 开启一次游戏会话（需 PT 签名）"""
        payload = {
            "game_id": int(game_id),
            "visitor_id": self.visitor_id,
            "sdk_user_id": self.ctx.account_id,
            "touch_signals": {
                "touch_supported": True,
                "total_touch_count": 0,
                "delta_touch_count": 0,
                "pointer_touch_count": 0,
                "touch_start_count": 0,
                "touch_end_count": 0,
                "last_touch_elapsed_ms": None,
                "session_elapsed_ms": None,
            },
        }
        resp = self._signed_post("/playtime/start", payload)
        data = resp.get("data") or {}
        self._play_session_id = str(data.get("session_id") or "")
        self._touch_total = 0
        self._elapsed_ms = 0
        if not self._play_session_id:
            raise RuntimeError(f"游戏会话开启失败: {resp}")
        return self._play_session_id

    def play_heartbeat(self, game_id: int, seconds: int) -> Dict[str, Any]:
        """POST /gamecenter/api/playtime/heartbeat —— 上报一段游戏时长（需 PT 签名）

        返回完整响应 dict（含 ok / data / error），便于上层识别
        error.code == 'UNAUTHORIZED' 后重新登录并重开会话。
        """
        payload = {
            "session_id": self._play_session_id,
            "game_id": int(game_id),
            "visitor_id": self.visitor_id,
            "sdk_user_id": self.ctx.account_id,
            "seconds": int(seconds),
            "touch_signals": self._touch_signals(seconds),
        }
        return self._signed_post("/playtime/heartbeat", payload)

    def play_stop(self, game_id: int, total_secs: int = 0) -> Dict[str, Any]:
        """POST /gamecenter/api/playtime/stop —— 结束游戏会话（需 PT 签名，失败不影响主流程）"""
        if not self._play_session_id:
            return {}
        half = self._touch_total // 2
        payload = {
            "session_id": self._play_session_id,
            "game_id": int(game_id),
            "visitor_id": self.visitor_id,
            "sdk_user_id": self.ctx.account_id,
            "duration_seconds": int(DEFAULT_GAME_CHUNK_SEC),
            "reason": "unload",
            "touch_signals": {
                "touch_supported": True,
                "total_touch_count": self._touch_total,
                "delta_touch_count": 50,
                "pointer_touch_count": half,
                "touch_start_count": half,
                "touch_end_count": half,
                "last_touch_elapsed_ms": 1000,
                "session_elapsed_ms": int(total_secs) * 1000,
            },
        }
        try:
            return self._signed_post("/playtime/stop", payload)
        except Exception:
            return {}

    def reward_click(self, completion_id: Any) -> Dict[str, Any]:
        """POST /gamecenter/api/playtime/reward-click —— 领取已达成档位的抽奖机会（需 PT 签名）

        服务端 completion.is_lottery_clicked=false 时必须先调用本端点，
        抽奖机会才会计入抽奖活动的 remain_counts，否则查到的 remain 可能为 0。
        """
        payload = {
            "completion_id": completion_id,
            "visitor_id": self.visitor_id,
            "sdk_user_id": self.ctx.account_id,
        }
        try:
            return self._signed_post("/playtime/reward-click", payload)
        except Exception:
            return {}


def _game_send_seconds(
    gc: GameCenterClient, *, game_id: int, total_sec: int,
    chunk_sec: int, sleep_sec: float, sleep_enabled: bool,
    account_name: str = "",
) -> int:
    """按 chunk_sec 分段心跳上报，直到累计上报满 total_sec 秒，返回本轮上报秒数。

    服务端会校验 playtime 写端点的 PT 签名；token 有效期约 2 小时，失效时返回
    业务错误 UNAUTHORIZED，此时重新登录并重开会话继续上报。其它错误累计到
    GAME_MAX_CONSECUTIVE_FAILS 次则放弃本轮，避免死循环。
    """
    tag = f"[{account_name}] " if account_name else ""
    reported = 0
    chunk = max(1, int(chunk_sec))
    fail_count = 0
    while reported < total_sec:
        step = min(chunk, int(total_sec - reported))
        if step <= 0:
            break
        try:
            resp = gc.play_heartbeat(game_id, step)
            ok = bool(resp.get("ok"))
        except Exception as e:
            if account_name:
                logger_api.warning(f"{tag}游戏时长上报异常: {e}")
            ok = False
            resp = {}

        if ok:
            fail_count = 0
            reported += step
            if account_name and (reported // chunk) % 8 == 0:
                logger_api.info(f"{tag}游戏进行中... 已上报 {reported}/{total_sec} 秒")
            if sleep_enabled and sleep_sec > 0 and reported < total_sec:
                time.sleep(sleep_sec)
            continue

        # 失败分支：先区分 token 失效还是其它错误
        err_code = str((resp.get("error") or {}).get("code") or "")
        if err_code == "UNAUTHORIZED":
            if account_name:
                logger_api.info(f"{tag}游戏中心 token 已失效，重新登录并重开会话")
            try:
                gc.login()
                gc.play_start(game_id)
                fail_count = 0
                continue
            except Exception as e:
                if account_name:
                    logger_api.warning(f"{tag}重新登录/重开会话失败: {e}")
                return reported

        fail_count += 1
        if fail_count >= GAME_MAX_CONSECUTIVE_FAILS:
            if account_name:
                logger_api.warning(
                    f"{tag}心跳连续失败 {fail_count} 次，停止本轮游戏: {resp}"
                )
            return reported
        backoff = GAME_FREQUENT_BACKOFF_SEC if "frequent" in str(resp).lower() else GAME_FAIL_RETRY_SEC
        if sleep_enabled and backoff > 0:
            time.sleep(backoff)
    return reported


def run_game_lottery_flow(
    aihoge: AihogeClient, gc: GameCenterClient, *,
    chunk_sec: int = DEFAULT_GAME_CHUNK_SEC,
    sleep_sec: float = DEFAULT_GAME_SLEEP_SEC,
    max_minutes: int = DEFAULT_GAME_MAX_MINUTES,
    sleep_enabled: bool = True, account_name: str = "",
    game_id: int = DEFAULT_GAME_ID,
) -> Dict[str, Any]:
    """游戏中心「玩游戏抽好礼」主流程。

    读 today-summary 拿到 3 个时长档位（5/25/45 分钟，各对应一个抽奖活动），
    按时长升序对每个档位走一次闭环：

        未达成 -> 玩游戏补时长（最多 GAME_REFILL_ROUNDS 轮）-> 刷新进度 -> 查 remain -> 抽奖
        已达成 -> 直接查 remain -> 有次数就抽

    与看视频模块不同：本活动档位达成后服务端自动发放抽奖机会
    （completion.delivery_status=delivered），没有 lottery/add 开通端点，
    因此 remain_counts 仍是可否抽奖的唯一可信判据。
    """
    empty: Dict[str, Any] = {
        "total": 0, "reached": 0, "played_sec": 0, "lottery_count": 0,
        "lottery_results": [], "error": "",
    }
    try:
        gc.login()
    except Exception as e:
        empty["error"] = f"游戏中心登录失败: {e}"
        return empty
    try:
        summary = gc.today_summary()
    except Exception as e:
        empty["error"] = f"获取游戏中心今日进度失败: {e}"
        return empty

    tiers_raw = [t for t in (summary.get("reward_tier_progress") or []) if isinstance(t, dict)]
    if not tiers_raw:
        empty["error"] = "游戏中心未配置玩游戏抽奖档位"
        return empty

    tiers = sorted(
        ((int(t.get("duration_seconds") or 0), t) for t in tiers_raw),
        key=lambda x: x[0],
    )
    cur_sec = int(summary.get("total_active_seconds") or 0)
    if account_name:
        logger_api.info(
            f"[{account_name}] 游戏中心，今日已玩 {cur_sec} 秒"
            f"（{cur_sec // 60} 分钟），共 {len(tiers)} 个档位"
        )

    max_sec = int(max_minutes) * 60
    result: Dict[str, Any] = {
        "total": len(tiers), "reached": 0, "played_sec": 0,
        "lottery_count": 0, "lottery_results": [], "error": "",
    }
    draw_results: List[str] = []
    draw_count = 0
    reached = 0

    for target, tier in tiers:
        if target <= 0:
            continue
        tag = f"[{account_name}] " if account_name else ""
        minutes = target // 60
        if account_name:
            logger_api.info(f"{tag}── {minutes} 分钟档 ──")
        if target > max_sec:
            if account_name:
                logger_api.info(
                    f"{tag}{minutes} 分钟档：超出上限 (DC_GAME_MAX_MIN={max_minutes})，跳过"
                )
            continue

        tid = _extract_lottery_tid(str(tier.get("lottery_url") or ""))
        if not tid:
            if account_name:
                logger_api.warning(f"{tag}{minutes} 分钟档未取到抽奖活动 tid，跳过")
            continue

        if cur_sec < target:
            if account_name:
                logger_api.info(
                    f"{tag}未达成（{cur_sec}s < {target}s），需游戏 {target - cur_sec} 秒"
                )
            for rnd in range(1, GAME_REFILL_ROUNDS + 1):
                gap = target - cur_sec
                if gap <= 0:
                    break
                # 第 1 轮按缺口原值上报；确认是丢失后第 2 轮起加余量
                send = gap if rnd == 1 else int(gap * GAME_REFILL_MARGIN) + 12
                try:
                    gc.play_start(game_id)
                except Exception as e:
                    if account_name:
                        logger_api.warning(f"{tag}开启游戏会话失败: {e}")
                    break
                played = _game_send_seconds(
                    gc, game_id=game_id, total_sec=send, chunk_sec=chunk_sec,
                    sleep_sec=sleep_sec, sleep_enabled=sleep_enabled,
                    account_name=account_name,
                )
                result["played_sec"] = int(result["played_sec"]) + played
                # 结束本轮游戏会话（对齐 App 行为，失败不影响主流程）
                try:
                    gc.play_stop(game_id, played)
                except Exception:
                    pass
                if sleep_enabled and GAME_SETTLE_SEC > 0:
                    time.sleep(GAME_SETTLE_SEC)
                # 以服务端值为准（本地上报量在服务端丢失时会虚高）
                try:
                    cur_sec = int(gc.today_summary().get("total_active_seconds") or cur_sec)
                except Exception:
                    pass
                if account_name:
                    logger_api.info(
                        f"{tag}第 {rnd}/{GAME_REFILL_ROUNDS} 轮游戏完成，"
                        f"上报 {played} 秒，服务端累计 {cur_sec} 秒"
                    )
                if cur_sec >= target:
                    break

            if cur_sec < target:
                if account_name:
                    logger_api.info(f"{tag}游戏后仍未达标（{cur_sec}s < {target}s），跳过本档")
                continue

        reached += 1
        referer_url = str(tier.get("lottery_url") or "")

        # 档位达成后必须先「领取」抽奖机会（/playtime/reward-click），否则抽奖活动的
        # remain_counts 可能仍为 0。completion 必须现取：本轮刚达标的档位，在循环
        # 开始时读的那份 summary 里还没有 completion 数据。
        fresh: Dict[str, Any] = {}
        try:
            for t in (gc.today_summary().get("reward_tier_progress") or []):
                if isinstance(t, dict) and int(t.get("duration_seconds") or 0) == target:
                    fresh = t
                    break
        except Exception:
            fresh = {}
        comp = fresh.get("completion") or (tier.get("completion") or {})
        comp_id = comp.get("id") if isinstance(comp, dict) else ""
        if comp_id and not comp.get("is_lottery_clicked"):
            if account_name:
                logger_api.info(f"{tag}领取抽奖机会（completion_id={comp_id}）")
            gc.reward_click(comp_id)

        # 先补兑历史未领红包，再抽奖，与剩余次数无关。
        # 注意：历史红包只可能出现在"已抽过"的档位，若把补兑放在 remain<=0 的
        # continue 之后，已抽过的档位会被跳过，补兑将永远不生效。
        draw_results.extend(aihoge.redeem_pending_prizes(
            lottery_id=tid, limit_id=tid, referer_url=referer_url,
        ))
        try:
            remain = aihoge.get_remain_counts(lottery_id=tid, limit_id=tid, referer_url=referer_url)
        except Exception:
            remain = 0
        if remain <= 0:
            if account_name:
                logger_api.info(f"{tag}剩余抽奖 0 次 → 今日已使用")
            continue
        if account_name:
            logger_api.info(f"{tag}剩余抽奖 {remain} 次 → 抽奖中...")
        for k in range(remain):
            try:
                res = aihoge.draw_lottery(lottery_id=tid, limit_id=tid, referer_url=referer_url)
                prize_desc = aihoge.parse_lottery_result(res)
                if str(res.get("type")) == "3":
                    prize_desc = aihoge.redeem_prize_code(str(res.get("code") or ""), prize_desc)
                draw_results.append(f"{minutes}分钟档: {prize_desc}")
                draw_count += 1
                if account_name:
                    logger_api.info(f"{tag}中奖: {prize_desc}")
            except Exception as e:
                draw_results.append(f"{minutes}分钟档: 抽奖失败: {e}")
                if account_name:
                    logger_api.warning(f"{tag}抽奖失败: {e}")
            if k < remain - 1:
                time.sleep(random.uniform(1.0, 3.0))
        time.sleep(random.uniform(1.0, 2.5))

    result["reached"] = reached
    result["lottery_count"] = draw_count
    result["lottery_results"] = draw_results
    return result




# ===========================================================================
# 主流程入口、配置加载、任务编排
# ===========================================================================

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("dachao")


@dataclass
class AccountResult:
    account_name: str
    sign_ok: bool = False
    sign_msg: str = ""
    sign_lottery_count: int = 0
    sign_lottery_results: List[str] = field(default_factory=list)
    read_total: int = 0
    read_completed: int = 0
    news_lottery_count: int = 0
    news_lottery_results: List[str] = field(default_factory=list)
    watch_lottery_count: int = 0
    watch_lottery_results: List[str] = field(default_factory=list)
    watch_lottery_reached: int = 0
    watch_sec: int = 0
    game_lottery_count: int = 0
    game_lottery_results: List[str] = field(default_factory=list)
    game_lottery_reached: int = 0
    game_sec: int = 0
    error: str = ""


def _parse_redeem_env(raw: str) -> Tuple[List[Dict[str, str]], Dict[str, Dict[str, str]]]:
    """解析环境变量 dc_redeem —— 兼容旧的手动配置微信会员身份（member 头 / Cookie / UA）。

    支持两种写法：
      1) 纯文本：每行一个账号，字段用 | 分隔
         member_json|cookie|ua
         member_json|cookie
         member_json
         手机号|member_json|cookie|ua      （首段是手机号时按手机号绑定账号）
      2) JSON：数组（按 dc_ck 顺序对应）或对象（key 为手机号）
         [{"member": {...}, "cookie": "...", "ua": "..."}]
         {"13800000000": {"member": "...", "cookie": "...", "ua": "..."}}
    """
    items: List[Dict[str, str]] = []
    text = str(raw or "").strip()
    if not text:
        return items, {}
    if text[0] in "[{":
        try:
            data = json.loads(text)
        except Exception:
            data = None
        if isinstance(data, dict):
            for phone, val in data.items():
                if isinstance(val, dict):
                    items.append({"phone": str(phone), **{k: str(v) for k, v in val.items() if v is not None}})
        elif isinstance(data, list):
            for val in data:
                if isinstance(val, dict):
                    items.append({k: str(v) for k, v in val.items() if v is not None})
    if not items:
        items = []
        for line in text.replace("\r", "\n").split("\n"):
            line = line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split("|")]
            if not parts[0]:
                continue
            item: Dict[str, str] = {}
            if parts[0].isdigit() and len(parts[0]) == 11 and len(parts) > 1:
                item["phone"] = parts[0]
                parts = parts[1:]
            if not parts[0]:
                continue
            item["member"] = parts[0]
            if len(parts) > 1:
                item["cookie"] = parts[1]
            if len(parts) > 2:
                item["ua"] = parts[2]
            items.append(item)

    ordered: List[Dict[str, str]] = []
    by_phone: Dict[str, Dict[str, str]] = {}
    for item in items:
        member = normalize_redeem_member(item.get("member") or item.get("redeem_member") or "")
        if not member:
            continue
        entry = {
            "redeem_member": member,
            "redeem_cookies": str(item.get("cookie") or item.get("redeem_cookies") or ""),
            "redeem_user_agent": str(item.get("ua") or item.get("redeem_user_agent") or ""),
        }
        phone = str(item.get("phone") or "").strip()
        if phone:
            by_phone[phone] = entry
        else:
            ordered.append(entry)
    return ordered, by_phone


def load_config_from_env() -> List[Dict[str, Any]]:
    """从环境变量 dc_ck 读取账号配置。

    格式：手机号#密码明文
    多账号用 & 分割。密码填明文，登录时自动 RSA 加密。

    可选环境变量：
      dc_openid     微信 openid（用于通过应用宝网关自动获取微信会员身份）。
      wx_server_url 应用宝网关地址
                    配合 dc_openid 使用，自动获取微信会员身份兑换红包。
    """
    raw = os.environ.get("dc_ck", "").strip()
    if not raw:
        raise ValueError("环境变量 dc_ck 未设置，请先设置后再运行")

    redeem_ordered, redeem_by_phone = _parse_redeem_env(os.environ.get("dc_redeem", ""))

    # 全局微信网关配置（所有账号共享）
    wx_server_url = os.environ.get("wx_server_url", "").strip()
    # dc_openid 支持多账号：纯文本换行或 & 分割，也可用 JSON
    dc_openid_raw = os.environ.get("dc_openid", "").strip()
    openid_by_phone: Dict[str, str] = {}
    openid_ordered: List[str] = []
    if dc_openid_raw:
        if dc_openid_raw[0] in "[{":
            try:
                od = json.loads(dc_openid_raw)
                if isinstance(od, dict):
                    for ph, oid in od.items():
                        if isinstance(oid, str) and oid.strip():
                            openid_by_phone[str(ph).strip()] = oid.strip()
                elif isinstance(od, list):
                    for oid in od:
                        if isinstance(oid, str) and oid.strip():
                            openid_ordered.append(oid.strip())
            except Exception:
                pass
        if not openid_by_phone and not openid_ordered:
            for line in dc_openid_raw.replace("\r", "\n").replace("&", "\n").split("\n"):
                line = line.strip()
                if not line:
                    continue
                parts = [p.strip() for p in line.split("|")]
                if not parts[0]:
                    continue
                if parts[0].isdigit() and len(parts[0]) == 11 and len(parts) > 1:
                    openid_by_phone[parts[0]] = parts[1]
                else:
                    openid_ordered.append(parts[0])

    accounts: List[Dict[str, Any]] = []
    for part in raw.split("&"):
        part = part.strip()
        if not part:
            continue
        fields = part.split("#", 1)
        if len(fields) < 2:
            raise ValueError(f"dc_ck 格式错误：'{part}' 应为 phone#password 两段用 # 分割")
        phone = fields[0].strip()
        # 第二段现在是【明文密码】，登录时由 encrypt_login_password() 自动 RSA 加密
        pwd_plain = fields[1].strip()
        if not pwd_plain:
            raise ValueError(f"账号 {phone} 的密码明文为空")

        account: Dict[str, Any] = {
            "phone_number": phone,
            "password_encrypted": pwd_plain,
        }
        redeem = redeem_by_phone.get(phone)
        if redeem is None and redeem_ordered:
            redeem = redeem_ordered.pop(0)
        if redeem:
            account.update(redeem)
        # 注入微信网关配置（自动获取微信会员身份）
        if wx_server_url:
            account["wx_server_url"] = wx_server_url
        oid = openid_by_phone.get(phone)
        if oid is None and openid_ordered:
            oid = openid_ordered.pop(0)
        if oid:
            account["dc_openid"] = oid
        accounts.append(account)

    if not accounts:
        raise ValueError("dc_ck 未解析到有效账号")
    return accounts


def log_task_header(title: str, timestamp: datetime) -> None:
    logger.info("=" * 60)
    logger.info(f"{title} - {timestamp.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)


def _account_section(account_name: str, title: str) -> None:
    logger.info(f"[{account_name}] ===== {title} =====")


def run_account(
    cfg: NewDachaoAccountConfig, mode: str, *,
    read_delay_min: float, read_delay_max: float,
    sleep_enabled: bool,
    watch_enabled: bool = True,
    watch_chunk_sec: int = DEFAULT_WATCH_CHUNK_SEC,
    watch_sleep_sec: float = DEFAULT_WATCH_SLEEP_SEC,
    watch_max_minutes: int = DEFAULT_WATCH_MAX_MINUTES,
    game_enabled: bool = True,
    game_chunk_sec: int = DEFAULT_GAME_CHUNK_SEC,
    game_sleep_sec: float = DEFAULT_GAME_SLEEP_SEC,
    game_max_minutes: int = DEFAULT_GAME_MAX_MINUTES,
) -> AccountResult:
    account_name = cfg.account_name
    result = AccountResult(account_name=account_name)
    try:
        logger.info(f"开始处理账号: {account_name}")
        _account_section(account_name, "登录模块")
        _auth_code, ctx, sign_page_url, sign_tid, news_tid, aihoge = login_build_clients(cfg, account_name=account_name)

        if mode in ("all", "sign"):
            _account_section(account_name, "签到模块")
            # 先查询签到详情，判断当日是否已签到
            already_signed = False
            try:
                sign_detail = aihoge.get_sign_detail(sign_tid=sign_tid, sign_page_url=sign_page_url)
                member_info = sign_detail.get("member_sign_info") or {}
                day_sign_num = int(member_info.get("day_sign_num") or 0)
                continuous_sign_num = int(member_info.get("continuous_sign_num") or 0)
                if day_sign_num > 0:
                    already_signed = True
                    result.sign_ok = True
                    result.sign_msg = f"今日已签到，连续签到 {continuous_sign_num} 天"
                    logger.info(f"[{account_name}] {result.sign_msg}")
            except Exception as e:
                logger_api.warning(f"[{account_name}] 查询签到详情失败: {e}")

            if not already_signed:
                sign_resp = run_sign_flow(aihoge, sign_tid=sign_tid, sign_page_url=sign_page_url)
                if sign_resp.get("error_code") == 0:
                    result.sign_ok = True
                    r = sign_resp.get("response") or {}
                    days = r.get("continuous_sign_num", 0)
                    if r.get("success") == 1:
                        result.sign_msg = f"签到成功，连续签到 {days} 天"
                    else:
                        result.sign_msg = f"今日已签到，连续签到 {days} 天"
                    logger.info(f"[{account_name}] {result.sign_msg}")
                else:
                    result.sign_ok = False
                    result.sign_msg = str(sign_resp.get("error_message") or sign_resp)
                    logger.warning(f"[{account_name}] 签到失败: {result.sign_msg}")

            time.sleep(random.uniform(2.0, 5.0))

            # 签到抽奖每天 8:30 后开放，未到时间则跳过
            _account_section(account_name, "签到抽奖")
            now_hm = time.localtime()
            if now_hm.tm_hour < 8 or (now_hm.tm_hour == 8 and now_hm.tm_min < 30):
                logger.info(f"[{account_name}] 签到抽奖活动每天 8:30 后开放，当前时间未到，跳过")
            else:
                # 自动获取签到关联的抽奖活动 ID（优先使用配置值，未配置则从签到活动动态获取）
                sign_lottery_id = cfg.sign_lottery_id
                lottery_draw_num = 0
                lottery_title = ""
                if not sign_lottery_id:
                    try:
                        relation_list = aihoge.get_relation_lottery_list(
                            sign_tid=sign_tid, sign_page_url=sign_page_url,
                        )
                        for item in relation_list:
                            draw_num = int(item.get("draw_num") or 0)
                            if draw_num > 0:
                                sign_lottery_id = str(item.get("hashid") or "")
                                lottery_draw_num = draw_num
                                lottery_title = item.get("title", "")
                                break
                        if not sign_lottery_id and relation_list:
                            logger.info(f"[{account_name}] 签到关联抽奖活动存在但无剩余次数，跳过抽奖")
                    except Exception as e:
                        logger_api.warning(f"[{account_name}] 获取签到关联抽奖列表失败: {e}")

                if sign_lottery_id:
                    logger.info(f"[{account_name}] 自动获取签到抽奖活动ID: {sign_lottery_id} ({lottery_title})，剩余 {lottery_draw_num} 次")
                    lottery = run_sign_lottery_flow(
                        aihoge, sign_tid=sign_tid, sign_page_url=sign_page_url,
                        sign_lottery_id=sign_lottery_id,
                    )
                    result.sign_lottery_count = int(lottery.get("lottery_count") or 0)
                    result.sign_lottery_results = list(lottery.get("lottery_results") or [])
                    logger.info(f"[{account_name}] 签到抽奖剩余次数: {result.sign_lottery_count}")
                    if result.sign_lottery_count <= 0:
                        logger.info(f"[{account_name}] 没有可用的签到抽奖次数")
                    for prize in result.sign_lottery_results:
                        logger.info(f"[{account_name}] 签到抽奖结果: {prize}")

        if mode in ("all", "read") and news_tid:
            _account_section(account_name, "阅读有礼任务模块")
            # 阅读有礼活动每天 9:30 后开放，未到时间则跳过
            now_hm = time.localtime()
            if now_hm.tm_hour < 9 or (now_hm.tm_hour == 9 and now_hm.tm_min < 30):
                logger.info(f"[{account_name}] 阅读有礼活动每天 9:30 后开放，当前时间未到，跳过")
            else:
                vapp = TmuyunVappClient()
                buoy = vapp.buoy_list(ctx, user_agent=cfg.user_agent, cookies=cfg.vapp_cookies)
                news_entry_url, _ = discover_news_read_tid(buoy)
                delay_min = float(read_delay_min)
                delay_max = float(read_delay_max)
                if delay_min > delay_max:
                    delay_min, delay_max = delay_max, delay_min
                read_stats = run_read_flow(
                    aihoge=aihoge, vapp=vapp, ctx=ctx, news_tid=news_tid,
                    news_entry_url=news_entry_url, vapp_user_agent=cfg.user_agent,
                    vapp_cookies=cfg.vapp_cookies,
                    read_delay_range_s=(max(0.0, delay_min), max(0.0, delay_max)),
                    sleep_enabled=bool(sleep_enabled), account_name=account_name,
                )
                result.read_total = int(read_stats.get("total") or 0)
                result.read_completed = int(read_stats.get("completed") or 0)
                result.news_lottery_count = int(read_stats.get("lottery_count") or 0)
                result.news_lottery_results = list(read_stats.get("lottery_results") or [])
                logger.info(f"[{account_name}] 阅读抽奖剩余次数: {result.news_lottery_count}")
                if result.news_lottery_count <= 0:
                    logger.info(f"[{account_name}] 没有可用的阅读抽奖次数")
                for prize in result.news_lottery_results:
                    logger.info(f"[{account_name}] 阅读抽奖结果: {prize}")

        if mode in ("all", "read", "watch") and watch_enabled:
            _account_section(account_name, "大潮老友/银龄视界-看视频抽好礼模块")
            try:
                aged = AgedClient(
                    tenant_id=cfg.tenant_id, account_id=ctx.account_id,
                    session_id=ctx.session_id, user_agent=cfg.webview_user_agent,
                    account_name=account_name,
                )
                watch_stats = run_watch_lottery_flow(
                    aihoge=aihoge, aged=aged,
                    chunk_sec=watch_chunk_sec, sleep_sec=watch_sleep_sec,
                    max_minutes=watch_max_minutes, sleep_enabled=sleep_enabled,
                    account_name=account_name,
                )
                if watch_stats.get("error"):
                    logger.warning(f"[{account_name}] {watch_stats['error']}")
                result.watch_lottery_reached = int(watch_stats.get("reached") or 0)
                result.watch_sec = int(watch_stats.get("watched_sec") or 0)
                result.watch_lottery_count = int(watch_stats.get("lottery_count") or 0)
                result.watch_lottery_results = list(watch_stats.get("lottery_results") or [])
                logger.info(
                    f"[{account_name}] 看视频抽好礼: 达成 {result.watch_lottery_reached}/"
                    f"{int(watch_stats.get('total') or 0)} 档，本轮观看 {result.watch_sec} 秒，"
                    f"抽奖 {result.watch_lottery_count} 次"
                )
                if result.watch_lottery_count <= 0:
                    logger.info(f"[{account_name}] 没有可用的看视频抽奖次数")
                for prize in result.watch_lottery_results:
                    logger.info(f"[{account_name}] 看视频抽奖结果: {prize}")
            except Exception as e:
                logger_api.warning(f"[{account_name}] 看视频抽好礼模块执行失败: {e}")

        if mode in ("all", "read", "watch", "game") and game_enabled:
            _account_section(account_name, "游戏中心-玩游戏抽好礼模块")
            try:
                gc = GameCenterClient(
                    ctx=ctx, user_agent=cfg.webview_user_agent, account_name=account_name,
                )
                game_stats = run_game_lottery_flow(
                    aihoge=aihoge, gc=gc,
                    chunk_sec=game_chunk_sec, sleep_sec=game_sleep_sec,
                    max_minutes=game_max_minutes, sleep_enabled=sleep_enabled,
                    account_name=account_name,
                )
                if game_stats.get("error"):
                    logger.warning(f"[{account_name}] {game_stats['error']}")
                result.game_lottery_reached = int(game_stats.get("reached") or 0)
                result.game_sec = int(game_stats.get("played_sec") or 0)
                result.game_lottery_count = int(game_stats.get("lottery_count") or 0)
                result.game_lottery_results = list(game_stats.get("lottery_results") or [])
                logger.info(
                    f"[{account_name}] 玩游戏抽好礼: 达成 {result.game_lottery_reached}/"
                    f"{int(game_stats.get('total') or 0)} 档，本轮游戏 {result.game_sec} 秒，"
                    f"抽奖 {result.game_lottery_count} 次"
                )
                if result.game_lottery_count <= 0:
                    logger.info(f"[{account_name}] 没有可用的玩游戏抽奖次数")
                for prize in result.game_lottery_results:
                    logger.info(f"[{account_name}] 玩游戏抽奖结果: {prize}")
            except Exception as e:
                logger_api.warning(f"[{account_name}] 玩游戏抽好礼模块执行失败: {e}")

        return result
    except Exception as e:
        result.error = str(e)
        logger.error(f"处理账号 {account_name} 时发生错误: {e}")
        return result


def _send_summary_notification(results: List[AccountResult], start_time: datetime, end_time: datetime, mode: str) -> None:
    duration = (end_time - start_time).total_seconds()
    total_accounts = len(results)
    success_accounts = sum(1 for r in results if not r.error)
    total_sign_success = sum(1 for r in results if r.sign_ok)
    total_sign_lottery = sum(r.sign_lottery_count for r in results)
    total_read_completed = sum(r.read_completed for r in results)
    total_news_lottery = sum(r.news_lottery_count for r in results)
    total_watch_lottery = sum(r.watch_lottery_count for r in results)
    total_game_lottery = sum(r.game_lottery_count for r in results)
    title = "大潮App任务完成 ✅"
    content_parts = [
        "📊 总体统计",
        "━━━━━━━━━━━━━━━━",
        f"👥 账号数量: {total_accounts}个",
        f"✅ 成功账号: {success_accounts}/{total_accounts}",
    ]
    if mode in ("all", "sign"):
        content_parts.extend([
            f"📝 签到成功: {total_sign_success}/{total_accounts}",
            f"🎰 签到抽奖: {total_sign_lottery}次",
        ])
    if mode in ("all", "read"):
        content_parts.extend([
            f"📖 阅读有礼: {total_read_completed}篇",
            f"🎰 阅读有礼抽奖: {total_news_lottery}次",
        ])
    if mode in ("all", "read", "watch"):
        content_parts.extend([
            f"📺 看视频抽奖: {total_watch_lottery}次",
        ])
    if mode in ("all", "read", "watch", "game"):
        content_parts.extend([
            f"🎮 玩游戏抽奖: {total_game_lottery}次",
        ])
    content_parts.extend([f"⏱️ 执行耗时: {int(duration)}秒", "", "📋 账号详情", "━━━━━━━━━━━━━━━━"])
    for idx, r in enumerate(results, 1):
        if r.error:
            content_parts.append(f"❌ [{r.account_name}] 执行失败")
            content_parts.append(f"   错误: {r.error}")
        else:
            sign_status = "✅" if r.sign_ok else "❌"
            content_parts.append(f"{sign_status} [{r.account_name}]")
            if mode in ("all", "sign"):
                content_parts.append(f"   📝 签到: {r.sign_msg or ('成功' if r.sign_ok else '失败')}")
                content_parts.append(f"   🎰 签到抽奖: {r.sign_lottery_count}次")
                if r.sign_lottery_results:
                    content_parts.append("   🎁 签到奖励:")
                    for prize in r.sign_lottery_results:
                        content_parts.append(f"      - {prize}")
            if mode in ("all", "read"):
                content_parts.append(f"   📖 阅读有礼: {r.read_completed}/{r.read_total}")
                content_parts.append(f"   🎰 阅读有礼抽奖: {r.news_lottery_count}次")
                if r.news_lottery_results:
                    content_parts.append("   🎁 阅读有礼奖励:")
                    for prize in r.news_lottery_results:
                        content_parts.append(f"      - {prize}")
            if mode in ("all", "read", "watch"):
                content_parts.append(
                    f"   📺 看视频抽好礼: 达成{r.watch_lottery_reached}档，"
                    f"抽奖{r.watch_lottery_count}次（观看{r.watch_sec}秒）"
                )
                if r.watch_lottery_results:
                    content_parts.append("   🎁 看视频奖励:")
                    for prize in r.watch_lottery_results:
                        content_parts.append(f"      - {prize}")
            if mode in ("all", "read", "watch", "game"):
                content_parts.append(
                    f"   🎮 玩游戏抽好礼: 达成{r.game_lottery_reached}档，"
                    f"抽奖{r.game_lottery_count}次（游戏{r.game_sec}秒）"
                )
                if r.game_lottery_results:
                    content_parts.append("   🎁 玩游戏奖励:")
                    for prize in r.game_lottery_results:
                        content_parts.append(f"      - {prize}")
        if idx < len(results):
            content_parts.append("")
    content_parts.append("━━━━━━━━━━━━━━━━")
    content_parts.append(f"🕐 {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"\n{'=' * 60}")
    print(f"{title}")
    print(f"{'=' * 60}")
    print("\n".join(content_parts))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["all", "sign", "read", "watch", "game"], default="all")
    parser.add_argument("--read-delay-min", type=float, default=20.0)
    parser.add_argument("--read-delay-max", type=float, default=30.0)
    parser.add_argument("--no-sleep", action="store_true", help="不等待直接上报阅读/观看时间（调试用）")
    parser.add_argument("--fast", action="store_true", help="调试快捷模式")
    parser.add_argument(
        "--watch-max-min", type=int, default=_env_int("DC_WATCH_MAX_MIN", DEFAULT_WATCH_MAX_MINUTES),
        help="看视频模块单日最多模拟观看的分钟数（默认 60，即 5/15/30/45/60 五档全做）",
    )
    parser.add_argument(
        "--watch-chunk", type=int, default=_env_int("DC_WATCH_CHUNK", DEFAULT_WATCH_CHUNK_SEC),
        help="单次观看时长上报的秒数（默认 12，与 App 端 apiRateLimit 一致）",
    )
    parser.add_argument(
        "--watch-sleep", type=float, default=_env_float("DC_WATCH_SLEEP", DEFAULT_WATCH_SLEEP_SEC),
        help="两次观看上报之间的真实等待秒数（默认 2；设为与 --watch-chunk 相同即等价于真实观看）",
    )
    parser.add_argument(
        "--game-max-min", type=int, default=_env_int("DC_GAME_MAX_MIN", DEFAULT_GAME_MAX_MINUTES),
        help="玩游戏模块单日最多模拟游戏的分钟数（默认 45，即 5/25/45 三档全做）",
    )
    parser.add_argument(
        "--game-chunk", type=int, default=_env_int("DC_GAME_CHUNK", DEFAULT_GAME_CHUNK_SEC),
        help="单次游戏心跳上报的秒数（默认 15，与 App 端节奏一致；服务端原样采信）",
    )
    parser.add_argument(
        "--game-sleep", type=float, default=_env_float("DC_GAME_SLEEP", DEFAULT_GAME_SLEEP_SEC),
        help="两次游戏心跳之间的真实等待秒数（默认 16；调小可加速但有 is_frequent_heartbeat 风控风险）",
    )
    args = parser.parse_args()

    start_time = datetime.now()
    log_task_header("大潮App任务开始执行", start_time)

    try:
        accounts_raw = load_config_from_env()
        debug = bool(os.environ.get("dc_debug", "").strip())
        if debug:
            logging.getLogger().setLevel(logging.DEBUG)
            logging.getLogger("urllib3").setLevel(logging.WARNING)

        accounts: List[NewDachaoAccountConfig] = []
        for a in accounts_raw:
            try:
                accounts.append(NewDachaoAccountConfig.from_dict(a))
            except Exception as e:
                logger.error(f"账号配置异常: {e}")

        if not accounts:
            logger.warning("未找到大潮账号信息，请检查环境变量 dc_ck")
            return 2

        logger.info(f"成功加载 {len(accounts)} 个账号配置")

        if args.fast:
            args.read_delay_min = 1.0
            args.read_delay_max = 2.0

        logger.info(f"开始执行任务，共 {len(accounts)} 个账号")
        results: List[AccountResult] = []
        for i, acc in enumerate(accounts, 1):
            logger.info(f"\n{'=' * 60}")
            logger.info(f"处理第 {i}/{len(accounts)} 个账号")
            logger.info(f"{'=' * 60}")
            results.append(
                run_account(
                    acc, mode=args.mode,
                    read_delay_min=args.read_delay_min, read_delay_max=args.read_delay_max,
                    sleep_enabled=(not args.no_sleep),
                    watch_enabled=_env_bool("DC_WATCH_ENABLE", True),
                    watch_chunk_sec=max(1, int(args.watch_chunk)),
                    watch_sleep_sec=max(0.0, float(args.watch_sleep)),
                    watch_max_minutes=max(0, int(args.watch_max_min)),
                    game_enabled=_env_bool("DC_GAME_ENABLE", True),
                    game_chunk_sec=max(1, int(args.game_chunk)),
                    game_sleep_sec=max(0.0, float(args.game_sleep)),
                    game_max_minutes=max(0, int(args.game_max_min)),
                )
            )
            logger.info(f"账号 {i} 处理完成")
            if i < len(accounts):
                account_delay = random.uniform(3.0, 8.0)
                logger.info(f"账号切换延时 {account_delay:.2f} 秒...")
                time.sleep(account_delay)

        logger.info("所有账号任务处理完成")
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        logger.info("=" * 60)
        logger.info(f"大潮App任务执行完成 - {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"执行耗时: {int(duration)} 秒")
        logger.info("=" * 60)

        _send_summary_notification(results, start_time, end_time, mode=args.mode)
        return 0 if all(not r.error for r in results) else 1

    except Exception as e:
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        logger.error(f"任务执行异常: {str(e)}", exc_info=True)
        print(f"\n❌ 任务执行异常\n💬 错误信息: {e}\n⏱️ 执行耗时: {int(duration)}秒\n🕐 完成时间: {end_time.strftime('%Y-%m-%d %H:%M:%S')}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
