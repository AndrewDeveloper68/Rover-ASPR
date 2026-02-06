#include "esp_camera.h"
#include <WiFi.h>
#include <WebServer.h>
#include <ESP32Servo.h>
#include <Wire.h>
#include "board_config.h"

// === НАСТРОЙКИ СЕТИ ===
const char *ssid = "ASUS";
const char *password = "frolov68";

// === ПИНЫ РОВЕРА ===
#define LEFT_SERVO_PIN 48
#define RIGHT_SERVO_PIN 3
#define TRIG_PIN 14
#define ECHO_PIN 2

#define FWD_LEFT 40
#define FWD_RIGHT 140
#define BWD_LEFT 140
#define BWD_RIGHT 40
#define STOP_ANGLE 90

Servo leftServo, rightServo;
bool isMoving = false;
unsigned long moveUntil = 0;

// === MPU6050 ===
#define MPU6050_PWR_MGMT_1 0x6B
#define MPU6050_ACCEL_XOUT_H 0x3B
#define MPU6050_GYRO_CONFIG 0x1B
#define MPU6050_ACCEL_CONFIG 0x1C

#define MPU6050_SDA_PIN 21 // ФИЗИЧЕСКИЙ SDA
#define MPU6050_SCL_PIN 20 // ФИЗИЧЕСКИЙ SCL

bool mpuPresent = false;
uint8_t mpuAddr = 0x68; // Будем автоопределять 0x68/0x69

// === HC-SR04 ===
volatile bool echoDone = false;
volatile unsigned long echoStart = 0;
volatile unsigned long echoDuration = 0;
unsigned long lastTriggerTime = 0;
bool isMeasuring = false;
float lastValidDistance = -1.0;

// === КАМЕРА ===
bool cameraInitialized = false;
unsigned long lastRecoveryAttempt = 0;
const unsigned long RECOVERY_INTERVAL = 1000; // 1 секунда

WebServer server(80);
extern void startCameraServer();

// ====== HC‑SR04 ======

void IRAM_ATTR echoInterrupt()
{
    if (digitalRead(ECHO_PIN) == HIGH)
    {
        echoStart = micros();
    }
    else
    {
        echoDuration = micros() - echoStart;
        echoDone = true;
    }
}

float getDistanceCM_NonBlocking()
{
    if (!isMeasuring && (millis() - lastTriggerTime > 100))
    {
        digitalWrite(TRIG_PIN, LOW);
        delayMicroseconds(2);
        digitalWrite(TRIG_PIN, HIGH);
        delayMicroseconds(10);
        digitalWrite(TRIG_PIN, LOW);
        isMeasuring = true;
        attachInterrupt(digitalPinToInterrupt(ECHO_PIN), echoInterrupt, CHANGE);
        lastTriggerTime = millis();
        return -2.0;
    }

    if (echoDone)
    {
        detachInterrupt(digitalPinToInterrupt(ECHO_PIN));
        isMeasuring = false;
        echoDone = false;
        if (echoDuration == 0 || echoDuration > 30000)
        {
            return -1.0;
        }
        float dist = echoDuration * 0.0343 / 2.0;
        if (dist >= 0 && dist <= 400)
            lastValidDistance = dist;
        return dist;
    }

    if (isMeasuring && (micros() - echoStart > 30000))
    {
        detachInterrupt(digitalPinToInterrupt(ECHO_PIN));
        isMeasuring = false;
        echoDone = false;
        return -1.0;
    }
    return -2.0;
}

// ====== Сканер I2C для MPU ======

void scanI2C()
{
    Serial.println("🔍 I2C scan на пинах SDA=SCL:");
    Serial.printf("    SDA=%d, SCL=%d\n", MPU6050_SDA_PIN, MPU6050_SCL_PIN);

    bool found = false;
    for (uint8_t addr = 1; addr < 127; addr++)
    {
        Wire.beginTransmission(addr);
        uint8_t err = Wire.endTransmission();
        if (err == 0)
        {
            Serial.printf("   ➜ I2C‑устройство по адресу 0x%02X\n", addr);
            found = true;
        }
    }
    if (!found)
    {
        Serial.println("   ⚠️ На шине I2C ничего не найдено");
    }
}

// ====== MPU6050: инициализация и чтение ======

