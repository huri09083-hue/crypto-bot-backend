"""
Работа с базой данных PostgreSQL (раньше был SQLite — тот файл жил прямо
на диске сервера и стирался при каждом деплое на Render, что теряло всех
юзеров и премиум-статусы. PostgreSQL — постоянная база на отдельном сервисе,
деплои backend её больше не касаются).

Хранит: пользователей, их отслеживаемые монеты/NFT, премиум, платежи,
промокоды, лог алертов.

Подключение берётся из переменной окружения DATABASE_URL — её нужно задать
в Render (Environment Variables), указав туда строку подключения от
провайдера Postgres (Neon, Supabase, сам Render Postgres и т.д.).
"""
import os
from datetime import datetime, timedelta
from contextlib import contextmanager

import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.environ.get("DATABASE_URL", "")


@contextmanager
def get_db():
    if not DATABASE_URL:
        raise RuntimeError(
            "Не задана переменная окружения DATABASE_URL — укажи строку "
            "подключения к PostgreSQL в настройках Render (Environment Variables)."
        )
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    """ISO-строка текущего времени — храним даты как TEXT, как и раньше в SQLite,
    формируя их на стороне Python, а не полагаясь на дефолты самой БД (у Postgres
    и SQLite разные типы времени, так проще избежать несовместимости)."""
    return datetime.now().isoformat()


def init_db():
    """Создаёт таблицы, если их ещё нет. Вызывать один раз при старте."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT,
                is_premium INTEGER DEFAULT 0,
                premium_until TEXT,
                default_alert_percent REAL DEFAULT 5.0,
                created_at TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tracked_coins (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                coin_id TEXT NOT NULL,
                alert_percent REAL DEFAULT 5.0,
                last_price REAL,
                last_checked_at TEXT,
                created_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                UNIQUE(user_id, coin_id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS tracked_nfts (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                nft_id TEXT NOT NULL,
                alert_percent REAL DEFAULT 5.0,
                last_floor_price REAL,
                last_checked_at TEXT,
                created_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                UNIQUE(user_id, nft_id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS alert_log (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                item_type TEXT NOT NULL,
                item_id TEXT NOT NULL,
                item_name TEXT,
                change_percent REAL,
                price REAL,
                created_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                telegram_charge_id TEXT UNIQUE,
                amount_stars INTEGER NOT NULL,
                days_granted INTEGER NOT NULL,
                created_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS promo_codes (
                code TEXT PRIMARY KEY,
                days INTEGER NOT NULL,
                max_uses INTEGER,
                used_count INTEGER DEFAULT 0,
                active INTEGER DEFAULT 1,
                created_at TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS promo_redemptions (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                code TEXT NOT NULL,
                redeemed_at TEXT,
                UNIQUE(user_id, code)
            )
        """)


def get_or_create_user(user_id: int, username: str = None) -> dict:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
        user = cur.fetchone()
        if user is None:
            cur.execute(
                "INSERT INTO users (user_id, username, created_at) VALUES (%s, %s, %s)",
                (user_id, username, _now()),
            )
            cur.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
            user = cur.fetchone()
        return dict(user)


def is_premium(user_id: int) -> bool:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT is_premium, premium_until FROM users WHERE user_id = %s",
            (user_id,),
        )
        user = cur.fetchone()
        if not user:
            return False
        if not user["is_premium"]:
            return False
        if user["premium_until"]:
            until = datetime.fromisoformat(user["premium_until"])
            if until < datetime.now():
                cur.execute(
                    "UPDATE users SET is_premium = 0 WHERE user_id = %s",
                    (user_id,),
                )
                return False
        return True


def grant_premium(user_id: int, days: int = 30):
    """
    Выдаёт/продлевает премиум. Если премиум уже активен — дни добавляются
    к текущей дате окончания, а не затирают её.
    """
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT premium_until, is_premium FROM users WHERE user_id = %s",
            (user_id,),
        )
        user = cur.fetchone()

        base = datetime.now()
        if user and user["is_premium"] and user["premium_until"]:
            current_until = datetime.fromisoformat(user["premium_until"])
            if current_until > base:
                base = current_until

        until = (base + timedelta(days=days)).isoformat()
        cur.execute(
            "UPDATE users SET is_premium = 1, premium_until = %s WHERE user_id = %s",
            (until, user_id),
        )


def get_tracked_coins(user_id: int) -> list:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM tracked_coins WHERE user_id = %s", (user_id,))
        return [dict(r) for r in cur.fetchall()]


