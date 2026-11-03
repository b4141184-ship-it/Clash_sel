import os, sqlite3, secrets
from pathlib import Path
from datetime import datetime
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash

BASE=Path(__file__).resolve().parent
UPLOADS=BASE/"uploads"; UPLOADS.mkdir(exist_ok=True)
app=Flask(__name__)
app.secret_key=os.environ.get("SECRET_KEY","CHANGE_TO_A_LONG_RANDOM_SECRET")
app.config["MAX_CONTENT_LENGTH"]=8*1024*1024
DB=BASE/"shop.db"
ALLOWED={"png","jpg","jpeg","webp"}

def connect():
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row; return c

def init():
    with connect() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS admins(id INTEGER PRIMARY KEY, username TEXT UNIQUE, password TEXT);
        CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT,description TEXT,price INTEGER,image TEXT);
        CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY AUTOINCREMENT,customer TEXT,phone TEXT,product TEXT,amount INTEGER,receipt TEXT,status TEXT,created TEXT);
        """)
        if not c.execute("SELECT id FROM admins").fetchone():
            c.execute("INSERT INTO admins(username,password) VALUES(?,?)",(os.getenv("ADMIN_USERNAME","admin"),generate_password_hash(os.getenv("ADMIN_PASSWORD","CHANGE-ME-NOW"))))
        if not c.execute("SELECT id FROM products").fetchone():
            c.execute("INSERT INTO products(title,description,price,image) VALUES(?,?,?,?)",("نمونه محصول","این محصول نمونه را از پنل مدیریت تغییر بده.",300000,""))

def admin_only(fn):
    @wraps(fn)
    def inner(*a,**k):
        if not session.get("admin"): return redirect(url_for("login"))
        return fn(*a,**k)
    return inner

def image_ok(f):
    return f and f.filename and "." in f.filename and f.filename.rsplit(".",1)[1].lower() in ALLOWED

def rubika_notice(order):
    # برای اتصال واقعی، وب‌هوک سازگار را در تنظیمات محرمانه میزبان قرار بده.
    hook=os.getenv("RUBIKA_WEBHOOK","").strip()
    if hook:
        try:
            import requests
            requests.post(hook,json={"text":f"سفارش #{order['id']} | {order['product']} | {order['amount']} تومان | {order['customer']} | {order['phone']} | رسید: {order['receipt']}"},timeout=10)
        except Exception: app.logger.exception("Rubika webhook error")

@app.route("/")
def home():
    with connect() as c: products=c.execute("SELECT * FROM products ORDER BY id DESC").fetchall()
    return render_template("home.html",products=products)

@app.route("/uploads/<path:name>")
def upload_file(name): return send_from_directory(UPLOADS,name)

@app.route("/order",methods=["POST"])
def order():
    name=request.form.get("customer","").strip(); phone=request.form.get("phone","").strip()
    pid=request.form.get("product_id",type=int); receipt=request.files.get("receipt")
    if not name or not phone or not pid or not image_ok(receipt):
        flash("نام، شماره تماس، محصول و تصویر رسید معتبر لازم است.","error"); return redirect(url_for("home"))
    with connect() as c:
        p=c.execute("SELECT * FROM products WHERE id=?",(pid,)).fetchone()
        if not p: flash("محصول پیدا نشد.","error"); return redirect(url_for("home"))
        ext=receipt.filename.rsplit(".",1)[1].lower(); filename=secrets.token_hex(16)+"."+ext
        receipt.save(UPLOADS/filename)
        cur=c.execute("INSERT INTO orders(customer,phone,product,amount,receipt,status,created) VALUES(?,?,?,?,?,?,?)",
          (name,phone,p["title"],p["price"],filename,"در انتظار بررسی",datetime.now().strftime("%Y-%m-%d %H:%M")))
        oid=cur.lastrowid
        data={"id":oid,"product":p["title"],"amount":p["price"],"customer":name,"phone":phone,"receipt":filename}
    rubika_notice(data)
    return render_template("success.html",oid=oid)

@app.route("/track",methods=["GET","POST"])
def track():
    result=None
    if request.method=="POST":
        with connect() as c:
            result=c.execute("SELECT * FROM orders WHERE id=? AND phone=?",(request.form.get("oid"),request.form.get("phone","").strip())).fetchone()
        if not result: flash("سفارش پیدا نشد؛ کد و شماره را بررسی کن.","error")
    return render_template("track.html",result=result)

@app.route("/admin/login",methods=["GET","POST"])
def login():
    if request.method=="POST":
        with connect() as c: a=c.execute("SELECT * FROM admins WHERE username=?",(request.form.get("username"),)).fetchone()
        if a and check_password_hash(a["password"],request.form.get("password","")):
            session["admin"]=a["id"]; return redirect(url_for("admin"))
        flash("اطلاعات ورود اشتباه است.","error")
    return render_template("login.html")

@app.route("/admin/logout")
def logout(): session.clear(); return redirect(url_for("home"))

@app.route("/admin")
@admin_only
def admin():
    with connect() as c:
        products=c.execute("SELECT * FROM products ORDER BY id DESC").fetchall()
        orders=c.execute("SELECT * FROM orders ORDER BY id DESC").fetchall()
    return render_template("admin.html",products=products,orders=orders)

@app.route("/admin/product",methods=["POST"])
@admin_only
def add_product():
    title=request.form.get("title","").strip(); desc=request.form.get("description","").strip(); price=request.form.get("price",type=int)
    f=request.files.get("image"); filename=""
    if not title or not price or price<1: flash("عنوان و قیمت معتبر وارد کن.","error"); return redirect(url_for("admin"))
    if f and f.filename:
        if not image_ok(f): flash("فرمت عکس پشتیبانی نمی‌شود.","error"); return redirect(url_for("admin"))
        filename=secrets.token_hex(16)+"."+f.filename.rsplit(".",1)[1].lower(); f.save(UPLOADS/filename)
    with connect() as c: c.execute("INSERT INTO products(title,description,price,image) VALUES(?,?,?,?)",(title,desc,price,filename))
    flash("محصول اضافه شد.","success"); return redirect(url_for("admin"))

@app.route("/admin/product/<int:pid>/delete",methods=["POST"])
@admin_only
def delete_product(pid):
    with connect() as c: c.execute("DELETE FROM products WHERE id=?",(pid,))
    return redirect(url_for("admin"))

@app.route("/admin/order/<int:oid>",methods=["POST"])
@admin_only
def set_status(oid):
    status=request.form.get("status")
    if status in {"در انتظار بررسی","تأیید شد","رد شد","تکمیل شد"}:
        with connect() as c: c.execute("UPDATE orders SET status=? WHERE id=?",(status,oid))
    return redirect(url_for("admin"))

@app.route("/health")
def health(): return jsonify(ok=True)

if __name__=="__main__":
    init(); app.run(host="0.0.0.0",port=int(os.getenv("PORT",5000)))
