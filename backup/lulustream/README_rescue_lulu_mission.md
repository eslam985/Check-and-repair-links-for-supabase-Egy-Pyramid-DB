# 📄 LuluStream Rescue & Sync System (Supabase Integration)

## 🎯 Overview

ده نظام متكامل لإدارة وربط ملفات **LuluStream** مع قاعدة بيانات **Supabase** ضمن مشروع Egy Pyramid.

النظام بيجمع بين:

- Scraping
- Sync
- Injection
- Auto Rescue

### الفكرة:

أي حلقة **مش موجودة على LuluStream** → النظام:

1. يحدد أفضل مصدر (Archive أو Telegram)
2. يجهّز الرابط بشكل ذكي
3. يرفعه عبر API
4. يتابع المعالجة
5. يحفظ الرابط النهائي

---

## 🧩 System Components

### 1️⃣ The Scout (كشف النواقص)

📁 `checking_for_missing_Lulu_links.py`

- يبحث عن الحلقات بدون Lulu
- يولد تقرير `lulu_missing_tasks.txt`

---

### 2️⃣ The Scraper (سحب البيانات)

📁 `scrapeAllFiles_from_lulu.js`

- يعمل داخل Console
- يسحب:
  - title
  - embed link
  - source_url

---

### 3️⃣ The Injector (الحقن الآمن)

📁 `final_sync_lulu_json.py`

- يربط البيانات عبر `source_url`
- يمنع أي mismatch

---

### 4️⃣ The Hunter (البحث الذكي)

📁 `Search_for_missing_links...`

- يبحث داخل Lulu API
- يضيف الروابط مباشرة

---

### 5️⃣ The Rescue Script (السكريبت الحالي)

📁 `mission_to_rescue_LULU.py`

- ينفذ عملية رفع تلقائي + تحقق + حفظ

---

## ⚙️ Environment

- Python 3.10+
- Google Colab أو Local Machine

---

## 📦 Installation

```python id="r5u1lx"
!pip install supabase requests
```

---

## 🔑 Configuration

```python id="v2k9fa"
SUPABASE_URL = "..."
SUPABASE_KEY = "..."

LULU_API_KEY = "..."

TARGET_SERVER = "lulustream"
SOURCE_SERVERS = ["archive", "telegram_direct"]
```

---

## 🧠 Workflow (Rescue Script)

### 1. جلب الحلقات من Supabase

- يجلب:
  - episode_id
  - الروابط المتاحة

- يتأكد إن LuluStream غير موجود

---

### 2. اختيار المصدر

🎯 Logic:

- Archive
- Telegram

---

### 3. تثبيت الرابط (Smart URL Lock)

```text id="0qp9yo"
?vid=timestamp
```

💡 الهدف:

- منع تغيير الرابط بين المحاولات
- ضمان consistency

---

### 4. Wake-Up System (تنبيه السيرفر)

- إرسال GET request مع:

```text id="4q6vya"
Range: bytes=0-100
```

📌 الهدف:

- تنشيط السيرفر
- التأكد إن الرابط شغال

---

### 5. إرسال الطلب لـ Lulu

```python id="8q6u2k"
https://www.lulustream.com/api/upload/url
```

✔ استخدام Headers مخصصة:

- User-Agent
- Referer
- Origin

🎯 الهدف:

- تقليد Browser حقيقي
- تجاوز الحماية

---

### 6. نظام Hunter (Polling)

- حتى 30 محاولة
- انتظار ذكي:
  - أول 5 محاولات → 10 ثواني
  - بعد كده → 45 ثانية

📊 الحالات:

- Processing
- Uploading
- Ready (`canplay = 1`)
- Error

---

### 7. التحقق النهائي

- التأكد من:
  - `canplay == 1`

- استخراج:

```text id="1qj7we"
https://lulustream.com/e/{file_code}
```

---

### 8. تثبيت اسم الفيديو

- تعديل اسم الملف عبر API
- استخدام اسم الميديا الحقيقي

---

### 9. تحديث Supabase

```python id="h3q9lk"
supabase.table("links").upsert(...)
```

---

### 10. نظام Retry + Failover

- 3 محاولات لكل مصدر
- إعادة استخدام نفس الرابط
- fallback تلقائي

---

### 11. Rate Limiting

- 120 ثانية بين كل حلقة

---

## ▶️ How to Run

### الخطوات:

1. ثبت المكتبات
2. أدخل API Keys
3. شغل السكربت

---

## 📊 Output Logs

هتشوف:

- حالة كل حلقة
- محاولات الرفع
- حالة السيرفر
- تقدم المعالجة
- الرابط النهائي

---

## 🧩 Smart Features

### ✔ Smart URL Locking

- تثبيت الرابط أثناء المحاولات

---

### ✔ Wake-Up System

- تنشيط السيرفر قبل الإرسال

---

### ✔ Advanced Hunter Mode

- متابعة دقيقة للحالة

---

### ✔ Anti-Failure Logic

- اكتشاف مشاكل 404 و Internal Errors

---

### ✔ Header Spoofing

- محاكاة متصفح حقيقي

---

### ✔ Auto Rename

- تثبيت اسم الفيديو

---

### ✔ Full Automation

- من المصدر → Lulu → Supabase

---

## ⚠️ Notes

- الروابط لازم تكون Direct
- Lulu حساس للـ Headers
- العملية قد تستغرق حتى 15 دقيقة لكل ملف
- تأكد من API Key

---

## 🚀 Summary

السكريبت يقوم بـ:

✔ اكتشاف الحلقات الناقصة
✔ تجهيز الرابط
✔ تنشيط السيرفر
✔ رفع الفيديو على Lulu
✔ متابعة المعالجة
✔ تثبيت الاسم
✔ حفظ الرابط

---

## 💡 Use Case

مثالي لـ:

- Streaming Platforms
- Multi-Server Distribution
- Backup Systems
- Advanced Media Automation

---

## 🧠 Final Insight

النظام ده مش مجرد سكربت…
ده **Intelligent Upload Engine** فيه:

- Retry Logic
- Smart Detection
- Server Handling
- Data Integrity

---

🔥 كده انت وصلت لمرحلة:
**Automation System بذكاء شبه بشري لإدارة المحتوى**

لو ربطت كل الأنظمة دي مع بعض…
إنت بتبني فعليًا:

> 🎬 منصة Streaming + Distribution Engine كاملة

---
