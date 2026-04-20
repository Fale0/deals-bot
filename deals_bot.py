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

# ==================== ТОЛЬКО СПЕЦИАЛИЗИРОВАННЫЕ ИСТОЧНИКИ ====================
# Никаких новостных лент! Только сайты со скидками, купонами и лайфхаками

RSS_FEEDS = [
    # БАНКИ - акции, кешбэк, промо
    {
        "url": "https://brobank.ru/promo/feed/",
        "name": "BroBank (Акции банков)",
        "category": "bank"
    },
    {
        "url": "https://vashifinancy.ru/rss/",
        "name": "Ваши финансы",
        "category": "bank"
    },
    
    # СКИДКИ И КУПОНЫ
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
    {
        "url": "https://skidkaonline.ru/rss",
        "name": "СкидкаОнлайн",
        "category": "marketplace"
    },
    {
        "url": "https://promocod.ru/rss",
        "name": "PromoCod.ru",
        "category": "marketplace"
    },
    
    # ЛАЙФХАКИ (жизнь + техника)
    {
        "url": "https://lifehacker.ru/rss/",
        "name": "Lifehacker",
        "category": "lifehack"
    },
    {
        "url": "https://lifehacker.ru/feed/",
        "name": "Lifehacker (alt)",
        "category": "lifehack"
    },
    {
        "url": "https://life.ru/rss",
        "name": "Life.ru",
        "category": "lifehack"
    },
]

# ==================== КЛЮЧЕВЫЕ СЛОВА ДЛЯ СКИДОК ====================
DEAL_KEYWORDS = [
    "скидка", "скидки", "акция", "акции", "промокод", "промо", "купон",
    "распродажа", "sale", "выгода", "выгод", "дешев", "цена снижен",
    "кешбэк", "cashback", "кэшбэк", "бонус", "подарок", "спецпредложение",
    "халява", "бесплатно", "даром", "скидочный", "акционный"
]

BANK_KEYWORDS = [
    "банк", "карт", "кредит", "ипотека", "вклад", "накопительн",
    "счет", "процент", "ставка", "кешбэк", "бонус"
]

MARKETPLACE_KEYWORDS = [
    "ozon", "озон", "wildberries", "вайлдберриз", "алиэкспресс", "aliexpress",
    "яндекс маркет", "сбермегамаркет", "мегамаркет", "ламода", "детский мир"
]

LIFEHACK_KEYWORDS = [
    "лайфхак", "совет", "секрет", "хитрость", "полезн", "инструкция",
    "как сделать", "как настроить", "экономия", "быстрый способ"
]

def clean_html(raw):
    if not raw:
        return ""
    return re.sub(r'<.*?>', '', raw)

def is_relevant_article(title, description, category):
    """Проверяет, релевантна ли статья для категории"""
    text = (title + " " + description).lower()
    
    if category == "bank":
        return any(kw in text for kw in DEAL_KEYWORDS) and any(kw in text for kw in BANK_KEYWORDS)
    
    elif category == "marketplace":
        return any(kw in text for kw in DEAL_KEYWORDS) or any(kw in text for kw in MARKETPLACE_KEYWORDS)
    
    elif category == "lifehack":
        return any(kw in text for kw in LIFEHACK_KEYWORDS)
    
    return False

def calculate_importance(title, description, category):
    """Оценка от 1 до 10"""
    text = (title + " " + description).lower()
    score = 5
    
    # Базовые очки за deal-слова
    for kw in DEAL_KEYWORDS[:5]:
        if kw in text:
            score += 1
    
    # Бонусы по категориям
    if category == "bank" and any(kw in text for kw in ["кешбэк", "cashback", "процент"]):
        score += 2
    
    if category == "marketplace" and any(kw in text for kw in MARKETPLACE_KEYWORDS):
        score += 2
    
    if category == "lifehack" and any(kw in text for kw in ["техник", "гаджет", "смартфон", "ноутбук"]):
        score += 2
    
    return min(10, max(1, score))

def translate_text(text):
    if not text or len(text.strip()) < 5:
        return text
    if any(ch in text for ch in "абвгдеёжзийклмнопрстуфхцчшщъыьэюя"):
        return text
    try:
        return translator.translate(text[:4000] if len(text) > 4000 else text)
    except:
        return text

