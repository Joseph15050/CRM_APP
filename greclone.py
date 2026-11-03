import os
import json
import logging
import asyncio
import threading
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
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
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "9215727245:AAHS4_B3C-mAdKTxHgVjvpD3N30Bx2zh1G0")
ADMIN_TELEGRAM_HANDLES = ["@greclone"]

# --- GOOGLE SHEETS SETUP ---
SCOPE = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]

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
    assigned_brands = []
    for r in records:
        assigned_id = str(r.get("Assigned Writer Telegram ID") or "").strip()
        if assigned_id.lower() == writer_handle.lower():
            assigned_brands.append(r)
    return assigned_brands


# --- COMMAND HANDLERS ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    handle = get_user_handle(update)
    admin_flag = await asyncio.to_thread(is_admin, handle)

    if admin_flag:
        msg = (
            f"👑 *Welcome Admin ({handle})*\n\n"
            "Admin Controls:\n"
            "• `/addbrand` - Add a new brand\n"
            "• `/search <keyword>` - Flexible search across brands\n"
            "• `/editbrand <ID> <field> <value>` - Update brand details / append notes\n"
            "• `/auto_distribute` - Automatically assign 5 brands to active writers\n"
            "• `/mybrands` - View assigned brands\n"
            "• `/cancel` - Cancel current action"
        )
    else:
        msg = (
            f"📝 *Welcome Writer ({handle})*\n\n"
            "Your Dashboard:\n"
            "• `/mybrands` - View your strictly assigned brands & submit logs\n"
            "• `/cancel` - Cancel current action"
        )
    await update.message.reply_text(msg, parse_mode="Markdown")


