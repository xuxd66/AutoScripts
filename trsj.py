"""
Author: anonymous
Date: 2026.08.20
Description: 甜润世界小程序签到
Cron: 5 9,12,20 * * *
------------------------------------------
甜润世界小程序签到 v1.0.0

功能：自动执行甜润世界小程序签到、种植石斛，支持多账号执行。

配置说明：
1. 微信 code 网关：（自建微信 code 网关，适配应用宝协议，ck自动获取）
   wx_server_url                                       必填，自建授权服务器地址
   - 示例：http://127.0.0.1:8000
   - 脚本会自动拼接 /wxapp/getCode（YYB 代理服务）
   - 请求格式：POST {网关}/wxapp/getCode
   - 请求体：{"app_id": "wx210e40a77dbe7a27", "ref": "<微信 openid>"}
   - 响应示例：{"code":0,"msg":"success","data":{"openid":"...","result":{"code":"0f...","errMsg":"login:ok"}}}

2. 账号变量：
   trsj_openid                                         推荐，甜润世界账号 openid 变量
   - 多账号支持使用 & 或换行分隔
   - 示例：openid_a&openid_b
------------------------------------------
"""
import os
import re
import sys
import time
import json
import requests
from datetime import datetime
import urllib3

# Windows 控制台默认 GBK 无法编码 emoji/特殊字符，强制 stdout/stderr 为 UTF-8
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass
from typing import Dict, List, Optional, Any, Tuple
from urllib.parse import unquote, urlparse, parse_qs, quote as url_encode

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

APPID = "wx210e40a77dbe7a27"
BASE = "https://m.ahzyssl.com"
UA = "Mozilla/5.0 (Linux; Android 14; PJE110) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Mobile Safari/537.36 MiniProgramEnv/android"
LOGIN_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a13) UnifiedPCWindowsWechat(0xf2541c1a) XWEB/25297 miniProgram/wx210e40a77dbe7a27"

BASE_HEADERS = {
    "Host": "m.ahzyssl.com",
    "Connection": "keep-alive",
    "charset": "utf-8",
    "User-Agent": UA,
    "Referer": f"https://servicewechat.com/{APPID}/page-frame.html",
}

# ========== 微信 code 网关配置 ==========
_WX_RAW = os.getenv("wx_server_url", "")
if _WX_RAW.startswith("http://"):
    _WX_RAW = _WX_RAW[7:]
elif _WX_RAW.startswith("https://"):
    _WX_RAW = _WX_RAW[8:]
WX_SERVER = _WX_RAW.rstrip("/")

# 多账号：trsj_openid，支持 & 或换行分隔
ACCOUNTS = []
env_openid = os.getenv("trsj_openid", "")
if env_openid:
    raw_list = re.split(r"[&\n]", env_openid)
    ACCOUNTS = [x.strip() for x in raw_list if x.strip()]

if not WX_SERVER:
    print("❌ 未配置环境变量 wx_server_url（微信 code 网关地址）")
    exit(1)
if not ACCOUNTS:
    print("❌ 未配置环境变量 trsj_openid（微信账号 openid，多账号用 & 或换行分隔）")
    exit(1)
print(f"✅ 网关已连接，读取到 {len(ACCOUNTS)} 个账号")


def get_wx_code(openid: str) -> Optional[str]:
    """通过 wx_server_url 网关（YYB 代理的 /wxapp/getCode）获取微信 code"""
    if not WX_SERVER:
        print('❌ 未设置wx_server_url环境变量')
        return None
    try:
        resp = requests.post(
            f"http://{WX_SERVER}/wxapp/getCode",
            json={"app_id": APPID, "ref": openid},
            timeout=15,
            proxies=None
        )
        data = resp.json()
        # YYB 代理响应: {"code":0,"msg":"success","data":{"openid":..,"result":{"code":"0f...","errMsg":"login:ok"}}}
        if data.get('code') == 0 or data.get('msg') == 'success':
            result_data = data.get('data', {})
            result_obj = result_data.get('result', {})
            code = result_obj.get('code')
            if code:
                print(f"✅ 获取code成功: {str(code)[:10]}... (len={len(str(code))})")
                return code
            else:
                print(f"获取code失败: 响应中未找到code字段, 完整响应={json.dumps(data, ensure_ascii=False)}")
        else:
            print(f"获取code失败: {data.get('msg', '未知错误')}")
    except Exception as e:
        print(f"获取code异常: {e}")
    return None


