import os
import re
import time
import json
import threading
import requests
from typing import Dict, List, Tuple, Optional, Set

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

CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "120"))
MONITOR_INTERVAL_SECONDS = int(os.getenv("MONITOR_INTERVAL_SECONDS", "600"))
SCAN_MAX_DEPTH = int(os.getenv("SCAN_MAX_DEPTH", "6"))
MAX_NOTIFS_PER_MODULE = int(os.getenv("MAX_NOTIFS_PER_MODULE", "8"))

STATE_FILE = "state.json"

FOLDER_MIME = "application/vnd.google-apps.folder"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"

# Notify only M19+
MIN_NOTIFY_MODULE = 19

# =========================
# STATE (persist)
# users: { "<user_id>": {"modules":[19,20], "dm_enabled": true} }
# last_seen: { "<module_folder_id>": [file_ids...] }
# modules: { "<module_folder_id>": {"mnum": 20, "name": "M20 ..."} }
# =========================
def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {"users": {}, "last_seen": {}, "modules": {}}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"users": {}, "last_seen": {}, "modules": {}}

def save_state(state: dict):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
    except Exception:
        pass

STATE = load_state()

# =========================
# CACHE (TTL)
# =========================
CACHE: Dict[str, Tuple[float, List[dict]]] = {}

def cache_get(folder_id: str) -> Optional[List[dict]]:
    entry = CACHE.get(folder_id)
    if not entry:
        return None
    ts, items = entry
    if time.time() - ts > CACHE_TTL_SECONDS:
        return None
    return items

def cache_set(folder_id: str, items: List[dict]):
    CACHE[folder_id] = (time.time(), items)

# =========================
# GOOGLE DRIVE HELPERS
# =========================
def drive_list(folder_id: str) -> List[dict]:
    cached = cache_get(folder_id)
    if cached is not None:
        return cached

    url = "https://www.googleapis.com/drive/v3/files"
    items: List[dict] = []
    page_token = None

    while True:
        params = {
            "key": GOOGLE_API_KEY,
            "q": f"'{folder_id}' in parents and trashed=false",
            "fields": "nextPageToken,files(id,name,mimeType,webViewLink,modifiedTime,shortcutDetails(targetId,targetMimeType))",
            "orderBy": "name",
            "pageSize": 1000,
        }
        if page_token:
            params["pageToken"] = page_token

        r = requests.get(url, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()

        items.extend(data.get("files", []))
        page_token = data.get("nextPageToken")
        if not page_token:
            break

    cache_set(folder_id, items)
    return items

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
# CHANNEL MEMBERSHIP GATE
# =========================
async def is_member(user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        member = await context.bot.get_chat_member(CHANNEL_ID, user_id)
        status = getattr(member, "status", "")
        return status in ("member", "administrator", "creator")
    except Exception:
        return False

def channel_link() -> str:
    # If CHANNEL_ID is @username, make https://t.me/username
    if isinstance(CHANNEL_ID, str) and CHANNEL_ID.startswith("@"):
        return f"https://t.me/{CHANNEL_ID[1:]}"
    # otherwise just show CHANNEL_ID
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
    # If it's a message command
    if update.message:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))
    else:
        await update.callback_query.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))
    return False

# =========================
# MAIN MENU
# =========================
async def show_main_menu(message, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("🔔 الحصول على إشعارات الكورسات المضافة", callback_data="MENU:NOTIFY")],
        [InlineKeyboardButton("📂 See Drives", callback_data="MENU:DRIVE")],
    ]
    await message.reply_text("اختر خياراً:", reply_markup=InlineKeyboardMarkup(kb))

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await require_membership(update, context):
        return
    await show_main_menu(update.message, context)

# =========================
# DRIVE BROWSING
# =========================
async def open_drive_root(message, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["stack"] = [(ROOT_FOLDER_ID, "Home")]
    context.user_data["offset"] = 0
    context.user_data["folderid_map"] = {}
    context.user_data["name_map"] = {}
    await send_folder(message, context)

async def send_folder(message, context: ContextTypes.DEFAULT_TYPE):
    folder_id, path = context.user_data["stack"][-1]
    offset = context.user_data.get("offset", 0)

    items = drive_list(folder_id)
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

    await message.reply_text(f"📂 {path}", reply_markup=InlineKeyboardMarkup(kb))

# =========================
# NOTIFICATION PREFERENCES (DM subscriptions)
# =========================
def list_root_modules() -> List[Tuple[int, str, str]]:
    items = drive_list(ROOT_FOLDER_ID)
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

async def show_notify_menu(message, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    # Only M19+
    modules = [(mnum, mid, mname) for (mnum, mid, mname) in list_root_modules() if mnum >= MIN_NOTIFY_MODULE]

    u = STATE.setdefault("users", {}).setdefault(str(user_id), {"modules": [], "dm_enabled": True})
    selected = set(u.get("modules", []))

    kb = []
    # Toggle DM on/off
    dm_enabled = bool(u.get("dm_enabled", True))
    kb.append([
        InlineKeyboardButton(
            f"{'✅' if dm_enabled else '❌'} إشعارات خاصة (DM)",
            callback_data="NOTIFY:DMTOGGLE"
        )
    ])

    for (mnum, mid, mname) in modules[:30]:
        is_on = str(mnum) in selected
        btn_text = f"{'✅' if is_on else '➕'} {mname}"
        kb.append([InlineKeyboardButton(btn_text, callback_data=f"NOTIFY:TOGGLE:{mnum}")])

    kb.append([InlineKeyboardButton("⬅️ Back to menu", callback_data="MENU:BACK")])

    text = (
        "🔔 اختر الموديولات التي تريد إشعاراتها.\n"
        "ملاحظة: القناة تستقبل كل التحديثات للجميع،\n"
        "لكن هذه القائمة تتحكم في إشعاراتك الخاصة (DM) حتى لو كانت القناة صامتة.\n"
    )
    await message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))

