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
from apscheduler.schedulers.background import BackgroundScheduler

# Создаём Flask приложение ДО ВСЕГО
app = Flask(__name__)

# --- Глобальные переменные ---
last_update_id = 0
scheduler_started = False

# --- Конфигурация окружения ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
CHANNEL_ID = os.environ.get("CHANNEL_ID")
CHECK_INTERVAL_HOURS = int(os.environ.get("CHECK_INTERVAL_HOURS", 6))

# --- Настройка DeepSeek ---
if DEEPSEEK_API_KEY:
    openai.api_key = DEEPSEEK_API_KEY
    openai.api_base = "https://api.deepseek.com/v1"
    DEEPSEEK_AVAILABLE = True
    print("✅ DeepSeek API подключен", flush=True)
else:
    DEEPSEEK_AVAILABLE = False
    print("⚠️ DeepSeek API ключ не найден", flush=True)

translator = GoogleTranslator(source='en', target='ru')
MOSCOW_TZ = timezone(timedelta(hours=3))

# --- Источники RSS ---
RSS_FEEDS = [
    {"url": "https://brobank.ru/promo/feed/", "name": "BroBank", "category": "bank"},
    {"url": "https://www.ixbt.com/export/rss/lastnews.xml", "name": "iXBT", "category": "electronics"},
    {"url": "https://3dnews.ru/news/rss/", "name": "3DNews", "category": "electronics"},
    {"url": "https://promokodus.com/rss", "name": "Promokodus", "category": "marketplace"},
    {"url": "https://vc.ru/feed", "name": "VC.ru", "category": "lifehack"},
]

# --- Ключевые слова для оценки выгодности ---
IMPORTANCE_KEYWORDS = {
    "high": [
        "скидка", "акция", "кешбэк", "cashback", "промокод", "купон", "бесплатно",
        "распродажа", "выгода", "бонус", "спецпредложение", "халява", "экономия",
        "подарок", "кэшбэк"
    ],
    "medium": [
        "партнёр", "партнер", "льгота", "привилегия", "скидочный", "акционный"
    ],
}

# ==================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ====================

def clean_html(raw):
    if not raw:
        return ""
    return re.sub(r'<.*?>', '', raw)

def calculate_importance(title, description):
    """Оценка выгодности предложения от 1 до 10"""
    text = (title + " " + description).lower()
    score = 5
    for kw in IMPORTANCE_KEYWORDS["high"]:
        if kw in text:
            score += 2
    for kw in IMPORTANCE_KEYWORDS["medium"]:
        if kw in text:
            score += 1
    if "ozon" in text or "wildberries" in text or "яндекс маркет" in text:
        score += 1
    if "банк" in text or "карт" in text:
        score += 1
    return min(10, max(1, score))

def translate_text(text):
    if not text or len(text.strip()) < 5:
        return text
    try:
        text_to_translate = text[:4000] if len(text) > 4000 else text
        return translator.translate(text_to_translate)
    except Exception as e:
        print(f"Ошибка перевода: {e}", flush=True)
        return text

def analyze_with_deepseek(title, content):
    """Анализ выгоды через DeepSeek AI"""
    if not DEEPSEEK_AVAILABLE:
        return ""
    try:
        prompt = f"""Ты — эксперт по скидкам и акциям. Проанализируй это предложение кратко.

Заголовок: {title}
Описание: {content[:300]}

Ответь кратко в формате:
💎 {title[:50]}...
💰 Выгода: (кратко)"""
        response = openai.ChatCompletion.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=100
        )
        return f"\n\n🤖 *AI-анализ:*\n{response.choices[0].message.content}"
    except Exception as e:
        print(f"Ошибка DeepSeek: {e}", flush=True)
        return ""

def extract_image_from_article(link):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = requests.get(link, timeout=10, headers=headers)
        patterns = [
            r'<meta[^>]*property="og:image"[^>]*content="([^"]+)"',
            r'<meta[^>]*name="twitter:image"[^>]*content="([^"]+)"',
        ]
        for pattern in patterns:
            match = re.search(pattern, response.text, re.IGNORECASE)
            if match:
                img_url = match.group(1)
                if img_url.startswith('http'):
                    return img_url
    except Exception:
        pass
    return None

