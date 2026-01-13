import os
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# Read secrets from environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# Root Google Drive folder (contains M18, M19, M20...)
ROOT_FOLDER_ID = "1EnPIlIcMf_XNI2Zu_xYoVZJnMKQiwThs"

PAGE_SIZE = 25  # buttons per page (safe for Telegram)

# ------------------ GOOGLE DRIVE ------------------

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

def is_folder(item):
    return item["mimeType"] == "application/vnd.google-apps.folder"

def icon(item):
    if is_folder(item):
        return "📁"
    mt = item["mimeType"]
    if mt == "application/pdf":
        return "📕"
    if "presentation" in mt:
        return "📊"
    if "document" in mt or "word" in mt:
        return "📝"
    return "📄"

def file_link(item):
    return item.get("webViewLink") or f"https://drive.google.com/file/d/{item['id']}/view"

# ------------------ TELEGRAM ------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["stack"] = [(ROOT_FOLDER_ID, "Home")]
    context.user_data["offset"] = 0
    await show_folder(update.message, context)

async def show_folder(message, context):
    folder_id, path = context.user_data["stack"][-1]
    offset = context.user_data["offset"]

    items = drive_list(folder_id)

    folders = sorted(
        [x for x in items if is_folder(x)],
        key=lambda x: x["name"].lower()
    )
    files = sorted(
        [x for x in items if not is_folder(x)],
        key=lambda x: x["name"].lower()
    )

    merged = folders + files
    page = merged[offset: offset + PAGE_SIZE]

    keyboard = []

    # Navigation
    if len(context.user_data["stack"]) > 1:
        keyboard.append([
            InlineKeyboardButton("⬅️ Back", callback_data="BACK"),
            InlineKeyboardButton("🏠 Home", callback_data="HOME")
        ])
    else:
        keyboard.append([InlineKeyboardButton("🏠 Home", callback_data="HOME")])

    # Pagination
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("◀️ Prev", callback_data="PREV"))
    if offset + PAGE_SIZE < len(merged):
        nav.append(InlineKeyboardButton("Next ▶️", callback_data="NEXT"))
    if nav:
        keyboard.append(nav)

    # Items
    for item in page:
        if is_folder(item):
            new_path = f"{path} › {item['name']}"
            keyboard.append([
                InlineKeyboardButton(
                    f"{icon(item)} {item['name']}",
                    callback_data=f"OPEN:{item['id']}:{new_path}"
                )
            ])
        else:
            keyboard.append([
                InlineKeyboardButton(
                    f"{icon(item)} {item['name']}",
                    url=file_link(item)
                )
            ])

    await message.reply_text(
        f"📂 {path}",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def on_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "HOME":
        context.user_data["stack"] = [(ROOT_FOLDER_ID, "Home")]
        context.user_data["offset"] = 0
        await show_folder(query.message, context)

    elif data == "BACK":
        if len(context.user_data["stack"]) > 1:
            context.user_data["stack"].pop()
        context.user_data["offset"] = 0
        await show_folder(query.message, context)

    elif data == "PREV":
        context.user_data["offset"] -= PAGE_SIZE
        await show_folder(query.message, context)

    elif data == "NEXT":
        context.user_data["offset"] += PAGE_SIZE
        await show_folder(query.message, context)

    elif data.startswith("OPEN:"):
        _, folder_id, path = data.split(":", 2)
        context.user_data["stack"].append((folder_id, path))
        context.user_data["offset"] = 0
        await show_folder(query.message, context)

# ------------------ MAIN ------------------

def main():
    if not BOT_TOKEN or not GOOGLE_API_KEY:
        raise RuntimeError("Missing BOT_TOKEN or GOOGLE_API_KEY environment variables")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_click))
    app.run_polling()

if __name__ == "__main__":
    main()
