"""
Работа с базой данных SQLite.
Хранит: пользователей, их отслеживаемые монеты, статус премиума.
"""
import sqlite3
from datetime import datetime, timedelta
from contextlib import contextmanager

DB_PATH = "crypto_bot.db"


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    """Создаёт таблицы, если их ещё нет. Вызывать один раз при старте."""
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                is_premium INTEGER DEFAULT 0,
                premium_until TEXT,
                default_alert_percent REAL DEFAULT 5.0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tracked_coins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                coin_id TEXT NOT NULL,
                alert_percent REAL DEFAULT 5.0,
                last_price REAL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                UNIQUE(user_id, coin_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tracked_nfts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                nft_id TEXT NOT NULL,
                alert_percent REAL DEFAULT 5.0,
                last_floor_price REAL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                UNIQUE(user_id, nft_id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alert_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                item_type TEXT NOT NULL,
                item_id TEXT NOT NULL,
                item_name TEXT,
                change_percent REAL,
                price REAL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
        """)


def get_or_create_user(user_id: int, username: str = None) -> sqlite3.Row:
    with get_db() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if user is None:
            conn.execute(
                "INSERT INTO users (user_id, username) VALUES (?, ?)",
                (user_id, username),
            )
            user = conn.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
        return dict(user)


def is_premium(user_id: int) -> bool:
    with get_db() as conn:
        user = conn.execute(
            "SELECT is_premium, premium_until FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if not user:
            return False
        if not user["is_premium"]:
            return False
        if user["premium_until"]:
            until = datetime.fromisoformat(user["premium_until"])
            if until < datetime.now():
                # премиум истёк — сбрасываем
                conn.execute(
                    "UPDATE users SET is_premium = 0 WHERE user_id = ?",
                    (user_id,),
                )
                return False
        return True


def grant_premium(user_id: int, days: int = 30):
    until = (datetime.now() + timedelta(days=days)).isoformat()
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET is_premium = 1, premium_until = ? WHERE user_id = ?",
            (until, user_id),
        )


def get_tracked_coins(user_id: int) -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM tracked_coins WHERE user_id = ?", (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def add_tracked_coin(user_id: int, coin_id: str, alert_percent: float = 5.0) -> bool:
    """Возвращает False, если у юзера уже лимит монет (для не-премиум)."""
    FREE_LIMIT = 3
    with get_db() as conn:
        if not is_premium(user_id):
            count = conn.execute(
                "SELECT COUNT(*) as c FROM tracked_coins WHERE user_id = ?",
                (user_id,),
            ).fetchone()["c"]
            if count >= FREE_LIMIT:
                return False
        conn.execute(
            """INSERT OR IGNORE INTO tracked_coins (user_id, coin_id, alert_percent)
               VALUES (?, ?, ?)""",
            (user_id, coin_id, alert_percent),
        )
        return True


def remove_tracked_coin(user_id: int, coin_id: str):
    with get_db() as conn:
        conn.execute(
            "DELETE FROM tracked_coins WHERE user_id = ? AND coin_id = ?",
            (user_id, coin_id),
        )


def update_last_price(user_id: int, coin_id: str, price: float):
    with get_db() as conn:
        conn.execute(
            "UPDATE tracked_coins SET last_price = ? WHERE user_id = ? AND coin_id = ?",
            (price, user_id, coin_id),
        )


def get_all_tracked_coins() -> list:
    """Для фоновой задачи проверки цен — все записи всех юзеров."""
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM tracked_coins").fetchall()
        return [dict(r) for r in rows]


def get_tracked_nfts(user_id: int) -> list:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM tracked_nfts WHERE user_id = ?", (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def add_tracked_nft(user_id: int, nft_id: str, alert_percent: float = 5.0) -> bool:
    """Возвращает False, если у юзера уже лимит коллекций (для не-премиум)."""
    FREE_LIMIT = 3
    with get_db() as conn:
        if not is_premium(user_id):
            count = conn.execute(
                "SELECT COUNT(*) as c FROM tracked_nfts WHERE user_id = ?",
                (user_id,),
            ).fetchone()["c"]
            if count >= FREE_LIMIT:
                return False
        conn.execute(
            """INSERT OR IGNORE INTO tracked_nfts (user_id, nft_id, alert_percent)
               VALUES (?, ?, ?)""",
            (user_id, nft_id, alert_percent),
        )
        return True


def remove_tracked_nft(user_id: int, nft_id: str):
    with get_db() as conn:
        conn.execute(
            "DELETE FROM tracked_nfts WHERE user_id = ? AND nft_id = ?",
            (user_id, nft_id),
        )


def update_last_nft_price(user_id: int, nft_id: str, floor_price: float):
    with get_db() as conn:
        conn.execute(
            "UPDATE tracked_nfts SET last_floor_price = ? WHERE user_id = ? AND nft_id = ?",
            (floor_price, user_id, nft_id),
        )


def get_all_tracked_nfts() -> list:
    """Для фоновой задачи проверки цен — все записи всех юзеров."""
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM tracked_nfts").fetchall()
        return [dict(r) for r in rows]


def get_default_alert_percent(user_id: int) -> float:
    with get_db() as conn:
        row = conn.execute(
            "SELECT default_alert_percent FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return row["default_alert_percent"] if row else 5.0


def set_default_alert_percent(user_id: int, percent: float):
    with get_db() as conn:
        conn.execute(
            "UPDATE users SET default_alert_percent = ? WHERE user_id = ?",
            (percent, user_id),
        )


def set_coin_alert_percent(user_id: int, coin_id: str, percent: float):
    with get_db() as conn:
        conn.execute(
            "UPDATE tracked_coins SET alert_percent = ? WHERE user_id = ? AND coin_id = ?",
            (percent, user_id, coin_id),
        )


def set_nft_alert_percent(user_id: int, nft_id: str, percent: float):
    with get_db() as conn:
        conn.execute(
            "UPDATE tracked_nfts SET alert_percent = ? WHERE user_id = ? AND nft_id = ?",
            (percent, user_id, nft_id),
        )


def log_alert(user_id: int, item_type: str, item_id: str, item_name: str,
              change_percent: float, price: float):
    """item_type: 'coin' или 'nft' — сохраняет запись для истории в разделе «Алерты»."""
    with get_db() as conn:
        conn.execute(
            """INSERT INTO alert_log (user_id, item_type, item_id, item_name, change_percent, price)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, item_type, item_id, item_name, change_percent, price),
        )


def get_recent_alerts(user_id: int, limit: int = 30) -> list:
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM alert_log WHERE user_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
