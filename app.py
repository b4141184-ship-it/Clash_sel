
import os
import sqlite3
import secrets
import hashlib
import re
import json
from pathlib import Path
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, jsonify, send_from_directory
)
from werkzeug.utils import secure_filename

BASE = Path(__file__).resolve().parent
UPLOADS = BASE / "uploads"
RECEIPTS = BASE / "private_receipts"

UPLOADS.mkdir(exist_ok=True)
RECEIPTS.mkdir(exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_SAMESITE="Lax"
)

app.permanent_session_lifetime = timedelta(hours=8)

DB = BASE / "shop.db"
ALLOWED = {"png", "jpg", "jpeg", "webp"}

# شماره‌های مجاز مدیر
ADMIN_PHONES = {
    "09050413871",
    "09045913823",
    "09303835459"
}


def connect():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def init():
    with connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS admins(
            id INTEGER PRIMARY KEY,
            username TEXT UNIQUE,
            password TEXT
        );

        CREATE TABLE IF NOT EXISTS products(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            description TEXT,
            price INTEGER,
            image TEXT
        );

        CREATE TABLE IF NOT EXISTS orders(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer TEXT,
            phone TEXT,
            product TEXT,
            amount INTEGER,
            receipt TEXT,
            status TEXT,
            created TEXT
        );

        CREATE TABLE IF NOT EXISTS admin_totp(
            phone TEXT PRIMARY KEY,
            secret TEXT NOT NULL,
            attempts INTEGER DEFAULT 0,
            locked_until INTEGER DEFAULT 0
        );
        """)

        if not c.execute(
            "SELECT id FROM products LIMIT 1"
        ).fetchone():
            c.execute(
                """INSERT INTO products
                (title,description,price,image)
                VALUES(?,?,?,?)""",
                (
                    "نمونه محصول",
                    "این محصول نمونه را از پنل مدیریت تغییر بده.",
                    300000,
                    ""
                )
            )


def admin_only(fn):
    @wraps(fn)
    def inner(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return inner


def image_ok(file):
    return (
        file
        and file.filename
        and "." in file.filename
        and file.filename.rsplit(".", 1)[1].lower() in ALLOWED
    )


def rubika_notice(order_data):
    hook = os.getenv("RUBIKA_WEBHOOK", "").strip()

    if not hook:
        return

    try:
        import requests
        requests.post(
            hook,
            json={
                "text": (
                    f"سفارش #{order_data['id']} | "
                    f"{order_data['product']} | "
                    f"{order_data['amount']} تومان | "
                    f"{order_data['customer']}"
                )
            },
            timeout=10
        )
    except Exception:
        app.logger.exception("Rubika notification failed")


@app.route("/")
def home():
    with connect() as c:
        products = c.execute(
            "SELECT * FROM products ORDER BY id DESC"
        ).fetchall()

    return render_template("home.html", products=products)


@app.route("/uploads/<path:name>")
def upload_file(name):
    return send_from_directory(UPLOADS, name)


@app.route("/order", methods=["POST"])
def order():
    name = request.form.get("customer", "").strip()
    phone = request.form.get("phone", "").strip()
    pid = request.form.get("product_id", type=int)
    receipt = request.files.get("receipt")

    if not name or not phone or not pid or not image_ok(receipt):
        flash("اطلاعات سفارش یا تصویر رسید معتبر نیست.", "error")
        return redirect(url_for("home"))

    with connect() as c:
        product = c.execute(
            "SELECT * FROM products WHERE id=?",
            (pid,)
        ).fetchone()

        if not product:
            flash("محصول پیدا نشد.", "error")
            return redirect(url_for("home"))

        ext = receipt.filename.rsplit(".", 1)[1].lower()
        filename = secrets.token_hex(16) + "." + ext
        receipt.save(RECEIPTS / filename)

        cur = c.execute(
            """INSERT INTO orders
            (customer,phone,product,amount,receipt,status,created)
            VALUES(?,?,?,?,?,?,?)""",
            (
                name,
                phone,
                product["title"],
                product["price"],
                filename,
                "در انتظار بررسی",
                datetime.now().strftime("%Y-%m-%d %H:%M")
            )
        )

        oid = cur.lastrowid

        data = {
            "id": oid,
            "product": product["title"],
            "amount": product["price"],
            "customer": name
        }

    rubika_notice(data)
    return render_template("success.html", oid=oid)


@app.route("/track", methods=["GET", "POST"])
def track():
    result = None

    if request.method == "POST":
        oid = request.form.get("oid", type=int)
        phone = request.form.get("phone", "").strip()

        if oid and phone:
            with connect() as c:
                result = c.execute(
                    """SELECT id,product,status
                    FROM orders WHERE id=? AND phone=?""",
                    (oid, phone)
                ).fetchone()

        if not result:
            flash("سفارش پیدا نشد؛ کد و شماره را بررسی کن.", "error")

    return render_template("track.html", result=result)


# ورود مدیر با Google Authenticator
@app.route("/admin/login", methods=["GET", "POST"])
@app.route("/login", methods=["GET", "POST"])
def login():

    if session.get("admin"):
        return redirect(url_for("admin"))

    setup_secret = None

    if request.method == "POST":

        action = request.form.get("action", "")

        phone = request.form.get("phone", "").strip()

        if phone not in ADMIN_PHONES:
            flash("این شماره اجازه ورود ندارد.", "error")
            return render_template(
                "login.html",
                setup_secret=None
            )

        # ثبت اولیه مدیر در Authenticator
        if action == "setup":

            setup_key = os.getenv("ADMIN_SETUP_KEY", "").strip()
            entered_key = request.form.get(
                "setup_key", ""
            ).strip()

            if not setup_key:
                flash(
                    "کلید راه‌اندازی در تنظیمات سرور تعریف نشده است.",
                    "error"
                )
                return render_template(
                    "login.html",
                    setup_secret=None
                )

            if not secrets.compare_digest(
                setup_key, entered_key
            ):
                flash("کلید راه‌اندازی اشتباه است.", "error")
                return render_template(
                    "login.html",
                    setup_secret=None
                )

            with connect() as c:
                exists = c.execute(
                    "SELECT phone FROM admin_totp WHERE phone=?",
                    (phone,)
                ).fetchone()

                if exists:
                    flash(
                        "این مدیر قبلاً راه‌اندازی شده است.",
                        "error"
                    )
                    return render_template(
                        "login.html",
                        setup_secret=None
                    )

                import pyotp

                new_secret = pyotp.random_base32()

                c.execute(
                    """INSERT INTO admin_totp
                    (phone,secret,attempts,locked_until)
                    VALUES(?,?,0,0)""",
                    (phone, new_secret)
                )

            setup_secret = new_secret

            return render_template(
                "login.html",
                setup_secret=setup_secret,
                setup_phone=phone
            )

        # بررسی کد شش رقمی
        elif action == "verify":

            code = request.form.get("code", "").strip()

            if not re.fullmatch(r"\d{6}", code):
                flash("کد باید دقیقاً ۶ رقم باشد.", "error")
                return render_template(
                    "login.html",
                    setup_secret=None
                )

            with connect() as c:
                row = c.execute(
                    "SELECT * FROM admin_totp WHERE phone=?",
                    (phone,)
                ).fetchone()

                if not row:
                    flash(
                        "ابتدا باید حساب خود را راه‌اندازی کنی.",
                        "error"
                    )
                    return render_template(
                        "login.html",
                        setup_secret=None
                    )

                now = int(datetime.now().timestamp())

                if now < row["locked_until"]:
                    flash(
                        "به دلیل تلاش ناموفق، موقتاً قفل شده است.",
                        "error"
                    )
                    return render_template(
                        "login.html",
                        setup_secret=None
                    )

                import pyotp

                totp = pyotp.TOTP(row["secret"])

                valid = totp.verify(
                    code,
                    valid_window=1
                )

                if not valid:
                    attempts = row["attempts"] + 1
                    locked_until = 0

                    if attempts >= 5:
                        attempts = 0
                        locked_until = now + 300

                    c.execute(
                        """UPDATE admin_totp
                        SET attempts=?, locked_until=?
                        WHERE phone=?""",
                        (attempts, locked_until, phone)
                    )

                    flash(
                        "کد اشتباه است یا اعتبار آن تمام شده.",
                        "error"
                    )

                    return render_template(
                        "login.html",
                        setup_secret=None
                    )

                c.execute(
                    """UPDATE admin_totp
                    SET attempts=0, locked_until=0
                    WHERE phone=?""",
                    (phone,)
                )

            session.clear()
            session.permanent = True
            session["admin"] = True
            session["admin_phone"] = phone

            return redirect(url_for("admin"))

    return render_template(
        "login.html",
        setup_secret=None
    )


@app.route("/admin/logout")
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


@app.route("/admin")
@admin_only
def admin():
    with connect() as c:
        products = c.execute(
            "SELECT * FROM products ORDER BY id DESC"
        ).fetchall()

        orders = c.execute(
            "SELECT * FROM orders ORDER BY id DESC"
        ).fetchall()

    return render_template(
        "admin.html",
        products=products,
        orders=orders
    )


@app.route("/admin/receipt/<int:oid>")
@admin_only
def view_receipt(oid):
    with connect() as c:
        order = c.execute(
            "SELECT receipt FROM orders WHERE id=?",
            (oid,)
        ).fetchone()

    if not order or not order["receipt"]:
        return "رسید پیدا نشد.", 404

    return send_from_directory(
        RECEIPTS,
        order["receipt"]
    )


@app.route("/admin/product", methods=["POST"])
@app.route("/admin/add", methods=["POST"])
@admin_only
def add_product():
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    price = request.form.get("price", type=int)
    image = request.files.get("image")

    if not title or not price or price < 1:
        flash("عنوان و قیمت معتبر وارد کن.", "error")
        return redirect(url_for("admin"))

    filename = ""

    if image and image.filename:
        if not image_ok(image):
            flash("فرمت تصویر پشتیبانی نمی‌شود.", "error")
            return redirect(url_for("admin"))

        ext = image.filename.rsplit(".", 1)[1].lower()
        filename = secrets.token_hex(16) + "." + ext
        image.save(UPLOADS / filename)

    with connect() as c:
        c.execute(
            """INSERT INTO products
            (title,description,price,image)
            VALUES(?,?,?,?)""",
            (title, description, price, filename)
        )

    flash("محصول اضافه شد.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/product/<int:pid>/delete", methods=["POST"])
@app.route("/admin/delete/<int:pid>", methods=["POST"])
@admin_only
def delete_product(pid):
    with connect() as c:
        c.execute(
            "DELETE FROM products WHERE id=?",
            (pid,)
        )

    return redirect(url_for("admin"))


@app.route("/admin/order/<int:oid>", methods=["POST"])
@app.route("/admin/status/<int:oid>", methods=["POST"])
@admin_only
def set_status(oid):
    status = request.form.get("status")

    allowed_statuses = {
        "در انتظار بررسی",
        "تأیید شده",
        "تأیید شد",
        "رد شده",
        "رد شد",
        "تکمیل شده",
        "تکمیل شد"
    }

    if status in allowed_statuses:
        with connect() as c:
            c.execute(
                "UPDATE orders SET status=? WHERE id=?",
                (status, oid)
            )

    return redirect(url_for("admin"))


@app.route("/health")
def health():
    return jsonify(ok=True)


init()

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", 5000))
    )
