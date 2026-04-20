from flask import Flask
import os
import threading
import time

app = Flask(__name__)

@app.route('/')
def index():
    return "✅ Бот работает!"

@app.route('/health')
def health():
    return "OK", 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    print(f"🌐 Запуск Flask на порту {port}...")
    print(f"🌐 Хост: 0.0.0.0")
    
    # Запускаем Flask СРАЗУ, без всяких потоков и задержек
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
