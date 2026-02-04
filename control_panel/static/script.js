let operatorName = localStorage.getItem('operatorName');
if (!operatorName) {
    const name = prompt("Введите ваше имя:");
    operatorName = name && name.trim() ? name.trim() : "Оператор";
    localStorage.setItem('operatorName', operatorName);
}

let hasControl = false;
let currentOperator = null;

function safeInit(fn) {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', fn);
    } else {
        fn();
    }
}

function updateControlUI() {
    const indicatorText = document.getElementById('control-indicator-text');
    const indicatorDot = document.getElementById('control-indicator-dot');
    const btn = document.getElementById('control-btn');
    const controlsSection = document.getElementById('controls-section');

    if (!indicatorText || !indicatorDot || !btn || !controlsSection) {
        return;
    }

    if (hasControl) {
        indicatorDot.classList.add('active');
        indicatorDot.classList.remove('inactive');
        indicatorText.textContent = `Управляет: ${operatorName}`;

        btn.textContent = "Отпустить управление";
        btn.className = "btn btn-sm btn-danger";

        controlsSection.style.display = 'block';
    } else {
        indicatorDot.classList.remove('active');
        indicatorDot.classList.add('inactive');
        indicatorText.textContent = `Текущий оператор: ${currentOperator || 'никто'}`;

        btn.textContent = "Взять управление";
        btn.className = "btn btn-sm btn-success";

        controlsSection.style.display = 'none';
    }
}

async function checkControlStatus() {
    try {
        const encodedName = encodeURIComponent(operatorName);
        const response = await fetch(`/control?operator=${encodedName}`);
        const data = await response.json();

        hasControl = data.has_control;
        currentOperator = data.current_operator;
        updateControlUI();
    } catch (error) {
        console.error("Ошибка статуса управления:", error);
    }
}

async function toggleControl() {
    const url = hasControl ? '/release_control' : '/take_control';
    try {
        const response = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: operatorName })
        });
        const data = await response.json();

        if (data.status === 'success') {
            hasControl = true;
            updateControlUI();
        } else if (data.status === 'released') {
            hasControl = false;
            updateControlUI();
        } else {
            alert("Ошибка: " + (data.message || "Не удалось изменить управление"));
        }
    } catch (error) {
        console.error("Ошибка управления:", error);
        alert("Не удалось изменить статус управления");
    }
}

async function sendCmd(move) {
    if (!hasControl) {
        alert("У вас нет прав управления!");
        return;
    }

    try {
        const encodedName = encodeURIComponent(operatorName);
        const url = `/cmd?move=${move}&operator=${encodedName}`;
        const response = await fetch(url);
        const text = await response.text();

        if (!response.ok) {
            if (text.includes("BLOCKED_BY_ASPR")) {
                alert("⚠️ АСПР заблокировала команду: " + text);
            } else if (text === "NO_CONTROL_RIGHTS") {
                alert("Управление передано другому оператору!");
                hasControl = false;
                updateControlUI();
            } else {
                alert("Ошибка: " + text);
            }
        }
    } catch (err) {
        console.error(`Ошибка команды "${move}":`, err);
        alert(`Ошибка: ${err.message}`);
    }
}

document.addEventListener('keydown', (e) => {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

    const key = e.key.toLowerCase();
    let command = null;

    switch (key) {
        case 'w': command = 'forward'; break;
        case 'a': command = 'left'; break;
        case 's': command = 'backward'; break;
        case 'd': command = 'right'; break;
        case ' ': command = 'stop'; e.preventDefault(); break;
    }

    if (command && hasControl) {
        sendCmd(command);
        highlightButton(key);
    }
});

function highlightButton(key) {
    const buttonMap = {
        'w': '.forward',
        'a': '.left',
        's': '.backward',
        'd': '.right',
        ' ': '.stop'
    };
    const selector = buttonMap[key];
    if (!selector) return;

    const btn = document.querySelector(selector);
    if (!btn) return;

    btn.style.transform = 'scale(0.95)';
    btn.style.opacity = '0.8';
    setTimeout(() => {
        btn.style.transform = '';
        btn.style.opacity = '';
    }, 150);
}

