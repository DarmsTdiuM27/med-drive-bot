# ===== Replit Health Check Ping (SAFE & SILENT) =====
import os
from threading import Thread
from http.server import HTTPServer, BaseHTTPRequestHandler

HEALTH_PATH = os.environ.get("HEALTH_PATH", "/health")

class ReplitPing(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def do_GET(self):
        if self.path != HEALTH_PATH:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

    def do_POST(self):
        self.send_response(405)
        self.end_headers()

def run_ping_server():
    port = int(os.environ.get("PORT", 8080))
    HTTPServer(("0.0.0.0", port), ReplitPing).serve_forever()

Thread(target=run_ping_server, daemon=True).start()
# ===================================================

import re
import time
import json
import threading
import requests
from typing import Dict, List, Tuple, Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# =========================
# ENV / CONFIG
# =========================
BOT_TOKEN = os.getenv("BOT_TOKEN")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

CHANNEL_ID = os.getenv("CHANNEL_ID", "@MedDriveUpdates")

ROOT_FOLDER_ID = "1EnPIlIcMf_XNI2Zu_xYoVZJnMKQiwThs"
PAGE_SIZE = 25

# FAST knobs (set in Secrets)
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "900"))          # 15 min cache
MONITOR_INTERVAL_SECONDS = int(os.getenv("MONITOR_INTERVAL_SECONDS", "900"))  # 15 min check
SCAN_MAX_DEPTH = int(os.getenv("SCAN_MAX_DEPTH", "4"))                 # reduce background load
MAX_NOTIFS_PER_MODULE = int(os.getenv("MAX_NOTIFS_PER_MODULE", "6"))

STATE_FILE = "state.json"

FOLDER_MIME = "application/vnd.google-apps.folder"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"

# Notifications only for M19+
MIN_NOTIFY_MODULE = 19

# =========================
# HTTP Session (FAST)
# =========================
SESSION = requests.Session()
SESSION.headers.update({"Accept-Encoding": "gzip"})

# =========================
# STATE
# users: { "<uid>": {"module": "20", "module_folder_id": "<id>", "dm_enabled": true} }
# last_seen: { "<module_folder_id>": [file_ids...] }
# =========================
def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {"users": {}, "last_seen": {}}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if "users" not in data: data["users"] = {}
            if "last_seen" not in data: data["last_seen"] = {}
            return data
    except Exception:
        return {"users": {}, "last_seen": {}}

def save_state(state: dict):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
    except Exception:
        pass

STATE = load_state()

# =========================
# SUPER FAST CACHE (SWR)
# folder_id -> served instantly, refreshed in background when stale
# key=(folder_id, mode)
# =========================
CACHE: Dict[Tuple[str, str], Dict] = {}  # (folder_id, mode) -> {"ts":..., "items":..., "refreshing":...}

def _cache_key(folder_id: str, mode: str) -> Tuple[str, str]:
    return (folder_id, mode)

def cache_get(folder_id: str, mode: str) -> Optional[List[dict]]:
    entry = CACHE.get(_cache_key(folder_id, mode))
    return entry.get("items") if entry else None

def cache_age(folder_id: str, mode: str) -> Optional[float]:
    entry = CACHE.get(_cache_key(folder_id, mode))
    if not entry:
        return None
    return time.time() - entry.get("ts", 0)

def cache_set(folder_id: str, mode: str, items: List[dict]):
    CACHE[_cache_key(folder_id, mode)] = {"ts": time.time(), "items": items, "refreshing": False}

def cache_is_refreshing(folder_id: str, mode: str) -> bool:
    entry = CACHE.get(_cache_key(folder_id, mode))
    return bool(entry and entry.get("refreshing"))

def cache_mark_refreshing(folder_id: str, mode: str, value: bool):
    entry = CACHE.get(_cache_key(folder_id, mode))
    if entry:
        entry["refreshing"] = value

# =========================
# GOOGLE DRIVE API (FAST)
# =========================
def drive_list(folder_id: str, mode: str) -> List[dict]:
    """
    mode="browse": light fields for UI (fast)
    mode="monitor": include modifiedTime (for updates)
    """
    items = cache_get(folder_id, mode)
    age = cache_age(folder_id, mode)

    if items is not None:
        # SWR: serve now, refresh later if stale
        if age is not None and age > CACHE_TTL_SECONDS and not cache_is_refreshing(folder_id, mode):
            Thread(target=_drive_refresh_background, args=(folder_id, mode), daemon=True).start()
        return items

    # no cache
    items = _drive_fetch(folder_id, mode)
    cache_set(folder_id, mode, items)
    return items

