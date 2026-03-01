import os
import requests
import base64
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

# ================= TELEGRAM HELPERS =================
def get_file_url(file_id):
    """Получаем прямую ссылку на файл через Telegram API"""
    try:
        r = session.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile",
            params={"file_id": file_id},
            timeout=(10, 30)
        )
        result = r.json()
        if result.get("ok"):
            file_path = result["result"]["file_path"]
            return f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
    except Exception as e:
        logger.error(f"Ошибка getFile: {e}")
    return None

def download_image_base64(file_url):
    """Скачиваем картинку и кодируем в base64"""
    try:
        r = session.get(file_url, timeout=(10, 30))
        if r.status_code == 200:
            return base64.b64encode(r.content).decode("utf-8")
    except Exception as e:
        logger.error(f"Ошибка скачивания картинки: {e}")
    return None

# ================= AI =================
class OpenRouterAI:
    def __init__(self):
        self.api_key = OPENROUTER_API_KEY
        self.available = bool(self.api_key)
        # Модели с поддержкой vision (картинок)
        self.vision_models = [
            "google/gemini-2.0-flash-exp:free",
            "meta-llama/llama-3.2-11b-vision-instruct:free",
            "qwen/qwen2-vl-7b-instruct:free",
        ]
        # Обычные текстовые модели
        self.text_models = [
            "openrouter/free",
            "meta-llama/llama-3.3-70b-instruct:free",
            "deepseek/deepseek-r1:free",
            "deepseek/deepseek-chat-v3-0324:free",
            "mistralai/mistral-small-3.1-24b-instruct:free",
        ]

    def generate_text(self, user_text):
        """Обычный текстовый запрос"""
        messages = [
            {"role": "system", "content": "Ты полезный ассистент. Отвечай кратко и по делу на языке пользователя."},
            {"role": "user", "content": user_text}
        ]
        return self._call_models(self.text_models, messages)

    def generate_with_image(self, user_text, image_base64):
        """Запрос с картинкой — используем vision-модели"""
        caption = user_text if user_text else "Что на этом изображении? Опиши подробно и помоги разобраться."

        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{image_base64}"
                        }
                    },
                    {
                        "type": "text",
                        "text": caption
                    }
                ]
            }
        ]
        result = self._call_models(self.vision_models, messages)

        # Если vision-модели не сработали — пробуем текстовые с описанием
        if result.startswith("❌"):
            logger.warning("Vision модели недоступны, пробую текстовые...")
            fallback_messages = [
                {"role": "system", "content": "Ты полезный ассистент."},
                {"role": "user", "content": f"Пользователь прислал картинку с вопросом: {caption}\nОтветь максимально полезно, хотя у тебя нет доступа к картинке."}
            ]
            return self._call_models(self.text_models, fallback_messages)

        return result

    def _call_models(self, models, messages):
        if not self.available:
            return "❌ OPENROUTER_API_KEY не задан"

        for model in models:
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
                        "max_tokens": 1500
                    },
                    timeout=(10, 90)
                )
                result = resp.json()
                logger.info(f"[{model}] {resp.status_code}")

                if "choices" in result and result["choices"]:
                    content = result["choices"][0]["message"].get("content", "").strip()
                    if content:
                        logger.info(f"✅ Успех: {model}")
                        return content

                if "error" in result:
                    logger.warning(f"❌ {model}: {result['error']}")

            except Exception as e:
                logger.warning(f"❌ {model}: {e}")

        return "❌ Все модели недоступны. Попробуй позже."


ai = OpenRouterAI()

# ================= ОТПРАВКА =================
def send_async(chat_id, text):
    def _send():
        t = text[:4000] + "..." if len(text) > 4000 else text
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        for parse_mode in ["Markdown", None]:
            payload = {"chat_id": chat_id, "text": t}
            if parse_mode:
                payload["parse_mode"] = parse_mode
            try:
                r = session.post(url, json=payload, timeout=(15, 60))
                if r.status_code == 200:
                    logger.info(f"✅ Отправлено chat_id={chat_id}")
                    return
                logger.warning(f"Telegram {r.status_code}: {r.text[:200]}")
            except Exception as e:
                logger.error(f"Ошибка отправки: {e}")
    threading.Thread(target=_send, daemon=True).start()


