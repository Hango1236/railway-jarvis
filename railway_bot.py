import os
import requests
import base64
import logging
import threading
import time
from flask import Flask, request
from collections import defaultdict
from datetime import datetime, timedelta

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Создаем Flask приложение
app = Flask(__name__)

# ================= КОНФИГУРАЦИЯ =================
# Переменные окружения
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

# Проверка токенов
if not TELEGRAM_TOKEN:
    logger.error("❌ TELEGRAM_TOKEN не задан!")
if not OPENROUTER_API_KEY:
    logger.error("❌ OPENROUTER_API_KEY не задан!")

# Хранилище истории (в памяти)
chat_histories = {}
MAX_HISTORY = 10

# ================= ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =================
def get_file_url(file_id):
    """Получение URL файла из Telegram"""
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

def download_image(file_url):
    """Скачивание изображения"""
    try:
        response = requests.get(file_url, timeout=30)
        if response.status_code == 200:
            return base64.b64encode(response.content).decode("utf-8")
    except Exception as e:
        logger.error(f"Ошибка скачивания: {e}")
    return None

def send_telegram_message(chat_id, text):
    """Отправка сообщения в Telegram"""
    if not text:
        return
    
    # Обрезаем длинные сообщения
    if len(text) > 4000:
        text = text[:4000] + "..."
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    
    try:
        # Пробуем отправить с Markdown
        requests.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }, timeout=10)
    except:
        try:
            # Если не получается, отправляем без форматирования
            requests.post(url, json={
                "chat_id": chat_id,
                "text": text
            }, timeout=10)
        except Exception as e:
            logger.error(f"Ошибка отправки: {e}")

def send_typing(chat_id):
    """Статус 'печатает'"""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendChatAction"
        requests.post(url, json={
            "chat_id": chat_id,
            "action": "typing"
        }, timeout=5)
    except:
        pass

# ================= AI ФУНКЦИИ =================
def call_openrouter(messages, model=None):
    """Вызов OpenRouter API"""
    if not OPENROUTER_API_KEY:
        return "❌ OPENROUTER_API_KEY не задан"
    
    # Модели по умолчанию
    if model is None:
        model = "openrouter/free"
    
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": model,
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 1000
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
                return data["choices"][0]["message"]["content"]
        
        logger.error(f"Ошибка API: {response.status_code} - {response.text}")
        return None
        
    except Exception as e:
        logger.error(f"Ошибка вызова API: {e}")
        return None

def process_text(chat_id, text):
    """Обработка текстового сообщения"""
    send_typing(chat_id)
    
    # Получаем историю чата
    history = chat_histories.get(chat_id, [])
    
    # Формируем сообщения
    messages = [
        {"role": "system", "content": "Ты полезный ассистент. Отвечай кратко и по делу."}
    ]
    
    # Добавляем историю
    for msg in history[-MAX_HISTORY:]:
        messages.append(msg)
    
    # Добавляем текущее сообщение
    messages.append({"role": "user", "content": text})
    
    # Пробуем разные модели
    models = [
        "openrouter/free",
        "meta-llama/llama-3.3-70b-instruct:free",
        "deepseek/deepseek-chat-v3-0324:free"
    ]
    
    reply = None
    for model in models:
        reply = call_openrouter(messages, model)
        if reply:
            break
        time.sleep(1)
    
    if not reply:
        reply = "❌ Модели временно недоступны. Попробуйте через минуту."
    
    # Сохраняем в историю
    if chat_id not in chat_histories:
        chat_histories[chat_id] = []
    
    chat_histories[chat_id].append({"role": "user", "content": text})
    chat_histories[chat_id].append({"role": "assistant", "content": reply})
    
    # Ограничиваем историю
    if len(chat_histories[chat_id]) > MAX_HISTORY * 2:
        chat_histories[chat_id] = chat_histories[chat_id][-MAX_HISTORY * 2:]
    
    send_telegram_message(chat_id, reply)

