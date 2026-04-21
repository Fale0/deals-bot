import os
import re
import time
import requests
import random
from flask import Flask, request, jsonify
import threading

app = Flask(__name__)
BOT_TOKEN = os.environ.get("BOT_TOKEN")
last_update_id = 0

# ==================== БАНКИ С АКЦИЯМИ ====================
BANKS = [
    {"name": "Альфа-Банк", "url": "https://alfabank.ru", "promo": "Акции с повышенным кешбэком до 10% у партнеров (Стройландия, фастфуд)", "cashback": "до 10%"},
    {"name": "Газпромбанк", "url": "https://gazprombank.ru", "promo": "Кешбэк 35% на ежедневные траты при поддержании остатка на счёте", "cashback": "до 35%"},
    {"name": "МТС Банк", "url": "https://mtsbank.ru", "promo": "Кешбэк за покупку ценных бумаг и инвестиционные продукты", "cashback": "до 10%"},
    {"name": "СберБанк", "url": "https://sberbank.ru", "promo": "Бонусы Спасибо до 30% у партнеров, акции на маркетплейсах", "cashback": "до 30% бонусами"},
    {"name": "ВТБ", "url": "https://vtb.ru", "promo": "Кешбэк до 25% в выбранных категориях", "cashback": "до 25%"},
    {"name": "Т-Банк", "url": "https://tbank.ru", "promo": "Мультикарта с кешбэком до 5% на выбор категорий", "cashback": "до 5%"},
    {"name": "Райффайзенбанк", "url": "https://raiffeisen.ru", "promo": "Кешбэк за коммунальные услуги и покупки у партнеров", "cashback": "до 10%"},
    {"name": "Совкомбанк", "url": "https://sovcombank.ru", "promo": "Кешбэк баллами на всё", "cashback": "до 10%"},
    {"name": "Почта Банк", "url": "https://pochtabank.ru", "promo": "Кешбэк за покупки у партнеров", "cashback": "до 15%"},
]

# ==================== МАРКЕТПЛЕЙСЫ И МАГАЗИНЫ ====================
MARKETPLACES = [
    {"name": "Ozon", "url": "https://ozon.ru", "promo": "Регулярные распродажи, промокоды на скидку", "discount": "до 70%"},
    {"name": "Wildberries", "url": "https://wildberries.ru", "promo": "Акции, скидки, распродажи каждый день", "discount": "до 70%"},
    {"name": "Яндекс.Маркет", "url": "https://market.yandex.ru", "promo": "Скидки, кешбэк баллами Плюса", "discount": "до 50%"},
    {"name": "AliExpress Россия", "url": "https://aliexpress.ru", "promo": "Распродажи 11.11, купоны на скидку", "discount": "до 80%"},
    {"name": "Ситилинк", "url": "https://citilink.ru", "promo": "Скидки на электронику и бытовую технику", "discount": "до 30%"},
    {"name": "М.Видео", "url": "https://mvideo.ru", "promo": "Акции, кешбэк бонусами, трейд-ин", "discount": "до 25%"},
    {"name": "Эльдорадо", "url": "https://eldorado.ru", "promo": "Скидки на технику, рассрочка 0%", "discount": "до 30%"},
    {"name": "DNS", "url": "https://dns-shop.ru", "promo": "Распродажи, трейд-ин, скидки по карте", "discount": "до 25%"},
    {"name": "ВсеИнструменты.ру", "url": "https://vseinstrumenti.ru", "promo": "Скидки на инструменты и стройматериалы", "discount": "до 40%"},
    {"name": "Леруа Мерлен", "url": "https://leroymerlin.ru", "promo": "Акции на товары для дома и ремонта", "discount": "до 30%"},
    {"name": "OBI", "url": "https://obi.ru", "promo": "Скидки на товары для дома и сада", "discount": "до 25%"},
]

