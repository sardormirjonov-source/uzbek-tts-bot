eimport telebot
import requests
import os
import uuid
import json
import threading
import time
from flask import Flask

# --- SOZLAMALAR (Render'dan Environment Variable orqali olinadi) ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
AISHA_API_KEY = os.environ.get("AISHA_API_KEY")
AISHA_TTS_URL = "https://back.aisha.group/api/v1/tts/post/"

bot = telebot.TeleBot(BOT_TOKEN)

# --- KEEP-ALIVE SERVER (Render uxlab qolmasligi uchun) ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is alive and running 24/7!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def self_ping():
    render_url = os.environ.get("RENDER_EXTERNAL_URL")
    if render_url:
        while True:
            time.sleep(600)
            try:
                requests.get(render_url)
                print("Keep-alive ping muvaffaqiyatli.")
            except Exception as e:
                print(f"Ping xatosi: {e}")

# --- BOT LOGIKASI ---
@bot.message_handler(commands=['start'])
def start_handler(message):
    bot.reply_to(message, "Salom! Menga matn yuboring, men uni Aisha AI (Gulnoza ovozi) orqali ovozga aylantiraman. 🎙️")

@bot.message_handler(func=lambda message: True, content_types=['text'])
def text_handler(message):
    matn = message.text
    if not matn.strip():
        return

    vaqtinchalik_fayl = f"{uuid.uuid4()}.mp3"
    yuklanmoqda = bot.reply_to(message, "Aisha AI ovoz yaratmoqda... ⏳")

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
    finally:
        if os.path.exists(vaqtinchalik_fayl):
            os.remove(vaqtinchalik_fayl)
        bot.delete_message(yuklanmoqda.chat.id, yuklanmoqda.message_id)

# --- ISHGA TUSHIRISH ---
if __name__ == "__main__":
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=self_ping, daemon=True).start()
    
    print("Bot 24/7 rejimida ishga tushmoqda...")
    try:
        requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/deleteWebhook?drop_pending_updates=true")
    except:
        pass
    bot.infinity_polling()
