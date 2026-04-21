import os
import re
import time
import json
import requests
import random
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
import threading
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
import openai

app = Flask(__name__)
BOT_TOKEN = os.environ.get("BOT_TOKEN")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
last_update_id = 0

# Настройка DeepSeek
if DEEPSEEK_API_KEY:
    openai.api_key = DEEPSEEK_API_KEY
    openai.api_base = "https://api.deepseek.com/v1"
    AI_AVAILABLE = True
    print("✅ DeepSeek AI подключен")
else:
    AI_AVAILABLE = False
    print("⚠️ DeepSeek API ключ не найден, AI-анализ отключён")

# ==================== КОНФИГУРАЦИЯ САЙТОВ ДЛЯ ПАРСИНГА ====================

# Банки и их сайты с акциями
BANK_SITES = [
    {
        "name": "Альфа-Банк",
        "url": "https://alfabank.ru/magazine/akcii",
        "type": "static",
        "selectors": {
            "container": "div.offers-list__item",
            "title": "h3",
            "description": "div.offers-list__description",
            "link": "a"
        }
    },
    {
        "name": "Т-Банк",
        "url": "https://www.tbank.ru/akcii/",
        "type": "static",
        "selectors": {
            "container": "div.promo-card",
            "title": "div.promo-card__title",
            "description": "div.promo-card__description",
            "link": "a"
        }
    },
    {
        "name": "СберБанк",
        "url": "https://www.sberbank.ru/ru/person/promo",
        "type": "static",
        "selectors": {
            "container": "div.promo-item",
            "title": "div.promo-item__title",
            "description": "div.promo-item__text",
            "link": "a"
        }
    }
]

# Маркетплейсы
MARKET_SITES = [
    {
        "name": "Ozon",
        "url": "https://www.ozon.ru/highlight/",
        "type": "dynamic",  # требует JS
        "selectors": {
            "container": "div.a9y0",
            "title": "span.tsBodyL",
            "link": "a"
        }
    },
    {
        "name": "Wildberries",
        "url": "https://www.wildberries.ru/sales",
        "type": "dynamic",
        "selectors": {
            "container": "div.sale-item",
            "title": "span.goods-name",
            "link": "a"
        }
    }
]

# Сервисы кешбэка
CASHBACK_SITES = [
    {
        "name": "LetyShops",
        "url": "https://letyshops.ru/best-cashback/",
        "type": "static",
        "selectors": {
            "container": "div.store-item",
            "title": "div.store-title",
            "cashback": "span.cashback-value",
            "link": "a"
        }
    }
]

# Лайфхаки (статичные, но можно парсить с форумов)
LIFE_HACKS = [
    "💡 Используйте банковский кешбэк + сервис кешбэка одновременно — получаете ДВОЙНУЮ выгоду!",
    "💡 Покупайте подарочные карты Ozon/WB через агрегаторы кешбэка — экономия до 20% сверху!",
    "💡 В Т-Банке «Мультикарта» позволяет менять категории кешбэка под ваши траты каждый месяц",
    "💡 В Альфа-Банке по выходным повышенный кешбэк до 10% — планируйте крупные покупки на выходные!",
    "💡 В Газпромбанке акция «Кешбэк 35%» — нужно поддерживать остаток на счёте, читайте условия!",
    "💡 Подключайте автоплатеж по кредитной карте — банки часто дают повышенный кешбэк за это!",
    "💡 Перед покупкой техники проверьте цены на Ситилинк, М.Видео и DNS — разница может быть 15-20%!",
    "💡 На AliExpress ищите товары с пометкой «Choice» — бесплатная доставка и дополнительные скидки!",
]

# ==================== ФУНКЦИИ ПАРСИНГА ====================

