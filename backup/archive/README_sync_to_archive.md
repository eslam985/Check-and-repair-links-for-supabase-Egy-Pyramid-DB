# 📄 Sync Telegram Links to Archive.org (Supabase Integration)

## 🎯 Overview

السكريبت ده بيعمل **Automation Pipeline عكسي** مقارنة بالسكريبت الأول:

* Telegram (مصدر الفيديو)
* Archive.org (رفع الفيديو كـ Backup)
* Supabase (تخزين اللينكات الجديدة)

### الفكرة:

أي حلقة عندها لينك Telegram (`hf.space`) ومفيهاش Archive → السكربت:

1. يحمل الفيديو
2. يرفعه على Archive.org
3. يولد لينك دائم
4. يحدث قاعدة البيانات

---

## ⚙️ Environment

* Python 3.10+
* Google Colab أو Local Machine

---

## 📦 Installation (Colab Cell)

```python
!pip install supabase httpx tqdm internetarchive
```

---

## 🔑 Configuration

```python
SUPABASE_URL = "..."
SUPABASE_KEY = "..."
IA_ACCESS_KEY = "..."
IA_SECRET_KEY = "..."
```

### شرح:

* `SUPABASE_URL / KEY` → الاتصال بقاعدة البيانات
* `IA_ACCESS_KEY / SECRET` → حساب Archive.org

---

## 🧠 Workflow

### 1. البحث عن الحلقات الناقصة

* يجلب روابط `hf.space`
* يتأكد من عدم وجود `archive.org`
* يعمل Join مع جدول `episodes` للحصول على:

  * اسم العمل
  * رقم الحلقة
  * media_id

---

### 2. تنظيف اسم العمل (Normalization)

```python
normalize_title()
```

بيعمل:

* تحويل النص لـ lowercase
* حذف الرموز غير المهمة
* إزالة كلمات زي:

  * مسلسل / فيلم / HD / مترجم
* توحيد المسافات

🎯 الهدف: إنشاء اسم نظيف يصلح للعرض والأرشفة

---

### 3. تحميل الفيديو من Telegram

```python
download_file()
```

المميزات:

* تحميل Streaming (بدون استهلاك RAM)
* Progress Bar
* Retry System (3 محاولات)
* Delay تصاعدي (5s / 10s / 15s)

---

### 4. إنشاء Identifier احترافي

```python
ia_identifier = f"egy-pyr_{media_id}_{ep_id}_{link_id}"
```

💡 الهدف:

* منع التكرار
* ضمان uniqueness
* تحسين تنظيم الملفات داخل Archive

---

### 5. رفع الفيديو على Archive.org

```python
upload_to_ia()
```

المميزات:

* Metadata كامل (title + description)
* Retry داخلي (5 مرات)
* File verification (checksum)
* Resume تلقائي عند الانقطاع

---

### 6. تحديث Supabase

```python
supabase.table("links").insert(...)
```

يتم إدخال:

* `episode_id`
* رابط التحميل المباشر
* `server_name = archive`
* `last_check_status = valid`

---

### 7. تنظيف الملفات

* حذف الفيديو بعد الرفع لتوفير المساحة

---

## ▶️ How to Run

### الخطوات:

1. افتح Google Colab
2. ثبت المكتبات
3. انسخ الكود
4. شغل السكربت

---

## 📊 Output Logs

أثناء التشغيل:

* تقدم التحميل
* حالة المحاولات
* تقدم الرفع
* الرابط النهائي
* عدد الحلقات المتبقية

---

## 🧩 Smart Logic Highlights

### ✔ نظام Retry ذكي

* تحميل: 3 محاولات
* رفع: 5 محاولات

---

### ✔ Structured Identifier

```text
egy-pyr_mediaId_episodeId_linkId
```

---

### ✔ Title Optimization

* إزالة الضوضاء من الاسم
* تحسين تجربة العرض

---

### ✔ Fail-Safe System

* تخطي الحلقات الفاشلة
* منع توقف السكربت بالكامل

---

## ⚠️ Notes

* لازم روابط Telegram تكون مباشرة وقابلة للتحميل
* تأكد من صلاحية مفاتيح Archive.org
* الفيديوهات الكبيرة هتاخد وقت في الرفع

---

## 🚀 Summary

السكريبت يقوم بـ:

✔ اكتشاف الحلقات الناقصة
✔ تحميل الفيديوهات من Telegram
✔ رفعها على Archive.org
✔ إنشاء روابط دائمة
✔ تحديث قاعدة البيانات

---

## 💡 Use Case

مثالي لـ:

* Backup Systems
* Media Archiving
* Streaming Platforms
* Automated Data Pipelines

---
