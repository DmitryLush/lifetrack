import ipaddress
import logging
import os
import time
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlparse

from flask import Flask, request, jsonify, abort
from dotenv import load_dotenv

from yookassa import Payment
# from flask_cors import CORS

from payment import configure_yookassa
from db import (
    init_db,
    upsert_order,
    update_order_status,
    get_succeeded_orders_by_email,
    set_verification_code,
    get_verification_code,
    delete_verification_code,
    update_verification_last_sent,
)
from mailer import send_email


load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
# CORS(
#     app,
#     resources={r"/*": {"origins": "*"}},
#     allow_headers=["Content-Type"],
#     methods=["GET", "POST", "OPTIONS"]
# )

# Настройка ЮKassa (берёт данные из окружения)
configure_yookassa()

RETURN_URL = os.getenv("RETURN_URL", "https://example.com/payment-success")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")


PRODUCTS = {
    "Недельный планер": {
        "title": "Недельный планер LifeTrack",
        "price": 1090,
        "links": [
            "https://docs.google.com/spreadsheets/d/1yXJcGgcHyfV5KPgMNpIyCXSLJRscOBibyPizDjywPV4/edit?usp=sharing",
        ],
    },
    "Финансовый трекер": {
        "title": "Финансовый трекер LifeTrack",
        "price": 1290,
        "links": [
            "https://docs.google.com/spreadsheets/d/194DHBL6i-cnQGXBlO4yw9tmNravRbh-7Gp80IsyoUfw/edit?usp=sharing",
        ],
    },
    "Трекер привычек": {
        "title": "Трекер привычек LifeTrack",
        "price": 1190,
        "links": [
            "https://docs.google.com/spreadsheets/d/1IMbgXKiGMgU9E36Vw5YhWYkzg1iVZOYdw_vu7AD3A08/edit?usp=sharing",
        ],
    },
    "Трекер задач": {
        "title": "Трекер задач LifeTrack",
        "price": 990,
        "links": [
            "https://docs.google.com/spreadsheets/d/1x7fyrqcysprg1y4Hh8w7IYeBOA67MXnur8tT7JV3qN8/edit?usp=sharing",
        ],
    },
    "Набор LifeTrack (4 планера)": {
        "title": "Набор LifeTrack (4 планера)",
        "price": 1490,
        "links": [
            "https://docs.google.com/spreadsheets/d/1yXJcGgcHyfV5KPgMNpIyCXSLJRscOBibyPizDjywPV4/edit?usp=sharing",
            "https://docs.google.com/spreadsheets/d/194DHBL6i-cnQGXBlO4yw9tmNravRbh-7Gp80IsyoUfw/edit?usp=sharing",
            "https://docs.google.com/spreadsheets/d/1IMbgXKiGMgU9E36Vw5YhWYkzg1iVZOYdw_vu7AD3A08/edit?usp=sharing",
            "https://docs.google.com/spreadsheets/d/1x7fyrqcysprg1y4Hh8w7IYeBOA67MXnur8tT7JV3qN8/edit?usp=sharing",
        ],
    },
}

PRODUCT_NAME_TO_ID = {
    "Недельный планер": "weekly_planner",
    "Финансовый трекер": "finance_tracker",
    "Трекер привычек": "habit_tracker",
    "Трекер задач": "task_tracker",
    "Набор LifeTrack (4 планера)": "bundle_all",
    "Все вместе": "bundle_all",
    "Планер Weekly": "weekly_planner",
    "Планер недели": "weekly_planner",
    "Планер недели LifeStats": "weekly_planner",
    "Финансовый трекер LifeStats": "finance_tracker",
    "Трекер привычек LifeStats": "habit_tracker",
    "Трекер задач LifeStats": "task_tracker",
    "Все вместе LifeStats": "bundle_all",
}

def send_product_links_email(email: str, product_ids: list):
    """
    Отправляет письмо со ссылками на купленные товары.
    """
    if not product_ids:
        return

    links = []
    product_titles = []
    for pid in product_ids:
        product = PRODUCTS.get(pid)
        if product:
            product_titles.append(product["title"])
            links.extend(product.get("links", []))

    # Убираем дубликаты ссылок (на случай, если товары дублируются)
    unique_links = []
    for link in links:
        if link not in unique_links:
            unique_links.append(link)

    if not unique_links:
        return

    # Формируем текст письма
    subject = "Ваши покупки в LifeStats"
    body = f"Здравствуйте!\n\nСпасибо за покупку: {', '.join(product_titles)}.\n\n"
    body += "Ссылки для скачивания:\n"
    for i, link in enumerate(unique_links, 1):
        body += f"{i}. {link}\n"
    body += "\n--\nLifeStats"

    # Отправляем
    try:
        send_email(email, subject, body)
        logger.info(f"Письмо с товарами успешно отправлено на {email}")
    except Exception as e:
        logger.error(f"Ошибка при отправке письма на {email}: {e}")
        # Можно также залогировать полный traceback
        import traceback
        logger.error(traceback.format_exc())