def scrape_static_site(url, selectors):
    """Парсит статический HTML-сайт с помощью requests + BeautifulSoup"""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = requests.get(url, timeout=30, headers=headers)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'lxml')
        items = soup.select(selectors.get("container", ""))
        
        results = []
        for item in items[:10]:  # ограничиваем количество
            title_elem = item.select_one(selectors.get("title", ""))
            desc_elem = item.select_one(selectors.get("description", "")) if "description" in selectors else None
            link_elem = item.select_one(selectors.get("link", ""))
            
            title = title_elem.get_text(strip=True) if title_elem else "Без названия"
            description = desc_elem.get_text(strip=True)[:300] if desc_elem else ""
            link = link_elem.get('href') if link_elem else url
            if link and not link.startswith('http'):
                link = url.rstrip('/') + '/' + link.lstrip('/')
            
            results.append({
                "title": title,
                "description": description,
                "link": link,
                "source": url
            })
        return results
    except Exception as e:
        print(f"Ошибка парсинга {url}: {e}")
        return []

def scrape_dynamic_site(url, selectors):
    """Парсит динамический сайт (с JavaScript) через Playwright"""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=30000)
            page.wait_for_timeout(3000)  # ждём загрузки
            
            items = page.query_selector_all(selectors.get("container", ""))
            
            results = []
            for item in items[:10]:
                title_elem = item.query_selector(selectors.get("title", ""))
                link_elem = item.query_selector(selectors.get("link", ""))
                
                title = title_elem.inner_text() if title_elem else "Без названия"
                link = link_elem.get_attribute('href') if link_elem else url
                if link and not link.startswith('http'):
                    link = url.rstrip('/') + '/' + link.lstrip('/')
                
                results.append({
                    "title": title,
                    "description": "",
                    "link": link,
                    "source": url
                })
            
            browser.close()
            return results
    except Exception as e:
        print(f"Ошибка парсинга {url}: {e}")
        return []

