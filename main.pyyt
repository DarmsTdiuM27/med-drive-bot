import os
import re
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

ROOT_FOLDER_ID = "1EnPIlIcMf_XNI2Zu_xYoVZJnMKQiwThs"
PAGE_SIZE = 25

# ---------- Drive helpers ----------

FOLDER_MIME = "application/vnd.google-apps.folder"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"

def drive_list(folder_id: str):
    """List children of folder_id, including shortcut target info."""
    url = "https://www.googleapis.com/drive/v3/files"
    items = []
    page_token = None

    while True:
        params = {
            "key": GOOGLE_API_KEY,
            "q": f"'{folder_id}' in parents and trashed=false",
            # include shortcutDetails so we can resolve shortcuts
            "fields": "nextPageToken,files(id,name,mimeType,webViewLink,shortcutDetails(targetId,targetMimeType))",
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

    return items

def is_shortcut(it) -> bool:
    return it.get("mimeType") == SHORTCUT_MIME and it.get("shortcutDetails")

def is_folder_or_folder_shortcut(it) -> bool:
    if it.get("mimeType") == FOLDER_MIME:
        return True
    if is_shortcut(it) and it["shortcutDetails"].get("targetMimeType") == FOLDER_MIME:
        return True
    return False

def resolve_folder_id_and_name(it):
    """
    If it's a real folder -> return its id/name.
    If it's a shortcut to folder -> return targetId and its displayed name.
    """
    if it.get("mimeType") == FOLDER_MIME:
        return it["id"], it["name"]
    # shortcut to folder
    return it["shortcutDetails"]["targetId"], it["name"]

def icon_for_mime(mime: str) -> str:
    # Google types
    if mime == "application/pdf":
        return "📕"
    if mime == "application/vnd.google-apps.document":
        return "📝"  # Google Doc
    if mime == "application/vnd.google-apps.presentation":
        return "📊"  # Google Slides
    if mime == "application/vnd.google-apps.spreadsheet":
        return "📗"  # Google Sheets
    # Office types
    if mime in ("application/msword", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"):
        return "📝"
    if mime in ("application/vnd.ms-powerpoint", "application/vnd.openxmlformats-officedocument.presentationml.presentation"):
        return "📊"
    if mime in ("application/vnd.ms-excel", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"):
        return "📗"
    return "📄"

def file_link(it) -> str:
    # For shortcuts to files, open the shortcut webViewLink (usually works).
    return it.get("webViewLink") or f"https://drive.google.com/file/d/{it['id']}/view"

def module_sort_key(name: str):
    """
    Sort M17, M18, M19... numerically at root.
    If not matching, fall back to alphabetical.
    """
    m = re.match(r"^\s*M(\d+)\b", name, re.IGNORECASE)
    if m:
        return (0, int(m.group(1)), name.lower())
    return (1, 10**9, name.lower())

# ---------- Telegram bot ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["stack"] = [(ROOT_FOLDER_ID, "Home")]
    context.user_data["offset"] = 0
    context.user_data["name_map"] = {}      # folder_id -> display name (current view)
    context.user_data["folderid_map"] = {}  # displayed-id -> real folder id (handles shortcuts)
    await send_folder(update.message, context)

async def send_folder(message, context: ContextTypes.DEFAULT_TYPE):
    folder_id, path = context.user_data["stack"][-1]
    offset = context.user_data.get("offset", 0)

    items = drive_list(folder_id)

    # Split into folders (including folder shortcuts) and files
    folders = [x for x in items if is_folder_or_folder_shortcut(x)]
    files = [x for x in items if not is_folder_or_folder_shortcut(x)]

    # Root sorting: M17 -> M18 -> M19...
    if folder_id == ROOT_FOLDER_ID:
        folders.sort(key=lambda x: module_sort_key(x.get("name", "")))
    else:
        folders.sort(key=lambda x: x.get("name", "").lower())

    files.sort(key=lambda x: x.get("name", "").lower())

    merged = folders + files
    page = merged[offset: offset + PAGE_SIZE]

    # Map displayed folder "button id" -> real folder id (handles shortcuts)
    folderid_map = {}
    for it in folders:
        real_id, display_name = resolve_folder_id_and_name(it)
        folderid_map[it["id"]] = real_id  # use item id in callback, resolve to real folder id later

    context.user_data["folderid_map"] = folderid_map

    # Also keep names for nice path display
    name_map = {}
    for it in folders:
        # display name as shown in Drive
        name_map[it["id"]] = it.get("name", "Folder")
    context.user_data["name_map"] = name_map

    kb = []

    # Nav
    if len(context.user_data["stack"]) > 1:
        kb.append([
            InlineKeyboardButton("⬅️ Back", callback_data="BACK"),
            InlineKeyboardButton("🏠 Home", callback_data="HOME")
        ])
    else:
        kb.append([InlineKeyboardButton("🏠 Home", callback_data="HOME")])

    # Paging
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
            # ✅ Always open folders inside bot (even if they are shortcuts)
            kb.append([InlineKeyboardButton(f"📁 {it['name']}", callback_data=f"OPEN:{it['id']}")])
        else:
            # files open via link
            icon = icon_for_mime(it.get("mimeType", ""))
            kb.append([InlineKeyboardButton(f"{icon} {it['name']}", url=file_link(it))])

    await message.reply_text(f"📂 {path}", reply_markup=InlineKeyboardMarkup(kb))

async def on_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

        # Resolve: if clicked item is a shortcut, map to target folder id
        real_folder_id = folderid_map.get(clicked_id, clicked_id)
        folder_name = name_map.get(clicked_id, "Folder")

        current_path = context.user_data["stack"][-1][1]
        new_path = f"{current_path} › {folder_name}"

        context.user_data["stack"].append((real_folder_id, new_path))
        context.user_data["offset"] = 0
        await send_folder(q.message, context)
        return

def main():
    if not BOT_TOKEN or not GOOGLE_API_KEY:
        raise RuntimeError("Missing BOT_TOKEN or GOOGLE_API_KEY in Secrets")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_click))
    app.run_polling()

if __name__ == "__main__":
    main()
