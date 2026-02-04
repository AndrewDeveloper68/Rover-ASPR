#include "esp_camera.h"
#include <WiFi.h>
#include <WebServer.h>
#include <ESP32Servo.h>
#include <Wire.h>

const char *ssid = "RoverAP";
const char *password = "12345678";

#define LEFT_SERVO_PIN 48 // ЛЕВЫЙ
#define RIGHT_SERVO_PIN 3 // ПРАВЫЙ
Servo leftServo, rightServo;

const int FWD_LEFT = 40;   // Левый вперёд
const int FWD_RIGHT = 140; // Правый вперёд
const int BWD_LEFT = 140;  // Левый назад
const int BWD_RIGHT = 40;  // Правый назад
const int STOP_ANGLE = 90;
bool isMoving = false;
unsigned long moveUntil = 0;

#define MPU6050_ADDR 0x68
#define MPU6050_PWR_MGMT_1 0x6B
#define MPU6050_ACCEL_XOUT_H 0x3B
#define MPU6050_GYRO_CONFIG 0x1B
#define MPU6050_ACCEL_CONFIG 0x1C
#define MPU6050_SDA_PIN 20
#define MPU6050_SCL_PIN 21

#define TRIG_PIN 14
#define ECHO_PIN 2

volatile bool echoDone = false;
volatile unsigned long echoStart = 0;
volatile unsigned long echoDuration = 0;
unsigned long lastTriggerTime = 0;
bool isMeasuring = false;

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
        return echoDuration * 0.0343 / 2.0; // см
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

unsigned long lastSensorPrint = 0;
const unsigned long SENSOR_PRINT_INTERVAL = 500; // 2 раза в секунду

void initMPU6050()
{
    Wire.begin(MPU6050_SDA_PIN, MPU6050_SCL_PIN, 400000);
    delay(100);

    Wire.beginTransmission(MPU6050_ADDR);
    uint8_t error = Wire.endTransmission();

    if (error != 0)
    {
        Serial.println("\nMPU6050 NOT FOUND!");
        Serial.println("   • VCC → 3.3V (НЕ 5V!)");
        Serial.println("   • GND → GND");
        Serial.printf("   • SDA → GPIO%d\n", MPU6050_SDA_PIN);
        Serial.printf("   • SCL → GPIO%d\n", MPU6050_SCL_PIN);
        return;
    }

    Wire.beginTransmission(MPU6050_ADDR);
    Wire.write(MPU6050_PWR_MGMT_1);
    Wire.write(0x00);
    Wire.endTransmission(true);
    delay(10);

    Wire.beginTransmission(MPU6050_ADDR);
    Wire.write(MPU6050_GYRO_CONFIG);
    Wire.write(0x00); // ±250°/с
    Wire.endTransmission(true);

    Wire.beginTransmission(MPU6050_ADDR);
    Wire.write(MPU6050_ACCEL_CONFIG);
    Wire.write(0x00); // ±2g
    Wire.endTransmission(true);

    Serial.printf("\nMPU6050 инициализирован: 0x%02X (SDA=%d, SCL=%d)\n",
                  MPU6050_ADDR, MPU6050_SDA_PIN, MPU6050_SCL_PIN);
}

bool readMPU6050(int16_t *ax, int16_t *ay, int16_t *az, int16_t *gx, int16_t *gy, int16_t *gz)
{
    Wire.beginTransmission(MPU6050_ADDR);
    Wire.write(MPU6050_ACCEL_XOUT_H);
    if (Wire.endTransmission(false) != 0)
        return false;

    if (Wire.requestFrom(MPU6050_ADDR, 14) != 14)
        return false;

    *ax = (Wire.read() << 8 | Wire.read());
    *ay = (Wire.read() << 8 | Wire.read());
    *az = (Wire.read() << 8 | Wire.read());
    Wire.read();
    Wire.read(); // Температура
    *gx = (Wire.read() << 8 | Wire.read());
    *gy = (Wire.read() << 8 | Wire.read());
    *gz = (Wire.read() << 8 | Wire.read());
    return true;
}

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

WebServer server(80);

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
    int16_t ax = 0, ay = 0, az = 0, gx = 0, gy = 0, gz = 0;
    bool mpuOk = readMPU6050(&ax, &ay, &az, &gx, &gy, &gz);

    static float lastValidDistance = -1.0;
    float distance = getDistanceCM_NonBlocking();

    if (distance >= 0)
    {
        lastValidDistance = distance;
    }

    String json = "{\"timestamp\":" + String(millis());

    if (mpuOk)
    {
        json += ",\"imu\":{\"raw\":{\"ax\":" + String(ax) + ",\"ay\":" + String(ay) + ",\"az\":" + String(az) +
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
        json += ",\"imu\":{\"error\":\"read_failed\"}";
    }

    if (lastValidDistance >= 0)
    {
        json += ",\"ultrasonic\":{\"distance_cm\":" + String(lastValidDistance, 1) + "}";
    }
    else
    {
        json += ",\"ultrasonic\":{\"error\":\"no_valid_reading_yet\"}";
    }

    json += "}";
    server.send(200, "application/json", json);
}

