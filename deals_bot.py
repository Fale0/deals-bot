import os
import re
import time
import random
import logging
import urllib.parse
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import feedparser
from flask import Flask
import threading

# Используем современный OpenAI клиент (>1.0.0)
from openai import OpenAI
from deep_translator import GoogleTranslator

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ============== Конфигурация (окружение) ==============
BOT_TOKEN = os.environ.get("BOT_TOKEN")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
MOSCOW_TZ = timezone(timedelta(hours=3))

# ============== Инициализация DeepSeek ==============
deepseek_client = None
if DEEPSEEK_API_KEY:
    deepseek_client = OpenAI(
        api_key=DEEPSEEK_API_KEY,
        base_url="https://api.deepseek.com/v1",
    )
    logger.info("✅ DeepSeek API подключён")
else:
    logger.warning("⚠️ DeepSeek API ключ не найден")

translator = GoogleTranslator(source="en", target="ru")

# ============== HTTP заголовки ==============
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

# ==================== ИСТОЧНИКИ ====================
MEDICAL_FEEDS = [
    ("WHO News", "https://www.who.int/rss-feeds/news-english.xml"),
    ("Nature Medicine", "https://www.nature.com/subjects/medical-research.rss"),
    ("NIH News Releases", "https://www.nih.gov/news-events/news-releases/feed"),
    ("ScienceDaily Health", "https://www.sciencedaily.com/rss/health_medicine.xml"),
    ("Medical News Today", "https://www.medicalnewstoday.com/feeds/all"),
    ("News-Medical.net", "https://www.news-medical.net/medical-news.aspx?format=rss"),
    ("EurekAlert Medicine", "https://www.eurekalert.org/rss/medicine.xml"),
    ("The Lancet Global Health", "https://www.thelancet.com/rss/global-health"),
    ("New England Journal of Medicine", "https://www.nejm.org/action/showFeed?type=etoc&feed=rss&jc=nejm"),
    ("NEJM", "https://www.nejm.org/rss"),
]

COSMETOLOGY_FEEDS = [
    ("Dermascope", "https://www.dermascope.com/feed"),
    ("Aesthetic Medicine", "https://aestheticmed.co.uk/feed"),
    ("Global Cosmetics News", "https://www.globalcosmeticsnews.com/feed/"),
    ("Cosmetics & Toiletries", "https://www.cosmeticsandtoiletries.com/rss"),
    ("Professional Beauty", "https://www.professionalbeauty.com.uk/feed"),
    ("NewBeauty", "https://www.newbeauty.com/feed/"),
    ("Skin Inc.", "https://www.skininc.com/rss/"),
    ("Well+Good Beauty", "https://www.wellandgood.com/beauty/feed"),
]

# ============ Вспомогательные функции ============
def clean_html(raw: str) -> str:
    if not raw:
        return ""
    return re.sub(r"<.*?>", "", raw)

def escape_html(text: str) -> str:
    """Минимальное экранирование для HTML, чтобы не ломать Telegram."""
    return text.replace("&", "&amp;").replace("<", "<").replace(">", ">")

def translate_text(text: str) -> str:
    if not text or len(text.strip()) < 5:
        return text
    try:
        # Ограничим длину для быстродействия
        chunk = text[:3000]
        return translator.translate(chunk)
    except Exception as e:
        logger.warning(f"Ошибка перевода: {e}")
        return text

def calculate_importance(title: str, description: str) -> int:
    text = (title + " " + description).lower()
    score = 5
    high_kw = [
        "breakthrough", "revolutionary", "cure", "fda approved",
        "clinical trial", "groundbreaking", "gene therapy", "stem cell"
    ]
    medium_kw = ["study shows", "research", "scientists discover", "anti-aging", "potential treatment"]
    for w in high_kw:
        if w in text:
            score += 2
    for w in medium_kw:
        if w in text:
            score += 1
    if "cancer" in text or "tumor" in text:
        score += 1
    if "aging" in text or "wrinkle" in text:
        score += 1
    return min(10, max(1, score))

