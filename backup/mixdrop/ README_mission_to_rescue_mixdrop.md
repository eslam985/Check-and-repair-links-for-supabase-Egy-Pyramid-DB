# 📄 MixDrop Rescue System (Supabase Auto Sync)

## 🎯 Overview

السكريبت ده بيعمل **Automation System ذكي لإنقاذ الحلقات الناقصة على سيرفر MixDrop**:

- Supabase → قراءة الحلقات
- Archive / Telegram → مصادر الفيديو
- MixDrop → رفع Remote تلقائي
- Supabase → تحديث الرابط الجديد

### الفكرة:

أي حلقة **مش موجودة على MixDrop** → السكربت:

1. يحدد أفضل مصدر (Archive أو Telegram)
2. يبعته لـ MixDrop (Remote Upload)
3. يتابع حالة الرفع
4. يستخرج الرابط النهائي
5. يحدث قاعدة البيانات

---

## ⚙️ Environment

- Python 3.10+
- Google Colab أو Local Machine

---

## 📦 Installation

```python id="y4f9nr"
!pip install supabase requests
```

---

## 🔑 Configuration

```python id="3vl0o4"
SUPABASE_URL = "..."
SUPABASE_KEY = "..."

MIXDROP_EMAIL = "..."
MIXDROP_KEY = "..."

TARGET_SERVER = "mixdrop"
SOURCE_SERVERS = ["archive", "telegram_direct"]
```

### شرح:

- `SUPABASE_URL / KEY` → قاعدة البيانات
- `MIXDROP_EMAIL / KEY` → API الخاص بـ MixDrop
- `TARGET_SERVER` → السيرفر المستهدف
- `SOURCE_SERVERS` → مصادر الفيديو

---

## 🧠 Workflow

### 1. جلب الحلقات من Supabase

- يجلب:
  - episode_id
  - روابط السيرفرات المختلفة

- يتحقق إن MixDrop غير موجود

---

### 2. اختيار أفضل مصدر

🎯 Logic:

1. Archive (الأولوية)
2. Telegram (fallback)

---

### 3. إرسال Remote Upload

```python id="0a6b7m"
https://api.mixdrop.ag/remoteupload
```

- إرسال رابط الفيديو مباشرة
- MixDrop يقوم بالتحميل داخليًا

---

### 4. نظام Hunter (Polling System)

- متابعة حالة الرفع كل 30 ثانية
- حتى 20 محاولة

📊 الحالات:

- `Downloading` → جاري التحميل
- `Complete` → تم بنجاح
- `Error` → فشل

---

### 5. استخراج الرابط النهائي

```text id="0n4r0z"
https://mixdrop.ag/e/{file_code}
```

---

### 6. تحديث Supabase

```python id="h2t7vw"
supabase.table("links").upsert(...)
```

يتم حفظ:

- episode_id
- server_name = mixdrop
- url

---

### 7. نظام Retry ذكي

- 3 محاولات لكل مصدر
- Delay بين المحاولات
- fallback تلقائي لمصدر آخر

---

### 8. Rate Limiting

- انتظار 60 ثانية بين كل حلقة
- لتجنب الحظر من MixDrop

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
- حالة الرفع
- الرابط النهائي
- عدد العمليات الناجحة

---

## 🧩 Smart Features

### ✔ Source Priority System

- Archive أولاً
- Telegram كخطة بديلة

---

### ✔ Remote Upload (Zero Bandwidth)

- لا يتم تحميل الفيديو محليًا
- MixDrop يسحب الملف بنفسه

---

### ✔ Polling Verification System

- التأكد من نجاح الرفع قبل الحفظ

---

### ✔ Auto Retry + Failover

- محاولات متعددة
- تبديل تلقائي بين المصادر

---

### ✔ Database Sync

- تحديث مباشر في Supabase

---

## ⚠️ Notes

- تأكد من صحة API الخاص بـ MixDrop
- الروابط لازم تكون مباشرة وقابلة للوصول
- العملية قد تستغرق وقت حسب حجم الفيديو

---

## 🚀 Summary

السكريبت يقوم بـ:

✔ اكتشاف الحلقات الناقصة
✔ اختيار أفضل مصدر
✔ رفع الفيديو على MixDrop
✔ متابعة حالة الرفع
✔ حفظ الرابط النهائي

---

## 💡 Use Case

مثالي لـ:

- Streaming Platforms
- Multi-Server Distribution
- Backup + Redundancy Systems
- Automated Media Pipelines

---

---

# لو بصيت على الأربع سكربتات اللي عملنا لهم README…

- إنت كده بنيت فعليًا Content Distribution System كامل (End-to-End) 👀🔥

- لو حابب، الخطوة الجاية نعمل:
- يتحكم فيهمDashboard
- أو نحولهم API واحدة
- أو حتى SaaS بسيط يشتغل لوحده

## وقتها مش بس بتشغل سكربت… إنت بتبني مشروع قابل للبيع 💰
