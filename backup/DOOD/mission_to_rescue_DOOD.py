import requests
import time
import os
from datetime import datetime
from urllib.parse import quote
from supabase import create_client

# --- الإعدادات ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

DOOD_EMAIL = os.getenv("MIXDROP_EMAIL")
DOOD_API_KEY = os.getenv("DOOD_API_KEY")
TARGET_SERVER = "doodstream"
SOURCE_SERVERS = ["archive", "telegram_direct", "streamtape", "lulustream"]  # تليجرام له الأولوية

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def is_archive_url_valid(url: str) -> bool:
    """يفحص بشكل صارم وآمن سلامة رابط آرشيف دون تحميل الملف وبآلية التأكيد المزدوج"""
    if "archive.org" not in url:
        return True
        
    url = str(url).strip()
    if "disabled" in url.lower() or not url.startswith("http"):
        print(f"   ❌ [Source] رابط تالف أو ملغى نصياً.")
        return False

    headers = {"Range": "bytes=0-50000"} # جلب جزء بسيط جداً من الداتا فقط لفحص الحذف

    try:
        print(f"   🔎 [Source] جاري فحص سلامة السورس المختار بشكل سريع...")
        
        # 1. محاولة الفحص الأولى السريعة عبر HEAD ثم GET جزئي
        resp = requests.head(url, timeout=7.0)
        status = resp.status_code

        if status == 200:
            resp = requests.get(url, headers=headers, timeout=7.0)
            status = resp.status_code

        is_dead = False
        page_content = resp.text.lower() if status == 200 else ""

        if status in [403, 404] or (status == 200 and ("item not available" in page_content or "disabled" in page_content)):
            is_dead = True

        # 2. جدار الحماية والتأكيد المزدوج: لو اشتبهنا بموته، ننتظر ونعيد الفحص للتأكد من عدم السقوط المؤقت لآرشيف
        if is_dead:
            print(f"   ⚠️ [Source] اشتباه بموت رابط آرشيف، جاري إعادة التأكيد بعد 3 ثوانٍ...")
            time.sleep(3)

            retry_resp = requests.head(url, timeout=7.0)
            retry_status = retry_resp.status_code
            if retry_status == 200:
                retry_resp = requests.get(url, headers=headers, timeout=7.0)
                retry_status = retry_resp.status_code

            retry_content = retry_resp.text.lower() if retry_status == 200 else ""

            if retry_status in [403, 404] or (retry_status == 200 and ("item not available" in retry_content or "disabled" in retry_content)):
                print(f"   ❌ [Source] تم تأكيد موت الرابط أو حذفه نهائياً من آرشيف.")
                return False
            else:
                print(f"   🛡️ [Source] الرابط عاد للعمل في المحاولة الثانية، الرابط سليم.")
                return True

        return True

    except Exception as e:
        print(f"   ⚠️ [Source] خطأ شبكة أو تايم أوت أثناء فحص الرابط: {e}")
        # في الـ Uploader نفضل تمريره كـ True لو حدث خطأ شبكة عابر لكي لا نخسر الرابط، أو اقلبه لـ False لو أردت الصرامة المطلقة
        return True