def analyze_with_deepseek(title: str, content: str) -> str:
    if not deepseek_client:
        return ""
    try:
        prompt = f"""Проанализируй медицинскую новость:
Заголовок: {title}
Содержание: {content[:300]}

Напиши кратко:
💡 Суть: (одно предложение)
🎯 Значение: (позитивное/нейтральное)"""
        response = deepseek_client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=120,
        )
        return f"\n\n🤖 <b>DeepSeek:</b>\n{response.choices[0].message.content}"
    except Exception as e:
        logger.error(f"Ошибка DeepSeek: {e}")
        return ""

def extract_image_from_article(url: str) -> str | None:
    """Извлекает og:image из статьи."""
    try:
        resp = requests.get(url, timeout=10, headers=REQUEST_HEADERS)
        resp.raise_for_status()
        patterns = [
            r'<meta[^>]*property="og:image"[^>]*content="([^"]+)"',
            r'<meta[^>]*name="twitter:image"[^>]*content="([^"]+)"',
            r'<meta[^>]*itemprop="image"[^>]*content="([^"]+)"',
        ]
        for pat in patterns:
            match = re.search(pat, resp.text, re.IGNORECASE)
            if match:
                img = match.group(1)
                if img.startswith("http") and "pixel" not in img.lower():
                    return img
    except Exception:
        pass
    return None

def get_fallback_image(category: str) -> str:
    """Случайная стоковая картинка из надёжных наборов."""
    medical = [
        "https://cdn.pixabay.com/photo/2016/06/28/05/10/microscope-1482987_640.jpg",
        "https://cdn.pixabay.com/photo/2020/10/18/09/16/hospital-5664806_640.jpg",
        "https://cdn.pixabay.com/photo/2015/11/16/22/14/surgery-1046403_640.jpg",
        "https://cdn.pixabay.com/photo/2016/03/06/05/47/heart-1239478_640.jpg",
        "https://cdn.pixabay.com/photo/2015/09/09/16/05/brain-931968_640.jpg",
        "https://cdn.pixabay.com/photo/2012/02/24/16/50/stethoscope-166002_640.jpg",
        "https://cdn.pixabay.com/photo/2020/04/10/13/52/coronavirus-5025812_640.jpg",
    ]
    cosmo = [
        "https://cdn.pixabay.com/photo/2016/11/29/12/54/beauty-1869540_640.jpg",
        "https://cdn.pixabay.com/photo/2017/08/07/21/31/skin-2607783_640.jpg",
        "https://cdn.pixabay.com/photo/2014/04/13/20/17/beauty-323952_640.jpg",
        "https://cdn.pixabay.com/photo/2015/10/31/12/20/face-cream-1015605_640.jpg",
        "https://cdn.pixabay.com/photo/2019/08/28/18/01/spa-4437173_640.jpg",
        "https://cdn.pixabay.com/photo/2017/01/19/19/08/cosmetics-1993549_640.jpg",
    ]
    pool = medical if category == "medical" else cosmo
    return random.choice(pool)

def get_ai_image(title: str, category: str) -> str | None:
    """Пытается сгенерировать AI‑иллюстрацию через pollinations.ai."""
    try:
        if category == "cosmetology":
            prompt = f"beauty skincare cosmetics {title[:60]}"
        else:
            prompt = f"medical research healthcare {title[:60]}"
        encoded = urllib.parse.quote(prompt)
        return f"https://image.pollinations.ai/prompt/{encoded}?width=1024&height=768"
    except Exception:
        return None

def get_news_image(title: str, link: str, category: str) -> str:
    # 1. Реальная картинка из статьи
    real_img = extract_image_from_article(link)
    if real_img and _is_url_accessible(real_img):
        return real_img

    # 2. Пробуем AI (только если не получилось реальное)
    ai_img = get_ai_image(title, category)
    if ai_img and _is_url_accessible(ai_img):
        return ai_img

    # 3. Гарантированный fallback – сток
    return get_fallback_image(category)

def _is_url_accessible(url: str, timeout: int = 3) -> bool:
    try:
        resp = requests.head(url, timeout=timeout, headers=REQUEST_HEADERS)
        return resp.status_code == 200
    except Exception:
        return False

