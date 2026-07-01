from flask import Flask, request, jsonify
from flask_cors import CORS
import sqlite3
import os
import json
import urllib.parse

app = Flask(__name__)
CORS(app)

DB_NAME = "p2p.db"


def get_conn():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id    INTEGER PRIMARY KEY,
            username   TEXT,
            first_name TEXT,
            balance    REAL DEFAULT 100.0,
            escrow     REAL DEFAULT 0.0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            seller_id      INTEGER,
            buyer_id       INTEGER,
            seller_name    TEXT,
            amount         REAL,
            price          REAL,
            min_limit      REAL DEFAULT 0,
            max_limit      REAL DEFAULT 999999,
            status         TEXT DEFAULT 'open',
            payment_method TEXT DEFAULT 'BANK',
            currency_pair  TEXT DEFAULT 'USDT/ETB',
            created_at     DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ratings (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id   INTEGER,
            rater_id   INTEGER,
            rated_id   INTEGER,
            stars      INTEGER,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fees (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id   INTEGER,
            user_id    INTEGER,
            amount     REAL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def get_user_from_init(init_data):
    try:
        for item in init_data.split("&"):
            if item.startswith("user="):
                user_json = urllib.parse.unquote(item[5:])
                return json.loads(user_json)
    except Exception:
        pass
    return None


@app.route("/")
def home():
    return jsonify({"status": "EthioP2P API running ✅"})


@app.route("/api/user", methods=["POST"])
def get_or_create_user():
    data = request.json
    init_data = data.get("init_data", "")
    user_info = get_user_from_init(init_data)

    if not user_info:
        user_info = {"id": 999, "first_name": "Test", "username": "test"}

    user_id = user_info["id"]
    conn = get_conn()
    conn.execute("""
        INSERT OR IGNORE INTO users (user_id, username, first_name)
        VALUES (?, ?, ?)
    """, (user_id, user_info.get("username", ""), user_info.get("first_name", "")))
    conn.commit()

    user = conn.execute(
        "SELECT * FROM users WHERE user_id=?", (user_id,)
    ).fetchone()
    conn.close()
    return jsonify(dict(user))


@app.route("/api/orders", methods=["GET"])
def get_orders():
    status = request.args.get("status", "open")
    conn = get_conn()
    rows = conn.execute("""
        SELECT * FROM orders WHERE status=?
        ORDER BY id DESC
    """, (status,)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/orders/create", methods=["POST"])
def create_order():
    data = request.json
    init_data = data.get("init_data", "")
    user_info = get_user_from_init(init_data)

    if not user_info:
        return jsonify({"error": "Invalid user"}), 401

    user_id = user_info["id"]
    first_name = user_info.get("first_name", "User")
    amount = float(data.get("amount", 0))
    price = float(data.get("price", 0))
    min_limit = float(data.get("min_limit", 0))
    max_limit = float(data.get("max_limit", 999999))
    payment = data.get("payment_method", "BANK")
    pair = data.get("currency_pair", "USDT/ETB")

    if amount <= 0 or price <= 0:
        return jsonify({"error": "Invalid amount or price"}), 400

    conn = get_conn()
    user = conn.execute(
        "SELECT balance FROM users WHERE user_id=?", (user_id,)
    ).fetchone()

    if not user or user["balance"] < amount:
        conn.close()
        return jsonify({"error": "Insufficient balance"}), 400

    cursor = conn.execute("""
        INSERT INTO orders
        (seller_id, seller_name, amount, price, min_limit, max_limit, payment_method, currency_pair)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, first_name, amount, price, min_limit, max_limit, payment, pair))
    conn.commit()
    order_id = cursor.lastrowid
    conn.close()
    return jsonify({"success": True, "order_id": order_id})


@app.route("/api/orders/buy", methods=["POST"])
def buy_order():
    data = request.json
    init_data = data.get("init_data", "")
    user_info = get_user_from_init(init_data)

    if not user_info:
        return jsonify({"error": "Invalid user"}), 401

    user_id = user_info["id"]
    order_id = int(data.get("order_id", 0))

    conn = get_conn()
    order = conn.execute(
        "SELECT * FROM orders WHERE id=?", (order_id,)
    ).fetchone()

    if not order:
        conn.close()
        return jsonify({"error": "Order not found"}), 404

    if order["status"] != "open":
        conn.close()
        return jsonify({"error": "Order not available"}), 400

    if order["seller_id"] == user_id:
        conn.close()
        return jsonify({"error": "Cannot buy your own order"}), 400

    cost = order["amount"] * order["price"]
    buyer = conn.execute(
        "SELECT balance FROM users WHERE user_id=?", (user_id,)
    ).fetchone()

    if not buyer or buyer["balance"] < cost:
        conn.close()
        return jsonify({"error": "Insufficient balance"}), 400

    conn.execute(
        "UPDATE users SET balance=balance-?, escrow=escrow+? WHERE user_id=?",
        (cost, cost, user_id)
    )
    conn.execute(
        "UPDATE orders SET status='locked', buyer_id=? WHERE id=?",
        (user_id, order_id)
    )
    conn.commit()
    conn.close()
    return jsonify({"success": True, "cost": cost})


@app.route("/api/orders/release", methods=["POST"])
def release_order():
    data = request.json
    init_data = data.get("init_data", "")
    user_info = get_user_from_init(init_data)

    if not user_info:
        return jsonify({"error": "Invalid user"}), 401

    user_id = user_info["id"]
    order_id = int(data.get("order_id", 0))

    conn = get_conn()
    order = conn.execute(
        "SELECT * FROM orders WHERE id=?", (order_id,)
    ).fetchone()

    if not order or order["seller_id"] != user_id:
        conn.close()
        return jsonify({"error": "Unauthorized"}), 401

    if order["status"] != "locked":
        conn.close()
        return jsonify({"error": "Order not locked"}), 400

    total = order["amount"] * order["price"]
    fee = round(total * 0.001, 4)
    seller_receives = round(total - fee, 4)

    conn.execute(
        "UPDATE users SET escrow=escrow-?, balance=balance+? WHERE user_id=?",
        (total, seller_receives, user_id)
    )
    conn.execute(
        "UPDATE orders SET status='completed' WHERE id=?", (order_id,)
    )
    conn.execute(
        "INSERT INTO fees (order_id, user_id, amount) VALUES (?, ?, ?)",
        (order_id, user_id, fee)
    )
    conn.commit()
    conn.close()
    return jsonify({"success": True, "received": seller_receives, "fee": fee})


@app.route("/api/user/trades", methods=["POST"])
def user_trades():
    data = request.json
    init_data = data.get("init_data", "")
    user_info = get_user_from_init(init_data)

    if not user_info:
        return jsonify({"error": "Invalid user"}), 401

    user_id = user_info["id"]
    conn = get_conn()
    rows = conn.execute("""
        SELECT * FROM orders
        WHERE seller_id=? OR buyer_id=?
        ORDER BY id DESC LIMIT 20
    """, (user_id, user_id)).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/stats", methods=["GET"])
def get_stats():
    conn = get_conn()
    total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    total_orders = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    completed = conn.execute(
        "SELECT COUNT(*) FROM orders WHERE status='completed'"
    ).fetchone()[0]
    total_fees = conn.execute(
        "SELECT SUM(amount) FROM fees"
    ).fetchone()[0] or 0
    conn.close()
    return jsonify({
        "total_users": total_users,
        "total_orders": total_orders,
        "completed": completed,
        "total_fees": round(total_fees, 4)
    })


init_db()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)