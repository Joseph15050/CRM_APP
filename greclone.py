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
# Brand States
ADD_BRAND_NAME, ADD_CATEGORY, ADD_X_HANDLE, ADD_WEBSITE, ADD_DESC, ADD_NOTES = range(6)
ADD_LOG_CONTENT = 6

# Writer Registration States
REG_EMAIL, REG_WHATSAPP, REG_X, REG_OPAY = range(10, 14)


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
            if writer_id == user_handle and (status == "admin" or status == "active"):
                # Checking if designated admin role exists in sheet
                pass
    except Exception as e:
        logger.error(f"Error checking admin status: {e}")
    return user_handle in ADMIN_TELEGRAM_HANDLES


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
            "• `/ranks` - View global writer leaderboard & rankings\n"
            "• `/mybrands` - View assigned brands\n"
            "• `/cancel` - Cancel current action"
        )
    else:
        msg = (
            f"📝 *Welcome Writer ({handle})*\n\n"
            "Get Started:\n"
            "• `/register` - Complete your writer profile & onboarding\n"
            "• `/mybrands` - View your strictly assigned brands & submit logs\n"
            "• `/ranks` - Check your leaderboard rank & points\n"
            "• `/cancel` - Cancel current action"
        )
    await update.message.reply_text(msg, parse_mode="Markdown")


# --- WRITER SELF-REGISTRATION CONVERSATION ---
async def start_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    handle = get_user_handle(update)
    
    # Check if already registered
    writers = await asyncio.to_thread(writers_sheet.get_all_records)
    for w in writers:
        w_id = str(w.get("Writer Telegram ID") or "").strip()
        if w_id.lower() == handle.lower():
            await update.message.reply_text("✅ You are already registered in the CRM system! Use `/mybrands` to check your brands.", parse_mode="Markdown")
            return ConversationHandler.END

    await update.message.reply_text(
        "📝 *Writer Self-Registration*\n\nLet's get you set up! Please enter your **Email Address**:",
        parse_mode="Markdown"
    )
    return REG_EMAIL


async def reg_email(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["reg_email"] = update.message.text.strip()
    await update.message.reply_text("Great! Now enter your **WhatsApp Number** (with country code, e.g., +234...):", parse_mode="Markdown")
    return REG_WHATSAPP


async def reg_whatsapp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["reg_whatsapp"] = update.message.text.strip()
    await update.message.reply_text("Enter your **X (Twitter) Handle** (e.g., @username):", parse_mode="Markdown")
    return REG_X


async def reg_x(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["reg_x"] = update.message.text.strip()
    await update.message.reply_text(
        "Almost done! Enter your **Opay Account Number & Name**\n*(Example: `7085946786 John Richard`)*:",
        parse_mode="Markdown"
    )
    return REG_OPAY


async def reg_opay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    opay_info = update.message.text.strip()
    u_data = context.user_data
    handle = get_user_handle(update)
    writer_name = update.effective_user.full_name or handle

    # Mapping to your exact columns:
    # [Writer Telegram ID, Writer Name, Email, WhatsApp, X Handle, Opay, Assigned Brands Count, Max Capacity, Status, Points]
    new_row = [
        handle,
        writer_name,
        u_data["reg_email"],
        u_data["reg_whatsapp"],
        u_data["reg_x"],
        opay_info,
        0,       # Assigned Brands Count (Starts at 0)
        5,       # Max Capacity
        "Active",# Status
        0        # Points (Starts at 0 for leaderboard)
    ]

    await asyncio.to_thread(writers_sheet.append_row, new_row)

    await update.message.reply_text(
        f"🎉 *Registration Successful!*\n\n"
        f"👤 *Handle:* `{handle}`\n"
        f"📧 *Email:* `{u_data['reg_email']}`\n"
        f"💬 *WhatsApp:* `{u_data['reg_whatsapp']}`\n"
        f"🐦 *X Handle:* `{u_data['reg_x']}`\n"
        f"🏦 *Opay:* `{opay_info}`\n\n"
        f"You are all set! Use `/mybrands` to view your brands or `/ranks` to check standings.",
        parse_mode="Markdown"
    )
    context.user_data.clear()
    return ConversationHandler.END
    



# --- LEADERBOARD & RANKS (`/ranks`) ---
async def show_ranks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    handle = get_user_handle(update)
    
    try:
        writers = await asyncio.to_thread(writers_sheet.get_all_records)
        if not writers:
            await update.message.reply_text("📊 No registered writers found on the leaderboard yet.")
            return

        # Sort writers by points (descending). Default points to 0 if missing.
        sorted_writers = sorted(
            writers,
            key=lambda x: float(str(x.get("Points", 0)).strip() or 0),
            reverse=True
        )

        user_rank = None
        user_points = 0
        leaderboard_text = "🏆 *Writer Leaderboard & Rankings*\n\n"

        for idx, w in enumerate(sorted_writers[:10], start=1):
            w_id = str(w.get("Writer Telegram ID") or "").strip()
            w_name = str(w.get("Writer Name") or w_id).strip()
            pts = w.get("Points", 0)
            
            medal = "🥇" if idx == 1 else "🥈" if idx == 2 else "🥉" if idx == 3 else f"`#{idx}`"
            leaderboard_text += f"{medal} *{w_name}* (`{w_id}`) — *{pts} pts*\n"

            if w_id.lower() == handle.lower():
                user_rank = idx
                user_points = pts

        # Find user position if outside top 10
        if not user_rank:
            for idx, w in enumerate(sorted_writers, start=1):
                w_id = str(w.get("Writer Telegram ID") or "").strip()
                if w_id.lower() == handle.lower():
                    user_rank = idx
                    user_points = w.get("Points", 0)
                    break

        if user_rank:
            leaderboard_text += f"\n---------------------------\n👤 *Your Standing:* `#{user_rank}` with *{user_points} pts*"
        else:
            leaderboard_text += f"\n---------------------------\n⚠️ You are not registered yet. Use `/register` to join!"

        await update.message.reply_text(leaderboard_text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Error fetching ranks: {e}")
        await update.message.reply_text(f"❌ Error loading leaderboard: {str(e)}")


# --- STRICT WRITER VIEW (Only assigned brands) ---
async def my_brands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    handle = get_user_handle(update)
    brands = await asyncio.to_thread(get_writer_brands, handle)

    if not brands:
        await update.message.reply_text(
            f"⚠️ You currently have **0 brands** assigned to your handle (`{handle}`). Please use `/register` if you haven't, or contact the admin.",
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
        b_x = brand.get("X Handle (URL)") or brand.get("X Handle") or brand.get("X")
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

    # Self-Registration Conversation Handler
    reg_conv = ConversationHandler(
        entry_points=[CommandHandler("register", start_registration)],
        states={
            REG_EMAIL: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_email)],
            REG_WHATSAPP: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_whatsapp)],
            REG_X: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_x)],
            REG_OPAY: [MessageHandler(filters.TEXT & ~filters.COMMAND, reg_opay)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

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
    app.add_handler(CommandHandler("ranks", show_ranks))
    app.add_handler(reg_conv)
    app.add_handler(add_brand_conv)
    app.add_handler(add_log_conv)

    print("🤖 Telegram CRM Bot started running...")
    
    # Start dummy web server in background thread for Render
    threading.Thread(target=run_dummy_server, daemon=True).start()
    
    app.run_polling()


if __name__ == "__main__":
    main()
