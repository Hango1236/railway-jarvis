import os
import requests
import json
import base64
from flask import Flask, request
import time
import logging
from io import BytesIO
from datetime import datetime

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ================= КОНФИГУРАЦИЯ =================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

# ================= КОНФИГ ПК =================
PC_API_URL = os.environ.get("PC_API_URL", "")
PC_API_KEY = os.environ.get("PC_API_KEY", "")

# ================= СИСТЕМНЫЙ ПРОМПТ =================
SYSTEM_PROMPT = """Ты Джарвис — ИИ-ассистент в Telegram. Отвечай на русском языке.
Ты умеешь анализировать изображения. Если тебе прислали картинку с задачей - реши её."""

# ================= КЛАСС ДЛЯ РАБОТЫ С ПК =================
class PCBridge:
    def __init__(self, api_url, api_key):
        self.api_url = api_url
        self.api_key = api_key
        self.pc_online = False
        self.last_check = 0
    
    def check_status(self):
        now = time.time()
        if now - self.last_check < 30:
            return self.pc_online
        
        try:
            headers = {"ngrok-skip-browser-warning": "true"}
            response = requests.get(f"{self.api_url}/ping", timeout=5, headers=headers)
            self.pc_online = response.status_code == 200
        except:
            self.pc_online = False
        
        self.last_check = now
        return self.pc_online
    
    def get_screenshot(self):
        if not self.check_status():
            return None, "❌ Компьютер выключен"
        
        try:
            headers = {"X-API-Key": self.api_key, "ngrok-skip-browser-warning": "true"}
            response = requests.post(f"{self.api_url}/screenshot", headers=headers, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    image_data = base64.b64decode(data["image"])
                    return BytesIO(image_data), data.get("filename", "screenshot.png")
            return None, "❌ Не удалось получить скриншот"
        except Exception as e:
            return None, f"❌ Ошибка: {str(e)[:100]}"
    
    def get_pc_status(self):
        if not self.check_status():
            return {"online": False}
        
        try:
            headers = {"X-API-Key": self.api_key}
            response = requests.get(f"{self.api_url}/status", headers=headers, timeout=5)
            if response.status_code == 200:
                return response.json()
            return {"online": True}
        except:
            return {"online": True}

# Инициализация
pc_bridge = PCBridge(PC_API_URL, PC_API_KEY) if PC_API_URL and PC_API_KEY else None

# ================= ФУНКЦИЯ СКАЧИВАНИЯ ФОТО =================
def download_photo(file_id):
    """Скачивает фото из Telegram"""
    try:
        file_info = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile",
            params={"file_id": file_id}
        ).json()
        
        if not file_info.get("ok"):
            return None
        
        file_path = file_info["result"]["file_path"]
        photo_data = requests.get(
            f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
        ).content
        
        return photo_data
    except Exception as e:
        logger.error(f"Ошибка скачивания фото: {e}")
        return None

# ================= AI КЛАСС =================
class OpenRouterAI:
    def __init__(self):
        self.api_key = OPENROUTER_API_KEY
        self.available = bool(self.api_key)
    
    def generate(self, user_text, photo=None):
        text_lower = user_text.lower() if user_text else ""
        
        # Команды ПК
        if "статус пк" in text_lower or "комп в сети" in text_lower:
            if pc_bridge:
                if pc_bridge.check_status():
                    status = pc_bridge.get_pc_status()
                    return f"✅ Компьютер в сети!\nВремя: {status.get('time', 'unknown')}\nДата: {status.get('date', 'unknown')}"
                return "💤 Компьютер выключен"
            return "❌ ПК не настроен"
        
        if "скриншот" in text_lower or "снимок экрана" in text_lower:
            if pc_bridge:
                if pc_bridge.check_status():
                    return "🔍 Делаю скриншот..."
                return "❌ Компьютер выключен"
            return "❌ ПК не настроен"
        
        # OpenRouter с поддержкой изображений
        if not self.available:
            return "❌ Нет API ключа"
        
        # Список моделей для проброса
        models_to_try = [
            "qwen/qwen-2.5-vl-72b-instruct:free",
            "google/gemini-2.0-flash-exp:free",
            "meta-llama/llama-3.2-90b-vision-instruct:free"
        ]
        
        for model in models_to_try:
            try:
                # Формируем сообщение
                messages = [{"role": "system", "content": SYSTEM_PROMPT}]
                
                # Если есть фото - добавляем multimodal контент
                if photo:
                    photo_base64 = base64.b64encode(photo).decode('utf-8')
                    
                    content = []
                    if user_text:
                        content.append({"type": "text", "text": user_text})
                    else:
                        content.append({"type": "text", "text": "Что изображено на этой картинке? Реши задачу если есть."})
                    
                    content.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{photo_base64}"
                        }
                    })
                    
                    messages.append({"role": "user", "content": content})
                else:
                    messages.append({"role": "user", "content": user_text})
                
                # Отправляем запрос
                response = requests.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": model,
                        "messages": messages,
                        "temperature": 0.2,
                        "max_tokens": 2000
                    },
                    timeout=60
                )
                
                result = response.json()
                
                # Проверяем наличие ошибки
                if "error" in result:
                    logger.warning(f"Модель {model} вернула ошибку: {result['error']}")
                    continue
                
                # Проверяем наличие choices
                if "choices" in result and len(result["choices"]) > 0:
                    return result["choices"][0]["message"]["content"]
                else:
                    logger.warning(f"Модель {model} не вернула choices: {result}")
                    continue
                    
            except Exception as e:
                logger.warning(f"Ошибка с моделью {model}: {e}")
                continue
        
        return "❌ Не удалось получить ответ от AI. Попробуйте еще раз."

ai = OpenRouterAI()

# ================= TELEGRAM ФУНКЦИИ =================
def send_message(chat_id, text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    params = {"chat_id": chat_id, "text": text}
    try:
        requests.get(url, params=params, timeout=5)
    except Exception as e:
        logger.error(f"Ошибка: {e}")

def send_photo(chat_id, photo_io, caption):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    files = {'photo': ('screenshot.png', photo_io, 'image/png')}
    data = {"chat_id": chat_id, "caption": caption}
    try:
        requests.post(url, data=data, files=files, timeout=30)
    except Exception as e:
        send_message(chat_id, f"❌ Ошибка: {str(e)[:100]}")

# ================= FLASK РОУТЫ =================
@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        update = request.get_json()
        
        if "message" in update:
            chat_id = update["message"]["chat"]["id"]
            text = update["message"].get("text", "")
            photo = None
            
            # Проверяем есть ли фото
            if "photo" in update["message"]:
                photo_file = update["message"]["photo"][-1]
                photo = download_photo(photo_file["file_id"])
                logger.info("📸 Получено фото")
            
            # Получаем ответ от AI
            reply = ai.generate(text, photo)
            
            # Скриншот
            if "🔍 Делаю скриншот" in reply and pc_bridge:
                img_io, filename = pc_bridge.get_screenshot()
                if img_io:
                    send_photo(chat_id, img_io, f"📸 {filename}")
                else:
                    send_message(chat_id, filename)
            else:
                send_message(chat_id, reply)
        
        return "OK", 200
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}")
        return "Error", 500

@app.route('/setwebhook')
def set_webhook():
    railway_url = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
    if not railway_url:
        railway_url = request.host
    webhook_url = f"https://{railway_url}/webhook"
    requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook", params={"url": webhook_url})
    return "Webhook установлен!"

@app.route('/')
def home():
    return "🤖 Бот работает!"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
