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
from PIL import Image
import io
import time

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

# Хранилище истории диалогов
chat_histories = defaultdict(lambda: {"messages": [], "last_time": datetime.now()})
HISTORY_TTL = timedelta(minutes=30)
MAX_HISTORY_MESSAGES = 10

# Кэш рабочих моделей
working_models_cache = {
    "vision": [],
    "text": [],
    "last_check": datetime.now()
}

def clean_old_histories():
    """Очистка старых диалогов"""
    now = datetime.now()
    to_delete = []
    for chat_id, data in chat_histories.items():
        if now - data["last_time"] > HISTORY_TTL:
            to_delete.append(chat_id)
    for chat_id in to_delete:
        del chat_histories[chat_id]
        logger.info(f"Очищена история для чата {chat_id}")

def add_to_history(chat_id, role, content):
    """Добавление сообщения в историю"""
    clean_old_histories()
    
    history = chat_histories[chat_id]
    history["messages"].append({"role": role, "content": content})
    history["last_time"] = datetime.now()
    
    if len(history["messages"]) > MAX_HISTORY_MESSAGES * 2:
        history["messages"] = history["messages"][-MAX_HISTORY_MESSAGES * 2:]
    
    logger.info(f"Добавлено в историю чата {chat_id}: {role} ({len(history['messages'])} сообщений)")

def get_recent_history(chat_id):
    """Получение истории диалога"""
    clean_old_histories()
    data = chat_histories.get(chat_id, {"messages": [], "last_time": datetime.now()})
    return data["messages"][-MAX_HISTORY_MESSAGES:]

def make_session():
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504])
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
            timeout=(10, 30)
        )
        result = r.json()
        if result.get("ok"):
            file_path = result["result"]["file_path"]
            return f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
    except Exception as e:
        logger.error(f"getFile error: {e}")
    return None

def download_and_process_image(file_url, max_size=1024, max_file_size=5*1024*1024):
    """Скачивает и обрабатывает изображение"""
    try:
        # Скачиваем изображение
        r = session.get(file_url, timeout=(30, 60))
        if r.status_code != 200:
            logger.error(f"Ошибка скачивания: {r.status_code}")
            return None
        
        # Проверяем размер файла
        content_length = len(r.content)
        logger.info(f"Размер изображения: {content_length} байт")
        
        if content_length > max_file_size:
            logger.warning(f"Изображение слишком большое: {content_length} > {max_file_size}")
            return None, "too_large"
        
        # Открываем изображение
        image = Image.open(io.BytesIO(r.content))
        
        # Конвертируем в RGB если нужно
        if image.mode in ('RGBA', 'LA', 'P'):
            rgb_image = Image.new('RGB', image.size, (255, 255, 255))
            rgb_image.paste(image, mask=image.split()[-1] if image.mode == 'RGBA' else None)
            image = rgb_image
        
        # Проверяем и уменьшаем размер
        original_size = image.size
        if max(image.width, image.height) > max_size:
            ratio = max_size / max(image.width, image.height)
            new_size = (int(image.width * ratio), int(image.height * ratio))
            image = image.resize(new_size, Image.Resampling.LANCZOS)
            logger.info(f"Изображение уменьшено: {original_size} -> {image.size}")
        
        # Сохраняем с оптимизацией
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85, optimize=True)
        compressed_size = len(buffer.getvalue())
        logger.info(f"Размер после сжатия: {compressed_size} байт")
        
        return base64.b64encode(buffer.getvalue()).decode("utf-8"), None
        
    except Exception as e:
        logger.error(f"Ошибка обработки изображения: {e}")
        return None, str(e)

