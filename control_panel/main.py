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
ESP32_IP = "192.168.1.187"
ESP32_CMD_URL = f"http://{ESP32_IP}/cmd"
ESP32_SENSOR_URL = f"http://{ESP32_IP}/sensor"
ESP32_STREAM_URL = f"http://{ESP32_IP}:81/stream"

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

            time.sleep(0.2)
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
    """
    Надёжная проксировка видеопотока с ПОЛНЫМ копированием заголовков.
    """
    client_ip = request.remote_addr
    stream_url = ESP32_STREAM_URL

    logger.info(f"📹 Запрос видеопотока от клиента {client_ip}")

    def generate():
        request_start = time.time()

        try:
            logger.debug(f"🔌 Подключение к {stream_url}...")

            resp = requests.get(
                stream_url,
                stream=True,
                timeout=(5.0, 30.0),
                headers={
                    "User-Agent": "Flask-Proxy/1.0",
                    "Accept": "multipart/x-mixed-replace"
                }
            )

            if resp.status_code != 200:
                logger.error(f"❌ Камера вернула статус {resp.status_code}")
                error_msg = f"ERROR: Camera returned {resp.status_code}"
                yield b"--frame\r\nContent-Type: text/plain\r\n\r\n" + error_msg.encode() + b"\r\n"
                return

            # Логируем полученные заголовки от камеры
            content_type = resp.headers.get('Content-Type', 'unknown')
            logger.info(f"✅ Подключение к камере установлено. Content-Type: {content_type}")

            # Передаём поток напрямую браузеру
            chunk_count = 0
            total_bytes = 0
            last_log_time = time.time()

            for chunk in resp.iter_content(chunk_size=32768):
                if chunk:
                    yield chunk
                    chunk_count += 1
                    total_bytes += len(chunk)

                    if time.time() - last_log_time > 10.0:
                        mbps = (total_bytes * 8) / (1024 * 1024 * (time.time() - request_start))
                        logger.info(
                            f"📊 Поток активен для {client_ip}: {chunk_count} чанков, {total_bytes / 1024:.1f}KB, {mbps:.2f} Mbps")
                        last_log_time = time.time()

            logger.warning(f"⚠️ Поток для {client_ip} завершился (камера закрыла соединение)")

        except requests.exceptions.Timeout:
            duration = time.time() - request_start
            logger.error(f"⏰ Таймаут подключения к камере ({duration:.1f}s)")
            yield b"--frame\r\nContent-Type: text/plain\r\n\r\nERROR: Camera timeout\r\n"

        except requests.exceptions.ConnectionError:
            logger.error(f"🔌 Ошибка соединения с камерой")
            yield b"--frame\r\nContent-Type: text/plain\r\n\r\nERROR: Connection failed\r\n"

        except requests.exceptions.RequestException as e:
            logger.error(f"🌐 Ошибка запроса к камере: {str(e)}")
            yield b"--frame\r\nContent-Type: text/plain\r\n\r\nERROR: Request failed\r\n"

        except Exception as e:
            logger.exception(f"💥 Неожиданная ошибка: {str(e)}")
            yield b"--frame\r\nContent-Type: text/plain\r\n\r\n" + f"ERROR: {str(e)}".encode() + b"\r\n"

        finally:
            if 'resp' in locals():
                try:
                    resp.close()
                    logger.debug(f"CloseOperation: Соединение с камерой закрыто для {client_ip}")
                except:
                    pass

    # === КРИТИЧЕСКИ ВАЖНО: Получаем поток ОДИН РАЗ для чтения заголовков ===
    try:
        # Создаём соединение с камерой для получения заголовков
        resp_headers = requests.get(
            stream_url,
            stream=True,
            timeout=(5.0, 5.0),
            headers={
                "User-Agent": "Flask-Proxy/1.0",
                "Accept": "multipart/x-mixed-replace"
            }
        )

        # Сохраняем оригинальный Content-Type от камеры
        original_content_type = resp_headers.headers.get('Content-Type', 'multipart/x-mixed-replace')
        logger.info(f"📨 Оригинальный Content-Type от камеры: {original_content_type}")

        # Закрываем соединение - оно нужно только для заголовков
        resp_headers.close()

    except Exception as e:
        logger.error(f"⚠️ Не удалось получить заголовки от камеры: {e}")
        original_content_type = 'multipart/x-mixed-replace;boundary=123456789000000000000987654321'

    # Создаём ответ с ПРАВИЛЬНЫМ Content-Type от камеры
    response = Response(generate(), mimetype=original_content_type)

    # Добавляем критически важные заголовки
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    response.headers['X-Accel-Buffering'] = 'no'
    response.headers['Access-Control-Allow-Origin'] = '*'  # CORS для безопасности
    response.headers['Connection'] = 'keep-alive'

    logger.info(f"🎬 Отправка видеопотока клиенту {client_ip} с Content-Type: {original_content_type}")
    return response


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

    logger.info("Flask-сервер запущен на http://0.0.0.0:80")
    app.run(host="0.0.0.0", port=80, debug=False, threaded=True)
