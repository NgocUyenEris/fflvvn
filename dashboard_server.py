# -*- coding: utf-8 -*-
"""
FreeFire Level Up Bot - Web Dashboard + Login + Admin Panel
Embedded Async Web Server (aiohttp)

- Đăng nhập bằng cookie session (HttpOnly)
- Admin tạo user (slot + số ngày panel) và tạo admin khác
- User chỉ thấy / thao tác acc của chính mình
- User hết hạn hoặc bị cấm: không đăng nhập được, acc tự động nghỉ cày
"""

import asyncio
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import time
from typing import Dict, List, Any, Optional
from aiohttp import web

# ==================== EXP TABLE & LEVEL HELPER ====================

EXP_TABLE: Dict[int, int] = {
    1: 0, 2: 48, 3: 202, 4: 544, 5: 1012, 6: 1844, 7: 2792, 8: 3800,
    9: 4870, 10: 6004, 11: 7192, 12: 8448, 13: 9760, 14: 11140, 15: 12566,
    16: 14060, 17: 15610, 18: 17224, 19: 18902, 20: 20632, 21: 22424, 22: 24278,
    23: 26192, 24: 28166, 25: 30200, 26: 32294, 27: 34448, 28: 37804, 29: 41274,
    30: 44870, 31: 48582, 32: 53394, 33: 58566, 34: 64096, 35: 69994, 36: 76460,
    37: 83506, 38: 91128, 39: 99322, 40: 108092, 41: 120144, 42: 133266, 43: 147472,
    44: 162760, 45: 179126, 46: 196572, 47: 215368, 48: 235516, 49: 257010, 50: 279860,
    51: 304056, 52: 348318, 53: 394982, 54: 444044, 55: 495508, 56: 549364, 57: 633756,
    58: 721744, 59: 813336, 60: 908522, 61: 1041438, 62: 1180352, 63: 1325266,
    64: 1476184, 65: 1634300, 66: 1840946, 67: 2056594, 68: 2281242, 69: 2514880,
    70: 2757530, 71: 3059506, 72: 3372284, 73: 3699456, 74: 4041030, 75: 4397002,
    76: 4829104, 77: 5282204, 78: 5756304, 79: 6251408, 80: 6776502, 81: 7381324,
    82: 8043154, 83: 8752982, 84: 9510808, 85: 10316338, 86: 11277190, 87: 12291748,
    88: 13360304, 89: 14482858, 90: 15659418, 91: 17026708, 92: 18453950, 93: 19941280,
    94: 21488570, 95: 23095858, 96: 24763138, 97: 26490428, 98: 28378704, 99: 30124996,
    100: 32032884
}

def get_level_from_exp(exp: int) -> int:
    """Tự động tra cứu level chuẩn từ lượng EXP dựa trên EXP_TABLE"""
    current_lv = 1
    for lv, required_exp in sorted(EXP_TABLE.items(), key=lambda x: x[0]):
        if exp >= required_exp:
            current_lv = lv
        else:
            break
    return current_lv


TARGET_LEVEL = 100
TARGET_EXP = EXP_TABLE[TARGET_LEVEL]
DONE_STATUS = f"COMPLETED_LV{TARGET_LEVEL}"


# ==================== BOT STATE ====================