def analyze_with_deepseek(title, content):
    if not DEEPSEEK_AVAILABLE:
        return ""
    try:
        prompt = f"""Проанализируй скидку/акцию и выдели главное.

Заголовок: {title}
Описание: {content[:200]}

Ответь кратко в 1-2 строки: 💎 Суть и 💰 Выгода"""
        response = openai.ChatCompletion.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=80
        )
        return f"\n\n🤖 *AI:* {response.choices[0].message.content}"
    except:
        return ""

def extract_image(link):
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(link, timeout=10, headers=headers)
        match = re.search(r'<meta[^>]*property="og:image"[^>]*content="([^"]+)"', r.text)
        if match and match.group(1).startswith('http'):
            return match.group(1)
    except:
        pass
    return None

def generate_image(title, category):
    try:
        prompt = f"{category} discount sale, {title[:50]}"
        encoded = urllib.parse.quote(prompt)
        return f"https://image.pollinations.ai/prompt/{encoded}?width=512&height=512&nologo=true"
    except:
        return "https://i.imgur.com/Xr5Kq9M.png"

def fetch_deals(hours=24, limit=5, categories=None):
    """Сбор ТОЛЬКО из скидочных источников"""
    if categories is None:
        categories = ["bank", "marketplace"]
    
    articles = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    
    for feed_conf in RSS_FEEDS:
        if feed_conf["category"] not in categories:
            continue
        
        try:
            print(f"📥 {feed_conf['name']}...", flush=True)
            feed = feedparser.parse(feed_conf["url"])
            
            for entry in feed.entries[:10]:
                pub = entry.get("published_parsed")
                if not pub:
                    continue
                pub_dt = datetime.fromtimestamp(datetime(*pub[:6]).timestamp(), tz=timezone.utc)
                if pub_dt < cutoff:
                    continue
                
                title = entry.get("title", "")
                desc = clean_html(entry.get("description", ""))[:400]
                
                # 🔥 СТРОГАЯ ФИЛЬТРАЦИЯ
                if not is_relevant_article(title, desc, feed_conf["category"]):
                    continue
                
                title_ru = translate_text(title)
                desc_ru = translate_text(desc[:250])
                
                articles.append({
                    "title": title_ru,
                    "link": entry.get("link", "#"),
                    "desc": desc_ru,
                    "date": pub_dt.astimezone(MOSCOW_TZ).strftime("%d.%m %H:%M"),
                    "source": feed_conf["name"],
                    "category": feed_conf["category"],
                    "importance": calculate_importance(title, desc, feed_conf["category"]),
                    "image": extract_image(entry.get("link", "")) or generate_image(title, feed_conf["category"])
                })
        except Exception as e:
            print(f"❌ {feed_conf['name']}: {e}", flush=True)
    
    articles.sort(key=lambda x: x["importance"], reverse=True)
    
    # Убираем дубликаты
    seen = set()
    unique = []
    for a in articles:
        key = a["title"][:40]
        if key not in seen:
            seen.add(key)
            unique.append(a)
    
    print(f"✅ Найдено {len(unique)} релевантных предложений", flush=True)
    return unique[:limit]

def send_message(chat_id, text, parse_mode="Markdown"):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True
        }, timeout=30)
    except:
        pass

def send_photo(chat_id, image_url, caption):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
        r = requests.post(url, json={
            "chat_id": chat_id,
            "photo": image_url,
            "caption": caption,
            "parse_mode": "Markdown"
        }, timeout=30)
        if r.status_code != 200:
            send_message(chat_id, caption)
    except:
        send_message(chat_id, caption)

def show_keyboard(chat_id):
    keyboard = {
        "keyboard": [
            ["🔥 ТОП-5 СКИДОК И КУПОНОВ"],
            ["💡 ТОП-3 ЛАЙФХАКА"]
        ],
        "resize_keyboard": True
    }
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": chat_id,
        "text": "👇 *Выберите:*",
        "reply_markup": keyboard,
        "parse_mode": "Markdown"
    }, timeout=30)

