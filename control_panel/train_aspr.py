import sqlite3
import pandas as pd
import joblib
import os
from sklearn.tree import DecisionTreeClassifier, plot_tree
import matplotlib.pyplot as plt

DB_PATH = "telemetry.db"
MODEL_PATH = "aspr_model.pkl"


def load_and_prepare_data():
    if not os.path.exists(DB_PATH):
        print(f"❌ БД не найдена: {DB_PATH}")
        print("Сначала запусти main.py и покатай робота 5 минут!")
        return None

    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("""
        SELECT ts, dist, ax, ay, az, gz 
        FROM sensor_data 
        WHERE dist IS NOT NULL AND az IS NOT NULL
        ORDER BY ts
    """, conn)
    conn.close()

    if len(df) < 100:
        print(f"⚠️  Слишком мало данных: {len(df)} записей")
        print("Покатай робота ещё 2–3 минуты и повтори.")
        return None

    print(f"✅ Загружено {len(df)} записей из БД")
    return df


def label_collisions(df):
    """Разметка: 1 = столкновение через 0.5с, 0 = безопасно"""
    df = df.copy()
    df['label'] = 0

    # Ищем моменты, где через 5 записей (≈0.5с) расстояние резко падает
    for i in range(len(df) - 6):
        current_dist = df.iloc[i]['dist']
        future_dist = df.iloc[i + 5]['dist']

        # Если сейчас далеко (>20см), а через 0.5с близко (<12см) → почти столкновение
        if current_dist > 20 and future_dist < 12:
            df.at[i, 'label'] = 1

    collisions = df['label'].sum()
    print(f"🔍 Найдено {collisions} ситуаций 'почти столкновения'")

    if collisions < 10:
        print("⚠️  Мало примеров столкновений! Покатай ближе к стене.")
        return None

    return df


def train_and_save_model(df):
    """Обучение и сохранение модели"""
    # Признаки
    X = df[['dist', 'az', 'gz']].copy()
    X['approach_speed'] = -df['dist'].diff().fillna(0) * 10  # скорость сближения

    y = df['label']

    # Простое дерево (понятное для защиты!)
    model = DecisionTreeClassifier(
        max_depth=4,  # не слишком глубокое — легко объяснить
        min_samples_leaf=8,
        random_state=42
    )
    model.fit(X, y)

    # Сохранение
    joblib.dump(model, MODEL_PATH)
    print(f"\n✅ Модель сохранена: {MODEL_PATH}")

    # Визуализация дерева (для презентации!)
    plt.figure(figsize=(14, 8))
    plot_tree(
        model,
        feature_names=X.columns,
        class_names=["безопасно", "столкновение"],
        filled=True,
        rounded=True,
        fontsize=10
    )
    plt.savefig("aspr_tree.png", dpi=150, bbox_inches='tight')
    print("🖼️  Дерево решений: aspr_tree.png (покажи на защите!)")

    # Важность признаков
    print("\n📊 Важность признаков:")
    for name, val in zip(X.columns, model.feature_importances_):
        print(f"  • {name:20s}: {val:.1%}")

    return model


if __name__ == "__main__":
    print("🚀 Обучение модели АСПР...\n")

    # Установка зависимостей (если нет)
    try:
        import joblib, sklearn, matplotlib
    except ImportError:
        print("❌ Нет нужных библиотек. Выполни:")
        print("   pip install pandas scikit-learn matplotlib joblib")
        exit(1)

    df = load_and_prepare_data()
    if df is None:
        exit(1)

    df = label_collisions(df)
    if df is None:
        exit(1)

    model = train_and_save_model(df)

    print("\n💡 Совет для защиты:")
    print("   'Моя модель предсказывает столкновение за 0.5 секунды")
    print("    на основе расстояния, наклона и скорости сближения'")