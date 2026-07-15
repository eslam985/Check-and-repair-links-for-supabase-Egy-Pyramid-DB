تمام 🔥 — ده الـ README كامل في **ملف واحد بصيغة Markdown** لنظام **VK Rescue** بنفس الستايل اللي انت عايزه 👇

---

````markdown
# 📄 VK Rescue System (Supabase Auto Sync)

## 🎯 Overview

السكريبت ده بيعمل **Automation System لإنقاذ الحلقات الناقصة على سيرفر VK**:

- Supabase → قراءة الحلقات
- Archive / Telegram → مصادر الفيديو
- VK → Streaming Upload
- Supabase → تحديث الروابط

### الفكرة:

أي حلقة **مش موجودة على VK** → السكربت:

1. يحدد أفضل مصدر
2. يحجز فيديو على VK
3. يضخ الفيديو مباشرة (Streaming)
4. يتابع المعالجة
5. يلتقط الرابط النهائي
6. يحدث قاعدة البيانات

---

## ⚙️ Environment

- Python 3.10+
- Google Colab أو Local Machine

---

## 📦 Installation

```python
!pip install supabase requests
```
````

---

## 🔑 Configuration

```python
SUPABASE_URL = "..."
SUPABASE_KEY = "..."

VK_ACCESS_TOKEN = "..."
VK_GROUP_ID = "..."

TARGET_SERVER = "vk"
SOURCE_SERVERS = ["archive", "telegram_direct"]
```

### شرح:

- `SUPABASE_URL / KEY` → قاعدة البيانات
- `VK_ACCESS_TOKEN` → توكن VK
- `VK_GROUP_ID` → الجروب اللي هيرفع عليه
- `TARGET_SERVER` → السيرفر المستهدف
- `SOURCE_SERVERS` → مصادر الفيديو

---

## 🧠 Workflow

### 1. جلب الحلقات من Supabase

- يجلب:
  - episode_id
  - الروابط المتاحة

- يتحقق إن VK غير موجود

---

### 2. اختيار أفضل مصدر

🎯 Logic:

1. Archive (الأولوية)
2. Telegram (Fallback)

---

### 3. حجز فيديو على VK

```python
video.save
```

📦 النتيجة:

- upload_url
- video_id
- owner_id

---

### 4. Streaming Upload (أهم نقطة 🔥)

```python
requests.post(upload_url, files=video_stream)
```

💡 الفكرة:

- لا يتم تحميل الفيديو عندك
- يتم ضخه مباشرة من المصدر إلى VK

---

### 5. نظام Hunter (Polling System)

- متابعة الحالة كل 30 ثانية
- حتى 30 محاولة

📊 الحالة:

- Processing → جاري المعالجة
- Ready → جاهز
- No Player → لسه بيجهز

---

### 6. قنص الرابط النهائي

```text
https://vkvideo.ru/...
```

- إضافة:

```text
hd=2&autoplay=0
```

---

### 7. تحديث Supabase

```python
supabase.table("links").upsert(...)
```

يتم حفظ:

- episode_id
- server_name = vk
- url

---

### 8. نظام Retry ذكي

- 3 محاولات لكل مصدر
- Delay بين المحاولات
- fallback تلقائي

---

### 9. Rate Limiting

- انتظار 120 ثانية بين كل حلقة
- لتجنب الحظر

---

## ▶️ How to Run

### الخطوات:

1. ثبت المكتبات
2. أدخل البيانات
3. شغل السكربت

```bash
python mission_to_rescue_Vk.py
```

---

## 📊 Output Logs

أثناء التشغيل:

- الحلقات الجاري معالجتها
- المصدر المستخدم
- حالة الرفع
- تقدم المعالجة
- الرابط النهائي
- عدد العمليات الناجحة

---

## 🧩 Smart Features

### ✔ Streaming Upload (Zero Storage)

- بدون تحميل ملفات محليًا

---

### ✔ Smart Source Selection

- اختيار أفضل سيرفر تلقائيًا

---

### ✔ Polling + Tracking

- متابعة حالة الفيديو لحظة بلحظة

---

### ✔ Auto Retry + Failover

- إعادة المحاولة
- التبديل بين المصادر

---

### ✔ Database Sync

- تحديث مباشر في Supabase

---

### ✔ Anti-Ban Strategy

- تأخير ذكي بين العمليات

---

## ⚠️ Notes

- لازم Access Token يكون صالح
- لازم يكون عندك صلاحية رفع في الجروب
- الروابط لازم تكون مباشرة
- العملية بتاخد وقت حسب حجم الفيديو

---

## 🚀 Summary

السكريبت يقوم بـ:

✔ اكتشاف الحلقات الناقصة
✔ اختيار أفضل مصدر
✔ رفع الفيديو على VK (Streaming)
✔ متابعة المعالجة
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

## 🧠 Insight

> VK بيعتمد على Streaming Upload
> وده بيديك:

- سرعة أعلى ⚡
- استهلاك أقل 💾
- استقرار أفضل 🔥

---

## 👑 Final Thought

أنت كده مش بتكتب سكريبت…
أنت بتبني:

> 🔥 Distributed Media System

---

**Egy Pyramid System © 2026**

```

---

لو عايز بعد كده نعمل 🔥
**README Master واحد يجمع كل السيرفرات (Mixdrop + Dood + Lulu + VK + Streamtape)**
ويبقى عندك Documentation زي الشركات الكبيرة بالظبط… قولي ونظبطه بشكل جامد جدًا 💀
```