def rescue_doodstream_mission():
    now = datetime.now().strftime("%H:%M:%S")
    print(f"🚀 [{now}] بدء مهمة الإنقاذ الذكية لسيرفر: {TARGET_SERVER.upper()}")

    # 1. جلب الحلقات الناقصة على دفعات (Pagination) لتغطية كل قاعدة البيانات
    all_episodes = []
    for offset in range(0, 30000, 1000):  # يمكنك زيادة الـ 30000 إذا كان حجم البيانات أكبر
        response = (
            supabase.table("episodes")
            .select("id, episode_number, medias(title), links(server_name, url)")
            .range(offset, offset + 999)
            .execute()
        )
        
        if not response.data:
            break
        all_episodes.extend(response.data)

    if not all_episodes:
        print("❌ لم يتم العثور على بيانات!")
        return

    count_success = 0

    for ep in all_episodes:
        ep_id = ep["id"]
        existing_links = ep.get("links", [])

        # التأكد إن دودو مش موجود
        if any(l["server_name"].lower() == TARGET_SERVER for l in existing_links):
            continue

        # تنظيم المصادر المتاحة
        available_sources = {
            l["server_name"].lower(): l["url"]
            for l in existing_links
            if l["server_name"].lower() in SOURCE_SERVERS
        }

        # ترتيب المصادر (الأرشيف أولاً ثم التليجرام)
        sorted_sources = []

        # 1. الأولوية للأرشيف
        if "archive" in available_sources:
            sorted_sources.append("archive")

        # 2. ثم التليجرام كخيار ثاني
        t_links = [
            l for l in existing_links if "telegram_direct" in l["server_name"].lower()
        ]
        if t_links:
            sorted_sources.append("telegram_direct")
            
        if "streamtape" in available_sources:
            sorted_sources.append("streamtape")
        if "lulustream" in available_sources:
            sorted_sources.append("archilulustreamve")
        if not sorted_sources:
            continue

        # === التعديل الجديد: الفحص المسبق والالتفاف التلقائي لـ Doodstream ===
        now = datetime.now().strftime("%H:%M:%S")
        print(f"\n[{now}] 🔍 فحص حلقة ID: {ep_id} | المصادر المتاحة: {sorted_sources}")
        is_rescued = False

        # جلب المصدر الأول المختار مبدئياً بناءً على ترتيب مصفوفة الأولويات المتاحة
        primary_source_key = sorted_sources[0]
        source_url = (
            available_sources.get(primary_source_key)
            if primary_source_key != "telegram_direct"
            else t_links[0]["url"]
        )
        print(f"   ✅ [Source] السورس الأولي المختار: [{primary_source_key}] → {source_url}")

        # إذا كان الاختيار الأول هو آرشيف، نتأكد من سلامته قبل بدء حجز المهمة في دود
        if primary_source_key == "archive":
            if not is_archive_url_valid(source_url):
                print(f"   ❌ [Source] رابط Archive تالف ومحذوف! جاري التبديل للبديل التالي...")
                
                # البحث عن بديل تليجرام في القائمة المرتبة
                fallback_key = next((k for k in sorted_sources if k != "archive"), None)
                if fallback_key:
                    primary_source_key = fallback_key
                    source_url = (
                        available_sources.get(fallback_key)
                        if fallback_key != "telegram_direct"
                        else t_links[0]["url"]
                    )
                    print(f"   ✅ [Source] تم التحويل تلقائياً للسورس البديل: [{primary_source_key}] → {source_url}")
                else:
                    print(f"   ❌ [Source] رابط Archive ميت ولا توجد مصادر بديلة أخرى لهذه الحلقة.")
                    continue

        # حصر التكرار التنفيذي على السورس المستقر والنهائي المقبول
        active_sources = [primary_source_key]
        for source_key in active_sources:
            # محاولات الرفع (3 محاولات لكل مصدر)
            for attempt in range(1, 4):
                print(f"📡 محاولة [{attempt}/3] باستخدام: [{source_key}]...")

                try:
                    # 1. إرسال طلب الـ Remote Upload
                    api_url = f"https://doodapi.com/api/upload/url?key={DOOD_API_KEY}&url={quote(source_url)}"

                    upload_res = requests.get(api_url, timeout=30).json()

                    if upload_res.get("success") or upload_res.get("msg") == "OK":
                        file_code = upload_res["result"]["filecode"]
                        # 2. نظام الـ Hunter (Polling) للتأكد من المعالجة
                        is_verified = False
                        for check_attempt in range(1, 31):  # 20 محاولة فحص
                            time.sleep(30)  # انتظر 30 ثانية بين كل فحص

                            status_url = f"https://doodapi.com/api/file/info?key={DOOD_API_KEY}&file_code={file_code}"
                            status_res = requests.get(status_url, timeout=20).json()
                            # دود بيستخدم status كود 200 لما يكون جاهز للعرض، غير كده بيكون لسه بيتحمل أو بيتحول
                            if status_res.get("status") == 200:
                                # أول ما نلاقي بيانات الملف والحجم ظهر، نسيف فوراً
                                # مش هنستنى الـ canplay عشان نكسب وقت
                                final_url = f"https://myvidplay.com/e/{file_code}"
                                now = datetime.now().strftime("%H:%M:%S")

                                raw_size = status_res["result"][0].get("size", 0)
                                size_mb = float(raw_size) / (1024 * 1024)

                                print(
                                    f"[{now}] ✅ تم التأكد من وصول الملف ({size_mb:.2f} MB) | الرابط: {final_url}"
                                )

                                # الحفظ في سوبابيز
                                supabase.table("links").upsert(
                                    {
                                        "episode_id": ep_id,
                                        "server_name": TARGET_SERVER,
                                        "url": final_url,
                                    },
                                    on_conflict="episode_id, server_name",
                                ).execute()

                                is_verified = True
                                break
                            else:
                                # حماية بسيطة لو الـ result لسه مجاش
                                result_data = status_res.get("result", [{}])
                                raw_size = (
                                    result_data[0].get("size", 0) if result_data else 0
                                )

                                try:
                                    size_mb = float(raw_size) / (1024 * 1024)
                                    size_str = f"{size_mb:.2f} MB"
                                except:
                                    size_str = "Unknown"

                                print(
                                    f"⏳ الملف وصل السيرفر وهو الآن في مرحلة المعالجة (الحجم: {size_str}) ({check_attempt}/30)..."
                                )

                        if is_verified:
                            is_rescued = True
                            count_success += 1
                            break
                    else:
                        print(f"⚠️ دود ستريم رفض الطلب: {upload_res.get('msg')}")

                except Exception as e:
                    print(f"❌ خطأ تقني: {str(e)}")

                if not is_rescued:
                    time.sleep(10)  # انتظار بسيط قبل المحاولة التالية

            if is_rescued:
                break
        print(f"✅ تم استرجاع الحلقة.")
        print(f"انتظر 360 دقيقة قبل الحلقة التالية...")
        # تهدئة بين الحلقات
        time.sleep(360)  # 120 دقيقة بين كل حلقة لتجنب الحظر

    now = datetime.now().strftime("%H:%M:%S")
    print(
        f"\n✨ [{now}] المهمة انتهت! تم إنقاذ {count_success} مادة لـ {TARGET_SERVER.upper()}."
    )


if __name__ == "__main__":
    rescue_doodstream_mission()
