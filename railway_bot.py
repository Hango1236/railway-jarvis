Falseimport os
import requests
import base64
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Flask, request
import logging
import threading
from collections import defaultdict
from datetime import datetime, timedelta
import time
import sys
import json
import io
import mimetypes

# Попытка импорта PIL
try:
    from PIL import Image, ImageOps
    from PIL import UnidentifiedImageError
    PIL_AVAILABLE = True
    logger = logging.getLogger(__name__)
    logger.info("PIL успешно загружен")
except ImportError:
    PIL_AVAILABLE = False
    logging.warning("PIL не установлен. Установите: pip install pillow")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Переменные окружения
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

if not TELEGRAM_TOKEN:
    logger.error("❌ TELEGRAM_TOKEN не задан!")
if not OPENROUTER_API_KEY:
    logger.error("❌ OPENROUTER_API_KEY не задан!")

# Хранилище истории диалогов
chat_histories = defaultdict(lambda: {"messages": [], "last_time": datetime.now()})
HISTORY_TTL = timedelta(minutes=30)
MAX_HISTORY_MESSAGES = 10

# Статистика ошибок
error_stats = defaultdict(lambda: {"count": 0, "last_time": None, "type": None})

def clean_old_histories():
    """Очистка старых диалогов"""
    now = datetime.now()
    to_delete = []
    for chat_id, data in chat_histories.items():
        if now - data["last_time"] > HISTORY_TTL:
            to_delete.append(chat_id)
    for chat_id in to_delete:
        del chat_histories[chat_id]
        if chat_id in error_stats:
            del error_stats[chat_id]

def add_to_history(chat_id, role, content):
    """Добавление сообщения в историю"""
    clean_old_histories()
    
    history = chat_histories[chat_id]
    history["messages"].append({"role": role, "content": content[:500]})  # Обрезаем для логов
    history["last_time"] = datetime.now()
    
    if len(history["messages"]) > MAX_HISTORY_MESSAGES * 2:
        history["messages"] = history["messages"][-MAX_HISTORY_MESSAGES * 2:]

def get_recent_history(chat_id):
    """Получение истории диалога"""
    clean_old_histories()
    data = chat_histories.get(chat_id, {"messages": [], "last_time": datetime.now()})
    return data["messages"][-MAX_HISTORY_MESSAGES:]

def make_session():
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s

session = make_session()

# ================= TELEGRAM HELPERS =================
def get_file_url(file_id):
    """Получение URL файла из Telegram"""
    try:
        r = session.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile",
            params={"file_id": file_id},
            timeout=30
        )
        result = r.json()
        if result.get("ok"):
            file_path = result["result"]["file_path"]
            return f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
    except Exception as e:
        logger.error(f"getFile error: {e}")
    return None

