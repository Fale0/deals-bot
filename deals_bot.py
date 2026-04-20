import os
import time
import html
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

app = Flask(__name__)

# --- Конфигурация окружения ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
CHANNEL_ID = os.environ.get("CHANNEL_ID")  # для холодного старта и авто-рассылки
CHECK_INTERVAL_HOURS = int(os.environ.get("CHECK_INTERVAL_HOURS", 6))  # периодичность авто-рассылки

last_update_id = 0

# --- Настройка DeepSeek ---
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

# ==================== ИСТОЧНИКИ RSS ====================
RSS_FEEDS = [
    # Банки
    {
        "url": "https://brobank.ru/promo/feed/",
        "name": "BroBank (Акции банков)",
        "category": "🏦 Банки"
    },
    # Электроника
    {
        "url": "https://www.ixbt.com/export/rss/lastnews.xml",
        "name": "iXBT (Новости электроники)",
        "category": "💻 Электроника"
    },
    {
        "url": "https://3dnews.ru/news/rss/",
        "name": "3DNews (IT-скидки)",
        "category": "💻 Электроника"
    },
    # Маркетплейсы и купоны
    {
        "url": "https://promokodus.com/rss",
        "name": "Promokodus (Промокоды)",
        "category": "🛒 Маркетплейсы"
    },
    # Лайфхаки
    {
        "url": "https://vc.ru/feed",
        "name": "VC.ru (Лайфхаки и финансы)",
        "category": "💡 Лайфхаки"
    },
]

# --- Ключевые слова для оценки выгодности ---
IMPORTANCE_KEYWORDS = {
    "high": [
        "скидка", "акция", "кешбэк", "cashback", "промокод", "купон", "бесплатно",
        "распродажа", "выгода", "бонус", "спецпредложение", "халява", "экономия",
        "подарок", "кэшбэк"
    ],
    "medium": [
        "партнёр", "партнер", "льгота", "привелегия", "скидочный", "акционный"
    ],
}

def clean_html(raw):
    return re.sub(r'<.*?>', '', raw)

def calculate_importance(title, description):
    """Оценка выгодности предложения от 1 до 10"""
    text = (title + " " + description).lower()
    score = 5  # базовая оценка
    for kw in IMPORTANCE_KEYWORDS["high"]:
        if kw in text:
            score += 2
    for kw in IMPORTANCE_KEYWORDS["medium"]:
        if kw in text:
            score += 1
    # Упоминания конкретных площадок повышают вес
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
        print(f"Ошибка перевода: {e}")
        return text

def analyze_with_deepseek(title, content):
    """Анализ выгоды через DeepSeek AI"""
    if not DEEPSEEK_AVAILABLE:
        return ""

    try:
        prompt = f"""Ты — эксперт по скидкам и акциям. Проанализируй это предложение и выдели самое важное.

Заголовок: {title}
Описание: {content[:400]}

Ответь в формате:
💎 Суть акции: (одно предложение)
💰 Экономия: (примерная выгода в рублях или процентах, если указано)
⏳ Сроки: (если указаны, иначе "не указаны")
⚠️ Важные условия: (если есть подводные камни)"""

        response = openai.ChatCompletion.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=200
        )
        return f"\n\n🤖 *DeepSeek AI:*\n{response.choices[0].message.content}"
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
            r'<img[^>]*src="([^"]+)"[^>]*class="[^"]*featured[^"]*"',
        ]
        for pattern in patterns:
            match = re.search(pattern, response.text, re.IGNORECASE)
            if match:
                img_url = match.group(1)
                if img_url.startswith('http'):
                    return img_url
    except Exception as e:
        print(f"Ошибка извлечения картинки: {e}")
    return None

