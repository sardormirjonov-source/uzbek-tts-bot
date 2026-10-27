import telebot
import os
import re
import time
from flask import Flask, request
from telebot import types
from apify_client import ApifyClient

# --- SOZLAMALAR ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
APIFY_TOKEN = os.environ.get("APIFY_TOKEN")

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# Toshkent shahri tumanlari ro'yxati
TASHKENT_DISTRICTS = [
    "Chilonzor", "Yunusobod", "Mirzo Ulug'bek", "Yakkasaroy", 
    "Shayxontohur", "Uchtepa", "Olmazor", "Sergeli", 
    "Bektemir", "Mirobod", "Yashnobod", "Hammasi"
]

# --- FLASK SERVER (Webhook uchun) ---
@app.route(f'/{BOT_TOKEN}', methods=['POST'])
def receive_update():
    json_string = request.get_data().decode('utf-8')
    update = telebot.types.Update.de_json(json_string)
    bot.process_new_updates([update])
    return "OK", 200

@app.route('/')
def home():
    return "OLX Apify Bot is alive!", 200

# --- APIFY ORQALI OLX QIDIRUVI ---
def search_olx_apartments_apify(max_price_usd, district, chat_id):
    try:
        client = ApifyClient(APIFY_TOKEN)
        
        if district == "Hammasi":
            query_text = "kvartira sotiladi Toshkent"
        else:
            query_text = f"kvartira sotiladi {district}"
        
        run_input = {
            "country": "uz",
            "queries": [query_text],
            "maxItems": 200, 
            "mode": "scrape"
        }
        
        run = client.actor("datahamster/olx-europe-listings").call(run_input=run_input)
        dataset_items = client.dataset(run.default_dataset_id).list_items().items
        
        results = []
        for item in dataset_items:
            title = item.get("title") or item.get("name") or "Noma'lum uy"
            location = item.get("location") or "Manzil ko'rsatilmagan"
            link = item.get("url") or item.get("link") or ""
            image_url = item.get("image") or item.get("photo") or None
            
            price_raw = item.get("price") or item.get("priceValue") or item.get("priceString") or "0"
            price_text = str(price_raw)
            price_text_lower = price_text.lower()
            
            digits = re.sub(r'[^\d]', '', price_text)
            price_num = int(digits) if digits else 0
            
            if "у.е." in price_text_lower or "$" in price_text_lower or "usd" in price_text_lower:
                price_in_usd = price_num 
            elif "сум" in price_text_lower or "so'm" in price_text_lower or "uzs" in price_text_lower:
                price_in_usd = int(price_num / 12600) 
            else:
                price_in_usd = int(price_num / 12600) if price_num > 1000000 else price_num

            if 0 < price_in_usd <= max_price_usd:
                results.append({
                    "title": title,
                    "price": price_text,
                    "location": location,
                    "link": link,
                    "image": image_url
                })
        
        return results[:15]
        
    except Exception as e:
        print(f"Apify xatosi: {e}")
        bot.send_message(chat_id, f"⚠️ Tizim xatosi: {e}")
        return []

# --- BOT LOGIKASI ---
user_states = {}