function showPlaceholder() {
    const video = document.getElementById('videoStream');
    const placeholder = document.getElementById('placeholder');

    if (!video || !placeholder) return;

    video.style.opacity = '0';
    video.style.visibility = 'hidden';

    placeholder.classList.remove('d-none');
    placeholder.style.display = 'flex';
}

function hidePlaceholder() {
    const video = document.getElementById('videoStream');
    const placeholder = document.getElementById('placeholder');

    if (!video || !placeholder) return;

    video.style.opacity = '1';
    video.style.visibility = 'visible';

    placeholder.classList.add('d-none');
    placeholder.style.display = 'none';
}

function handlePlaceholderError() {
    const img = document.getElementById('staticPlaceholder');
    const text = document.getElementById('fallbackText');

    if (img && text) {
        img.style.display = 'none';
        text.classList.remove('d-none');
    }
}

safeInit(() => {
    const video = document.getElementById('videoStream');
    if (!video) return;

    video.onload = hidePlaceholder;

    setTimeout(() => {
        if (video.complete && video.naturalHeight === 0) {
            showPlaceholder();
        }
    }, 3000);
});

async function updateSensors() {
    try {
        const response = await fetch('/sensor?_=' + Date.now());
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = await response.json();

        const ultrasonicEl = document.getElementById('ultrasonic-data');
        const imuEl = document.getElementById('imu-data');
        if (!ultrasonicEl || !imuEl) return;

        if (data.ultrasonic && data.ultrasonic.distance_cm !== undefined) {
            const dist = data.ultrasonic.distance_cm;
            ultrasonicEl.innerHTML = `
                <div class="distance-value">${dist.toFixed(1)}<span class="unit"> см</span></div>
                ${dist < 20 ? '<div class="warning">⚠️ Близко!</div>' : ''}
            `;
        } else {
            ultrasonicEl.innerHTML = '<span class="sensor-error">Нет данных</span>';
        }

        if (data.imu && data.imu.calibrated) {
            const c = data.imu.calibrated;
            imuEl.innerHTML = `
                <div><strong>Ускорение:</strong></div>
                X: <span class="axis-x">${c.ax_g}</span>g |
                Y: <span class="axis-y">${c.ay_g}</span>g |
                Z: <span class="axis-z">${c.az_g}</span>g
                <div style="margin-top:8px"><strong>Гироскоп:</strong></div>
                Z: <span class="axis-z">${c.gz_dps}</span>°/с
            `;
        } else {
            imuEl.innerHTML = '<span class="sensor-error">Нет данных</span>';
        }

    } catch (error) {
        console.error("Ошибка датчиков:", error);
        const ultrasonicEl = document.getElementById('ultrasonic-data');
        const imuEl = document.getElementById('imu-data');
        if (ultrasonicEl) ultrasonicEl.innerHTML = '<span class="sensor-error">⚠️ Ошибка</span>';
        if (imuEl) imuEl.innerHTML = '<span class="sensor-error">⚠️ Ошибка</span>';
    }
}

async function updateMetrics() {
    try {
        const response = await fetch('/metrics');
        const data = await response.json();

        const s = document.getElementById('metric-sensors');
        const c = document.getElementById('metric-commands');
        const a = document.getElementById('metric-aspr');
        const d = document.getElementById('metric-distance');

        if (s) s.textContent = data.sensor_records || 0;
        if (c) c.textContent = data.total_commands || 0;
        if (a) a.textContent = data.aspr_interventions || 0;
        if (d) d.textContent = data.last_distance !== null ? data.last_distance : '-';

    } catch (error) {
        console.error("Ошибка метрик:", error);
    }
}

safeInit(() => {
    updateControlUI();
    checkControlStatus();

    setInterval(checkControlStatus, 1000);
    setInterval(updateSensors, 200);
    setInterval(updateMetrics, 1000);
});