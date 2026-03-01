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
        """Проверяет включен ли ПК (кеширует на 30 секунд) с заголовками для ngrok"""
        now = time.time()
        if now - self.last_check < 30:
            return self.pc_online
        
        try:
            # Добавляем заголовок для обхода предупреждения ngrok
            headers = {
                "ngrok-skip-browser-warning": "true",
                "User-Agent": "Mozilla/5.0 (compatible; TelegramBot/1.0)"
            }
            
            logger.info(f"🔄 Проверяю статус ПК по URL: {self.api_url}/ping")
            response = requests.get(f"{self.api_url}/ping", timeout=5, headers=headers)
            
            logger.info(f"📡 Ответ от ПК: статус {response.status_code}")
            self.pc_online = response.status_code == 200
            
            if self.pc_online:
                data = response.json()
                logger.info(f"✅ ПК в сети ({data.get('time', 'unknown')})")
            else:
                logger.info("❌ ПК не в сети")
                
        except requests.exceptions.ConnectionError:
            self.pc_online = False
            logger.error("❌ Ошибка соединения: ПК не доступен (ngrok не работает?)")
        except requests.exceptions.Timeout:
            self.pc_online = False
            logger.error("❌ Таймаут: ПК не отвечает")
        except Exception as e:
            self.pc_online = False
            logger.error(f"❌ Неизвестная ошибка при проверке ПК: {e}")
        
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
            
            logger.info("📸 Запрашиваю скриншот с ПК")
            response = requests.post(
                f"{self.api_url}/screenshot",
                headers=headers,
                timeout=30
            )
            
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    image_data = base64.b64decode(data["image"])
                    logger.info(f"✅ Скриншот получен: {data.get('filename', 'unknown')}")
                    return BytesIO(image_data), data.get("filename", "screenshot.png")
            logger.error(f"❌ Ошибка получения скриншота: {response.status_code}")
            return None, "❌ Не удалось получить скриншот"
        except Exception as e:
            logger.error(f"❌ Ошибка при получении скриншота: {e}")
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
            
            logger.info("📊 Запрашиваю статус ПК")
            response = requests.get(
                f"{self.api_url}/status",
                headers=headers,
                timeout=5
            )
            
            if response.status_code == 200:
                data = response.json()
                logger.info(f"✅ Статус получен: время {data.get('time', 'unknown')}")
                return data
            logger.error(f"❌ Ошибка получения статуса: {response.status_code}")
            return {"online": True, "message": "ПК в сети, но статус недоступен"}
        except Exception as e:
            logger.error(f"❌ Ошибка при получении статуса: {e}")
            return {"online": True, "message": f"ПК в сети, но ошибка: {str(e)[:50]}"}

