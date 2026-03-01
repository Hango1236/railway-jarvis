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
PC_API_URL = os.environ.get("PC_API_URL", "")  # ngrok URL (https://...ngrok-free.dev)
PC_API_KEY = os.environ.get("PC_API_KEY", "")  # тот же ключ что в pc_bridge.py

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
        logger.info(f"🔧 PCBridge инициализирован с URL: {api_url}")
    
    def check_status(self):
        """Проверяет включен ли ПК (кеширует на 30 секунд)"""
        now = time.time()
        if now - self.last_check < 30:
            return self.pc_online
        
        try:
            headers = {
                "ngrok-skip-browser-warning": "true",
                "User-Agent": "Mozilla/5.0 (compatible; TelegramBot/1.0)"
            }
            
            response = requests.get(f"{self.api_url}/ping", timeout=5, headers=headers)
            self.pc_online = response.status_code == 200
            
            if self.pc_online:
                logger.info("✅ ПК в сети")
            else:
                logger.info("❌ ПК не в сети")
                
        except Exception as e:
            self.pc_online = False
            logger.error(f"❌ Ошибка при проверке ПК: {e}")
        
        self.last_check = now
        return self.pc_online
    
    def get_screenshot(self):
        """Получает скриншот с ПК"""
        if not self.check_status():
            return None, "❌ Компьютер выключен или не в сети"
        
        try:
            headers = {
                "X-API-Key": self.api_key,
                "ngrok-skip-browser-warning": "true",
                "User-Agent": "Mozilla/5.0 (compatible; TelegramBot/1.0)"
            }
            
            response = requests.post(
                f"{self.api_url}/screenshot",
                headers=headers,
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    image_data = base64.b64decode(data["image"])
                    return BytesIO(image_data), data.get("filename", "screenshot.png")
            return None, "❌ Не удалось получить скриншот"
        except Exception as e:
            return None, f"❌ Ошибка: {str(e)[:100]}"
    
    def get_pc_status(self):
        """Получает полный статус ПК"""
        if not self.check_status():
            return {
                "online": False,
                "message": "💤 Компьютер выключен или спит"
            }
        
        try:
            headers = {
                "X-API-Key": self.api_key,
                "ngrok-skip-browser-warning": "true",
                "User-Agent": "Mozilla/5.0 (compatible; TelegramBot/1.0)"
            }
            
            response = requests.get(
                f"{self.api_url}/status",
                headers=headers,
                timeout=5
            )
            
            if response.status_code == 200:
                return response.json()
            return {"online": True, "message": "ПК в сети, но статус недоступен"}
        except Exception as e:
            return {"online": True, "message": f"ПК в сети, но ошибка: {str(e)[:50]}"}

# ================= ИНИЦИАЛИЗАЦИЯ PCBridge =================
logger.info("="*50)
logger.info("🔧 ИНИЦИАЛИЗАЦИЯ PCBridge")
logger.info(f"📦 PC_API_URL = '{PC_API_URL}'")
logger.info(f"📦 PC_API_KEY = {'✅ установлен' if PC_API_KEY else '❌ не установлен'}")

if PC_API_URL and PC_API_KEY:
    try:
        pc_bridge = PCBridge(PC_API_URL, PC_API_KEY)
        logger.info("✅ PCBridge успешно создан")
    except Exception as e:
        logger.error(f"❌ Ошибка при создании PCBridge: {e}")
        pc_bridge = None
else:
    logger.warning("⚠️ PCBridge не создан: не хватает URL или ключа")
    pc_bridge = None

# ================= AI КЛАСС (OpenRouter) =================
class OpenRouterAI:
    def __init__(self):
        self.api_key = OPENROUTER_API_KEY
        self.available = bool(self.api_key)
        if self.available:
            logger.info("✅ AI инициализирован с OpenRouter")
            logger.info("🤖 Модель: Trinity 400B (400 МИЛЛИАРДОВ ПАРАМЕТРОВ!)")
        else:
            logger.warning("⚠ OpenRouter API ключ не найден")
    
    def generate(self, user_text):
        """Генерация ответа - сначала проверяем команды ПК, потом идём в AI"""
        
        # ===== 1. ПРЯМАЯ ОБРАБОТКА КОМАНД ПК =====
        text_lower = user_text.lower()
        
        # Команда статус ПК
        if any(word in text_lower for word in ["статус пк", "комп в сети", "пк онлайн", "что с пк"]):
            logger.info("🖥️ Прямая команда: статус ПК")
            if pc_bridge:
                try:
                    if pc_bridge.check_status():
                        status = pc_bridge.get_pc_status()
                        if status.get("online"):
                            return f"✅ Компьютер в сети!\n⏰ Время: {status.get('time', 'unknown')}\n📅 Дата: {status.get('date', 'unknown')}"
                        else:
                            return "💤 Компьютер выключен или в спящем режиме"
                    else:
                        return "💤 Компьютер выключен или в спящем режиме"
                except Exception as e:
                    logger.error(f"❌ Ошибка при запросе к ПК: {e}")
                    return f"❌ Ошибка связи с ПК: {str(e)[:100]}"
            else:
                return "❌ ПК не настроен в боте"
        
        # Команда скриншот
        if any(word in text_lower for word in ["скриншот", "снимок экрана", "что на экране"]):
            logger.info("📸 Прямая команда: скриншот")
            if pc_bridge:
                if pc_bridge.check_status():
                    return "🔍 Делаю скриншот..."
                else:
                    return "❌ Компьютер выключен. Включи его чтобы сделать скриншот."
            else:
                return "❌ ПК не настроен в боте"
        
        # ===== 2. ВСЁ ОСТАЛЬНОЕ ИДЁТ В AI =====
        if not self.available:
            return "❌ Ошибка: Не добавлен API ключ OpenRouter."
        
        try:
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://railway.app",
                    "X-Title": "Jarvis Telegram Bot"
                },
                json={
                    "model": "arcee-ai/trinity-large-preview:free",
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_text}
                    ],
                    "temperature": 0.2,
                    "max_tokens": 2000,
                    "top_p": 0.9
                },
                timeout=60
            )
            
            result = response.json()
            
            if "error" in result:
                error_msg = result["error"].get("message", "Неизвестная ошибка")
                return f"⚠ Ошибка AI: {error_msg}"
            
            return result["choices"][0]["message"]["content"]
            
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
            return f"❌ Произошла ошибка: {str(e)[:100]}"