def parse_with_ai(raw_text, source_name):
    """Использует DeepSeek для извлечения структурированной информации из сырого текста"""
    if not AI_AVAILABLE:
        return raw_text[:500]
    
    try:
        prompt = f"""Извлеки из этого текста информацию об акции или скидке от {source_name}.
Верни ТОЛЬКО JSON без пояснений в формате:
{{"title": "краткий заголовок", "discount": "размер скидки/кешбэка", "valid_until": "срок действия", "conditions": "условия получения", "link": "ссылка"}}

Текст: {raw_text[:1500]}"""
        
        response = openai.ChatCompletion.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=300
        )
        result = response.choices[0].message.content
        # Пытаемся распарсить JSON
        json_match = re.search(r'\{.*\}', result, re.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        return {"error": "Не удалось распарсить", "raw": result}
    except Exception as e:
        print(f"AI ошибка: {e}")
        return {"error": str(e), "raw": raw_text[:300]}

# ==================== СБОР ВСЕХ АКЦИЙ ====================

def collect_all_discounts():
    """Собирает акции со всех настроенных сайтов"""
    all_offers = []
    
    # Парсим банки
    for bank in BANK_SITES:
        print(f"Парсим {bank['name']}...")
        if bank["type"] == "static":
            items = scrape_static_site(bank["url"], bank["selectors"])
        else:
            items = scrape_dynamic_site(bank["url"], bank["selectors"])
        
        for item in items:
            # Формируем текст для AI-анализа
            raw_text = f"{item['title']}\n{item['description']}\n{item['link']}"
            ai_data = parse_with_ai(raw_text, bank["name"])
            
            all_offers.append({
                "source_type": "bank",
                "source_name": bank["name"],
                "title": item["title"],
                "description": item["description"][:300],
                "link": item["link"],
                "ai_analysis": ai_data
            })
        time.sleep(1)  # пауза между запросами
    
    # Парсим маркетплейсы
    for market in MARKET_SITES:
        print(f"Парсим {market['name']}...")
        if market["type"] == "static":
            items = scrape_static_site(market["url"], market["selectors"])
        else:
            items = scrape_dynamic_site(market["url"], market["selectors"])
        
        for item in items:
            all_offers.append({
                "source_type": "marketplace",
                "source_name": market["name"],
                "title": item["title"],
                "description": item["description"][:300] if item["description"] else "Скидка на товары",
                "link": item["link"],
                "ai_analysis": {}
            })
        time.sleep(1)
    
    return all_offers

def format_offers_message(offers, limit=10):
    """Форматирует собранные предложения для отправки в Telegram"""
    if not offers:
        return "😕 Активных акций и скидок не найдено.\n\nПопробуйте позже или проверьте источники."
    
    result = "🛍️ *АКТУАЛЬНЫЕ АКЦИИ И СКИДКИ* 🛍️\n\n"
    result += f"📅 Обновлено: {datetime.now().strftime('%d.%m.%Y %H:%M')} (МСК)\n"
    result += f"📊 Найдено предложений: {len(offers)}\n\n"
    result += "━" * 20 + "\n\n"
    
    for i, offer in enumerate(offers[:limit], 1):
        # Иконка в зависимости от источника
        if offer["source_type"] == "bank":
            icon = "🏦"
        elif offer["source_type"] == "marketplace":
            icon = "🛒"
        else:
            icon = "💰"
        
        result += f"{icon} *{i}. {offer['source_name']}*\n"
        result += f"📌 *{offer['title'][:80]}*\n"
        
        # Добавляем AI-анализ если есть
        if offer.get("ai_analysis") and isinstance(offer["ai_analysis"], dict):
            if "discount" in offer["ai_analysis"] and offer["ai_analysis"]["discount"]:
                result += f"💰 Скидка: {offer['ai_analysis']['discount']}\n"
            if "valid_until" in offer["ai_analysis"] and offer["ai_analysis"]["valid_until"]:
                result += f"⏰ Действует до: {offer['ai_analysis']['valid_until']}\n"
            if "conditions" in offer["ai_analysis"] and offer["ai_analysis"]["conditions"]:
                result += f"📋 Условия: {offer['ai_analysis']['conditions'][:100]}\n"
        else:
            if offer["description"]:
                result += f"📝 {offer['description'][:100]}\n"
        
        result += f"🔗 [Подробнее]({offer['link']})\n\n"
        result += "─" * 15 + "\n\n"
    
    result += "\n💡 *Совет:* Используйте кешбэк-сервисы поверх этих скидок!\n"
    result += "📌 Обновляйте список командой /discounts"
    
    return result

def get_life_hack():
    """Возвращает случайный лайфхак"""
    return random.choice(LIFE_HACKS)

# ==================== ФУНКЦИИ БОТА ====================

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

def send_long_message(chat_id, text):
    """Отправляет длинные сообщения по частям"""
    if len(text) < 4000:
        send_message(chat_id, text)
        return
    
    parts = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for i, part in enumerate(parts, 1):
        send_message(chat_id, f"📄 *Часть {i}*\n\n{part}")

def show_keyboard(chat_id):
    keyboard = {
        "keyboard": [
            ["🛍️ Актуальные скидки", "💡 Лайфхак дня"],
            ["🏦 Акции банков", "🛒 Скидки в магазинах"],
            ["🔄 Обновить данные"]
        ],
        "resize_keyboard": True
    }
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": "📱 *Выбери действие:*",
        "reply_markup": keyboard,
        "parse_mode": "Markdown"
    }
    requests.post(url, json=payload)

# Кеш для собранных скидок (чтобы не парсить каждый раз)
discounts_cache = {
    "data": [],
    "last_update": None
}

def get_cached_discounts(force_refresh=False):
    """Возвращает кешированные скидки или парсит заново"""
    if force_refresh or not discounts_cache["data"] or \
       (datetime.now() - discounts_cache["last_update"]).seconds > 3600:
        print("🔄 Обновляем кеш скидок...")
        send_message(ADMIN_CHAT_ID, "🔄 Начинаю сбор актуальных скидок с сайтов...") if 'ADMIN_CHAT_ID' in dir() else None
        discounts_cache["data"] = collect_all_discounts()
        discounts_cache["last_update"] = datetime.now()
        print(f"✅ Собрано {len(discounts_cache['data'])} предложений")
    return discounts_cache["data"]