def add_tracked_coin(user_id: int, coin_id: str, alert_percent: float = 5.0) -> bool:
    """Возвращает False, если у юзера уже лимит монет (для не-премиум)."""
    FREE_LIMIT = 3
    with get_db() as conn:
        cur = conn.cursor()
        if not is_premium(user_id):
            cur.execute(
                "SELECT COUNT(*) as c FROM tracked_coins WHERE user_id = %s",
                (user_id,),
            )
            count = cur.fetchone()["c"]
            if count >= FREE_LIMIT:
                return False
        cur.execute(
            """INSERT INTO tracked_coins (user_id, coin_id, alert_percent, created_at)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (user_id, coin_id) DO NOTHING""",
            (user_id, coin_id, alert_percent, _now()),
        )
        return True


def remove_tracked_coin(user_id: int, coin_id: str):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM tracked_coins WHERE user_id = %s AND coin_id = %s",
            (user_id, coin_id),
        )


def update_last_price(user_id: int, coin_id: str, price: float):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """UPDATE tracked_coins SET last_price = %s, last_checked_at = %s
               WHERE user_id = %s AND coin_id = %s""",
            (price, _now(), user_id, coin_id),
        )


def get_all_tracked_coins() -> list:
    """
    Для фоновой задачи проверки цен — все записи всех юзеров, вместе с их
    премиум-статусом (нужно боту, чтобы решить, как часто проверять).
    """
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT tc.*, u.is_premium AS user_is_premium, u.premium_until AS user_premium_until
            FROM tracked_coins tc
            JOIN users u ON tc.user_id = u.user_id
        """)
        return [dict(r) for r in cur.fetchall()]


def get_tracked_nfts(user_id: int) -> list:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM tracked_nfts WHERE user_id = %s", (user_id,))
        return [dict(r) for r in cur.fetchall()]


def add_tracked_nft(user_id: int, nft_id: str, alert_percent: float = 5.0) -> bool:
    """Возвращает False, если у юзера уже лимит коллекций (для не-премиум)."""
    FREE_LIMIT = 3
    with get_db() as conn:
        cur = conn.cursor()
        if not is_premium(user_id):
            cur.execute(
                "SELECT COUNT(*) as c FROM tracked_nfts WHERE user_id = %s",
                (user_id,),
            )
            count = cur.fetchone()["c"]
            if count >= FREE_LIMIT:
                return False
        cur.execute(
            """INSERT INTO tracked_nfts (user_id, nft_id, alert_percent, created_at)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (user_id, nft_id) DO NOTHING""",
            (user_id, nft_id, alert_percent, _now()),
        )
        return True


def remove_tracked_nft(user_id: int, nft_id: str):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM tracked_nfts WHERE user_id = %s AND nft_id = %s",
            (user_id, nft_id),
        )


def update_last_nft_price(user_id: int, nft_id: str, floor_price: float):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """UPDATE tracked_nfts SET last_floor_price = %s, last_checked_at = %s
               WHERE user_id = %s AND nft_id = %s""",
            (floor_price, _now(), user_id, nft_id),
        )


def get_all_tracked_nfts() -> list:
    """
    Для фоновой задачи проверки цен — все записи всех юзеров, вместе с их
    премиум-статусом (нужно боту, чтобы решить, как часто проверять).
    """
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT tn.*, u.is_premium AS user_is_premium, u.premium_until AS user_premium_until
            FROM tracked_nfts tn
            JOIN users u ON tn.user_id = u.user_id
        """)
        return [dict(r) for r in cur.fetchall()]


def get_default_alert_percent(user_id: int) -> float:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT default_alert_percent FROM users WHERE user_id = %s",
            (user_id,),
        )
        row = cur.fetchone()
        return row["default_alert_percent"] if row else 5.0


def set_default_alert_percent(user_id: int, percent: float):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE users SET default_alert_percent = %s WHERE user_id = %s",
            (percent, user_id),
        )


def set_coin_alert_percent(user_id: int, coin_id: str, percent: float):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE tracked_coins SET alert_percent = %s WHERE user_id = %s AND coin_id = %s",
            (percent, user_id, coin_id),
        )


def set_nft_alert_percent(user_id: int, nft_id: str, percent: float):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE tracked_nfts SET alert_percent = %s WHERE user_id = %s AND nft_id = %s",
            (percent, user_id, nft_id),
        )


def log_alert(user_id: int, item_type: str, item_id: str, item_name: str,
              change_percent: float, price: float):
    """item_type: 'coin' или 'nft' — сохраняет запись для истории в разделе «Алерты»."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO alert_log (user_id, item_type, item_id, item_name, change_percent, price, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (user_id, item_type, item_id, item_name, change_percent, price, _now()),
        )


def get_recent_alerts(user_id: int, limit: int = 30) -> list:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT * FROM alert_log WHERE user_id = %s
               ORDER BY created_at DESC LIMIT %s""",
            (user_id, limit),
        )
        return [dict(r) for r in cur.fetchall()]