void initMPU6050()
{
    Wire.begin(MPU6050_SDA_PIN, MPU6050_SCL_PIN, 400000);
    delay(100);

    scanI2C(); // для отладки: сразу видно, жив ли вообще модуль

    // Пробуем 0x68, потом 0x69
    uint8_t candidates[2] = {0x68, 0x69};
    mpuPresent = false;

    for (int i = 0; i < 2; i++)
    {
        uint8_t addr = candidates[i];

        Wire.beginTransmission(addr);
        uint8_t error = Wire.endTransmission();

        if (error == 0)
        {
            mpuAddr = addr;
            mpuPresent = true;
            Serial.printf("\n✅ Найден MPU6050 по адресу 0x%02X\n", mpuAddr);
            break;
        }
    }

    if (!mpuPresent)
    {
        Serial.println("\n❌ MPU6050 NOT FOUND по адресам 0x68/0x69");
        Serial.println("   • Проверь питание 3.3V, GND, SDA/SCL, AD0");
        return;
    }

    // Инициализация по выбранному адресу
    Wire.beginTransmission(mpuAddr);
    Wire.write(MPU6050_PWR_MGMT_1);
    Wire.write(0x00);
    Wire.endTransmission(true);
    delay(10);

    Wire.beginTransmission(mpuAddr);
    Wire.write(MPU6050_GYRO_CONFIG);
    Wire.write(0x00); // ±250°/с
    Wire.endTransmission(true);

    Wire.beginTransmission(mpuAddr);
    Wire.write(MPU6050_ACCEL_CONFIG);
    Wire.write(0x00); // ±2g
    Wire.endTransmission(true);

    Serial.printf("✅ MPU6050 инициализирован: addr=0x%02X (SDA=%d, SCL=%d)\n",
                  mpuAddr, MPU6050_SDA_PIN, MPU6050_SCL_PIN);
}

bool readMPU6050(int16_t *ax, int16_t *ay, int16_t *az,
                 int16_t *gx, int16_t *gy, int16_t *gz)
{
    if (!mpuPresent)
        return false;

    Wire.beginTransmission(mpuAddr);
    Wire.write(MPU6050_ACCEL_XOUT_H);
    if (Wire.endTransmission(false) != 0)
        return false;

    if (Wire.requestFrom(mpuAddr, (uint8_t)14) != 14)
        return false;

    *ax = (Wire.read() << 8 | Wire.read());
    *ay = (Wire.read() << 8 | Wire.read());
    *az = (Wire.read() << 8 | Wire.read());
    Wire.read();
    Wire.read(); // температура
    *gx = (Wire.read() << 8 | Wire.read());
    *gy = (Wire.read() << 8 | Wire.read());
    *gz = (Wire.read() << 8 | Wire.read());
    return true;
}

// ====== СЕРВЫ ======

void stopServos()
{
    if (leftServo.attached())
    {
        leftServo.write(STOP_ANGLE);
        delay(50);
        leftServo.detach();
    }
    if (rightServo.attached())
    {
        rightServo.write(STOP_ANGLE);
        delay(50);
        rightServo.detach();
    }
    isMoving = false;
}

void moveServos(int leftAngle, int rightAngle, unsigned long durationMs)
{
    if (!leftServo.attached())
    {
        leftServo.setPeriodHertz(50);
        leftServo.attach(LEFT_SERVO_PIN, 500, 2400);
    }
    if (!rightServo.attached())
    {
        rightServo.setPeriodHertz(50);
        rightServo.attach(RIGHT_SERVO_PIN, 500, 2400);
    }
    leftServo.write(leftAngle);
    rightServo.write(rightAngle);
    moveUntil = millis() + durationMs;
    isMoving = true;
}

// ====== HTTP ======

void handleCommand()
{
    String move = server.arg("move");
    const unsigned long DURATION = 1000;
    stopServos();

    if (move == "forward")
    {
        moveServos(FWD_LEFT, FWD_RIGHT, DURATION);
        Serial.println("[CMD] Вперёд");
    }
    else if (move == "backward")
    {
        moveServos(BWD_LEFT, BWD_RIGHT, DURATION);
        Serial.println("[CMD] Назад");
    }
    else if (move == "left")
    {
        moveServos(FWD_LEFT, STOP_ANGLE, DURATION);
        Serial.println("[CMD] Влево");
    }
    else if (move == "right")
    {
        moveServos(STOP_ANGLE, FWD_RIGHT, DURATION);
        Serial.println("[CMD] Вправо");
    }
    else if (move == "stop")
    {
        stopServos();
        Serial.println("[CMD] ⏹ Стоп");
    }
    else
    {
        server.send(400, "text/plain", "Unknown move: " + move);
        Serial.printf("[CMD] Неизвестная команда: %s\n", move.c_str());
        return;
    }
    server.send(200, "text/plain", "OK");
}

