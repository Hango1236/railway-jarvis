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
PC_API_URL = os.environ.get("PC_API_URL", "")  # ngrok URL
PC_API_KEY = os.environ.get("PC_API_KEY", "")  # тот же ключ что в pc_bridge.py

# ================= СИСТЕМНЫЙ ПРОМПТ =================
SYSTEM_PROMPT = """Ты Джарвис — ИИ-ассистент в Telegram. Отвечай на русском языке.

ПРАВИЛА:
1. Если просят код или скрипт — генерируй ПОЛНЫЙ рабочий код
2. Для Roblox используй Luau (Lua)
3. Не пиши "сейчас", "начинаю", "хорошо" — сразу давай результат
4. Код должен быть с комментариями на русском

Если спрашивают не про код — отвечай кратко и по делу."""

# ================= КЛАСС ДЛЯ РАБОТЫ С ПК =================
class PCBridge:
    def __init__(self, api_url, api_key):
        self.api_url = api_url
        self.api_key = api_key
        self.pc_online = False
        self.last_check = 0
    
    def check_status(self):
        """Проверяет включен ли ПК (кеширует на 30 секунд)"""
        now = time.time()
        if now - self.last_check < 30:
            return self.pc_online
        
        try:
            response = requests.get(f"{self.api_url}/ping", timeout=3)
            self.pc_online = response.status_code == 200
            if self.pc_online:
                data = response.json()
                logger.info(f"✅ ПК в сети ({data.get('time', 'unknown')})")
            else:
                logger.info("❌ ПК не в сети")
        except:
            self.pc_online = False
            logger.info("❌ ПК не отвечает")
        
        self.last_check = now
        return self.pc_online
    
    def get_screenshot(self):
        """Получает скриншот с ПК"""
        if not self.check_status():
            return None, "❌ Компьютер выключен или не в сети"
        
        try:
            response = requests.post(
                f"{self.api_url}/screenshot",
                headers={"X-API-Key": self.api_key},
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
    
    def execute_command(self, text):
        """Отправляет команду на ПК для выполнения через твоего AI"""
        if not self.check_status():
            return "❌ Компьютер выключен. Чтобы выполнить эту команду, включи ПК."
        
        try:
            response = requests.post(
                f"{self.api_url}/command",
                headers={"X-API-Key": self.api_key},
                json={"text": text},
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    # Возвращаем speech от твоего AI
                    return data.get("speech", "Команда выполнена")
            return "❌ Ошибка выполнения на ПК"
        except Exception as e:
            return f"❌ Ошибка связи с ПК: {str(e)[:100]}"
    
    def get_pc_status(self):
        """Получает полный статус ПК"""
        if not self.check_status():
            return {
                "online": False,
                "message": "💤 Компьютер выключен или спит"
            }
        
        try:
            response = requests.get(
                f"{self.api_url}/status",
                headers={"X-API-Key": self.api_key},
                timeout=5
            )
            
            if response.status_code == 200:
                return response.json()
            return {"online": True, "message": "ПК в сети, но статус недоступен"}
        except:
            return {"online": True, "message": "ПК в сети, но не отвечает"}

# Создаем экземпляр PC Bridge
pc_bridge = PCBridge(PC_API_URL, PC_API_KEY) if PC_API_URL else None

# ================= AI КЛАСС (OpenRouter) =================
class OpenRouterAI:
    def __init__(self):
        self.api_key = OPENROUTER_API_KEY
        self.available = bool(self.api_key)
        if self.available:
            logger.info("✅ AI инициализирован с OpenRouter")
        else:
            logger.warning("⚠ OpenRouter API ключ не найден")
    
    def generate(self, user_text):
        """Генерация ответа через OpenRouter"""
        if not self.available:
            return "❌ Ошибка: Не добавлен API ключ OpenRouter."
        
        try:
            # Проверяем, может это команда для ПК?
            text_lower = user_text.lower()
            
            # Команды для ПК
            if any(word in text_lower for word in ["скриншот", "снимок экрана", "что на экране"]):
                if pc_bridge:
                    if pc_bridge.check_status():
                        return "🔍 Делаю скриншот..."  # Будет обработано отдельно
                    else:
                        return "❌ Компьютер выключен. Включи его чтобы сделать скриншот."
            
            if any(word in text_lower for word in ["статус пк", "комп в сети", "пк онлайн"]):
                if pc_bridge:
                    status = pc_bridge.get_pc_status()
                    if status.get("online"):
                        return f"✅ Компьютер в сети!\n⏰ Время: {status.get('time', 'unknown')}\n📅 Дата: {status.get('date', 'unknown')}"
                    else:
                        return "💤 Компьютер выключен или в спящем режиме"
            
            # Если не команда для ПК - используем OpenRouter
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://railway.app",
                    "X-Title": "Jarvis Telegram Bot"
                },
                json={
                    "meta-llama/llama-4-maverick:free",
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_text}
                    ],
                    "temperature": 0.2,
                    "max_tokens": 1500
                },
                timeout=30
            )
            
            result = response.json()
            
            if "error" in result:
                return f"⚠ Ошибка AI: {result['error'].get('message', 'Unknown')}"
            
            return result["choices"][0]["message"]["content"]
            
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
            return f"⚠ Произошла ошибка: {str(e)[:100]}"

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
    
    if "```" in text:
        data["parse_mode"] = "Markdown"
    
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
            <p>AI: {'✅ Доступен' if ai.available else '❌ Нет API ключа'}</p>
            <p>ПК: <b>{pc_status}</b></p>
            <p>Время: {time.strftime('%Y-%m-%d %H:%M:%S')}</p>
            <p>
                <a href="/setwebhook">🔗 Установить вебхук</a><br>
                <a href="/status">📊 Статус</a>
            </p>
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
            
            logger.info(f"📨 Сообщение: {text[:50]}...")
            
            # Показываем что печатает
            send_action(chat_id, "typing")
            
            # Получаем ответ от AI
            reply = ai.generate(text)
            
            # Если AI сказал что делает скриншот
            if "🔍 Делаю скриншот" in reply and pc_bridge:
                # Показываем что загружает фото
                send_action(chat_id, "upload_photo")
                
                # Получаем скриншот
                img_io, filename = pc_bridge.get_screenshot()
                
                if img_io:
                    send_photo(chat_id, img_io, f"📸 {filename}", message_id)
                else:
                    send_message(chat_id, filename, message_id)
            else:
                # Обычный ответ
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
        if railway_url.startswith('localhost'):
            return "❌ Локальный сервер"
    
    webhook_url = f"https://{railway_url}/webhook"
    
    try:
        response = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook",
            params={"url": webhook_url}
        )
        return f"""
        <h2>Установка вебхука</h2>
        <p>URL: {webhook_url}</p>
        <p>Ответ Telegram: <pre>{json.dumps(response.json(), indent=2, ensure_ascii=False)}</pre></p>
        <p><a href="/">На главную</a></p>
        """
    except Exception as e:
        return f"❌ Ошибка: {e}"

@app.route('/status')
def status():
    pc_status = pc_bridge.get_pc_status() if pc_bridge else {"online": False}
    return {
        "bot_running": True,
        "ai_available": ai.available,
        "pc_online": pc_status.get("online", False),
        "pc_status": pc_status,
        "telegram_token_set": bool(TELEGRAM_TOKEN),
        "openrouter_key_set": bool(OPENROUTER_API_KEY),
        "pc_configured": bool(PC_API_URL),
        "time": time.strftime("%Y-%m-%d %H:%M:%S")
    }

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info("="*50)
    logger.info("🚀 ЗАПУСК ДЖАРВИС БОТА")
    logger.info("="*50)
    logger.info(f"🤖 AI: {'доступен' if ai.available else 'недоступен'}")
    logger.info(f"🖥️  ПК: {'настроен' if pc_bridge else 'не настроен'}")
    if pc_bridge:
        logger.info(f"   Статус: {'✅ В сети' if pc_bridge.check_status() else '❌ Не в сети'}")
    logger.info("="*50)
    app.run(host="0.0.0.0", port=port)
