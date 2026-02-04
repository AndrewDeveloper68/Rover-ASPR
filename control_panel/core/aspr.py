import requests
import time
import logging
import joblib
import os
from sklearn.tree import DecisionTreeClassifier

logger = logging.getLogger(__name__)

# ===== НАСТРОЙКИ =====
ESP32_CMD_URL = None
ASPR_MODE = "safe"  # "learning", "safe", "autonomous"
MODEL_PATH = "aspr_model.pkl"
MIN_CONFIDENCE = 0.7

# ===== ГЛОБАЛЬНОЕ СОСТОЯНИЕ =====
aspr_active = False
aspr_interventions = 0
last_distance = 999
last_time = time.time()
collision_model = None
last_explanation = "Система готова"  # ← НОВОЕ: объяснение для интерфейса
last_reason = None  # ← НОВОЕ: причина последней остановки


# ===== ЗАГРУЗКА МОДЕЛИ =====
def load_collision_model():
    global collision_model
    if os.path.exists(MODEL_PATH):
        try:
            collision_model = joblib.load(MODEL_PATH)
            logger.info(f"✅ Модель АСПР загружена: {MODEL_PATH}")
            return True
        except Exception as e:
            logger.warning(f"⚠️ Не удалось загрузить модель: {e}")
    else:
        logger.info("ℹ️ Модель АСПР не найдена — работает правило-базовая защита")
    return False


# ===== ИНИЦИАЛИЗАЦИЯ =====
def init_aspr(esp32_cmd_url):
    """Инициализация АСПР"""
    global ESP32_CMD_URL
    ESP32_CMD_URL = esp32_cmd_url
    load_collision_model()


def set_aspr_mode(mode):
    """Установка режима работы АСПР"""
    global ASPR_MODE
    if mode in ["learning", "safe", "autonomous"]:
        ASPR_MODE = mode
        logger.info(f"🔄 АСПР режим: {mode}")
        return True
    return False


# ===== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =====
def _calculate_approach_speed(current_distance):
    """Расчёт скорости сближения (см/с)"""
    global last_distance, last_time
    now = time.time()
    delta_t = now - last_time

    if delta_t < 0.05 or current_distance is None or last_distance is None:
        speed = 0.0
    else:
        speed = (last_distance - current_distance) / delta_t

    last_distance, last_time = current_distance, now
    return speed


def _extract_features(data):
    """Извлечение признаков для модели"""
    ultrasonic = data.get("ultrasonic", {})
    imu = data.get("imu", {}).get("calibrated", {})

    distance = ultrasonic.get("distance_cm", 999)
    approach_speed = _calculate_approach_speed(distance)
    az = imu.get("az_g", 1.0)
    gz = imu.get("gz_dps", 0.0)

    return {
        "distance": distance,
        "approach_speed": approach_speed,
        "az_g": az,
        "gz_dps": gz
    }, [distance, approach_speed, az, gz]


# ===== ML: ПРЕДСКАЗАНИЕ СТОЛКНОВЕНИЯ =====
def predict_collision(features_vector):
    """Возвращает: (столкновение?, уверенность)"""
    if collision_model is None:
        return False, 0.0

    try:
        proba = collision_model.predict_proba([features_vector])[0]
        collision_prob = proba[1]
        confidence = max(proba)
        return collision_prob > 0.5 and confidence >= MIN_CONFIDENCE, confidence
    except Exception as e:
        logger.debug(f"Ошибка предсказания: {e}")
        return False, 0.0