void handleSensor()
{
    // MPU6050
    int16_t ax = 0, ay = 0, az = 0, gx = 0, gy = 0, gz = 0;
    bool mpuOk = readMPU6050(&ax, &ay, &az, &gx, &gy, &gz);

    // Ультразвук
    float distance = getDistanceCM_NonBlocking();
    if (distance >= 0)
    {
        lastValidDistance = distance;
    }

    // ЛОГ В КОНСОЛЬ
    static unsigned long lastPrint = 0;
    if (millis() - lastPrint >= 500)
    {
        lastPrint = millis();
        if (mpuOk)
        {
            Serial.printf("[IMU] aX:%5d aY:%5d aZ:%5d | gX:%5d gY:%5d gZ:%5d (addr=0x%02X)\n",
                          ax, ay, az, gx, gy, gz, mpuAddr);
        }
        else if (mpuPresent)
        {
            Serial.println("[IMU] read_failed");
        }
        else
        {
            Serial.println("[IMU] not_detected");
        }

        if (distance >= 0)
        {
            Serial.printf("[ULTRASONIC] Distance: %.1f cm\n", distance);
        }
        else if (distance == -1.0)
        {
            Serial.println("[ULTRASONIC] Timeout/error");
        }
    }

    // JSON /sensor
    String json = "{\"timestamp\":" + String(millis());

    if (mpuOk)
    {
        json += ",\"imu\":{\"status\":\"ok\",\"raw\":{\"ax\":" + String(ax) + ",\"ay\":" + String(ay) + ",\"az\":" + String(az) +
                ",\"gx\":" + String(gx) + ",\"gy\":" + String(gy) + ",\"gz\":" + String(gz) +
                "},\"calibrated\":{\"ax_g\":" + String(ax / 16384.0, 2) +
                ",\"ay_g\":" + String(ay / 16384.0, 2) +
                ",\"az_g\":" + String(az / 16384.0, 2) +
                ",\"gx_dps\":" + String(gx / 131.0, 1) +
                ",\"gy_dps\":" + String(gy / 131.0, 1) +
                ",\"gz_dps\":" + String(gz / 131.0, 1) + "}}";
    }
    else
    {
        if (mpuPresent)
        {
            json += ",\"imu\":{\"status\":\"error\",\"error\":\"read_failed\"}";
        }
        else
        {
            json += ",\"imu\":{\"status\":\"error\",\"error\":\"not_detected\"}";
        }
    }

    if (lastValidDistance >= 0)
    {
        json += ",\"ultrasonic\":{\"status\":\"ok\",\"distance_cm\":" + String(lastValidDistance, 1) + "}";
    }
    else
    {
        json += ",\"ultrasonic\":{\"status\":\"error\",\"error\":\"no_valid_reading_yet\"}";
    }

    json += "}";
    server.send(200, "application/json", json);
}

// ====== КАМЕРА ======

bool initCamera()
{
    camera_config_t config;
    memset(&config, 0, sizeof(config));

    config.ledc_channel = LEDC_CHANNEL_0;
    config.ledc_timer = LEDC_TIMER_0;
    config.pin_d0 = Y2_GPIO_NUM;
    config.pin_d1 = Y3_GPIO_NUM;
    config.pin_d2 = Y4_GPIO_NUM;
    config.pin_d3 = Y5_GPIO_NUM;
    config.pin_d4 = Y6_GPIO_NUM;
    config.pin_d5 = Y7_GPIO_NUM;
    config.pin_d6 = Y8_GPIO_NUM;
    config.pin_d7 = Y9_GPIO_NUM;
    config.pin_xclk = XCLK_GPIO_NUM;
    config.pin_pclk = PCLK_GPIO_NUM;
    config.pin_vsync = VSYNC_GPIO_NUM;
    config.pin_href = HREF_GPIO_NUM;
    config.pin_sccb_sda = SIOD_GPIO_NUM;
    config.pin_sccb_scl = SIOC_GPIO_NUM;
    config.pin_pwdn = PWDN_GPIO_NUM;
    config.pin_reset = RESET_GPIO_NUM;
    config.xclk_freq_hz = 7000000;
    config.frame_size = FRAMESIZE_QVGA;
    config.pixel_format = PIXFORMAT_JPEG;
    config.jpeg_quality = 12;
    config.fb_count = 1;
    config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
    config.fb_location = CAMERA_FB_IN_PSRAM;

    if (psramFound())
    {
        config.jpeg_quality = 10;
        config.fb_count = 2;
        config.grab_mode = CAMERA_GRAB_LATEST;
        Serial.println("✅ PSRAM обнаружен (8 МБ)");
    }
    else
    {
        Serial.println("⚠️ PSRAM НЕ обнаружен! Проверьте: Tools → PSRAM → OPI PSRAM");
        config.fb_location = CAMERA_FB_IN_DRAM;
        config.frame_size = FRAMESIZE_QQVGA;
    }

    esp_err_t err = esp_camera_init(&config);
    if (err != ESP_OK)
    {
        Serial.printf("❌ Ошибка инициализации камеры (0x%x)\n", err);
        return false;
    }

    sensor_t *s = esp_camera_sensor_get();
    if (s)
    {
        s->set_framesize(s, FRAMESIZE_QVGA);
#if defined(CAMERA_MODEL_ESP32S3_EYE)
        s->set_vflip(s, 1);
#endif
        Serial.println("✅ Камера инициализирована (7 МГц, QVGA)");
    }

    return true;
}