@bot.message_handler(commands=['start'])
def start_handler(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    btn = types.KeyboardButton("🏠 Kvartira qidirish")
    markup.add(btn)
    bot.send_message(
        message.chat.id, 
        "Assalomu alaykum! 👋\n\n"
        "Men <b>OLX Aqlli Qidiruv</b> botiman. 🕵️‍♂️\n"
        "Sizga kerakli tumandagi va byudjetingizga mos eng sara uylarni topib beraman.\n\n"
        "Boshlash uchun pastdagi tugmani bosing 👇", 
        reply_markup=markup,
        parse_mode='HTML'
    )

@bot.message_handler(func=lambda message: message.text == "🏠 Kvartira qidirish")
def ask_budget(message):
    user_states[message.chat.id] = {'state': 'waiting_for_budget'}
    msg = bot.send_message(
        message.chat.id, 
        "💰 <b>1-qadam:</b> Byudjetingizni <i>dollarda</i> kiriting.\n"
        "Masalan: <code>50000</code>", 
        parse_mode='HTML'
    )
    bot.register_next_step_handler(msg, ask_district)

def ask_district(message):
    user_id = message.chat.id
    try:
        max_price = int(message.text)
        if max_price <= 0: raise ValueError
        
        user_states[user_id] = {'state': 'waiting_for_district', 'budget': max_price}
        
        # Tumanlarni tugmalar ko'rinishida chiqaramiz
        markup = types.InlineKeyboardMarkup(row_width=2)
        buttons = []
        for dist in TASHKENT_DISTRICTS:
            buttons.append(types.InlineKeyboardButton(text=dist, callback_data=f"dist_{dist}"))
        
        markup.add(*buttons)
        
        bot.send_message(
            user_id, 
            "📍 <b>2-qadam:</b> Qaysi tumandan qidiramiz?\n"
            "Quyidagi ro'yxatdan tanlang 👇", 
            reply_markup=markup,
            parse_mode='HTML'
        )
    except ValueError:
        bot.send_message(user_id, "❌ Xato! Iltimos, faqat raqam kiriting. Qaytadan boshlang: /start")
        if user_id in user_states: del user_states[user_id]

# Tugma bosilganda ishlaydigan qism
@bot.callback_query_handler(func=lambda call: call.data.startswith('dist_'))
def handle_district_choice(call):
    user_id = call.from_user.id
    district = call.data.replace('dist_', '')
    
    if user_id not in user_states or 'budget' not in user_states[user_id]:
        bot.answer_callback_query(call.id, "Xatolik yuz berdi. Iltimos, /start ni bosing.")
        return
        
    max_price = user_states[user_id]['budget']
    
    # Tugma bosilgach, eski xabarni o'chirib, kutish xabarini yuboramiz
    bot.edit_message_text(
        chat_id=user_id, 
        message_id=call.message.message_id, 
        text=f"🔎 <b>{district}</b> tumanidan <b>{max_price}$</b> gacha uylar qidirilmoqda...\n<i>Iltimos, 10-15 soniya kuting.</i>", 
        parse_mode='HTML'
    )
    
    bot.send_chat_action(user_id, 'typing')
    
    try:
        results = search_olx_apartments_apify(max_price, district, user_id)
        
        if not results:
            bot.send_message(user_id, f"😔 Afsuski, <b>{district}</b> tumanidan <b>{max_price}$</b> byudjetingizga mos uy topilmadi.\nBoshqa narx yoki tuman tanlang.", parse_mode='HTML')
            return
            
        bot.send_message(user_id, f"🎉 <b>{district}</b> tumanidan <b>{max_price}$</b> gacha topilgan uylar (Jami: {len(results)} ta):", parse_mode='HTML')
        
        for res in results:
            text = (
                f"🏢 <b>{res['title']}</b>\n\n"
                f"💵 Narxi: <code>{res['price']}</code>\n"
                f"📍 Manzil: {res['location']}\n"
            )
            
            markup = types.InlineKeyboardMarkup()
            if res['link']:
                btn = types.InlineKeyboardButton("🔗 E'lonni ko'rish", url=res['link'])
                markup.add(btn)
            
            if res.get('image') and str(res['image']).startswith('http'):
                try:
                    bot.send_photo(user_id, photo=res['image'], caption=text, parse_mode='HTML', reply_markup=markup)
                except:
                    bot.send_message(user_id, text, parse_mode='HTML', reply_markup=markup, disable_web_page_preview=True)
            else:
                bot.send_message(user_id, text, parse_mode='HTML', reply_markup=markup, disable_web_page_preview=True)
                
            time.sleep(0.5) 
            
        bot.send_message(user_id, "Yana qidirish uchun pastdagi tugmani bosing 👇", reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True).add(types.KeyboardButton("🏠 Kvartira qidirish")))
            
    except Exception as e:
        bot.send_message(user_id, f"⚠️ Kutilmagan xatolik: {e}")
    finally:
        if user_id in user_states:
            del user_states[user_id]
        # Callback query ga javob berish (tugmadagi soat belgisi yo'qolishi uchun)
        bot.answer_callback_query(call.id)

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