class BotState:
    def __init__(self):
        self.accounts: Dict[str, Dict[str, Any]] = {}
        self.logs: List[Dict[str, Any]] = []
        self.max_logs = 200
        self.total_matches = 0
        self.total_gained_exp = 0
        self.start_time = time.time()
        self.account_workers: Dict[str, asyncio.Task] = {}
        self.refresh_callbacks: Dict[str, Any] = {}
        self.account_credentials: Dict[str, Dict[str, Any]] = {}

    # Ánh xạ UID đăng nhập (accounts.json) <-> account_id trong game.
    # Lấy từ account_credentials do Main.py tự đăng ký (account_id + auth_uid).
    def game_id_of(self, login_uid: str) -> Optional[str]:
        cred = self.account_credentials.get(str(login_uid))
        if cred and cred.get("account_id"):
            return str(cred["account_id"])
        return None

    def login_uid_of(self, game_id: str) -> Optional[str]:
        cred = self.account_credentials.get(str(game_id))
        if cred and cred.get("auth_uid"):
            return str(cred["auth_uid"])
        return None

    def log(self, message: str, level: str = "info", uid: Optional[str] = None):
        entry = {
            "time": time.strftime("%H:%M:%S"),
            "level": level,
            "message": message,
            "uid": uid
        }
        self.logs.append(entry)
        if len(self.logs) > self.max_logs:
            self.logs.pop(0)

    def register_account(self, uid: str, nickname: str, region: str, level: int, exp: int, likes: int = 0):
        uid_str = str(uid)
        real_level = level if (level and level > 1) else (get_level_from_exp(exp) if exp > 0 else 1)
        initial_status = DONE_STATUS if (real_level >= TARGET_LEVEL or exp >= TARGET_EXP) else "ONLINE"

        if uid_str not in self.accounts:
            self.accounts[uid_str] = {
                "uid": uid_str,
                "nickname": nickname or f"Player_{uid_str[:6]}",
                "region": region or "BD",
                "level": real_level,
                "initial_exp": exp,
                "current_exp": exp,
                "gained_exp": 0,
                "likes": likes or 0,
                "status": initial_status,
                "target_level": TARGET_LEVEL,
                "matches_played": 0,
                "active_matches": 0,
                "last_match_time": None,
                "last_updated": time.strftime("%H:%M:%S")
            }
        else:
            acc = self.accounts[uid_str]
            if nickname:
                acc["nickname"] = nickname
            if region:
                acc["region"] = region
            acc["level"] = real_level
            if real_level >= TARGET_LEVEL or exp >= TARGET_EXP:
                acc["status"] = DONE_STATUS
            acc["current_exp"] = exp
            acc["gained_exp"] = max(0, exp - acc["initial_exp"])
            acc["likes"] = likes
            acc["last_updated"] = time.strftime("%H:%M:%S")
        self.recalc_totals()

    def update_exp(self, uid: str, current_exp: int, level: Optional[int] = None):
        uid_str = str(uid)
        if uid_str in self.accounts:
            acc = self.accounts[uid_str]
            old_exp = acc["current_exp"]
            acc["current_exp"] = current_exp
            calc_level = level if (level is not None and level > 0) else acc.get("level", 1)
            
            if current_exp >= TARGET_EXP and calc_level < TARGET_LEVEL:
                calc_level = TARGET_LEVEL

            acc["level"] = calc_level
            if calc_level >= TARGET_LEVEL or current_exp >= TARGET_EXP:
                acc["status"] = DONE_STATUS

            acc["gained_exp"] = max(0, current_exp - acc["initial_exp"])
            acc["last_updated"] = time.strftime("%H:%M:%S")
            diff = current_exp - old_exp
            if diff > 0:
                self.log(f"Account {acc['nickname']} ({uid_str}) gained +{diff} EXP! Total Gained: +{acc['gained_exp']} (Lv.{calc_level})", "success", uid_str)
            self.recalc_totals()

    def update_status(self, uid: str, status: str, active_matches: Optional[int] = None):
        uid_str = str(uid)
        if uid_str in self.accounts:
            acc = self.accounts[uid_str]
            if acc.get("level", 1) >= TARGET_LEVEL or acc.get("current_exp", 0) >= TARGET_EXP:
                acc["status"] = DONE_STATUS
            else:
                acc["status"] = status

            if active_matches is not None:
                acc["active_matches"] = active_matches
            acc["last_updated"] = time.strftime("%H:%M:%S")

    def increment_match(self, uid: str):
        uid_str = str(uid)
        self.total_matches += 1
        if uid_str in self.accounts:
            self.accounts[uid_str]["matches_played"] += 1
            self.accounts[uid_str]["last_match_time"] = time.strftime("%H:%M:%S")
            self.accounts[uid_str]["last_updated"] = time.strftime("%H:%M:%S")
            self.log(f"Account {self.accounts[uid_str]['nickname']} finished Match #{self.accounts[uid_str]['matches_played']}", "info", uid_str)

    def recalc_totals(self):
        self.total_gained_exp = sum(acc.get("gained_exp", 0) for acc in self.accounts.values())


bot_state = BotState()


# ==================== STORAGE / AUTH HELPERS ====================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
USERS_FILE = "users.json"
ACCOUNTS_FILE = "accounts.json"
_DATA_DIR = os.environ.get("DATA_DIR", "")
ANNOUNCE_FILE = (os.path.join(_DATA_DIR, "announcement.json")
                 if _DATA_DIR and os.path.isdir(_DATA_DIR) and os.access(_DATA_DIR, os.W_OK)
                 else "announcement.json")
SESSION_COOKIE = "ffvn_session"
SESSION_TTL = 7 * 86400
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")

