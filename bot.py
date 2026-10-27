import telebot
import os
import re
import time
import sqlite3
import threading
from flask import Flask, request
from telebot import types
from apify_client import ApifyClient

# --- SOZLAMALAR ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
APIFY_TOKEN = os.environ.get("APIFY_TOKEN")

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# Tumanlar ro'yxati va ularning ruscha muqobillari (qidiruv uchun)
DISTRICT_MAP = {
    "Chilonzor": ["Chilonzor", "Чиланзар"],
    "Yunusobod": ["Yunusobod", "Юнусабад", "Юнусобод"],
    "Mirzo Ulug'bek": ["Mirzo Ulug'bek", "Мирзо Улугбек"],
    "Yakkasaroy": ["Yakkasaroy", "Яккасарай"],
    "Shayxontohur": ["Shayxontohur", "Шайхантахур", "Шайхонтохур"],
    "Uchtepa": ["Uchtepa", "Учтепа"],
    "Olmazor": ["Olmazor", "Алмазар"],
    "Sergeli": ["Sergeli", "Сергели"],
    "Bektemir": ["Bektemir", "Бектемир"],
    "Mirobod": ["Mirobod", "Мирабад"],
    "Yashnobod": ["Yashnobod", "Яшнабад"]
}

# --- MA'LUMOTLAR BAZASI (KESHLASH) ---
def init_db():
    conn = sqlite3.connect('olx_cache.db')
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS listings (
            id TEXT PRIMARY KEY,
            title TEXT,
            price_str TEXT,
            price_usd INTEGER,
            location TEXT,
            link TEXT,
            image TEXT
        )
    ''')
    conn.commit()
    conn.close()

def save_to_db(items):
    conn = sqlite3.connect('olx_cache.db')
    c = conn.cursor()
    c.execute('DELETE FROM listings')
    
    for item in items:
        title = item.get("title") or item.get("name") or "Noma'lum uy"
        location = item.get("location") or "Manzil ko'rsatilmagan"
        link = item.get("url") or item.get("link") or ""
        image = item.get("image") or item.get("photo") or None
        item_id = str(item.get("id") or link)
        
        price_raw = str(item.get("price") or item.get("priceValue") or item.get("priceString") or "0")
        price_lower = price_raw.lower()
        digits = re.sub(r'[^\d]', '', price_raw)
        price_num = int(digits) if digits else 0
        
        if "у.е." in price_lower or "$" in price_lower or "usd" in price_lower:
            price_usd = price_num 
        elif "сум" in price_lower or "so'm" in price_lower or "uzs" in price_lower:
            price_usd = int(price_num / 12600) 
        else:
            price_usd = int(price_num / 12600) if price_num > 1000000 else price_num

        c.execute('''
            INSERT OR REPLACE INTO listings (id, title, price_str, price_usd, location, link, image)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (item_id, title, price_raw, price_usd, location, link, image))
        
    conn.commit()
    conn.close()
    print(f"Baza yangilandi. {len(items)} ta e'lon saqlandi.")

def search_db(max_price_usd, district):
    conn = sqlite3.connect('olx_cache.db')
    c = conn.cursor()
    
    if district == "Hammasi":
        c.execute('SELECT title, price_str, location, link, image FROM listings WHERE price_usd <= ? ORDER BY price_usd ASC', (max_price_usd,))
    else:
        # Ham o'zbekcha, ham ruscha nomlar bo'yicha qidiramiz
        names = DISTRICT_MAP.get(district, [district])
        like_queries = " OR ".join(["location LIKE ?"] * len(names))
        params = [f'%{name}%' for name in names]
        params.append(max_price_usd)
        
        query = f'SELECT title, price_str, location, link, image FROM listings WHERE ({like_queries}) AND price_usd <= ? ORDER BY price_usd ASC'
        c.execute(query, tuple(params))
        
    results = c.fetchall()
    
    # Bazada jami qancha e'lon borligini tekshiramiz
    c.execute('SELECT COUNT(*) FROM listings')
    total_in_db = c.fetchone()[0]
    
    conn.close()
    return results, total_in_db

# --- FON REJIMIDA APIFY'DAN YUKLASH ---
def background_scraper():
    time.sleep(3)
    while True:
        print("Fon rejimida OLX ma'lumotlari yuklanmoqda...")
        try:
            client = ApifyClient(APIFY_TOKEN)
            run_input = {
                "country": "uz",
                "queries": ["kvartira sotiladi Toshkent"],
                "maxItems": 500, 
                "mode": "scrape"
            }
            run = client.actor("datahamster/olx-europe-listings").call(run_input=run_input)
            items = client.dataset(run.default_dataset_id).list_items().items
            save_to_db(items)
        except Exception as e:
            print(f"Fon yuklashda xatolik: {e}")
        
        print("Keyingi yuklash 2 soatdan keyin...")
        time.sleep(7200)

# --- FLASK SERVER ---
@app.route(f'/{BOT_TOKEN}', methods=['POST'])
def receive_update():
    json_string = request.get_data().decode('utf-8')
    update = telebot.types.Update.de_json(json_string)
    bot.process_new_updates([update])
    return "OK", 200

@app.route('/')
def home():
    return "OLX Instant Bot is alive!", 200

# --- BOT LOGIKASI ---
user_states = {}

