import os
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import telebot
import logging
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= КОНФИГУРАЦИЯ =================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

# ================= HTTP СЕССИЯ =================
def make_session():
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    return session

session = make_session()

# ================= AI =================
class OpenRouterAI:
    def __init__(self):
        self.api_key = OPENROUTER_API_KEY
        self.available = bool(self.api_key)
        self.models = [
            "openrouter/free",
            "meta-llama/llama-3.3-70b-instruct:free",
            "deepseek/deepseek-r1:free",
            "deepseek/deepseek-chat-v3-0324:free",
            "google/gemini-2.0-flash-exp:free",
            "mistralai/mistral-small-3.1-24b-instruct:free",
        ]

    def generate(self, user_text):
        if not self.available:
            return "❌ OPENROUTER_API_KEY не задан"

        messages = [
            {"role": "system", "content": "Ты полезный ассистент. Отвечай кратко и по делу на языке пользователя."},
            {"role": "user", "content": user_text}
        ]

        for model in self.models:
            try:
                logger.info(f"Пробую модель: {model}")
                response = session.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": "https://t.me/bot",
                        "X-Title": "Telegram Bot"
                    },
                    json={
                        "model": model,
                        "messages": messages,
                        "temperature": 0.7,
                        "max_tokens": 1000
                    },
                    timeout=(10, 60)
                )
                result = response.json()
                logger.info(f"[{model}] {result}")

                if "choices" in result and result["choices"]:
                    content = result["choices"][0]["message"].get("content", "").strip()
                    if content:
                        logger.info(f"✅ Успех: {model}")
                        return content

                if "error" in result:
                    logger.warning(f"❌ {model}: {result['error']}")
                    continue

            except Exception as e:
                logger.warning(f"❌ {model}: {e}")
                continue

        return "❌ Все модели недоступны. Попробуй позже."

ai = OpenRouterAI()

# ================= TELEGRAM BOT (POLLING) =================
bot = telebot.TeleBot(TELEGRAM_TOKEN, threaded=False)

@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, "👋 Привет! Я AI-ассистент. Задай любой вопрос!")

@bot.message_handler(commands=['help'])
def help_cmd(message):
    bot.reply_to(message, 
        "🤖 Что я умею:\n"
        "• Отвечать на вопросы\n"
        "• Писать тексты и код\n"
        "• Переводить и объяснять\n\n"
        "Просто напиши мне что-нибудь!"
    )

@bot.message_handler(func=lambda m: True)
def handle_message(message):
    chat_id = message.chat.id
    text = message.text.strip()
    logger.info(f"Сообщение от {chat_id}: {text}")

    bot.send_chat_action(chat_id, 'typing')
    reply = ai.generate(text)
    logger.info(f"Ответ: {reply[:80]}")
    bot.reply_to(message, reply)

# ================= ЗАПУСК =================
if __name__ == "__main__":
    logger.info(f"🚀 Запуск бота (polling режим)")
    logger.info(f"Telegram: {'✅' if TELEGRAM_TOKEN else '❌ Не задан'}")
    logger.info(f"OpenRouter: {'✅' if OPENROUTER_API_KEY else '❌ Не задан'}")

    while True:
        try:
            logger.info("Запускаю polling...")
            bot.infinity_polling(timeout=30, long_polling_timeout=20)
        except Exception as e:
            logger.error(f"Ошибка polling: {e}")
            time.sleep(5)
            logger.info("Перезапускаю...")
