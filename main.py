import os
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

# Your channel (public username). You can override via Secrets if you want.
CHANNEL_ID = os.getenv("CHANNEL_ID", "@MedDriveUpdates")

ROOT_FOLDER_ID = "1EnPIlIcMf_XNI2Zu_xYoVZJnMKQiwThs"
PAGE_SIZE = 25

# TTL caching (speed)
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "120"))  # 2 minutes default

# Monitoring interval (seconds)
MONITOR_INTERVAL_SECONDS = int(os.getenv("MONITOR_INTERVAL_SECONDS", "600"))  # 10 minutes default

# How deep to scan inside each module for notifications
SCAN_MAX_DEPTH = int(os.getenv("SCAN_MAX_DEPTH", "6"))

# Max notifications per module per cycle (anti-spam)
MAX_NOTIFS_PER_MODULE = int(os.getenv("MAX_NOTIFS_PER_MODULE", "8"))

STATE_FILE = "state.json"

FOLDER_MIME = "application/vnd.google-apps.folder"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"

# Notify only M19+
MIN_NOTIFY_MODULE = 19

# =========================
# STATE (persist)
# =========================
def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {"last_seen": {}, "modules": {}}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"last_seen": {}, "modules": {}}

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
# folder_id -> (timestamp, items)
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
# GOOGLE DRIVE API HELPERS
# =========================
def drive_list(folder_id: str) -> List[dict]:
    """
    List items in a folder, with shortcutDetails so we can resolve folder shortcuts.
    Uses TTL cache for speed.
    """
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

def drive_get_meta(file_id: str) -> Optional[dict]:
    url = f"https://www.googleapis.com/drive/v3/files/{file_id}"
    params = {"fields": "id,name,mimeType,webViewLink,modifiedTime", "key": GOOGLE_API_KEY}
    r = requests.get(url, params=params, timeout=20)
    if r.status_code != 200:
        return None
    return r.json()

def is_shortcut(it: dict) -> bool:
    return it.get("mimeType") == SHORTCUT_MIME and it.get("shortcutDetails")

def is_folder_or_folder_shortcut(it: dict) -> bool:
    if it.get("mimeType") == FOLDER_MIME:
        return True
    if is_shortcut(it) and it["shortcutDetails"].get("targetMimeType") == FOLDER_MIME:
        return True
    return False