def generate_ai_image(title):
    try:
        prompt = f"shopping discount sale, {title[:50]}"
        encoded_prompt = urllib.parse.quote(prompt)
        image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=512&height=512&nologo=true"
        return image_url
    except Exception:
        return "https://i.imgur.com/Xr5Kq9M.png"

def get_news_image(link, title):
    image_url = extract_image_from_article(link)
    if not image_url:
        image_url = generate_ai_image(title)
    return image_url

def fetch_deals(hours=24, limit=5, categories=None):
    """Сбор предложений за указанное количество часов"""
    if categories is None:
        categories = ["bank", "electronics", "marketplace"]
    
    articles = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    
    for feed_conf in RSS_FEEDS:
        if feed_conf["category"] not in categories:
            continue
        try:
            print(f"Загружаю: {feed_conf['url']}", flush=True)
            feed = feedparser.parse(feed_conf["url"])
            for entry in feed.entries[:10]:
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
                desc_en = clean_html(entry.get("description", ""))[:300]
                link = entry.get("link", "#")
                importance = calculate_importance(title_en, desc_en)
                if importance < 3:
                    continue
                title_ru = translate_text(title_en)
                desc_ru = translate_text(desc_en[:200])
                image_url = get_news_image(link, title_en)
                articles.append({
                    "title": title_ru,
                    "link": link,
                    "desc": desc_ru,
                    "date": pub_dt_msk.strftime("%d.%m %H:%M"),
                    "source": feed_conf["name"],
                    "importance": importance,
                    "image_url": image_url
                })
        except Exception as e:
            print(f"Ошибка {feed_conf['url']}: {e}", flush=True)
    
    articles.sort(key=lambda x: x["importance"], reverse=True)
    
    # Удаляем дубликаты
    seen = set()
    unique = []
    for a in articles:
        if a["title"][:50] not in seen:
            seen.add(a["title"][:50])
            unique.append(a)
    
    return unique[:limit]

def send_message(chat_id, text, parse_mode="Markdown"):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }
        response = requests.post(url, json=payload, timeout=30)
        print(f"Отправка сообщения: статус {response.status_code}", flush=True)
        if response.status_code != 200:
            print(f"Ошибка: {response.text}", flush=True)
    except Exception as e:
        print(f"Ошибка отправки: {e}", flush=True)

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
        send_message(chat_id, caption)

def show_main_keyboard(chat_id):
    """Показывает главное меню с двумя кнопками"""
    keyboard = {
        "keyboard": [
            ["🔥 ТОП-5 СКИДОК И АКЦИЙ (24ч)"],
            ["💡 ТОП-3 ЛАЙФХАКА (48ч)"]
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False
    }
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": "👇 *Выберите действие:*",
        "reply_markup": keyboard,
        "parse_mode": "Markdown"
    }
    requests.post(url, json=payload, timeout=30)

def auto_post_to_channel():
    """Автоматическая рассылка в канал"""
    if not CHANNEL_ID:
        print("⚠️ CHANNEL_ID не задан", flush=True)
        return
    print("🔄 Авто-рассылка...", flush=True)
    deals = fetch_deals(hours=24, limit=5, categories=["bank", "electronics", "marketplace"])
    if deals:
        send_message(CHANNEL_ID, "🛍 *ЛУЧШИЕ СКИДКИ ЗА 24 ЧАСА*")
        for deal in deals:
            caption = f"🔥 *{deal['title']}*\n\n📝 {deal['desc']}\n\n⭐ Выгодность: {deal['importance']}/10\n🔗 [Подробнее]({deal['link']})"
            if deal.get("image_url"):
                send_photo(CHANNEL_ID, deal["image_url"], caption)
            else:
                send_message(CHANNEL_ID, caption)
            time.sleep(1)

def cold_start():
    if CHANNEL_ID:
        print("❄️ Холодный старт...", flush=True)
        auto_post_to_channel()

