import os
import re
import time
import requests
import random
from datetime import datetime
from flask import Flask, request, jsonify
import threading
from bs4 import BeautifulSoup

app = Flask(__name__)
BOT_TOKEN = os.environ.get("BOT_TOKEN")
last_update_id = 0

# ==================== КОНФИГУРАЦИЯ САЙТОВ ====================
BANK_SITES = [
    {
        "name": "Альфа-Банк",
        "url": "https://alfabank.ru/magazine/akcii",
        "type": "static"
    },
    {
        "name": "Т-Банк",
        "url": "https://www.tbank.ru/akcii/",
        "type": "static"
    },
    {
        "name": "СберБанк",
        "url": "https://www.sberbank.ru/ru/person/promo",
        "type": "static"
    },
    {
        "name": "ВТБ",
        "url": "https://www.vtb.ru/akcii/",
        "type": "static"
    },
]

MARKET_SITES = [
    {
        "name": "Ozon",
        "url": "https://www.ozon.ru/highlight/",
        "type": "static"
    },
    {
        "name": "Wildberries",
        "url": "https://www.wildberries.ru/sales",
        "type": "static"
    },
    {
        "name": "Яндекс.Маркет",
        "url": "https://market.yandex.ru/promo",
        "type": "static"
    },
]

# Лайфхаки для экономии
LIFE_HACKS = [
    "💡 *Лайфхак:* Используйте банковский кешбэк + сервис кешбэка одновременно — получаете ДВОЙНУЮ выгоду!",
    "💡 *Лайфхак:* Покупайте подарочные карты Ozon/WB через агрегаторы кешбэка — экономия до 20% сверху!",
    "💡 *Лайфхак:* В Т-Банке «Мультикарта» позволяет менять категории кешбэка под ваши траты каждый месяц",
    "💡 *Лайфхак:* В Альфа-Банке по выходным повышенный кешбэк до 10% — планируйте крупные покупки на выходные!",
    "💡 *Лайфхак:* В Газпромбанке акция «Кешбэк 35%» — нужно поддерживать остаток на счёте",
    "💡 *Лайфхак:* Подключайте автоплатеж по кредитной карте — банки часто дают повышенный кешбэк!",
    "💡 *Лайфхак:* Перед покупкой техники проверьте цены на Ситилинк, М.Видео и DNS — разница может быть 15-20%!",
    "💡 *Лайфхак:* На AliExpress ищите товары с пометкой «Choice» — бесплатная доставка и дополнительные скидки!",
    "💡 *Лайфхак:* В Ozon и Wildberries подписка на WB Plus/Ozon Premium окупается, если вы часто заказываете",
    "💡 *Лайфхак:* Используйте кешбэк-сервисы даже при покупке по промокоду — кешбэк часто начисляется сверху!",
]

# ==================== ФУНКЦИИ ПАРСИНГА ====================

def get_headers():
    """Возвращает полные заголовки браузера для обхода блокировок"""
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Cache-Control': 'max-age=0'
    }

def scrape_site(site):
    """Парсит сайт и извлекает информацию об акциях"""
    try:
        print(f"🔍 Парсим {site['name']}: {site['url']}")
        
        session = requests.Session()
        response = session.get(site['url'], headers=get_headers(), timeout=30)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, 'lxml')
        
        # Ищем потенциальные акции по разным селекторам
        found_items = []
        
        # Поиск по тегам с ключевыми словами
        keywords = ['акция', 'скидка', 'кешбэк', '%', 'бонус', 'промо', 'sale', 'discount', 'cashback']
        
        # Ищем в заголовках h1, h2, h3
        for tag in soup.find_all(['h1', 'h2', 'h3', 'h4', 'a', 'div']):
            text = tag.get_text(strip=True)
            if len(text) > 15 and len(text) < 200:
                text_lower = text.lower()
                if any(kw in text_lower for kw in keywords):
                    # Ищем ссылку
                    link = tag.get('href', '')
                    if not link:
                        parent_link = tag.find_parent('a')
                        if parent_link:
                            link = parent_link.get('href', '')
                    
                    if link and not link.startswith('http'):
                        link = site['url'].rstrip('/') + '/' + link.lstrip('/')
                    
                    found_items.append({
                        "title": text[:120],
                        "link": link if link else site['url'],
                        "source": site['name']
                    })
        
        # Убираем дубликаты по заголовку
        unique_items = []
        seen_titles = set()
        for item in found_items:
            if item['title'] not in seen_titles:
                seen_titles.add(item['title'])
                unique_items.append(item)
        
        print(f"   ✅ Найдено {len(unique_items)} предложений")
        return unique_items[:5]  # максимум 5 на сайт
        
    except Exception as e:
        print(f"   ❌ Ошибка парсинга {site['name']}: {e}")
        return []

def collect_all_discounts():
    """Собирает акции со всех сайтов"""
    all_offers = []
    
    # Парсим банки
    print("\n🏦 Парсим банки...")
    for site in BANK_SITES:
        items = scrape_site(site)
        for item in items:
            item['type'] = 'bank'
            all_offers.append(item)
        time.sleep(2)  # Пауза между запросами
    
    # Парсим маркетплейсы
    print("\n🛒 Парсим маркетплейсы...")
    for site in MARKET_SITES:
        items = scrape_site(site)
        for item in items:
            item['type'] = 'market'
            all_offers.append(item)
        time.sleep(2)
    
    print(f"\n📊 Всего собрано: {len(all_offers)} предложений")
    return all_offers