sessions: Dict[str, Dict[str, Any]] = {}
login_attempts: Dict[str, List[float]] = {}
suspended_users = set()


def now() -> float:
    return time.time()


def load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            return json.loads(content) if content else default
    except Exception:
        return default


def save_json(path: str, data) -> None:
    path = os.path.realpath(path)  # giữ nguyên symlink tới /var/data (Render Disk)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def load_users() -> List[Dict[str, Any]]:
    data = load_json(USERS_FILE, [])
    return data if isinstance(data, list) else []


def save_users(users: List[Dict[str, Any]]) -> None:
    save_json(USERS_FILE, users)


def load_accounts() -> List[Dict[str, Any]]:
    data = load_json(ACCOUNTS_FILE, [])
    return data if isinstance(data, list) else []


def load_announcement() -> Dict[str, Any]:
    data = load_json(ANNOUNCE_FILE, {})
    if not isinstance(data, dict):
        data = {}
    return {
        "enabled": bool(data.get("enabled", False)),
        "title": str(data.get("title") or "Thông báo"),
        "content": str(data.get("content") or ""),
        "version": int(data.get("version") or 0),
        "updated_at": data.get("updated_at"),
        "updated_by": data.get("updated_by"),
    }


def hash_password(password: str, salt: Optional[str] = None):
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), 200_000)
    return salt, h.hex()


def verify_password(user: Dict[str, Any], password: str) -> bool:
    _, h = hash_password(password, user["salt"])
    return hmac.compare_digest(h, user["hash"])


def make_user(username: str, password: str, role: str, slots: Optional[int], days: Optional[int]) -> Dict[str, Any]:
    salt, h = hash_password(password)
    return {
        "id": secrets.token_hex(4),
        "username": username,
        "salt": salt,
        "hash": h,
        "role": role,
        "slots": None if role == "admin" else slots,
        "created_at": now(),
        "expires_at": None if role == "admin" else now() + (days or 1) * 86400,
        "last_login": None,
        "banned": False,
    }


def find_user(username: str) -> Optional[Dict[str, Any]]:
    username = (username or "").strip().lower()
    for u in load_users():
        if u["username"].lower() == username:
            return u
    return None


def user_active(u: Dict[str, Any]) -> bool:
    if u.get("banned"):
        return False
    if u.get("role") == "admin":
        return True
    exp = u.get("expires_at")
    return exp is not None and exp > now()


def ensure_default_admin() -> None:
    users = load_users()
    if any(u.get("role") == "admin" for u in users):
        return
    name = os.environ.get("FFVN_ADMIN_USER", "admin").strip().lower()
    generated = not os.environ.get("FFVN_ADMIN_PASS")
    pw = os.environ.get("FFVN_ADMIN_PASS") or secrets.token_urlsafe(9)
    users.append(make_user(name, pw, "admin", None, None))
    save_users(users)
    msg = f"[+] Tạo admin mặc định: {name}"
    if generated:
        msg += f" / mật khẩu: {pw}  (hãy lưu lại, sẽ không hiện lần nữa)"
    print(f"\033[93m{msg}\033[0m")


def owned_uids(username: str) -> set:
    return {str(a["uid"]) for a in load_accounts() if a.get("uid") and a.get("owner") == username}


def owned_ids(username: str) -> set:
    """Tất cả ID mà user sở hữu: UID đăng nhập + account_id trong game tương ứng."""
    ids = set()
    for login_uid in owned_uids(username):
        ids.add(login_uid)
        game_id = bot_state.game_id_of(login_uid)
        if game_id:
            ids.add(game_id)
    return ids


def related_ids(uid: str) -> set:
    """Từ 1 ID bất kỳ (login hoặc game) trả về cả hai dạng."""
    uid = str(uid)
    out = {uid}
    g = bot_state.game_id_of(uid)
    if g:
        out.add(g)
    l = bot_state.login_uid_of(uid)
    if l:
        out.add(l)
    return out


def count_owned_entries(username: str) -> int:
    return sum(1 for a in load_accounts() if a.get("owner") == username)


def get_session_user(request: web.Request) -> Optional[Dict[str, Any]]:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    s = sessions.get(token)
    if not s:
        return None
    if s["exp"] < now():
        sessions.pop(token, None)
        return None
    u = find_user(s["username"])
    if not u or not user_active(u):
        sessions.pop(token, None)
        return None
    return u


def drop_sessions(username: str) -> None:
    for t in [t for t, s in sessions.items() if s["username"] == username]:
        sessions.pop(t, None)


