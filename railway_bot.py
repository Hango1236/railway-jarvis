import os
import requests
import json
from flask import Flask, request
import time
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ================= КОНФИГУРАЦИЯ =================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

# ================= СИСТЕМНЫЙ ПРОМПТ =================
SYSTEM_PROMPT = """Ты Джарвис — ИИ-ассистент в Telegram. Отвечай на русском языке.

ПРАВИЛА:
1. Если просят код или скрипт — генерируй ПОЛНЫЙ рабочий код
2. Для Roblox используй Luau (Lua)
3. Не пиши "сейчас", "начинаю", "хорошо" — сразу давай результат
4. Код должен быть с комментариями на русском

Если спрашивают не про код — отвечай кратко и по делу."""

# ================= AI КЛАСС =================
class OpenRouterAI:
    def __init__(self):
        self.api_key = OPENROUTER_API_KEY
        self.available = bool(self.api_key)
        if self.available:
            logger.info("✅ AI инициализирован с OpenRouter")
            logger.info("🤖 Модель: Trinity Large (400B параметров!)")
        else:
            logger.warning("⚠ OpenRouter API ключ не найден")
    
    def generate(self, user_text):
        """Генерация ответа через OpenRouter"""
        if not self.available:
            return "❌ Ошибка: Не добавлен API ключ OpenRouter.\n\nДобавь переменную OPENROUTER_API_KEY в настройках Railway."
        
        try:
            logger.info(f"📤 Отправляю запрос к Trinity 400B...")
            
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://railway.app",
                    "X-Title": "Jarvis Telegram Bot"
                },
                json={
                    "model": "arcee-ai/trinity-large-preview:free",  # ⭐ ТО ЧТО УЖЕ РАБОТАЛО!
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_text}
                    ],
                    "temperature": 0.2,
                    "max_tokens": 2000,
                    "top_p": 0.9
                },
                timeout=60
            )
            
            result = response.json()
            
            if "error" in result:
                error_msg = result["error"].get("message", "Неизвестная ошибка")
                logger.error(f"❌ Ошибка API: {error_msg}")
                
                # Если Trinity занята - пробуем Nemotron
                if "capacity" in error_msg.lower() or "overloaded" in error_msg.lower():
                    logger.info("🔄 Trinity занята, пробую Nemotron...")
                    return self.try_nemotron(user_text)
                    
                return f"⚠ Ошибка AI: {error_msg}"
            
            reply = result["choices"][0]["message"]["content"]
            logger.info(f"✅ Получен ответ от Trinity, длина: {len(reply)} символов")
            return reply
            
        except requests.exceptions.Timeout:
            logger.error("⏰ Таймаут запроса")
            return "⏰ Превышено время ожидания. Попробуй еще раз или упрости запрос."
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
            return f"⚠ Произошла ошибка: {str(e)[:100]}"
    
    def try_nemotron(self, user_text):
        """Запасная модель - Nemotron (тоже работала у тебя)"""
        try:
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "nvidia/nemotron-nano-12b-2-5-vl:free",  # ⭐ Nemotron
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_text}
                    ],
                    "temperature": 0.2,
                    "max_tokens": 1500
                },
                timeout=45
            )
            result = response.json()
            reply = result["choices"][0]["message"]["content"]
            logger.info(f"✅ Получен ответ от Nemotron")
            return reply
        except Exception as e:
            logger.error(f"❌ Nemotron тоже не сработал: {e}")
            return "❌ Все модели временно недоступны. Попробуй через 5 минут."

# Создаем экземпляр AI
ai = OpenRouterAI()

# ================= TELEGRAM ФУНКЦИИ =================
def send_message(chat_id, text):
    """Отправка сообщения в Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    
    # Проверяем есть ли код в ответе
    if "```" in text:
        data = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }
    else:
        data = {
            "chat_id": chat_id,
            "text": text
        }
    
    try:
        response = requests.post(url, json=data, timeout=5)
        if not response.ok:
            logger.error(f"Ошибка Telegram: {response.text}")
            if "parse_mode" in data:
                del data["parse_mode"]
                requests.post(url, json=data, timeout=5)
    except Exception as e:
        logger.error(f"Ошибка отправки: {e}")

def send_action(chat_id, action):
    """Отправка статуса 'печатает'"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendChatAction"
    try:
        requests.post(url, json={"chat_id": chat_id, "action": action}, timeout=2)
    except:
        pass

# ================= FLASK РОУТЫ =================
@app.route('/')
def home():
    return f"""
    <html>
        <head><title>Джарвис Бот</title></head>
        <body>
            <h1>🤖 Джарвис Telegram Бот</h1>
            <p>Статус: <b>✅ Работает</b></p>
            <p>AI: {'✅ Trinity 400B' if ai.available else '❌ Нет API ключа'}</p>
            <p>Время: {time.strftime('%Y-%m-%d %H:%M:%S')}</p>
            <p>
                <a href="/setwebhook">🔗 Установить вебхук</a><br>
                <a href="/status">📊 Статус</a>
            </p>
        </body>
    </html>
    """

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        update = request.get_json()
        if "message" in update:
            chat_id = update["message"]["chat"]["id"]
            text = update["message"]["text"]
            
            send_action(chat_id, "typing")
            reply = ai.generate(text)
            send_message(chat_id, reply)
        
        return "OK", 200
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        return "Error", 500

@app.route('/setwebhook')
def set_webhook():
    railway_url = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
    
    if not railway_url:
        railway_url = request.host
        if railway_url.startswith('localhost'):
            return "❌ Локальный сервер"
    
    webhook_url = f"https://{railway_url}/webhook"
    
    try:
        response = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook",
            params={"url": webhook_url}
        )
        return f"""
        <h2>Установка вебхука</h2>
        <p>URL: {webhook_url}</p>
        <p>Ответ Telegram: <pre>{json.dumps(response.json(), indent=2, ensure_ascii=False)}</pre></p>
        <p><a href="/">На главную</a></p>
        """
    except Exception as e:
        return f"❌ Ошибка: {e}"

@app.route('/status')
def status():
    return {
        "bot_running": True,
        "ai_available": ai.available,
        "model": "Trinity Large 400B",
        "telegram_token_set": bool(TELEGRAM_TOKEN),
        "openrouter_key_set": bool(OPENROUTER_API_KEY),
        "time": time.strftime("%Y-%m-%d %H:%M:%S")
    }

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info("="*60)
    logger.info("🚀 ЗАПУСК ДЖАРВИС БОТА")
    logger.info("="*60)
    logger.info("🤖 МОДЕЛЬ: Trinity Large 400B (400 МИЛЛИАРДОВ ПАРАМЕТРОВ!)")
    logger.info("🔥 Это в 57 раз больше твоей старой 7B модели!")
    logger.info(f"✅ AI статус: {'доступен' if ai.available else 'недоступен'}")
    logger.info("="*60)
    app.run(host="0.0.0.0", port=port)