# --- STRICT WRITER VIEW (Only assigned brands) ---
async def my_brands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    handle = get_user_handle(update)
    brands = await asyncio.to_thread(get_writer_brands, handle)

    if not brands:
        await update.message.reply_text(
            f"⚠️ You currently have **0 brands** assigned to your handle (`{handle}`). Please contact the admin.",
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


# --- ADMIN: AUTO-DISTRIBUTE 5 BRANDS PER WRITER ---
async def auto_distribute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    handle = get_user_handle(update)
    if not await asyncio.to_thread(is_admin, handle):
        await update.message.reply_text("⛔ Unauthorized action. Admin access required.")
        return

    try:
        writers_data = await asyncio.to_thread(writers_sheet.get_all_records)
        brands_data = await asyncio.to_thread(brands_sheet.get_all_records)

        unassigned_brands = [
            (i + 2, b) for i, b in enumerate(brands_data) 
            if not str(b.get("Assigned Writer Telegram ID", "")).strip()
        ]

        if not unassigned_brands:
            await update.message.reply_text("⚠️ No unassigned brands available in the database to distribute.")
            return

        for writer in writers_data:
            w_id = str(writer.get("Writer Telegram ID") or writer.get("Telegram ID")).strip()
            w_name = str(writer.get("Writer Name")).strip()
            status = str(writer.get("Status", "Active")).strip().lower()

            if status != "active" or not w_id:
                continue

            current_writer_brands = [b for b in brands_data if str(b.get("Assigned Writer Telegram ID")).strip() == w_id]
            needed = 5 - len(current_writer_brands)

            if needed > 0 and unassigned_brands:
                for _ in range(needed):
                    if not unassigned_brands:
                        break
                    row_idx, brand_obj = unassigned_brands.pop(0)
                    
                    await asyncio.to_thread(brands_sheet.update_cell, row_idx, 7, w_id)
                    await asyncio.to_thread(brands_sheet.update_cell, row_idx, 8, w_name)

        await update.message.reply_text(
            f"✅ *Distribution Complete!*\nSuccessfully assigned brands (up to 5 per writer) across your active team.",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Error in auto_distribute: {e}")
        await update.message.reply_text(f"❌ Error executing distribution: {str(e)}")


# --- ADMIN: FLEXIBLE SEARCH ---
async def search_brands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    handle = get_user_handle(update)
    if not await asyncio.to_thread(is_admin, handle):
        await update.message.reply_text("⛔ Admin access required.")
        return

    query = " ".join(context.args).strip().lower()
    if not query:
        await update.message.reply_text("Usage: `/search <keyword>` (e.g., `/search DeFi` or `/search Aether`)", parse_mode="Markdown")
        return

    try:
        records = await asyncio.to_thread(brands_sheet.get_all_records)
        matches = []
        for r in records:
            b_name = str(r.get("Brand Name", "")).lower()
            b_cat = str(r.get("Category", "")).lower()
            b_desc = str(r.get("Description (About)", "")).lower()
            b_notes = str(r.get("Notes", "")).lower()
            b_id = str(r.get("Brand ID", "")).lower()

            if query in b_name or query in b_cat or query in b_desc or query in b_notes or query in b_id:
                matches.append(r)

        if not matches:
            await update.message.reply_text(f"🔍 No brands found matching '{query}'. Try a broader keyword.")
            return

                res = f"🔍 *Found {len(matches)} matching brand(s) for '{query}':*\n\n"
        for b in matches[:10]:
            assigned = b.get("Assigned Writer Telegram ID") or "❌ Unassigned"
            x_handle = b.get("X Handle (URL)") or b.get("X Handle") or b.get("X") or "N/A"
            res += (
                f"• *{b.get('Brand Name')}* (`{b.get('Brand ID')}`) | {b.get('Category')}\n"
                f"  🐦 X: `{x_handle}`\n"
                f"  👤 Assigned: `{assigned}`\n"
                f"  🌐 [Website]({b.get('Website')})\n\n"
            )
            
        await update.message.reply_text(res, parse_mode="Markdown", disable_web_page_preview=True)
    except Exception as e:
        logger.error(f"Error searching brands: {e}")
        await update.message.reply_text(f"❌ Error performing search: {str(e)}")


# --- ADMIN: EDIT BRAND (WITH AUTO-APPEND FOR NOTES) ---
async def edit_brand(update: Update, context: ContextTypes.DEFAULT_TYPE):
    handle = get_user_handle(update)
    if not await asyncio.to_thread(is_admin, handle):
        await update.message.reply_text("⛔ Admin access required.")
        return

    if len(context.args) < 3:
        msg = (
            "🛠 *Usage for Editing Brands:*\n"
            "`/editbrand <Brand_ID> <field> <new_value>`\n\n"
            "*Available Fields:* `name`, `category`, `x`, `website`, `desc`, `notes`, `status`\n"
            "*Note on `notes`:* Adding new notes automatically appends them cleanly to any existing notes!\n"
            "*Example:* `/editbrand BRD-001 notes Completed week 1 content rollout.`"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")
        return

    target_id = context.args[0].strip().upper()
    field_key = context.args[1].strip().lower()
    input_value = " ".join(context.args[2:]).strip()

    field_mapping = {
        "name": (2, "Brand Name"),
        "category": (3, "Category"),
        "x": (4, "X Handle (URL)"),
        "website": (5, "Website"),
        "desc": (6, "Description (About)"),
        "notes": (9, "Notes"),
        "status": (10, "Status")
    }

    if field_key not in field_mapping:
        await update.message.reply_text(f"❌ Invalid field '{field_key}'. Use: name, category, x, website, desc, notes, status")
        return

    col_idx, col_name = field_mapping[field_key]

    try:
        brands_data = await asyncio.to_thread(brands_sheet.get_all_records)
        found_row = None
        current_val = ""
        
        for idx, brand in enumerate(brands_data):
            if str(brand.get("Brand ID")).strip().upper() == target_id:
                found_row = idx + 2
                current_val = str(brand.get("Notes", "")).strip()
                break

        if not found_row:
            await update.message.reply_text(f"❌ Brand ID `{target_id}` not found in the CRM.", parse_mode="Markdown")
            return

        if field_key == "notes":
            today_date = datetime.now().strftime("%Y-%m-%d")
            formatted_addition = f"[{today_date}] {input_value}"
            if current_val and current_val.lower() != "none":
                new_value = f"{current_val} | {formatted_addition}"
            else:
                new_value = formatted_addition
        else:
            new_value = input_value

        await asyncio.to_thread(brands_sheet.update_cell, found_row, col_idx, new_value)
        
        today_str = datetime.now().strftime("%Y-%m-%d")
        await asyncio.to_thread(brands_sheet.update_cell, found_row, 11, today_str)

        await update.message.reply_text(
            f"✅ *Brand Successfully Updated!*\n\n"
            f"🆔 *ID:* `{target_id}`\n"
            f"📝 *Field Changed:* `{col_name}`\n"
            f"📌 *Updated Content:* {new_value}",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Error editing brand: {e}")
        await update.message.reply_text(f"❌ Error updating brand: {str(e)}")


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
    await update.message.reply_text("Enter the **Category**:", parse_mode="Markdown")
    return ADD_CATEGORY


async def add_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["b_cat"] = update.message.text
    await update.message.reply_text("Enter the **X Handle / URL**:", parse_mode="Markdown")
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
        "Active",
        today_str,
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


# --- HEALTH CHECK SERVER FOR RENDER ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

def run_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()


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
    app.add_handler(CommandHandler("editbrand", edit_brand))
    app.add_handler(CommandHandler("auto_distribute", auto_distribute))
    app.add_handler(add_brand_conv)
    app.add_handler(add_log_conv)

    print("🤖 Telegram CRM Bot started running...")
    
    # Start dummy web server in background thread for Render
    threading.Thread(target=run_dummy_server, daemon=True).start()
    
    app.run_polling()


if __name__ == "__main__":
    main()