def process_photo(chat_id, file_id, caption):
    """Обработка изображения"""
    send_typing(chat_id)
    send_telegram_message(chat_id, "🔄 Обрабатываю изображение...")
    
    # Получаем URL изображения
    file_url = get_file_url(file_id)
    if not file_url:
        send_telegram_message(chat_id, "❌ Не удалось получить изображение")
        return
    
    # Скачиваем изображение
    image_base64 = download_image(file_url)
    if not image_base64:
        send_telegram_message(chat_id, "❌ Не удалось скачать изображение")
        return
    
    # Пробуем vision модели
    vision_models = [
        "qwen/qwen2.5-vl-72b-instruct:free",
        "google/gemini-2.0-flash-exp:free"
    ]
    
    prompt = caption if caption else "Что на этом изображении? Опиши кратко."
    
    messages = [
        {"role": "system", "content": "Ты полезный ассистент. Отвечай кратко."},
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
                    "text": prompt
                }
            ]
        }
    ]
    
    reply = None
    for model in vision_models:
        reply = call_openrouter(messages, model)
        if reply:
            break
        time.sleep(1)
    
    if not reply:
        reply = (
            "⚠️ **Не удалось проанализировать изображение**\n\n"
            "Попробуйте:\n"
            "• Отправить фото в формате JPEG\n"
            "• Уменьшить размер фото\n"
            "• Описать текстом"
        )
    
    send_telegram_message(chat_id, reply)

# ================= FLASK ROUTES =================
@app.route('/webhook', methods=['POST'])
def webhook():
    """Обработка вебхука от Telegram"""
    try:
        data = request.get_json()
        logger.info(f"Получено обновление: {data}")
        
        if "message" not in data:
            return "OK", 200
        
        message = data["message"]
        chat_id = message["chat"]["id"]
        text = message.get("text", "").strip()
        
        # Команды
        if text == "/start":
            send_telegram_message(chat_id,
                "👋 Привет! Я AI-ассистент.\n\n"
                "Просто напиши вопрос или отправь фото."
            )
            return "OK", 200
        
        if text == "/help":
            send_telegram_message(chat_id,
                "📖 Помощь:\n"
                "• Текст - отвечу на вопрос\n"
                "• Фото - проанализирую\n"
                "• /clear - очистить историю"
            )
            return "OK", 200
        
        if text == "/clear":
            if chat_id in chat_histories:
                del chat_histories[chat_id]
            send_telegram_message(chat_id, "🧹 История очищена!")
            return "OK", 200
        
        # Обработка фото
        if "photo" in message:
            # Берем самое большое фото
            photos = message["photo"]
            file_id = photos[-1]["file_id"]
            caption = message.get("caption", "")
            
            thread = threading.Thread(
                target=process_photo,
                args=(chat_id, file_id, caption)
            )
            thread.start()
            return "OK", 200
        
        # Обработка документа (файла)
        if "document" in message:
            doc = message["document"]
            mime = doc.get("mime_type", "")
            
            if mime and mime.startswith("image/"):
                file_id = doc["file_id"]
                caption = message.get("caption", "")
                
                thread = threading.Thread(
                    target=process_photo,
                    args=(chat_id, file_id, caption)
                )
                thread.start()
                return "OK", 200
            else:
                send_telegram_message(chat_id, "❌ Пожалуйста, отправьте изображение")
                return "OK", 200
        
        # Обработка текста
        if text:
            thread = threading.Thread(
                target=process_text,
                args=(chat_id, text)
            )
            thread.start()
        
        return "OK", 200
        
    except Exception as e:
        logger.error(f"Ошибка в webhook: {e}")
        return "OK", 200

@app.route('/setwebhook', methods=['GET'])
def set_webhook():
    """Установка вебхука"""
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
    """Главная страница"""
    return """
    <html>
    <head><title>Telegram Bot</title></head>
    <body>
        <h1>🤖 Bot is running!</h1>
        <p>Version: 4.0 (Minimal)</p>
        <ul>
            <li><a href="/setwebhook">Set Webhook</a></li>
        </ul>
    </body>
    </html>
    """

# Для Railway - это обязательно!
application = app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