def parse_entry(entry, cutoff_utc: datetime) -> dict | None:
    """Обрабатывает одну запись RSS, возвращает словарь или None."""
    # Дата
    pub_struct = entry.get("published_parsed") or entry.get("updated_parsed") or entry.get("date_parsed")
    if not pub_struct:
        return None
    try:
        pub_dt = datetime(*pub_struct[:6], tzinfo=timezone.utc)
    except Exception:
        return None
    if pub_dt < cutoff_utc:
        return None

    title_en = entry.get("title", "Без заголовка")
    desc_en = clean_html(entry.get("description", "") or entry.get("summary", ""))[:500]
    link = entry.get("link", "#")
    importance = calculate_importance(title_en, desc_en)

    return {
        "title_en": title_en,
        "desc_en": desc_en,
        "link": link,
        "date_utc": pub_dt,
        "importance": importance,
    }

def fetch_source(source_name: str, url: str, cutoff: datetime, category: str) -> list:
    """Получает новости из одного источника."""
    articles = []
    try:
        resp = requests.get(url, timeout=15, headers=REQUEST_HEADERS)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
        if not feed.entries:
            return articles
        for entry in feed.entries[:20]:
            parsed = parse_entry(entry, cutoff)
            if parsed:
                parsed["source"] = source_name
                parsed["category"] = category
                articles.append(parsed)
        logger.info(f"{source_name}: +{len(articles)} новостей")
    except Exception as e:
        logger.warning(f"Ошибка загрузки {source_name}: {e}")
    return articles

