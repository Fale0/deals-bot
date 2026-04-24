import os
import feedparser
import re
from datetime import datetime, timedelta, timezone
import time
import requests
from flask import Flask, request, jsonify
import threading
from deep_translator import GoogleTranslator
import random

app = Flask(__name__)
BOT_TOKEN = os.environ.get("BOT_TOKEN")
last_update_id = 0

# DeepSeek временно ОТКЛЮЧЁН из-за нулевого баланса
DEEPSEEK_AVAILABLE = False

# Подключаем переводчик (он бесплатный)
translator = GoogleTranslator(source='en', target='ru')

# Московское время
MOSCOW_TZ = timezone(timedelta(hours=3))

# ------------------- ИСТОЧНИКИ НОВОСТЕЙ -------------------
# Медицина (научные исследования)
MEDICAL_FEEDS = [
    ("Nature Medicine", "https://www.nature.com/subjects/medical-research.rss"),
    ("WHO News", "https://www.who.int/rss-feeds/news-english.xml"),
    ("ScienceDaily Health", "https://www.sciencedaily.com/rss/health_medicine/all.xml"),
]

# Косметология – используем те же научные источники, но фильтруем по ключевым словам
# (так как специализированные RSS почти все недоступны)
COSMETOLOGY_SOURCES = MEDICAL_FEEDS  # переиспользуем источники

# Ключевые слова для отбора НАУЧНЫХ косметологических новостей
COSMETOLOGY_KEYWORDS = [
    # на русском и английском
    "cosmetic", "beauty", "skin", "dermatology", "anti-aging", "wrinkle",
    "collagen", "hyaluronic acid", "retinol", "peptide", "antioxidant",
    "sunscreen", "melasma", "acne", "rosacea", "psoriasis", "eczema",
    "cosmeceutical", "aesthetic medicine", "laser treatment", "botox",
    "filler", "microneedling", "chemical peel", "regenerative medicine",
    "косметология", "красота", "кожа", "дерматология", "антивозрастной",
    "морщины", "коллаген", "гиалуроновая кислота", "ретинол", "пептид",
    "антиоксидант", "солнцезащитный", "мелазма", "акне", "розацеа",
    "псориаз", "экзема", "космецевтика", "эстетическая медицина",
]

# ------------------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ -------------------
def clean_html(raw):
    if not raw:
        return ""
    return re.sub(r'<.*?>', '', raw)

def translate_text(text):
    if not text or len(text.strip()) < 5:
        return text
    try:
        # ограничим длину для перевода
        text_to_translate = text[:4000] if len(text) > 4000 else text
        return translator.translate(text_to_translate)
    except Exception as e:
        print(f"Ошибка перевода: {e}")
        return text

def calculate_importance(title, description, category="medical"):
    """Оценка важности новости (1-10)"""
    text = (title + " " + description).lower()
    score = 5
    # Высокая важность
    high = ["breakthrough", "cure", "treatment", "clinical trial", "fda approved", "groundbreaking", "discovery"]
    for kw in high:
        if kw in text:
            score += 2
    # Средняя важность
    medium = ["study shows", "research", "scientists", "new method", "development", "potential", "promising"]
    for kw in medium:
        if kw in text:
            score += 1
    # Бонус для медицинских новостей
    if category == "medical":
        if "cancer" in text or "tumor" in text:
            score += 1
        if "gene therapy" in text or "stem cell" in text:
            score += 1
    return min(10, max(1, score))

def is_cosmetology_news(title, description):
    """Проверяет, относится ли новость к косметологии/уходу за кожей (по ключевым словам)"""
    text = (title + " " + description).lower()
    # проверяем наличие хотя бы двух ключевых слов для надёжности
    matches = sum(1 for kw in COSMETOLOGY_KEYWORDS if kw in text)
    return matches >= 2

