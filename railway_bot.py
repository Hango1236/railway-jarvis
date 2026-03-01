import os
import requests
import json
from flask import Flask, request
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
        self.models = [
            "deepseek/deepseek-chat-v3-0324:free",
            "deepseek/deepseek-r1:free",
            "meta-llama/llama-3.1-8b-instruct:free",
            "mistralai/mistral-7b-instruct:free",
        ]

    def generate(self, user_text):
        if not self.available:
            return "❌ Нет API ключа OpenRouter. Укажи OPENROUTER_API_KEY в переменных окружения."

        for model in self.models:
            try:
                logger.info(f"Пробую модель: {model}")

                response = requests.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": "https://telegram-bot.app",
                        "X-Title": "Telegram Bot"
                    },
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": "Ты полезный ассистент. Отвечай кратко и по делу на языке пользователя."},
                            {"role": "user", "content": user_text}
                        ],
                        "temperature": 0.7,
                        "max_tokens": 1000
                    },
                    timeout=30
                )

                result = response.json()
                logger.info(f"Статус: {response.status_code}, Ответ: {result}")

                if "choices" in result and len(result["choices"]) > 0:
                    content = result["choices"][0]["message"]["content"]
                    if content and content.strip():
                        logger.info(f"✅ Успешно через модель: {model}")
                        return content.strip()

                if "error" in result:
                    error_msg = result["error"].get("message", "Неизвестная ошибка")
                    logger.warning(f"❌ Модель {model} вернула ошибку: {error_msg}")
                    continue

            except requests.exceptions.Timeout:
                logger.warning(f"⏱ Таймаут для модели: {model}")
                continue
            except Exception as e:
                logger.error(f"❌ Ошибка с моделью {model}: {e}")
                continue

        return "❌ Все модели временно недоступны. Попробуйте позже."


ai = OpenRouterAI()


# ================= TELEGRAM ФУНКЦИИ =================
def send_message(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    params = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        logger.info(f"Сообщение отправлено: {r.status_code}")
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения: {e}")


def send_typing(chat_id):
    """Показывает анимацию печатания"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendChatAction"
    params = {"chat_id": chat_id, "action": "typing"}
    try:
        requests.get(url, params=params, timeout=5)
    except Exception:
        pass


# ================= FLASK РОУТЫ =================
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        update = request.get_json()
        logger.info(f"Входящий апдейт: {update}")

        if "message" in update:
            chat_id = update["message"]["chat"]["id"]
            text = update["message"].get("text", "").strip()

            if not text:
                return "OK", 200

            logger.info(f"Сообщение от {chat_id}: {text}")

            # Обработка команд
            if text == "/start":
                reply = (
                    "👋 Привет! Я AI-ассистент на базе DeepSeek.\n\n"
                    "Просто напиши мне любой вопрос — я отвечу!"
                )
                send_message(chat_id, reply)
                return "OK", 200

            if text == "/help":
                reply = (
                    "🤖 *Что я умею:*\n"
                    "• Отвечать на любые вопросы\n"
                    "• Писать тексты и код\n"
                    "• Переводить и объяснять\n\n"
                    "Просто напиши мне что-нибудь!"
                )
                send_message(chat_id, reply)
                return "OK", 200

            # Показываем анимацию печатания
            send_typing(chat_id)

            # Генерируем ответ
            reply = ai.generate(text)
            logger.info(f"Ответ: {reply[:100]}...")
            send_message(chat_id, reply)

        return "OK", 200

    except Exception as e:
        logger.error(f"❌ Ошибка в webhook: {e}")
        return "Error", 500


@app.route('/setwebhook')
def set_webhook():
    railway_url = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
    if not railway_url:
        railway_url = request.host

    webhook_url = f"https://{railway_url}/webhook"
    logger.info(f"Устанавливаю webhook: {webhook_url}")

    r = requests.get(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook",
        params={"url": webhook_url},
        timeout=10
    )
    result = r.json()
    logger.info(f"Результат setWebhook: {result}")

    if result.get("ok"):
        return f"✅ Webhook установлен: {webhook_url}"
    else:
        return f"❌ Ошибка: {result.get('description', 'Неизвестная ошибка')}"


@app.route('/status')
def status():
    ai_status = "✅ Готов" if ai.available else "❌ Нет API ключа"
    bot_status = "✅ Настроен" if TELEGRAM_TOKEN else "❌ Нет токена"
    return (
        f"🤖 Бот работает!\n\n"
        f"Telegram: {bot_status}\n"
        f"AI: {ai_status}\n"
        f"Модели: {', '.join(ai.models)}"
    )


@app.route('/')
def home():
    return "🤖 Бот работает! Перейди на /status для деталей или /setwebhook для настройки."


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"🚀 Запуск на порту {port}")
    logger.info(f"Telegram токен: {'✅' if TELEGRAM_TOKEN else '❌ Не найден'}")
    logger.info(f"OpenRouter ключ: {'✅' if OPENROUTER_API_KEY else '❌ Не найден'}")
    app.run(host="0.0.0.0", port=port, debug=False)