def generate_ai_image(title):
    try:
        prompt = f"shopping discount sale, {title[:80]}"
        encoded_prompt = urllib.parse.quote(prompt)
        image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1024&height=768&nologo=true"
        response = requests.head(image_url, timeout=10)
        if response.status_code == 200:
            return image_url
    except Exception as e:
        print(f"Ошибка генерации AI картинки: {e}")

    # fallback тематические картинки
    theme_images = {
        "bank": "https://i.imgur.com/8qD4q4M.png",
        "electronics": "https://i.imgur.com/Kp4zq8Z.png",
        "marketplace": "https://i.imgur.com/2nJqj7L.png",
        "lifehack": "https://i.imgur.com/YxqJ5jK.png",
        "default": "https://i.imgur.com/Xr5Kq9M.png"
    }
    title_lower = title.lower()
    if any(w in title_lower for w in ["банк", "кредит", "ипотека"]):
        return theme_images["bank"]
    elif any(w in title_lower for w in ["телефон", "ноутбук", "гаджет", "электроник"]):
        return theme_images["electronics"]
    elif any(w in title_lower for w in ["ozon", "wildberries", "маркетплейс", "промокод"]):
        return theme_images["marketplace"]
    elif any(w in title_lower for w in ["лайфхак", "экономия", "секрет"]):
        return theme_images["lifehack"]
    return theme_images["default"]

def get_news_image(link, title):
    image_url = extract_image_from_article(link)
    if not image_url:
        image_url = generate_ai_image(title)
    return image_url

def fetch_deals(feed_list=None, limit=5, category_filter=None):
    """Сбор предложений из RSS с фильтрацией и сортировкой по выгодности"""
    if feed_list is None:
        feed_list = RSS_FEEDS

    articles = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)

    for feed_conf in feed_list:
        try:
            print(f"Загружаю: {feed_conf['url']}")
            feed = feedparser.parse(feed_conf["url"])
            for entry in feed.entries[:15]:
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
                if importance < 4:  # пропускаем слабые предложения
                    continue

                title_ru = translate_text(title_en)
                desc_ru = translate_text(desc_en[:400])

                image_url = None
                if 'media_content' in entry and entry.media_content:
                    image_url = entry.media_content[0].get('url')
                if not image_url:
                    image_url = get_news_image(link, title_en)

                articles.append({
                    "title": title_ru,
                    "title_en": title_en,
                    "link": link,
                    "desc": desc_ru[:350],
                    "date": pub_dt_msk.strftime("%d.%m.%Y %H:%M"),
                    "source": feed.feed.get("title", feed_conf["name"]),
                    "category": feed_conf["category"],
                    "importance": importance,
                    "image_url": image_url
                })
        except Exception as e:
            print(f"Ошибка {feed_conf['url']}: {e}")

    articles.sort(key=lambda x: (x["importance"], x["date"]), reverse=True)

    # Удаление дубликатов
    seen = set()
    unique = []
    for a in articles:
        if a["title"] not in seen:
            seen.add(a["title"])
            unique.append(a)

    # Фильтр по категории, если задан
    if category_filter:
        unique = [a for a in unique if a["category"] == category_filter]

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

def send_deals_batch(chat_id, deals, title_message):
    """Отправка пачки предложений с картинками и AI-анализом"""
    send_message(chat_id, f"🔍 {title_message}\n⏳ Загружаю предложения... (15-25 секунд)")

    if not deals:
        send_message(chat_id, "😕 *Актуальных акций не найдено*\nПопробуйте позже.")
        show_keyboard(chat_id)
        return

    for idx, deal in enumerate(deals, 1):
        if deal["importance"] >= 8:
            imp_emoji = "🔥"
        elif deal["importance"] >= 6:
            imp_emoji = "💰"
        else:
            imp_emoji = "📌"

        caption = f"{imp_emoji} *{idx}. {deal['title']}*\n\n"
        caption += f"📝 {deal['desc']}\n\n"
        caption += f"📅 {deal['date']} (МСК) | 📰 {deal['source']}\n"
        caption += f"⭐ Выгодность: {deal['importance']}/10\n\n"
        caption += f"🔗 [Подробнее]({deal['link']})"

        if DEEPSEEK_AVAILABLE:
            ai_analysis = analyze_with_deepseek(deal['title'], deal['desc'])
            caption += ai_analysis

        if deal.get("image_url"):
            send_photo(chat_id, deal["image_url"], caption)
        else:
            send_message(chat_id, caption)

        time.sleep(0.5)

    send_message(chat_id, f"✅ *Готово!* Показано {len(deals)} выгодных предложений.")
    show_keyboard(chat_id)