def normalize_product_name(name: str) -> str:
    return " ".join((name or "").strip().split())


def is_valid_return_url(url: str) -> bool:
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)

# Временное хранилище только для rate limiting в памяти
RATE_LIMIT = {}  # key (ip or email) -> [timestamps]

# Подсети ЮKassa из официальной документации
YOOKASSA_NETWORKS = [
    ipaddress.ip_network("2a02:5180::/32"),
    ipaddress.ip_network("77.75.154.128/25"),
    ipaddress.ip_network("77.75.156.35/32"),
    ipaddress.ip_network("77.75.156.11/32"),
    ipaddress.ip_network("77.75.153.0/25"),
    ipaddress.ip_network("185.71.77.0/27"),
    ipaddress.ip_network("185.71.76.0/27"),
]


def rate_limited(key: str, limit: int, per_seconds: int) -> bool:
    """
    Простейший rate-limit в памяти: не более `limit` запросов за `per_seconds`.
    """
    now = time.time()
    entries = RATE_LIMIT.get(key, [])
    # Оставляем только недавние записи
    entries = [ts for ts in entries if now - ts < per_seconds]
    if len(entries) >= limit:
        RATE_LIMIT[key] = entries
        return True
    entries.append(now)
    RATE_LIMIT[key] = entries
    return False


