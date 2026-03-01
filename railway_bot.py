import os
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Flask, request
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ================= КОНФИГУРАЦИЯ =================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

# ================= HTTP СЕССИЯ С RETRY =================
def make_session():
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"]
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

session = make_session()

# ================= AI КЛАСС =================
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

    def call_model(self, model, messages):
        try:
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
                timeout=(10, 60)  # (connect timeout, read timeout)
            )

            logger.info(f"[{model}] HTTP: {response.status_code}")
            result = response.json()
            logger.info(f"[{model}] Ответ: {result}")

            if "choices" in result and result["choices"]:
                content = result["choices"][0]["message"].get("content", "")
                if content and content.strip():
                    return content.strip(), None
                return None, "Пустой ответ"

            if "error" in result:
                err = result["error"]
                return None, f"code={err.get('code', '')}: {err.get('message', '')}"

            return None, f"Неожиданный ответ: {result}"

        except requests.exceptions.Timeout:
            return None, "Таймаут"
        except requests.exceptions.ConnectionError as e:
            return None, f"Нет соединения: {e}"
        except Exception as e:
            return None, str(e)

    def generate(self, user_text):
        if not self.available:
            return "❌ OPENROUTER_API_KEY не задан в переменных окружения Railway"

        messages = [
            {"role": "system", "content": "Ты полезный ассистент. Отвечай кратко и по делу на языке пользователя."},
            {"role": "user", "content": user_text}
        ]

        errors = []
        for model in self.models:
            logger.info(f"Пробую модель: {model}")
            text, error = self.call_model(model, messages)
            if text:
                logger.info(f"✅ Успех: {model}")
                return text
            logger.warning(f"❌ {model} → {error}")
            errors.append(f"{model}: {error}")

        error_summary = "\n".join(errors[:3])
        return f"❌ Все модели недоступны.\n\nОшибки:\n{error_summary}"

    def debug_check(self):
        if not self.api_key:
            return {"status": "error", "message": "OPENROUTER_API_KEY не задан"}
        try:
            r = session.get(
                "https://openrouter.ai/api/v1/auth/key",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=(5, 10)
            )
            key_info = r.json()
        except Exception as e:
            key_info = {"error": str(e)}

        messages = [{"role": "user", "content": "Say: OK"}]
        text, error = self.call_model("meta-llama/llama-3.3-70b-instruct:free", messages)

        return {
            "api_key_prefix": self.api_key[:12] + "..." if self.api_key else None,
            "key_info": key_info,
            "test_result": text if text else f"ОШИБКА: {error}"
        }


ai = OpenRouterAI()


# ================= TELEGRAM =================
def send_message(chat_id, text, parse_mode="Markdown"):
    if len(text) > 4000:
        text = text[:4000] + "..."

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    # Пробуем с Markdown
    try:
        r = session.post(
            url,
            json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode},
            timeout=(10, 30)  # connect=10s, read=30s
        )
        if r.status_code == 200:
            return True
        logger.warning(f"Telegram вернул {r.status_code}: {r.text}")
    except requests.exceptions.Timeout:
        logger.error(f"Таймаут отправки в Telegram (chat_id={chat_id})")
    except Exception as e:
        logger.error(f"Ошибка отправки: {e}")

    # Пробуем без Markdown
    try:
        r = session.post(
            url,
            json={"chat_id": chat_id, "text": text},
            timeout=(10, 30)
        )
        return r.status_code == 200
    except Exception as e:
        logger.error(f"Повторная ошибка отправки: {e}")
        return False


def send_typing(chat_id):
    try:
        session.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendChatAction",
            json={"chat_id": chat_id, "action": "typing"},
            timeout=(5, 10)
        )
    except Exception:
        pass


# ================= РОУТЫ =================
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
            send_message(chat_id, "👋 Привет! Я AI-ассистент. Задай любой вопрос!")
            return "OK", 200

        if text == "/help":
            send_message(chat_id,
                "🤖 *Что я умею:*\n"
                "• Отвечать на вопросы\n"
                "• Писать тексты и код\n"
                "• Переводить и объяснять\n\n"
                "Просто напиши мне что-нибудь!"
            )
            return "OK", 200

        send_typing(chat_id)
        reply = ai.generate(text)
        send_message(chat_id, reply)

        return "OK", 200

    except Exception as e:
        logger.error(f"Ошибка webhook: {e}")
        return "Error", 500


@app.route('/debug')
def debug():
    import json
    result = ai.debug_check()
    return f"<pre>{json.dumps(result, ensure_ascii=False, indent=2)}</pre>"


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
        else:
            return f"❌ Ошибка: {result}"
    except Exception as e:
        return f"❌ Исключение: {e}"


@app.route('/status')
def status():
    return (
        f"🤖 Бот работает\n\n"
        f"Telegram: {'✅' if TELEGRAM_TOKEN else '❌ Не задан'}\n"
        f"OpenRouter: {'✅' if OPENROUTER_API_KEY else '❌ Не задан'}\n\n"
        f"Диагностика AI: /debug"
    )


@app.route('/')
def home():
    return "🤖 Бот работает! <a href='/status'>Статус</a> | <a href='/debug'>Диагностика</a> | <a href='/setwebhook'>Установить webhook</a>"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"🚀 Запуск на порту {port}")
    logger.info(f"Telegram: {'✅' if TELEGRAM_TOKEN else '❌'}")
    logger.info(f"OpenRouter: {'✅' if OPENROUTER_API_KEY else '❌'}")
    app.run(host="0.0.0.0", port=port, debug=False)
