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

# Настройка DeepSeek (старый API формат для версии 0.28.1)
if DEEPSEEK_API_KEY:
    openai.api_key = DEEPSEEK_API_KEY
    openai.api_base = "https://api.deepseek.com/v1"
    DEEPSEEK_AVAILABLE = True
    print("✅ DeepSeek API подключен")
else:
    DEEPSEEK_AVAILABLE = False
    print("⚠️ DeepSeek API ключ не найден")

translator = GoogleTranslator(source='en', target='ru')

# Московское время (UTC+3)
MOSCOW_TZ = timezone(timedelta(hours=3))

# ==================== ИСТОЧНИКИ НОВОСТЕЙ ====================
# Источники по косметологии (обновленные и рабочие)
COSMETOLOGY_FEEDS = [
    "https://www.sciencedaily.com/rss/matter_energy/cosmetics.xml",
    "https://www.news-medical.net/tag/feed/Cosmetic-Medicine",
    "https://www.medicalnewstoday.com/feeds/categories/beauty",
    "https://www.cosmeticsdesign-europe.com/RSS",
    "https://www.cosmeticsdesign-asia.com/RSS",
    "https://www.happi.com/rss",
    "https://www.personalcaremagazine.com/rss-news",
    "https://www.dermascope.com/feed",
]

# Медицинские источники
MEDICAL_FEEDS = [
    "https://www.nih.gov/news-events/news-releases/feed",
    "https://www.nature.com/subjects/medical-research.rss",
    "https://www.news-medical.net/medical-news.aspx?format=rss",
    "https://www.medicalnewstoday.com/feeds/all",
    "https://www.thelancet.com/rss",
    "https://www.nejm.org/rss",
    "https://www.who.int/rss-feeds/news-english.xml",
    "https://www.sciencedaily.com/rss/health_medicine/all.xml",
]

# Ключевые слова для оценки важности
IMPORTANCE_KEYWORDS = {
    "high": [
        "breakthrough", "revolutionary", "cure", "treatment", "clinical trial", 
        "fda approved", "groundbreaking", "significant discovery", "gene therapy",
        "stem cell", "innovation", "revolutionary treatment",
        "clinical study", "research finding"
    ],
    "medium": [
        "study shows", "research", "scientists discover", "new method",
        "innovation", "development", "cosmetic breakthrough", "anti-aging",
        "skin care innovation", "aesthetic medicine"
    ],
}

def clean_html(raw):
    if not raw:
        return ""
    return re.sub(r'<.*?>', '', raw)

def calculate_importance(title, description):
    text = (title + " " + description).lower()
    score = 5
    for kw in IMPORTANCE_KEYWORDS["high"]:
        if kw in text:
            score += 2
    for kw in IMPORTANCE_KEYWORDS["medium"]:
        if kw in text:
            score += 1
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
    """Анализирует новость с помощью DeepSeek AI"""
    if not DEEPSEEK_AVAILABLE:
        return ""

    try:
        prompt = f"""Ты — медицинский аналитик. Сделай краткий анализ этой новости на русском языке.

Заголовок: {title}
Содержание: {content[:400]}

Напиши в формате:
💡 Суть: (одно предложение)
🎯 Значение: (для медицины/косметологии - позитивное/нейтральное)"""

        response = openai.ChatCompletion.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=150
        )
        return f"\n\n🤖 *DeepSeek:*\n{response.choices[0].message.content}"
    except Exception as e:
        print(f"Ошибка DeepSeek: {e}")
        return ""

