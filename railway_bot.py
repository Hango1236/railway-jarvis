import os
import requests
import base64
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Flask, request
import logging
import threading
from collections import defaultdict
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

# Хранилище истории диалогов (в памяти)
# Структура: {chat_id: {"messages": [список сообщений], "last_time": datetime}}
chat_histories = defaultdict(lambda: {"messages": [], "last_time": datetime.now()})
HISTORY_TTL = timedelta(minutes=30)  # Время жизни истории
MAX_HISTORY_MESSAGES = 10  # Максимальное количество сообщений в истории

def clean_old_histories():
    """Очистка старых диалогов (запускается периодически)"""
    now = datetime.now()
    to_delete = []
    for chat_id, data in chat_histories.items():
        if now - data["last_time"] > HISTORY_TTL:
            to_delete.append(chat_id)
    for chat_id in to_delete:
        del chat_histories[chat_id]
        logger.info(f"Очищена история для чата {chat_id}")

def add_to_history(chat_id, role, content):
    """Добавление сообщения в историю диалога"""
    clean_old_histories()  # Очищаем старые диалоги при добавлении нового
    
    history = chat_histories[chat_id]
    history["messages"].append({"role": role, "content": content})
    history["last_time"] = datetime.now()
    
    # Ограничиваем длину истории
    if len(history["messages"]) > MAX_HISTORY_MESSAGES * 2:  # *2 потому что храним и user и assistant
        history["messages"] = history["messages"][-MAX_HISTORY_MESSAGES * 2:]
    
    logger.info(f"Добавлено в историю чата {chat_id}: {role} ({len(history['messages'])} сообщений всего)")

def get_recent_history(chat_id):
    """Получение недавней истории диалога"""
    clean_old_histories()
    data = chat_histories.get(chat_id, {"messages": [], "last_time": datetime.now()})
    return data["messages"][-MAX_HISTORY_MESSAGES:]  # Возвращаем последние N сообщений