def optimize_image(image_data, max_size=1024, max_file_size=10*1024*1024):
    """
    Оптимизация изображения для отправки в API
    Поддерживает: JPEG, PNG, WEBP, GIF, BMP
    """
    if not PIL_AVAILABLE:
        logger.warning("PIL не доступен, возвращаем оригинал")
        return base64.b64encode(image_data).decode('utf-8'), None
    
    try:
        # Открываем изображение
        img = Image.open(io.BytesIO(image_data))
        original_format = img.format
        logger.info(f"Исходный формат: {original_format}, размер: {img.size}, режим: {img.mode}")
        
        # Автокоррекция ориентации
        img = ImageOps.exif_transpose(img)
        
        # Конвертируем в RGB если нужно
        if img.mode in ('RGBA', 'LA', 'P'):
            logger.info(f"Конвертация из {img.mode} в RGB")
            # Для PNG с прозрачностью
            if img.mode == 'RGBA':
                background = Image.new('RGB', img.size, (255, 255, 255))
                background.paste(img, mask=img.split()[3])  # Используем альфа-канал как маску
                img = background
            elif img.mode == 'P':
                img = img.convert('RGB')
            else:
                img = img.convert('RGB')
        
        # Уменьшаем если слишком большое
        if max(img.width, img.height) > max_size:
            ratio = max_size / max(img.width, img.height)
            new_size = (int(img.width * ratio), int(img.height * ratio))
            img = img.resize(new_size, Image.Resampling.LANCZOS)
            logger.info(f"Уменьшено до: {new_size}")
        
        # Сохраняем в JPEG с оптимизацией
        output = io.BytesIO()
        
        # Определяем качество в зависимости от исходного размера
        quality = 85
        if len(image_data) > 5 * 1024 * 1024:  # > 5 MB
            quality = 70
        elif len(image_data) > 2 * 1024 * 1024:  # > 2 MB
            quality = 80
        
        img.save(output, format='JPEG', quality=quality, optimize=True)
        optimized_data = output.getvalue()
        
        logger.info(f"Оптимизация: {len(image_data)} -> {len(optimized_data)} байт")
        
        return base64.b64encode(optimized_data).decode('utf-8'), None
        
    except UnidentifiedImageError:
        logger.error("Не удалось определить формат изображения")
        return None, "invalid_format"
    except Exception as e:
        logger.error(f"Ошибка оптимизации: {e}")
        # В случае ошибки возвращаем оригинал
        return base64.b64encode(image_data).decode('utf-8'), None

def process_telegram_file(file_id, caption):
    """Полная обработка файла из Telegram"""
    try:
        # Получаем URL
        file_url = get_file_url(file_id)
        if not file_url:
            return None, "file_url_error", None
        
        # Скачиваем файл
        r = session.get(file_url, timeout=60)
        if r.status_code != 200:
            return None, f"download_error_{r.status_code}", None
        
        file_data = r.content
        file_size = len(file_data)
        
        # Проверяем размер
        if file_size > 20 * 1024 * 1024:  # 20 MB лимит
            return None, "too_large", f"Размер: {file_size/1024/1024:.1f} МБ"
        
        # Оптимизируем изображение
        image_base64, error = optimize_image(file_data)
        
        if error:
            return None, error, None
        
        return image_base64, None, f"Размер после оптимизации: {len(file_data)/1024/1024:.1f} МБ"
        
    except Exception as e:
        logger.error(f"process_telegram_file error: {e}")
        return None, str(e), None