def log_payment(user_id: int, telegram_charge_id: str, amount_stars: int, days_granted: int) -> bool:
    """
    Сохраняет платёж для аудита. Возвращает False, если такой charge_id
    уже был записан раньше — защита от повторной обработки одного и
    того же платежа.
    """
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM payments WHERE telegram_charge_id = %s",
            (telegram_charge_id,),
        )
        if cur.fetchone():
            return False
        cur.execute(
            """INSERT INTO payments (user_id, telegram_charge_id, amount_stars, days_granted, created_at)
               VALUES (%s, %s, %s, %s, %s)""",
            (user_id, telegram_charge_id, amount_stars, days_granted, _now()),
        )
        return True


def get_user_payments(user_id: int) -> list:
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM payments WHERE user_id = %s ORDER BY created_at DESC",
            (user_id,),
        )
        return [dict(r) for r in cur.fetchall()]


def create_promo_code(code: str, days: int, max_uses: int | None = None) -> bool:
    """Создаёт новый промокод. Возвращает False, если код уже существует."""
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT code FROM promo_codes WHERE code = %s", (code,))
        if cur.fetchone():
            return False
        cur.execute(
            "INSERT INTO promo_codes (code, days, max_uses, created_at) VALUES (%s, %s, %s, %s)",
            (code, days, max_uses, _now()),
        )
        return True


def redeem_promo_code(user_id: int, code: str) -> dict:
    """
    Пытается активировать промокод для юзера.
    Возвращает {"success": bool, "message": str, "days": int|None}.
    """
    code = code.strip().upper()
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM promo_codes WHERE code = %s", (code,))
        promo = cur.fetchone()

        if not promo:
            return {"success": False, "message": "Промокод не найден"}
        if not promo["active"]:
            return {"success": False, "message": "Этот промокод больше не активен"}
        if promo["max_uses"] is not None and promo["used_count"] >= promo["max_uses"]:
            return {"success": False, "message": "Промокод исчерпан"}

        cur.execute(
            "SELECT id FROM promo_redemptions WHERE user_id = %s AND code = %s",
            (user_id, code),
        )
        if cur.fetchone():
            return {"success": False, "message": "Ты уже использовал этот промокод"}

        cur.execute(
            "INSERT INTO promo_redemptions (user_id, code, redeemed_at) VALUES (%s, %s, %s)",
            (user_id, code, _now()),
        )
        cur.execute(
            "UPDATE promo_codes SET used_count = used_count + 1 WHERE code = %s",
            (code,),
        )
        days = promo["days"]

    # grant_premium открывает свою транзакцию — вызываем после закрытия предыдущей
    grant_premium(user_id, days=days)
    return {"success": True, "message": f"Промокод активирован! +{days} дней премиума", "days": days}


def get_stats() -> dict:
    """Сводка для команды /stats: юзеры, премиум, выручка, отслеживания."""
    with get_db() as conn:
        cur = conn.cursor()

        cur.execute("SELECT COUNT(*) as c FROM users")
        total_users = cur.fetchone()["c"]

        cur.execute("SELECT COUNT(*) as c FROM users WHERE is_premium = 1")
        premium_users = cur.fetchone()["c"]

        cur.execute("SELECT COALESCE(SUM(amount_stars), 0) as total, COUNT(*) as c FROM payments")
        revenue_row = cur.fetchone()

        cur.execute("SELECT COUNT(*) as c FROM tracked_coins")
        tracked_coins = cur.fetchone()["c"]

        cur.execute("SELECT COUNT(*) as c FROM tracked_nfts")
        tracked_nfts = cur.fetchone()["c"]

        day_ago = (datetime.now() - timedelta(days=1)).isoformat()
        week_ago = (datetime.now() - timedelta(days=7)).isoformat()

        cur.execute("SELECT COUNT(*) as c FROM users WHERE created_at >= %s", (day_ago,))
        new_today = cur.fetchone()["c"]

        cur.execute("SELECT COUNT(*) as c FROM users WHERE created_at >= %s", (week_ago,))
        new_week = cur.fetchone()["c"]

        cur.execute("SELECT COUNT(*) as c FROM promo_redemptions")
        promo_redemptions = cur.fetchone()["c"]

    return {
        "total_users": total_users,
        "premium_users": premium_users,
        "total_revenue_stars": revenue_row["total"],
        "total_payments": revenue_row["c"],
        "tracked_coins": tracked_coins,
        "tracked_nfts": tracked_nfts,
        "new_users_today": new_today,
        "new_users_week": new_week,
        "promo_redemptions": promo_redemptions,
    }