def extract_image_from_article(link):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = requests.get(link, timeout=15, headers=headers)

        patterns = [
            r'<meta[^>]*property="og:image"[^>]*content="([^"]+)"',
            r'<meta[^>]*name="twitter:image"[^>]*content="([^"]+)"',
            r'<meta[^>]*itemprop="image"[^>]*content="([^"]+)"',
            r'<img[^>]*src="([^"]+)"[^>]*class="[^"]*featured[^"]*"',
            r'<img[^>]*src="([^"]+)"[^>]*class="[^"]*main-image[^"]*"',
        ]

        for pattern in patterns:
            match = re.search(pattern, response.text, re.IGNORECASE)
            if match:
                img_url = match.group(1)
                if img_url.startswith('http') and not any(bad in img_url.lower() for bad in ['pixel', 'placeholder', 'blank']):
                    try:
                        img_check = requests.head(img_url, timeout=5, headers=headers)
                        if img_check.status_code == 200 and 'image' in img_check.headers.get('content-type', ''):
                            return img_url
                    except:
                        continue
    except Exception as e:
        print(f"Ошибка извлечения картинки: {e}")
    return None

def get_fallback_image(title):
    """Резервные изображения из Pixabay (всегда доступны)"""
    medical_images = [
        "https://cdn.pixabay.com/photo/2020/10/18/09/16/hospital-5664806_640.jpg",
        "https://cdn.pixabay.com/photo/2016/06/28/05/10/microscope-1482987_640.jpg",
        "https://cdn.pixabay.com/photo/2015/11/16/22/14/surgery-1046403_640.jpg",
        "https://cdn.pixabay.com/photo/2016/03/06/05/47/heart-1239478_640.jpg",
        "https://cdn.pixabay.com/photo/2015/09/09/16/05/brain-931968_640.jpg",
    ]
    
    cosmetic_images = [
        "https://cdn.pixabay.com/photo/2016/11/29/12/54/beauty-1869540_640.jpg",
        "https://cdn.pixabay.com/photo/2017/08/07/21/31/skin-2607783_640.jpg",
        "https://cdn.pixabay.com/photo/2014/04/13/20/17/beauty-323952_640.jpg",
        "https://cdn.pixabay.com/photo/2015/10/31/12/20/face-cream-1015605_640.jpg",
    ]
    
    title_lower = title.lower()
    if any(word in title_lower for word in ['cosmetic', 'beauty', 'skin', 'anti-aging', 'косметолог', 'spa', 'face', 'cream']):
        return random.choice(cosmetic_images)
    else:
        return random.choice(medical_images)

def get_news_image(link, title):
    """Главная функция получения картинки - ВСЕГДА возвращает URL"""
    # 1. Пробуем извлечь из статьи
    image_url = extract_image_from_article(link)
    if image_url:
        return image_url
    
    # 2. Возвращаем fallback (всегда рабочий)
    return get_fallback_image(title)

def fetch_news(feed_list, limit=7, source_name="main"):
    articles = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=72)  # Увеличил до 72 часов

    for url in feed_list:
        try:
            print(f"Загружаю: {url}")
            feed = feedparser.parse(url)

            for entry in feed.entries[:30]:  # Увеличил до 30
                pub = entry.get("published_parsed")
                if not pub:
                    continue

                pub_dt_utc = datetime.fromtimestamp(
                    datetime(*pub[:6]).timestamp(),
                    tz=timezone.utc
                )

                if pub_dt_utc < cutoff:
                    continue

                pub_dt_msk = pub_dt_utc.astimezone(MOSCOW_TZ)

                title_en = entry.get("title", "Без заголовка")
                desc_en = clean_html(entry.get("description", "Нет описания"))[:500]
                link = entry.get("link", "#")

                importance = calculate_importance(title_en, desc_en)

                if importance >= 4:
                    title_ru = translate_text(title_en)
                    desc_ru = translate_text(desc_en[:400])
                else:
                    title_ru = title_en
                    desc_ru = desc_en[:400]

                image_url = get_news_image(link, title_en)  # Упростил вызов

                articles.append({
                    "title": title_ru,
                    "title_en": title_en,
                    "link": link,
                    "desc": desc_ru[:350],
                    "date": pub_dt_msk.strftime("%d.%m.%Y %H:%M"),
                    "source": feed.feed.get("title", url.split("/")[2]),
                    "importance": importance,
                    "image_url": image_url
                })
        except Exception as e:
            print(f"Ошибка {url}: {e}")

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
            print(f"Ошибка фото: {response.text}")
            send_message(chat_id, caption)
    except Exception as e:
        print(f"Ошибка отправки фото: {e}")
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