def _drive_refresh_background(folder_id: str, mode: str):
    try:
        cache_mark_refreshing(folder_id, mode, True)
        items = _drive_fetch(folder_id, mode)
        cache_set(folder_id, mode, items)
    finally:
        cache_mark_refreshing(folder_id, mode, False)

def _drive_fetch(folder_id: str, mode: str) -> List[dict]:
    url = "https://www.googleapis.com/drive/v3/files"
    out: List[dict] = []
    page_token = None

    if mode == "browse":
        fields = "nextPageToken,files(id,name,mimeType,webViewLink,shortcutDetails(targetId,targetMimeType))"
    else:
        fields = "nextPageToken,files(id,name,mimeType,webViewLink,modifiedTime,shortcutDetails(targetId,targetMimeType))"

    while True:
        params = {
            "key": GOOGLE_API_KEY,
            "q": f"'{folder_id}' in parents and trashed=false",
            "fields": fields,
            "orderBy": "name",
            "pageSize": 1000,
        }
        if page_token:
            params["pageToken"] = page_token

        r = SESSION.get(url, params=params, timeout=25)
        r.raise_for_status()
        data = r.json()

        out.extend(data.get("files", []))
        page_token = data.get("nextPageToken")
        if not page_token:
            break

    return out

def is_shortcut(it: dict) -> bool:
    return it.get("mimeType") == SHORTCUT_MIME and it.get("shortcutDetails")

def is_folder_or_folder_shortcut(it: dict) -> bool:
    if it.get("mimeType") == FOLDER_MIME:
        return True
    if is_shortcut(it) and it["shortcutDetails"].get("targetMimeType") == FOLDER_MIME:
        return True
    return False

def resolve_folder_id_and_name(it: dict) -> Tuple[str, str]:
    if it.get("mimeType") == FOLDER_MIME:
        return it["id"], it.get("name", "Folder")
    return it["shortcutDetails"]["targetId"], it.get("name", "Folder")

def file_link(it: dict) -> str:
    return it.get("webViewLink") or f"https://drive.google.com/file/d/{it['id']}/view"

def module_sort_key(name: str):
    m = re.match(r"^\s*M(\d+)\b", name, re.IGNORECASE)
    if m:
        return (0, int(m.group(1)), name.lower())
    return (1, 10**9, name.lower())

def parse_module_number(name: str) -> Optional[int]:
    m = re.match(r"^\s*M(\d+)\b", name, re.IGNORECASE)
    if not m:
        return None
    try:
        return int(m.group(1))
    except:
        return None

# =========================
# EMOJIS (circles only; M20 purple)
# =========================
def module_circle(m: int) -> str:
    mapping = {
        19: "🔴",
        20: "🟣",  # M20 purple
        21: "🟢",
        22: "🟡",
        23: "🔵",
        24: "🟠",
        25: "⚫",
    }
    return mapping.get(m, "⚪")

def circles_header(m: int) -> str:
    c = module_circle(m)
    return f"{c}{c}{c}  M{m}  {c}{c}{c}"

