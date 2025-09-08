import json
import logging
import mysql.connector
import re
import asyncio
from datetime import datetime
from dateparser import parse as parse_date
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
import ollama

# === CONFIG ===
BOT_TOKEN = "8490428005:AAGuuUQajHSMqzKEMI7gkdTeaoR1o3Xv5Bw"
DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "Jay@14111",
    "database": "telegrambot",
}
PUBLIC_BASE_URL = "https://81113dea1ef3.ngrok-free.app"

# === LOGGING ===
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_connection():
    return mysql.connector.connect(**DB_CONFIG)


def register_user(username):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT IGNORE INTO users (username) VALUES (%s)",
        (username,),
    )
    conn.commit()
    rowcount = cursor.rowcount
    cursor.close()
    conn.close()
    return rowcount == 1

def insert_expense(username, amount, category, description):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO expenses (username, amount, category, description, date) VALUES (%s,%s,%s,%s,NOW())",
        (username, amount, category, description),
    )
    conn.commit()
    expense_id = cursor.lastrowid
    cursor.close()
    conn.close()
    return expense_id

# === Auto-categorization ===
def auto_categorize(note):
    note_lower = note.lower()
    if any(w in note_lower for w in ["coffee", "food", "lunch"]):
        return "Food"
    elif any(w in note_lower for w in ["uber", "bus", "taxi"]):
        return "Transport"
    elif any(w in note_lower for w in ["movie", "netflix"]):
        return "Entertainment"
    else:
        return "Misc"


INSTRUCTION_PROMPT = """
You are a strict JSON extractor. 

Read the given paragraph describing expenses and return ONLY a JSON array.  

Each object must have:
- "amount": numeric value (float, no currency symbol)
- "description": clean item name or short note (capitalize each word)

Rules:
- If there are multiple expenses, split them into separate objects.
- Do not include other fields or text outside the JSON array.
- Return ONLY a valid single JSON array with proper syntax having multiple expenses as array elements.
- Make sure to return JSON array which is parseable

Examples:

Input:
"On my cousin’s birthday, I spent ₹2500 on a cake, ₹4000 on gifts, ₹1500 for dinner at a restaurant, and ₹700 on decoration."

Output:
[
  {{"amount": 2500, "description": "Cake"}},
  {{"amount": 4000, "description": "Gifts"}},
  {{"amount": 1500, "description": "Dinner"}},
  {{"amount": 700, "description": "Decoration"}}
]

Input 2:
"Icecream 200"

Output 2:
[
  {{"amount": 200, "description": "Icecream"}}
]

Input 3:
"dmart 1200,bike service 800,movie 450 "

Output 3:
[
  {{"amount": 1200, "description": "dmart"}},
  {{"amount": 800, "description": "bike service"}},
  {{"amount": 450, "description": "movie"}}
]

Now process the following paragraph:
{user_text}
"""


def ask_qwen_sync(prompt):
    response = ollama.generate(model="qwen", prompt=prompt, stream=False)
    return response["response"].strip()

async def ask_qwen(prompt):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, ask_qwen_sync, prompt)


# === Telegram Commands ===

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.message.from_user.username
    if not username:
        await update.message.reply_text(
            "⚠️ Please set a Telegram username in your profile before using this bot."
        )
        return

    new_registration = register_user(username)
    if new_registration:
        await update.message.reply_text("✅ You are registered!")
    else:
        await update.message.reply_text("ℹ️ You are already registered.")

    await update.message.reply_text(
        """👋 Welcome to Expense Tracker Bot !

Available commands:
/add <amount> <category> <note> - Add expense
/summary [today|month|year|<start> <end>] - View summaries
/view [date] - View daily expenses
/delete <id> - Delete entry
/my_expenses_link
"""
    )