def fetch_all_news(feed_list: list, category: str, limit: int = 7) -> list:
    """Параллельно собирает новости со всех источников."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=72)  # 3 дня
    all_articles = []

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {
            executor.submit(fetch_source, name, url, cutoff, category): name
            for name, url in feed_list
        }
        for future in as_completed(futures):
            all_articles.extend(future.result())

    # Удаление дубликатов по заголовку
    seen = set()
    unique = []
    for a in all_articles:
        if a["title_en"] not in seen:
            seen.add(a["title_en"])
            unique.append(a)

    # Сортировка: важность, потом дата
    unique.sort(key=lambda x: (x["importance"], x["date_utc"]), reverse=True)
    return unique[:limit]

def build_caption(article: dict, idx: int, with_ai: bool = True) -> str:
    """Формирует HTML‑подпись для Telegram."""
    title_ru = escape_html(translate_text(article["title_en"]))
    desc_ru = escape_html(translate_text(article["desc_en"]))[:350]

    imp = article["importance"]
    if imp >= 8:
        emoji = "🔴🔥"
    elif imp >= 6:
        emoji = "🟠⚠️"
    elif imp >= 4:
        emoji = "🟡📌"
    else:
        emoji = "⚪📰"

    msk_time = article["date_utc"].astimezone(MOSCOW_TZ).strftime("%d.%m.%Y %H:%M")
    caption = (
        f"{emoji} <b>{idx}. {title_ru}</b>\n\n"
        f"📝 {desc_ru}\n\n"
        f"📅 {msk_time} (МСК) | 📰 {article['source']}\n"
        f"⭐ Важность: {imp}/10\n\n"
        f"🔗 <a href='{article['link']}'>Читать полностью</a>"
    )

    if with_ai and deepseek_client:
        ai = analyze_with_deepseek(title_ru, desc_ru)
        caption += ai
    return caption

# ==================== Telegram API ====================
def send_message(chat_id: int, text: str, parse_mode: str = "HTML"):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        requests.post(url, json=payload, timeout=15)
    except Exception as e:
        logger.error(f"Ошибка sendMessage: {e}")

def send_photo(chat_id: int, image_url: str, caption: str):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
        payload = {
            "chat_id": chat_id,
            "photo": image_url,
            "caption": caption,
            "parse_mode": "HTML",
        }
        resp = requests.post(url, json=payload, timeout=20)
        if resp.status_code != 200:
            logger.warning(f"Фото не отправлено: {resp.text}, пробуем текст")
            send_message(chat_id, caption)
    except Exception as e:
        logger.error(f"Ошибка sendPhoto: {e}")
        send_message(chat_id, caption)

def show_keyboard(chat_id: int):
    keyboard = {
        "keyboard": [
            ["🏥 Топ 7 мед. исследований"],
            ["💄 Топ 7 косметологии"],
        ],
        "resize_keyboard": True,
    }
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": "<b>🔬 Выберите категорию:</b>",
        "reply_markup": keyboard,
        "parse_mode": "HTML",
    }
    requests.post(url, json=payload, timeout=10)

def process_category_request(chat_id: int, feed_list: list, category: str, title_msg: str):
    """Фоновая задача: загружает и отправляет новости."""
    send_message(chat_id, f"🔍 <b>{title_msg}</b>\n⏳ Загружаю новости...")
    articles = fetch_all_news(feed_list, category, limit=7)
    if not articles:
        send_message(chat_id, "😕 Новости не найдены. Попробуйте позже.")
        show_keyboard(chat_id)
        return

    for i, art in enumerate(articles, 1):
        # Получаем картинку (реальная > AI > сток)
        img_url = get_news_image(art["title_en"], art["link"], category)
        caption = build_caption(art, i)
        send_photo(chat_id, img_url, caption)
        time.sleep(0.5)  # чтобы не упереться в лимиты Telegram

    send_message(chat_id, f"✅ Показано <b>{len(articles)}</b> новостей с иллюстрациями.")
    show_keyboard(chat_id)

# ==================== Long Polling ====================
def bot_polling():
    last_update_id = 0
    logger.info("Бот запущен")
    while True:
        try:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?offset={last_update_id+1}&timeout=30"
            resp = requests.get(url, timeout=35)
            updates = resp.json().get("result", [])
            for upd in updates:
                last_update_id = upd["update_id"]
                msg = upd.get("message")
                if not msg:
                    continue
                chat_id = msg["chat"]["id"]
                text = msg.get("text", "")

                if text == "/start":
                    welcome = (
                        "🏥 <b>Медицинский новостной бот (улучшенный)</b>\n\n"
                        "• Свежие новости из 17+ источников\n"
                        "• Реальные иллюстрации из статей\n"
                        "• Оценка важности AI (DeepSeek)\n"
                        "• Перевод на русский\n\n"
                        "Нажмите кнопку ниже:"
                    )
                    send_message(chat_id, welcome)
                    show_keyboard(chat_id)

                elif text == "🏥 Топ 7 мед. исследований":
                    # Запускаем обработку в отдельном потоке, чтобы не блокировать polling
                    threading.Thread(
                        target=process_category_request,
                        args=(chat_id, MEDICAL_FEEDS, "medical", "🏥 Топ-7 медицинских исследований"),
                        daemon=True,
                    ).start()

                elif text == "💄 Топ 7 косметологии":
                    threading.Thread(
                        target=process_category_request,
                        args=(chat_id, COSMETOLOGY_FEEDS, "cosmetology", "💄 Топ-7 косметологии"),
                        daemon=True,
                    ).start()

                elif text == "/health":
                    send_message(chat_id, "✅ Бот работает нормально!")
        except Exception as e:
            logger.error(f"Polling error: {e}")
            time.sleep(5)

# ==================== Flask для health check ====================
@app.route("/")
def index():
    return "Medical News Bot is running."

@app.route("/health")
def health():
    return "OK", 200

def keep_alive():
    """Пинг самого себя, чтобы Render не усыплял."""
    app_url = os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:5000")
    time.sleep(30)
    while True:
        try:
            requests.get(app_url + "/health", timeout=10)
            logger.info("Keep-alive ping отправлен")
        except Exception:
            pass
        time.sleep(600)

if __name__ == "__main__":
    # Фоновый пинг
    threading.Thread(target=keep_alive, daemon=True).start()
    # Запуск polling в отдельном потоке
    threading.Thread(target=bot_polling, daemon=True).start()
    # Flask сервер (нужен для health check)
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
