```python
import logging
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

# --- CONFIGURATION ---
TELEGRAM_BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
ADMIN_TELEGRAM_HANDLES = ["@admin_user"]  # List of Admin Telegram Handles (or Telegram IDs)

# --- GOOGLE SHEETS SETUP ---
# Ensure your JSON key file is present in the working directory as 'credentials.json'
SCOPE = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]
CREDS = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", SCOPE)
GSPREAD_CLIENT = gspread.authorize(CREDS)

# Open Google Sheet workbook
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
            if str(r.get("Writer Telegram ID")).strip() == user_handle and str(r.get("Status")).strip().lower() == "admin":
                return True
    except Exception as e:
        logger.error(f"Error checking admin status: {e}")
    return False


def get_writer_brands(writer_handle: str):
    records = brands_sheet.get_all_records()
    assigned_brands = [
        r for r in records if str(r.get("Assigned Writer")).strip() == writer_handle
    ]
    return assigned_brands


# --- COMMAND HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    handle = get_user_handle(update)
    admin_flag = is_admin(handle)

    if admin_flag:
        msg = (
            f"👑 *Welcome Admin ({handle})*\n\n"
            "Use the menu below or type commands:\n"
            "• `/addbrand` - Interactive wizard to add a brand\n"
            "• `/search <keyword>` - Search brands by name or category\n"
            "• `/mybrands` - View assigned brands\n"
            "• `/stats` - CRM overview"
        )
    else:
        msg = (
            f"📝 *Welcome Writer ({handle})*\n\n"
            "Use the menu below to manage your assigned brands:\n"
            "• `/mybrands` - View your 5 assigned brands & submit logs\n"
            "• `/help` - Assistance and guidelines"
        )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def my_brands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    handle = get_user_handle(update)
    brands = get_writer_brands(handle)

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
        text = (
            f"🆔 *ID:* `{brand.get('Brand ID')}`\n"
            f"🏢 *Name:* {brand.get('Brand Name')}\n"
            f"🏷 *Category:* {brand.get('Category')}\n"
            f"🐦 *X:* {brand.get('X Handle')}\n"
            f"🌐 *Website:* {brand.get('Website')}\n\n"
            f"📖 *Description:* {brand.get('Description')}\n"
            f"📌 *Current Notes:* {brand.get('Notes')}"
        )
        keyboard = [
            [
                InlineKeyboardButton(
                    "➕ Add Activity Log", callback_data=f"log_{brand.get('Brand ID')}"
                )
            ]
        ]
        await update.message.reply_text(
            text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def search_brands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    handle = get_user_handle(update)
    if not is_admin(handle):
        await update.message.reply_text("⛔ Unauthorized action.")
        return

    query = " ".join(context.args).strip().lower()
    if not query:
        await update.message.reply_text("Usage: `/search <category or brand name>`", parse_mode="Markdown")
        return

    records = brands_sheet.get_all_records()
    matches = [
        r for r in records
        if query in str(r.get("Brand Name")).lower() or query in str(r.get("Category")).lower()
    ]

    if not matches:
        await update.message.reply_text(f"🔍 No brands found matching '{query}'.")
        return

    res = f"🔍 *Found {len(matches)} matching brand(s):*\n\n"
    for b in matches[:10]:
        assigned = b.get("Assigned Writer") or "❌ Unassigned"
        res += (
            f"• *{b.get('Brand Name')}* (`{b.get('Brand ID')}`) | {b.get('Category')}\n"
            f"  Assigned: `{assigned}` | [Website]({b.get('Website')})\n\n"
        )
    await update.message.reply_text(res, parse_mode="Markdown", disable_web_page_preview=True)


# --- CONVERSATION: ADD BRAND (ADMIN ONLY) ---
async def start_add_brand(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(get_user_handle(update)):
        await update.message.reply_text("⛔ Admin access required.")
        return ConversationHandler.END

    await update.message.reply_text("➕ *Add New Brand*\n\nPlease enter the **Brand Name**:")
    return ADD_BRAND_NAME


async def add_brand_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["b_name"] = update.message.text
    await update.message.reply_text("Enter the **Category** (e.g., DeFi, GameFi, Infrastructure):")
    return ADD_CATEGORY


async def add_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["b_cat"] = update.message.text
    await update.message.reply_text("Enter the **X Handle** (e.g., @brandhandle):")
    return ADD_X_HANDLE


async def add_x_handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["b_x"] = update.message.text
    await update.message.reply_text("Enter the **Website URL**:")
    return ADD_WEBSITE


async def add_website(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["b_web"] = update.message.text
    await update.message.reply_text("Enter the **Description**:")
    return ADD_DESC


async def add_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["b_desc"] = update.message.text
    await update.message.reply_text("Enter any initial **Notes** (or type 'none'):")
    return ADD_NOTES


async def add_notes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    notes = update.message.text
    u_data = context.user_data

    # Generate new ID
    records = brands_sheet.get_all_records()
    new_id = f"BRD-00{len(records) + 1}"

    new_row = [
        new_id,
        u_data["b_name"],
        u_data["b_cat"],
        u_data["b_x"],
        u_data["b_web"],
        u_data["b_desc"],
        "",  # Unassigned initially
        notes if notes.lower() != "none" else "",
    ]

    brands_sheet.append_row(new_row)
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

    # Get writer's full name if available
    writers = writers_sheet.get_all_records()
    writer_name = handle
    for w in writers:
        if str(w.get("Writer Telegram ID")).strip() == handle:
            writer_name = w.get("Writer Name", handle)

    logs = logs_sheet.get_all_records()
    log_id = f"LOG-{1001 + len(logs)}"
    today_str = datetime.now().strftime("%Y-%m-%d")

    # Add row to Activity Logs
    logs_sheet.append_row([log_id, brand_id, handle, writer_name, today_str, log_text])

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

    # Add Brand Conversation Handler
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

    # Add Activity Log Conversation Handler
    add_log_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(handle_log_button, pattern="^log_")],
        states={
            ADD_LOG_CONTENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_log_entry)]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # Register handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("mybrands", my_brands))
    app.add_handler(CommandHandler("search", search_brands))
    app.add_handler(add_brand_conv)
    app.add_handler(add_log_conv)

    print("🤖 Telegram CRM Bot started running...")
    app.run_polling()


if __name__ == "__main__":
    main()
```