def read_template(name: str) -> str:
    path = os.path.join(TEMPLATES_DIR, name)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return f"<h1>templates/{name} not found!</h1>"


def html_response(text: str) -> web.Response:
    return web.Response(text=text, content_type="text/html", charset="utf-8",
                        headers={"Cache-Control": "no-store"})


def err(msg: str, status: int = 200) -> web.Response:
    return web.json_response({"status": "error", "error": msg}, status=status)


def status_kind(acc: Dict[str, Any]) -> str:
    s = str(acc.get("status", "")).upper()
    if "COMPLETED" in s or "DONE" in s:
        return "done"
    if "BAN" in s:
        return "ban"
    if "OFFLINE" in s or "ERROR" in s or "LOGIN_FAIL" in s:
        return "off"
    if any(k in s for k in ("MATCH", "SEARCH", "QUEUE", "FARM")) or (acc.get("active_matches") or 0) > 0:
        return "farm"
    return "on"


# ==================== MIDDLEWARE ====================

PUBLIC_PATHS = {"/login", "/api/login", "/favicon.ico"}


@web.middleware
async def auth_middleware(request: web.Request, handler):
    path = request.path
    if path in PUBLIC_PATHS or path.startswith("/static/"):
        return await handler(request)
    user = get_session_user(request)
    if not user:
        if path.startswith("/api/"):
            return web.json_response({"status": "error", "error": "unauthorized"}, status=401)
        raise web.HTTPFound("/login")
    request["user"] = user
    if (path == "/admin" or path.startswith("/api/admin")) and user.get("role") != "admin":
        if path.startswith("/api/"):
            return web.json_response({"status": "error", "error": "forbidden"}, status=403)
        raise web.HTTPFound("/")
    return await handler(request)


# ==================== PAGE + AUTH HANDLERS ====================

async def handle_index(request: web.Request) -> web.Response:
    return html_response(read_template("index.html"))


async def handle_static(request: web.Request) -> web.Response:
    name = request.match_info.get("name", "favicon.ico")
    if name == "favicon.ico":
        name = "logo.png"
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.(png|jpg|svg|webp)", name):
        raise web.HTTPNotFound()
    path = os.path.join(BASE_DIR, "static", name)
    if not os.path.isfile(path):
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Cache-Control": "public, max-age=86400"})


async def handle_login_page(request: web.Request) -> web.Response:
    if get_session_user(request):
        raise web.HTTPFound("/")
    return html_response(read_template("login.html"))


async def handle_admin_page(request: web.Request) -> web.Response:
    return html_response(read_template("admin.html"))


async def handle_login(request: web.Request) -> web.Response:
    try:
        ip = request.remote or "?"
        t = now()
        attempts = [x for x in login_attempts.get(ip, []) if t - x < 300]
        login_attempts[ip] = attempts
        if len(attempts) >= 8:
            return err("Thử quá nhiều lần. Vui lòng đợi vài phút.", 429)

        data = await request.json()
        username = str(data.get("username", "")).strip().lower()
        password = str(data.get("password", ""))
        u = find_user(username)

        if not u:
            hash_password(password)
            ok = False
        else:
            ok = verify_password(u, password)

        if not ok:
            login_attempts[ip].append(t)
            return err("Sai tên đăng nhập hoặc mật khẩu")
        if u.get("banned"):
            return err("Tài khoản đã bị cấm")
        if not user_active(u):
            return err("Panel đã hết hạn, liên hệ admin để gia hạn")

        users = load_users()
        for x in users:
            if x["username"] == u["username"]:
                x["last_login"] = now()
        save_users(users)

        token = secrets.token_urlsafe(32)
        sessions[token] = {"username": u["username"], "exp": now() + SESSION_TTL}
        resp = web.json_response({"status": "ok", "role": u["role"]})
        resp.set_cookie(SESSION_COOKIE, token, max_age=SESSION_TTL, httponly=True,
                        samesite="Lax", path="/")
        return resp
    except Exception as e:
        return err(str(e))


async def handle_logout(request: web.Request) -> web.Response:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        sessions.pop(token, None)
    resp = web.json_response({"status": "ok"})
    resp.del_cookie(SESSION_COOKIE, path="/")
    return resp


async def handle_me(request: web.Request) -> web.Response:
    u = request["user"]
    return web.json_response({
        "username": u["username"],
        "role": u["role"],
        "slots": u.get("slots"),
        "used": count_owned_entries(u["username"]),
        "expires_at": u.get("expires_at"),
    })