# ================= AI =================
class OpenRouterAI:
    def __init__(self):
        self.api_key = OPENROUTER_API_KEY
        self.available = bool(self.api_key)
        self.request_count = 0
        self.error_count = 0

        # Vision модели
        self.vision_models = [
            "qwen/qwen2.5-vl-72b-instruct:free",
            "qwen/qwen2.5-vl-7b-instruct:free",
            "meta-llama/llama-3.2-11b-vision-instruct:free",
            "google/gemini-2.0-flash-exp:free",
            "mistralai/mistral-small-3.1-24b-instruct:free",
        ]

        # Текстовые модели
        self.text_models = [
            "openrouter/free",
            "meta-llama/llama-3.3-70b-instruct:free",
            "deepseek/deepseek-r1:free",
            "deepseek/deepseek-chat-v3-0324:free",
        ]

    def _call(self, model, messages):
        """Вызов модели"""
        self.request_count += 1
        
        try:
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
                    "max_tokens": 2000
                },
                timeout=120
            )
            
            result = resp.json()
            
            if resp.status_code == 200 and "choices" in result:
                content = result["choices"][0]["message"].get("content", "")
                if content:
                    return content, None
            
            # Обработка ошибок
            if "error" in result:
                err = result["error"]
                msg = err.get("message", str(err))
                
                if any(x in msg.lower() for x in ["overloaded", "rate limit", "too many requests"]):
                    return None, "model_busy"
                return None, msg
            
            return None, f"HTTP {resp.status_code}"
            
        except Exception as e:
            self.error_count += 1
            return None, str(e)

    def generate_text(self, chat_id, user_text):
        """Генерация текста"""
        history = get_recent_history(chat_id)
        
        messages = [
            {"role": "system", "content": "Ты полезный ассистент. Отвечай кратко на языке пользователя. НИКОГДА не используй LaTeX. Формулы пиши текстом: sqrt(5)/5, x^2."}
        ]
        messages.extend(history)
        messages.append({"role": "user", "content": user_text})
        
        # Пробуем модели по очереди
        for model in self.text_models:
            text, error = self._call(model, messages)
            if text:
                add_to_history(chat_id, "user", user_text)
                add_to_history(chat_id, "assistant", text)
                return text
            
            if error == "model_busy":
                time.sleep(1)
                continue
        
        return "❌ Все модели временно недоступны. Попробуйте через минуту."

    def generate_with_image(self, chat_id, caption, image_base64):
        """Анализ изображения"""
        history = get_recent_history(chat_id)
        
        prompt = caption if caption else "Что на этом изображении? Опиши подробно."
        
        messages = [
            {"role": "system", "content": "Ты полезный ассистент. Отвечай на языке пользователя. НИКОГДА не используй LaTeX."}
        ]
        messages.extend(history)
        
        # Формируем сообщение с изображением
        user_content = [
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{image_base64}",
                }
            },
            {"type": "text", "text": prompt}
        ]
        messages.append({"role": "user", "content": user_content})
        
        # Пробуем vision модели
        busy_count = 0
        for model in self.vision_models:
            text, error = self._call(model, messages)
            
            if text:
                add_to_history(chat_id, "user", f"[📸] {prompt}")
                add_to_history(chat_id, "assistant", text)
                return text
            
            if error == "model_busy":
                busy_count += 1
                logger.info(f"Модель {model} перегружена")
        
        # Если все модели перегружены
        if busy_count == len(self.vision_models):
            return (
                "⚠️ **Все vision-модели перегружены**\n\n"
                "Попробуйте:\n"
                "• Отправить через 2-3 минуты\n"
                "• Уменьшить изображение\n"
                "• Описать текстом"
            )
        
        # Другие ошибки
        return (
            "⚠️ **Ошибка анализа изображения**\n\n"
            "Возможные причины:\n"
            "• Неподдерживаемый формат\n"
            "• Слишком большое изображение\n"
            "• Проблемы с качеством\n\n"
            "Попробуйте:\n"
            "• Отправить другое изображение\n"
            "• Уменьшить размер\n"
            "• Описать текстом"
        )

ai = OpenRouterAI()

# ================= TELEGRAM SEND =================
def send_message(chat_id, text):
    """Отправка сообщения"""
    if not text:
        return
    
    # Обрезаем длинные сообщения
    if len(text) > 4000:
        text = text[:4000] + "..."
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    
    # Пробуем с Markdown
    try:
        session.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }, timeout=30)
    except:
        # Если не получается, отправляем без форматирования
        try:
            session.post(url, json={
                "chat_id": chat_id,
                "text": text
            }, timeout=30)
        except Exception as e:
            logger.error(f"Ошибка отправки: {e}")

def send_typing(chat_id):
    """Статус печатает"""
    try:
        session.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendChatAction",
            json={"chat_id": chat_id, "action": "typing"},
            timeout=5
        )
    except:
        pass

# ================= HANDLERS =================
def handle_text(chat_id, text):
    """Обработка текста"""
    send_typing(chat_id)
    reply = ai.generate_text(chat_id, text)
    send_message(chat_id, reply)

def handle_photo(chat_id, file_id, caption):
    """Обработка фото"""
    send_typing(chat_id)
    send_message(chat_id, "🔄 Обрабатываю изображение...")
    
    # Обрабатываем файл
    image_base64, error, info = process_telegram_file(file_id, caption)
    
    if error:
        error_msg = f"❌ Ошибка: {error}"
        if info:
            error_msg += f"\n{info}"
        send_message(chat_id, error_msg)
        return
    
    # Анализируем
    send_typing(chat_id)
    reply = ai.generate_with_image(chat_id, caption, image_base64)
    send_message(chat_id, reply)