def code_login(openid: str) -> str | None:
    """小程序直登：code → applet_auth_token（data 字段即 ck）
    """
    code = get_wx_code(openid)
    if not code:
        return None
    try:
        resp = requests.get(
            f"{BASE}/wx/user/appletLogin",
            params={"code": code},
            headers={"User-Agent": LOGIN_UA},
            timeout=20,
            verify=False,
            proxies=None
        )
        data = resp.json()
        # 响应: {"code":200,"data":"<uuid token>"}，data 即 applet_auth_token
        if data.get("code") == 200 and data.get("data"):
            token = data["data"]
            print(f"✅ 登录成功，authToken: {str(token)[:8]}...")
            return token
        print(f"❌ 登录失败：{data.get('msg', '未知错误')}，状态码: {resp.status_code}")
        print(f"   响应体: {resp.text[:500]}")
        return None
    except Exception as e:
        print(f"❌ 登录异常: {e}")
        return None


def get_userinfo(ck: str) -> dict | None:
    """用 applet_auth_token 查用户资料，验证 ck 是否有效"""
    return do_request(ck, f"{BASE}/applet/user/getUserBaseInfo", "验证ck-查资料")


def verify_ck(ck: str) -> bool:
    """验证 ck 是否有效（非 None 且能拿到用户资料）"""
    if not ck:
        return False
    data = get_userinfo(ck)
    if data and data.get("code") == 200 and data.get("data"):
        u = data["data"]
        print(f"✅ ck 有效: {u.get('userName', '?')}")
        return True
    print(f"❌ ck 无效或已过期: {str(data)[:120]}")
    return False


def get_ck(openid: str) -> str | None:
    """通过 code→ck 获取 applet_auth_token（网关 getCode，见文件头注释）"""
    ck = code_login(openid)
    if ck and verify_ck(ck):
        return ck
    return None


def step(icon: str, desc: str, result: str = "") -> None:
    """统一子步骤打印：🟢 描述 → 结果"""
    if result:
        print(f"  {icon} {desc} → {result}")
    else:
        print(f"  {icon} {desc}")


def section(title: str, icon: str = "📌") -> None:
    """章节标题分隔条"""
    bar = "─" * 40
    print(f"\n{icon} {title}")
    print(f"  {bar}")


def do_request(auth_token: str, url: str, desc: str, method: str = "GET") -> dict | None:
    headers = {**BASE_HEADERS, "Authorization": auth_token}
    try:
        if method == "POST":
            resp = requests.post(url, headers=headers, timeout=45, verify=False,
                                 proxies=None)
        else:
            resp = requests.get(url, headers=headers, timeout=45, verify=False,
                                proxies=None)
        data = resp.json()
        msg = (data or {}).get("msg", "操作成功")
        step("🔹", desc, msg)
        return data
    except Exception as e:
        err_msg = str(e)
        step("⚠️", desc, f"失败: {err_msg}")
        return None


def sign_in_award(auth_token: str) -> list:
    """签到有奖"""
    section("签到有奖", "🎁")
    logs = []
    resp = do_request(auth_token, f"{BASE}/applet/user/signIn/getUserSignInLog", "查询签到有奖状态")
    if not resp or resp.get("code") != 200:
        logs.append("❌ 查询签到有奖状态失败")
        return logs
    today = datetime.now().strftime("%Y-%m-%d")
    sign_list = (resp.get("data") or {}).get("userSignInList") or []
    signed = any(
        (i.get("signInDate") == today and i.get("signInStatus") == 1)
        for i in sign_list
    )
    if signed:
        step("✅", "签到有奖", "今日已完成")
        logs.append("✅ 签到有奖：今日已完成")
    else:
        do_sign = do_request(auth_token, f"{BASE}/applet/user/signIn", "执行签到有奖", "POST")
        ok = bool(do_sign and do_sign.get("code") == 200)
        step("✅" if ok else "❌", "签到有奖", "成功" if ok else "失败")
        logs.append(("✅ 签到有奖成功" if ok else "❌ 签到有奖失败"))
    return logs