def make_session():
    s = requests.Session()
    retry = Retry(total=2, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s

session = make_session()

# ================= TELEGRAM HELPERS =================
def get_file_url(file_id):
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
        logger.error(f"getFile error: {e}")
    return None

def download_image_base64(file_url):
    try:
        r = session.get(file_url, timeout=(10, 30))
        if r.status_code == 200:
            return base64.b64encode(r.content).decode("utf-8")
    except Exception as e:
        logger.error(f"Download error: {e}")
    return None

# ================= AI =================
class OpenRouterAI:
    def __init__(self):
        self.api_key = OPENROUTER_API_KEY
        self.available = bool(self.api_key)

        # Vision-модели — пробуем все по очереди
        self.vision_models = [
            "qwen/qwen2.5-vl-72b-instruct:free",
            "qwen/qwen2.5-vl-7b-instruct:free",
            "meta-llama/llama-3.2-11b-vision-instruct:free",
            "google/gemini-2.0-flash-exp:free",
            "mistralai/mistral-small-3.1-24b-instruct:free",  # тоже поддерживает vision
        ]

        self.text_models = [
            "openrouter/free",
            "meta-llama/llama-3.3-70b-instruct:free",
            "deepseek/deepseek-r1:free",
            "deepseek/deepseek-chat-v3-0324:free",
        ]

    def _call(self, model, messages):
        """Один запрос к модели. Возвращает (текст | None, ошибка | None)"""
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
                timeout=(15, 120)
            )
            result = resp.json()
            logger.info(f"[{model}] HTTP {resp.status_code} | keys: {list(result.keys())}")

            if "choices" in result and result["choices"]:
                content = result["choices"][0]["message"].get("content", "")
                if content and content.strip():
                    return content.strip(), None
                return None, "Пустой ответ"

            if "error" in result:
                err = result["error"]
                # Проверяем специфические ошибки моделей
                msg = err.get("message", str(err))
                error_code = err.get('code', '')
                
                # Если модель перегружена или недоступна
                if any(x in msg.lower() for x in ["overloaded", "rate limit", "too many requests", "quota"]):
                    return None, f"MODEL_BUSY: {msg}"
                
                return None, f"{error_code} {msg}"

            return None, f"Неизвестный ответ: {list(result.keys())}"

        except requests.exceptions.Timeout:
            return None, "MODEL_BUSY: Таймаут (модель не отвечает)"
        except Exception as e:
            return None, str(e)

    def generate_text(self, chat_id, user_text):
        # Получаем историю диалога
        history = get_recent_history(chat_id)
        
        # Формируем сообщения с историей
        messages = [
            {"role": "system", "content": (
                "Ты полезный ассистент. Отвечай кратко и по делу на языке пользователя. "
                "ВАЖНО: никогда не используй LaTeX ($, $$, \\frac, \\sqrt, \\boxed и т.д.) — "
                "Telegram это не рендерит. Формулы пиши простым текстом: sqrt(5)/5, x^2, "
                "используй Unicode-символы: ²³√±≤≥≠π. Помни контекст предыдущих сообщений в диалоге."
            )}
        ]
        
        # Добавляем историю диалога
        messages.extend(history)
        
        # Добавляем текущее сообщение пользователя
        messages.append({"role": "user", "content": user_text})
        
        # Пробуем модели по очереди
        for model in self.text_models:
            text, err = self._call(model, messages)
            if text:
                # Сохраняем успешный ответ в историю
                add_to_history(chat_id, "user", user_text)
                add_to_history(chat_id, "assistant", text)
                return text
            logger.warning(f"❌ {model}: {err}")
        
        # Если все модели недоступны
        return "❌ Все модели временно недоступны. Пожалуйста, попробуйте позже."

    def generate_with_image(self, chat_id, caption, image_base64):
        if not self.available:
            return "❌ OPENROUTER_API_KEY не задан"

        # Получаем историю диалога
        history = get_recent_history(chat_id)
        
        prompt = caption if caption else "Что изображено на картинке? Опиши подробно."

        system_prompt = (
            "Ты полезный ассистент. Отвечай на языке пользователя. "
            "ВАЖНО: никогда не используй LaTeX-разметку ($, $$, \\frac, \\sqrt, \\boxed и т.д.) — "
            "Telegram её не поддерживает и пользователь увидит мусор. "
            "Формулы пиши простым текстом: например 'sqrt(5)/5' вместо '\\frac{\\sqrt{5}}{5}', "
            "'x^2' вместо '\\x^{2}'. Используй только обычный текст и Unicode-символы (²³√±≤≥≠). "
            "Помни контекст предыдущих сообщений в диалоге."
        )

        messages = [
            {
                "role": "system",
                "content": system_prompt
            }
        ]
        
        # Добавляем историю диалога
        messages.extend(history)
        
        # Добавляем текущее сообщение с изображением
        messages.append({
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{image_base64}",
                        "detail": "high"
                    }
                },
                {
                    "type": "text",
                    "text": prompt
                }
            ]
        })

        errors = []
        busy_models = 0
        
        for model in self.vision_models:
            logger.info(f"Vision: пробую {model}")
            text, err = self._call(model, messages)
            if text:
                logger.info(f"✅ Vision успех: {model}")
                # Сохраняем в историю
                add_to_history(chat_id, "user", f"[Изображение] {prompt}")
                add_to_history(chat_id, "assistant", text)
                return text
            
            # Считаем перегруженные модели
            if err and "MODEL_BUSY" in err:
                busy_models += 1
                logger.warning(f"⏳ Vision модель {model} перегружена")
            else:
                logger.warning(f"❌ Vision {model}: {err}")
            
            errors.append(f"{model}: {err}")

        # Если все модели перегружены
        if busy_models == len(self.vision_models):
            return (
                "⚠️ **Все vision-модели сейчас перегружены**\n\n"
                "Попробуйте:\n"
                "• Отправить изображение через 1-2 минуты\n"
                "• Описать задачу текстом\n"
                "• Использовать другое изображение (возможно, проблема в формате)"
            )
        
        # Другие ошибки
        err_details = "\n".join(errors)
        logger.error(f"Все vision-модели упали:\n{err_details}")
        return (
            "⚠️ **Не удалось проанализировать изображение**\n\n"
            "Попробуйте:\n"
            "• Отправить изображение в другом формате (JPEG/PNG)\n"
            "• Уменьшить размер изображения\n"
            "• Описать задачу текстом"
        )

ai = OpenRouterAI()

# ================= ОТПРАВКА =================
def send_msg(chat_id, text):
    t = text[:4000] + "..." if len(text) > 4000 else text
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    
    # Пробуем отправить с Markdown, если не получается - без форматирования
    for parse_mode in ["Markdown", None]:
        payload = {"chat_id": chat_id, "text": t}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        try:
            r = session.post(url, json=payload, timeout=(15, 60))
            if r.status_code == 200:
                return
            logger.warning(f"Telegram {r.status_code}: {r.text[:100]}")
        except Exception as e:
            logger.error(f"send_msg error: {e}")

