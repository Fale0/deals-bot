import os
import feedparser
import re
from datetime import datetime, timedelta, timezone
import time
import requests
from flask import Flask, request, jsonify
import threading
from deep_translator import GoogleTranslator
import urllib.parse
import openai
import random

app = Flask(__name__)
BOT_TOKEN = os.environ.get("BOT_TOKEN")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
last_update_id = 0

# Настройка DeepSeek (отключаем при ошибке баланса)
if DEEPSEEK_API_KEY:
    openai.api_key = DEEPSEEK_API_KEY
    openai.api_base = "https://api.deepseek.com/v1"
    DEEPSEEK_AVAILABLE = False  # Временно отключаем из-за ошибки баланса
    print("⚠️ DeepSeek временно отключен (недостаточно средств)")
else:
    DEEPSEEK_AVAILABLE = False
    print("⚠️ DeepSeek API ключ не найден")

translator = GoogleTranslator(source='en', target='ru')

# Московское время
MOSCOW_TZ = timezone(timedelta(hours=3))

# ==================== ТОЛЬКО РАБОЧИЕ ИСТОЧНИКИ ====================

# Медицинские источники (только те, что работают)
MEDICAL_FEEDS = [
    ("Nature", "https://www.nature.com/subjects/medical-research.rss"),
    ("WHO", "https://www.who.int/rss-feeds/news-english.xml"),
]

# Косметологические источники (альтернативные, через поиск новостей)
# Так как RSS не работают, используем медицинские источники с косметологическими ключевыми словами
COSMETOLOGY_FEEDS = [
    ("Medical News (Cosmetics)", "https://www.news-medical.net/medical-news.aspx?format=rss"),
    ("ScienceDaily", "https://www.sciencedaily.com/rss/health_medicine/all.xml"),
]

# Ключевые слова для косметологии (фильтрация)
COSMETOLOGY_KEYWORDS = [
    "cosmetic", "beauty", "skin", "anti-aging", "aesthetic", 
    "dermatology", "face cream", "wrinkle", "botox", "filler",
    "косметология", "красота", "кожа", "антивозрастной"
]

def clean_html(raw):
    if not raw:
        return ""
    return re.sub(r'<.*?>', '', raw)

def calculate_importance(title, description, category="medical"):
    text = (title + " " + description).lower()
    score = 5
    
    # Базовые ключевые слова
    high_keywords = ["breakthrough", "cure", "treatment", "clinical trial", "fda approved", "groundbreaking"]
    medium_keywords = ["study shows", "research", "scientists discover", "new method", "development"]
    
    for kw in high_keywords:
        if kw in text:
            score += 2
    for kw in medium_keywords:
        if kw in text:
            score += 1
    
    # Бонус для косметологии
    if category == "cosmetology":
        if any(kw in text for kw in COSMETOLOGY_KEYWORDS):
            score += 2
    else:
        if "cancer" in text or "tumor" in text:
            score += 1
        if "aging" in text or "wrinkle" in text:
            score += 1
    
    return min(10, max(1, score))

def translate_text(text):
    if not text or len(text.strip()) < 5:
        return text
    try:
        text_to_translate = text[:4000] if len(text) > 4000 else text
        return translator.translate(text_to_translate)
    except Exception as e:
        print(f"Ошибка перевода: {e}")
        return text

def analyze_with_deepseek(title, content):
    # Временно отключено из-за ошибки баланса
    return ""

def get_news_image(title, category="medical"):
    """Всегда возвращает рабочую картинку"""
    medical_images = [
        "https://cdn.pixabay.com/photo/2016/06/28/05/10/microscope-1482987_640.jpg",
        "https://cdn.pixabay.com/photo/2020/10/18/09/16/hospital-5664806_640.jpg",
        "https://cdn.pixabay.com/photo/2015/11/16/22/14/surgery-1046403_640.jpg",
        "https://cdn.pixabay.com/photo/2016/03/06/05/47/heart-1239478_640.jpg",
        "https://cdn.pixabay.com/photo/2015/09/09/16/05/brain-931968_640.jpg",
    ]
    
    cosmetic_images = [
        "https://cdn.pixabay.com/photo/2016/11/29/12/54/beauty-1869540_640.jpg",
        "https://cdn.pixabay.com/photo/2017/08/07/21/31/skin-2607783_640.jpg",
        "https://cdn.pixabay.com/photo/2014/04/13/20/17/beauty-323952_640.jpg",
        "https://cdn.pixabay.com/photo/2015/10/31/12/20/face-cream-1015605_640.jpg",
        "https://cdn.pixabay.com/photo/2019/08/28/18/01/spa-4437173_640.jpg",
    ]
    
    if category == "cosmetology":
        return random.choice(cosmetic_images)
    else:
        return random.choice(medical_images)

