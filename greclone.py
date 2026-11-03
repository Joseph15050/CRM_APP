import os
import json
import logging
import asyncio
from datetime import datetime
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- CONFIGURATION (ENVIRONMENT VARIABLES) ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "9215727245:AAHS4_B3C-mAdKTxHgVjvpD3N30Bx2zh1G0")
ADMIN_TELEGRAM_HANDLES = ["@greclone"]

# --- GOOGLE SHEETS SETUP ---
SCOPE = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]

# Support both local credentials.json and Render GOOGLE_CREDENTIALS_JSON env var
creds_env = os.getenv("GOOGLE_CREDENTIALS_JSON")
if creds_env:
    CREDS = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(creds_env), SCOPE)
else:
    CREDS = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", SCOPE)

GSPREAD_CLIENT = gspread.authorize(CREDS)

SPREADSHEET_NAME = "Greclone_Blog_CRM"
sheet = GSPREAD_CLIENT.open(SPREADSHEET_NAME)

brands_sheet = sheet.worksheet("Brands")
writers_sheet = sheet.worksheet("Writers")
logs_sheet = sheet.worksheet("Activity_Logs")

# --- CONVERSATION STATES ---
ADD_BRAND_NAME, ADD_CATEGORY, ADD_X_HANDLE, ADD_WEBSITE, ADD_DESC, ADD_NOTES = range(6)
ADD_LOG_CONTENT = 6


# --- HELPER FUNCTIONS ---
def get_user_handle(update: Update) -> str:
    username = update.effective_user.username
    return f"@{username}" if username else str(update.effective_user.id)


def is_admin(user_handle: str) -> bool:
    if user_handle in ADMIN_TELEGRAM_HANDLES:
        return True
    try:
        records = writers_sheet.get_all_records()
        for r in records:
            writer_id = str(r.get("Writer Telegram ID") or r.get("Telegram ID")).strip()
            status = str(r.get("Status") or r.get("Role")).strip().lower()
            if writer_id == user_handle and status == "admin":
                return True
    except Exception as e:
        logger.error(f"Error checking admin status: {e}")
    return False


def get_writer_brands(writer_handle: str):
    records = brands_sheet.get_all_records()
    assigned_brands = [
        r for r in records 
        if str(r.get("Assigned Writer Telegram ID") or r.get("Assigned Writer")).strip() == writer_handle
    ]
    return assigned_brands