def send_async(chat_id, text):
    threading.Thread(target=send_msg, args=(chat_id, text), daemon=True).start()

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
    reply = ai.generate_text(chat_id, text)  # Передаём chat_id для истории
    send_msg(chat_id, reply)

def process_photo(chat_id, file_id, caption):
    send_typing(chat_id)

    file_url = get_file_url(file_id)
    if not file_url:
        send_msg(chat_id, "❌ Не удалось получить изображение от Telegram")
        return

    image_base64 = download_image_base64(file_url)
    if not image_base64:
        send_msg(chat_id, "❌ Не удалось скачать изображение")
        return

    send_msg(chat_id, "🔍 Анализирую изображение...")
    send_typing(chat_id)

    reply = ai.generate_with_image(chat_id, caption, image_base64)  # Передаём chat_id
    send_msg(chat_id, reply)

# ================= FLASK =================
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        update = request.get_json()
        if "message" not in update:
            return "OK", 200

        msg = update["message"]
        chat_id = msg["chat"]["id"]
        text = msg.get("text", "").strip()

        if text == "/start":
            # Очищаем историю при новом старте
            if chat_id in chat_histories:
                del chat_histories[chat_id]
            send_async(chat_id,
                "👋 Привет! Я AI-ассистент.\n\n"
                "• Задай любой вопрос текстом — я помню контекст диалога\n"
                "• Отправь фото с подписью — разберу задачу на картинке 🖼\n"
                "• /clear — очистить историю диалога\n"
                "• /help — помощь"
            )
            return "OK", 200

        if text == "/help":
            send_async(chat_id,
                "📖 **Как пользоваться:**\n"
                "• Напиши вопрос — отвечу с учётом контекста\n"
                "• Отправь фото задачи с подписью «реши» или «что здесь?»\n"
                "• Фото без подписи — опишу что на нём\n"
                "• /clear — очистить историю диалога\n"
                "• /stats — статистика диалога"
            )
            return "OK", 200
            
        if text == "/clear":
            if chat_id in chat_histories:
                del chat_histories[chat_id]
            send_async(chat_id, "🧹 История диалога очищена!")
            return "OK", 200
            
        if text == "/stats":
            history = get_recent_history(chat_id)
            msg_count = len(history)
            send_async(chat_id, f"📊 Статистика диалога:\n• Сообщений в истории: {msg_count}\n• Максимум хранится: {MAX_HISTORY_MESSAGES} последних сообщений")
            return "OK", 200

        # Фото
        if "photo" in msg:
            file_id = msg["photo"][-1]["file_id"]
            caption = msg.get("caption", "").strip()
            logger.info(f"Фото от {chat_id}, подпись: '{caption}'")
            threading.Thread(target=process_photo, args=(chat_id, file_id, caption), daemon=True).start()
            return "OK", 200

        # Документ-изображение (без сжатия)
        if "document" in msg:
            doc = msg["document"]
            if doc.get("mime_type", "").startswith("image/"):
                caption = msg.get("caption", "").strip()
                threading.Thread(target=process_photo, args=(chat_id, doc["file_id"], caption), daemon=True).start()
                return "OK", 200

        # Текст
        if text:
            threading.Thread(target=process_text, args=(chat_id, text), daemon=True).start()

        return "OK", 200

    except Exception as e:
        logger.error(f"webhook error: {e}")
        return "Error", 500


@app.route('/setwebhook')
def set_webhook():
    railway_url = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "") or request.host
    webhook_url = f"https://{railway_url}/webhook"
    try:
        r = session.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook",
            params={"url": webhook_url},
            timeout=(10, 30)
        )
        result = r.json()
        return f"✅ {webhook_url}" if result.get("ok") else f"❌ {result}"
    except Exception as e:
        return f"❌ {e}"


@app.route('/debug')
def debug():
    import json
    return f"""
    <pre>
    Bot Status: OK
    Chat Histories: {len(chat_histories)} active chats
    MAX_HISTORY_MESSAGES: {MAX_HISTORY_MESSAGES}
    HISTORY_TTL: {HISTORY_TTL}
    </pre>
    <a href='/'>Home</a>
    """


@app.route('/')
def home():
    return "🤖 Бот работает с поддержкой контекста! <a href='/debug'>Debug</a> | <a href='/setwebhook'>Webhook</a>"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
