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

app = Flask(__name__)

last_update_id = 0
scheduler_started = False

# --- Конфигурация ---
BOT_TOKEN = os.environ.get("BOT_TOKEN")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
CHANNEL_ID = os.environ.get("CHANNEL_ID")
CHECK_INTERVAL_HOURS = int(os.environ.get("CHECK_INTERVAL_HOURS", 6))

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

# ==================== ИСТОЧНИКИ RSS ====================
# Категории: bank, electronics, marketplace, lifehack

RSS_FEEDS = [
    # БАНКИ - акции и кешбэк
    {
        "url": "https://brobank.ru/promo/feed/",
        "name": "BroBank",
        "category": "bank"
    },
    {
        "url": "https://www.banki.ru/news/rss/",
        "name": "Banki.ru",
        "category": "bank"
    },
    # ЭЛЕКТРОНИКА - сайты со скидками
    {
        "url": "https://www.ixbt.com/export/rss/lastnews.xml",
        "name": "iXBT",
        "category": "electronics"
    },
    {
        "url": "https://3dnews.ru/news/rss/",
        "name": "3DNews",
        "category": "electronics"
    },
    # МАРКЕТПЛЕЙСЫ - промокоды и купоны
    {
        "url": "https://promokodus.com/rss",
        "name": "Promokodus",
        "category": "marketplace"
    },
    {
        "url": "https://promokod.party/rss",
        "name": "Promokod.party",
        "category": "marketplace"
    },
    # ЛАЙФХАКИ
    {
        "url": "https://lifehacker.ru/rss/",
        "name": "Lifehacker.ru",
        "category": "lifehack"
    },
    {
        "url": "https://vc.ru/feed",
        "name": "VC.ru",
        "category": "lifehack"
    },
]

# ==================== КЛЮЧЕВЫЕ СЛОВА ДЛЯ ФИЛЬТРАЦИИ ====================
# Если новость НЕ содержит эти слова - она не попадёт в выдачу

DEAL_KEYWORDS = [
    # Скидки и акции
    "скидка", "скидки", "акция", "акции", "промокод", "промо", "купон",
    "распродажа", "sale", "выгода", "выгод", "дешев", "цена снижен",
    "кешбэк", "cashback", "кэшбэк", "бонус", "подарок",
    # Банковские продукты
    "кредит", "ипотека", "вклад", "карт", "дебетов", "кредитн",
    "накопительн", "счет", "процент", "ставка", "рефинансирование",
    # Электроника
    "смартфон", "iphone", "айфон", "samsung", "ноутбук", "телевизор",
    "наушники", "планшет", "гаджет", "умные часы", "фитнес-браслет",
    # Маркетплейсы
    "ozon", "озон", "wildberries", "вайлдберриз", "алиэкспресс", "aliexpress",
    "яндекс маркет", "сбермегамаркет", "мегамаркет",
    # Лайфхаки
    "лайфхак", "лайфхаки", "секрет", "хитрость", "совет", "экономия",
    "как сэкономить", "полезн", "инструкция", "настройка"
]

LIFEHACK_EXTRA_KEYWORDS = [
    "ios", "айос", "iphone", "айфон", "mac", "мак", "apple", "эппл",
    "андроид", "android", "windows", "виндовс", "настройк", "лайфхак"
]

def clean_html(raw):
    if not raw:
        return ""
    return re.sub(r'<.*?>', '', raw)

def is_deal_related(title, description, category):
    """Проверяет, относится ли статья к скидкам/акциям"""
    text = (title + " " + description).lower()
    
    # Для категории lifehack - особые правила
    if category == "lifehack":
        # Должен содержать лайфхак-слова И tech-слова
        has_lifehack = any(kw in text for kw in ["лайфхак", "совет", "секрет", "хитрость", "экономия", "полезн"])
        has_tech = any(kw in text for kw in LIFEHACK_EXTRA_KEYWORDS)
        return has_lifehack and has_tech
    
    # Для остальных категорий - проверяем наличие deal-ключевых слов
    return any(kw in text for kw in DEAL_KEYWORDS)

def calculate_importance(title, description, category):
    """Оценка выгодности от 1 до 10"""
    text = (title + " " + description).lower()
    score = 5
    
    # Высокоприоритетные слова
    high_priority = ["скидка", "акция", "промокод", "купон", "распродажа", "кешбэк", "бесплатно"]
    for kw in high_priority:
        if kw in text:
            score += 1
    
    # Упоминание конкретных площадок
    if "ozon" in text or "wildberries" in text or "алиэкспресс" in text:
        score += 1
    if "банк" in text or "карт" in text:
        score += 1
    
    # Лайфхаки с iOS получают бонус
    if category == "lifehack" and ("ios" in text or "iphone" in text or "айфон" in text):
        score += 2
    
    return min(10, max(1, score))

def translate_text(text):
    if not text or len(text.strip()) < 5:
        return text
    # Если текст уже на русском - не переводим
    if any(ch in text for ch in "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"):
        return text
    try:
        text_to_translate = text[:4000] if len(text) > 4000 else text
        return translator.translate(text_to_translate)
    except Exception as e:
        print(f"Ошибка перевода: {e}", flush=True)
        return text

def analyze_with_deepseek(title, content):
    if not DEEPSEEK_AVAILABLE:
        return ""
    try:
        prompt = f"""Проанализируй это предложение и выдели главное о скидке.

Заголовок: {title}
Описание: {content[:300]}

Ответь кратко в формате:
💎 Суть: (1 предложение)
💰 Выгода: (цифра или процент)"""
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