def start_scheduler():
    global scheduler_started
    if scheduler_started:
        return
    scheduler = BackgroundScheduler()
    scheduler.add_job(auto_post_to_channel, 'interval', hours=CHECK_INTERVAL_HOURS)
    scheduler.start()
    scheduler_started = True
    print(f"⏰ Планировщик: каждые {CHECK_INTERVAL_HOURS} ч.", flush=True)

def keep_alive():
    while True:
        time.sleep(10 * 60)
        try:
            requests.get("http://localhost:10000/health", timeout=10)
        except:
            pass

def bot_polling():
    global last_update_id
    print("🤖 Бот запущен!", flush=True)
    
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
                
                print(f"Получено сообщение: '{text}' от {chat_id}", flush=True)
                
                # Обработка команд
                if text == "/start":
                    send_message(chat_id, 
                        "🛍 *БОТ СКИДОК И АКЦИЙ*\n\n"
                        "🔹 ТОП-5 скидок за 24 часа (банки, электроника, маркетплейсы)\n"
                        "🔹 ТОП-3 лайфхака за 48 часов\n\n"
                        "👇 *Выберите кнопку:*"
                    )
                    show_main_keyboard(chat_id)
                
                elif text == "🔥 ТОП-5 СКИДОК И АКЦИЙ (24ч)":
                    send_message(chat_id, "🔍 *Ищу лучшие скидки за 24 часа...*")
                    deals = fetch_deals(hours=24, limit=5, categories=["bank", "electronics", "marketplace"])
                    if deals:
                        send_message(chat_id, f"✅ *Найдено {len(deals)} выгодных предложений:*")
                        for i, deal in enumerate(deals, 1):
                            caption = f"*{i}. {deal['title']}*\n\n📝 {deal['desc']}\n\n📅 {deal['date']} | ⭐ {deal['importance']}/10\n🔗 [Подробнее]({deal['link']})"
                            if DEEPSEEK_AVAILABLE:
                                caption += analyze_with_deepseek(deal['title'], deal['desc'])
                            if deal.get("image_url"):
                                send_photo(chat_id, deal["image_url"], caption)
                            else:
                                send_message(chat_id, caption)
                            time.sleep(1)
                    else:
                        send_message(chat_id, "😕 Пока нет подходящих акций. Попробуйте позже.")
                    show_main_keyboard(chat_id)
                
                elif text == "💡 ТОП-3 ЛАЙФХАКА (48ч)":
                    send_message(chat_id, "🔍 *Ищу лайфхаки за 48 часов...*")
                    deals = fetch_deals(hours=48, limit=3, categories=["lifehack"])
                    if deals:
                        send_message(chat_id, f"✅ *Найдено {len(deals)} лайфхаков:*")
                        for i, deal in enumerate(deals, 1):
                            caption = f"*{i}. {deal['title']}*\n\n📝 {deal['desc']}\n\n📅 {deal['date']}\n🔗 [Читать]({deal['link']})"
                            if DEEPSEEK_AVAILABLE:
                                caption += analyze_with_deepseek(deal['title'], deal['desc'])
                            send_message(chat_id, caption)
                            time.sleep(1)
                    else:
                        send_message(chat_id, "😕 Лайфхаков пока нет.")
                    show_main_keyboard(chat_id)
                
                else:
                    # На любой другой текст показываем клавиатуру
                    show_main_keyboard(chat_id)
                    
        except Exception as e:
            print(f"Ошибка polling: {e}", flush=True)
            time.sleep(5)

def start_background_tasks():
    print("🔄 Запуск фоновых задач...", flush=True)
    if CHANNEL_ID:
        threading.Thread(target=cold_start, daemon=True).start()
    start_scheduler()
    threading.Thread(target=keep_alive, daemon=True).start()
    if BOT_TOKEN:
        threading.Thread(target=bot_polling, daemon=True).start()
        print("✅ Telegram бот запущен", flush=True)

# ==================== FLASK МАРШРУТЫ ====================

@app.route('/')
def index():
    return "🛍 Бот скидок работает!"

@app.route('/health')
def health():
    return "OK", 200

# ==================== ТОЧКА ВХОДА ====================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    threading.Timer(3.0, start_background_tasks).start()
    print(f"🌐 Flask на порту {port}", flush=True)
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