def is_yookassa_ip(ip: str) -> bool:
    """
    Проверка, входит ли IP-адрес в диапазоны ЮKassa.
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False

    for net in YOOKASSA_NETWORKS:
        if addr in net:
            return True
    return False


@app.route("/products", methods=["GET"])
def list_products():
    result = []
    for pid, p in PRODUCTS.items():
        result.append(
            {
                "id": pid,
                "title": p["title"],
                "price": p["price"],
                "currency": "RUB",
            }
        )
    return jsonify(result)

@app.route("/create-payment", methods=["POST", "OPTIONS"])
def create_payment():
    # Обработка CORS preflight
    if request.method == "OPTIONS":
        response = jsonify({"status": "ok"})
        response.status_code = 200
        return response

    data = request.get_json(silent=True) or {}
    raw_product_id = data.get("product_id") or data.get("productId")
    product_name = data.get("productName") or data.get("product_name")
    email = (data.get("email") or "").strip().lower()
    success_url = data.get("successUrl") or data.get("success_url")

    logger.info("DATA: %s", data)

    if not email:
        return jsonify({"error": "email is required"}), 400

    # Определяем реальный ключ товара
    resolved_id = None

    if raw_product_id:
        if raw_product_id in PRODUCTS:
            resolved_id = raw_product_id
        else:
            normalized = normalize_product_name(raw_product_id)
            resolved_id = PRODUCT_NAME_TO_ID.get(normalized)

    if not resolved_id and product_name:
        normalized = normalize_product_name(product_name)
        resolved_id = PRODUCT_NAME_TO_ID.get(normalized)

    if not resolved_id or resolved_id not in PRODUCTS:
        return jsonify({"error": "Unknown product_id"}), 400

    product = PRODUCTS[resolved_id]
    amount = product["price"]
    amount_str = f"{amount:.2f}"

    idempotence_key = str(uuid.uuid4())
    return_url = success_url if is_valid_return_url(success_url) else RETURN_URL

    try:
        payment = Payment.create(
            {
                "amount": {
                    "value": amount_str,
                    "currency": "RUB",
                },
                "confirmation": {
                    "type": "redirect",
                    "return_url": return_url,
                },
                "capture": True,
                "description": f"Покупка: {product['title']}",
                "metadata": {
                    "product_id": resolved_id,
                    "email": email,
                    "product_name": product["title"],
                },
            },
            idempotency_key=idempotence_key,
        )
    except Exception as e:
        logger.exception("Payment creation failed")
        return jsonify({"error": "Payment creation failed", "details": str(e)}), 500

    confirmation_url = payment.confirmation.confirmation_url
    payment_id = payment.id

    upsert_order(
        payment_id=payment_id,
        email=email,
        product_id=resolved_id,
        status="pending"
    )

    return jsonify(
        {
            "payment_id": payment_id,
            "confirmation_url": confirmation_url,
        }
    )


@app.route("/payment-status", methods=["GET"])
def payment_status():
    """
    Проверка статуса платежа и выдача ссылок на продукт.
    Ожидает query-параметр ?payment_id=...
    """
    payment_id = request.args.get("payment_id")
    if not payment_id:
        return jsonify({"error": "payment_id is required"}), 400

    try:
        payment = Payment.find_one(payment_id)
    except Exception:
        return jsonify({"error": "PAYMENT_NOT_FOUND"}), 404

    status = payment.status
    metadata = getattr(payment, "metadata", {}) or {}
    product_id = metadata.get("product_id")

    # Если платеж не успешен — просто возвращаем статус
    if status != "succeeded":
        return jsonify(
            {
                "status": status,
                "product_id": product_id,
            }
        )

    product = PRODUCTS.get(product_id)
    if not product:
        return jsonify({"error": "Unknown product in payment metadata"}), 500

    return jsonify(
        {
            "status": status,
            "product": {
                "id": product_id,
                "title": product["title"],
                "links": product["links"],
            },
        }
    )


@app.route("/auth/send-code", methods=["POST"])
def send_code():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    if not email:
        return jsonify({"error": "EMAIL_REQUIRED"}), 400

    products = get_succeeded_orders_by_email(email)
    if not products:
        return jsonify({"error": "EMAIL_NOT_FOUND"}), 404
    

    # Rate limit по IP
    ip_key = f"send-code-ip:{request.remote_addr}"
    if rate_limited(ip_key, limit=5, per_seconds=60):
        return jsonify({"error": "TOO_MANY_REQUESTS"}), 429

    # Запрещаем повторную отправку раньше, чем через 30 секунд
    now = datetime.utcnow()
    entry = get_verification_code(email)
    if entry and (now - entry["last_sent_at"]).total_seconds() < 30:
        return jsonify({"error": "TOO_MANY_REQUESTS"}), 429

    # Генерируем 4-значный код
    code = str(uuid.uuid4().int % 9000 + 1000)
    set_verification_code(email, code, ttl_seconds=300)

    send_email(
        to_email=email,
        subject="Код подтверждения LifeStats",
        body=f"Ваш код подтверждения: {code}",
    )

    return jsonify({"success": True})


@app.route("/auth/verify-code", methods=["POST"])
def verify_code():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    code = (data.get("code") or "").strip()

    if not email or not code:
        return jsonify({"error": "INVALID_CODE"}), 400

    # Rate limit по IP
    ip_key = f"verify-code-ip:{request.remote_addr}"
    if rate_limited(ip_key, limit=10, per_seconds=60):
        return jsonify({"error": "TOO_MANY_REQUESTS"}), 429

    entry = get_verification_code(email)
    if not entry:
        return jsonify({"error": "INVALID_CODE"}), 400

    now = datetime.utcnow()
    if now > entry["expires_at"]:
        delete_verification_code(email)
        return jsonify({"error": "CODE_EXPIRED"}), 400

    if entry["code"] != code:
        return jsonify({"error": "INVALID_CODE"}), 400

    # Код верен, удаляем
    delete_verification_code(email)

    # Находим все купленные товары
    product_ids = get_succeeded_orders_by_email(email)
    links = []
    for pid in product_ids:
        product = PRODUCTS.get(pid)
        if product:
            links.extend(product.get("links", []))

     # Убираем дубликаты и сохраняем порядок
    unique_links = []
    for link in links:
        if link not in unique_links:
            unique_links.append(link)

    # ВОЗВРАЩАЕМ ТОЛЬКО МАССИВ (список) ССЫЛОК
    return jsonify(unique_links)


@app.route("/yookassa/webhook", methods=["POST"])
def yookassa_webhook():
    # Простая защита по секрету (?secret=...)
    secret = request.args.get("secret")
    if secret != WEBHOOK_SECRET:
        abort(403)

    remote_ip = request.headers.get("X-Forwarded-For", request.remote_addr or "")
    # В X-Forwarded-For может быть список IP, берём первый
    remote_ip = remote_ip.split(",")[0].strip()
    if not is_yookassa_ip(remote_ip):
        logger.warning("Rejected webhook from non-YooKassa IP: %s", remote_ip)
        abort(403)

    event_data = request.get_json(silent=True) or {}
    event = event_data.get("event")
    obj = event_data.get("object", {})

    if event == "payment.succeeded":
        payment_id = obj.get("id")
        metadata = obj.get("metadata", {}) or {}
        product_id = metadata.get("product_id")
        email = (metadata.get("email") or "").strip().lower()
        amount = obj.get("amount", {}).get("value")

        logger.info(
            "[YooKassa] Payment succeeded: %s, product=%s, amount=%s, email=%s",
            payment_id,
            product_id,
            amount,
            email,
        )

        if payment_id and product_id and email:
            upsert_order(payment_id=payment_id, email=email, product_id=product_id, status="succeeded")
            # ОТПРАВЛЯЕМ ПИСЬМО С ТОВАРОМ (можно передать список из одного product_id)
            send_product_links_email(email, [product_id])

    elif event == "payment.canceled":
        payment_id = obj.get("id")
        logger.info("[YooKassa] Payment canceled: %s", payment_id)
        if payment_id:
            update_order_status(payment_id, "canceled")

    return jsonify({"status": "ok"})


if __name__ == "__main__":
    # Инициализируем базу перед запуском
    init_db()

    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

