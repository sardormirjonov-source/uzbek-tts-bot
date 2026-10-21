import telebot
import requests
import os
import uuid
import json
import threading
import sqlite3
from datetime import date
from flask import Flask, request, jsonify
from paytechuz.gateways.payme import PaymeGateway
from paytechuz.gateways.click import ClickGateway

# --- SOZLAMALAR (Render Environment Variables) ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
AISHA_API_KEY = os.environ.get("AISHA_API_KEY")
AISHA_TTS_URL = "https://back.aisha.group/api/v1/tts/post/"

# To'lov kalitlari (Hozircha test rejimi uchun)
PAYME_ID = os.environ.get("PAYME_ID")
PAYME_KEY = os.environ.get("PAYME_KEY")
CLICK_SERVICE_ID = os.environ.get("CLICK_SERVICE_ID")
CLICK_MERCHANT_ID = os.environ.get("CLICK_MERCHANT_ID")
CLICK_MERCHANT_USER_ID = os.environ.get("CLICK_MERCHANT_USER_ID")
CLICK_SECRET_KEY = os.environ.get("CLICK_SECRET_KEY")

IS_TEST_MODE = True  # Test rejimi yoqilgan

# --- LIMITLAR ---
MAX_BELGI_PER_MSG = 150    # Bitta xabar uchun
KUNLIK_BELGI_LIMIT = 300   # Bepul kunlik limit

# Bot va Flask
bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# To'lov shlyuzlarini ishga tushirish (Agar kalitlar kiritilgan bo'lsa)
payme = PaymeGateway(payme_id=PAYME_ID, payme_key=PAYME_KEY, is_test_mode=IS_TEST_MODE) if PAYME_ID else None
click = ClickGateway(
    service_id=CLICK_SERVICE_ID,
    merchant_id=CLICK_MERCHANT_ID,
    merchant_user_id=CLICK_MERCHANT_USER_ID,
    secret_key=CLICK_SECRET_KEY,
    is_test_mode=IS_TEST_MODE,
) if CLICK_SERVICE_ID else None