# Создаем экземпляр AI
ai = OpenRouterAI()

# ================= TELEGRAM ФУНКЦИИ =================
def send_message(chat_id, text, reply_to_message_id=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    
    data = {
        "chat_id": chat_id,
        "text": text,
        "reply_to_message_id": reply_to_message_id
    }
    
    try:
        requests.post(url, json=data, timeout=5)
    except Exception as e:
        logger.error(f"Ошибка отправки: {e}")

def send_photo(chat_id, photo_io, caption, reply_to_message_id=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    
    files = {'photo': ('screenshot.png', photo_io, 'image/png')}
    data = {
        "chat_id": chat_id,
        "caption": caption,
        "reply_to_message_id": reply_to_message_id
    }
    
    try:
        requests.post(url, data=data, files=files, timeout=30)
    except Exception as e:
        logger.error(f"Ошибка отправки фото: {e}")
        send_message(chat_id, "❌ Не удалось отправить скриншот", reply_to_message_id)

def send_action(chat_id, action):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendChatAction"
    try:
        requests.post(url, json={"chat_id": chat_id, "action": action}, timeout=2)
    except:
        pass

# ================= FLASK РОУТЫ =================
@app.route('/')
def home():
    pc_status = "✅ В сети" if pc_bridge and pc_bridge.check_status() else "❌ Не в сети"
    return f"""
    <html>
        <head><title>Джарвис Бот</title></head>
        <body>
            <h1>🤖 Джарвис Telegram Бот</h1>
            <p>Статус: <b>✅ Работает</b></p>
            <p>ПК: <b>{pc_status}</b></p>
            <p><a href="/setwebhook">🔗 Установить вебхук</a></p>
        </body>
    </html>
    """

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        update = request.get_json()
        
        if "message" in update:
            chat_id = update["message"]["chat"]["id"]
            message_id = update["message"]["message_id"]
            text = update["message"].get("text", "")
            
            send_action(chat_id, "typing")
            reply = ai.generate(text)
            
            if "🔍 Делаю скриншот" in reply and pc_bridge:
                send_action(chat_id, "upload_photo")
                img_io, filename = pc_bridge.get_screenshot()
                if img_io:
                    send_photo(chat_id, img_io, f"📸 {filename}", message_id)
                else:
                    send_message(chat_id, filename, message_id)
            else:
                send_message(chat_id, reply, message_id)
        
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

# ================= ЗАПУСК =================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