async def unified_expense_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    username = update.message.from_user.username
    if not username:
        await update.message.reply_text("⚠️ Please set a Telegram username in your profile.")
        return

    register_user(username)

    # Handle direct /add command
    if text.lower().startswith("/add"):
        parts = text.split(maxsplit=3)
        if len(parts) < 3:
            await update.message.reply_text("❌ Usage: /add <amount> <category> <note>")
            return
        try:
            amount = float(parts[1])
            category = parts[2]
            note = parts[3] if len(parts) > 3 else ""
            expense_id = insert_expense(username, amount, category, note)
            await update.message.reply_text(
                f"✅ Added expense: ₹{amount} | {category} | {note or '-'} (ID: {expense_id})"
            )
            return
        except Exception:
            await update.message.reply_text("❌ Invalid amount.")
            return

    prompt = INSTRUCTION_PROMPT.format(user_text=text)
    await update.message.reply_text("⏳ Processing your expenses...")

    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            response_text = await ask_qwen(prompt)
            print(response_text)
            start = response_text.find("[")
            end = response_text.rfind("]")
            json_str = response_text[start : end + 1]
            expenses = json.loads(json_str)
            if not expenses or not isinstance(expenses, list):
                raise ValueError("No valid expenses found")
            break  # Success, break the retry loop
        except Exception as e:
            logger.error(f"Qwen parsing attempt {attempt} failed: {e} | Raw response: {locals().get('response_text','')}")
            expenses = None
            if attempt == max_attempts:
                await update.message.reply_text("❌ Could not parse expenses after 3 attempts. Please try rephrasing your input.")
                return
            # Optionally: await update.message.reply_text(f"Retry {attempt}/3...")

    # Insert into DB
    added_expenses = []
    for exp in expenses:
        try:
            amount = float(exp.get("amount", 0))
            description = exp.get("description", "").strip().title()
            if amount <= 0 or not description:
                continue  # skip invalid objects
            category = auto_categorize(description)
            expense_id = insert_expense(username, amount, category, description)
            added_expenses.append(
                f"ID:{expense_id} ₹{amount:.2f} | {category} | {description}"
            )
        except Exception as e:
            logger.warning(f"Skipping invalid expense: {exp} | {e}")
            continue

    if added_expenses:
        profile_url = f"{PUBLIC_BASE_URL}/user/{username}"
        msg_lines = ["✅ Added the following expenses:"] + added_expenses  + [f"\n🔗 [View all your expenses]({profile_url})"]
        await update.message.reply_text("\n".join(msg_lines), parse_mode="Markdown")
    else:
        await update.message.reply_text("⚠️ No expenses were added. Please check your input.")



async def delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        username = update.message.from_user.username
        if not username:
            await update.message.reply_text(
                "⚠️ Please set a Telegram username in your profile before deleting expenses."
            )
            return

        if not context.args:
            await update.message.reply_text("❌ Usage: /delete <expense_id>")
            return

        exp_id = context.args[0]

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM expenses WHERE id=%s AND username=%s", (exp_id, username)
        )
        conn.commit()
        rows_deleted = cursor.rowcount
        cursor.close()
        conn.close()

        if rows_deleted:
            await update.message.reply_text("✅ Expense deleted.")
        else:
            await update.message.reply_text("❌ Expense not found.")
    except Exception as e:
        logger.error(e)
        await update.message.reply_text("❌ Could not delete expense.")

async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        username = update.message.from_user.username
        if not username:
            await update.message.reply_text(
                "⚠️ Please set a Telegram username in your profile before checking summary."
            )
            return

        args = context.args
        now = datetime.now()

        conn = get_connection()
        cursor = conn.cursor(dictionary=True)

        if not args:
            # No arguments: today's expenses
            today_str = now.strftime("%Y-%m-%d")
            cursor.execute(
                "SELECT * FROM expenses WHERE username = %s AND DATE(date) = %s",
                (username, today_str),
            )

        elif len(args) == 1:
            arg = args[0].lower()
            if arg == "month":
                month_str = now.strftime("%Y-%m")
                cursor.execute(
                    "SELECT * FROM expenses WHERE username = %s AND DATE_FORMAT(date,'%%Y-%%m') = %s",
                    (username, month_str),
                )
            elif arg == "year":
                year_str = now.strftime("%Y")
                cursor.execute(
                    "SELECT * FROM expenses WHERE username = %s AND YEAR(date) = %s",
                    (username, year_str),
                )
            elif arg.isdigit():
                if len(arg) == 1 or len(arg) == 2:  # treat as month number for current year
                    month_num = int(arg)
                    if 1 <= month_num <= 12:
                        month_str = f"{now.year}-{month_num:02d}"
                        cursor.execute(
                            "SELECT * FROM expenses WHERE username = %s AND DATE_FORMAT(date,'%%Y-%%m') = %s",
                            (username, month_str),
                        )
                    else:
                        await update.message.reply_text("❌ Invalid month number. Use 1-12.")
                        cursor.close()
                        conn.close()
                        return

                elif len(arg) == 4:  # treat as year
                    year_num = int(arg)
                    if 1900 <= year_num <= now.year:  # reasonable year check
                        cursor.execute(
                            "SELECT * FROM expenses WHERE username = %s AND YEAR(date) = %s",
                            (username, year_num),
                        )
                    else:
                        await update.message.reply_text("❌ Invalid year.")
                        cursor.close()
                        conn.close()
                        return
                else:
                    await update.message.reply_text(
                        "❌ Invalid argument. Use /summary, /summary month, /summary year, or /summary <start> <end>"
                    )
                    cursor.close()
                    conn.close()
                    return
            else:
                await update.message.reply_text(
                    "❌ Invalid argument. Use /summary, /summary month, /summary year, or /summary <start> <end>"
                )
                cursor.close()
                conn.close()
                return

        elif len(args) == 2:
            start_date = parse_date(args[0])
            end_date = parse_date(args[1])
            if not start_date or not end_date:
                await update.message.reply_text(
                    "❌ Could not parse the dates. Use format: YYYY-MM-DD"
                )
                cursor.close()
                conn.close()
                return
            cursor.execute(
                "SELECT * FROM expenses WHERE username = %s AND date BETWEEN %s AND %s",
                (username, start_date, end_date),
            )
        else:
            await update.message.reply_text("❌ Too many arguments")
            cursor.close()
            conn.close()
            return

        expenses = cursor.fetchall()
        cursor.close()
        conn.close()

        if not expenses:
            await update.message.reply_text("📭 No expenses found for the specified period.")
            return

        total = sum(float(e["amount"]) for e in expenses)
        msg = f"💰 Total expenses: ₹{total:.2f}\n\n"
        for e in expenses:
            msg += f"{e['id']}: {e['category']} ₹{e['amount']} ({e['description']}) on {e['date']}\n"
        await update.message.reply_text(msg)

    except Exception as e:
        logger.error(e)
        await update.message.reply_text("❌ Error fetching summary.")