# ==================== ANNOUNCEMENT ====================

def _session_of(request: web.Request) -> Optional[Dict[str, Any]]:
    return sessions.get(request.cookies.get(SESSION_COOKIE) or "")


async def handle_get_announcement(request: web.Request) -> web.Response:
    ann = load_announcement()
    sess = _session_of(request) or {}
    has_content = ann["enabled"] and bool(ann["content"].strip())
    ann["show"] = bool(has_content and sess.get("notice_seen_ver") != ann["version"])
    return web.json_response(ann)


async def handle_ack_announcement(request: web.Request) -> web.Response:
    sess = _session_of(request)
    if sess is not None:
        sess["notice_seen_ver"] = load_announcement()["version"]
    return web.json_response({"status": "ok"})


async def handle_admin_set_announcement(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        title = str(data.get("title", "")).strip()[:100] or "Thông báo"
        content = str(data.get("content", "")).strip()[:2000]
        enabled = bool(data.get("enabled"))
        if enabled and not content:
            return err("Nội dung thông báo không được để trống")
        old = load_announcement()
        save_json(ANNOUNCE_FILE, {
            "enabled": enabled,
            "title": title,
            "content": content,
            "version": old["version"] + 1,   # tăng version -> mọi người thấy lại thông báo mới
            "updated_at": now(),
            "updated_by": request["user"]["username"],
        })
        bot_state.log(f"Admin {request['user']['username']} cập nhật thông báo ({'bật' if enabled else 'tắt'}).", "info")
        return web.json_response({"status": "ok"})
    except Exception as e:
        return err(str(e))


# ==================== DASHBOARD API ====================

def pending_placeholders(owner: Optional[str] = None) -> List[Dict[str, Any]]:
    """Acc đã lưu trong accounts.json nhưng bot chưa đăng nhập xong -> hiện thẻ 'đang kết nối'."""
    out = []
    for a in load_accounts():
        login_uid = str(a.get("uid") or "")
        if not login_uid:
            continue
        if owner is not None and a.get("owner") != owner:
            continue
        if login_uid in bot_state.accounts:
            continue
        gid = bot_state.game_id_of(login_uid)
        if gid and gid in bot_state.accounts:
            continue
        out.append({
            "uid": login_uid, "nickname": f"Player_{login_uid[:6]}", "region": "VN",
            "level": 1, "initial_exp": 0, "current_exp": 0, "gained_exp": 0, "likes": 0,
            "status": "CONNECTING", "target_level": TARGET_LEVEL, "matches_played": 0, "active_matches": 0,
            "last_match_time": None, "last_updated": time.strftime("%H:%M:%S"),
        })
    return out


async def handle_get_stats(request: web.Request) -> web.Response:
    # Mọi người dùng đều xem được toàn bộ acc (chỉ admin mới được xoá)
    visible = list(bot_state.accounts.values()) + pending_placeholders()
    logs = bot_state.logs[-60:]
    total_matches = bot_state.total_matches
    total_exp = bot_state.total_gained_exp

    visible.sort(key=lambda x: x.get("gained_exp", 0), reverse=True)
    return web.json_response({
        "total_accounts": len(visible),
        "total_matches": total_matches,
        "total_gained_exp": total_exp,
        "accounts": visible,
        "logs": logs,
        "uptime": int(time.time() - bot_state.start_time)
    })


def parse_raw_lines(raw_text: str) -> List[Dict[str, str]]:
    out = []
    for line in raw_text.splitlines()[:500]:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            parts = line.split(":", 1)
        elif "|" in line:
            parts = line.split("|", 1)
        elif "," in line:
            parts = line.split(",", 1)
        else:
            parts = line.split(None, 1)
        if len(parts) == 2:
            u, p = parts[0].strip(), parts[1].strip()
            if u and p:
                out.append({"uid": u, "password": p})
    return out


async def handle_add_account(request: web.Request) -> web.Response:
    try:
        user = request["user"]
        is_admin = user["role"] == "admin"
        username = user["username"]
        data = await request.json()
        existing = load_accounts()

        new_entries: List[Dict[str, str]] = []
        if data.get("raw_text"):
            new_entries = parse_raw_lines(str(data["raw_text"]))
            if not new_entries:
                return err("Không có dòng hợp lệ (định dạng UID:mật khẩu)")
        elif "uid" in data and "password" in data:
            uid = str(data["uid"]).strip()
            pwd = str(data["password"]).strip()
            if not uid or not pwd:
                return err("UID and Password are required")
            new_entries = [{"uid": uid, "password": pwd}]
        elif "token" in data:
            if not is_admin:
                return err("Chỉ admin được thêm bằng token")
            token = str(data["token"]).strip()
            if not token:
                return err("Token is required")
            new_entries = [{"token": token}]
        else:
            return err("Invalid payload")

        merged = list(existing)
        to_start: List[Dict[str, str]] = []
        for ne in new_entries:
            uid_val = str(ne.get("uid", ""))
            token_val = ne.get("token", "")
            
            match = None
            for a in merged:
                if uid_val and str(a.get("uid")) == uid_val:
                    match = a
                    break
                elif token_val and a.get("token") == token_val:
                    match = a
                    break

            if match:
                prev_owner = match.get("owner", "")
                if prev_owner and prev_owner != username and not is_admin:
                    return err(f"UID {uid_val} đã thuộc sở hữu của người dùng khác")
                ne["owner"] = prev_owner if prev_owner else username
                merged = [a for a in merged if a is not match]
                # đổi mật khẩu/token -> chạy lại worker với thông tin mới
                if match.get("password") != ne.get("password") or match.get("token") != ne.get("token"):
                    old_task = bot_state.account_workers.pop(uid_val or str(token_val)[:10], None)
                    if old_task:
                        old_task.cancel()
            else:
                ne["owner"] = username

            merged.append(ne)
            to_start.append(ne)

        if not is_admin:
            slots = int(user.get("slots") or 0)
            used = sum(1 for a in merged if a.get("owner") == username)
            if used > slots:
                return err(f"Vượt giới hạn slot ({used}/{slots}). Liên hệ admin để tăng slot.")

        save_json(ACCOUNTS_FILE, merged)
        bot_state.log(f"Accounts updated/added by {username}.", "success")

        cb = bot_state.refresh_callbacks.get("on_account_added")
        if cb:
            for ne in to_start:
                if ne.get("uid"):
                    key, payload = str(ne["uid"]), {"uid": str(ne["uid"]), "password": ne.get("password", "")}
                elif ne.get("token"):
                    key, payload = str(ne["token"])[:10], {"token": ne["token"]}
                else:
                    continue
                running = bot_state.account_workers.get(key)
                if running and not running.done():
                    continue  # đã có worker đang chạy
                asyncio.create_task(cb(payload))

        return web.json_response({"status": "ok"})
    except Exception as e:
        return err(str(e))


def can_touch(user: Dict[str, Any], uid: str) -> bool:
    if user["role"] == "admin":
        return True
    return bool(related_ids(uid) & owned_ids(user["username"]))


def remove_uid_runtime(uid: str) -> None:
    for k in related_ids(uid):
        bot_state.accounts.pop(k, None)
        task = bot_state.account_workers.pop(k, None)
        if task:
            task.cancel()
    bot_state.recalc_totals()


async def handle_delete_account(request: web.Request) -> web.Response:
    try:
        user = request["user"]
        if user.get("role") != "admin":
            return err("Chỉ admin được xoá tài khoản", 403)
        data = await request.json()
        uid = str(data.get("uid")).strip()

        ids = related_ids(uid)
        existing = [a for a in load_accounts() if str(a.get("uid")) not in ids]
        save_json(ACCOUNTS_FILE, existing)
        remove_uid_runtime(uid)
        bot_state.log(f"Account {uid} removed from rotation.", "warning", uid)
        return web.json_response({"status": "ok"})
    except Exception as e:
        return err(str(e))


async def handle_refresh_account(request: web.Request) -> web.Response:
    try:
        user = request["user"]
        data = await request.json()
        uid = str(data.get("uid")).strip()
        if "on_refresh_account" in bot_state.refresh_callbacks:
            asyncio.create_task(bot_state.refresh_callbacks["on_refresh_account"](uid))
        return web.json_response({"status": "ok"})
    except Exception as e:
        return err(str(e))


# ==================== ADMIN API ====================

def build_user_row(u: Dict[str, Any], entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    uname = u["username"]
    mine = [a for a in entries if a.get("owner") == uname]
    uids = [str(a["uid"]) for a in mine if a.get("uid")]
    gids = [(bot_state.game_id_of(x) or x) for x in uids]
    states = [bot_state.accounts[g] for g in gids if g in bot_state.accounts]
    return {
        "id": u["id"],
        "username": uname,
        "role": u["role"],
        "banned": bool(u.get("banned")),
        "active": user_active(u),
        "created_at": u.get("created_at"),
        "expires_at": u.get("expires_at"),
        "last_login": u.get("last_login"),
        "slots": u.get("slots"),
        "acc_count": len(mine),
        "gained_exp": sum(s.get("gained_exp", 0) for s in states),
        "matches": sum(s.get("matches_played", 0) for s in states),
        "avg_level": round(sum(s.get("level", 0) for s in states) / len(states)) if states else 0,
        "farming": sum(1 for s in states if status_kind(s) == "farm"),
        "accounts": [
            {"uid": g, "nickname": bot_state.accounts.get(g, {}).get("nickname", x),
             "level": bot_state.accounts.get(g, {}).get("level", 0),
             "kind": status_kind(bot_state.accounts[g]) if g in bot_state.accounts else "off"}
            for x, g in zip(uids, gids)
        ],
    }


async def handle_admin_overview(request: web.Request) -> web.Response:
    users = load_users()
    entries = load_accounts()
    rows = [build_user_row(u, entries) for u in users]
    dist: Dict[str, int] = {}
    for a in bot_state.accounts.values():
        k = status_kind(a)
        dist[k] = dist.get(k, 0) + 1
    midnight = time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d"))
    return web.json_response({
        "users": rows,
        "total_users": len(users),
        "active_users": sum(1 for u in users if user_active(u)),
        "admins": sum(1 for u in users if u["role"] == "admin"),
        "admins_today": sum(1 for u in users if u["role"] == "admin" and (u.get("last_login") or 0) >= midnight),
        "banned": sum(1 for u in users if u.get("banned")),
        "total_accounts": len(entries),
        "farming": dist.get("farm", 0),
        "total_gained_exp": bot_state.total_gained_exp,
        "uptime": int(time.time() - bot_state.start_time),
        "distribution": dist,
        "me": request["user"]["username"],
    })


def parse_int(v, lo, hi, name):
    try:
        n = int(v)
    except Exception:
        raise ValueError(f"{name} không hợp lệ")
    if n < lo or n > hi:
        raise ValueError(f"{name} phải từ {lo} đến {hi}")
    return n


async def handle_admin_create_user(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        role = str(data.get("role", "user"))
        if role not in ("user", "admin"):
            return err("Vai trò không hợp lệ")
        username = str(data.get("username", "")).strip().lower()
        password = str(data.get("password", ""))
        if not USERNAME_RE.match(username):
            return err("Tên đăng nhập 3-32 ký tự (chữ, số, _ . -)")
        if len(password) < 4:
            return err("Mật khẩu tối thiểu 4 ký tự")
        if find_user(username):
            return err("Tên đăng nhập đã tồn tại")
        slots = days = None
        if role == "user":
            slots = parse_int(data.get("slots"), 1, 1000, "Số slot")
            days = parse_int(data.get("days"), 1, 3650, "Số ngày")
        users = load_users()
        users.append(make_user(username, password, role, slots, days))
        save_users(users)
        bot_state.log(f"Admin {request['user']['username']} created {role} '{username}'.", "success")
        return web.json_response({"status": "ok"})
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(str(e))


async def handle_admin_update_user(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        username = str(data.get("username", "")).strip().lower()
        users = load_users()
        target = next((u for u in users if u["username"].lower() == username), None)
        if not target:
            return err("Không tìm thấy user")
        me = request["user"]["username"]

        if "slots" in data and target["role"] == "user":
            target["slots"] = parse_int(data["slots"], 1, 1000, "Số slot")
        if "extend_days" in data and target["role"] == "user":
            d = parse_int(data["extend_days"], 1, 3650, "Số ngày")
            base = max(now(), target.get("expires_at") or 0)
            target["expires_at"] = base + d * 86400
        if data.get("password"):
            pw = str(data["password"])
            if len(pw) < 4:
                return err("Mật khẩu tối thiểu 4 ký tự")
            target["salt"], target["hash"] = hash_password(pw)
            drop_sessions(target["username"])
        if "banned" in data:
            want = bool(data["banned"])
            if want and target["username"] == me:
                return err("Không thể tự cấm chính mình")
            if want and target["role"] == "admin":
                active_admins = [u for u in users if u["role"] == "admin" and not u.get("banned") and u is not target]
                if not active_admins:
                    return err("Phải còn ít nhất một admin hoạt động")
            target["banned"] = want
            if want:
                drop_sessions(target["username"])

        save_users(users)
        return web.json_response({"status": "ok"})
    except ValueError as e:
        return err(str(e))
    except Exception as e:
        return err(str(e))


async def handle_admin_delete_user(request: web.Request) -> web.Response:
    try:
        data = await request.json()
        username = str(data.get("username", "")).strip().lower()
        me = request["user"]["username"]
        users = load_users()
        target = next((u for u in users if u["username"].lower() == username), None)
        if not target:
            return err("Không tìm thấy user")
        if target["username"] == me:
            return err("Không thể tự xoá chính mình")
        if target["role"] == "admin" and sum(1 for u in users if u["role"] == "admin") <= 1:
            return err("Phải còn ít nhất một admin")

        entries = load_accounts()
        uids = {str(a["uid"]) for a in entries if a.get("uid") and a.get("owner") == target["username"]}
        entries = [a for a in entries if a.get("owner") != target["username"]]
        save_json(ACCOUNTS_FILE, entries)
        for uid in uids:
            remove_uid_runtime(uid)

        users = [u for u in users if u is not target and u["username"] != target["username"]]
        save_users(users)
        drop_sessions(target["username"])
        suspended_users.discard(target["username"])
        bot_state.log(f"Admin {me} deleted user '{target['username']}' ({len(uids)} acc).", "warning")
        return web.json_response({"status": "ok"})
    except Exception as e:
        return err(str(e))


# ==================== EXPIRY ENFORCER ====================

def suspend_user_accounts(username: str) -> None:
    for uid in owned_uids(username):
        task = bot_state.account_workers.pop(uid, None)
        if task:
            task.cancel()
        acc = bot_state.accounts.get((bot_state.game_id_of(uid) or uid))
        if acc and acc.get("level", 1) < TARGET_LEVEL and acc.get("current_exp", 0) < TARGET_EXP:
            acc["status"] = "OFFLINE"
            acc["active_matches"] = 0
    bot_state.log(f"User '{username}' hết hạn/bị cấm: acc tạm nghỉ cày.", "warning")


async def resume_user_accounts(username: str) -> None:
    cb = bot_state.refresh_callbacks.get("on_account_added")
    if not cb:
        return
    for a in load_accounts():
        if a.get("owner") != username or not a.get("uid"):
            continue
        if str(a["uid"]) in bot_state.account_workers:
            continue
        try:
            await cb({"uid": str(a["uid"]), "password": a.get("password", "")})
        except Exception:
            pass
    bot_state.log(f"User '{username}' được kích hoạt lại: acc tiếp tục cày.", "success")


async def enforce_expiry_loop():
    while True:
        try:
            for u in load_users():
                name = u["username"]
                if user_active(u):
                    if name in suspended_users:
                        suspended_users.discard(name)
                        await resume_user_accounts(name)
                elif name not in suspended_users:
                    suspended_users.add(name)
                    suspend_user_accounts(name)
            for t in [t for t, s in sessions.items() if s["exp"] < now()]:
                sessions.pop(t, None)
        except Exception:
            pass
        await asyncio.sleep(30)


# ==================== SERVER ====================

async def start_web_dashboard(host: str = "0.0.0.0", port: int = 5000):
    ensure_default_admin()

    app = web.Application(middlewares=[auth_middleware])
    app.router.add_get("/", handle_index)
    app.router.add_get("/static/{name}", handle_static)
    app.router.add_get("/favicon.ico", handle_static)
    app.router.add_get("/login", handle_login_page)
    app.router.add_get("/admin", handle_admin_page)

    app.router.add_post("/api/login", handle_login)
    app.router.add_post("/api/logout", handle_logout)
    app.router.add_get("/api/me", handle_me)
    app.router.add_get("/api/announcement", handle_get_announcement)
    app.router.add_post("/api/announcement/ack", handle_ack_announcement)
    app.router.add_post("/api/admin/announcement", handle_admin_set_announcement)

    app.router.add_get("/api/stats", handle_get_stats)
    app.router.add_post("/api/account/add", handle_add_account)
    app.router.add_post("/api/account/delete", handle_delete_account)
    app.router.add_post("/api/account/refresh", handle_refresh_account)

    app.router.add_get("/api/admin/overview", handle_admin_overview)
    app.router.add_post("/api/admin/user/create", handle_admin_create_user)
    app.router.add_post("/api/admin/user/update", handle_admin_update_user)
    app.router.add_post("/api/admin/user/delete", handle_admin_delete_user)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    app["expiry_task"] = asyncio.create_task(enforce_expiry_loop())
    print(f"\033[92m[+] Web Dashboard running on http://localhost:{port}\033[0m")