@bot.message_handler(commands=['start'])
def start_handler(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("🏠 Kvartira qidirish"))
    bot.send_message(
        message.chat.id, 
        "Assalomu alaykum! 👋\n\nMen <b>OLX Tezkor Qidiruv</b> botiman. 🕵️‍♂️\nEndi qidiruvlar <b>0 soniya</b> vaqt oladi!\nBoshlash uchun pastdagi tugmani bosing 👇", 
        reply_markup=markup, parse_mode='HTML'
    )

@bot.message_handler(func=lambda message: message.text == "🏠 Kvartira qidirish")
def ask_budget(message):
    user_states[message.chat.id] = {'state': 'waiting_for_budget'}
    msg = bot.send_message(message.chat.id, "💰 <b>1-qadam:</b> Byudjetingizni <i>dollarda</i> kiriting.\nMasalan: <code>50000</code>", parse_mode='HTML')
    bot.register_next_step_handler(msg, ask_district)

def ask_district(message):
    user_id = message.chat.id
    try:
        max_price = int(message.text)
        if max_price <= 0: raise ValueError
        
        user_states[user_id] = {'state': 'waiting_for_district', 'budget': max_price}
        
        markup = types.InlineKeyboardMarkup(row_width=2)
        buttons = [types.InlineKeyboardButton(text=dist, callback_data=f"dist_{dist}") for dist in DISTRICT_MAP.keys()]
        buttons.append(types.InlineKeyboardButton(text="Hammasi", callback_data="dist_Hammasi"))
        markup.add(*buttons)
        
        bot.send_message(user_id, "📍 <b>2-qadam:</b> Qaysi tumandan qidiramiz?\nQuyidagi ro'yxatdan tanlang 👇", reply_markup=markup, parse_mode='HTML')
    except ValueError:
        bot.send_message(user_id, "❌ Xato! Iltimos, faqat raqam kiriting. Qaytadan boshlang: /start")
        if user_id in user_states: del user_states[user_id]

@bot.callback_query_handler(func=lambda call: call.data.startswith('dist_'))
def handle_district_choice(call):
    user_id = call.from_user.id
    district = call.data.replace('dist_', '')
    
    if user_id not in user_states or 'budget' not in user_states[user_id]:
        bot.answer_callback_query(call.id, "Xatolik yuz berdi. Iltimos, /start ni bosing.")
        return
        
    max_price = user_states[user_id]['budget']
    bot.answer_callback_query(call.id)
    
    results, total_in_db = search_db(max_price, district)
    
    # Agar baza umuman bo'sh bo'lsa (yangi deploy bo'lgan bo'lsa)
    if total_in_db == 0:
        bot.edit_message_text(
            chat_id=user_id, message_id=call.message.message_id, 
            text=f"⏳ <b>Ma'lumotlar bazasi hozir yuklanmoqda...</b>\n\n"
                 f"Server yaqinda ishga tushgani uchun OLX'dan ma'lumotlar yuklanmoqda. "
                 f"Iltimos, <b>15 soniyadan so'ng</b> qaytadan urinib ko'ring.", 
            parse_mode='HTML'
        )
        return
    
    if not results:
        bot.edit_message_text(
            chat_id=user_id, message_id=call.message.message_id, 
            text=f"🎉 <b>{district}</b> tumanidan <b>{max_price}$</b> gacha topilgan uylar (Jami: 0 ta):\n\n"
                 f"😔 Afsuski, hozircha bu tumandan mos uy topilmadi.\n"
                 f"<i>(Bazada jami {total_in_db} ta uy bor, lekin ular narxga yoki tumanga mos kelmadi).</i>", 
            parse_mode='HTML'
        )
        return
        
    bot.edit_message_text(
        chat_id=user_id, message_id=call.message.message_id, 
        text=f"🎉 <b>{district}</b> tumanidan <b>{max_price}$</b> gacha topilgan uylar (Jami: {len(results)} ta):", 
        parse_mode='HTML'
    )
    
    for res in results[:15]:
        title, price_str, location, link, image = res
        text = f"🏢 <b>{title}</b>\n\n💵 Narxi: <code>{price_str}</code>\n📍 Manzil: {location}\n"
        markup = types.InlineKeyboardMarkup()
        if link:
            markup.add(types.InlineKeyboardButton("🔗 E'lonni ko'rish", url=link))
        
        if image and str(image).startswith('http'):
            try:
                bot.send_photo(user_id, photo=image, caption=text, parse_mode='HTML', reply_markup=markup)
            except:
                bot.send_message(user_id, text, parse_mode='HTML', reply_markup=markup, disable_web_page_preview=True)
        else:
            bot.send_message(user_id, text, parse_mode='HTML', reply_markup=markup, disable_web_page_preview=True)
        time.sleep(0.4) 
        
    bot.send_message(user_id, "Yana qidirish uchun pastdagi tugmani bosing 👇", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add(types.KeyboardButton("🏠 Kvartira qidirish")))
    del user_states[user_id]

# --- ISHGA TUSHIRISH ---
if __name__ == "__main__":
    init_db()
    threading.Thread(target=background_scraper, daemon=True).start()
    
    render_url = os.environ.get("RENDER_EXTERNAL_URL")
    if render_url:
        bot.remove_webhook()
        bot.set_webhook(url=f"{render_url}/{BOT_TOKEN}")
        print(f"Webhook o'rnatildi: {render_url}/{BOT_TOKEN}")
    
    print("Server ishga tushdi...")
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)