# --- MA'LUMOTLAR BAZASI ---
def init_db():
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            last_date TEXT,
            used_chars INTEGER,
            balance INTEGER DEFAULT 0
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS payments (
            order_id TEXT PRIMARY KEY,
            user_id INTEGER,
            amount INTEGER,
            status TEXT,
            provider TEXT
        )
    ''')
    conn.commit()
    conn.close()

def get_user(user_id):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute("SELECT last_date, used_chars, balance FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {"last_date": row[0], "used_chars": row[1], "balance": row[2]}
    return None

def update_user(user_id, last_date, used_chars, balance):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR REPLACE INTO users (user_id, last_date, used_chars, balance) VALUES (?, ?, ?, ?)",
        (user_id, last_date, used_chars, balance)
    )
    conn.commit()
    conn.close()

def check_limit(user_id, text_length):
    today = str(date.today())
    user = get_user(user_id)
    if user is None:
        update_user(user_id, today, text_length, 0)
        return True, KUNLIK_BELGI_LIMIT - text_length
    if user["last_date"] != today:
        update_user(user_id, today, text_length, user["balance"])
        return True, KUNLIK_BELGI_LIMIT - text_length
    if user["used_chars"] + text_length <= KUNLIK_BELGI_LIMIT:
        update_user(user_id, today, user["used_chars"] + text_length, user["balance"])
        return True, KUNLIK_BELGI_LIMIT - (user["used_chars"] + text_length)
    if user["balance"] >= text_length:
        update_user(user_id, today, user["used_chars"], user["balance"] - text_length)
        return True, 0
    return False, 0

def add_balance(user_id, amount):
    user = get_user(user_id)
    if user:
        update_user(user_id, user["last_date"], user["used_chars"], user["balance"] + amount)
    else:
        update_user(user_id, str(date.today()), 0, amount)

def create_payment_order(user_id, amount, provider):
    order_id = str(uuid.uuid4())[:8] # Qisqaroq ID
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute("INSERT INTO payments (order_id, user_id, amount, status, provider) VALUES (?, ?, ?, ?, ?)",
                   (order_id, user_id, amount, "pending", provider))
    conn.commit()
    conn.close()
    return order_id

def get_order(order_id):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, amount, status, provider FROM payments WHERE order_id = ?", (order_id,))
    row = cursor.fetchone()
    conn.close()
    return row

def update_order_status(order_id, status):
    conn = sqlite3.connect('bot_data.db')
    cursor = conn.cursor()
    cursor.execute("UPDATE payments SET status = ? WHERE order_id = ?", (status, order_id))
    conn.commit()
    conn.close()

init_db()

# --- FLASK SERVER VA WEBHOOK'LAR ---
@app.route('/')
def home():
    return "Bot is alive!", 200

@app.route('/payme/webhook', methods=['POST'])
def payme_webhook():
    data = request.get_json()
    method = data.get("method")
    params = data.get("params", {})
    order_id = params.get("account", {}).get("order_id") or params.get("account", {}).get("id")

    if method == "CheckPerformTransaction":
        order = get_order(order_id)
        if order and order[2] == "pending":
            return jsonify({"result": {"allow": True}})
        return jsonify({"error": {"code": -31050, "message": "Order not found"}})

    if method == "CreateTransaction":
        order = get_order(order_id)
        if order and order[2] == "pending":
            update_order_status(order_id, "created")
            return jsonify({"result": {"create_time": 0, "transaction": order_id, "state": 1}})
        return jsonify({"error": {"code": -31050, "message": "Order not found"}})

    if method == "PerformTransaction":
        order = get_order(order_id)
        if order and order[2] == "created":
            update_order_status(order_id, "performed")
            add_balance(order[0], order[1])
            try:
                bot.send_message(order[0], f"✅ To'lov muvaffaqiyatli! Balansingizga {order[1]} so'm qo'shildi.")
            except: pass
            return jsonify({"result": {"perform_time": 0, "transaction": order_id, "state": 2}})
        return jsonify({"error": {"code": -31050, "message": "Order not found"}})

    return jsonify({"error": {"code": -32601, "message": "Method not found"}})

@app.route('/click/webhook', methods=['POST'])
def click_webhook():
    data = request.form.to_dict() # Click form-data yuboradi
    action = data.get("action")
    order_id = data.get("merchant_trans_id")

    if action == "0":  # Prepare
        order = get_order(order_id)
        if order and order[2] == "pending":
            return jsonify({"error": 0, "error_note": "Success"})
        return jsonify({"error": -1, "error_note": "Order not found"})

    if action == "1":  # Complete
        order = get_order(order_id)
        if order and order[2] == "pending":
            update_order_status(order_id, "completed")
            add_balance(order[0], order[1])
            try:
                bot.send_message(order[0], f"✅ To'lov muvaffaqiyatli! Balansingizga {order[1]} so'm qo'shildi.")
            except: pass
            return jsonify({"error": 0, "error_note": "Success"})
        return jsonify({"error": -1, "error_note": "Order not found"})

    return jsonify({"error": -1, "error_note": "Unknown action"})

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, use_reloader=False)

# --- BOT LOGIKASI ---
@bot.message_handler(commands=['start'])
def start_handler(message):
    bot.reply_to(
        message,
        f"Salom! Menga matn yuboring, men uni Aisha AI orqali ovozga aylantiraman. 🎙️\n\n"
        f"📌 **Qoidalar:**\n"
        f"• Bitta xabar uchun: {MAX_BELGI_PER_MSG} belgi\n"
        f"• Kunlik bepul limit: {KUNLIK_BELGI_LIMIT} belgi\n\n"
        f"Balansni to'ldirish uchun /balance buyrug'ini bosing."
    )

@bot.message_handler(commands=['balance'])
def balance_handler(message):
    user_id = message.from_user.id
    user = get_user(user_id)
    balans = user["balance"] if user else 0
    markup = telebot.types.InlineKeyboardMarkup()
    
    if payme:
        markup.add(telebot.types.InlineKeyboardButton("Payme (Test)", callback_data="pay_payme"))
    if click:
        markup.add(telebot.types.InlineKeyboardButton("Click (Test)", callback_data="pay_click"))
        
    bot.reply_to(message, f"💰 Joriy balansingiz: {balans} so'm\n\nTo'lov usulini tanlang:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("pay_"))
def payment_callback(call):
    user_id = call.from_user.id
    provider = call.data.split("_")[1]
    amount = 5000  # Test uchun 5000 so'm
    order_id = create_payment_order(user_id, amount, provider)

    try:
        if provider == "payme" and payme:
            link = payme.create_payment(
                id=order_id,
                amount=amount,
                return_url=f"https://t.me/{bot.get_me().username}",
                account_field_name="order_id",
            )
        elif provider == "click" and click:
            link = click.create_payment(
                id=order_id,
                amount=amount,
                description=f"Balans to'ldirish",
                return_url=f"https://t.me/{bot.get_me().username}",
            )
        else:
            bot.answer_callback_query(call.id, "To'lov tizimi sozlanmagan.")
            return

        bot.send_message(user_id, f"To'lov havolasi:\n{link}\n\nTo'lovni amalga oshirgach, balansingiz avtomatik yangilanadi.")
    except Exception as e:
        print(f"To'lov yaratishda xatolik: {e}")
        bot.send_message(user_id, "To'lov havolasini yaratishda xatolik yuz berdi.")

@bot.message_handler(func=lambda message: True, content_types=['text'])
def text_handler(message):
    user_id = message.from_user.id
    matn = message.text.strip()
    if not matn:
        return

    if len(matn) > MAX_BELGI_PER_MSG:
        bot.reply_to(message, f"⚠️ Kechirasiz, bitta xabarda maksimal {MAX_BELGI_PER_MSG} belgi yuborishingiz mumkin.")
        return

    ruxsat, qolgan_belgi = check_limit(user_id, len(matn))
    if not ruxsat:
        bot.reply_to(message, "Kechirasiz, bugungi bepul limitingiz tugadi. /balance orqali balansni to'ldiring.")
        return

    vaqtinchalik_fayl = f"{uuid.uuid4()}.mp3"
    yuklanmoqda = bot.reply_to(message, f"Ovoz yaratilmoqda... ⏳\n(Qolgan bepul limit: {qolgan_belgi} belgi)")

    try:
        headers = {"X-Api-Key": AISHA_API_KEY, "Accept-Language": "uz"}
        files = {
            'transcript': (None, matn),
            'language': (None, 'uz'),
            'model': (None, 'Gulnoza'),
            'mood': (None, 'Neutral'),
            'speed': (None, '1.0')
        }
        response = requests.post(AISHA_TTS_URL, files=files, headers=headers, timeout=20)
        response.raise_for_status()
        javob_json = response.json()
        audio_url = javob_json.get('audio_path') or javob_json.get('url') or javob_json.get('audio_url') or javob_json.get('result')
        
        if isinstance(audio_url, dict):
            audio_url = audio_url.get('url') or audio_url.get('audio_path')
        if not audio_url:
            bot.reply_to(message, "API dan audio havolasi topilmadi.")
            return
            
        if audio_url.startswith('/'):
            audio_url = "https://back.aisha.group" + audio_url
        elif not audio_url.startswith('http'):
            audio_url = "https://back.aisha.group/media/" + audio_url
            
        audio_response = requests.get(audio_url, timeout=20)
        audio_response.raise_for_status()
        
        with open(vaqtinchalik_fayl, 'wb') as f:
            f.write(audio_response.content)
            
        with open(vaqtinchalik_fayl, 'rb') as audio:
            bot.send_audio(message.chat.id, audio, title="Aisha AI (Gulnoza)")
            
    except Exception as xato:
        print(f"Xatolik: {xato}")
        bot.reply_to(message, "Kechirasiz, texnik xatolik yuz berdi.")
    finally:
        if os.path.exists(vaqtinchalik_fayl):
            os.remove(vaqtinchalik_fayl)
        bot.delete_message(yuklanmoqda.chat.id, yuklanmoqda.message_id)

# --- ISHGA TUSHIRISH ---
if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    print("Bot ishga tushdi...")
    try:
        requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook?drop_pending_updates=true")
    except:
        pass
    bot.infinity_polling()
