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

# ================= AI КЛАСС =================
class OpenRouterAI:
    def __init__(self):
        self.api_key = OPENROUTER_API_KEY
        self.available = bool(self.api_key)
    
    def generate(self, user_text):
        if not self.available:
            return "❌ Нет API ключа"
        
        try:
            logger.info(f"Отправляю запрос к DeepSeek...")
            
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": "deepseek/deepseek-chat:free",
                    "messages": [
                        {"role": "system", "content": "Ты полезный ассистент. Отвечай кратко и по делу."},
                        {"role": "user", "content": user_text}
                    ],
                    "temperature": 0.1,
                    "max_tokens": 1000
                },
                timeout=30
            )
            
            result = response.json()
            logger.info(f"Ответ от API: {result}")
            
            if "choices" in result and len(result["choices"]) > 0:
                return result["choices"][0]["message"]["content"]
            elif "error" in result:
                return f"❌ Ошибка API: {result['error'].get('message', 'Неизвестная ошибка')}"
            else:
                return f"❌ Странный ответ: {result}"
            
        except Exception as e:
            logger.error(f"Ошибка: {e}")
            return f"❌ Ошибка: {str(e)[:100]}"

ai = OpenRouterAI()

# ================= TELEGRAM ФУНКЦИИ =================
def send_message(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    params = {"chat_id": chat_id, "text": text}
    try:
        requests.get(url, params=params, timeout=5)
    except Exception as e:
        logger.error(f"Ошибка: {e}")

# ================= FLASK РОУТЫ =================
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        update = request.get_json()
        
        if "message" in update:
            chat_id = update["message"]["chat"]["id"]
            text = update["message"].get("text", "")
            
            logger.info(f"Сообщение: {text}")
            reply = ai.generate(text)
            logger.info(f"Ответ: {reply}")
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
    webhook_url = f"https://{railway_url}/webhook"
    requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook", params={"url": webhook_url})
    return "Webhook установлен!"

@app.route('/')
def home():
    return "🤖 Бот работает!"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
