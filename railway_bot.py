import os
import requests
import base64
import logging
import threading
import time
from flask import Flask, request

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

if not TELEGRAM_TOKEN:
    logger.error("❌ TELEGRAM_TOKEN не задан!")
if not OPENROUTER_API_KEY:
    logger.error("❌ OPENROUTER_API_KEY не задан!")
if not GEMINI_API_KEY:
    logger.error("❌ GEMINI_API_KEY не задан!")

chat_histories = {}
MAX_HISTORY = 10

# ================= ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =================
def get_file_url(file_id):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile"
        response = requests.get(url, params={"file_id": file_id}, timeout=10)
        data = response.json()
        if data.get("ok"):
            file_path = data["result"]["file_path"]
            return f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
    except Exception as e:
        logger.error(f"Ошибка getFile: {e}")
    return None

def download_image_bytes(file_url):
    try:
        response = requests.get(file_url, timeout=30)
        if response.status_code == 200:
            return response.content
    except Exception as e:
        logger.error(f"Ошибка скачивания: {e}")
    return None

def send_telegram_message(chat_id, text):
    if not text:
        return
    if len(text) > 4000:
        text = text[:4000] + "..."
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }, timeout=10)
    except:
        try:
            requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)
        except Exception as e:
            logger.error(f"Ошибка отправки: {e}")

def send_typing(chat_id):
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendChatAction"
        requests.post(url, json={"chat_id": chat_id, "action": "typing"}, timeout=5)
    except:
        pass

# ================= AI ФУНКЦИИ =================
def call_gemini_vision(image_bytes, prompt):
    """Анализ изображения через Gemini REST API"""
    if not GEMINI_API_KEY:
        return None
    try:
        image_base64 = base64.b64encode(image_bytes).decode("utf-8")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [
                {
                    "parts": [
                        {
                            "inline_data": {
                                "mime_type": "image/jpeg",
                                "data": image_base64
                            }
                        },
                        {
                            "text": prompt
                        }
                    ]
                }
            ]
        }
        response = requests.post(url, json=payload, timeout=60)
        logger.info(f"Gemini vision статус: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            if text and text.strip():
                return text.strip()
        else:
            logger.error(f"Gemini vision ошибка: {response.status_code} - {response.text[:300]}")
    except Exception as e:
        logger.error(f"Ошибка Gemini vision: {e}")
    return None

def call_gemini_text(history, text):
    """Текстовый запрос через Gemini REST API"""
    if not GEMINI_API_KEY:
        return None
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"

        # Формируем contents из истории
        contents = []
        for msg in history[-MAX_HISTORY:]:
            role = "user" if msg["role"] == "user" else "model"
            contents.append({"role": role, "parts": [{"text": msg["content"]}]})
        contents.append({"role": "user", "parts": [{"text": text}]})

        payload = {
            "system_instruction": {
                "parts": [{"text": "Ты полезный ассистент. Отвечай кратко и по делу на русском языке."}]
            },
            "contents": contents
        }

        response = requests.post(url, json=payload, timeout=60)
        logger.info(f"Gemini text статус: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            text_reply = data["candidates"][0]["content"]["parts"][0]["text"]
            if text_reply and text_reply.strip():
                return text_reply.strip()
        else:
            logger.error(f"Gemini text ошибка: {response.status_code} - {response.text[:300]}")
    except Exception as e:
        logger.error(f"Ошибка Gemini text: {e}")
    return None

def call_openrouter(messages, model):
    """Fallback через OpenRouter"""
    if not OPENROUTER_API_KEY:
        return None
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://t.me/yourbot",
        "X-Title": "Telegram Bot"
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 1500
    }
    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=60
        )
        if response.status_code == 200:
            data = response.json()
            if "choices" in data and data["choices"]:
                content = data["choices"][0]["message"]["content"]
                if content and content.strip():
                    return content.strip()
        else:
            logger.error(f"OpenRouter {model}: {response.status_code} - {response.text[:200]}")
    except Exception as e:
        logger.error(f"Ошибка OpenRouter {model}: {e}")
    return None

def process_text(chat_id, text):
    send_typing(chat_id)
    history = chat_histories.get(chat_id, [])

    # Основной — Gemini
    reply = call_gemini_text(history, text)

    # Fallback — OpenRouter
    if not reply:
        logger.info("Gemini недоступен, пробую OpenRouter...")
        messages = [
            {"role": "system", "content": "Ты полезный ассистент. Отвечай кратко и по делу на русском языке."}
        ]
        for msg in history[-MAX_HISTORY:]:
            messages.append(msg)
        messages.append({"role": "user", "content": text})
        for model in ["meta-llama/llama-3.3-70b-instruct:free", "deepseek/deepseek-chat-v3-0324:free"]:
            reply = call_openrouter(messages, model)
            if reply:
                break
            time.sleep(1)

    if not reply:
        reply = "❌ Сервисы временно недоступны. Попробуйте через минуту."

    if chat_id not in chat_histories:
        chat_histories[chat_id] = []
    chat_histories[chat_id].append({"role": "user", "content": text})
    chat_histories[chat_id].append({"role": "assistant", "content": reply})
    if len(chat_histories[chat_id]) > MAX_HISTORY * 2:
        chat_histories[chat_id] = chat_histories[chat_id][-MAX_HISTORY * 2:]

    send_telegram_message(chat_id, reply)

