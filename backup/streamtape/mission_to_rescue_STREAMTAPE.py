import requests
import time
import os
from datetime import datetime
from urllib.parse import quote
from supabase import create_client

# --- الإعدادات ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

ST_LOGIN = os.environ.get("STREAMTAPE_LOGIN")
ST_KEY = os.environ.get("STREAMTAPE_KEY")
TARGET_SERVER = "streamtape"  # السيرفر المستهدف للإنقاذ
SOURCE_SERVERS = [
    "archive",
    "telegram_direct",
    "mixdrop",
    "vk",
]


def is_archive_url_valid(url: str) -> bool:
    """يفحص إذا كان رابط آرشيف يحتوي على جملة تفيد بحذفه أو إغلاقه باستخدام requests"""
    if "archive.org" not in url:
        return True
    try:
        print(f"   🔎 [Source] جاري فحص سلامة سورس آرشيف المختار...")
        resp = requests.get(url, timeout=15.0, verify=False)
        if resp.status_code == 404:
            return False
        if resp.status_code == 200 and "Item not available" in resp.text:
            return False
        return True
    except Exception as e:
        print(f"   ⚠️ [Source] خطأ أثناء فحص الرابط: {e}")
        return False


def rescue_streamtape_mission():
    now = datetime.now().strftime("%H:%M:%S")
    print(f"🚀 [{now}] بدء مهمة الإنقاذ الذكية لسيرفر: {TARGET_SERVER.upper()}")

    # 1. جلب جميع الحلقات باستخدام Pagination (تجاوز حد الـ 1000 سجل)
    all_episodes = []
    start = 0
    step = 1000

    while True:
        response = (
            supabase.table("episodes")
            .select("id, episode_number, medias(title), links(server_name, url)")
            .range(start, start + step - 1)
            .execute()
        )

        if not response.data:
            break

        all_episodes.extend(response.data)

        if len(response.data) < step:
            break

        start += step

    if not all_episodes:
        print("❌ لم يتم العثور على بيانات!")
        return

    count_success = 0

    for ep in all_episodes:
        ep_id = ep["id"]
        existing_links = ep.get("links", [])

        # التأكد إن ميكس دروب مش موجود
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

        # 2. ثم التليجرام كخيار ثاني
        t_links = [
            l for l in existing_links if "telegram_direct" in l["server_name"].lower()
        ]
        if t_links:
            sorted_sources.append("telegram_direct")

            # 1. الأولوية للأرشيف
        if "archive" in available_sources:
            sorted_sources.append("archive")

        if "vk" in available_sources:
            sorted_sources.append("vk")

        if "lulustream" in available_sources:
            sorted_sources.append("lulustream")

        if "voe" in available_sources:
            sorted_sources.append("voe")

        if "download" in available_sources:
            sorted_sources.append("download")

        if not sorted_sources:
            continue

        # === التعديل المحصن: تدوير شامل على السيرفرات المتاحة وحقن باراميتر التليجرام ===
        now = datetime.now().strftime("%H:%M:%S")
        print(f"\n[{now}] 🔍 فحص حلقة ID: {ep_id} | المصادر المتاحة: {sorted_sources}")
        is_rescued = False

        for source_key in sorted_sources:
            # 1. استخراج الرابط الديناميكي للسيرفر الحالي في الدورة
            source_url = (
                available_sources.get(source_key)
                if source_key != "telegram_direct"
                else t_links[0]["url"]
            )

            # 2. شرط حقن معامل d=true لروابط التليجرام
            if source_key == "telegram_direct":
                if "?" in source_url:
                    source_url = f"{source_url}"
                else:
                    source_url = f"{source_url}"

            # 3. التحقق المسبق من روابط آرشيف
            if source_key == "archive" and not is_archive_url_valid(source_url):
                print(
                    f"   ❌ [Source] رابط Archive الحالي ميت! الانتقال تلقائياً للسيرفر التالي..."
                )
                continue

            print(f"   ✅ [Source] السورس النشط الحالي: [{source_key}] → {source_url}")

            # محاولات الرفع (3 محاولات لكل سيرفر متاح)
            for attempt in range(1, 4):
                print(f"📡 محاولة [{attempt}/3] باستخدام: [{source_key}]...")

                try:
                    # إرسال البيانات عبر params لحماية بنية الرابط من التلف أثناء النقل والترميز
                    api_url = "https://api.streamtape.com/remotedl/add"
                    payload = {"login": ST_LOGIN, "key": ST_KEY, "url": source_url}
                    upload_res = requests.get(
                        api_url, params=payload, timeout=30
                    ).json()

                    if upload_res.get("status") == 200:
                        remote_id = upload_res["result"]["id"]
                        # 2. نظام الـ Hunter (Polling) للتأكد من المعالجة
                        is_verified = False
                        for check_attempt in range(1, 100):  # 20 محاولة فحص
                            time.sleep(30)  # انتظر 30 ثانية بين كل فحص

                            status_url = f"https://api.streamtape.com/remotedl/status?login={ST_LOGIN}&key={ST_KEY}&id={remote_id}"
                            status_res = requests.get(status_url, timeout=20).json()
                            # دود بيستخدم status كود 200 لما يكون جاهز للعرض، غير كده بيكون لسه بيتحمل أو بيتحول
                            # ... (بعد تعريف status_res)

                            if status_res.get("status") == 200:
                                task_info = status_res.get("result", {}).get(
                                    remote_id, {}
                                )

                                # 1. قنص المعرف: ستريم تيب ساعات بيحطه في fileid وساعات في url
                                file_code = task_info.get("fileid")

                                # لو مفيش fileid، حاول تقنصه من الـ url لو موجود
                                if not file_code and task_info.get("url"):
                                    # الرابط بيكون: https://streamtape.com/v/xxxxxxx/name.mp4
                                    try:
                                        file_code = (
                                            task_info.get("url")
                                            .split("/v/")[1]
                                            .split("/")[0]
                                        )
                                    except:
                                        pass

                                # 2. التحقق من الاكتمال: لو الـ file_code ظهر، فالفيديو جاهز فوراً
                                if file_code:
                                    final_url = f"https://streamtape.com/e/{file_code}"
                                    now = datetime.now().strftime("%H:%M:%S")

                                    raw_size = task_info.get("bytes_total", 0)
                                    size_mb = float(raw_size) / (1024 * 1024)

                                    print(
                                        f"[{now}] ✅ تم القنص بنجاح! ({size_mb:.2f} MB) | الرابط: {final_url}"
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

                                # 3. حالة الانتظار (لو لسه مخلصش)
                                # 3. التعامل مع حالة الخطأ أو الانتظار
                                else:
                                    current_status = task_info.get("status")
                                    if current_status == "error":
                                        print(
                                            f"⚠️ المهمة الحالية فشلت على السيرفر (Status: error). جاري إلغاء الفحص لبدء محاولة جديدة..."
                                        )
                                        break  # الخروج من لوب الفحص (Polling) فوراً

                                    bytes_loaded = task_info.get("bytes_loaded", 0)
                                    size_mb = float(bytes_loaded) / (1024 * 1024)
                                    print(
                                        f"⏳ جاري السحب (Status: {current_status}) | المحمل: {size_mb:.2f} MB ({check_attempt}/100)..."
                                    )

                        if is_verified:
                            is_rescued = True
                            count_success += 1
                            break
                    else:
                        print(
                            f"⚠️ {TARGET_SERVER.upper()} رفض الطلب: {upload_res.get('msg')}"
                        )

                except Exception as e:
                    print(f"❌ خطأ تقني: {str(e)}")

                if not is_rescued:
                    time.sleep(10)  # انتظار بسيط قبل المحاولة التالية

            if is_rescued:
                break
        print("انتظر 20 ثانية بين كل حلقة لتجنب الحظر...")
        # تهدئة بين الحلقات
        time.sleep(20)  # 20 ثانية بين كل حلقة لتجنب الحظر

    now = datetime.now().strftime("%H:%M:%S")
    print(
        f"\n✨ [{now}] المهمة انتهت! تم إنقاذ {count_success} مادة لـ {TARGET_SERVER.upper()}."
    )


if __name__ == "__main__":
    rescue_streamtape_mission()