def resolve_folder_id_and_name(it: dict) -> Tuple[str, str]:
    """
    Real folder -> (id, name)
    Shortcut to folder -> (targetId, displayed name)
    """
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
# EMOJIS (CIRCLES ONLY)
# M20 must be purple
# M17/M18 removed from notifications
# =========================
def module_circle(m: int) -> str:
    # Only circles requested
    mapping = {
        19: "🔴",  # M19
        20: "🟣",  # M20 purple
        21: "🟢",  # M21
        22: "🟡",  # M22
        23: "🔵",  # M23
        24: "🟠",  # M24
        25: "⚫",  # M25
    }
    return mapping.get(m, "⚪")  # default white circle for others

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
# CHANNEL MEMBERSHIP REQUIREMENT
# =========================
async def must_be_member(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Requires user to be subscribed to CHANNEL_ID.
    Bot should be admin in channel for reliable check.
    """
    user = update.effective_user
    chat_id = update.effective_chat.id

    try:
        member = await context.bot.get_chat_member(CHANNEL_ID, user.id)
        status = getattr(member, "status", "")
        if status in ("member", "administrator", "creator"):
            return True
    except Exception:
        # If check fails, we enforce requirement (block) and show instructions.
        pass

    msg = (
        "🔒 لا يمكنك استعمال البوت قبل الاشتراك في القناة.\n\n"
        f"✅ اشترك هنا: {CHANNEL_ID}\n"
        "ثم ارجع وأرسل /start"
    )
    await context.bot.send_message(chat_id=chat_id, text=msg)
    return False

# =========================
# TELEGRAM BROWSING (inside bot)
# =========================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await must_be_member(update, context):
        return

    context.user_data["stack"] = [(ROOT_FOLDER_ID, "Home")]
    context.user_data["offset"] = 0
    context.user_data["folderid_map"] = {}  # displayed item id -> real folder id (shortcut support)
    context.user_data["name_map"] = {}      # displayed item id -> displayed name
    await send_folder(update.message, context)

async def send_folder(message, context: ContextTypes.DEFAULT_TYPE):
    folder_id, path = context.user_data["stack"][-1]
    offset = context.user_data.get("offset", 0)

    items = drive_list(folder_id)
    folders = [x for x in items if is_folder_or_folder_shortcut(x)]
    files = [x for x in items if not is_folder_or_folder_shortcut(x)]

    # Root modules sorted numerically
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

    # Navigation
    if len(context.user_data["stack"]) > 1:
        kb.append([
            InlineKeyboardButton("⬅️ Back", callback_data="BACK"),
            InlineKeyboardButton("🏠 Home", callback_data="HOME")
        ])
    else:
        kb.append([InlineKeyboardButton("🏠 Home", callback_data="HOME")])

    # Pagination
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("◀️ Prev", callback_data="PREV"))
    if offset + PAGE_SIZE < len(merged):
        nav.append(InlineKeyboardButton("Next ▶️", callback_data="NEXT"))
    if nav:
        kb.append(nav)

    # Items
    for it in page:
        if is_folder_or_folder_shortcut(it):
            kb.append([InlineKeyboardButton(f"📁 {it.get('name','Folder')}", callback_data=f"OPEN:{it['id']}")])
        else:
            ic = icon_for_mime(it.get("mimeType", ""))
            kb.append([InlineKeyboardButton(f"{ic} {it.get('name','file')}", url=file_link(it))])

    await message.reply_text(f"📂 {path}", reply_markup=InlineKeyboardMarkup(kb))

async def on_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await must_be_member(update, context):
        return

    q = update.callback_query
    await q.answer()
    data = q.data

    if data == "HOME":
        context.user_data["stack"] = [(ROOT_FOLDER_ID, "Home")]
        context.user_data["offset"] = 0
        await send_folder(q.message, context)
        return

    if data == "BACK":
        if len(context.user_data["stack"]) > 1:
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

        current_path = context.user_data["stack"][-1][1]
        new_path = f"{current_path} › {folder_name}"

        context.user_data["stack"].append((real_folder_id, new_path))
        context.user_data["offset"] = 0
        await send_folder(q.message, context)
        return

# =========================
# NOTIFICATIONS (periodic monitoring)
# - single channel for all
# - exclude M17/M18
# - colored circles, M20 purple
# =========================
def list_root_modules() -> List[Tuple[int, str, str]]:
    """
    Returns list of (module_number, module_folder_id, module_display_name) from root.
    Resolves shortcuts to real folder IDs.
    """
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

def scan_folder_recursive(folder_id: str, depth: int, max_depth: int) -> List[dict]:
    """
    Recursive scan to find all items under a module (folders + files).
    Uses drive_list which is cached.
    """
    if depth > max_depth:
        return []

    items = drive_list(folder_id)
    results = []

    for it in items:
        results.append(it)
        # Recurse into folders (including folder shortcuts)
        if is_folder_or_folder_shortcut(it):
            real_child_id, _ = resolve_folder_id_and_name(it)
            results.extend(scan_folder_recursive(real_child_id, depth + 1, max_depth))

    return results

def monitor_loop(app: Application):
    """
    Periodically scans M19+ modules and posts NEW items to CHANNEL_ID.
    Keeps last_seen per module in state.json (set of file IDs).
    """
    # Build module map (name) once
    try:
        for mnum, mid, mname in list_root_modules():
            STATE["modules"][str(mid)] = {"mnum": mnum, "name": mname}
        save_state(STATE)
    except Exception:
        pass

    while True:
        try:
            modules = list_root_modules()

            for mnum, module_id, module_name in modules:
                # Skip M17 & M18 and anything below M19
                if mnum < MIN_NOTIFY_MODULE:
                    continue

                module_key = str(module_id)
                prev_seen = set(STATE.get("last_seen", {}).get(module_key, []))

                # Recursive scan under module
                items = scan_folder_recursive(module_id, depth=0, max_depth=SCAN_MAX_DEPTH)

                # New items by ID
                current_ids = set()
                new_items = []

                for it in items:
                    fid = it.get("id")
                    if not fid:
                        continue
                    current_ids.add(fid)
                    if fid not in prev_seen:
                        new_items.append(it)

                # Post notifications (limit per cycle)
                if new_items:
                    # Sort by modifiedTime if available
                    def mt(it):
                        return it.get("modifiedTime") or ""
                    new_items.sort(key=mt, reverse=True)

                    header = circles_header(mnum)

                    count_sent = 0
                    for it in new_items:
                        if count_sent >= MAX_NOTIFS_PER_MODULE:
                            break

                        mime = it.get("mimeType", "")
                        name = it.get("name", "item")
                        link = file_link(it)

                        emoji = "📁" if is_folder_or_folder_shortcut(it) else icon_for_mime(mime)

                        msg = (
                            f"{header}\n\n"
                            f"{emoji} {name}\n"
                            f"🔗 {link}"
                        )

                        try:
                            app.bot.send_message(chat_id=CHANNEL_ID, text=msg)
                            count_sent += 1
                        except Exception:
                            # If sending fails, skip
                            pass

                # Save snapshot
                STATE.setdefault("last_seen", {})[module_key] = list(current_ids)
                save_state(STATE)

        except Exception:
            # Keep loop alive even if something fails once
            pass

        time.sleep(MONITOR_INTERVAL_SECONDS)

# =========================
# MAIN
# =========================
def main():
    if not BOT_TOKEN or not GOOGLE_API_KEY:
        raise RuntimeError("Missing BOT_TOKEN or GOOGLE_API_KEY in Secrets")
    if not CHANNEL_ID:
        raise RuntimeError("Missing CHANNEL_ID in Secrets")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_click))

    # Start monitoring thread
    t = threading.Thread(target=monitor_loop, args=(app,), daemon=True)
    t.start()

    app.run_polling()

if __name__ == "__main__":
    main()
