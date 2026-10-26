import telebot
import os
import threading
import re
import asyncio
from flask import Flask, request # <-- BU YERDA 'request' QO'SHILDI
from telebot import types
from apify_client import ApifyClient

# --- SOZLAMALAR ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
APIFY_TOKEN = os.environ.get("APIFY_TOKEN")
bot = telebot.TeleBot(BOT_TOKEN)

# --- FLASK SERVER (Webhook uchun) ---
app = Flask(__name__)

@app.route(f'/{BOT_TOKEN}', methods=['POST'])
def receive_update():
    """Telegram'dan kelgan xabarlarni qabul qiluvchi yo'l."""
    json_string = request.get_data().decode('utf-8')
    update = telebot.types.Update.de_json(json_string)
    bot.process_new_updates([update])
    return "OK", 200

@app.route('/')
def home():
    return "OLX Apify Bot is alive!", 200

# --- APIFY ORQALI OLX QIDIRUVI ---
def search_olx_apartments_apify(max_price_usd, chat_id):
    try:
        client = ApifyClient(APIFY_TOKEN)
        
        # Skraper uchun sozlamalar (input)
        run_input = {
            "searchQuery": "kvartira sotiladi",
            "olxDomain": "olx.uz",
            "maxPages": 1,
            "sortBy": "newest",
            "proxyConfiguration": { "useApifyProxy": True }
        }
        
        print("Apify skraperi ishga tushirilmoqda...")
        run = client.actor("daddyapi/olx-search-scraper").call(run_input=run_input)
        
        dataset_items = client.dataset(run.default_dataset_id).list_items().items
        
        results = []
        for item in dataset_items:
            title = item.get("title", "Noma'lum uy")
            price_text = item.get("price", "0")
            location = item.get("location", "Manzil yo'q")
            link = item.get("url", "")
            
            price_text_lower = str(price_text).lower()
            digits = re.sub(r'[^\d]', '', str(price_text))
            price_num = int(digits) if digits else 0
            
            if ("сум" in price_text_lower or "so'm" in price_text_lower or "uzs" in price_text_lower) and price_num > 1000000:
                price_in_usd = int(price_num / 12600)
            else:
                price_in_usd = price_num

            if 0 < price_in_usd <= max_price_usd:
                results.append({
                    "title": title,
                    "price": price_text,
                    "location": location,
                    "link": link
                })
        
        return results[:5]
        
    except Exception as e:
        print(f"Apify xatosi: {e}")
        bot.send_message(chat_id, f"⚠️ Apify xatosi: {e}")
        return []

# --- BOT LOGIKASI ---
user_states = {}

@bot.message_handler(commands=['start'])
def start_handler(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    btn = types.KeyboardButton("🏠 Kvartira qidirish")
    markup.add(btn)
    bot.send_message(message.chat.id, "Salom! Men OLX'dan uylarni topuvchi botman. 🕵️‍♂️\nBoshlash uchun tugmani bosing.", reply_markup=markup)

@bot.message_handler(func=lambda message: message.text == "🏠 Kvartira qidirish")
def ask_price(message):
    user_states[message.chat.id] = {'state': 'waiting_for_price'}
    msg = bot.send_message(message.chat.id, "Byudjetingizni dollarda kiriting (masalan: 50000):")
    bot.register_next_step_handler(msg, process_search)

def process_search(message):
    user_id = message.chat.id
    try:
        max_price = int(message.text)
        loading_msg = bot.reply_to(message, "🔎 Apify orqali OLX tekshirilmoqda, biroz kuting (10-15 soniya)...")
        
        results = search_olx_apartments_apify(max_price, user_id)
        
        bot.delete_message(user_id, loading_msg.message_id)
        
        if not results:
            bot.send_message(user_id, "Uy topilmadi yoki byudjetga mos keladigan e'lon yo'q.")
            return
            
        bot.send_message(user_id, f"🎉 {max_price}$ ga mos top 5 ta uy:")
        for res in results:
            text = f"🏠 **{res['title']}**\n💰 {res['price']}\n📍 {res['location']}\n🔗 [Ko'rish]({res['link']})"
            bot.send_message(user_id, text, parse_mode='Markdown', disable_web_page_preview=True)
    except Exception as e:
        bot.send_message(user_id, "Xatolik yuz berdi. Iltimos, faqat raqam kiriting.")
        print(f"Xatolik: {e}")

# --- ISHGA TUSHIRISH ---
if __name__ == "__main__":
    render_url = os.environ.get("RENDER_EXTERNAL_URL")
    if render_url:
        bot.remove_webhook()
        bot.set_webhook(url=f"{render_url}/{BOT_TOKEN}")
        print(f"Webhook o'rnatildi: {render_url}/{BOT_TOKEN}")
    
    print("Server ishga tushdi...")
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
