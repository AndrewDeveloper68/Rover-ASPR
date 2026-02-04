import requests
import time
import logging

logger = logging.getLogger(__name__)

# Настройки ESP32
ESP32_CMD_URL = None

# Глобальное состояние АСПР
aspr_active = False
aspr_interventions = 0


def init_aspr(esp32_cmd_url):
    """Инициализация АСПР"""
    global ESP32_CMD_URL
    ESP32_CMD_URL = esp32_cmd_url


def analyze_sensor_data(data):
    """
    Анализ данных с датчиков для принятия решений
    Вызывается при получении новых данных
    """
    global aspr_active, aspr_interventions

    ultrasonic = data.get("ultrasonic", {})
    distance = ultrasonic.get("distance_cm", 999)

    if distance is not None and distance < 15:
        aspr_active = True
        aspr_interventions += 1
        try:
            requests.get(f"{ESP32_CMD_URL}?move=stop", timeout=1.0)
            logger.warning(
                f"🚨 АСПР: аварийная остановка! Расстояние: {distance:.1f} см"
            )
            return {
                "action": "emergency_stop",
                "reason": f"emergency_stop_distance_{distance:.1f}cm",
            }
        except Exception as e:
            logger.error(f"Не удалось отправить аварийную команду: {e}")
    elif distance > 50:
        aspr_active = False

    return None


def check_command_safety(command, sensor_data):
    """
    Проверка безопасности команды перед выполнением
    Вызывается перед отправкой команды на ESP32
    """
    if command == "forward":
        ultrasonic = sensor_data.get("ultrasonic", {})
        distance = ultrasonic.get("distance_cm", 999)

        if distance is not None and distance < 20:
            return {
                "blocked": True,
                "reason": f"blocked_by_aspr_distance_{distance:.1f}cm",
            }

    return {"blocked": False}


def get_aspr_metrics():
    """Получение метрик АСПР"""
    return {"active": aspr_active, "interventions": aspr_interventions}