def plant_dendrobium(auth_token: str) -> tuple[list, bool]:
    """石斛播种（若尚未种植）"""
    section("石斛播种检查", "🌱")
    logs = []
    info = do_request(auth_token, f"{BASE}/applet/game/dendrobium/get", "查询石斛状态")
    if not info:
        step("❌", "查询石斛状态", "失败")
        logs.append("❌ 查询石斛状态失败")
        return logs, False
    # 未播种：code==500 且 msg 含"没有正在培养"
    if info.get("code") == 500 or "没有正在培养" in (info.get("msg") or ""):
        step("🟡", "石斛状态", "未种植，准备播种")
        sow = do_request(
            auth_token,
            f"{BASE}/applet/game/dendrobium/sowing?inviteUserId=sIH3CMTkxHnniqbPfy1B8g%3D%3D",
            "执行石斛播种"
        )
        if sow and sow.get("code") == 200:
            step("✅", "石斛播种", "成功")
            logs.append("✅ 石斛播种成功")
        else:
            step("❌", "石斛播种", "失败")
            logs.append("❌ 石斛播种失败")
            return logs, False
        # 播种后再次查询确认
        confirm = do_request(auth_token, f"{BASE}/applet/game/dendrobium/get", "确认石斛状态")
        planted = bool(confirm and confirm.get("code") == 200 and confirm.get("data"))
        return logs, planted
    # 已播种：code==200 且有 data
    if info.get("code") == 200 and info.get("data"):
        step("✅", "石斛状态", "已种植，跳过播种")
        logs.append("✅ 石斛已种植，跳过播种")
        return logs, True
    # 其他未知情况，保守当已种植处理（避免重复播种报错）
    step("⚠️", "石斛状态", "未知，按已种植处理")
    logs.append("⚠️ 石斛状态未知，按已种植处理")
    return logs, True


def dendrobium_sign(auth_token: str) -> list:
    """石斛签到"""
    section("石斛签到", "🌿")
    logs = []
    resp = do_request(auth_token, f"{BASE}/applet/game/dendrobium/signIn/getUserSignInLog", "查询石斛签到状态")
    if resp and (resp.get("data") or {}).get("todaySignInStatus"):
        logs.append("✅ 石斛签到：今日已完成")
    else:
        do_sign = do_request(auth_token, f"{BASE}/applet/game/dendrobium/signIn", "执行石斛签到")
        logs.append("✅ 石斛签到成功" if do_sign and do_sign.get("code") == 200 else "❌ 石斛签到失败")
    return logs


def _article_done_count(auth_token: str) -> int:
    """取推文任务(type=5)已完成次数：解析 task/list 的 schedule 'x/3'"""
    lst = do_request(auth_token, f"{BASE}/applet/game/dendrobium/task/list", "查询任务进度")
    if not lst or lst.get("code") != 200 or not lst.get("data"):
        return -1
    for t in lst["data"]:
        if t.get("type") == 5:
            sch = (t.get("schedule") or "0/3")
            try:
                done = int(str(sch).split("/")[0])
            except Exception:
                done = 0
            return done
    return 0


def browse_articles(auth_token: str) -> list:
    """推文浏览（每日3次，每次等30-40秒）"""
    section("推文浏览", "📖")
    logs = []

    before = _article_done_count(auth_token)
    if before < 0:
        logs.append("❌ 查询推文任务进度失败")
        return logs
    if before >= 3:
        step("✅", "推文", f"今日已完成（{before}/3），跳过")
        return ["✅ 今日推文已完成，跳过"]

    step("📊", "推文进度", f"当前 {before}/3，开始补满")
    done = before
    for i in range(before + 1, 4):
        sec = 30 + int(os.urandom(1)[0] % 11)
        step("⏳", f"第{i}次浏览", f"等待{sec}秒模拟阅读")
        time.sleep(sec)
        do_request(auth_token, f"{BASE}/applet/game/dendrobium/article/completeRead", f"第{i}次推文浏览")
        # 不依赖 msg，复查进度
        after = _article_done_count(auth_token)
        if after > done:
            done = after
            logs.append(f"✅ 第{i}次浏览成功（进度 {done}/3）")
        else:
            logs.append(f"🚫 第{i}次未推进进度（可能需真机阅读或额度已满），停止")
            break
        time.sleep(2)

    if done >= 3:
        logs.append("🎉 今日推文浏览已补满 3/3")
    else:
        logs.append(f"⚠️ 今日推文仅完成 {done}/3（接口未推进进度，可能需真机阅读）")
    return logs


