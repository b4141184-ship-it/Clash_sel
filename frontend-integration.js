/*
  این نمونه را در انتهای HTML سایت، پس از اسکریپت اصلی قرار بده.
  اول API_BASE_URL را با آدرس عمومی سروری که deploy کردی جایگزین کن.
*/
const API_BASE_URL = "https://YOUR-SERVICE-HOST.example.com";

(function setupClashSelOrderBridge() {
  const form = document.getElementById("orderForm");
  if (!form) return;

  // اضافه کردن ورودی رسید و پیام راهنما
  if (!document.getElementById("receipt")) {
    const group = document.createElement("div");
    group.className = "form-group";
    group.innerHTML = `
      <label for="receipt">رسید پرداخت (اختیاری؛ JPG/PNG/WEBP/PDF، حداکثر ۵ مگابایت)</label>
      <input class="field" id="receipt" type="file" accept="image/jpeg,image/png,image/webp,application/pdf">
      <small class="muted">رسید فقط پس از ثبت سفارش به سرور ارسال می‌شود.</small>`;
    const submit = form.querySelector('button[type="submit"]');
    form.insertBefore(group, submit);
  }

  // این handler جایگزین ثبت سفارش محلی می‌شود.
  form.onsubmit = async function (e) {
    e.preventDefault();
    if (!Array.isArray(cart) || !cart.length) {
      alert("سبد خرید خالی است.");
      return;
    }
    if (document.getElementById("payment").value === "online") {
      document.getElementById("result").innerHTML =
        '<div class="notice">پرداخت آنلاین فعال نیست؛ گزینه هماهنگی پرداخت را انتخاب کن.</div>';
      return;
    }

    const all = getProducts();
    const items = cart.map(x => ({...x, p: all.find(p => p.id === x.id)})).filter(x => x.p);
    const total = items.reduce((s, x) => s + Number(x.p.price) * x.qty, 0);
    const order = {
      code: "CS-" + Date.now().toString().slice(-8),
      name: document.getElementById("buyerName").value.trim(),
      phone: document.getElementById("buyerPhone").value.trim(),
      note: document.getElementById("note").value.trim(),
      items: items.map(x => ({id: x.p.id, name: x.p.name, price: Number(x.p.price), qty: x.qty})),
      total,
      date: new Date().toISOString()
    };
    const file = document.getElementById("receipt").files[0];
    if (file && file.size > 5 * 1024 * 1024) {
      document.getElementById("result").innerHTML =
        '<div class="notice">حجم رسید نباید بیشتر از ۵ مگابایت باشد.</div>';
      return;
    }

    const data = new FormData();
    data.append("order", JSON.stringify(order));
    if (file) data.append("receipt", file);
    const submit = form.querySelector('button[type="submit"]');
    submit.disabled = true;
    submit.textContent = "در حال ارسال سفارش…";
    try {
      const response = await fetch(API_BASE_URL.replace(/\/$/, "") + "/api/order", {
        method: "POST",
        body: data
      });
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(result.error || "ارسال ناموفق بود.");
      document.getElementById("result").innerHTML =
        `<div class="success"><strong>سفارش برای مدیر ارسال شد.</strong><br>کد سفارش: ${escapeHTML(order.code)}<br>مبلغ: ${fa(total)} تومان<br>برای نهایی‌شدن خرید منتظر تأیید مدیر بمان.</div>`;
      cart = [];
      saveCart();
      renderCart();
      document.getElementById("result").innerHTML =
        `<div class="success"><strong>سفارش برای مدیر ارسال شد.</strong><br>کد سفارش: ${escapeHTML(order.code)}<br>مبلغ: ${fa(total)} تومان<br>برای نهایی‌شدن خرید منتظر تأیید مدیر بمان.</div>`;
      form.reset();
    } catch (err) {
      document.getElementById("result").innerHTML =
        `<div class="notice">ارسال سفارش انجام نشد: ${escapeHTML(err.message)}<br>لطفاً بعداً دوباره تلاش کن.</div>`;
    } finally {
      submit.disabled = false;
      submit.textContent = "ثبت سفارش";
    }
  };
})();
