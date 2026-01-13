import os
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

ROOT_FOLDER_ID = "1EnPIlIcMf_XNI2Zu_xYoVZJnMKQiwThs"
PAGE_SIZE = 25

def drive_list(folder_id: str):
    url = "https://www.googleapis.com/drive/v3/files"
    items = []
    page_token = None
    while True:
        params = {
            "key": GOOGLE_API_KEY,
            "q": f"'{folder_id}' in parents and trashed=false",
            "fields": "nextPageToken,files(id,name,mimeType,webViewLink)",
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

def is_folder(it):
    return it["mimeType"] == "application/vnd.google-apps.folder"

def icon(it):
    if is_folder(it):
        return "📁"
    mt = it["mimeType"]
    if mt == "application/pdf":
        return "📕"
    if "presentation" in mt:
        return "📊"
    if "document" in mt or "word" in mt:
        return "📝"
    return "📄"

def file_link(it):
    return it.get("webViewLink") or f"https://drive.google.com/file/d/{it['id']}/view"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["stack"] = [(ROOT_FOLDER_ID, "Home")]
    context.user_data["offset"] = 0
    context.user_data["name_map"] = {}
    await send_folder(update.message, context)

async def send_folder(message, context: ContextTypes.DEFAULT_TYPE):
    folder_id, path = context.user_data["stack"][-1]
    offset = context.user_data.get("offset", 0)

    items = drive_list(folder_id)
    folders = sorted([x for x in items if is_folder(x)], key=lambda x: x["name"].lower())
    files = sorted([x for x in items if not is_folder(x)], key=lambda x: x["name"].lower())
    merged = folders + files

    page = merged[offset: offset + PAGE_SIZE]

    name_map = {}
    for it in folders:
        name_map[it["id"]] = it["name"]
    context.user_data["name_map"] = name_map

    kb = []

    if len(context.user_data["stack"]) > 1:
        kb.append([
            InlineKeyboardButton("⬅️ Back", callback_data="BACK"),
            InlineKeyboardButton("🏠 Home", callback_data="HOME")
        ])
    else:
        kb.append([InlineKeyboardButton("🏠 Home", callback_data="HOME")])

    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("◀️ Prev", callback_data="PREV"))
    if offset + PAGE_SIZE < len(merged):
        nav.append(InlineKeyboardButton("Next ▶️", callback_data="NEXT"))
    if nav:
        kb.append(nav)

    for it in page:
        if is_folder(it):
            kb.append([InlineKeyboardButton(f"{icon(it)} {it['name']}", callback_data=f"OPEN:{it['id']}")])
        else:
            kb.append([InlineKeyboardButton(f"{icon(it)} {it['name']}", url=file_link(it))])

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
        folder_id = data.split(":", 1)[1]
        name_map = context.user_data.get("name_map", {})
        folder_name = name_map.get(folder_id, "Folder")
        current_path = context.user_data["stack"][-1][1]
        new_path = f"{current_path} › {folder_name}"
        context.user_data["stack"].append((folder_id, new_path))
        context.user_data["offset"] = 0
        await send_folder(q.message, context)

def main():
    if not BOT_TOKEN or not GOOGLE_API_KEY:
        raise RuntimeError("Missing BOT_TOKEN or GOOGLE_API_KEY")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_click))
    app.run_polling()

if __name__ == "__main__":
    main()
