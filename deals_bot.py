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
import json

app = Flask(__name__)
BOT_TOKEN = os.environ.get("BOT_TOKEN")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
last_update_id = 0

# Настройка DeepSeek
if DEEPSEEK_API_KEY:
    openai.api_key = DEEPSEEK_API_KEY
    openai.api_base = "https://api.deepseek.com/v1"
    DEEPSEEK_AVAILABLE = True
    print("✅ DeepSeek API подключен")
else:
    DEEPSEEK_AVAILABLE = False
    print("⚠️ DeepSeek API ключ не найден")

translator = GoogleTranslator(source='en', target='ru')

# Московское время
MOSCOW_TZ = timezone(timedelta(hours=3))

# ==================== НАСТРОЙКИ ====================
REQUEST_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/rss+xml, application/xml, text/xml, */*',
    'Accept-Language': 'en-US,en;q=0.9',
}

# ==================== РАБОЧИЕ ИСТОЧНИКИ НОВОСТЕЙ ====================
# Проверенные медицинские источники
MEDICAL_FEEDS = [
    ("Nature Medical Research", "https://www.nature.com/subjects/medical-research.rss"),
    ("News-Medical", "https://www.news-medical.net/medical-news.aspx?format=rss"),
    ("Medical News Today", "https://www.medicalnewstoday.com/feeds/all"),
    ("ScienceDaily Health", "https://www.sciencedaily.com/rss/health_medicine/all.xml"),
    ("NIH News", "https://www.nih.gov/news-events/news-releases/feed"),
    ("EurekAlert Medicine", "https://www.eurekalert.org/rss/medicine.xml"),
    ("WHO News", "https://www.who.int/rss-feeds/news-english.xml"),
    ("The Lancet", "https://www.thelancet.com/rss"),
    ("NEJM", "https://www.nejm.org/rss"),
]

# Проверенные косметологические источники
COSMETOLOGY_FEEDS = [
    ("ScienceDaily Cosmetics", "https://www.sciencedaily.com/rss/matter_energy/cosmetics.xml"),
    ("News-Medical Cosmetics", "https://www.news-medical.net/medical-news.aspx?category=Cosmetic-Medicine&format=rss"),
    ("Medical News Today Beauty", "https://www.medicalnewstoday.com/feeds/categories/beauty"),
    ("CosmeticsDesign Europe", "https://www.cosmeticsdesign-europe.com/RSS"),
    ("CosmeticsDesign Asia", "https://www.cosmeticsdesign-asia.com/RSS"),
    ("Happi Magazine", "https://www.happi.com/rss"),
    ("Personal Care Magazine", "https://www.personalcaremagazine.com/rss-news"),
    ("Dermascope", "https://www.dermascope.com/feed"),
    ("Cosmetics Business", "https://www.cosmeticsbusiness.com/rss/news"),
]

# Ключевые слова для оценки важности
IMPORTANCE_KEYWORDS = {
    "high": [
        "breakthrough", "revolutionary", "cure", "treatment", "clinical trial", 
        "fda approved", "groundbreaking", "significant discovery", "gene therapy",
        "stem cell", "innovation"
    ],
    "medium": [
        "study shows", "research", "scientists discover", "new method",
        "development", "anti-aging", "potential treatment"
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
    if not DEEPSEEK_AVAILABLE:
        return ""

    try:
        prompt = f"""Проанализируй эту медицинскую новость на русском:

Заголовок: {title}
Суть: {content[:300]}

Напиши кратко:
💡 Суть: (одно предложение)
🎯 Значение: (позитивное/нейтральное)"""

        response = openai.ChatCompletion.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=120
        )
        return f"\n\n🤖 *DeepSeek:*\n{response.choices[0].message.content}"
    except Exception as e:
        print(f"Ошибка DeepSeek: {e}")
        return ""

def extract_image_from_article(link):
    try:
        response = requests.get(link, timeout=15, headers=REQUEST_HEADERS)

        patterns = [
            r'<meta[^>]*property="og:image"[^>]*content="([^"]+)"',
            r'<meta[^>]*name="twitter:image"[^>]*content="([^"]+)"',
            r'<meta[^>]*itemprop="image"[^>]*content="([^"]+)"',
        ]

        for pattern in patterns:
            match = re.search(pattern, response.text, re.IGNORECASE)
            if match:
                img_url = match.group(1)
                if img_url.startswith('http') and 'pixel' not in img_url.lower():
                    return img_url
    except Exception as e:
        print(f"Ошибка извлечения картинки: {e}")
    return None

def get_news_image(title, category="medical"):
    """Генерирует или возвращает картинку для новости"""
    # Тематические изображения из надежных источников
    images = {
        "medical": [
            "https://cdn.pixabay.com/photo/2016/06/28/05/10/microscope-1482987_640.jpg",
            "https://cdn.pixabay.com/photo/2020/10/18/09/16/hospital-5664806_640.jpg",
            "https://cdn.pixabay.com/photo/2015/11/16/22/14/surgery-1046403_640.jpg",
            "https://cdn.pixabay.com/photo/2016/03/06/05/47/heart-1239478_640.jpg",
            "https://cdn.pixabay.com/photo/2015/09/09/16/05/brain-931968_640.jpg",
            "https://cdn.pixabay.com/photo/2012/02/24/16/50/stethoscope-166002_640.jpg",
            "https://cdn.pixabay.com/photo/2020/04/10/13/52/coronavirus-5025812_640.jpg",
            "https://cdn.pixabay.com/photo/2016/10/20/18/35/earth-1756274_640.jpg",
        ],
        "cosmetology": [
            "https://cdn.pixabay.com/photo/2016/11/29/12/54/beauty-1869540_640.jpg",
            "https://cdn.pixabay.com/photo/2017/08/07/21/31/skin-2607783_640.jpg",
            "https://cdn.pixabay.com/photo/2014/04/13/20/17/beauty-323952_640.jpg",
            "https://cdn.pixabay.com/photo/2015/10/31/12/20/face-cream-1015605_640.jpg",
            "https://cdn.pixabay.com/photo/2019/08/28/18/01/spa-4437173_640.jpg",
            "https://cdn.pixabay.com/photo/2017/01/19/19/08/cosmetics-1993549_640.jpg",
            "https://cdn.pixabay.com/photo/2016/10/27/22/57/lipstick-1776596_640.jpg",
            "https://cdn.pixabay.com/photo/2018/05/08/07/59/skincare-3382320_640.jpg",
        ]
    }
    
    # Пробуем сгенерировать AI-картинку
    try:
        if category == "cosmetology":
            prompt = f"beauty skincare cosmetics aesthetic medical treatment {title[:60]}"
        else:
            prompt = f"medical research healthcare hospital doctor treatment {title[:60]}"
        
        encoded_prompt = urllib.parse.quote(prompt)
        ai_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=768"
        return ai_url
    except:
        pass
    
    # Возвращаем случайное изображение из категории
    return random.choice(images.get(category, images["medical"]))

def fetch_news(feed_list, limit=7, category="medical"):
    articles = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=72)
    successful_sources = 0
    
    for source_name, url in feed_list:
        try:
            print(f"📡 Загружаю: {source_name} - {url}")
            
            # Загружаем RSS с правильными заголовками
            response = requests.get(url, timeout=20, headers=REQUEST_HEADERS)
            response.raise_for_status()
            
            feed = feedparser.parse(response.content)
            
            if not feed.entries:
                print(f"⚠️ {source_name}: нет записей")
                continue
            
            print(f"✅ {source_name}: найдено {len(feed.entries)} записей")
            successful_sources += 1
            
            for entry in feed.entries[:20]:
                try:
                    # Получаем дату
                    pub = entry.get("published_parsed")
                    if not pub:
                        pub = entry.get("updated_parsed")
                    if not pub:
                        pub = entry.get("date_parsed")
                    if not pub:
                        continue
                    
                    pub_dt_utc = datetime.fromtimestamp(
                        datetime(*pub[:6]).timestamp(),
                        tz=timezone.utc
                    )
                    
                    if pub_dt_utc < cutoff:
                        continue
                    
                    pub_dt_msk = pub_dt_utc.astimezone(MOSCOW_TZ)
                    
                    # Получаем заголовок и описание
                    title_en = entry.get("title", "Без заголовка")
                    desc_en = clean_html(entry.get("description", entry.get("summary", "Нет описания")))[:500]
                    link = entry.get("link", "#")
                    
                    # Оценка важности
                    importance = calculate_importance(title_en, desc_en)
                    
                    # Перевод важных новостей
                    if importance >= 3:
                        title_ru = translate_text(title_en)
                        desc_ru = translate_text(desc_en[:400])
                    else:
                        title_ru = title_en
                        desc_ru = desc_en[:400]
                    
                    # Получаем картинку
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
                    print(f"Ошибка обработки записи в {source_name}: {e}")
                    continue
                    
        except Exception as e:
            print(f"❌ Ошибка {source_name}: {e}")
    
    print(f"📊 Всего обработано источников: {successful_sources}/{len(feed_list)}")
    print(f"📰 Собрано новостей до сортировки: {len(articles)}")
    
    # Сортируем по важности и дате
    articles.sort(key=lambda x: (x["importance"], x["date"]), reverse=True)
    
    # Убираем дубликаты
    seen = set()
    unique = []
    for a in articles:
        if a["title"] not in seen:
            seen.add(a["title"])
            unique.append(a)
    
    print(f"📰 После удаления дубликатов: {len(unique)}")
    
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

def send_news_with_keyboard(chat_id, feed_list, count, title_message, category):
    send_message(chat_id, f"🔍 {title_message}\n⏳ Загружаю новости... (20-30 секунд)")

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
        caption += f"📅 {news['date']} (МСК) | 📰 {news['source']}\n"
        caption += f"⭐ Важность: {news['importance']}/10\n\n"
        caption += f"🔗 [Читать полностью]({news['link']})"

        if DEEPSEEK_AVAILABLE:
            ai_analysis = analyze_with_deepseek(news['title'], news['desc'])
            caption += ai_analysis

        send_photo(chat_id, news["image_url"], caption)
        time.sleep(0.5)

    send_message(chat_id, f"✅ *Готово!* Показано {len(news_list)} новостей с иллюстрациями 🖼️")
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
    bot_url = os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:5000") + "/health"
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
    print(f"📊 Медицинских источников: {len(MEDICAL_FEEDS)}")
    print(f"💄 Косметологических источников: {len(COSMETOLOGY_FEEDS)}")

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
                        "🏥 *Медицинский новостной бот v4.0* 🖼️🔬\n\n"
                        "📊 *Что умею:*\n"
                        "• Собираю новости из 15+ медицинских источников\n"
                        "• Оцениваю важность исследований (1-10)\n"
                        "• Перевожу на русский язык\n"
                        "• Добавляю иллюстрации к новостям\n"
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
    return "🏥 Медицинский новостной бот v4.0 работает!"

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        update = request.get_json()
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
