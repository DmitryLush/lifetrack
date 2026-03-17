import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

DB_PATH = os.getenv("ORDERS_DB_PATH", os.path.join(os.path.dirname(__file__), "orders.db"))


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payment_id TEXT UNIQUE,
                email TEXT,
                product_id TEXT,
                status TEXT,
                created_at TEXT
            )
            """
        )
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_email ON orders (email)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_payment_id ON orders (payment_id)")

        # Временное хранилище кодов подтверждения
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS verification_codes (
                email TEXT PRIMARY KEY,
                code TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                last_sent_at TEXT NOT NULL
            )
            """
        )


def upsert_order(payment_id: str, email: str, product_id: str, status: str) -> None:
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO orders (payment_id, email, product_id, status, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(payment_id) DO UPDATE SET
                email=excluded.email,
                product_id=excluded.product_id,
                status=excluded.status
            """,
            (payment_id, email, product_id, status, now),
        )


def update_order_status(payment_id: str, status: str) -> None:
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE orders SET status = ? WHERE payment_id = ?",
            (status, payment_id),
        )


def get_succeeded_orders_by_email(email: str):

    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT product_id FROM orders WHERE email = ? AND status = 'succeeded'",
            (email,),
        )
        rows = cursor.fetchall()

    return [row[0] for row in rows]


def set_verification_code(email: str, code: str, ttl_seconds: int = 300) -> None:
    """
    Сохранить или обновить код подтверждения для email.
    ttl_seconds — время жизни кода (по умолчанию 5 минут).
    """
    now = datetime.utcnow()
    created_at = now.isoformat()
    expires_at = (now.replace(microsecond=0) + timedelta(seconds=ttl_seconds)).isoformat()
    last_sent_at = created_at

    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO verification_codes (email, code, created_at, expires_at, last_sent_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET
                code=excluded.code,
                created_at=excluded.created_at,
                expires_at=excluded.expires_at,
                last_sent_at=excluded.last_sent_at
            """,
            (email, code, created_at, expires_at, last_sent_at),
        )


def get_verification_code(email: str):
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT code, created_at, expires_at, last_sent_at
            FROM verification_codes
            WHERE email = ?
            """,
            (email,),
        )
        row = cursor.fetchone()

    if not row:
        return None

    code, created_at, expires_at, last_sent_at = row
    return {
        "code": code,
        "created_at": datetime.fromisoformat(created_at),
        "expires_at": datetime.fromisoformat(expires_at),
        "last_sent_at": datetime.fromisoformat(last_sent_at),
    }


def update_verification_last_sent(email: str) -> None:
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE verification_codes SET last_sent_at = ? WHERE email = ?",
            (now, email),
        )


def delete_verification_code(email: str) -> None:
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM verification_codes WHERE email = ?", (email,))