def bot_polling():
    global last_update_id
    print("✅ Бот-агрегатор скидок запущен!")
    print("📌 Команды: /start, /discounts, /banks, /shops, /hack")
    
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
                        "💰 *Бот-агрегатор скидок и акций* 💰\n\n"
                        "📊 *Что я умею:*\n"
                        "• 🔍 Реально паршу сайты банков и магазинов\n"
                        "• 🧠 Анализирую акции через DeepSeek AI\n"
                        "• 💡 Делюсь лайфхаками экономии\n\n"
                        "📌 *Команды:*\n"
                        "• `/discounts` — все актуальные скидки\n"
                        "• `/banks` — только акции банков\n"
                        "• `/shops` — только скидки в магазинах\n"
                        "• `/hack` — лайфхак дня\n"
                        "• `/refresh` — принудительно обновить данные\n\n"
                        "⚡ *Внимание:* Данные собираются в реальном времени!\n"
                        "🕒 Обновление кеша: раз в час\n\n"
                        "👇 Нажми на кнопки ниже!"
                    )
                    send_message(chat_id, welcome)
                    show_keyboard(chat_id)
                
                elif text in ["/discounts", "🛍️ Актуальные скидки"]:
                    send_message(chat_id, "🔍 Собираю актуальные скидки с сайтов...\n⏳ Обычно занимает 10-20 секунд")
                    offers = get_cached_discounts()
                    message = format_offers_message(offers, 15)
                    send_long_message(chat_id, message)
                
                elif text in ["/banks", "🏦 Акции банков"]:
                    send_message(chat_id, "🏦 Собираю акции банков...")
                    offers = get_cached_discounts()
                    bank_offers = [o for o in offers if o["source_type"] == "bank"]
                    message = format_offers_message(bank_offers, 10)
                    send_long_message(chat_id, message)
                
                elif text in ["/shops", "🛒 Скидки в магазинах"]:
                    send_message(chat_id, "🛒 Собираю скидки на маркетплейсах...")
                    offers = get_cached_discounts()
                    shop_offers = [o for o in offers if o["source_type"] == "marketplace"]
                    message = format_offers_message(shop_offers, 10)
                    send_long_message(chat_id, message)
                
                elif text in ["/hack", "💡 Лайфхак дня"]:
                    hack = get_life_hack()
                    send_message(chat_id, f"{hack}\n\n💡 Ещё лайфхаки по команде /hack")
                
                elif text in ["/refresh", "🔄 Обновить данные"]:
                    send_message(chat_id, "🔄 Принудительно обновляю данные с сайтов...\n⏳ Это может занять 20-30 секунд")
                    offers = get_cached_discounts(force_refresh=True)
                    message = format_offers_message(offers, 10)
                    send_long_message(chat_id, message)
                
                elif text == "/health":
                    status = f"✅ Бот работает!\n📊 В кеше: {len(discounts_cache['data'])} предложений\n🕒 Последнее обновление: {discounts_cache['last_update']}"
                    send_message(chat_id, status)
                
        except Exception as e:
            print(f"Ошибка в polling: {e}")
            time.sleep(5)

def keep_alive():
    """Авто-пинг каждые 10 минут"""
    bot_url = f"https://crypto-news-bot-v7aj.onrender.com/health"
    while True:
        time.sleep(10 * 60)
        try:
            response = requests.get(bot_url, timeout=10)
            print(f"🔄 Auto-ping: статус {response.status_code}")
        except Exception as e:
            print(f"❌ Auto-ping ошибка: {e}")

@app.route('/')
def index():
    return "💰 Бот-агрегатор скидок и акций работает!"

@app.route('/health')
def health():
    return "OK", 200

if __name__ == "__main__":
    ADMIN_CHAT_ID = os.environ.get("ADMIN_CHAT_ID")  # опционально
    
    ping_thread = threading.Thread(target=keep_alive, daemon=True)
    ping_thread.start()
    print("🟢 Auto-ping активирован (каждые 10 минут)")
    
    bot_thread = threading.Thread(target=bot_polling, daemon=True)
    bot_thread.start()
    
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
