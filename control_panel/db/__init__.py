import os
import duckdb
import threading
from collections import deque
import time

# Путь к БД
DB_PATH = os.path.join(os.path.dirname(__file__), "rover.db")
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sql")

# Глобальные буферы
sensor_buffer = deque(maxlen=100)
command_buffer = deque(maxlen=100)
db_lock = threading.Lock()

# Метрики в памяти
sensor_records = 0
command_records = 0
last_distance = None
aspr_interventions = 0


def init_database():
    """Инициализация базы данных"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = duckdb.connect(DB_PATH, read_only=False)
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        schema_sql = f.read()
    con.execute(schema_sql)
    con.close()
    print("База данных инициализирована успешно!")


def save_sensor_data(data):
    """Добавление данных датчиков в буфер"""
    global sensor_records, last_distance
    sensor_buffer.append(data)
    sensor_records += 1
    last_distance = data["dist"]


def save_command(command, source, operator_name=None, aspr_reason=None):
    """Добавление команды в буфер"""
    global command_records, aspr_interventions
    command_buffer.append(
        {
            "ts": int(time.time() * 1000),
            "cmd": command,
            "src": source,
            "op": operator_name,
            "reason": aspr_reason,
        }
    )
    command_records += 1
    if source == "aspr":
        aspr_interventions += 1


def get_metrics():
    """Получение метрик из памяти"""
    return {
        "sensor_records": sensor_records,
        "last_distance": round(last_distance, 1) if last_distance is not None else None,
        "total_commands": command_records,
        "aspr_interventions": aspr_interventions,
    }


def save_to_disk():
    """Сохранение буфера на диск"""
    global sensor_buffer, command_buffer
    try:
        with db_lock:
            sensors = list(sensor_buffer)
            commands = list(command_buffer)
            sensor_buffer.clear()
            command_buffer.clear()

        if sensors or commands:
            con = duckdb.connect(DB_PATH, read_only=False)
            if sensors:
                con.executemany(
                    """
                    INSERT INTO sensor_log VALUES (?, ?, ?, ?, ?, ?)
                """,
                    [
                        (d["ts"], d["dist"], d["ax"], d["ay"], d["az"], d["gz"])
                        for d in sensors
                    ],
                )
            if commands:
                con.executemany(
                    """
                    INSERT INTO command_log VALUES (?, ?, ?, ?, ?)
                """,
                    [
                        (d["ts"], d["cmd"], d["src"], d["op"], d["reason"])
                        for d in commands
                    ],
                )
            con.close()
    except Exception as e:
        print(f"Ошибка сохранения на диск: {e}")
