from flask import Flask, Response, jsonify, request
import time
import random
import os
import io
from PIL import Image, ImageDraw

app = Flask(__name__)

STATIC_FOLDER = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(STATIC_FOLDER, exist_ok=True)

# Генерация тестового изображения, если его нет
placeholder_path = os.path.join(STATIC_FOLDER, "mock_camera.jpg")
if not os.path.exists(placeholder_path):
    img = Image.new("RGB", (640, 480), color=(30, 30, 50))
    draw = ImageDraw.Draw(img)
    draw.text((180, 220), "МОК-КАМЕРА\nESP32-S3 Rover", fill=(200, 200, 255))
    img.save(placeholder_path)

# Состояние робота
last_command = "stop"
last_cmd_time = time.time()


def generate_mock_imu():
    # Базовые значения в покое
    ax_g = round(random.uniform(-0.05, 0.05), 2)
    ay_g = round(random.uniform(-0.05, 0.05), 2)
    az_g = round(random.uniform(0.95, 1.05), 2)

    # Гироскоп почти нулевой
    gx_dps = round(random.uniform(-1, 1), 1)
    gy_dps = round(random.uniform(-1, 1), 1)
    gz_dps = round(random.uniform(-1, 1), 1)

    # Эмуляция движения при команде
    if last_command == "forward" and time.time() - last_cmd_time < 2:
        ax_g = round(random.uniform(0.2, 0.4), 2)  # ускорение вперёд
    elif last_command == "backward" and time.time() - last_cmd_time < 2:
        ax_g = round(random.uniform(-0.4, -0.2), 2)  # ускорение назад
    elif last_command in ["left", "right"] and time.time() - last_cmd_time < 2:
        gz_dps = round(
            (
                random.uniform(10, 30)
                if last_command == "right"
                else random.uniform(-30, -10)
            ),
            1,
        )

    return {
        "raw": {
            "ax": int(ax_g * 16384),
            "ay": int(ay_g * 16384),
            "az": int(az_g * 16384),
            "gx": int(gx_dps * 131),
            "gy": int(gy_dps * 131),
            "gz": int(gz_dps * 131),
        },
        "calibrated": {
            "ax_g": ax_g,
            "ay_g": ay_g,
            "az_g": az_g,
            "gx_dps": gx_dps,
            "gy_dps": gy_dps,
            "gz_dps": gz_dps,
        },
    }


def generate_mock_ultrasonic():
    base_dist = 150.0  # базовое расстояние

    # Эмуляция приближения к препятствию при движении вперёд
    if last_command == "forward" and time.time() - last_cmd_time < 2:
        base_dist = max(10.0, base_dist - (time.time() - last_cmd_time) * 40)

    # Небольшой шум
    distance = round(base_dist + random.uniform(-5, 5), 1)
    return {"distance_cm": distance}


@app.route("/cmd")
def mock_cmd():
    global last_command, last_cmd_time
    move = request.args.get("move", "stop")
    last_command = move
    last_cmd_time = time.time()
    print(f"[MOCK] Получена команда: {move}")
    return "OK"


@app.route("/sensor")
def mock_sensor():
    imu_data = generate_mock_imu()
    ultrasonic_data = generate_mock_ultrasonic()

    response = {
        "timestamp": int(time.time() * 1000),
        "imu": imu_data,
        "ultrasonic": ultrasonic_data,
    }
    return jsonify(response)


@app.route("/stream")
def mock_stream():
    def generate():
        with open(placeholder_path, "rb") as f:
            img_bytes = f.read()
        while True:
            yield (
                b"--frame\r\n" b"Content-Type: image/jpeg\r\n\r\n" + img_bytes + b"\r\n"
            )
            time.sleep(0.1)  # ~10 FPS

    return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")


@app.route("/")
def index():
    return """
    <h2>Мок-сервер лунохода запущен</h2>
    <p>Этот сервер эмулирует ESP32-S3 Rover:</p>
    <ul>
        <li><a href="/cmd?move=forward">/cmd?move=forward</a> — управление</li>
        <li><a href="/sensor">/sensor</a> — JSON с датчиками</li>
        <li><a href="/stream">/stream</a> — видеопоток (статичный)</li>
    </ul>
    """


if __name__ == "__main__":
    print("  Запуск МОК-СЕРВЕРА лунохода (для разработки веб-интерфейса)")
    print("   IP: http://127.0.0.1:5000")
    print("   Остановка: Ctrl+C")
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
