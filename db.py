import os
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

import psycopg2
from psycopg2.extras import RealDictCursor

# Используем полный URL базы данных, который предоставил Neon
DATABASE_URL = os.environ.get("STORAGE_DATABASE_URL_UNPOOLED")
if not DATABASE_URL:
    raise ValueError("STORAGE_DATABASE_URL_UNPOOLED not set in environment variables")

def get_db_connection():
    """Возвращает соединение с PostgreSQL (Neon)."""
    return psycopg2.connect(DATABASE_URL, sslmode="require", cursor_factory=RealDictCursor)

def init_db() -> None:
    """Создаёт таблицы, если их нет."""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            # Таблица заказов
            cur.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id SERIAL PRIMARY KEY,
                    payment_id TEXT UNIQUE,
                    email TEXT NOT NULL,
                    product_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_orders_email ON orders (email)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_orders_payment_id ON orders (payment_id)")

            # Таблица для кодов подтверждения
            cur.execute("""
                CREATE TABLE IF NOT EXISTS verification_codes (
                    email TEXT PRIMARY KEY,
                    code TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL,
                    expires_at TIMESTAMP NOT NULL,
                    last_sent_at TIMESTAMP NOT NULL
                )
            """)
        conn.commit()

def upsert_order(payment_id: str, email: str, product_id: str, status: str) -> None:
    """Вставить или обновить заказ."""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO orders (payment_id, email, product_id, status, created_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (payment_id) DO UPDATE SET
                    email = EXCLUDED.email,
                    product_id = EXCLUDED.product_id,
                    status = EXCLUDED.status
            """, (payment_id, email, product_id, status, datetime.utcnow()))
        conn.commit()

def update_order_status(payment_id: str, status: str) -> None:
    """Обновить статус заказа."""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE orders SET status = %s WHERE payment_id = %s", (status, payment_id))
        conn.commit()

def get_succeeded_orders_by_email(email: str) -> List[str]:
    """Вернуть список product_id для всех успешных заказов пользователя."""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT product_id FROM orders
                WHERE email = %s AND status = 'succeeded'
            """, (email,))
            rows = cur.fetchall()
    return [row["product_id"] for row in rows]

def set_verification_code(email: str, code: str, ttl_seconds: int = 300) -> None:
    """
    Сохранить или обновить код подтверждения для email.
    ttl_seconds — время жизни кода (по умолчанию 5 минут).
    """
    now = datetime.utcnow()
    expires_at = now + timedelta(seconds=ttl_seconds)

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO verification_codes (email, code, created_at, expires_at, last_sent_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (email) DO UPDATE SET
                    code = EXCLUDED.code,
                    created_at = EXCLUDED.created_at,
                    expires_at = EXCLUDED.expires_at,
                    last_sent_at = EXCLUDED.last_sent_at
            """, (email, code, now, expires_at, now))
        conn.commit()

def get_verification_code(email: str) -> Optional[Dict[str, Any]]:
    """Получить данные кода подтверждения для email."""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT code, created_at, expires_at, last_sent_at
                FROM verification_codes
                WHERE email = %s
            """, (email,))
            row = cur.fetchone()
    if not row:
        return None
    # psycopg2 возвращает поля TIMESTAMP как datetime, преобразовывать не нужно
    return {
        "code": row["code"],
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
        "last_sent_at": row["last_sent_at"],
    }

def update_verification_last_sent(email: str) -> None:
    """Обновить время последней отправки кода."""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE verification_codes SET last_sent_at = %s WHERE email = %s",
                        (datetime.utcnow(), email))
        conn.commit()

def delete_verification_code(email: str) -> None:
    """Удалить код подтверждения."""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM verification_codes WHERE email = %s", (email,))
        conn.commit()