// ====== setup / loop ======

void setup()
{
    Serial.begin(115200);
    delay(100);

    Serial.println("\n==========================================");
    Serial.println(" ESP32-S3-EYE Rover: Камера + MPU6050 + HC-SR04");
    Serial.println("==========================================");

    Serial.printf("🔧 MPU6050: SDA=GPIO%d, SCL=GPIO%d\n", MPU6050_SDA_PIN, MPU6050_SCL_PIN);

    // 1. Камера
    Serial.println("🔧 Инициализация камеры...");
    cameraInitialized = initCamera();

    // 2. MPU6050
    initMPU6050();

    // 3. Ультразвук
    pinMode(TRIG_PIN, OUTPUT);
    pinMode(ECHO_PIN, INPUT);
    digitalWrite(TRIG_PIN, LOW);
    Serial.println("✅ HC-SR04: пины настроены");

    // 4. Сервы
    ESP32PWM::allocateTimer(0);
    ESP32PWM::allocateTimer(1);
    Serial.printf("✅ Сервоприводы: ЛЕВЫЙ=GPIO%d | ПРАВЫЙ=GPIO%d\n",
                  LEFT_SERVO_PIN, RIGHT_SERVO_PIN);

    // 5. Wi‑Fi
    Serial.print("\n📶 Подключение к Wi‑Fi: ");
    Serial.println(ssid);
    WiFi.begin(ssid, password);
    WiFi.setSleep(false);

    unsigned long startAttemptTime = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - startAttemptTime < 15000)
    {
        Serial.print(".");
        delay(500);
    }

    if (WiFi.status() != WL_CONNECTED)
    {
        Serial.println("\n❌ Ошибка подключения к Wi‑Fi!");
    }
    else
    {
        Serial.println("\n✅ Wi‑Fi подключён!");
        Serial.print("🌐 IP адрес: ");
        Serial.println(WiFi.localIP());
        Serial.println("   • Управление: http://<IP>/cmd?move=forward|backward|left|right|stop");
        Serial.println("   • Датчики:    http://<IP>/sensor");
        Serial.println("   • Камера:     http://<IP>:81/stream");
        Serial.println("   • Снимок:     http://<IP>/capture");
    }

    // 6. HTTP
    server.on("/cmd", HTTP_GET, handleCommand);
    server.on("/sensor", HTTP_GET, handleSensor);
    server.begin();
    Serial.println("✅ WebServer запущен на порту 80 (/cmd, /sensor)");

    // 7. Сервер камеры
    if (cameraInitialized)
    {
        startCameraServer();
    }
    else
    {
        Serial.println("⚠️ Сервер камеры НЕ запущен (камера не инициализирована)");
    }

    Serial.println("\n==========================================");
    Serial.println("✅ Система готова к работе");
    Serial.println("==========================================\n");
}

void loop()
{
    server.handleClient();

    if (isMoving && millis() >= moveUntil)
    {
        Serial.println("[AUTO] ⏹ Автоостановка сервоприводов");
        stopServos();
    }

    getDistanceCM_NonBlocking();

    if (!cameraInitialized && millis() - lastRecoveryAttempt > RECOVERY_INTERVAL)
    {
        lastRecoveryAttempt = millis();
        Serial.println("\n🔄 Попытка восстановления камеры...");
        esp_camera_deinit();
        delay(100);
        cameraInitialized = initCamera();
        if (cameraInitialized)
        {
            Serial.println("✅ Камера восстановлена!");
            startCameraServer();
        }
    }

    delay(10);
}