# =========================
# MONITORING (channel + DM)
# =========================
def scan_folder_recursive(folder_id: str, depth: int, max_depth: int) -> List[dict]:
    if depth > max_depth:
        return []
    items = drive_list(folder_id)
    results = []
    for it in items:
        results.append(it)
        if is_folder_or_folder_shortcut(it):
            real_child_id, _ = resolve_folder_id_and_name(it)
            results.extend(scan_folder_recursive(real_child_id, depth + 1, max_depth))
    return results

def users_for_module(mnum: int) -> List[int]:
    """Return user IDs who subscribed to this module number (as string)."""
    out = []
    for uid, entry in STATE.get("users", {}).items():
        if not entry.get("dm_enabled", True):
            continue
        mods = set(entry.get("modules", []))
        if str(mnum) in mods:
            try:
                out.append(int(uid))
            except:
                pass
    return out

def monitor_loop(app: Application):
    # Build module map once
    try:
        for mnum, mid, mname in list_root_modules():
            STATE.setdefault("modules", {})[str(mid)] = {"mnum": mnum, "name": mname}
        save_state(STATE)
    except Exception:
        pass

    while True:
        try:
            modules = list_root_modules()

            for mnum, module_id, module_name in modules:
                if mnum < MIN_NOTIFY_MODULE:
                    continue  # skip M17/M18

                module_key = str(module_id)
                prev_seen = set(STATE.get("last_seen", {}).get(module_key, []))

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
                    # sort newest first by modifiedTime
                    def mt(x): return x.get("modifiedTime") or ""
                    new_items.sort(key=mt, reverse=True)

                    header = circles_header(mnum)
                    sent = 0

                    for it in new_items:
                        if sent >= MAX_NOTIFS_PER_MODULE:
                            break

                        name = it.get("name", "item")
                        link = file_link(it)
                        emoji = "📁" if is_folder_or_folder_shortcut(it) else icon_for_mime(it.get("mimeType", ""))

                        msg = f"{header}\n\n{emoji} {name}\n🔗 {link}"

                        # 1) Send to channel (always)
                        try:
                            app.bot.send_message(chat_id=CHANNEL_ID, text=msg)
                        except Exception:
                            pass

                        # 2) Send DM to users who subscribed to this module (so they won’t miss it if channel is muted)
                        for uid in users_for_module(mnum):
                            try:
                                app.bot.send_message(chat_id=uid, text=msg)
                            except Exception:
                                # user may not have started bot or blocked it
                                pass

                        sent += 1

                STATE.setdefault("last_seen", {})[module_key] = list(current_ids)
                save_state(STATE)

        except Exception:
            pass

        time.sleep(MONITOR_INTERVAL_SECONDS)

# =========================
# CALLBACKS
# =========================
async def on_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    uid = update.effective_user.id

    # Check subscription confirmation
    if data == "CHECK_SUB":
        if await is_member(uid, context):
            await q.message.reply_text("✅ تم التحقق من الاشتراك. أهلاً بك!")
            await show_main_menu(q.message, context)
        else:
            await q.message.reply_text("❌ لم يتم العثور على اشتراكك بعد. اشترك ثم جرّب مرة أخرى.")
        return

    # Enforce channel membership for everything else
    if not await require_membership(update, context):
        return

    # Main menu
    if data == "MENU:BACK":
        await show_main_menu(q.message, context)
        return

    if data == "MENU:DRIVE":
        await open_drive_root(q.message, context)
        return

    if data == "MENU:NOTIFY":
        await show_notify_menu(q.message, context, uid)
        return

    # Notify toggles
    if data == "NOTIFY:DMTOGGLE":
        user_entry = STATE.setdefault("users", {}).setdefault(str(uid), {"modules": [], "dm_enabled": True})
        user_entry["dm_enabled"] = not bool(user_entry.get("dm_enabled", True))
        save_state(STATE)
        await show_notify_menu(q.message, context, uid)
        return

    if data.startswith("NOTIFY:TOGGLE:"):
        mnum = int(data.split(":")[-1])
        user_entry = STATE.setdefault("users", {}).setdefault(str(uid), {"modules": [], "dm_enabled": True})
        mods = set(user_entry.get("modules", []))
        if str(mnum) in mods:
            mods.remove(str(mnum))
        else:
            mods.add(str(mnum))
        user_entry["modules"] = sorted(list(mods))
        save_state(STATE)
        await show_notify_menu(q.message, context, uid)
        return

    # Drive navigation
    if data == "DRIVE_HOME":
        await open_drive_root(q.message, context)
        return

    if data == "BACK":
        if len(context.user_data.get("stack", [])) > 1:
            context.user_data["stack"].pop()
        context.user_data["offset"] = 0
        await send_folder(q.message, context)
        return

    if data == "PREV":
        context.user_data["offset"] = max(0, context.user_data.get("offset", 0) - PAGE_SIZE)
        await send_folder(q.message, context)
        return

    if data == "NEXT":
        context.user_data["offset"] = context.user_data.get("offset", 0) + PAGE_SIZE
        await send_folder(q.message, context)
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
        await send_folder(q.message, context)
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

    # Start monitoring thread
    t = threading.Thread(target=monitor_loop, args=(app,), daemon=True)
    t.start()

    app.run_polling()

if __name__ == "__main__":
    main()