# --- COMMAND HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    handle = get_user_handle(update)
    admin_flag = await asyncio.to_thread(is_admin, handle)

    if admin_flag:
        msg = (
            f"👑 *Welcome Admin ({handle})*\n\n"
            "Use the menu below or type commands:\n"
            "• `/addbrand` - Add a new brand\n"
            "• `/search <keyword>` - Search brands by name/category\n"
            "• `/mybrands` - View assigned brands\n"
            "• `/cancel` - Cancel current action"
        )
    else:
        msg = (
            f"📝 *Welcome Writer ({handle})*\n\n"
            "Use the menu below to manage your assigned brands:\n"
            "• `/mybrands` - View your assigned brands & submit logs\n"
            "• `/cancel` - Cancel current action"
        )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def my_brands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    handle = get_user_handle(update)
    brands = await asyncio.to_thread(get_writer_brands, handle)

    if not brands:
        await update.message.reply_text(
            f"⚠️ No brands are currently assigned to `{handle}` in the CRM.",
            parse_mode="Markdown",
        )
        return

    await update.message.reply_text(
        f"📋 *Your Assigned Brands ({len(brands)}/5):*", parse_mode="Markdown"
    )

    for brand in brands[:5]:
        b_id = brand.get("Brand ID")
        b_name = brand.get("Brand Name")
        b_cat = brand.get("Category")
        b_x = brand.get("X Handle (URL)") or brand.get("X Handle")
        b_web = brand.get("Website")
        b_desc = brand.get("Description (About)") or brand.get("Description")
        b_notes = brand.get("Notes")

        text = (
            f"🆔 *ID:* `{b_id}`\n"
            f"🏢 *Name:* {b_name}\n"
            f"🏷 *Category:* {b_cat}\n"
            f"🐦 *X:* {b_x}\n"
            f"🌐 *Website:* {b_web}\n\n"
            f"📖 *Description:* {b_desc}\n"
            f"📌 *Notes:* {b_notes}"
        )
        keyboard = [
            [InlineKeyboardButton("➕ Add Activity Log", callback_data=f"log_{b_id}")]
        ]
        await update.message.reply_text(
            text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def search_brands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    handle = get_user_handle(update)
    if not await asyncio.to_thread(is_admin, handle):
        await update.message.reply_text("⛔ Unauthorized action.")
        return

    query = " ".join(context.args).strip().lower()
    if not query:
        await update.message.reply_text("Usage: `/search <category or brand name>`", parse_mode="Markdown")
        return

    records = await asyncio.to_thread(brands_sheet.get_all_records)
    matches = [
        r for r in records
        if query in str(r.get("Brand Name")).lower() or query in str(r.get("Category")).lower()
    ]

    if not matches:
        await update.message.reply_text(f"🔍 No brands found matching '{query}'.")
        return

    res = f"🔍 *Found {len(matches)} matching brand(s):*\n\n"
    for b in matches[:10]:
        assigned = b.get("Assigned Writer Telegram ID") or b.get("Assigned Writer") or "❌ Unassigned"
        b_name = b.get("Brand Name")
        b_id = b.get("Brand ID")
        b_cat = b.get("Category")
        b_web = b.get("Website")

        res += (
            f"• *{b_name}* (`{b_id}`) | {b_cat}\n"
            f"  Assigned: `{assigned}` | [Website]({b_web})\n\n"
        )
    await update.message.reply_text(res, parse_mode="Markdown", disable_web_page_preview=True)


# --- CONVERSATION: ADD BRAND ---
async def start_add_brand(update: Update, context: ContextTypes.DEFAULT_TYPE):
    handle = get_user_handle(update)
    if not await asyncio.to_thread(is_admin, handle):
        await update.message.reply_text("⛔ Admin access required.")
        return ConversationHandler.END

    await update.message.reply_text("➕ *Add New Brand*\n\nPlease enter the **Brand Name**:", parse_mode="Markdown")
    return ADD_BRAND_NAME


async def add_brand_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["b_name"] = update.message.text
    await update.message.reply_text("Enter the **Category** (e.g., DeFi / DEX, Infrastructure / L2):", parse_mode="Markdown")
    return ADD_CATEGORY


async def add_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["b_cat"] = update.message.text
    await update.message.reply_text("Enter the **X Handle / URL** (e.g., https://x.com/brand):", parse_mode="Markdown")
    return ADD_X_HANDLE


async def add_x_handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["b_x"] = update.message.text
    await update.message.reply_text("Enter the **Website URL**:", parse_mode="Markdown")
    return ADD_WEBSITE


async def add_website(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["b_web"] = update.message.text
    await update.message.reply_text("Enter the **Description (About)**:", parse_mode="Markdown")
    return ADD_DESC


async def add_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["b_desc"] = update.message.text
    await update.message.reply_text("Enter initial **Notes** (or type 'none'):", parse_mode="Markdown")
    return ADD_NOTES


async def add_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    notes = update.message.text
    u_data = context.user_data

    records = await asyncio.to_thread(brands_sheet.get_all_records)
    new_id = f"BRD-{len(records) + 1:03d}"
    today_str = datetime.now().strftime("%Y-%m-%d")

    # Match all 11 columns in Greclone_Blog_CRM_Brands.csv
    new_row = [
        new_id,
        u_data["b_name"],
        u_data["b_cat"],
        u_data["b_x"],
        u_data["b_web"],
        u_data["b_desc"],
        "",  # Assigned Writer Telegram ID
        "",  # Assigned Writer Name
        notes if notes.lower() != "none" else "",
        "Active",  # Status
        today_str,  # Last Updated
    ]

    await asyncio.to_thread(brands_sheet.append_row, new_row)
    await update.message.reply_text(
        f"✅ *Brand Successfully Added!*\n\nID: `{new_id}`\nName: *{u_data['b_name']}*",
        parse_mode="Markdown",
    )
    context.user_data.clear()
    return ConversationHandler.END


# --- CONVERSATION: ADD LOG NOTE ---
async def handle_log_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    brand_id = query.data.split("_")[1]
    context.user_data["target_brand_id"] = brand_id

    await query.message.reply_text(
        f"📝 Please enter the **Activity Log entry** for Brand `{brand_id}`:",
        parse_mode="Markdown",
    )
    return ADD_LOG_CONTENT


async def save_log_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    log_text = update.message.text
    brand_id = context.user_data.get("target_brand_id")
    handle = get_user_handle(update)

    writers = await asyncio.to_thread(writers_sheet.get_all_records)
    writer_name = handle
    for w in writers:
        w_id = str(w.get("Writer Telegram ID") or w.get("Telegram ID")).strip()
        if w_id == handle:
            writer_name = w.get("Writer Name", handle)

    logs = await asyncio.to_thread(logs_sheet.get_all_records)
    log_id = f"LOG-{1001 + len(logs)}"
    timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Match exact 6 columns in Greclone_Blog_CRM_Activity_Logs.csv
    # [Log ID, Timestamp, Brand ID, Telegram ID, Writer Name, Action/Note]
    log_row = [log_id, timestamp_str, brand_id, handle, writer_name, log_text]

    await asyncio.to_thread(logs_sheet.append_row, log_row)

    await update.message.reply_text(
        f"✅ Activity Log recorded successfully for brand `{brand_id}` under log ID `{log_id}`!",
        parse_mode="Markdown",
    )
    context.user_data.clear()
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Operation cancelled.")
    context.user_data.clear()
    return ConversationHandler.END


# --- MAIN ENGINE ---
def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    add_brand_conv = ConversationHandler(
        entry_points=[CommandHandler("addbrand", start_add_brand)],
        states={
            ADD_BRAND_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_brand_name)],
            ADD_CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_category)],
            ADD_X_HANDLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_x_handle)],
            ADD_WEBSITE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_website)],
            ADD_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_desc)],
            ADD_NOTES: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_notes)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    add_log_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(handle_log_button, pattern="^log_")],
        states={
            ADD_LOG_CONTENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_log_entry)]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("mybrands", my_brands))
    app.add_handler(CommandHandler("search", search_brands))
    app.add_handler(add_brand_conv)
    app.add_handler(add_log_conv)

    print("🤖 Telegram CRM Bot started running...")
    app.run_polling()


if __name__ == "__main__":
    main()
