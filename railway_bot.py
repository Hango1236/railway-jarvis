import os
import requests
import json
import base64
from flask import Flask, request
import time
import logging
from io import BytesIO
from datetime import datetime
import urllib.parse

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

ПРАВИЛА:
1. Если просят код или скрипт — генерируй ПОЛНЫЙ рабочий код
2. Для Roblox используй Luau (Lua)
3. Не пиши "сейчас", "начинаю", "хорошо" — сразу давай результат
4. Код должен быть с комментариями на русском
5. Ты используешь Trinity 400B — одну из самых мощных бесплатных моделей

Если спрашивают не про код — отвечай кратко и по делу."""

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
logger.info("="*50)
logger.info("🔧 ИНИЦИАЛИЗАЦИЯ PCBridge")
logger.info(f"PC_API_URL = '{PC_API_URL}'")
logger.info(f"PC_API_KEY = {'✅ есть' if PC_API_KEY else '❌ нет'}")

pc_bridge = PCBridge(PC_API_URL, PC_API_KEY) if PC_API_URL and PC_API_KEY else None
if pc_bridge:
    logger.info("✅ PCBridge создан")
else:
    logger.warning("⚠️ PCBridge не создан")

# ================= AI КЛАСС =================
class OpenRouterAI:
    def __init__(self):
        self.api_key = OPENROUTER_API_KEY
        self.available = bool(self.api_key)
        if self.available:
            logger.info("✅ AI инициализирован")
    
    def generate(self, user_text):
        text_lower = user_text.lower()
        
        # Команды ПК
        if any(word in text_lower for word in ["статус пк", "комп в сети", "пк онлайн"]):
            if pc_bridge:
                if pc_bridge.check_status():
                    status = pc_bridge.get_pc_status()
                    return f"✅ Компьютер в сети!\n⏰ Время: {status.get('time', 'unknown')}\n📅 Дата: {status.get('date', 'unknown')}"
                else:
                    return "💤 Компьютер выключен"
            return "❌ ПК не настроен"
        
        if any(word in text_lower for word in ["скриншот", "снимок экрана"]):
            if pc_bridge:
                if pc_bridge.check_status():
                    return "🔍 Делаю скриншот..."
                return "❌ Компьютер выключен"
            return "❌ ПК не настроен"
        
        # OpenRouter
        if not self.available:
            return "❌ Нет API ключа OpenRouter"
        
        try:
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json={
                    "model": "arcee-ai/trinity-large-preview:free",
                    "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_text}]
                },
                timeout=30
            )
            return response.json()["choices"][0]["message"]["content"]
        except Exception as e:
            return f"❌ Ошибка: {str(e)[:100]}"

ai = OpenRouterAI()

# ================= TELEGRAM ФУНКЦИИ (100% РАБОЧИЕ) =================
def send_message(chat_id, text):
    """Отправка сообщения через простой GET запрос"""
    try:
        # Кодируем текст в URL
        encoded_text = urllib.parse.quote(text)
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage?chat_id={chat_id}&text={encoded_text}"
        
        # Простой GET запрос
        response = requests.get(url, timeout=5)
        if not response.ok:
            logger.error(f"Ошибка: {response.text}")
    except Exception as e:
        logger.error(f"Ошибка отправки: {e}")

def send_photo(chat_id, photo_io, caption):
    """Отправка фото"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    
    files = {'photo': ('screenshot.png', photo_io, 'image/png')}
    data = {"chat_id": chat_id, "caption": caption}
    
    try:
        response = requests.post(url, data=data, files=files, timeout=30)
        if not response.ok:
            logger.error(f"Ошибка фото: {response.text}")
            send_message(chat_id, "❌ Не удалось отправить скриншот")
    except Exception as e:
        logger.error(f"Ошибка: {e}")
        send_message(chat_id, f"❌ Ошибка: {str(e)[:100]}")

def send_action(chat_id, action):
    """Отправка статуса"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendChatAction"
    try:
        requests.post(url, json={"chat_id": chat_id, "action": action}, timeout=2)
    except:
        pass

# ================= FLASK РОУТЫ =================
@app.route('/')
def home():
    return "🤖 Бот работает!"

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        update = request.get_json()
        
        if "message" in update:
            chat_id = update["message"]["chat"]["id"]
            text = update["message"].get("text", "")
            
            logger.info(f"📨 Сообщение: {text[:50]}...")
            
            send_action(chat_id, "typing")
            reply = ai.generate(text)
            
            if "🔍 Делаю скриншот" in reply and pc_bridge:
                send_action(chat_id, "upload_photo")
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
    response = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook", params={"url": webhook_url})
    return json.dumps(response.json(), indent=2, ensure_ascii=False)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