def send_typing(chat_id):
    try:
        session.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendChatAction",
            json={"chat_id": chat_id, "action": "typing"},
            timeout=(5, 10)
        )
    except Exception:
        pass


# ================= ОБРАБОТКА =================
def process_text(chat_id, text):
    send_typing(chat_id)
    reply = ai.generate_text(text)
    send_async(chat_id, reply)


def process_photo(chat_id, file_id, caption):
    send_typing(chat_id)
    send_async(chat_id, "🔍 Анализирую изображение...")

    file_url = get_file_url(file_id)
    if not file_url:
        send_async(chat_id, "❌ Не удалось получить изображение от Telegram")
        return

    image_base64 = download_image_base64(file_url)
    if not image_base64:
        send_async(chat_id, "❌ Не удалось скачать изображение")
        return

    reply = ai.generate_with_image(caption, image_base64)
    send_async(chat_id, reply)


# ================= FLASK =================
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        update = request.get_json()

        if "message" not in update:
            return "OK", 200

        msg = update["message"]
        chat_id = msg["chat"]["id"]

        # Команды
        text = msg.get("text", "").strip()
        if text == "/start":
            send_async(chat_id, "👋 Привет! Я AI-ассистент.\n\nМогу:\n• Отвечать на вопросы\n• Анализировать картинки 🖼\n• Решать задачи по фото\n\nПросто напиши или отправь фото!")
            return "OK", 200
        if text == "/help":
            send_async(chat_id, "📖 Как пользоваться:\n• Напиши любой вопрос\n• Отправь картинку (можно с подписью-вопросом)\n• Например: отправь фото задачи и спроси «реши это»")
            return "OK", 200

        # Фото с подписью или без
        if "photo" in msg:
            # Берём самое высокое качество (последний элемент массива)
            file_id = msg["photo"][-1]["file_id"]
            caption = msg.get("caption", "").strip()
            logger.info(f"Фото от {chat_id}, подпись: '{caption}'")
            threading.Thread(target=process_photo, args=(chat_id, file_id, caption), daemon=True).start()
            return "OK", 200

        # Документ-изображение (отправлено как файл без сжатия)
        if "document" in msg:
            doc = msg["document"]
            mime = doc.get("mime_type", "")
            if mime.startswith("image/"):
                file_id = doc["file_id"]
                caption = msg.get("caption", "").strip()
                logger.info(f"Документ-изображение от {chat_id}")
                threading.Thread(target=process_photo, args=(chat_id, file_id, caption), daemon=True).start()
                return "OK", 200

        # Обычный текст
        if text:
            logger.info(f"Текст от {chat_id}: {text}")
            threading.Thread(target=process_text, args=(chat_id, text), daemon=True).start()

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
        return f"✅ Webhook: {webhook_url}" if result.get("ok") else f"❌ {result}"
    except Exception as e:
        return f"❌ {e}"


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
        "telegram": "✅" if TELEGRAM_TOKEN else "❌",
        "openrouter_key": (OPENROUTER_API_KEY[:12] + "...") if OPENROUTER_API_KEY else "❌",
        "key_info": key_info,
        "vision_models": ai.vision_models,
        "text_models": ai.text_models,
    }
    return f"<pre>{json.dumps(result, ensure_ascii=False, indent=2)}</pre>"


@app.route('/')
def home():
    return "🤖 Бот работает! <a href='/status'>Статус</a> | <a href='/debug'>Диагностика</a> | <a href='/setwebhook'>Webhook</a>"


@app.route('/status')
def status():
    return f"🤖 OK | Telegram: {'✅' if TELEGRAM_TOKEN else '❌'} | OpenRouter: {'✅' if OPENROUTER_API_KEY else '❌'}"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    logger.info(f"🚀 Запуск на порту {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
