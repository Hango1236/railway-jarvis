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
            logger.info("🤖 Модель: Qwen3 235B (МОНСТР!)")
        else:
            logger.warning("⚠ OpenRouter API ключ не найден")
    
    def generate(self, user_text):
        """Генерация ответа через OpenRouter"""
        if not self.available:
            return "❌ Ошибка: Не добавлен API ключ OpenRouter.\n\nДобавь переменную OPENROUTER_API_KEY в настройках Railway."
        
        try:
            logger.info(f"📤 Отправляю запрос к Qwen3 235B...")
            
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://railway.app",
                    "X-Title": "Jarvis Telegram Bot"
                },
                json={
                    "model": "qwen/qwen3-235b-a22b-thinking:free",  # ⭐ ТВОЯ НОВАЯ МОДЕЛЬ 235B!
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_text}
                    ],
                    "temperature": 0.2,
                    "max_tokens": 2000,  # Больше токенов для длинных скриптов
                    "top_p": 0.9
                },
                timeout=60  # Даём больше времени на размышления
            )
            
            result = response.json()
            
            if "error" in result:
                error_msg = result["error"].get("message", "Неизвестная ошибка")
                logger.error(f"❌ Ошибка API: {error_msg}")
                
                # Если ошибка что модель не найдена - пробуем другую
                if "model" in error_msg.lower():
                    logger.info("🔄 Пробуем запасную модель...")
                    return self.generate_fallback(user_text)
                    
                return f"⚠ Ошибка AI: {error_msg}"
            
            reply = result["choices"][0]["message"]["content"]
            logger.info(f"✅ Получен ответ от Qwen3 235B, длина: {len(reply)} символов")
            return reply
            
        except requests.exceptions.Timeout:
            logger.error("⏰ Таймаут запроса")
            return "⏰ Превышено время ожидания. Попробуй еще раз или упрости запрос."
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
            return f"⚠ Произошла ошибка: {str(e)[:100]}"
    
    def generate_fallback(self, user_text):
        """Запасная модель на случай если основная не работает"""
        try:
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "qwen/qwen-2.5-72b-instruct:free",  # Запасная 72B
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
            return result["choices"][0]["message"]["content"]
        except:
            return "⚠ Не удалось получить ответ ни от одной модели. Попробуй позже."

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
            # Пробуем без Markdown
            if "parse_mode" in data:
                del data["parse_mode"]
                requests.post(url, json=data, timeout=5)
    except Exception as e:
        logger.error(f"Ошибка отправки: {e}")

def send_action(chat_id, action):
    """Отправка статуса 'печатает' и т.д."""
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
            <p>AI: {'✅ Qwen3 235B (МОНСТР!)' if ai.available else '❌ Нет API ключа'}</p>
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
    """Основной обработчик сообщений от Telegram"""
    try:
        update = request.get_json()
        
        if "message" in update:
            chat_id = update["message"]["chat"]["id"]
            text = update["message"]["text"]
            
            logger.info(f"📨 Сообщение от {chat_id}: {text[:50]}...")
            
            # Показываем что печатает
            send_action(chat_id, "typing")
            
            # Получаем ответ от AI
            reply = ai.generate(text)
            
            # Отправляем ответ
            send_message(chat_id, reply)
            
            logger.info(f"✅ Ответ отправлен")
        
        return "OK", 200
    except Exception as e:
        logger.error(f"❌ Ошибка в webhook: {e}")
        return "Error", 500

@app.route('/setwebhook')
def set_webhook():
    """Установка вебхука"""
    # Берем домен из переменной или из запроса
    railway_url = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
    
    if not railway_url:
        # Пробуем получить из request
        railway_url = request.host
        if railway_url.startswith('localhost'):
            return "❌ Локальный сервер. Используй продакшн URL."
    
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
    """Проверка статуса"""
    return {
        "bot_running": True,
        "ai_available": ai.available,
        "model": "qwen3-235b-a22b-thinking (235B МОНСТР!)",
        "telegram_token_set": bool(TELEGRAM_TOKEN),
        "openrouter_key_set": bool(OPENROUTER_API_KEY),
        "time": time.strftime("%Y-%m-%d %H:%M:%S")
    }

# ================= ЗАПУСК =================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info("="*60)
    logger.info("🚀 ЗАПУСК ДЖАРВИС БОТА")
    logger.info("="*60)
    logger.info("🤖 МОДЕЛЬ: Qwen3 235B (235 МИЛЛИАРДОВ ПАРАМЕТРОВ!)")
    logger.info("🔥 Это в 30 раз больше чем твоя старая 7B модель!")
    logger.info(f"✅ AI статус: {'доступен' if ai.available else 'недоступен'}")
    logger.info(f"🌐 Порт: {port}")
    logger.info("="*60)
    app.run(host="0.0.0.0", port=port)
