import telebot
import requests
from bs4 import BeautifulSoup
import os
import threading
import re
from flask import Flask
from telebot import types

# --- SOZLAMALAR ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
bot = telebot.TeleBot(BOT_TOKEN)

# --- FLASK SERVER (Render uxlab qolmasligi uchun) ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Smart Search Bot is alive!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, use_reloader=False)

# --- OLX PARSER FUNKSIYASI ---
def search_olx_apartments(max_price_usd):
    """OLX'dan Toshkentdagi kvartiralarni qidiradi va narx bo'yicha filtrlaydi."""
    url = "https://www.olx.uz/nedvizhimost/kvartiry/prodazha/tashkent/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # OLX dagi e'lonlar blokini topamiz
        ads = soup.find_all('div', attrs={'data-cy': 'l-card'})
        
        results = []
        for ad in ads:
            try:
                title_elem = ad.find('h6')
                title = title_elem.text.strip() if title_elem else "Noma'lum uy"
                
                price_elem = ad.find('p', attrs={'data-testid': 'ad-price'})
                price_text = price_elem.text.strip() if price_elem else "Narx yo'q"
                
                location_elem = ad.find('p', attrs={'data-testid': 'location-date'})
                location = location_elem.text.strip() if location_elem else "Manzil yo'q"
                
                link_elem = ad.find('a')
                link = "https://www.olx.uz" + link_elem['href'] if link_elem and link_elem['href'].startswith('/') else (link_elem['href'] if link_elem else "")

                # Narxni raqamga ajratib olish (masalan, "$ 50 000" dan 50000 ni olish)
                price_num = 0
                if "so'm" in price_text or "у.е." in price_text or "$" in price_text:
                    # Faqat raqamlarni qoldiramiz
                    digits = re.sub(r'[^\d]', '', price_text)
                    if digits:
                        price_num = int(digits)
                        # Agar narx so'mda berilgan bo'lsa va u juda katta bo'lsa (masalan 500 million so'm), uni dollarga o'tkazish shart emas, 
                        # shunchaki foydalanuvchi kiritgan limit bilan solishtiramiz. 
                        # Lekin soddalik uchun faqat raqamni tekshiramiz.
                
                # Agar foydalanuvchi dollar kiritgan bo'lsa, lekin e'lon so'mda bo'lsa, taxminiy konvertatsiya (1$ = 12600 so'm)
                if "so'm" in price_text and price_num > 1000000:
                    price_in_usd = int(price_num / 12600)
                else:
                    price_in_usd = price_num

                if price_in_usd <= max_price_usd:
                    results.append({
                        "title": title,
                        "price": price_text,
                        "location": location,
                        "link": link
                    })
            except Exception as e:
                continue # Bitta e'londa xato bo'lsa, o'tkazib yuboramiz
                
        return results[:5] # Eng ko'pi bilan 5 ta natija qaytaramiz
    except Exception as e:
        print(f"Scraping xatosi: {e}")
        return None

# --- BOT LOGIKASI ---
user_states = {}

@bot.message_handler(commands=['start'])
def start_handler(message):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    btn = types.KeyboardButton("🏠 Kvartira qidirish")
    markup.add(btn)
    
    bot.send_message(
        message.chat.id,
        "Salom! Men OLX'dan sizga eng mos uylarni topib beruvchi aqlli botman. 🕵️‍♂️\n\n"
        "Hozircha faqat Toshkentdagi sotilayotgan kvartiralarni qidiraman.\n"
        "Boshlash uchun pastdagi tugmani bosing.",
        reply_markup=markup
    )

@bot.message_handler(func=lambda message: message.text == "🏠 Kvartira qidirish")
def ask_price(message):
    user_states[message.chat.id] = {'state': 'waiting_for_price'}
    msg = bot.send_message(
        message.chat.id, 
        "Qancha byudjetingiz bor? (Dollarda kiriting, masalan: 50000)\n"
        "Faqat raqam yozing:"
    )
    bot.register_next_step_handler(msg, process_search)

def process_search(message):
    user_id = message.chat.id
    if user_id not in user_states or user_states[user_id].get('state') != 'waiting_for_price':
        return
        
    try:
        max_price = int(message.text)
        if max_price <= 0:
            raise ValueError
            
        loading_msg = bot.reply_to(message, "🔎 OLX'dan ma'lumotlar qidirilmoqda, biroz kuting...")
        
        results = search_olx_apartments(max_price)
        
        bot.delete_message(user_id, loading_msg.message_id)
        
        if not results:
            bot.send_message(user_id, "Afsuski, sizning byudjetingizga mos uy topilmadi. Boshqa narx kiriting yoki keyinroq urinib ko'ring.")
            return
            
        bot.send_message(user_id, f"🎉 Sizning byudjetingizga ({max_price}$) mos keladigan top 5 ta uy:")
        
        for res in results:
            text = f"🏠 **{res['title']}**\n💰 {res['price']}\n📍 {res['location']}\n🔗 [E'lonni ko'rish]({res['link']})"
            bot.send_message(user_id, text, parse_mode='Markdown', disable_web_page_preview=True)
            
    except ValueError:
        bot.send_message(user_id, "Iltimos, faqat raqam kiriting (masalan: 50000).")
    finally:
        if user_id in user_states:
            del user_states[user_id]

# --- ISHGA TUSHIRISH ---
if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    print("Aqlli Qidiruv boti ishga tushdi...")
    try:
        requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook?drop_pending_updates=true")
    except:
        pass
    bot.infinity_polling()