async def view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        username = update.message.from_user.username
        if not username:
            await update.message.reply_text(
                "⚠️ Please set a Telegram username in your profile before viewing expenses."
            )
            return

        args = context.args
        date_str = (
            datetime.now().strftime("%Y-%m-%d")
            if not args
            else parse_date(" ".join(args)).strftime("%Y-%m-%d")
        )

        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM expenses WHERE username = %s AND DATE(date) = %s",
            (username, date_str),
        )
        expenses = cursor.fetchall()
        cursor.close()
        conn.close()

        if not expenses:
            await update.message.reply_text(f"No expenses found for {date_str}.")
            return

        msg = f"📅 Expenses for {date_str}:\n\n"
        for idx, e in enumerate(expenses, start=1):
            msg += f"{idx}. {e['category']}: ₹{e['amount']} ({e['description']}) [ID:{e['id']}]\n"
        await update.message.reply_text(msg)
    except Exception as e:
        logger.error(e)
        await update.message.reply_text("❌ Could not fetch daily expenses.")



async def view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        username = update.message.from_user.username
        if not username:
            await update.message.reply_text(
                "⚠️ Please set a Telegram username in your profile before viewing expenses."
            )
            return

        args = context.args
        date_str = (
            datetime.now().strftime("%Y-%m-%d")
            if not args
            else parse_date(" ".join(args)).strftime("%Y-%m-%d")
        )

        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM expenses WHERE username = %s AND DATE(date) = %s",
            (username, date_str),
        )
        expenses = cursor.fetchall()
        cursor.close()
        conn.close()

        if not expenses:
            await update.message.reply_text(f"No expenses found for {date_str}.")
            return

        msg = f"📅 Expenses for {date_str}:\n\n"
        for idx, e in enumerate(expenses, start=1):
            msg += f"{idx}. {e['category']}: ₹{e['amount']} ({e['description']}) [ID:{e['id']}]\n"
        await update.message.reply_text(msg)
    except Exception as e:
        logger.error(e)
        await update.message.reply_text("❌ Could not fetch daily expenses.")

async def my_expenses_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.message.from_user.username
    if not username:
        await update.message.reply_text(
            "⚠️ Please set your Telegram username in your profile to get your expense report link."
        )
        return

    profile_url = f"{PUBLIC_BASE_URL}/user/{username}"

    keyboard = [[InlineKeyboardButton("View My Expenses", url=profile_url)]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "🔗 Click the button below to view your expenses on the web:", reply_markup=reply_markup
    )


# === Bot Setup ===
app = ApplicationBuilder().token(BOT_TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("summary", summary))
app.add_handler(CommandHandler("view", view))
app.add_handler(CommandHandler("delete", delete))
app.add_handler(CommandHandler("my_expenses_link", my_expenses_link))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unified_expense_handler))


print("🚀 Bot is running...")
app.run_polling()