# ================= FLASK ROUTES =================
@app.route('/webhook', methods=['POST'])
def webhook():
    """Основной вебхук от Telegram"""
    try:
        update = request.get_json()
        
        if "message" not in update:
            return "OK", 200
        
        msg = update["message"]
        chat_id = msg["chat"]["id"]
        text = msg.get("text", "").strip()
        
        # Команды
        if text == "/start":
            send_message(chat_id,
                "👋 **Привет!**\n\n"
                "Я AI-ассистент с поддержкой изображений.\n\n"
                "**Что я умею:**\n"
                "• Отвечать на вопросы\n"
                "• Анализировать фото\n"
                "• Помнить контекст\n\n"
                "**Команды:**\n"
                "/help - помощь\n"
                "/clear - очистить историю\n"
                "/stats - статистика"
            )
            return "OK", 200
        
        if text == "/help":
            send_message(chat_id,
                "📖 **Помощь**\n\n"
                "**Как пользоваться:**\n"
                "• Просто напишите вопрос\n"
                "• Отправьте фото с вопросом\n"
                "• Отправьте фото без подписи - опишу\n\n"
                "**Поддерживаемые форматы:**\n"
                "• JPEG, PNG, WEBP, GIF\n"
                "• Макс. размер: 20 МБ\n\n"
                "**Советы:**\n"
                "• Для формул используйте текст\n"
                "• Четкие фото лучше работают"
            )
            return "OK", 200
        
        if text == "/clear":
            if chat_id in chat_histories:
                del chat_histories[chat_id]
            send_message(chat_id, "🧹 История очищена!")
            return "OK", 200
        
        if text == "/stats":
            history = get_recent_history(chat_id)
            send_message(chat_id,
                f"📊 **Статистика**\n\n"
                f"Сообщений в истории: {len(history)}\n"
                f"Всего запросов: {ai.request_count}\n"
                f"Ошибок: {ai.error_count}"
            )
            return "OK", 200
        
        # Обработка фото
        if "photo" in msg:
            # Берем самое большое фото
            file_id = msg["photo"][-1]["file_id"]
            caption = msg.get("caption", "")
            threading.Thread(target=handle_photo, args=(chat_id, file_id, caption)).start()
            return "OK", 200
        
        # Обработка документа (файла)
        if "document" in msg:
            doc = msg["document"]
            mime = doc.get("mime_type", "")
            
            # Проверяем что это изображение
            if mime.startswith("image/"):
                file_id = doc["file_id"]
                caption = msg.get("caption", "")
                threading.Thread(target=handle_photo, args=(chat_id, file_id, caption)).start()
                return "OK", 200
            else:
                send_message(chat_id, "❌ Пожалуйста, отправьте изображение (JPEG/PNG)")
                return "OK", 200
        
        # Обработка текста
        if text:
            threading.Thread(target=handle_text, args=(chat_id, text)).start()
        
        return "OK", 200
        
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return "OK", 200

@app.route('/setwebhook', methods=['GET'])
def set_webhook():
    """Установка вебхука"""
    railway_url = os.environ.get("RAILWAY_PUBLIC_DOMAIN", request.host)
    webhook_url = f"https://{railway_url}/webhook"
    
    try:
        r = session.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook",
            params={"url": webhook_url}
        )
        result = r.json()
        if result.get("ok"):
            return f"✅ Webhook установлен: {webhook_url}"
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
        <p>Version: 3.0 (Fixed PNG support)</p>
        <ul>
            <li><a href='/setwebhook'>Set webhook</a></li>
        </ul>
    </body>
    </html>
    """

# Для Railway
application = app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
