# 📄 Sync Archive Links to Telegram (Supabase Integration)

## 🎯 Overview

السكريبت ده بيعمل **Automation Pipeline** لربط الحلقات بين:

* Archive.org (مصدر الفيديو)
* Telegram (رفع الفيديو والحصول على لينك مباشر)
* Supabase (تخزين اللينكات الجديدة)

### الفكرة:
## تحديث قاعدة البينات بلينكات تليجرام الناقصه عن طريقة الارشيف اورج

أي حلقة عندها لينك archive ومفيهاش لينك Telegram → السكربت:

1. يحمل الفيديو
2. يرفعه على Telegram
3. يستخرج اللينك
4. يحدث قاعدة البيانات
---

## ⚙️ Environment

* Python 3.10+
* Google Colab (موصى به)

---

## 📦 Installation (Colab Cell)

```python
!pip install supabase httpx telethon tqdm nest_asyncio
```

---

## 🔑 Configuration

```python
SUPABASE_URL = "..."
SUPABASE_KEY = "..."
API_ID = ...
API_HASH = "..."
BOT_TOKEN = "..."
TARGET_CHAT = "@EgyPyramid_stream_bot"
```

### شرح:

* `SUPABASE_URL / KEY` → الاتصال بقاعدة البيانات
* `API_ID / API_HASH` → حساب Telegram
* `BOT_TOKEN` → البوت
* `TARGET_CHAT` → البوت المستهدف

---

## 🧠 Workflow

### 1. البحث عن الحلقات الناقصة

* يجلب روابط archive
* يتحقق من عدم وجود hf.space
* يعمل join مع جدول episodes للحصول على:

  * اسم العمل
  * رقم الحلقة

---

### 2. تحميل الفيديو من Archive

* قراءة metadata من archive
* تحديد ملف `.mp4` الحقيقي
* في حالة عدم وجوده → يتم تخطي الحلقة
* تحميل مع:

  * Retry System
  * Progress Bar

---

### 3. رفع الفيديو على Telegram

* رفع الفيديو إلى **Saved Messages**
* Forward إلى البوت
* انتظار رد البوت

---

### 4. استخراج الرابط

* البحث داخل الرسائل عن رابط `hf.space`
* تنظيف الرابط من أي رموز إضافية

---

### 5. تحديث Supabase

يتم إدخال:

* `episode_id`
* `url`
* `server_name = telegram_direct`
* `last_check_status = valid`

---

### 6. تنظيف الملفات

* حذف الفيديو بعد الرفع لتوفير المساحة

---

## ▶️ How to Run (Colab)

### الخطوات:

1. افتح Google Colab
2. شغل خلية تثبيت المكتبات
3. انسخ الكود بالكامل في خلية جديدة
4. شغل السكربت

---

## 🔐 أول تشغيل فقط (Telegram Login)

عند أول تشغيل:

* سيطلب إدخال رقم الهاتف
* ثم كود التحقق من Telegram

بعدها:

* يتم حفظ Session
* لن تحتاج لإعادة تسجيل الدخول مرة أخرى

---

## 📊 Output Logs

أثناء التشغيل ستظهر:

* تقدم التحميل
* تقدم الرفع
* حالة كل حلقة
* اللينك الجديد بعد الاستخراج
* تأكيد التحديث في قاعدة البيانات

---

## ⚠️ Notes

* يجب أن يكون البوت يرسل روابط `hf.space`
* تأكد من وجود مساحة كافية أثناء التحميل
* السكربت يتعامل مع فيديوهات كبيرة (قد تستغرق وقت)

---

## 🚀 Summary

السكريبت يقوم بـ:

✔ اكتشاف الحلقات الناقصة
✔ تحميل الفيديوهات
✔ رفعها على Telegram
✔ استخراج اللينكات المباشرة
✔ تحديث قاعدة البيانات تلقائيًا
## تحديث قاعدة البينات بلينكات تليجرام الناقصه عن طريقة الارشيف اورج
---

## 💡 Use Case

مناسب لـ:

* منصات Streaming
* أرشفة الأنمي أو المحتوى
* أنظمة Automation لرفع المحتوى

---