# ================= AI =================
class OpenRouterAI:
    def __init__(self):
        self.api_key = OPENROUTER_API_KEY
        self.available = bool(self.api_key)
        self.request_count = 0
        self.error_count = 0
        self.last_reset = datetime.now()

        # Все доступные модели
        self.all_vision_models = [
            "qwen/qwen2.5-vl-72b-instruct:free",
            "qwen/qwen2.5-vl-7b-instruct:free",
            "meta-llama/llama-3.2-11b-vision-instruct:free",
            "google/gemini-2.0-flash-exp:free",
            "mistralai/mistral-small-3.1-24b-instruct:free",
            "deepseek/deepseek-vl2:free",
        ]

        self.all_text_models = [
            "openrouter/free",
            "meta-llama/llama-3.3-70b-instruct:free",
            "deepseek/deepseek-r1:free",
            "deepseek/deepseek-chat-v3-0324:free",
            "microsoft/phi-3.5-mini-128k-instruct:free",
            "google/gemini-flash-1.5-8b:free",
        ]

    def get_working_models(self, model_type="vision"):
        """Получает список рабочих моделей с кэшированием"""
        global working_models_cache
        
        # Проверяем кэш (обновляем раз в 10 минут)
        cache_age = (datetime.now() - working_models_cache["last_check"]).seconds
        if cache_age < 600 and working_models_cache[model_type]:
            return working_models_cache[model_type]
        
        # Тестируем модели
        working = []
        models = self.all_vision_models if model_type == "vision" else self.all_text_models
        
        logger.info(f"Тестирование {model_type} моделей...")
        
        for model in models[:3]:  # Тестируем только первые 3 для скорости
            try:
                # Быстрый тест модели
                test_messages = [{"role": "user", "content": "Say 'ok' in one word"}]
                if model_type == "vision":
                    test_messages = [{"role": "user", "content": [{"type": "text", "text": "Say 'ok'"}]}]
                
                resp = session.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": test_messages,
                        "max_tokens": 10
                    },
                    timeout=10
                )
                
                if resp.status_code == 200:
                    result = resp.json()
                    if "choices" in result and result["choices"]:
                        working.append(model)
                        logger.info(f"✅ Модель работает: {model}")
                    else:
                        logger.warning(f"❌ Модель не отвечает: {model}")
                else:
                    logger.warning(f"❌ Модель недоступна: {model} (код {resp.status_code})")
                    
            except Exception as e:
                logger.warning(f"❌ Ошибка теста {model}: {e}")
            
            time.sleep(0.5)  # Небольшая задержка между запросами
        
        # Обновляем кэш
        working_models_cache[model_type] = working or models
        working_models_cache["last_check"] = datetime.now()
        
        logger.info(f"Найдено рабочих {model_type} моделей: {len(working)}")
        return working_models_cache[model_type]

    def _call(self, model, messages, max_retries=2):
        """Улучшенный вызов модели с повторными попытками"""
        self.request_count += 1
        
        for attempt in range(max_retries):
            try:
                logger.info(f"Попытка {attempt + 1} для модели {model}")
                
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
                    timeout=(30, 180)
                )
                
                result = resp.json()
                logger.info(f"[{model}] HTTP {resp.status_code}")
                
                # Успешный ответ
                if "choices" in result and result["choices"]:
                    content = result["choices"][0]["message"].get("content", "")
                    if content and content.strip():
                        return content.strip(), None
                
                # Обработка ошибок
                if "error" in result:
                    err = result["error"]
                    msg = err.get("message", str(err))
                    
                    # Специфические ошибки
                    if any(x in msg.lower() for x in ["overloaded", "rate limit", "too many requests", "quota"]):
                        if attempt < max_retries - 1:
                            wait_time = (attempt + 1) * 5
                            logger.warning(f"Модель перегружена, ждем {wait_time}с...")
                            time.sleep(wait_time)
                            continue
                        return None, f"MODEL_BUSY: {msg}"
                    
                    return None, msg
                
                return None, f"Неизвестный ответ: {list(result.keys())}"
                
            except requests.exceptions.Timeout:
                if attempt < max_retries - 1:
                    logger.warning(f"Таймаут, повтор через {attempt + 1}с...")
                    time.sleep(attempt + 1)
                    continue
                return None, "MODEL_BUSY: Таймаут"
                
            except Exception as e:
                self.error_count += 1
                if attempt < max_retries - 1:
                    logger.warning(f"Ошибка, повтор: {e}")
                    time.sleep(attempt + 1)
                    continue
                return None, str(e)
        
        return None, "Все попытки исчерпаны"

    def generate_text(self, chat_id, user_text):
        """Генерация текста с контекстом"""
        # Сбрасываем счетчики раз в час
        if (datetime.now() - self.last_reset).seconds > 3600:
            self.request_count = 0
            self.error_count = 0
            self.last_reset = datetime.now()
        
        # Получаем историю
        history = get_recent_history(chat_id)
        
        # Формируем сообщения
        messages = [
            {"role": "system", "content": (
                "Ты полезный ассистент. Отвечай кратко и по делу на языке пользователя. "
                "ВАЖНО: никогда не используй LaTeX ($, $$, \\frac, \\sqrt, \\boxed и т.д.) — "
                "Telegram это не рендерит. Формулы пиши простым текстом: sqrt(5)/5, x^2, "
                "используй Unicode-символы: ²³√±≤≥≠π. Помни контекст предыдущих сообщений."
            )}
        ]
        
        messages.extend(history)
        messages.append({"role": "user", "content": user_text})
        
        # Получаем рабочие модели
        text_models = self.get_working_models("text")
        errors = []
        
        for model in text_models:
            text, err = self._call(model, messages)
            if text:
                # Сохраняем в историю
                add_to_history(chat_id, "user", user_text)
                add_to_history(chat_id, "assistant", text)
                return text
            errors.append(f"{model}: {err}")
            logger.warning(f"❌ {model}: {err}")
        
        # Статистика ошибок
        error_rate = (self.error_count / max(self.request_count, 1)) * 100
        logger.error(f"Все модели недоступны. Ошибок: {self.error_count}, Процент: {error_rate:.1f}%")
        
        return (
            "❌ **Все модели временно недоступны**\n\n"
            f"Попыток: {self.request_count}, Ошибок: {self.error_count}\n"
            "Пожалуйста, попробуйте через несколько минут."
        )

    def generate_with_image(self, chat_id, caption, image_base64):
        """Анализ изображения с контекстом"""
        if not self.available:
            return "❌ OPENROUTER_API_KEY не задан"
        
        if image_base64 is None:
            return "❌ Не удалось обработать изображение"
        
        # Получаем историю
        history = get_recent_history(chat_id)
        
        prompt = caption if caption else "Что изображено на картинке? Опиши подробно."

        system_prompt = (
            "Ты полезный ассистент. Отвечай на языке пользователя. "
            "ВАЖНО: никогда не используй LaTeX. Формулы пиши простым текстом. "
            "Помни контекст предыдущих сообщений."
        )

        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)
        
        # Добавляем текущее сообщение с изображением
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

        # Получаем рабочие vision-модели
        vision_models = self.get_working_models("vision")
        errors = []
        busy_count = 0
        
        for model in vision_models:
            logger.info(f"Vision: пробую {model}")
            text, err = self._call(model, messages)
            
            if text:
                logger.info(f"✅ Vision успех: {model}")
                add_to_history(chat_id, "user", f"[Изображение] {prompt}")
                add_to_history(chat_id, "assistant", text)
                return text
            
            if err and "MODEL_BUSY" in err:
                busy_count += 1
                logger.warning(f"⏳ Модель {model} перегружена")
            else:
                logger.warning(f"❌ {model}: {err}")
            
            errors.append(f"{model}: {err}")

        # Формируем понятное сообщение об ошибке
        if busy_count == len(vision_models):
            return (
                "⚠️ **Все vision-модели сейчас перегружены**\n\n"
                "Это временная проблема бесплатных моделей OpenRouter.\n\n"
                "**Что делать:**\n"
                "• Отправить изображение через 2-3 минуты\n"
                "• Уменьшить размер изображения\n"
                "• Описать задачу текстом\n\n"
                "Я могу помочь с текстовым описанием задачи!"
            )
        elif busy_count > 0:
            return (
                "⚠️ **Частичная перегрузка vision-моделей**\n\n"
                "Некоторые модели работают, но сейчас заняты.\n\n"
                "**Попробуйте:**\n"
                "• Повторить через минуту\n"
                "• Отправить изображение меньшего размера\n"
                "• Описать текстом"
            )
        else:
            return (
                "⚠️ **Ошибка анализа изображения**\n\n"
                "**Возможные причины:**\n"
                "• Неподдерживаемый формат (используйте JPEG/PNG)\n"
                "• Слишком большое изображение\n"
                "• Проблемы с качеством фото\n\n"
                "**Попробуйте:**\n"
                "• Отправить другое изображение\n"
                "• Уменьшить размер\n"
                "• Описать текстом"
            )

