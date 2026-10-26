import telebot
import os
import threading
import re
import asyncio
from flask import Flask
from telebot import types
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

# --- SOZLAMALAR ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
bot = telebot.TeleBot(BOT_TOKEN)

# --- FLASK SERVER ---
app = Flask(__name__)

@app.route('/')
def home():
    return "OLX Smart Search Bot is alive!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port, use_reloader=False)

# --- OLX PARSER (PLAYWRIGHT) ---
async def fetch_olx_html(url):
    """Playwright orqali sahifani ochib, HTML qaytaradi."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        await page.goto(url, timeout=60000, wait_until="domcontentloaded")
        await page.wait_for_timeout(7000) # Sahifa to'liq yuklanishini kutish
        html = await page.content()
        await browser.close()
        return html

def search_olx_apartments(max_price_usd, chat_id):
    url = "https://www.olx.uz/nedvizhimost/kvartiry/prodazha/tashkent/"
    
    try:
        html = asyncio.run(fetch_olx_html(url))
    except Exception as e:
        bot.send_message(chat_id, f"⚠️ Debug: Brauzer xatosi: {e}")
        return []

    soup = BeautifulSoup(html, 'html.parser')
    ads = soup.select('div[data-cy="l-card"]')

    # 🔍 DEBUG: Agar uylar topilmasa, sababini tekshiramiz
    if not ads:
        page_title = soup.title.string if soup.title else "Sarlavha topilmadi"
        is_cloudflare = "Just a moment" in html or "cf-challenge" in html or "Cloudflare" in html
        html_size = len(html)
        
        debug_msg = (
            f"🛑 **Debug hisobot:**\n"
            f"• Sahifa nomi: `{page_title[:50]}`\n"
            f"• HTML hajmi: `{html_size}` bayt\n"
            f"• Cloudflare himoyasi: `{'Bor (Bloklangan)' if is_cloudflare else 'Yo\'q'}`\n\n"
            f"HTML boshlanishi:\n<pre>{html[:300]}</pre>"
        )
        bot.send_message(chat_id, debug_msg, parse_mode='HTML')
        return []

    results = []
    for ad in ads:
        try:
            title_elem = ad.find('h6')
            title = title_elem.text.strip() if title_elem else "Noma'lum uy"
            
            price_elem = ad.find('p', attrs={'data-testid': 'ad-price'})
            price_text = price_elem.text.strip() if price_elem else "0"
            
            location_elem = ad.find('p', attrs={'data-testid': 'location-date'})
            location = location_elem.text.strip() if location_elem else "Manzil yo'q"
            
            link_elem = ad.find('a')
            link = "https://www.olx.uz" + link_elem['href'] if link_elem and link_elem['href'].startswith('/') else (link_elem['href'] if link_elem else "")

            # Narxni hisoblash
            price_text_lower = price_text.lower()
            digits = re.sub(r'[^\d]', '', price_text)
            price_num = int(digits) if digits else 0
            
            if ("сум" in price_text_lower or "so'm" in price_text_lower) and price_num > 1000000:
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
        except Exception:
            continue

    return results[:5]

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
    msg = bot.send_message(message.chat.id, "Qancha byudjetingiz bor? (Dollarda kiriting, masalan: 50000)\nFaqat raqam yozing:")
    bot.register_next_step_handler(msg, process_search)

def process_search(message):
    user_id = message.chat.id
    if user_id not in user_states or user_states[user_id].get('state') != 'waiting_for_price':
        return
        
    try:
        max_price = int(message.text)
        if max_price <= 0:
            raise ValueError
            
        loading_msg = bot.reply_to(message, "🔎 OLX tekshirilmoqda, biroz kuting (10-15 soniya)...")
        results = search_olx_apartments(max_price, user_id)
        bot.delete_message(user_id, loading_msg.message_id)
        
        if not results:
            bot.send_message(user_id, "Afsuski, sizning byudjetingizga mos uy topilmadi yoki tizim bloklandi.")
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
    print("Bot ishga tushdi...")
    try:
        import requests
        requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook?drop_pending_updates=true")
    except:
        pass
    bot.infinity_polling()
