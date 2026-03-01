# pc_bridge.py - Запускать на ПК где работает Джарвис
import os
import sys
import json
import base64
from datetime import datetime
from flask import Flask, request, jsonify
import threading
import time

# Добавляем путь к твоему проекту с Джарвисом
# ПРЕДПОЛОЖИМ, что твой Джарвис лежит в D:\Ollama\models\jarvis\
JARVIS_PATH = r"D:\Ollama\models\jarvis"

# Добавляем путь в системные переменные
if JARVIS_PATH not in sys.path:
    sys.path.append(JARVIS_PATH)

app = Flask(__name__)

# ================= КОНФИГ =================
API_KEY = "твой_секретный_ключ_123"  # Тот же ключ что в Railway!

# ================= ПОДКЛЮЧЕНИЕ К ТВОЕМУ ДЖАРВИСУ =================
try:
    # Пробуем импортировать твои модули
    from actions import execute
    from ai import OllamaAI
    from config import OLLAMA_MODEL
    
    # Создаем экземпляр AI (твоего)
    ai = OllamaAI()
    
    print("✅ Модули Джарвиса загружены!")
    print(f"🤖 Модель: {OLLAMA_MODEL}")
    
except Exception as e:
    print(f"⚠ Ошибка загрузки модулей Джарвиса: {e}")
    print("⚠ Бот будет работать в режиме только скриншотов")
    ai = None
    execute = None

# ================= ФУНКЦИИ =================
def get_status():
    """Получает статус системы через ТВОИ функции"""
    try:
        if execute:
            cpu = execute("get_status", {})  # Твоя функция из actions.py
        else:
            cpu = "N/A"
    except Exception as e:
        print(f"⚠ Ошибка получения статуса: {e}")
        cpu = "N/A"
    
    # ВСЕГДА добавляем время и дату принудительно
    now = datetime.now()
    return {
        "online": True,
        "time": now.strftime("%H:%M:%S"),
        "date": now.strftime("%d.%m.%Y"),
        "cpu": cpu if cpu else "N/A",
        "timestamp": now.isoformat()
    }

def take_screenshot():
    """Делает скриншот ТВОИМ методом"""
    if execute:
        try:
            execute("screenshot", {})  # Твоя функция из actions.py
        except Exception as e:
            print(f"⚠ Ошибка при выполнении screenshot: {e}")
            # Если не сработало - делаем сами
            import pyautogui
            pyautogui.screenshot()
    else:
        # Если execute нет - делаем сами
        import pyautogui
        pyautogui.screenshot()
    
    # Ищем последний скриншот
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    if not os.path.exists(desktop):
        desktop = os.path.join(os.path.expanduser("~"), "Рабочий стол")
    
    # Ищем файлы начинающиеся с "скриншот_" или "screenshot_"
    screenshots = []
    for f in os.listdir(desktop):
        if f.startswith("скриншот_") and f.endswith(".png"):
            screenshots.append(f)
        elif f.startswith("screenshot_") and f.endswith(".png"):
            screenshots.append(f)
    
    if screenshots:
        # Берем самый новый по дате создания
        latest = max(screenshots, key=lambda x: os.path.getctime(os.path.join(desktop, x)))
        filepath = os.path.join(desktop, latest)
        
        # Читаем файл и кодируем в base64
        with open(filepath, 'rb') as f:
            image_data = base64.b64encode(f.read()).decode('utf-8')
        
        return image_data, latest
    return None, None

def understand_command(text):
    """Понимает команду ТВОИМ AI"""
    if ai:
        return ai.understand(text)
    return {"speech": "AI не доступен", "action": None, "params": {}}

# ================= API ЭНДПОИНТЫ =================
@app.route('/ping', methods=['GET'])
def ping():
    """Простая проверка что ПК в сети"""
    return jsonify({
        "online": True,
        "time": datetime.now().strftime("%H:%M:%S")
    })

@app.route('/status', methods=['GET'])
def status():
    """Полный статус ПК"""
    # Проверка ключа
    if request.headers.get('X-API-Key') != API_KEY:
        return jsonify({"error": "Unauthorized"}), 401
    
    try:
        status_data = get_status()
        # На всякий случай дублируем время и дату
        now = datetime.now()
        status_data["time"] = now.strftime("%H:%M:%S")
        status_data["date"] = now.strftime("%d.%m.%Y")
        return jsonify(status_data)
    except Exception as e:
        # Даже при ошибке возвращаем время
        now = datetime.now()
        return jsonify({
            "online": True,
            "time": now.strftime("%H:%M:%S"),
            "date": now.strftime("%d.%m.%Y"),
            "error": str(e)
        }), 200  # Возвращаем 200 чтоб бот не падал

@app.route('/screenshot', methods=['POST'])
def screenshot():
    """Делает скриншот"""
    # Проверка ключа
    if request.headers.get('X-API-Key') != API_KEY:
        return jsonify({"error": "Unauthorized"}), 401
    
    try:
        image_data, filename = take_screenshot()
        if image_data:
            return jsonify({
                "success": True,
                "image": image_data,
                "filename": filename
            })
        else:
            return jsonify({"error": "No screenshot found"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/command', methods=['POST'])
def command():
    """Выполняет команду через ТВОЕГО AI"""
    # Проверка ключа
    if request.headers.get('X-API-Key') != API_KEY:
        return jsonify({"error": "Unauthorized"}), 401
    
    data = request.json
    text = data.get('text', '')
    
    try:
        # Используем ТВОЙ AI
        result = understand_command(text)
        speech = result.get("speech", "")
        action = result.get("action")
        params = result.get("params", {})
        
        # Выполняем действие если нужно
        action_result = None
        if action and action != "null" and execute:
            action_result = execute(action, params)
        
        return jsonify({
            "success": True,
            "speech": speech,
            "action": action,
            "action_result": action_result
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/execute', methods=['POST'])
def execute_action():
    """Прямое выполнение действия (без AI)"""
    # Проверка ключа
    if request.headers.get('X-API-Key') != API_KEY:
        return jsonify({"error": "Unauthorized"}), 401
    
    data = request.json
    action = data.get('action')
    params = data.get('params', {})
    
    if not execute:
        return jsonify({"error": "Execute function not available"}), 500
    
    try:
        result = execute(action, params)
        return jsonify({
            "success": True,
            "result": result
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ================= ЗАПУСК =================
if __name__ == '__main__':
    print("="*50)
    print("🤖 PC BRIDGE ДЛЯ ДЖАРВИСА")
    print("="*50)
    print(f"📁 Путь к Джарвису: {JARVIS_PATH}")
    print(f"✅ AI загружен: {ai is not None}")
    print(f"✅ Execute доступен: {execute is not None}")
    print("🌐 Сервер запущен на порту 5001")
    print(f"🔑 API Key: {API_KEY}")
    print("="*50)
    print("📸 Доступные команды:")
    print("   /ping - проверка связи")
    print("   /status - статус системы")
    print("   /screenshot - скриншот")
    print("   /command - команда через AI")
    print("="*50)
    app.run(host='0.0.0.0', port=5001, debug=False, threaded=True)
