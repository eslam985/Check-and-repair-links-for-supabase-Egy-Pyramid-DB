# 📄 DOODStream Rescue System (Supabase Auto Sync)

## 🎯 Overview

السكريبت ده بيعمل **Automation System لإنقاذ الحلقات الناقصة على سيرفر DOODStream**:

- Supabase → قراءة الحلقات
- Archive / Telegram → مصادر الفيديو
- DOODStream → رفع Remote تلقائي
- Supabase → تحديث الرابط الجديد

### الفكرة:

أي حلقة **مش موجودة على DOODStream** → السكربت:

1. يحدد أفضل مصدر (Archive أو Telegram)
2. يرسل الرابط لـ DOOD (Remote Upload)
3. يتابع حالة الملف
4. يستخرج الرابط النهائي
5. يحدث قاعدة البيانات

---

## ⚙️ Environment

- Python 3.10+
- Google Colab أو Local Machine

---

## 📦 Installation

```python id="z7qk1n"
!pip install supabase requests
```

---

## 🔑 Configuration

```python id="9f3xqa"
SUPABASE_URL = "..."
SUPABASE_KEY = "..."

DOOD_EMAIL = "..."
DOOD_API_KEY = "..."

TARGET_SERVER = "doodstream"
SOURCE_SERVERS = ["archive", "telegram_direct"]
```

### شرح:

- `SUPABASE_URL / KEY` → قاعدة البيانات
- `DOOD_API_KEY` → API الخاص بـ DOODStream
- `TARGET_SERVER` → السيرفر المستهدف
- `SOURCE_SERVERS` → مصادر الفيديو

---

## 🧠 Workflow

### 1. جلب الحلقات من Supabase

- يجلب:
  - episode_id
  - الروابط المتاحة لكل حلقة

- يتحقق إن DOODStream غير موجود

---

### 2. اختيار أفضل مصدر

🎯 Logic:

1. Archive (الأولوية)
2. Telegram (Fallback)

---

### 3. إرسال Remote Upload

```python id="0m3t8g"
https://doodapi.com/api/upload/url
```

- إرسال رابط الفيديو مباشرة
- DOOD يقوم بالتحميل داخليًا

---

### 4. نظام Hunter (Polling System)

- متابعة حالة الملف كل 30 ثانية
- حتى 30 محاولة

📊 الحالات:

- الملف قيد المعالجة
- الملف وصل السيرفر
- الملف جاهز (status = 200)

---

### 5. استخراج الرابط النهائي

```text id="m0l2av"
https://myvidplay.com/e/{file_code}
```

---

### 6. التحقق من حجم الملف

- يتم قراءة حجم الفيديو أثناء المعالجة
- تحويله إلى MB لعرضه في اللوج

🎯 الهدف:

- التأكد إن الملف فعلاً تم رفعه
- متابعة تقدم المعالجة

---

### 7. تحديث Supabase

```python id="u5p1zx"
supabase.table("links").upsert(...)
```

يتم حفظ:

- episode_id
- server_name = doodstream
- url

---

### 8. نظام Retry ذكي

- 3 محاولات لكل مصدر
- Delay بين المحاولات
- fallback تلقائي

---

### 9. Rate Limiting

- انتظار 120 ثانية بين كل حلقة
- لتجنب الحظر من DOOD

---

## ▶️ How to Run

### الخطوات:

1. ثبت المكتبات
2. أدخل البيانات (API Keys)
3. شغل السكربت

---

## 📊 Output Logs

أثناء التشغيل:

- الحلقات الجاري معالجتها
- المصدر المستخدم
- حالة الملف
- حجم الفيديو أثناء المعالجة
- الرابط النهائي
- عدد العمليات الناجحة

---

## 🧩 Smart Features

### ✔ Remote Upload (Zero Bandwidth)

- لا يتم تحميل الفيديو محليًا
- السيرفر يسحب الملف بنفسه

---

### ✔ Polling + Size Tracking

- متابعة حالة الملف
- عرض الحجم أثناء المعالجة

---

### ✔ Auto Retry + Failover

- محاولات متعددة
- تبديل بين المصادر تلقائيًا

---

### ✔ Database Sync

- تحديث مباشر في Supabase

---

### ✔ Anti-Ban Strategy

- تأخير بين العمليات
- تقليل الضغط على API

---

## ⚠️ Notes

- تأكد من صحة API الخاص بـ DOODStream
- الروابط لازم تكون مباشرة وقابلة للوصول
- العملية قد تستغرق وقت حسب حجم الفيديو
- الدومين المستخدم (`myvidplay.com`) يجب أن يكون مرتبط بحسابك

---

## 🚀 Summary

السكريبت يقوم بـ:

✔ اكتشاف الحلقات الناقصة
✔ اختيار أفضل مصدر
✔ رفع الفيديو على DOODStream
✔ متابعة المعالجة
✔ حفظ الرابط النهائي

---

## 💡 Use Case

مثالي لـ:

- Streaming Platforms
- Multi-Server Distribution
- Backup + Redundancy Systems
- Automated Media Pipelines

---

- كده عندك 5 سكربتات + 5 README =
- سيستم توزيع محتوى كامل Multi-Server جاهز يتباع 🔥

- لو حابب الخطوة الجاية:
- نحوّل كل ده لـ:

-ل CLI Tool

- أو Dashboard
- أو API واحدة

- ساعتها هتبقى داخل مرحلة منتج حقيقي مش مجرد كود 💰