# Создаем экземпляр PC Bridge с диагностикой
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
        
        # ===== СУПЕР-ДИАГНОСТИКА =====
        logger.info("="*50)
        logger.info("🔍 ДИАГНОСТИКА PCBridge:")
        logger.info(f"📦 pc_bridge объект существует: {pc_bridge is not None}")
        if pc_bridge:
            logger.info(f"🔗 PC_API_URL: {pc_bridge.api_url}")
            logger.info(f"🔑 PC_API_KEY: {'✅ есть' if pc_bridge.api_key else '❌ нет'}")
            try:
                online = pc_bridge.check_status()
                logger.info(f"🖥️ ПК онлайн: {online}")
            except Exception as e:
                logger.error(f"❌ Ошибка при check_status: {e}")
        else:
            logger.error("❌ pc_bridge = None! Проверь PC_API_URL и PC_API_KEY в переменных")
        logger.info("="*50)
        
        # ===== 1. ПРЯМАЯ ОБРАБОТКА КОМАНД ПК (БЕЗ AI) =====
        text_lower = user_text.lower()
        
        # Команда статус ПК
        if any(word in text_lower for word in ["статус пк", "комп в сети", "пк онлайн", "что с пк"]):
            logger.info("🖥️ Прямая команда: статус ПК")
            if pc_bridge:
                try:
                    if pc_bridge.check_status():
                        # Получаем реальный статус с ПК
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
                logger.error("❌ pc_bridge = None в момент обработки команды!")
                return "❌ ПК не настроен в боте (pc_bridge is None)"
        
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
            # Отправляем запрос в OpenRouter
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://railway.app",
                    "X-Title": "Jarvis Telegram Bot"
                },
                json={
                    "model": "arcee-ai/trinity-large-preview:free",  # ⭐ 400B Trinity!
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
                logger.error(f"❌ Ошибка API: {error_msg}")
                
                # Если Trinity занята - пробуем запасные модели
                if "capacity" in error_msg.lower() or "overloaded" in error_msg.lower():
                    logger.info("🔄 Trinity занята, пробую запасную...")
                    return self.try_fallback_model(user_text)
                    
                return f"⚠ Ошибка AI: {error_msg}"
            
            reply = result["choices"][0]["message"]["content"]
            logger.info(f"✅ Получен ответ от Trinity 400B, длина: {len(reply)} символов")
            return reply
            
        except Exception as e:
            logger.error(f"❌ Ошибка: {e}")
            return self.try_fallback_model(user_text)
    
    def try_fallback_model(self, user_text):
        """Запасные модели на случай если Trinity занята"""
        fallback_models = [
            "deepseek/deepseek-chat:free",           # DeepSeek (очень умная)
            "google/gemma-3-12b-it:free",            # Google Gemma 3
            "mistralai/mistral-small-24b-instruct-2501:free",  # Mistral
            "meta-llama/llama-3.3-70b-instruct:free" # Meta Llama
        ]
        
        for model in fallback_models:
            try:
                logger.info(f"🔄 Пробую запасную модель: {model}")
                
                response = requests.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": model,
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
                
                if "error" not in result:
                    reply = result["choices"][0]["message"]["content"]
                    logger.info(f"✅ Получен ответ от {model}")
                    return reply
                    
            except Exception as e:
                logger.warning(f"⚠ Модель {model} не сработала: {e}")
                continue
        
        return "❌ Все модели временно недоступны. Попробуй через 5 минут."

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
        response = requests.post(url, json=data, timeout=5)
        if not response.ok:
            logger.error(f"Ошибка Telegram: {response.text}")
            if "parse_mode" in data:
                del data["parse_mode"]
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
        <head><title>Джарвис Бот - Trinity 400B</title></head>
        <body>
            <h1>🤖 Джарвис Telegram Бот</h1>
            <p>Статус: <b>✅ Работает</b></p>
            <p>AI: <b>🔥 Trinity 400B (400 миллиардов параметров!)</b></p>
            <p>AI доступен: {'✅ Да' if ai.available else '❌ Нет'}</p>
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
            
            # Получаем ответ от AI (он уже обработал команды ПК)
            reply = ai.generate(text)
            
            # ===== ОБРАБОТКА СПЕЦИАЛЬНЫХ КОМАНД =====
            
            # Скриншот
            if "🔍 Делаю скриншот" in reply and pc_bridge:
                # Показываем что загружает фото
                send_action(chat_id, "upload_photo")
                
                # Получаем скриншот
                img_io, filename = pc_bridge.get_screenshot()
                
                if img_io:
                    send_photo(chat_id, img_io, f"📸 {filename}", message_id)
                else:
                    send_message(chat_id, filename, message_id)
            
            # Статус ПК (эти фразы возвращаются из прямой обработки)
            elif any(status_word in reply for status_word in ["✅ Компьютер в сети", "💤 Компьютер выключен", "❌ ПК не настроен"]):
                # Просто отправляем готовый ответ
                send_message(chat_id, reply, message_id)
            
            # Обычный ответ
            else:
                send_message(chat_id, reply, message_id)
        
        return "OK", 200
    except Exception as e:
        logger.error(f"❌ Ошибка в webhook: {e}")
        return "Error", 500

@app.route('/setwebhook')
def set_webhook():
    railway_url = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "")
    
    if not railway_url:
        railway_url = request.host
        if railway_url.startswith('localhost'):
            return "❌ Локальный сервер. Используй продакшн URL."
    
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
        "ai_model": "Trinity 400B (400 млрд параметров)",
        "pc_online": pc_status.get("online", False),
        "pc_status": pc_status,
        "telegram_token_set": bool(TELEGRAM_TOKEN),
        "openrouter_key_set": bool(OPENROUTER_API_KEY),
        "pc_configured": bool(PC_API_URL),
        "pc_url": PC_API_URL if PC_API_URL else "не настроен",
        "time": time.strftime("%Y-%m-%d %H:%M:%S")
    }

# ================= ЗАПУСК =================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info("="*60)
    logger.info("🚀 ЗАПУСК ДЖАРВИС БОТА")
    logger.info("="*60)
    logger.info("🤖 МОДЕЛЬ: Trinity 400B (400 МИЛЛИАРДОВ ПАРАМЕТРОВ!)")
    logger.info(f"✅ AI статус: {'доступен' if ai.available else 'недоступен'}")
    logger.info(f"🖥️  ПК: {'настроен' if pc_bridge else 'не настроен'}")
    if pc_bridge:
        logger.info(f"   Статус: {'✅ В сети' if pc_bridge.check_status() else '❌ Не в сети'}")
    if PC_API_URL:
        logger.info(f"   URL: {PC_API_URL}")
    logger.info("="*60)
    app.run(host="0.0.0.0", port=port)