# ===== ОСНОВНАЯ ЛОГИКА АСПР =====
def analyze_sensor_data(data):
    """
    Анализ данных с датчиков для принятия решений
    Вызывается при получении новых данных
    """
    global aspr_active, aspr_interventions, last_explanation, last_reason

    ultrasonic = data.get("ultrasonic", {})
    imu = data.get("imu", {}).get("calibrated", {})
    distance = ultrasonic.get("distance_cm", 999)
    az = imu.get("az_g", 1.0)
    approach_speed = _calculate_approach_speed(distance)

    # ===== 1. Проверка наклона (опрокидывание) =====
    if az < 0.75:
        last_explanation = f"⚠️ Опасный наклон! az={az:.2f} (порог: 0.75)"
        last_reason = "tilt_danger"
        if ASPR_MODE == "learning":
            logger.info(f"🎓 Режим обучения: {last_explanation}")
            return {"action": "log_only", "reason": last_reason, "explanation": last_explanation}

        aspr_active = True
        aspr_interventions += 1
        try:
            requests.get(f"{ESP32_CMD_URL}?move=stop", timeout=1.0)
            logger.warning(f"🚨 АСПР: остановка из-за наклона! az={az:.2f}")
            return {"action": "emergency_stop", "reason": last_reason, "explanation": last_explanation}
        except Exception as e:
            logger.error(f"Ошибка отправки команды: {e}")
        return None

    # ===== 2. ML-предсказание =====
    features_dict, features_vec = _extract_features(data)
    ml_triggered, confidence = predict_collision(features_vec)

    if ml_triggered:
        last_explanation = f"🤖 ML: столкновение через ~0.5с! Уверенность: {confidence:.0%}"
        last_reason = "ml_prediction"
        if ASPR_MODE == "learning":
            logger.info(f"🎓 Режим обучения: {last_explanation}")
            return {"action": "log_only", "reason": last_reason, "explanation": last_explanation}

        aspr_active = True
        aspr_interventions += 1
        try:
            requests.get(f"{ESP32_CMD_URL}?move=stop", timeout=1.0)
            logger.warning(f"🤖 АСПР (ML): {last_explanation} | данные: {features_dict}")
            return {"action": "emergency_stop", "reason": last_reason, "explanation": last_explanation}
        except Exception as e:
            logger.error(f"Ошибка ML-остановки: {e}")
        return None

    # ===== 3. Скорость сближения =====
    danger_threshold = 15 + max(0, approach_speed * 0.4)
    if distance < danger_threshold and approach_speed > 7:
        last_explanation = f"⚡ Слишком быстрое сближение! {approach_speed:.1f} см/с (порог: {danger_threshold:.1f} см)"
        last_reason = "high_approach_speed"
        if ASPR_MODE == "learning":
            logger.info(f"🎓 Режим обучения: {last_explanation}")
            return {"action": "log_only", "reason": last_reason, "explanation": last_explanation}

        aspr_active = True
        aspr_interventions += 1
        try:
            requests.get(f"{ESP32_CMD_URL}?move=stop", timeout=1.0)
            logger.warning(f"⚡ АСПР: {last_explanation}")
            return {"action": "emergency_stop", "reason": last_reason, "explanation": last_explanation}
        except Exception as e:
            logger.error(f"Ошибка остановки по скорости: {e}")
        return None

    # ===== 4. Базовое правило =====
    if distance < 15:
        last_explanation = f"🛑 Слишком близко! {distance:.1f} см (порог: 15 см)"
        last_reason = "too_close"
        if ASPR_MODE == "learning":
            logger.info(f"🎓 Режим обучения: {last_explanation}")
            return {"action": "log_only", "reason": last_reason, "explanation": last_explanation}

        aspr_active = True
        aspr_interventions += 1
        try:
            requests.get(f"{ESP32_CMD_URL}?move=stop", timeout=1.0)
            logger.warning(f"🚨 АСПР: {last_explanation}")
            return {"action": "emergency_stop", "reason": last_reason, "explanation": last_explanation}
        except Exception as e:
            logger.error(f"Не удалось отправить аварийную команду: {e}")
        return None

    # ===== БЕЗОПАСНОЕ СОСТОЯНИЕ =====
    if distance > 50:
        aspr_active = False
        last_explanation = f"✅ Безопасно: {distance:.1f} см, наклон {az:.2f}"

    return None


# ===== ПРОВЕРКА БЕЗОПАСНОСТИ КОМАНДЫ =====
def check_command_safety(command, sensor_data):
    """
    Проверка безопасности команды перед выполнением
    Вызывается перед отправкой команды на ESP32
    """
    if command == "forward":
        ultrasonic = sensor_data.get("ultrasonic", {})
        imu = sensor_data.get("imu", {}).get("calibrated", {})
        distance = ultrasonic.get("distance_cm", 999)
        az = imu.get("az_g", 1.0)

        # Наклон → блокировка
        if az < 0.85:
            return {
                "blocked": True,
                "reason": f"blocked_by_tilt_az_{az:.2f}"
            }

        # Расстояние → блокировка
        if distance is not None and distance < 20:
            return {
                "blocked": True,
                "reason": f"blocked_by_aspr_distance_{distance:.1f}cm"
            }

    return {"blocked": False}


# ===== МЕТРИКИ =====
def get_aspr_metrics():
    """Получение метрик АСПР"""
    return {
        "active": aspr_active,
        "interventions": aspr_interventions,
        "mode": ASPR_MODE,
        "model_loaded": collision_model is not None
    }


# ===== ОБЪЯСНЕНИЕ ДЛЯ ИНТЕРФЕЙСА =====
def get_aspr_explanation():
    """Возвращает текущее объяснение АСПР для отображения в интерфейсе"""
    global last_explanation, aspr_active, aspr_interventions, ASPR_MODE, collision_model
    return {
        "explanation": last_explanation,
        "active": aspr_active,
        "interventions": aspr_interventions,
        "mode": ASPR_MODE,
        "model_loaded": collision_model is not None
    }