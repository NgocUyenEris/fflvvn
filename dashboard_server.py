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

# ==================== BOT STATE (giữ nguyên để Main.py dùng) ====================

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

        # Kiểm tra nếu đã đạt level 21 thì gán trạng thái hoàn thành luôn
        initial_status = "COMPLETED_LV21" if (level and level >= 21) else "ONLINE"

        if uid_str not in self.accounts:
            self.accounts[uid_str] = {
                "uid": uid_str,
                "nickname": nickname or f"Player_{uid_str[:6]}",
                "region": region or "BD",
                "level": level or 1,
                "initial_exp": exp,
                "current_exp": exp,
                "gained_exp": 0,
                "likes": likes or 0,
                "status": initial_status,
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
            if level:
                acc["level"] = level
                if level >= 21:
                    acc["status"] = "COMPLETED_LV21"
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
            if level is not None and level > 0:
                acc["level"] = level
                if level >= 21:
                    acc["status"] = "COMPLETED_LV21"
            acc["gained_exp"] = max(0, current_exp - acc["initial_exp"])
            acc["last_updated"] = time.strftime("%H:%M:%S")
            diff = current_exp - old_exp
            if diff > 0:
                self.log(f"Account {acc['nickname']} ({uid_str}) gained +{diff} EXP! Total Gained: +{acc['gained_exp']}", "success", uid_str)
            self.recalc_totals()

    def update_status(self, uid: str, status: str, active_matches: Optional[int] = None):
        uid_str = str(uid)
        if uid_str in self.accounts:
            # Nếu tài khoản đã hoàn thành level 21, ưu tiên giữ nguyên trạng thái hoàn thành
            if self.accounts[uid_str].get("level", 1) >= 21:
                self.accounts[uid_str]["status"] = "COMPLETED_LV21"
            else:
                self.accounts[uid_str]["status"] = status

            if active_matches is not None:
                self.accounts[uid_str]["active_matches"] = active_matches
            self.accounts[uid_str]["last_updated"] = time.strftime("%H:%M:%S")

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
            hash_password(password)  # giữ thời gian xử lý tương đương
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


# ==================== DASHBOARD API (lọc theo chủ sở hữu) ====================

async def handle_get_stats(request: web.Request) -> web.Response:
    user = request["user"]
    if user["role"] == "admin":
        visible = list(bot_state.accounts.values())
        logs = bot_state.logs[-60:]
        total_matches = bot_state.total_matches
        total_exp = bot_state.total_gained_exp
    else:
        mine = owned_uids(user["username"])
        visible = [a for uid, a in bot_state.accounts.items() if uid in mine]
        logs = [l for l in bot_state.logs if l.get("uid") in mine][-60:]
        total_matches = sum(a.get("matches_played", 0) for a in visible)
        total_exp = sum(a.get("gained_exp", 0) for a in visible)

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
        for ne in new_entries:
            if "uid" in ne:
                match = [a for a in merged if str(a.get("uid")) == ne["uid"]]
            else:
                match = [a for a in merged if a.get("token") == ne["token"]]
            if match:
                prev_owner = match[0].get("owner", "")
                if prev_owner != username and not is_admin:
                    return err(f"UID {ne.get('uid', '')} đã tồn tại trong hệ thống")
                ne["owner"] = prev_owner or username
                merged = [a for a in merged if a is not match[0]]
            else:
                ne["owner"] = username
            merged.append(ne)

        if not is_admin:
            slots = int(user.get("slots") or 0)
            used = sum(1 for a in merged if a.get("owner") == username)
            if used > slots:
                return err(f"Vượt giới hạn slot ({used}/{slots}). Liên hệ admin để tăng slot.")

        save_json(ACCOUNTS_FILE, merged)
        bot_state.log(f"Accounts updated/added by {username}.", "success")

        if "on_account_added" in bot_state.refresh_callbacks:
            asyncio.create_task(bot_state.refresh_callbacks["on_account_added"](data))

        return web.json_response({"status": "ok"})
    except Exception as e:
        return err(str(e))


def can_touch(user: Dict[str, Any], uid: str) -> bool:
    if user["role"] == "admin":
        return True
    return uid in owned_uids(user["username"])


def remove_uid_runtime(uid: str) -> None:
    bot_state.accounts.pop(uid, None)
    task = bot_state.account_workers.pop(uid, None)
    if task:
        task.cancel()
    bot_state.recalc_totals()


async def handle_delete_account(request: web.Request) -> web.Response:
    try:
        user = request["user"]
        data = await request.json()
        uid = str(data.get("uid")).strip()
        if not can_touch(user, uid):
            return err("Không có quyền với tài khoản này", 403)

        existing = [a for a in load_accounts() if str(a.get("uid")) != uid]
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
        if not can_touch(user, uid):
            return err("Không có quyền với tài khoản này", 403)
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
    states = [bot_state.accounts[x] for x in uids if x in bot_state.accounts]
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
            {"uid": x, "nickname": bot_state.accounts.get(x, {}).get("nickname", x),
             "level": bot_state.accounts.get(x, {}).get("level", 0),
             "kind": status_kind(bot_state.accounts[x]) if x in bot_state.accounts else "off"}
            for x in uids
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
        acc = bot_state.accounts.get(uid)
        if acc and acc.get("level", 1) < 21:
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