# ==================== АГРЕГАТОРЫ КЕШБЭКА ====================
CASHBACK_SERVICES = [
    {"name": "LetyShops", "url": "https://letyshops.ru", "cashback": "до 30%", "note": "2000+ магазинов, вывод на карту"},
    {"name": "ePN", "url": "https://epn.ru", "cashback": "до 25%", "note": "Кешбэк за покупки и задания"},
    {"name": "Megabonus", "url": "https://megabonus.com", "cashback": "до 40%", "note": "Промокоды + кешбэк"},
    {"name": "CashbackCity", "url": "https://cashbackcity.ru", "cashback": "до 50%", "note": "Высокий кешбэк у партнеров"},
    {"name": "CopyCash", "url": "https://copycash.ru", "cashback": "до 35%", "note": "Кешбэк + промокоды"},
    {"name": "GoHit", "url": "https://gohit.ru", "cashback": "до 30%", "note": "Быстрый вывод денег"},
]

# ==================== ЛАЙФХАКИ ДЛЯ ЭКОНОМИИ ====================
LIFE_HACKS = [
    "💡 *Лайфхак:* Используйте банковский кешбэк + сервис кешбэка одновременно — получаете ДВОЙНУЮ выгоду!",
    "💡 *Лайфхак:* Покупайте подарочные карты Ozon/WB через агрегаторы кешбэка — экономия до 20% сверху!",
    "💡 *Лайфхак:* В Т-Банке «Мультикарта» позволяет менять категории кешбэка под ваши траты каждый месяц",
    "💡 *Лайфхак:* В Альфа-Банке по выходным повышенный кешбэк до 10% — планируйте крупные покупки на выходные!",
    "💡 *Лайфхак:* В Газпромбанке акция «Кешбэк 35%» — нужно поддерживать остаток на счёте, читайте условия!",
    "💡 *Лайфхак:* Подключайте автоплатеж по кредитной карте — банки часто дают повышенный кешбэк за это!",
    "💡 *Лайфхак:* Перед покупкой техники проверьте цены на Ситилинк, М.Видео и DNS — разница может быть 15-20%!",
    "💡 *Лайфхак:* На AliExpress ищите товары с пометкой «Choice» — бесплатная доставка и дополнительные скидки!",
    "💡 *Лайфхак:* В Ozon и Wildberries подписка на WB Plus/Ozon Premium окупается, если вы часто заказываете",
    "💡 *Лайфхак:* Используйте кешбэк-сервисы даже при покупке по промокоду — кешбэк часто начисляется сверху!",
    "💡 *Лайфхак:* В СберБанке есть акции с повышенными бонусами Спасибо у партнеров — до 30% возврата",
    "💡 *Лайфхак:* Сравнивайте цены через Яндекс.Маркет перед покупкой — можно найти дешевле на 10-30%",
]

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

def get_banks_info():
    """Формирует информацию о банковских акциях"""
    result = "🏦 *Банковские акции и кешбэк*\n\n"
    for bank in BANKS:
        result += f"🏛️ *{bank['name']}*\n"
        result += f"   📌 {bank['promo']}\n"
        result += f"   💰 Кешбэк: {bank['cashback']}\n"
        result += f"   🔗 [Перейти]({bank['url']})\n\n"
    return result

def get_marketplaces_info():
    """Формирует информацию о скидках в магазинах"""
    result = "🛍️ *Скидки и распродажи на маркетплейсах*\n\n"
    for mp in MARKETPLACES:
        result += f"📦 *{mp['name']}*\n"
        result += f"   📌 {mp['promo']}\n"
        result += f"   🔥 Скидка: {mp['discount']}\n"
        result += f"   🔗 [Перейти]({mp['url']})\n\n"
    return result

def get_cashback_info():
    """Формирует информацию о сервисах кешбэка"""
    result = "💰 *Сервисы дополнительного кешбэка*\n\n"
    result += "🔔 *Как работают:* переходите в магазин по их ссылке и получаете кешбэк СВЕРХ банковского!\n\n"
    for cs in CASHBACK_SERVICES:
        result += f"⭐ *{cs['name']}*\n"
        result += f"   📊 Кешбэк: {cs['cashback']}\n"
        result += f"   📝 {cs['note']}\n"
        result += f"   🔗 [Перейти]({cs['url']})\n\n"
    return result