ai = OpenRouterAI()

# ================= ОТПРАВКА =================
def send_msg(chat_id, text):
    """Отправка сообщения с обработкой ошибок"""
    if not text:
        return
    
    # Обрезаем слишком длинные сообщения
    t = text[:4000] + "..." if len(text) > 4000 else text
    
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    
    # Пробуем с разными режимами форматирования
    for parse_mode in ["Markdown", "HTML", None]:
        try:
            payload = {"chat_id": chat_id, "text": t}
            if parse_mode:
                payload["parse_mode"] = parse_mode
            
            r = session.post(url, json=payload, timeout=(15, 30))
            
            if r.status_code == 200:
                return
            
            # Если ошибка из-за форматирования, пробуем без него
            if r.status_code == 400 and "can't parse" in r.text.lower():
                logger.warning(f"Ошибка парсинга {parse_mode}, пробуем без форматирования")
                continue
                
            logger.warning(f"Telegram {r.status_code}: {r.text[:100]}")
            
        except Exception as e:
            logger.error(f"send_msg error: {e}")

def send_async(chat_id, text):
    """Асинхронная отправка"""
    threading.Thread(target=send_msg, args=(chat_id, text), daemon=True).start()

def send_typing(chat_id):
    """Отправка статуса 'печатает'"""
    try:
        session.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendChatAction",
            json={"chat_id": chat_id, "action": "typing"},
            timeout=5
        )
    except Exception:
        pass