def format_offers(offers):
    """Форматирует предложения для отправки в Telegram"""
    if not offers:
        return "😕 *Активных акций и скидок не найдено*\n\n" \
               "💡 *Возможные причины:*\n" \
               "• Сайты временно недоступны\n" \
               "• Изменилась структура страниц\n" \
               "• Нет активных акций в данный момент\n\n" \
               "🔄 Попробуйте обновить данные через 10-15 минут командой /refresh"
    
    result = "🛍️ *АКТУАЛЬНЫЕ АКЦИИ И СКИДКИ* 🛍️\n\n"
    result += f"📅 Обновлено: {datetime.now().strftime('%d.%m.%Y %H:%M')} (МСК)\n"
    result += f"📊 Найдено предложений: {len(offers)}\n\n"
    result += "─" * 25 + "\n\n"
    
    for i, offer in enumerate(offers[:15], 1):
        icon = "🏦" if offer['type'] == 'bank' else "🛒"
        result += f"{icon} *{i}. {offer['source']}*\n"
        result += f"📌 *{offer['title'][:80]}*\n"
        if offer.get('link') and offer['link'] != '#':
            result += f"🔗 [Подробнее]({offer['link']})\n"
        result += "\n"
    
    result += "─" * 25 + "\n\n"
    result += "💡 *Совет:* Используйте кешбэк-сервисы поверх этих скидок!\n"
    result += "🔄 Для обновления данных используйте /refresh"
    
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

# Кеш для скидок
discounts_cache = {"data": [], "last_update": None}

def get_cached_discounts(force_refresh=False):
    """Возвращает кешированные скидки или парсит заново"""
    if force_refresh or not discounts_cache["data"] or \
       (datetime.now() - discounts_cache["last_update"]).seconds > 3600:
        print("🔄 Обновляем кеш скидок...")
        discounts_cache["data"] = collect_all_discounts()
        discounts_cache["last_update"] = datetime.now()
    return discounts_cache["data"]

def bot_polling():
    global last_update_id
    print("✅ Бот-агрегатор скидок запущен!")
    print("📌 Команды: /start, /discounts, /banks, /shops, /hack, /refresh")
    
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
                        "• 💡 Делюсь лайфхаками экономии\n"
                        "• 🔄 Автоматически обновляю данные\n\n"
                        "📌 *Команды:*\n"
                        "• `/discounts` — все актуальные скидки\n"
                        "• `/banks` — только акции банков\n"
                        "• `/shops` — только скидки в магазинах\n"
                        "• `/hack` — лайфхак дня\n"
                        "• `/refresh` — принудительно обновить данные\n\n"
                        "⚡ *Внимание:* Данные собираются в реальном времени!\n"
                        "🕒 Автообновление кеша: раз в час\n\n"
                        "👇 Нажми на кнопки ниже!"
                    )
                    send_message(chat_id, welcome)
                    show_keyboard(chat_id)
                
                elif text in ["/discounts", "🛍️ Актуальные скидки"]:
                    send_message(chat_id, "🔍 Собираю актуальные скидки с сайтов...\n⏳ Обычно занимает 10-20 секунд")
                    offers = get_cached_discounts()
                    message = format_offers(offers)
                    send_message(chat_id, message)
                
                elif text in ["/banks", "🏦 Акции банков"]:
                    send_message(chat_id, "🏦 Собираю акции банков...")
                    offers = get_cached_discounts()
                    bank_offers = [o for o in offers if o.get('type') == 'bank']
                    if not bank_offers:
                        send_message(chat_id, "😕 Акций банков не найдено в данный момент")
                    else:
                        message = format_offers(bank_offers)
                        send_message(chat_id, message)
                
                elif text in ["/shops", "🛒 Скидки в магазинах"]:
                    send_message(chat_id, "🛒 Собираю скидки на маркетплейсах...")
                    offers = get_cached_discounts()
                    shop_offers = [o for o in offers if o.get('type') == 'market']
                    if not shop_offers:
                        send_message(chat_id, "😕 Скидок в магазинах не найдено в данный момент")
                    else:
                        message = format_offers(shop_offers)
                        send_message(chat_id, message)
                
                elif text in ["/hack", "💡 Лайфхак дня"]:
                    hack = get_life_hack()
                    send_message(chat_id, f"{hack}\n\n💡 Ещё лайфхаки по команде /hack")
                
                elif text in ["/refresh", "🔄 Обновить данные"]:
                    send_message(chat_id, "🔄 Принудительно обновляю данные с сайтов...\n⏳ Это может занять 20-30 секунд")
                    offers = get_cached_discounts(force_refresh=True)
                    message = format_offers(offers)
                    send_message(chat_id, message)
                
                elif text == "/health":
                    last_update = discounts_cache["last_update"]
                    last_update_str = last_update.strftime('%d.%m.%Y %H:%M') if last_update else "никогда"
                    status = f"✅ *Бот работает!*\n\n📊 В кеше: {len(discounts_cache['data'])} предложений\n🕒 Последнее обновление: {last_update_str}\n🔄 Автообновление: раз в час"
                    send_message(chat_id, status)
                
        except Exception as e:
            print(f"Ошибка в polling: {e}")
            time.sleep(5)

def keep_alive():
    """Авто-пинг каждые 10 минут, чтобы бот не засыпал"""
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
    ping_thread = threading.Thread(target=keep_alive, daemon=True)
    ping_thread.start()
    print("🟢 Auto-ping активирован (каждые 10 минут)")
    
    bot_thread = threading.Thread(target=bot_polling, daemon=True)
    bot_thread.start()
    
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