def show_keyboard(chat_id):
    keyboard = {
        "keyboard": [
            ["🔥 Топ-3 выгоды", "📋 Топ-5 выгоды"],
            ["🏦 Банки", "💻 Электроника"],
            ["🛒 Маркетплейсы", "💡 Лайфхаки"]
        ],
        "resize_keyboard": True,
        "one_time_keyboard": False
    }
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": "📱 *Выбери категорию:*",
        "reply_markup": keyboard,
        "parse_mode": "Markdown"
    }
    requests.post(url, json=payload)

# ========== Автоматическая рассылка в канал ==========
def auto_post_to_channel():
    """Функция для планировщика — отправляет подборку в канал"""
    if not CHANNEL_ID:
        print("⚠️ CHANNEL_ID не задан, авто-рассылка отключена")
        return
    print("🔄 Авто-рассылка: ищу свежие акции...")
    deals = fetch_deals(limit=5)
    if deals:
        send_deals_batch(CHANNEL_ID, deals, "🛍 *Автоматическая подборка лучших акций и скидок*")
    else:
        send_message(CHANNEL_ID, "Сегодня новых супер-выгодных предложений не найдено 😕")

def cold_start():
    """Холодный старт — отправка в канал при запуске бота"""
    if CHANNEL_ID:
        print("❄️ Холодный старт: отправляю первую подборку...")
        auto_post_to_channel()
    else:
        print("ℹ️ CHANNEL_ID не указан, холодный старт пропущен")

def start_scheduler():
    scheduler = BackgroundScheduler()
    scheduler.add_job(auto_post_to_channel, 'interval', hours=CHECK_INTERVAL_HOURS)
    scheduler.start()
    print(f"⏰ Планировщик активирован: рассылка каждые {CHECK_INTERVAL_HOURS} ч.")

# ========== Keep Alive ==========
def keep_alive():
    bot_url = f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME', 'localhost')}/health"
    while True:
        time.sleep(10 * 60)
        try:
            response = requests.get(bot_url, timeout=10)
            print(f"🔄 Auto-ping: статус {response.status_code}")
        except Exception as e:
            print(f"❌ Auto-ping ошибка: {e}")