# ================= ОБРАБОТКА =================
def process_text(chat_id, text):
    """Обработка текстового сообщения"""
    send_typing(chat_id)
    reply = ai.generate_text(chat_id, text)
    send_msg(chat_id, reply)

def process_photo(chat_id, file_id, caption):
    """Обработка изображения"""
    send_typing(chat_id)
    
    # Получаем URL изображения
    file_url = get_file_url(file_id)
    if not file_url:
        send_msg(chat_id, "❌ Не удалось получить изображение от Telegram")
        return
    
    # Отправляем уведомление о начале обработки
    send_msg(chat_id, "🔄 Обрабатываю изображение...")
    send_typing(chat_id)
    
    # Скачиваем и обрабатываем изображение
    image_base64, error = download_and_process_image(file_url)
    
    if error == "too_large":
        send_msg(chat_id, 
            "❌ **Изображение слишком большое**\n\n"
            "Максимальный размер: 5 МБ\n\n"
            "Попробуйте:\n"
            "• Уменьшить изображение\n"
            "• Отправить в формате JPEG сжатый\n"
            "• Описать задачу текстом")
        return
    elif not image_base64:
        send_msg(chat_id, 
            "❌ **Ошибка обработки изображения**\n\n"
            "Попробуйте:\n"
            "• Отправить другое изображение\n"
            "• Отправить в формате JPEG\n"
            "• Описать текстом")
        return
    
    # Анализируем изображение
    reply = ai.generate_with_image(chat_id, caption, image_base64)
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

        # Команды
        if text == "/start":
   
