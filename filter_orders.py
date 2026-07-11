"""
autoab.net 订单过滤脚本
功能：检测高额/特定订单并推送 Telegram（静默）
规则：
  - JustGrab >= RM150
  - Plus >= RM150
  - 6 Seats >= RM150
  - Advance Standard (To/From KLIA) 全部金额
"""
import os
import json
import sys
import time
from datetime import datetime
from pathlib import Path
try:
    import requests
except ImportError:
    print("请先安装 requests: pip install requests")
    sys.exit(1)

CONFIG = {
    "autoab_username": os.environ.get("AUTOAB_USERNAME", ""),
    "autoab_password": os.environ.get("AUTOAB_PASSWORD", ""),
    "autoab_grabid": os.environ.get("AUTOAB_GRABID", ""),
    "telegram_bot_token": os.environ.get("TELEGRAM_BOT_TOKEN", ""),
    "telegram_chat_id": os.environ.get("TELEGRAM_CHAT_ID", ""),
}

BASE_URL = "https://beta.autoab.net/index.php/api"
LOGIN_URL = f"{BASE_URL}/user/login"
PROFILE_URL = f"{BASE_URL}/user/profile"
HISTORY_URL = f"{BASE_URL}/grab/history_orders"
STATE_FILE = Path(__file__).parent / "state.json"


def send_telegram(message: str) -> bool:
    token = CONFIG["telegram_bot_token"]
    chat_id = CONFIG["telegram_chat_id"]
    if not token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = requests.post(url, json={
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "disable_notification": True,
        }, timeout=15)
        return resp.json().get("ok", False)
    except Exception as e:
        print(f"[x] Telegram 异常: {e}")
        return False


def notify_filtered(order: dict, category: str) -> bool:
    title = order.get("order_title", "")
    if title.startswith("Advance Standard"):
        title = title[len("Advance Standard"):].strip().lstrip("|").strip()
    amount = order.get("order_amount", "?")
    pickup_time = order.get("order_time", "?")

    msg = f"💰 <b>{amount} 马币</b>\n🕐 {pickup_time}\n📋 {title}"
    return send_telegram(msg)


def fetch_all_history(session, keyword, min_price):
    """遍历所有页，返回全部匹配订单"""
    all_orders = []
    page = 1
    total_pages = 1
    while page <= total_pages:
        try:
            resp = session.get(HISTORY_URL, params={
                "grabid": CONFIG["autoab_grabid"],
                "day": "today",
                "keyword": keyword,
                "min_price": min_price,
                "size": 100,
                "page": page,
            }, timeout=15)
            data = resp.json()
            if data.get("code") != 1:
                break
            orders = data["data"].get("list", [])
            all_orders.extend(orders)
            # 更新总页数
            total_pages = data["data"].get("pagination", {}).get("total_page", 1)
            page += 1
            time.sleep(0.3)
        except Exception as e:
            print(f"[x] 查询失败 ({keyword} 第{page}页): {e}")
            break
    return all_orders


def login_and_get_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
    resp = session.post(LOGIN_URL, data={
        "username": CONFIG["autoab_username"],
        "password": CONFIG["autoab_password"],
        "keeptime": "31536000",
    }, timeout=15)
    data = resp.json()
    if data.get("code") != 1:
        raise Exception(f"登录失败: {data.get('msg', '未知错误')}")
    print(f"[+] 登录成功: {data['data']['userinfo']['username']}")
    return session


def try_saved_session(session: requests.Session) -> bool:
    try:
        resp = session.get(PROFILE_URL, timeout=10)
        data = resp.json()
        if data.get("code") == 1:
            print("[+] 使用已有 session（无需登录）")
            return True
        return False
    except Exception:
        return False


def load_state() -> dict:
    default = {"notified_ids": [], "phpsessid": None}
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return default
    return default


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def main():
    print(f"[*] 启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    if not CONFIG["telegram_bot_token"] or not CONFIG["telegram_chat_id"]:
        print("[!] 请设置 Telegram 配置")
        return
    if not CONFIG["autoab_username"] or not CONFIG["autoab_password"]:
        print("[!] 请设置 autoab 账号")
        return

    state = load_state()
    notified = set(state.get("notified_high", []))
    saved_phpsessid = state.get("phpsessid")
    print(f"[*] 已通知数: {len(notified)}")

    session = requests.Session()
    session.headers.update({"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
    session_ok = False

    if saved_phpsessid:
        session.cookies.set("PHPSESSID", saved_phpsessid, domain="beta.autoab.net", path="/")
        if try_saved_session(session):
            session_ok = True
        else:
            print("[*] 已有 session 过期，重新登录")

    if not session_ok:
        session = login_and_get_session()
        for cookie in session.cookies:
            if cookie.name == "PHPSESSID":
                state["phpsessid"] = cookie.value
                break

    total_new = 0
    new_set = set(notified)

    # JustGrab >= 150
    for o in fetch_all_history(session, "JustGrab", 150):
        if o["order_no"] not in notified:
            notify_filtered(o, "JustGrab")
            new_set.add(o["order_no"])
            total_new += 1
            time.sleep(0.3)

    # Plus >= 150
    for o in fetch_all_history(session, "Plus", 150):
        if o["order_no"] not in notified:
            notify_filtered(o, "Plus")
            new_set.add(o["order_no"])
            total_new += 1
            time.sleep(0.3)

    # 6 Seats >= 150
    for o in fetch_all_history(session, "6 seats", 150):
        if o["order_no"] not in notified:
            notify_filtered(o, "6 Seats")
            new_set.add(o["order_no"])
            total_new += 1
            time.sleep(0.3)

    # Advance Standard 去/回机场（全部金额）
    for o in fetch_all_history(session, "KLIA", 0):
        if o["order_no"] not in notified:
            title = o["order_title"]
            if "Advance Standard" in title:
                if "To KLIA" in title and "Plus" not in title and "6 seats" not in title and "JustGrab" not in title:
                    notify_filtered(o, "To KLIA")
                    new_set.add(o["order_no"])
                    total_new += 1
                    time.sleep(0.3)
                elif "From KLIA" in title and "Plus" not in title and "6 seats" not in title and "JustGrab" not in title:
                    notify_filtered(o, "From KLIA")
                    new_set.add(o["order_no"])
                    total_new += 1
                    time.sleep(0.3)

    state["notified_high"] = list(new_set)
    save_state(state)
    print(f"[*] 新通知: {total_new} 条（累计 {len(new_set)} 条）")
    print("[*] 完成")


if __name__ == "__main__":
    main()
