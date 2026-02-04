from flask import Flask, render_template, request, Response, jsonify
import requests
import time
import io
import os
from PIL import Image, ImageDraw
import logging
import threading
from core import aspr
from db import init_database, save_to_disk, get_metrics, save_sensor_data, save_command

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Настройки подключения к ESP32
ESP32_IP = "127.0.0.1"
ESP32_CMD_URL = f"http://{ESP32_IP}:5000/cmd"
ESP32_SENSOR_URL = f"http://{ESP32_IP}:5000/sensor"
ESP32_STREAM_URL = f"http://{ESP32_IP}:5000/stream"

# Глобальное состояние
latest_sensor_data = {}
sensor_lock = threading.Lock()
current_operator = None
operator_lock = threading.Lock()


def background_logger():
    """Фоновый сбор данных"""
    global latest_sensor_data
    while True:
        try:
            resp = requests.get(ESP32_SENSOR_URL, timeout=2.0)
            if resp.status_code == 200:
                data = resp.json()
                with sensor_lock:
                    latest_sensor_data = data.copy()

                # Сохранение в буфер
                ultrasonic = data.get("ultrasonic", {})
                imu = data.get("imu", {}).get("calibrated", {})
                save_sensor_data(
                    {
                        "ts": data.get("timestamp", int(time.time() * 1000)),
                        "dist": ultrasonic.get("distance_cm"),
                        "ax": imu.get("ax_g"),
                        "ay": imu.get("ay_g"),
                        "az": imu.get("az_g"),
                        "gz": imu.get("gz_dps"),
                    }
                )

                # Анализ АСПР
                aspr_result = aspr.analyze_sensor_data(data)
                if aspr_result:
                    save_command("stop", "aspr", None, aspr_result["reason"])

            time.sleep(0.5)
        except Exception as e:
            logger.error(f"Ошибка фонового сбора: {e}")
            time.sleep(1)


def send_cmd_to_esp32(move):
    try:
        resp = requests.get(f"{ESP32_CMD_URL}?move={move}", timeout=2.0)
        return resp.text, resp.status_code
    except Exception as e:
        logger.error(f"Ошибка отправки команды: {e}")
        raise


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/cmd")
def cmd():
    move = request.args.get("move", "stop")
    operator_name = request.args.get("operator", "Anonymous")

    with operator_lock:
        if current_operator != operator_name:
            return "NO_CONTROL_RIGHTS", 403

    with sensor_lock:
        current_data = latest_sensor_data.copy()

    safety_check = aspr.check_command_safety(move, current_data)
    if safety_check["blocked"]:
        return f"BLOCKED_BY_ASPR: {safety_check['reason']}", 403

    try:
        result, status = send_cmd_to_esp32(move)
        save_command(move, "human", operator_name)
        logger.info(f"Команда '{move}' от {operator_name} выполнена")
        return result, status
    except Exception as e:
        return f"ESP32 cmd error: {str(e)}", 500


@app.route("/sensor")
def sensor_proxy():
    with sensor_lock:
        data = latest_sensor_data.copy() if latest_sensor_data else {"error": "no_data"}
    return jsonify(data), 200


@app.route("/video_feed")
def video_feed():
    def generate():
        try:
            resp = requests.get(ESP32_STREAM_URL, stream=True, timeout=5.0)
            resp.raise_for_status()
            for chunk in resp.iter_content(chunk_size=10240):
                if chunk:
                    yield chunk
        except Exception as e:
            logger.warning(f"Ошибка видеопотока: {e}")
            placeholder_path = os.path.join(app.static_folder, "no_signal.png")
            with open(placeholder_path, "rb") as f:
                img_bytes = f.read()
            while True:
                yield (
                    b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + img_bytes + b"\r\n"
                )
                time.sleep(1)

    return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/control")
def control_status():
    operator_name = request.args.get("operator", "Anonymous")
    with operator_lock:
        return jsonify(
            {
                "current_operator": current_operator,
                "has_control": operator_name == current_operator,
            }
        )


@app.route("/take_control", methods=["POST"])
def take_control():
    data = request.get_json()
    operator_name = data.get("name", "Anonymous")
    with operator_lock:
        global current_operator
        current_operator = operator_name
    logger.info(f"Оператор '{operator_name}' взял управление")
    return jsonify({"status": "success", "operator": operator_name})


@app.route("/release_control", methods=["POST"])
def release_control():
    data = request.get_json()
    operator_name = data.get("name", "Anonymous")
    with operator_lock:
        global current_operator
        if current_operator == operator_name:
            current_operator = None
            logger.info(f"Оператор '{operator_name}' отпустил управление")
            return jsonify({"status": "released"})
        else:
            return jsonify({"status": "error", "message": "Not your control"}), 403


@app.route("/metrics")
def metrics():
    return jsonify(get_metrics())
@app.route("/aspr_status")
def aspr_status():
    """Статус и объяснение АСПР для отображения в интерфейсе"""
    return jsonify(aspr.get_aspr_explanation())

if __name__ == "__main__":
    init_database()
    aspr.init_aspr(ESP32_CMD_URL)

    # Запуск фоновых потоков
    threading.Thread(target=background_logger, daemon=True).start()
    threading.Thread(
        target=lambda: [time.sleep(2) or save_to_disk() for _ in iter(int, 1)],
        daemon=True,
    ).start()

    logger.info("🚀 Flask-сервер запущен на http://0.0.0.0:80")
    app.run(host="0.0.0.0", port=80, debug=False, threaded=True)