def process_photo(chat_id, file_id, caption):
    send_typing(chat_id)

    file_url = get_file_url(file_id)
    if not file_url:
        send_telegram_message(chat_id, "❌ Не удалось получить изображение от Telegram")
        return

    image_bytes = download_image_bytes(file_url)
    if not image_bytes:
        send_telegram_message(chat_id, "❌ Не удалось скачать изображение")
        return

    prompt = caption if caption else "Опиши подробно, что изображено на этой картинке."
    logger.info(f"Анализирую изображение, размер: {len(image_bytes)} байт")

    # Основной — Gemini
    reply = call_gemini_vision(image_bytes, prompt)
    if reply:
        logger.info("✅ Gemini успешно проанализировал изображение")

    # Fallback — OpenRouter
    if not reply:
        logger.info("Gemini не справился, пробую OpenRouter...")
        image_base64 = base64.b64encode(image_bytes).decode("utf-8")
        for model in ["google/gemini-2.0-flash-exp:free", "qwen/qwen2.5-vl-72b-instruct:free"]:
            messages = [
                {"role": "system", "content": "Ты ассистент с анализом изображений. Отвечай на русском."},
                {"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}},
                    {"type": "text", "text": prompt}
                ]}
            ]
            reply = call_openrouter(messages, model)
            if reply:
                break
            time.sleep(2)

    if not reply:
        reply = "❌ Не удалось проанализировать изображение. Попробуйте позже."

    send_telegram_message(chat_id, reply)

# ================= FLASK ROUTES =================
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        logger.info(f"Получено обновление: {data}")

        if "message" not in data:
            return "OK", 200

        message = data["message"]
        chat_id = message["chat"]["id"]
        text = message.get("text", "").strip()

        if text == "/start":
            send_telegram_message(chat_id,
                "👋 Привет! Я AI-ассистент на базе Gemini.\n\n"
                "• Напиши вопрос — отвечу\n"
                "• Отправь фото — проанализирую\n"
                "• Фото с подписью — выполню задание\n\n"
                "Команды: /help, /clear"
            )
            return "OK", 200

        if text == "/help":
            send_telegram_message(chat_id,
                "📖 Помощь:\n"
                "• Текст — отвечу на вопрос\n"
                "• Фото — проанализирую изображение\n"
                "• Фото с подписью — выполню задание по фото\n"
                "• /clear — очистить историю диалога"
            )
            return "OK", 200

        if text == "/clear":
            if chat_id in chat_histories:
                del chat_histories[chat_id]
            send_telegram_message(chat_id, "🧹 История очищена!")
            return "OK", 200

        if "photo" in message:
            photos = message["photo"]
            file_id = photos[-1]["file_id"]
            caption = message.get("caption", "")
            thread = threading.Thread(target=process_photo, args=(chat_id, file_id, caption))
            thread.start()
            return "OK", 200

        if "document" in message:
            doc = message["document"]
            mime = doc.get("mime_type", "")
            if mime and mime.startswith("image/"):
                file_id = doc["file_id"]
                caption = message.get("caption", "")
                thread = threading.Thread(target=process_photo, args=(chat_id, file_id, caption))
                thread.start()
            else:
                send_telegram_message(chat_id, "❌ Пожалуйста, отправьте изображение или текст")
            return "OK", 200

        if text:
            thread = threading.Thread(target=process_text, args=(chat_id, text))
            thread.start()

        return "OK", 200

    except Exception as e:
        logger.error(f"Ошибка в webhook: {e}")
        return "OK", 200

@app.route('/setwebhook', methods=['GET'])
def set_webhook():
    railway_url = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
    if not railway_url:
        railway_url = request.host
    webhook_url = f"https://{railway_url}/webhook"
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook"
        response = requests.get(url, params={"url": webhook_url})
        result = response.json()
        if result.get("ok"):
            return f"✅ Webhook установлен: {webhook_url}"
        else:
            return f"❌ Ошибка: {result}"
    except Exception as e:
        return f"❌ Ошибка: {e}"

@app.route('/', methods=['GET'])
def home():
    return """
    <html>
    <head><title>Telegram Bot</title></head>
    <body>
        <h1>🤖 Bot is running!</h1>
        <p>Version: 5.1 (Gemini REST)</p>
        <ul><li><a href="/setwebhook">Set Webhook</a></li></ul>
    </body>
    </html>
    """

application = app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