def send_news_with_keyboard(chat_id, feed_list, count, title_message, source_type):
    send_message(chat_id, f"🔍 {title_message}\n⏳ Загружаю новости... (15-25 секунд)")

    news_list = fetch_news(feed_list, count, source_type)

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
        caption += f"📅 {news['date']} (МСК) | 📰 {news['source']}\n"
        caption += f"⭐ Важность: {news['importance']}/10\n\n"
        caption += f"🔗 [Читать полностью]({news['link']})"

        if DEEPSEEK_AVAILABLE:
            ai_analysis = analyze_with_deepseek(news['title'], news['desc'])
            caption += ai_analysis

        if news.get("image_url"):
            send_photo(chat_id, news["image_url"], caption)
        else:
            send_message(chat_id, caption)

        time.sleep(0.5)

    send_message(chat_id, f"✅ *Готово!* Показано {len(news_list)} новостей с картинками 🖼️")
    show_keyboard(chat_id)

def show_keyboard(chat_id):
    keyboard = {
        "keyboard": [
            ["🏥 Топ 7 новостей по мед. исследованиям"],
            ["💄 Топ 7 новостей косметологии"]
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False
    }

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": "🔬 *Выберите категорию новостей:*",
        "reply_markup": keyboard,
        "parse_mode": "Markdown"
    }
    requests.post(url, json=payload)

def keep_alive():
    bot_url = f"https://your-bot-name.onrender.com/health"
    while True:
        time.sleep(10 * 60)
        try:
            response = requests.get(bot_url, timeout=10)
            print(f"🔄 Auto-ping: статус {response.status_code}")
        except Exception as e:
            print(f"❌ Auto-ping ошибка: {e}")

def bot_polling():
    global last_update_id
    print("✅ Медицинский бот запущен!")
    print("📌 Доступные команды: /start")

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
                    welcome = (
                        "🏥 *Медицинский новостной бот v2.0* 🖼️🔬\n\n"
                        "📊 *Что умею:*\n"
                        "• Собираю новости из 15+ источников\n"
                        "• Оцениваю важность (1-10)\n"
                        "• Перевожу на русский\n"
                        "• Добавляю картинки к новостям\n"
                        "• Анализирую через DeepSeek AI 🧠\n\n"
                        "📌 *Доступные категории:*\n"
                        "• 🏥 Медицинские исследования\n"
                        "• 💄 Косметология\n\n"
                        "💡 Нажмите на кнопки ниже!"
                    )
                    send_message(chat_id, welcome)
                    show_keyboard(chat_id)

                elif text == "🏥 Топ 7 новостей по мед. исследованиям":
                    send_news_with_keyboard(chat_id, MEDICAL_FEEDS, 7, "🏥 *Топ-7 медицинских исследований*", "medical")

                elif text == "💄 Топ 7 новостей косметологии":
                    send_news_with_keyboard(chat_id, COSMETOLOGY_FEEDS, 7, "💄 *Топ-7 новостей косметологии*", "cosmetology")

                elif text == "/health":
                    send_message(chat_id, "✅ Бот работает нормально!")

        except Exception as e:
            print(f"Ошибка в polling: {e}")
            time.sleep(5)

@app.route('/')
def index():
    return "🏥 Медицинский новостной бот v2.0 работает!"

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        update = request.get_json()
        print(f"Webhook: {update}")
        return jsonify({"ok": True})
    except Exception as e:
        print(f"Ошибка webhook: {e}")
        return jsonify({"ok": False})

@app.route('/health')
def health():
    return "OK", 200

if __name__ == "__main__":
    ping_thread = threading.Thread(target=keep_alive, daemon=True)
    ping_thread.start()
    print("🟢 Auto-ping активирован")

    bot_thread = threading.Thread(target=bot_polling, daemon=True)
    bot_thread.start()

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