def generate_ai_image(title, category):
    try:
        prompt = f"{category} discount sale, {title[:50]}"
        encoded_prompt = urllib.parse.quote(prompt)
        return f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=512&height=512&nologo=true"
    except Exception:
        return "https://i.imgur.com/Xr5Kq9M.png"

def get_news_image(link, title, category):
    image_url = extract_image_from_article(link)
    if not image_url:
        image_url = generate_ai_image(title, category)
    return image_url

def fetch_deals(hours=24, limit=5, categories=None):
    """Сбор ТОЛЬКО релевантных скидок/акций/лайфхаков"""
    if categories is None:
        categories = ["bank", "electronics", "marketplace"]
    
    articles = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    
    for feed_conf in RSS_FEEDS:
        if feed_conf["category"] not in categories:
            continue
        
        try:
            print(f"Загружаю: {feed_conf['name']}", flush=True)
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
                
                title_en = entry.get("title", "")
                desc_en = clean_html(entry.get("description", ""))[:500]
                
                # 🔥 ВАЖНО: проверяем, относится ли к скидкам
                if not is_deal_related(title_en, desc_en, feed_conf["category"]):
                    continue
                
                pub_dt_msk = pub_dt_utc.astimezone(MOSCOW_TZ)
                link = entry.get("link", "#")
                
                title_ru = translate_text(title_en)
                desc_ru = translate_text(desc_en[:300])
                
                importance = calculate_importance(title_en, desc_en, feed_conf["category"])
                
                image_url = None
                if 'media_content' in entry and entry.media_content:
                    image_url = entry.media_content[0].get('url')
                if not image_url:
                    image_url = get_news_image(link, title_en, feed_conf["category"])
                
                articles.append({
                    "title": title_ru,
                    "link": link,
                    "desc": desc_ru,
                    "date": pub_dt_msk.strftime("%d.%m %H:%M"),
                    "source": feed_conf["name"],
                    "category": feed_conf["category"],
                    "importance": importance,
                    "image_url": image_url
                })
        except Exception as e:
            print(f"Ошибка {feed_conf['name']}: {e}", flush=True)
    
    # Сортируем по важности
    articles.sort(key=lambda x: x["importance"], reverse=True)
    
    # Удаляем дубликаты
    seen = set()
    unique = []
    for a in articles:
        key = a["title"][:50]
        if key not in seen:
            seen.add(key)
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
        requests.post(url, json=payload, timeout=30)
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
    except Exception:
        send_message(chat_id, caption)

def show_main_keyboard(chat_id):
    keyboard = {
        "keyboard": [
            ["🔥 ТОП-5 СКИДОК И АКЦИЙ"],
            ["💡 ТОП-3 ЛАЙФХАКА (iOS/техника)"]
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
    if not CHANNEL_ID:
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
                
                print(f"📨 Получено: '{text}'", flush=True)
                
                if text == "/start":
                    send_message(chat_id,
                        "🛍 *БОТ СКИДОК И АКЦИЙ v2.0*\n\n"
                        "✅ *Что я умею:*\n"
                        "🔹 ТОП-5 скидок/акций/промокодов (банки, электроника, маркетплейсы)\n"
                        "🔹 ТОП-3 лайфхака (iOS, техника, экономия)\n\n"
                        "⚠️ *Я отфильтровываю только релевантные скидки!*"
                    )
                    show_main_keyboard(chat_id)
                
                elif text == "🔥 ТОП-5 СКИДОК И АКЦИЙ":
                    send_message(chat_id, "🔍 *Ищу скидки за 24 часа...*")
                    deals = fetch_deals(hours=24, limit=5, categories=["bank", "electronics", "marketplace"])
                    
                    if deals:
                        send_message(chat_id, f"✅ *Найдено {len(deals)} предложений:*")
                        for i, deal in enumerate(deals, 1):
                            caption = f"*{i}. {deal['title']}*\n\n📝 {deal['desc']}\n\n📅 {deal['date']} | 📰 {deal['source']}\n⭐ Выгодность: {deal['importance']}/10\n\n🔗 [Подробнее]({deal['link']})"
                            if DEEPSEEK_AVAILABLE:
                                caption += analyze_with_deepseek(deal['title'], deal['desc'])
                            if deal.get("image_url"):
                                send_photo(chat_id, deal["image_url"], caption)
                            else:
                                send_message(chat_id, caption)
                            time.sleep(1)
                    else:
                        send_message(chat_id, "😕 *Скидок не найдено.*\nПопробуйте позже.")
                    show_main_keyboard(chat_id)
                
                elif text == "💡 ТОП-3 ЛАЙФХАКА (iOS/техника)":
                    send_message(chat_id, "🔍 *Ищу лайфхаки за 48 часов...*")
                    deals = fetch_deals(hours=48, limit=3, categories=["lifehack"])
                    
                    if deals:
                        send_message(chat_id, f"✅ *Найдено {len(deals)} лайфхаков:*")
                        for i, deal in enumerate(deals, 1):
                            caption = f"*{i}. {deal['title']}*\n\n📝 {deal['desc']}\n\n📅 {deal['date']} | 📰 {deal['source']}\n\n🔗 [Читать]({deal['link']})"
                            if DEEPSEEK_AVAILABLE:
                                caption += analyze_with_deepseek(deal['title'], deal['desc'])
                            send_message(chat_id, caption)
                            time.sleep(1)
                    else:
                        send_message(chat_id, "😕 *Лайфхаков не найдено.*")
                    show_main_keyboard(chat_id)
                
                else:
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

@app.route('/')
def index():
    return "🛍 Бот скидок работает!"

@app.route('/health')
def health():
    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    threading.Timer(3.0, start_background_tasks).start()
    print(f"🌐 Flask на порту {port}", flush=True)
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
