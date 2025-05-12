import asyncio
import sqlite3
from telegram import Update, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from datetime import datetime, timedelta
import pytz
import logging

# إعدادات التسجيل
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# بيانات الاعتماد
TELEGRAM_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
CHANNEL_ID = -1001234567890  # استبدال بمعرف قناتك الفعلي

# إعداد قاعدة البيانات
def init_db():
    conn = sqlite3.connect('subscriptions.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY, 
                 phone TEXT, 
                 subscription_code TEXT,
                 joined_at TEXT, 
                 expires_at TEXT, 
                 duration_days INTEGER)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS codes
                 (code TEXT PRIMARY KEY,
                 duration INTEGER,
                 used INTEGER DEFAULT 0)''')
    
    # إضافة الأكواد إذا لم تكن موجودة
    default_codes = [
        ("gg01bb", 3),
        ("gg02bb", 3),
        ("gg03bb", 3),
        ("rr01mm", 4),
        ("rr02mm", 4),
        ("rr03mm", 4),
        ("ll01jj", 5),
        ("ll02jj", 5),
    ]
    
    for code, duration in default_codes:
        try:
            c.execute("INSERT INTO codes VALUES (?, ?, 0)", (code, duration))
        except sqlite3.IntegrityError:
            pass
    
    conn.commit()
    conn.close()

init_db()

# وظائف مساعدة للتعامل مع قاعدة البيانات
def get_user(user_id):
    conn = sqlite3.connect('subscriptions.db')
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
    user = c.fetchone()
    conn.close()
    return user

def update_user(user_id, **kwargs):
    conn = sqlite3.connect('subscriptions.db')
    c = conn.cursor()
    
    if not get_user(user_id):
        # إنشاء مستخدم جديد
        columns = ['user_id'] + list(kwargs.keys())
        values = [user_id] + list(kwargs.values())
        placeholders = ','.join(['?']*len(values))
        c.execute(f"INSERT INTO users ({','.join(columns)}) VALUES ({placeholders})", values)
    else:
        # تحديث مستخدم موجود
        set_clause = ','.join([f"{k}=?" for k in kwargs.keys()])
        values = list(kwargs.values()) + [user_id]
        c.execute(f"UPDATE users SET {set_clause} WHERE user_id=?", values)
    
    conn.commit()
    conn.close()

def check_code(code):
    conn = sqlite3.connect('subscriptions.db')
    c = conn.cursor()
    c.execute("SELECT duration FROM codes WHERE code=? AND used=0", (code,))
    result = c.fetchone()
    conn.close()
    return result[0] if result else None

def mark_code_used(code):
    conn = sqlite3.connect('subscriptions.db')
    c = conn.cursor()
    c.execute("UPDATE codes SET used=1 WHERE code=?", (code,))
    conn.commit()
    conn.close()

# معالجات الأوامر
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    keyboard = [[KeyboardButton("مشاركة رقم الهاتف", request_contact=True)]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    
    await update.message.reply_text(
        "مرحباً! للانضمام إلى قناة بوت ريتك، يرجى مشاركة رقم هاتفك:",
        reply_markup=reply_markup
    )
    
    # إضافة المستخدم إلى قاعدة البيانات إذا لم يكن موجوداً
    if not get_user(user_id):
        update_user(user_id, phone=None, subscription_code=None, 
                   joined_at=None, expires_at=None, duration_days=None)

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    phone_number = update.message.contact.phone_number
    
    update_user(user_id, phone=phone_number)
    
    await update.message.reply_text(
        "شكراً لمشاركة رقم هاتفك.\n"
        "للانضمام إلى قناة مجموعة بوت ريتك، ادخل كود الانضمام:",
        reply_markup=ReplyKeyboardRemove()
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip().lower()
    
    user = get_user(user_id)
    if not user or not user[1]:  # إذا لم يشارك رقم الهاتف
        await update.message.reply_text("يرجى البدء باستخدام الأمر /start أولاً")
        return
    
    duration = check_code(text)
    if duration:
        mark_code_used(text)
        
        now = datetime.now(pytz.utc)
        expires_at = now + timedelta(days=duration)
        
        update_user(user_id, 
                   subscription_code=text,
                   joined_at=now.isoformat(),
                   expires_at=expires_at.isoformat(),
                   duration_days=duration)
        
        try:
            # إنشاء رابط دعوة
            invite_link = await context.bot.create_chat_invite_link(
                chat_id=CHANNEL_ID,
                member_limit=1,
                expire_date=expires_at
            )
            
            await update.message.reply_text(
                f"✅ تم تفعيل اشتراكك بنجاح لمدة {duration} يوم!\n"
                f"📅 تاريخ الانتهاء: {expires_at.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"🔗 رابط الانضمام إلى القناة:\n{invite_link.invite_link}",
                parse_mode="HTML"
            )
            
            # جدولة التذكير والإزالة
            reminder_time = expires_at - timedelta(days=1)
            seconds_until_reminder = (reminder_time - now).total_seconds()
            
            if seconds_until_reminder > 0:
                context.job_queue.run_once(
                    send_reminder,
                    seconds_until_reminder,
                    chat_id=user_id,
                    data={"expires_at": expires_at}
                )
            
            seconds_until_expire = (expires_at - now).total_seconds()
            if seconds_until_expire > 0:
                context.job_queue.run_once(
                    remove_user,
                    seconds_until_expire,
                    chat_id=CHANNEL_ID,
                    user_id=user_id
                )
            
        except Exception as e:
            logger.error(f"Error in subscription activation: {e}")
            await update.message.reply_text(
                "❌ حدث خطأ أثناء تفعيل اشتراكك، يرجى التواصل مع المسؤول"
            )
    else:
        await update.message.reply_text("⚠️ كود غير صحيح أو مستخدم مسبقاً، يرجى المحاولة بكود آخر")

async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    expires_at = job.data["expires_at"]
    
    try:
        await context.bot.send_message(
            chat_id=job.chat_id,
            text=f"⏰ تذكير: اشتراكك في القناة سينتهي بعد 24 ساعة\n"
                 f"📅 تاريخ الانتهاء: {expires_at.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                 "للتجديد، يرجى التواصل مع المسؤول.",
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Error sending reminder: {e}")

async def remove_user(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    
    try:
        # إرسال رسالة للمستخدم
        await context.bot.send_message(
            chat_id=job.user_id,
            text="⏳ لقد انتهت فترة اشتراكك في القناة.\n"
                 "شكراً لك على وقتك معنا!\n\n"
                 "للتجديد، يرجى التواصل مع المسؤول.",
            parse_mode="HTML"
        )
        
        # محاولة إزالة المستخدم
        try:
            await context.bot.ban_chat_member(
                chat_id=job.chat_id,
                user_id=job.user_id
            )
            await asyncio.sleep(60)
            await context.bot.unban_chat_member(
                chat_id=job.chat_id,
                user_id=job.user_id
            )
        except Exception as e:
            logger.warning(f"Can't remove user: {e}")
        
        # حذف المستخدم من قاعدة البيانات
        conn = sqlite3.connect('subscriptions.db')
        c = conn.cursor()
        c.execute("DELETE FROM users WHERE user_id=?", (job.user_id,))
        conn.commit()
        conn.close()
            
    except Exception as e:
        logger.error(f"Error in user removal: {e}")

async def check_subscriptions(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(pytz.utc).isoformat()
    
    conn = sqlite3.connect('subscriptions.db')
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE expires_at < ?", (now,))
    expired_users = c.fetchall()
    
    for (user_id,) in expired_users:
        try:
            await context.bot.send_message(
                chat_id=user_id,
                text="⌛️ لقد انتهت فترة اشتراكك في القناة تلقائياً.\n"
                     "شكراً لك على وقتك معنا!",
                parse_mode="HTML"
            )
            
            try:
                await context.bot.ban_chat_member(
                    chat_id=CHANNEL_ID,
                    user_id=user_id
                )
                await asyncio.sleep(60)
                await context.bot.unban_chat_member(
                    chat_id=CHANNEL_ID,
                    user_id=user_id
                )
            except Exception as e:
                logger.warning(f"Can't remove user {user_id}: {e}")
            
            c.execute("DELETE FROM users WHERE user_id=?", (user_id,))
            conn.commit()
            
        except Exception as e:
            logger.error(f"Error checking subscription for {user_id}: {e}")
    
    conn.close()

def main():
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.CONTACT, handle_contact))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    if application.job_queue:
        application.job_queue.run_repeating(
            check_subscriptions,
            interval=3600,  # كل ساعة
            first=10
        )
    
    logger.info("✅ البوت يعمل الآن...")
    application.run_polling()

if __name__ == "__main__":
    main()
