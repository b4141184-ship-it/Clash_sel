import os
import json
import uuid
import asyncio
import inspect
import threading
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from werkzeug.utils import secure_filename
from rubka import Robot, Message

# رازها را فقط در Environment Variables سرور تنظیم کن؛ داخل سایت یا GitHub ننویس.
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID", "").strip()
MAX_UPLOAD_MB = 5
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "private_receipts"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".pdf"}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
CORS(app, resources={r"/api/*": {"origins": "*"}})

bot = Robot(token=BOT_TOKEN) if BOT_TOKEN else None

def call_bot(method, *args, **kwargs):
    """سازگار با متدهایی که ممکن است coroutine برگردانند."""
    result = method(*args, **kwargs)
    if inspect.isawaitable(result):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(result)
        future = asyncio.run_coroutine_threadsafe(result, loop)
        return future.result(timeout=30)
    return result

def bot_message(chat_id, text):
    if not bot or not chat_id:
        raise RuntimeError("BOT_TOKEN یا ADMIN_CHAT_ID تنظیم نشده است.")
    return call_bot(bot.send_message, chat_id, text)

def bot_receipt(chat_id, path, caption):
    if not bot or not chat_id:
        raise RuntimeError("BOT_TOKEN یا ADMIN_CHAT_ID تنظیم نشده است.")
    # Rubka در نسخه‌های فعلی متد send_image برای تصویر و send_document برای PDF دارد.
    ext = Path(path).suffix.lower()
    if ext == ".pdf":
        return call_bot(bot.send_document, chat_id, path=str(path), text=caption)
    return call_bot(bot.send_image, chat_id, path=str(path), text=caption)

@app.get("/")
def health():
    return jsonify({"ok": True, "service": "Clash_sel order bridge"})

@app.get("/api/health")
def api_health():
    return jsonify({
        "ok": True,
        "bot_configured": bool(BOT_TOKEN),
        "admin_chat_configured": bool(ADMIN_CHAT_ID)
    })

@app.post("/api/order")
def receive_order():
    # انتظار می‌رود order به صورت JSON داخل فیلد فرم ارسال شود.
    raw = request.form.get("order", "")
    if not raw:
        return jsonify({"ok": False, "error": "اطلاعات سفارش ارسال نشده است."}), 400
    try:
        order = json.loads(raw)
    except json.JSONDecodeError:
        return jsonify({"ok": False, "error": "ساختار سفارش معتبر نیست."}), 400

    code = str(order.get("code", ""))[:40]
    name = str(order.get("name", ""))[:60]
    phone = str(order.get("phone", ""))[:30]
    note = str(order.get("note", ""))[:400]
    items = order.get("items", [])
    if not code or not name or not phone or not isinstance(items, list) or not items:
        return jsonify({"ok": False, "error": "اطلاعات ضروری سفارش ناقص است."}), 400

    # قیمت‌های دریافتی از مرورگر به تنهایی قابل اعتماد نیستند؛ قبل از فروش واقعی،
    # قیمت‌ها را در سمت سرور هم از فهرست معتبر بررسی کن.
    try:
        total = int(order.get("total", 0))
        if total < 0:
            raise ValueError()
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "مبلغ سفارش معتبر نیست."}), 400

    receipt = request.files.get("receipt")
    receipt_path = None
    if receipt and receipt.filename:
        ext = Path(secure_filename(receipt.filename)).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            return jsonify({"ok": False, "error": "رسید باید JPG، PNG، WEBP یا PDF باشد."}), 400
        receipt_path = UPLOAD_DIR / f"{uuid.uuid4().hex}{ext}"
        receipt.save(receipt_path)

    lines = [
        "🛍 سفارش جدید Clash_sel",
        f"🔢 کد سفارش: {code}",
        f"👤 خریدار: {name}",
        f"📞 تماس: {phone}",
        "🎮 محصولات:"
    ]
    for item in items[:30]:
        try:
            item_id = str(item.get("id", "?"))
            item_name = str(item.get("name", "محصول"))[:100]
            qty = int(item.get("qty", 1))
            price = int(item.get("price", 0))
            lines.append(f"• شماره {item_id} | {item_name} × {qty} | {price:,} تومان")
        except Exception:
            lines.append("• اطلاعات یکی از محصولات نامعتبر بود.")
    lines.extend([
        f"💰 مبلغ اعلام‌شده: {total:,} تومان",
        f"📝 توضیحات: {note or 'ندارد'}",
        f"🧾 رسید: {'ضمیمه پیام بعدی می‌شود' if receipt_path else 'بارگذاری نشده'}",
        "",
        "⚠️ پیش از تأیید، مبلغ را در حساب بانکی بررسی کن."
    ])
    text = "\n".join(lines)

    try:
        bot_message(ADMIN_CHAT_ID, text)
        if receipt_path:
            bot_receipt(ADMIN_CHAT_ID, str(receipt_path), f"رسید سفارش {code}")
    except Exception as exc:
        # اگر ارسال شکست بخورد، پیام موفقیت به خریدار نمی‌دهیم.
        app.logger.exception("Rubika notification failed")
        return jsonify({
            "ok": False,
            "error": "سفارش ثبت نشد یا ارسال به ربات ناموفق بود. مدیر باید تنظیمات سرور را بررسی کند."
        }), 502

    return jsonify({"ok": True, "code": code, "message": "سفارش برای مدیر ارسال شد."})

@app.errorhandler(413)
def too_large(_error):
    return jsonify({"ok": False, "error": f"حجم رسید نباید بیشتر از {MAX_UPLOAD_MB} مگابایت باشد."}), 413

# این هندلر فقط شناسه چت فرستنده را نشان می‌دهد تا بتوانی ADMIN_CHAT_ID را تنظیم کنی.
if bot:
    @bot.on_message(commands=["start"])
    def start_handler(bot_instance: Robot, message: Message):
        chat_id = getattr(message, "chat_id", "") or ""
        message.reply(
            "سلام! ربات فروشگاه Clash_sel فعال است.\n"
            f"شناسه چت شما:\n{chat_id}\n\n"
            "این شناسه را فقط در متغیر امن ADMIN_CHAT_ID سرور قرار بده."
        )

def run_bot():
    if bot:
        bot.run()

if __name__ == "__main__":
    if not BOT_TOKEN:
        print("هشدار: BOT_TOKEN در Environment Variables تنظیم نشده است.")
    if not ADMIN_CHAT_ID:
        print("هشدار: ADMIN_CHAT_ID در Environment Variables تنظیم نشده است.")
    if bot:
        threading.Thread(target=run_bot, daemon=True).start()
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