def icon_for_mime(mime: str) -> str:
    if mime == "application/pdf":
        return "📕"
    if mime in ("application/msword", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"):
        return "📝"
    if mime in ("application/vnd.ms-powerpoint", "application/vnd.openxmlformats-officedocument.presentationml.presentation"):
        return "📊"
    if mime == "application/vnd.google-apps.document":
        return "📝"
    if mime == "application/vnd.google-apps.presentation":
        return "📊"
    if mime == FOLDER_MIME:
        return "📁"
    return "📄"

# =========================
# CHANNEL MEMBERSHIP FLOW
# =========================
async def is_member(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        member = await context.bot.get_chat_member(CHANNEL_ID, user_id)
        status = getattr(member, "status", "")
        return status in ("member", "administrator", "creator")
    except Exception:
        return False

def channel_link() -> str:
    if isinstance(CHANNEL_ID, str) and CHANNEL_ID.startswith("@"):
        return f"https://t.me/{CHANNEL_ID[1:]}"
    return str(CHANNEL_ID)

async def require_membership(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    uid = update.effective_user.id
    if await is_member(uid, context):
        return True

    kb = [
        [InlineKeyboardButton("📢 Subscribe to channel", url=channel_link())],
        [InlineKeyboardButton("✅ I subscribed", callback_data="CHECK_SUB")],
    ]
    text = (
        "🔒 يجب الاشتراك في القناة أولاً لاستعمال البوت.\n\n"
        f"📢 القناة: {channel_link()}\n\n"
        "بعد الاشتراك اضغط: ✅ I subscribed"
    )

    if update.message:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.callback_query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))
    return False

# =========================
# UI: MAIN MENU
# =========================
async def show_main_menu(msg):
    kb = [
        [InlineKeyboardButton("🔔 الحصول على إشعارات الكورسات المضافة", callback_data="MENU:NOTIFY")],
        [InlineKeyboardButton("📂 See Drives", callback_data="MENU:DRIVE")],
    ]
    await msg.reply_text("اختر خياراً:", reply_markup=InlineKeyboardMarkup(kb))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_membership(update, context):
        return
    await show_main_menu(update.message)

# =========================
# DRIVE BROWSING (FAST + EDIT)
# =========================
async def open_drive_root(message, context: ContextTypes.DEFAULT_TYPE, edit: bool = False):
    context.user_data["stack"] = [(ROOT_FOLDER_ID, "Home")]
    context.user_data["offset"] = 0
    context.user_data["folderid_map"] = {}
    context.user_data["name_map"] = {}
    await show_folder(message, context, edit=edit)

async def open_specific_module(folder_id: str, title: str, message, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["stack"] = [(folder_id, title)]
    context.user_data["offset"] = 0
    context.user_data["folderid_map"] = {}
    context.user_data["name_map"] = {}
    await show_folder(message, context, edit=True)

async def show_folder(msg, context: ContextTypes.DEFAULT_TYPE, edit: bool):
    folder_id, path = context.user_data["stack"][-1]
    offset = context.user_data.get("offset", 0)

    items = drive_list(folder_id, mode="browse")
    folders = [x for x in items if is_folder_or_folder_shortcut(x)]
    files = [x for x in items if not is_folder_or_folder_shortcut(x)]

    if folder_id == ROOT_FOLDER_ID:
        folders.sort(key=lambda x: module_sort_key(x.get("name", "")))
    else:
        folders.sort(key=lambda x: x.get("name", "").lower())
    files.sort(key=lambda x: x.get("name", "").lower())

    merged = folders + files
    page = merged[offset: offset + PAGE_SIZE]

    folderid_map = {}
    name_map = {}
    for it in folders:
        real_id, display_name = resolve_folder_id_and_name(it)
        folderid_map[it["id"]] = real_id
        name_map[it["id"]] = display_name

    context.user_data["folderid_map"] = folderid_map
    context.user_data["name_map"] = name_map

    kb = []
    if len(context.user_data["stack"]) > 1:
        kb.append([
            InlineKeyboardButton("⬅️ Back", callback_data="BACK"),
            InlineKeyboardButton("🏠 Home", callback_data="DRIVE_HOME"),
        ])
    else:
        kb.append([InlineKeyboardButton("🏠 Home", callback_data="DRIVE_HOME")])

    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("◀️ Prev", callback_data="PREV"))
    if offset + PAGE_SIZE < len(merged):
        nav.append(InlineKeyboardButton("Next ▶️", callback_data="NEXT"))
    if nav:
        kb.append(nav)

    for it in page:
        if is_folder_or_folder_shortcut(it):
            kb.append([InlineKeyboardButton(f"📁 {it.get('name','Folder')}", callback_data=f"OPEN:{it['id']}")])
        else:
            ic = icon_for_mime(it.get("mimeType", ""))
            kb.append([InlineKeyboardButton(f"{ic} {it.get('name','file')}", url=file_link(it))])

    kb.append([InlineKeyboardButton("⬅️ Back to menu", callback_data="MENU:BACK")])

    text = f"📂 {path}"
    markup = InlineKeyboardMarkup(kb)

    if edit and hasattr(msg, "edit_text"):
        try:
            await msg.edit_text(text, reply_markup=markup)
            return
        except Exception:
            pass

    await msg.reply_text(text, reply_markup=markup)

# =========================
# NOTIFICATIONS: ONE M ONLY + after-choose buttons
# =========================
def list_root_modules() -> List[Tuple[int, str, str]]:
    items = drive_list(ROOT_FOLDER_ID, mode="browse")
    mods = []
    for it in items:
        if not is_folder_or_folder_shortcut(it):
            continue
        real_id, display_name = resolve_folder_id_and_name(it)
        mnum = parse_module_number(display_name)
        if mnum is None:
            continue
        mods.append((mnum, real_id, display_name))
    mods.sort(key=lambda x: x[0])
    return mods

async def show_notify_menu(msg, user_id: int, edit: bool = False):
    modules = [(mnum, mid, mname) for (mnum, mid, mname) in list_root_modules() if mnum >= MIN_NOTIFY_MODULE]

    u = STATE.setdefault("users", {}).setdefault(str(user_id), {"module": None, "module_folder_id": None, "dm_enabled": True})
    chosen = u.get("module")  # single
    dm_enabled = bool(u.get("dm_enabled", True))

    kb = []
    kb.append([InlineKeyboardButton(f"{'✅' if dm_enabled else '❌'} إشعارات خاصة (DM)", callback_data="NOTIFY:DMTOGGLE")])

    # radio style
    for (mnum, mid, mname) in modules[:30]:
        is_on = (chosen == str(mnum))
        prefix = "✅" if is_on else "⚪"
        kb.append([InlineKeyboardButton(f"{prefix} {mname}", callback_data=f"NOTIFY:CHOOSE:{mnum}:{mid}")])

    kb.append([InlineKeyboardButton("⬅️ Back to menu", callback_data="MENU:BACK")])

    text = (
        "🔔 اختر موديول واحد فقط لاستقبال الإشعارات.\n"
        "📢 القناة تنشر كل التحديثات للجميع.\n"
        "✅ DM يرسل لك فقط الموديول الذي اخترته (حتى لو القناة Silent).\n"
    )
    markup = InlineKeyboardMarkup(kb)

    if edit and hasattr(msg, "edit_text"):
        try:
            await msg.edit_text(text, reply_markup=markup)
            return
        except Exception:
            pass
    await msg.reply_text(text, reply_markup=markup)

# =========================
# MONITOR (channel + DM for chosen M only)
# =========================
def scan_folder_recursive(folder_id: str, depth: int, max_depth: int) -> List[dict]:
    if depth > max_depth:
        return []
    items = drive_list(folder_id, mode="monitor")
    results = []
    for it in items:
        results.append(it)
        if is_folder_or_folder_shortcut(it):
            real_child_id, _ = resolve_folder_id_and_name(it)
            results.extend(scan_folder_recursive(real_child_id, depth + 1, max_depth))
    return results

def users_for_module(mnum: int) -> List[int]:
    out = []
    for uid, entry in STATE.get("users", {}).items():
        if not entry.get("dm_enabled", True):
            continue
        chosen = entry.get("module")  # SINGLE
        if chosen == str(mnum):
            try:
                out.append(int(uid))
            except:
                pass
    return out

def monitor_loop(app: Application):
    while True:
        try:
            modules = list_root_modules()
            for mnum, module_id, module_name in modules:
                if mnum < MIN_NOTIFY_MODULE:
                    continue

                key = str(module_id)
                prev_seen = set(STATE.get("last_seen", {}).get(key, []))

                items = scan_folder_recursive(module_id, 0, SCAN_MAX_DEPTH)

                current_ids = set()
                new_items = []
                for it in items:
                    fid = it.get("id")
                    if not fid:
                        continue
                    current_ids.add(fid)
                    if fid not in prev_seen:
                        new_items.append(it)

                if new_items:
                    new_items.sort(key=lambda x: x.get("modifiedTime") or "", reverse=True)
                    header = circles_header(mnum)
                    sent = 0

                    for it in new_items:
                        if sent >= MAX_NOTIFS_PER_MODULE:
                            break

                        name = it.get("name", "item")
                        link = file_link(it)
                        emoji = "📁" if is_folder_or_folder_shortcut(it) else icon_for_mime(it.get("mimeType", ""))

                        msg = f"{header}\n\n{emoji} {name}\n🔗 {link}"

                        # channel (always)
                        try:
                            app.bot.send_message(chat_id=CHANNEL_ID, text=msg)
                        except Exception:
                            pass

                        # DM only to users who selected this single M
                        for uid in users_for_module(mnum):
                            try:
                                app.bot.send_message(chat_id=uid, text=msg)
                            except Exception:
                                pass

                        sent += 1

                STATE.setdefault("last_seen", {})[key] = list(current_ids)
                save_state(STATE)

        except Exception:
            pass

        time.sleep(MONITOR_INTERVAL_SECONDS)

# =========================
# CALLBACK HANDLER
# =========================
async def on_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    uid = update.effective_user.id

    if data == "CHECK_SUB":
        if await is_member(uid, context):
            await q.message.reply_text("✅ تم التحقق من الاشتراك. أهلاً بك!")
            await show_main_menu(q.message)
        else:
            await q.message.reply_text("❌ لم يتم العثور على اشتراكك بعد. اشترك ثم جرّب مرة أخرى.")
        return

    if not await require_membership(update, context):
        return

    # Main menu
    if data == "MENU:BACK":
        await show_main_menu(q.message)
        return

    if data == "MENU:DRIVE":
        await open_drive_root(q.message, context, edit=False)
        return

    if data == "MENU:NOTIFY":
        await show_notify_menu(q.message, uid, edit=False)
        return

    # DM toggle
    if data == "NOTIFY:DMTOGGLE":
        user_entry = STATE.setdefault("users", {}).setdefault(str(uid), {"module": None, "module_folder_id": None, "dm_enabled": True})
        user_entry["dm_enabled"] = not bool(user_entry.get("dm_enabled", True))
        save_state(STATE)
        await show_notify_menu(q.message, uid, edit=True)
        return

    # Choose ONE module
    if data.startswith("NOTIFY:CHOOSE:"):
        # NOTIFY:CHOOSE:<mnum>:<folderid>
        _, _, mnum, folder_id = data.split(":", 3)
        user_entry = STATE.setdefault("users", {}).setdefault(str(uid), {"module": None, "module_folder_id": None, "dm_enabled": True})

        user_entry["module"] = str(mnum)               # single choice
        user_entry["module_folder_id"] = folder_id
        save_state(STATE)

        kb = [
            [InlineKeyboardButton(f"📂 See M{mnum} drive", callback_data=f"SEE_M:{mnum}:{folder_id}")],
            [InlineKeyboardButton("📁 All drives", callback_data="MENU:DRIVE")],
            [InlineKeyboardButton("⬅️ Back to menu", callback_data="MENU:BACK")],
        ]
        await q.message.reply_text(
            f"✅ لقد اخترت استقبال الإشعارات من M{mnum} فقط.",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        return

    # open module drive after choosing notifications
    if data.startswith("SEE_M:"):
        # SEE_M:<mnum>:<folderid>
        _, mnum, folder_id = data.split(":", 2)
        await open_specific_module(folder_id, f"M{mnum}", q.message, context)
        return

    # Drive navigation
    if data == "DRIVE_HOME":
        await open_drive_root(q.message, context, edit=True)
        return

    if data == "BACK":
        if len(context.user_data.get("stack", [])) > 1:
            context.user_data["stack"].pop()
        context.user_data["offset"] = 0
        await show_folder(q.message, context, edit=True)
        return

    if data == "PREV":
        context.user_data["offset"] = max(0, context.user_data.get("offset", 0) - PAGE_SIZE)
        await show_folder(q.message, context, edit=True)
        return

    if data == "NEXT":
        context.user_data["offset"] = context.user_data.get("offset", 0) + PAGE_SIZE
        await show_folder(q.message, context, edit=True)
        return

    if data.startswith("OPEN:"):
        clicked_id = data.split(":", 1)[1]
        folderid_map = context.user_data.get("folderid_map", {})
        name_map = context.user_data.get("name_map", {})

        real_folder_id = folderid_map.get(clicked_id, clicked_id)
        folder_name = name_map.get(clicked_id, "Folder")

        current_path = context.user_data.get("stack", [(ROOT_FOLDER_ID, "Home")])[-1][1]
        new_path = f"{current_path} › {folder_name}"

        context.user_data.setdefault("stack", [(ROOT_FOLDER_ID, "Home")]).append((real_folder_id, new_path))
        context.user_data["offset"] = 0
        await show_folder(q.message, context, edit=True)
        return

# =========================
# MAIN
# =========================
def main():
    if not BOT_TOKEN or not GOOGLE_API_KEY:
        raise RuntimeError("Missing BOT_TOKEN or GOOGLE_API_KEY in Secrets")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_click))

    t = threading.Thread(target=monitor_loop, args=(app,), daemon=True)
    t.start()

    app.run_polling()

if __name__ == "__main__":
    main()
