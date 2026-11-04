
import os
import sqlite3
import secrets
from pathlib import Path
from datetime import datetime
from functools import wraps

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, jsonify, send_from_directory
)
from werkzeug.security import generate_password_hash, check_password_hash


# تنظیمات اصلی
BASE = Path(__file__).resolve().parent
UPLOADS = BASE / "uploads"
RECEIPTS = BASE / "private_receipts"

UPLOADS.mkdir(exist_ok=True)
RECEIPTS.mkdir(exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024

DB = BASE / "shop.db"
ALLOWED = {"png", "jpg", "jpeg", "webp"}


# اتصال دیتابیس
def connect():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


# ساخت دیتابیس و اطلاعات اولیه
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
        """)

        admin_exists = c.execute(
            "SELECT id FROM admins LIMIT 1"
        ).fetchone()

        username = os.getenv("ADMIN_USERNAME", "").strip()
        password = os.getenv("ADMIN_PASSWORD", "")

        if not admin_exists and username and password:
            c.execute(
                "INSERT INTO admins(username,password) VALUES(?,?)",
                (username, generate_password_hash(password))
            )

        if not c.execute(
            "SELECT id FROM products LIMIT 1"
        ).fetchone():
            c.execute(
                """INSERT INTO products(title,description,price,image)
                VALUES(?,?,?,?)""",
                (
                    "نمونه محصول",
                    "این محصول نمونه را از پنل مدیریت تغییر بده.",
                    300000,
                    ""
                )
            )


# دسترسی مدیر
def admin_only(fn):
    @wraps(fn)
    def inner(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("login"))
        return fn(*args, **kwargs)
    return inner


# بررسی پسوند تصویر
def image_ok(file):
    return (
        file
        and file.filename
        and "." in file.filename
        and file.filename.rsplit(".", 1)[1].lower() in ALLOWED
    )


# اطلاع‌رسانی اختیاری روبیکا
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


# صفحه اصلی
@app.route("/")
def home():
    with connect() as c:
        products = c.execute(
            "SELECT * FROM products ORDER BY id DESC"
        ).fetchall()

    return render_template("home.html", products=products)


# نمایش تصاویر محصولات
@app.route("/uploads/<path:name>")
def upload_file(name):
    return send_from_directory(UPLOADS, name)


# ثبت سفارش
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

        # ذخیره رسید در پوشه خصوصی
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


# پیگیری سفارش
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


# ورود مدیر
@app.route("/admin/login", methods=["GET", "POST"])
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        with connect() as c:
            admin = c.execute(
                "SELECT * FROM admins WHERE username=?",
                (username,)
            ).fetchone()

        if admin and check_password_hash(admin["password"], password):
            session.clear()
            session["admin"] = admin["id"]
            return redirect(url_for("admin"))

        flash("نام کاربری یا رمز عبور اشتباه است.", "error")

    return render_template("login.html")


# خروج مدیر
@app.route("/admin/logout")
@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


# پنل مدیریت
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


# نمایش امن رسید فقط برای مدیر
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


# افزودن محصول
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
            """INSERT INTO products(title,description,price,image)
            VALUES(?,?,?,?)""",
            (title, description, price, filename)
        )

    flash("محصول اضافه شد.", "success")
    return redirect(url_for("admin"))


# حذف محصول
@app.route("/admin/product/<int:pid>/delete", methods=["POST"])
@app.route("/admin/delete/<int:pid>", methods=["POST"])
@admin_only
def delete_product(pid):
    with connect() as c:
        c.execute("DELETE FROM products WHERE id=?", (pid,))

    return redirect(url_for("admin"))


# تغییر وضعیت سفارش
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


# وضعیت سلامت سایت
@app.route("/health")
def health():
    return jsonify(ok=True)


# راه‌اندازی دیتابیس
init()


# اجرای برنامه
if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", 5000))
    )