def fetch_news(feed_list, limit=7, category="medical"):
    articles = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=72)
    successful_sources = 0
    
    for source_name, url in feed_list:
        try:
            print(f"📡 Загружаю: {source_name} - {url}")
            
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            response = requests.get(url, timeout=20, headers=headers)
            feed = feedparser.parse(response.content)
            
            if not feed.entries:
                print(f"⚠️ {source_name}: нет записей")
                continue
            
            print(f"✅ {source_name}: {len(feed.entries)} записей")
            successful_sources += 1
            
            for entry in feed.entries[:30]:
                try:
                    pub = entry.get("published_parsed") or entry.get("updated_parsed")
                    if not pub:
                        continue
                    
                    pub_dt_utc = datetime.fromtimestamp(datetime(*pub[:6]).timestamp(), tz=timezone.utc)
                    
                    if pub_dt_utc < cutoff:
                        continue
                    
                    pub_dt_msk = pub_dt_utc.astimezone(MOSCOW_TZ)
                    
                    title_en = entry.get("title", "Без заголовка")
                    desc_en = clean_html(entry.get("description", entry.get("summary", "")))[:500]
                    link = entry.get("link", "#")
                    
                    # Для косметологии фильтруем по ключевым словам
                    if category == "cosmetology":
                        if not any(kw in (title_en + desc_en).lower() for kw in COSMETOLOGY_KEYWORDS):
                            continue
                    
                    importance = calculate_importance(title_en, desc_en, category)
                    
                    title_ru = translate_text(title_en)
                    desc_ru = translate_text(desc_en[:400])
                    image_url = get_news_image(title_en, category)
                    
                    articles.append({
                        "title": title_ru,
                        "link": link,
                        "desc": desc_ru[:350],
                        "date": pub_dt_msk.strftime("%d.%m.%Y %H:%M"),
                        "source": source_name,
                        "importance": importance,
                        "image_url": image_url
                    })
                except Exception as e:
                    continue
                    
        except Exception as e:
            print(f"❌ Ошибка {source_name}: {e}")
    
    print(f"📊 {category}: обработано {successful_sources}/{len(feed_list)} источников, найдено {len(articles)} новостей")
    
    articles.sort(key=lambda x: (x["importance"], x["date"]), reverse=True)
    
    seen = set()
    unique = []
    for a in articles:
        if a["title"] not in seen:
            seen.add(a["title"])
            unique.append(a)
    
    return unique[:limit]

def send_photo(chat_id, image_url, caption):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
        payload = {
            "chat_id": chat_id,
            "photo": image_url,
            "caption": caption,
            "parse_mode": "Markdown"
        }
        response = requests.post(url, json=payload, timeout=30)
        if response.status_code != 200:
            send_message(chat_id, caption)
    except Exception as e:
        print(f"Ошибка фото: {e}")
        send_message(chat_id, caption)

def send_message(chat_id, text, parse_mode="Markdown"):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }
        requests.post(url, json=payload, timeout=30)
    except Exception as e:
        print(f"Ошибка отправки: {e}")

def send_news_with_keyboard(chat_id, feed_list, count, title_message, category):
    send_message(chat_id, f"🔍 {title_message}\n⏳ Загружаю новости...")
    
    news_list = fetch_news(feed_list, count, category)
    
    if not news_list:
        send_message(chat_id, "😕 *Новости не найдены*\n\nПопробуйте позже.")
        show_keyboard(chat_id)
        return
    
    for idx, news in enumerate(news_list, 1):
        if news["importance"] >= 8:
            imp_emoji = "🔴🔥"
        elif news["importance"] >= 6:
            imp_emoji = "🟠⚠️"
        elif news["importance"] >= 4:
            imp_emoji = "🟡📌"
        else:
            imp_emoji = "⚪📰"
        
        caption = f"{imp_emoji} *{idx}. {news['title']}*\n\n"
        caption += f"📝 {news['desc']}\n\n"
        caption += f"📅 {news['date']} | 📰 {news['source']}\n"
        caption += f"⭐ Важность: {news['importance']}/10\n\n"
        caption += f"🔗 [Читать полностью]({news['link']})"
        
        send_photo(chat_id, news["image_url"], caption)
        time.sleep(0.5)
    
    send_message(chat_id, f"✅ *Готово!* {len(news_list)} новостей 🖼️")
    show_keyboard(chat_id)

def show_keyboard(chat_id):
    keyboard = {
        "keyboard": [
            ["🏥 Топ 7 новостей по мед. исследованиям"],
            ["💄 Топ 7 новостей косметологии"]
        ],
        "resize_keyboard": True
    }
    
    payload = {
        "chat_id": chat_id,
        "text": "🔬 *Выберите категорию:*",
        "reply_markup": keyboard,
        "parse_mode": "Markdown"
    }
    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json=payload)

def keep_alive():
    while True:
        time.sleep(10 * 60)
        try:
            url = os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:5000") + "/health"
            requests.get(url, timeout=10)
        except:
            pass

def bot_polling():
    global last_update_id
    print("✅ Бот запущен!")
    
    while True:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={last_update_id+1}&timeout=30"
            resp = requests.get(url, timeout=35)
            updates = resp.json()
            
            for update in updates.get("result", []):
                last_update_id = update["update_id"]
                msg = update.get("message", {})
                chat_id = msg.get("chat", {}).get("id")
                text = msg.get("text", "")
                
                if text == "/start":
                    welcome = "🏥 *Медицинский бот*\n\n📌 Медицинские исследования\n💄 Косметология\n\n👇 Нажмите на кнопку"
                    send_message(chat_id, welcome)
                    show_keyboard(chat_id)
                
                elif "мед. исследованиям" in text:
                    send_news_with_keyboard(chat_id, MEDICAL_FEEDS, 7, "🏥 *Топ медицинских исследований*", "medical")
                
                elif "косметологии" in text:
                    send_news_with_keyboard(chat_id, MEDICAL_FEEDS, 7, "💄 *Топ новостей косметологии*", "cosmetology")
                
                elif text == "/health":
                    send_message(chat_id, "✅ Бот работает")
        
        except Exception as e:
            print(f"Ошибка: {e}")
            time.sleep(5)

@app.route('/')
def index():
    return "Бот работает"

@app.route('/health')
def health():
    return "OK", 200

if __name__ == "__main__":
    threading.Thread(target=keep_alive, daemon=True).start()
    threading.Thread(target=bot_polling, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