def auto_post():
    if not CHANNEL_ID:
        return
    deals = fetch_deals(hours=24, limit=5, categories=["bank", "marketplace"])
    if deals:
        send_message(CHANNEL_ID, "🛍 *ЛУЧШИЕ СКИДКИ ЗА 24 ЧАСА*")
        for d in deals:
            cap = f"🔥 *{d['title']}*\n\n📝 {d['desc']}\n\n⭐ {d['importance']}/10\n🔗 [Подробнее]({d['link']})"
            send_photo(CHANNEL_ID, d["image"], cap)
            time.sleep(1)

def start_scheduler():
    global scheduler_started
    if scheduler_started:
        return
    scheduler = BackgroundScheduler()
    scheduler.add_job(auto_post, 'interval', hours=CHECK_INTERVAL_HOURS)
    scheduler.start()
    scheduler_started = True
    print(f"⏰ Планировщик: каждые {CHECK_INTERVAL_HOURS} ч.", flush=True)

def bot_polling():
    global last_update_id
    print("🤖 Бот запущен!", flush=True)
    
    while True:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={last_update_id+1}&timeout=30"
            updates = requests.get(url, timeout=35).json()
            
            for upd in updates.get("result", []):
                last_update_id = upd["update_id"]
                msg = upd.get("message", {})
                chat_id = msg.get("chat", {}).get("id")
                text = msg.get("text", "")
                
                print(f"📨 '{text}'", flush=True)
                
                if text == "/start":
                    send_message(chat_id, 
                        "🛍 *БОТ СКИДОК И ЛАЙФХАКОВ*\n\n"
                        "✅ *Источники:* только сайты скидок и лайфхаков\n"
                        "🔹 ТОП-5 скидок/купонов (банки, маркетплейсы)\n"
                        "🔹 ТОП-3 лайфхака (жизнь + техника)\n\n"
                        "⚠️ *Никаких новостей — только акции!*"
                    )
                    show_keyboard(chat_id)
                
                elif text == "🔥 ТОП-5 СКИДОК И КУПОНОВ":
                    send_message(chat_id, "🔍 *Ищу скидки...*")
                    deals = fetch_deals(hours=48, limit=5, categories=["bank", "marketplace"])
                    
                    if deals:
                        send_message(chat_id, f"✅ *Найдено {len(deals)}:*")
                        for i, d in enumerate(deals, 1):
                            cap = f"*{i}. {d['title']}*\n\n📝 {d['desc']}\n\n📅 {d['date']} | ⭐ {d['importance']}/10\n\n🔗 [Подробнее]({d['link']})"
                            if DEEPSEEK_AVAILABLE:
                                cap += analyze_with_deepseek(d['title'], d['desc'])
                            send_photo(chat_id, d["image"], cap)
                            time.sleep(1)
                    else:
                        send_message(chat_id, "😕 Скидок не найдено. Попробуйте позже.")
                    show_keyboard(chat_id)
                
                elif text == "💡 ТОП-3 ЛАЙФХАКА":
                    send_message(chat_id, "🔍 *Ищу лайфхаки...*")
                    deals = fetch_deals(hours=72, limit=3, categories=["lifehack"])
                    
                    if deals:
                        send_message(chat_id, f"✅ *Найдено {len(deals)}:*")
                        for i, d in enumerate(deals, 1):
                            cap = f"*{i}. {d['title']}*\n\n📝 {d['desc']}\n\n📅 {d['date']} | 📰 {d['source']}\n\n🔗 [Читать]({d['link']})"
                            send_message(chat_id, cap)
                            time.sleep(1)
                    else:
                        send_message(chat_id, "😕 Лайфхаков не найдено.")
                    show_keyboard(chat_id)
                
                else:
                    show_keyboard(chat_id)
                    
        except Exception as e:
            print(f"❌ Polling: {e}", flush=True)
            time.sleep(5)

def start_background():
    if CHANNEL_ID:
        threading.Thread(target=auto_post, daemon=True).start()
    start_scheduler()
    threading.Thread(target=lambda: (time.sleep(600), requests.get("http://localhost:10000/health")) or True, daemon=True).start()
    threading.Thread(target=bot_polling, daemon=True).start()
    print("✅ Все задачи запущены", flush=True)

@app.route('/')
def index():
    return "🛍 Бот скидок (только акции)"

@app.route('/health')
def health():
    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    threading.Timer(3.0, start_background).start()
    print(f"🌐 Порт {port}", flush=True)
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
