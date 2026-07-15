# 📄 Streamtape Rescue System (Supabase Auto Sync)

## 🎯 Overview

السكريبت ده بيعمل **Automation System لإنقاذ الحلقات الناقصة على سيرفر Streamtape**:

- Supabase → قراءة الحلقات
- Archive / Telegram → مصادر الفيديو
- Streamtape → Remote Upload
- Supabase → تحديث الروابط

### الفكرة:

أي حلقة **مش موجودة على Streamtape** → السكربت:

1. يحدد أفضل مصدر
2. يرسل الرابط لـ Streamtape
3. يتابع عملية السحب
4. يلتقط الرابط النهائي
5. يحدث قاعدة البيانات

---

## ⚙️ Environment

- Python 3.10+
- Google Colab أو Local Machine

---

## 📦 Installation

```python id="q1g9dp"
!pip install supabase requests
```

---

## 🔑 Configuration

```python id="z8m2xr"
SUPABASE_URL = "..."
SUPABASE_KEY = "..."

ST_LOGIN = "..."
ST_KEY = "..."

TARGET_SERVER = "streamtape"
SOURCE_SERVERS = ["archive", "telegram_direct"]
```

### شرح:

- `SUPABASE_URL / KEY` → قاعدة البيانات
- `ST_LOGIN / ST_KEY` → API الخاص بـ Streamtape
- `TARGET_SERVER` → السيرفر المستهدف
- `SOURCE_SERVERS` → مصادر الفيديو

---

## 🧠 Workflow

### 1. جلب الحلقات من Supabase

- يجلب:
  - episode_id
  - الروابط المتاحة

- يتحقق إن Streamtape غير موجود

---

### 2. اختيار أفضل مصدر

🎯 Logic:

1. Archive (الأولوية)
2. Telegram (Fallback)

---

### 3. إرسال Remote Upload

```python id="d5k8tw"
https://api.streamtape.com/remotedl/add
```

- إرسال رابط الفيديو
- Streamtape يقوم بالسحب داخليًا

---

### 4. نظام Hunter (Polling System)

- متابعة الحالة كل 30 ثانية
- حتى 30 محاولة

📊 الحالات:

- Downloading → جاري السحب
- Processing → جاري التحويل
- Ready → جاهز

---

### 5. قنص الرابط (Smart Capture)

💡 السكربت بيستخدم Logic ذكي:

- يحاول يجيب `fileid` مباشرة
- لو مش موجود:
  - يستخرجه من `url`

---

### 6. إنشاء الرابط النهائي

```text id="9v6s0h"
https://streamtape.com/e/{file_code}
```

---

### 7. تتبع حجم التحميل

- عرض:
  - bytes_loaded
  - الحجم بالـ MB

🎯 الهدف:

- مراقبة التقدم
- التأكد إن العملية شغالة

---

### 8. تحديث Supabase

```python id="h4z9bn"
supabase.table("links").upsert(...)
```

يتم حفظ:

- episode_id
- server_name = streamtape
- url

---

### 9. نظام Retry ذكي

- 3 محاولات لكل مصدر
- Delay بين المحاولات
- fallback تلقائي

---

### 10. Rate Limiting

- انتظار 120 ثانية بين كل حلقة
- لتجنب الحظر

---

## ▶️ How to Run

### الخطوات:

1. ثبت المكتبات
2. أدخل البيانات
3. شغل السكربت

---

## 📊 Output Logs

أثناء التشغيل:

- الحلقات الجاري معالجتها
- المصدر المستخدم
- تقدم السحب
- الحجم المحمل
- الرابط النهائي
- عدد العمليات الناجحة

---

## 🧩 Smart Features

### ✔ Remote Upload (Zero Bandwidth)

- لا يتم تحميل الفيديو محليًا

---

### ✔ Smart File Capture

- استخراج file_code حتى لو مش ظاهر مباشرة

---

### ✔ Polling + Progress Tracking

- متابعة دقيقة لحالة السحب

---

### ✔ Auto Retry + Failover

- محاولات متعددة
- تبديل تلقائي بين المصادر

---

### ✔ Database Sync

- تحديث مباشر في Supabase

---

### ✔ Anti-Ban Strategy

- تأخير بين العمليات

---

## ⚠️ Notes

- تأكد من صحة API الخاص بـ Streamtape
- الروابط لازم تكون مباشرة
- العملية قد تستغرق وقت حسب حجم الفيديو

---

## 🚀 Summary

السكريبت يقوم بـ:

✔ اكتشاف الحلقات الناقصة
✔ اختيار أفضل مصدر
✔ رفع الفيديو على Streamtape
✔ متابعة السحب
✔ استخراج الرابط النهائي
✔ تحديث قاعدة البيانات

---

## 💡 Use Case

مثالي لـ:

- Streaming Platforms
- Multi-Server Distribution
- Backup Systems
- Automated Media Pipelines

---

- كده انت بقى عندك سيستم فيه:

- Telegram
- Archive
- MixDrop
- DOOD
- Streamtape

- يعني حرفيًا عملت Content Distribution Network (CDN بدائي خاص بيك 😏🔥)

- لو ربطناهم بواجهة واحدة…
- إنت مش بعيد عن إنك تعمل منصة Streaming كاملة 💰