def get_news_image(title, category="medical"):
    """Надёжные изображения – только из Pixabay (без AI, без внешних запросов)"""
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
    """Загружает новости из RSS, фильтрует и оценивает важность"""
    articles = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=72)

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
            for entry in feed.entries[:30]:
                try:
                    # Дата публикации
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

                    # Фильтрация для косметологии
                    if category == "cosmetology" and not is_cosmetology_news(title_en, desc_en):
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

    # Сортировка по важности и дате
    articles.sort(key=lambda x: (x["importance"], x["date"]), reverse=True)
    # Удаление дубликатов по заголовку
    seen = set()
    unique = []
    for a in articles:
        if a["title"] not in seen:
            seen.add(a["title"])
            unique.append(a)
    print(f"📊 {category}: отобрано {len(unique)} новостей")
    return unique[:limit]

# ------------------- ФУНКЦИИ ТЕЛЕГРАМ -------------------
def send_photo(chat_id, image_url, caption):
    """Отправляет фото с fallback на текст, если фото недоступно"""
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
            # Если фото не отправилось, шлём просто текст
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
        print(f"Ошибка отправки сообщения: {e}")

def send_news_with_keyboard(chat_id, feed_list, count, title_message, category):
    send_message(chat_id, f"🔍 {title_message}\n⏳ Загружаю новости... (около 20 секунд)")

    news_list = fetch_news(feed_list, count, category)

    if not news_list:
        send_message(chat_id, "😕 *Новости не найдены*\n\nПопробуйте позже.")
        show_keyboard(chat_id)
        return

    for idx, news in enumerate(news_list, 1):
        # Эмодзи важности
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
        "resize_keyboard": True
    }
    payload = {
        "chat_id": chat_id,
        "text": "🔬 *Выберите категорию:*",
        "reply_markup": keyboard,
        "parse_mode": "Markdown"
    }
    requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json=payload)

# ------------------- ФОНОВЫЕ ЗАДАЧИ -------------------
def keep_alive():
    """Пинг самого себя, чтобы сервис не засыпал (для Render)"""
    while True:
        time.sleep(10 * 60)
        try:
            url = os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:5000") + "/health"
            requests.get(url, timeout=10)
        except:
            pass

def bot_polling():
    global last_update_id
    print("✅ Бот запущен. DeepSeek отключён (нет баланса).")
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
                        "🏥 *Медицинско-косметологический бот* 🖼️🔬\n\n"
                        "📊 *Возможности:*\n"
                        "• Новости медицинских исследований (Nature, WHO, ScienceDaily)\n"
                        "• Новости косметологии (отфильтрованные из научных источников)\n"
                        "• Оценка важности (1–10)\n"
                        "• Перевод на русский\n"
                        "• Иллюстрации из Pixabay\n\n"
                        "⚠️ *DeepSeek AI временно отключён* (пополните баланс на сайте DeepSeek)\n\n"
                        "👇 *Нажмите на кнопку ниже*"
                    )
                    send_message(chat_id, welcome)
                    show_keyboard(chat_id)
                elif "мед. исследованиям" in text:
                    send_news_with_keyboard(chat_id, MEDICAL_FEEDS, 7, "🏥 *Топ‑7 медицинских исследований*", "medical")
                elif "косметологии" in text:
                    # Для косметологии используем те же источники, но с фильтром
                    send_news_with_keyboard(chat_id, COSMETOLOGY_SOURCES, 7, "💄 *Топ‑7 научных новостей косметологии*", "cosmetology")
                elif text == "/health":
                    send_message(chat_id, "✅ Бот работает. DeepSeek не активен.")
        except Exception as e:
            print(f"Ошибка polling: {e}")
            time.sleep(5)

# ------------------- ЗАПУСК -------------------
@app.route('/')
def index():
    return "Бот работает (DeepSeek отключён)"

@app.route('/health')
def health():
    return "OK", 200

if __name__ == "__main__":
    # Запускаем авто-пинг
    threading.Thread(target=keep_alive, daemon=True).start()
    # Запускаем polling
    threading.Thread(target=bot_polling, daemon=True).start()
    # Запускаем Flask
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