#include "camera_index.h"
void startCameraServer();
void setupLedFlash();

void setup()
{
    Serial.begin(115200);
    Serial.println("\n==========================================");
    Serial.println(" ESP32-S3 Rover + MPU6050 + HC-SR04");
    Serial.println("==========================================");
    Serial.printf("🔧 Серво: ЛЕВЫЙ=GPIO%d | ПРАВЫЙ=GPIO%d\n", LEFT_SERVO_PIN, RIGHT_SERVO_PIN);
    Serial.printf("🔧 MPU6050: SDA=GPIO%d | SCL=GPIO%d\n", MPU6050_SDA_PIN, MPU6050_SCL_PIN);
    Serial.printf("🔧 HC-SR04: TRIG=GPIO%d | ECHO=GPIO%d (через делитель!)\n", TRIG_PIN, ECHO_PIN);
    Serial.println("==========================================\n");

    initMPU6050();

    pinMode(TRIG_PIN, OUTPUT);
    pinMode(ECHO_PIN, INPUT);
    digitalWrite(TRIG_PIN, LOW);
    Serial.println("HC-SR04 инициализирован");

    ESP32PWM::allocateTimer(0);
    ESP32PWM::allocateTimer(1);

    // Инициализация камеры
#include "board_config.h"
    camera_config_t config;
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
    config.xclk_freq_hz = 20000000;
    config.frame_size = FRAMESIZE_QVGA;
    config.pixel_format = PIXFORMAT_JPEG;
    config.jpeg_quality = 12;
    config.fb_count = 1;
    config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;

    if (psramFound())
    {
        config.jpeg_quality = 10;
        config.fb_count = 2;
    }

    esp_err_t err = esp_camera_init(&config);
    if (err != ESP_OK)
    {
        Serial.printf("\nCamera init failed: 0x%x\n", err);
        Serial.println("   Продолжаем работу без камеры");
    }
    else
    {
        sensor_t *s = esp_camera_sensor_get();
        s->set_framesize(s, FRAMESIZE_QVGA);
#if defined(LED_GPIO_NUM)
        setupLedFlash();
#endif
        Serial.println("\nКамера инициализирована");
    }

    WiFi.softAP(ssid, password);
    Serial.print("\n📶 Wi-Fi AP: ");
    Serial.print(ssid);
    Serial.print(" | Пароль: ");
    Serial.println(password);
    Serial.print("🌐 IP адрес: ");
    Serial.println(WiFi.softAPIP());

    // Роутинг
    server.on("/cmd", HTTP_GET, handleCommand);
    server.on("/sensor", HTTP_GET, handleSensor);
    server.on("/stream", HTTP_GET, []()
              { server.sendContent(""); });
    server.begin();

    if (err == ESP_OK)
        startCameraServer();

    Serial.println("\n==========================================");
    Serial.println("Rover API готов к работе:");
    Serial.println("   • Управление: /cmd?move=forward|backward|left|right|stop");
    Serial.println("   • Датчики:   /sensor → JSON (IMU + ультразвук)");
    Serial.println("   • Камера:    /stream → видеопоток MJPEG");
    Serial.println("==========================================");
    Serial.println("\nВАЖНО ДЛЯ HC-SR04:");
    Serial.println("   • Питание датчика — ТОЛЬКО от внешнего 5В!");
    Serial.println("   • Echo → GPIO2 ТОЛЬКО через делитель 10к+20к!");
    Serial.println("==========================================\n");
}

void loop()
{
    server.handleClient();

    if (isMoving && millis() >= moveUntil)
    {
        Serial.println("[AUTO] ⏹ Автоостановка по таймеру");
        stopServos();
    }

    if (millis() - lastSensorPrint >= SENSOR_PRINT_INTERVAL)
    {
        lastSensorPrint = millis();

        int16_t ax, ay, az, gx, gy, gz;
        if (readMPU6050(&ax, &ay, &az, &gx, &gy, &gz))
        {
            Serial.printf("[IMU] aX:%5d aY:%5d aZ:%5d | gX:%5d gY:%5d gZ:%5d\n",
                          ax, ay, az, gx, gy, gz);
        }

        float dist = getDistanceCM_NonBlocking();
        if (dist >= 0)
        {
            Serial.printf("[ULTRASONIC] Distance: %.1f cm\n", dist);
        }
        else if (dist == -1.0)
        {
            Serial.println("[ULTRASONIC] Timeout/error");
        }
    }
}