def buy_fertilizer(auth_token: str) -> list:
    """徽宝买肥料"""
    section("徽宝买肥料", "🛒")
    logs = []

    # 1. 查积分
    user_info = do_request(auth_token, f"{BASE}/applet/game/dendrobium/getUserInfo", "查询积分")
    if not user_info or user_info.get("code") != 200:
        logs.append("❌ 查询积分失败")
        return logs
    integrate = (user_info.get("data") or {}).get("integrate", 0)
    step("💰", "当前徽宝", str(integrate))

    # 2. 查商品
    goods_resp = do_request(auth_token, f"{BASE}/applet/game/dendrobium/goods/list?type=1", "查询肥料商品")
    if not goods_resp or goods_resp.get("code") != 200 or not goods_resp.get("data"):
        logs.append("❌ 查询商品列表失败")
        return logs

    # 按价格降序，优先买贵的（200g > 100g）
    goods_list = sorted(goods_resp["data"], key=lambda x: x.get("price", 0), reverse=True)
    min_price = min((x.get("price", 0) for x in goods_list if x.get("price", 0) > 0), default=0)
    remain = integrate

    for item in goods_list:
        price = item.get("price", 0)
        if price <= 0:
            continue
        max_count = remain // price
        if max_count <= 0:
            continue
        goods_name = item.get("goodsName", "")
        goods_id = item.get("goodsId", "")
        step("🛍️", f"购买 {goods_name}", f"x{max_count} (共{max_count * price}徽宝)")
        for i in range(max_count):
            order = do_request(
                auth_token,
                f"{BASE}/applet/game/dendrobium/order/placeOrder?goodsId={goods_id}&goodsNum=1",
                f"买{goods_name} 第{i+1}次"
            )
            if order and order.get("code") == 200:
                remain -= price
            else:
                break
            time.sleep(1)

    spent = integrate - remain
    if spent == 0 and remain > 0 and min_price > 0:
        step("⚠️", "徽宝不足", f"当前 {remain} 徽宝，不足最低 {min_price} 徽宝，无法购买")
    step("💰", "购买结果", f"花费 {spent} 徽宝，剩余 {remain} 徽宝")
    logs.append(f"💰 共花费 {spent} 徽宝，剩余 {remain} 徽宝")
    return logs


def exhaust_fertilizer(auth_token: str, planted: bool = True) -> list:
    """自动施肥（肥料<100g停止）"""
    section("自动施肥", "🌿")
    logs = []
    if not planted:
        step("⏭️", "施肥", "无培养中石斛，跳过")
        logs.append("⏭️ 没有正在培养中的石斛，跳过施肥")
        logs.append("🌿 共施肥0次")
        return logs
    count = 0
    while True:
        info = do_request(auth_token, f"{BASE}/applet/game/dendrobium/get", "查询肥料数量")
        if not info or info.get("code") != 200:
            # code==500 表示未种植，停止循环
            if info and info.get("code") == 500:
                logs.append("石斛已不在培养中，停止施肥")
            break
        val = (info.get("data") or {}).get("fertilizer", 0)
        if val < 100:
            logs.append(f"肥料剩余{val}g，停止")
            break
        do_request(auth_token, f"{BASE}/applet/game/dendrobium/fertilizer", f"施肥第{count+1}次")
        count += 1
        time.sleep(1)
    logs.append(f"共施肥{count}次")
    return logs


def run_account(openid: str) -> bool:
    mask = openid[:6] + "****" + openid[-4:] if len(openid) > 10 else "****"
    print(f"\n{'━' * 46}")
    print(f"  🍃 甜润世界 | {mask}")
    print(f"  🕒 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'━' * 46}")

    auth_token = get_ck(openid)
    if not auth_token:
        print("❌ 获取 ck 失败，跳过")
        return False

    all_logs = []

    # 1. 签到有奖
    all_logs.extend(sign_in_award(auth_token))

    # 2. 石斛播种（未种植则播种，已种植跳过）→ 返回是否已在培养
    plant_logs, planted = plant_dendrobium(auth_token)
    all_logs.extend(plant_logs)

    # 3. 石斛签到（需先种植，否则会提示"请先种植石斛"）
    all_logs.extend(dendrobium_sign(auth_token))

    # 4. 推文浏览
    all_logs.extend(browse_articles(auth_token))

    # 5. 徽宝买肥料
    all_logs.extend(buy_fertilizer(auth_token))

    # 6. 自动施肥（未种植则跳过）
    all_logs.extend(exhaust_fertilizer(auth_token, planted))

    print(f"\n{'─' * 46}")
    print(f"  📋 本账号执行汇总")
    print(f"{'─' * 46}")
    for line in all_logs:
        print(f"  {line}")
    return True


if __name__ == "__main__":
    results = []
    for i, openid in enumerate(ACCOUNTS):
        ok = run_account(openid)
        mask = openid[:6] + "****" + openid[-4:] if len(openid) > 10 else "****"
        results.append(f"{mask}: {'✅' if ok else '❌'}")
        if i < len(ACCOUNTS) - 1:
            time.sleep(5)

    summary = "\n".join(results)
    print(f"\n{'━' * 46}")
    print(f"  🏁 运行结果汇总")
    print(f"{'━' * 46}")
    print(summary)