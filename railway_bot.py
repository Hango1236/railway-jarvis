import os
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Flask, request
import logging
import threading

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

# ================= HTTP СЕССИЯ =================
def make_session():
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s

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
                resp = session.post(
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
                result = resp.json()
                logger.info(f"[{model}] {resp.status_code}: {result}")

                if "choices" in result and result["choices"]:
                    content = result["choices"][0]["message"].get("content", "").strip()
                    if content:
                        return content

                if "error" in result:
                    logger.warning(f"❌ {model}: {result['error']}")

            except Exception as e:
                logger.warning(f"❌ {model}: {e}")

        return "❌ Все модели недоступны. Попробуй позже."

ai = OpenRouterAI()

# ================= ОТПРАВКА В TELEGRAM =================
def send_async(chat_id, text):
    """Отправляем в отдельном потоке чтобы не блокировать webhook"""
    def _send():
        if len(text) > 4000:
            t = text[:4000] + "..."
        else:
            t = text

        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

        # Пробуем с Markdown
        for parse_mode in ["Markdown", None]:
            payload = {"chat_id": chat_id, "text": t}
            if parse_mode:
                payload["parse_mode"] = parse_mode
            try:
                r = session.post(url, json=payload, timeout=(15, 60))
                if r.status_code == 200:
                    logger.info(f"✅ Сообщение отправлено chat_id={chat_id}")
                    return
                else:
                    logger.warning(f"Telegram {r.status_code}: {r.text[:200]}")
                    if parse_mode:
                        continue  # пробуем без Markdown
                    else:
                        break
            except requests.exceptions.Timeout:
                logger.error(f"Таймаут отправки (попытка parse_mode={parse_mode})")
            except Exception as e:
                logger.error(f"Ошибка отправки: {e}")
                break

    thread = threading.Thread(target=_send, daemon=True)
    thread.start()

def send_typing(chat_id):
    try:
        session.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendChatAction",
            json={"chat_id": chat_id, "action": "typing"},
            timeout=(5, 10)
        )
    except Exception:
        pass

# ================= ОБРАБОТКА СООБЩЕНИЙ =================
def process_message(chat_id, text):
    """Запускается в отдельном потоке — не блокирует ответ Telegram"""
    send_typing(chat_id)
    reply = ai.generate(text)
    send_async(chat_id, reply)

# ================= FLASK =================
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        update = request.get_json()

        if "message" not in update:
            return "OK", 200

        chat_id = update["message"]["chat"]["id"]
        text = update["message"].get("text", "").strip()

        if not text:
            return "OK", 200

        logger.info(f"Входящее от {chat_id}: {text}")

        if text == "/start":
            send_async(chat_id, "👋 Привет! Я AI-ассистент. Задай любой вопрос!")
            return "OK", 200

        if text == "/help":
            send_async(chat_id,
                "🤖 Что я умею:\n"
                "• Отвечать на вопросы\n"
                "• Писать тексты и код\n"
                "• Переводить и объяснять\n\n"
                "Просто напиши мне что-нибудь!"
            )
            return "OK", 200

        # Обрабатываем в фоне — сразу возвращаем 200 Telegram
        thread = threading.Thread(target=process_message, args=(chat_id, text), daemon=True)
        thread.start()

        return "OK", 200

    except Exception as e:
        logger.error(f"Ошибка webhook: {e}")
        return "Error", 500


@app.route('/setwebhook')
def set_webhook():
    railway_url = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
    if not railway_url:
        railway_url = request.host
    webhook_url = f"https://{railway_url}/webhook"
    try:
        r = session.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook",
            params={"url": webhook_url},
            timeout=(10, 30)
        )
        result = r.json()
        if result.get("ok"):
            return f"✅ Webhook установлен: {webhook_url}"
        return f"❌ Ошибка: {result}"
    except Exception as e:
        return f"❌ Исключение: {e}"


@app.route('/debug')
def debug():
    import json
    try:
        r = session.get(
            "https://openrouter.ai/api/v1/auth/key",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
            timeout=(5, 10)
        )
        key_info = r.json()
    except Exception as e:
        key_info = {"error": str(e)}

    result = {
        "telegram_token": "✅" if TELEGRAM_TOKEN else "❌ Не задан",
        "openrouter_key": (OPENROUTER_API_KEY[:12] + "...") if OPENROUTER_API_KEY else "❌ Не задан",
        "key_info": key_info,
    }
    return f"<pre>{json.dumps(result, ensure_ascii=False, indent=2)}</pre>"


@app.route('/status')
def status():
    return (
        f"🤖 Бот работает\n"
        f"Telegram: {'✅' if TELEGRAM_TOKEN else '❌'}\n"
        f"OpenRouter: {'✅' if OPENROUTER_API_KEY else '❌'}\n"
        f"Диагностика: /debug | Webhook: /setwebhook"
    )


@app.route('/')
def home():
    return "🤖 Бот работает! <a href='/status'>Статус</a> | <a href='/debug'>Диагностика</a> | <a href='/setwebhook'>Установить webhook</a>"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"🚀 Запуск на порту {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
