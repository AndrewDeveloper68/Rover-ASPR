-- Таблица 1: Лог датчиков (временные ряды)
CREATE TABLE IF NOT EXISTS sensor_log (
    timestamp_ms BIGINT PRIMARY KEY,
    distance_cm FLOAT,
    ax_g FLOAT,
    ay_g FLOAT,
    az_g FLOAT,
    gz_dps FLOAT
);

-- Таблица 2: Исполнение команд
CREATE TABLE IF NOT EXISTS command_log (
    timestamp_ms BIGINT PRIMARY KEY,
    command TEXT NOT NULL,
    source TEXT NOT NULL,
    operator_name TEXT,
    aspr_reason TEXT
);

-- Индексы для быстрого поиска
CREATE INDEX IF NOT EXISTS idx_sensor_time ON sensor_log(timestamp_ms);

CREATE INDEX IF NOT EXISTS idx_command_time ON command_log(timestamp_ms);

CREATE INDEX IF NOT EXISTS idx_command_source ON command_log(source);