# ========== Polling ==========
def bot_polling():
    global last_update_id
    print("✅ Бот скидок запущен (DeepSeek AI + картинки)")
    print("📌 Команды: /start, /deals3, /deals5, /bank, /electronics, /marketplaces, /lifehacks")

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
                        "🛍 *Бот скидок и акций v1.0*\n\n"
                        "📊 *Что я умею:*\n"
                        "• Мониторю скидки, купоны, акции банков и магазинов\n"
                        "• **Оцениваю выгодность** (от 1 до 10)\n"
                        "• Перевожу на русский (если нужно)\n"
                        "• Генерирую AI-картинки 🖼️\n"
                        "• **Анализирую каждую акцию через DeepSeek AI** 🧠\n\n"
                        "📌 *Методология выгодности:*\n"
                        "• Базовая оценка: 5/10\n"
                        "• +2 за слова: скидка, акция, кешбэк, промокод, бесплатно\n"
                        "• +1 за упоминание популярных маркетплейсов\n\n"
                        "📌 *Команды:*\n"
                        "• `/deals3` — топ-3 выгодных предложения\n"
                        "• `/deals5` — топ-5 выгодных предложений\n"
                        "• `/bank` — акции банков\n"
                        "• `/electronics` — скидки на электронику\n"
                        "• `/marketplaces` — промокоды и купоны\n"
                        "• `/lifehacks` — лайфхаки экономии\n\n"
                        "⏰ Новости за последние 48 часов (МСК)\n"
                        "♻️ *Бот работает 24/7*\n\n"
                        "💡 Используй кнопки ниже!"
                    )
                    send_message(chat_id, welcome)
                    show_keyboard(chat_id)

                elif text in ["/deals3", "🔥 Топ-3 выгоды"]:
                    deals = fetch_deals(limit=3)
                    send_deals_batch(chat_id, deals, "🔥 *Топ-3 самые выгодные предложения*")

                elif text in ["/deals5", "📋 Топ-5 выгоды"]:
                    deals = fetch_deals(limit=5)
                    send_deals_batch(chat_id, deals, "📋 *Топ-5 самых выгодных предложений*")

                elif text in ["/bank", "🏦 Банки"]:
                    deals = fetch_deals(limit=5, category_filter="🏦 Банки")
                    send_deals_batch(chat_id, deals, "🏦 *Акции и бонусы банков*")

                elif text in ["/electronics", "💻 Электроника"]:
                    deals = fetch_deals(limit=5, category_filter="💻 Электроника")
                    send_deals_batch(chat_id, deals, "💻 *Скидки на электронику и гаджеты*")

                elif text in ["/marketplaces", "🛒 Маркетплейсы"]:
                    deals = fetch_deals(limit=5, category_filter="🛒 Маркетплейсы")
                    send_deals_batch(chat_id, deals, "🛒 *Промокоды и купоны маркетплейсов*")

                elif text in ["/lifehacks", "💡 Лайфхаки"]:
                    deals = fetch_deals(limit=5, category_filter="💡 Лайфхаки")
                    send_deals_batch(chat_id, deals, "💡 *Лайфхаки для экономии*")

                elif text == "/health":
                    send_message(chat_id, "✅ Бот работает!\n🕒 Московское время\n♻️ Авто-рассылка активна\n🧠 DeepSeek AI подключен")

        except Exception as e:
            print(f"Ошибка polling: {e}")
            time.sleep(5)

# ========== Flask ==========
@app.route('/')
def index():
    return "🛍 Бот скидок и акций v1.0 (DeepSeek AI + картинки) работает!"

@app.route('/health')
def health():
    return "OK", 200

if __name__ == "__main__":
    # Холодный старт
    cold_start()

    # Запуск планировщика
    start_scheduler()

    # Auto-ping в отдельном потоке
    ping_thread = threading.Thread(target=keep_alive, daemon=True)
    ping_thread.start()
    print("🟢 Auto-ping активирован (каждые 10 минут)")

    # Polling в отдельном потоке
    bot_thread = threading.Thread(target=bot_polling, daemon=True)
    bot_thread.start()

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    
    # 1. ЗАПУСКАЕМ FLASK В ОСНОВНОМ ПОТОКЕ (блокирующий)
    # Но перед этим запускаем всё остальное в отдельных потоках
    
    # Холодный старт (в отдельном потоке, чтобы не блокировать Flask)
    threading.Thread(target=cold_start, daemon=True).start()
    
    # Планировщик (в отдельном потоке)
    threading.Thread(target=start_scheduler, daemon=True).start()
    
    # Auto-ping (в отдельном потоке)
    threading.Thread(target=keep_alive, daemon=True).start()
    print("🟢 Auto-ping активирован")
    
    # Telegram бот (в отдельном потоке)
    threading.Thread(target=bot_polling, daemon=True).start()
    print("🤖 Telegram бот запущен в фоне")
    
    # 2. FLASK ЗАПУСКАЕТСЯ ПОСЛЕДНИМ В ОСНОВНОМ ПОТОКЕ
    # Это гарантирует, что порт откроется сразу
    print(f"🌐 Запуск Flask на порту {port}...")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
