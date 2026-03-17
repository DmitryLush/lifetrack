import os
from yookassa import Configuration


def configure_yookassa() -> None:
    """
    Настройка ЮKassa через переменные окружения.
    Должны быть заданы:
    - YOOKASSA_SHOP_ID
    - YOOKASSA_SECRET_KEY
    """
    shop_id = os.getenv("YOOKASSA_SHOP_ID")
    secret_key = os.getenv("YOOKASSA_SECRET_KEY")

    if not shop_id or not secret_key:
        raise RuntimeError("YOOKASSA_SHOP_ID или YOOKASSA_SECRET_KEY не заданы в окружении")

    Configuration.account_id = shop_id
    Configuration.secret_key = secret_key