def get_all_discounts():
    """Собирает всю информацию о скидках и акциях"""
    result = "🛍️ *ВСЕ АКЦИИ И СКИДКИ*\n\n"
    result += get_banks_info()
    result += get_marketplaces_info()
    result += get_cashback_info()
    return result

def get_life_hack():
    """Возвращает случайный лайфхак"""
    return random.choice(LIFE_HACKS)

def show_keyboard(chat_id):
    """Показывает клавиатуру с кнопками"""
    keyboard = {
        "keyboard": [
            ["🏦 Акции банков", "🛍️ Скидки в магазинах"],
            ["💰 Сервисы кешбэка", "💡 Лайфхак дня"],
            ["📋 Все акции сразу"]
        ],
        "resize_keyboard": True
    }
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": "📱 *Выбери, что тебя интересует:*",
        "reply_markup": keyboard,
        "parse_mode": "Markdown"
    }
    requests.post(url, json=payload)

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

def bot_polling():
    global last_update_id
    print("✅ Бот скидок и акций запущен!")
    print("📌 Команды: /start, /banks, /shops, /cashback, /hack, /alldiscounts")
    
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
                
                # Обработка команд и кнопок
                if text == "/start":
                    welcome = (
                        "💰 *Бот скидок, акций и кешбэка* 💰\n\n"
                        "📊 *Что я умею:*\n"
                        "• 🏦 Акции банков с кешбэком до 35%\n"
                        "• 🛍️ Скидки на маркетплейсах до 70%\n"
                        "• 💰 Сервисы дополнительного кешбэка\n"
                        "• 💡 Лайфхаки для экономии денег\n\n"
                        "📌 *Команды:*\n"
                        "• `/banks` — акции банков\n"
                        "• `/shops` — скидки в магазинах\n"
                        "• `/cashback` — сервисы кешбэка\n"
                        "• `/hack` — лайфхак дня\n"
                        "• `/alldiscounts` — все акции сразу\n\n"
                        "💡 *Главный совет:*\n"
                        "Используйте банковский кешбэк + сервис кешбэка вместе — получаете ДВОЙНУЮ выгоду!\n\n"
                        "👇 Нажми на кнопки ниже!"
                    )
                    send_message(chat_id, welcome)
                    show_keyboard(chat_id)
                
                elif text in ["/banks", "🏦 Акции банков"]:
                    info = get_banks_info()
                    send_message(chat_id, info)
                
                elif text in ["/shops", "🛍️ Скидки в магазинах"]:
                    info = get_marketplaces_info()
                    send_message(chat_id, info)
                
                elif text in ["/cashback", "💰 Сервисы кешбэка"]:
                    info = get_cashback_info()
                    send_message(chat_id, info)
                
                elif text in ["/hack", "💡 Лайфхак дня"]:
                    hack = get_life_hack()
                    send_message(chat_id, hack)
                
                elif text in ["/alldiscounts", "📋 Все акции сразу"]:
                    info = get_all_discounts()
                    send_message(chat_id, info)
                
                elif text == "/health":
                    send_message(chat_id, "✅ Бот работает! Авто-пинг активен.\n💰 Экономьте с умом!")
                
        except Exception as e:
            print(f"Ошибка в polling: {e}")
            time.sleep(5)

@app.route('/')
def index():
    return "💰 Бот скидок, акций и кешбэка работает!"

@app.route('/health')
def health():
    return "OK", 200

if __name__ == "__main__":
    # Запускаем авто-пинг (чтобы бот не засыпал)
    ping_thread = threading.Thread(target=keep_alive, daemon=True)
    ping_thread.start()
    print("🟢 Auto-ping активирован (каждые 10 минут)")
    
    # Запускаем бота
    bot_thread = threading.Thread(target=bot_polling, daemon=True)
    bot_thread.start()
    
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
