import os
import httpx
import asyncio
import time
from tqdm.notebook import tqdm
from supabase import create_client
import nest_asyncio

from datetime import datetime

nest_asyncio.apply()

# --- الإعدادات ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
VOE_API_KEY = os.getenv("VOE_API_KEY")  # ضع مفتاح الـ API الخاص بـ Voe هنا

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
FAILED_TASKS = []


# --- دالة جلب المهام (مع معالجة الـ 1000 سجل) ---
async def get_next_voe_task():
    timeNow = datetime.now()
    print(f"get_next_voe_task: {timeNow}")
    print("🔍 البحث عن حلقات تفتقد سيرفر Voe...")

    all_episodes = []
    # جلب البيانات على دفعات لضمان مسح كل قاعدة البيانات
    for offset in range(0, 20000, 1000):
        res = (
            supabase.table("episodes")
            .select(
                "id, episode_number, media_id, medias(title), links(server_name, url)"
            )
            .range(offset, offset + 999)
            .execute()
        )

        if not res.data:
            break
        all_episodes.extend(res.data)

    for ep in all_episodes:
        ep_id = ep["id"]
        if ep_id in FAILED_TASKS:
            continue

        existing_links = ep.get("links", [])

        # التأكد هل سيرفر voe موجود فعلاً؟
        has_voe = any(
            "voe" in str(l.get("server_name", "")).lower() for l in existing_links
        )
        if has_voe:
            continue

        # تحديد السيرفرات المسموحة فقط وترتيب أولوياتها
        priority_map = {
            "archive": 1,
            "telegram_direct": 2,
            "streamtape": 4,
        }

        valid_sources = []
        for l in existing_links:
            s_name = str(l.get("server_name", "")).lower()
            # التأكد أن السيرفر ضمن القائمة المطلوبة فقط
            if s_name in priority_map:
                l["priority"] = priority_map[s_name]
                valid_sources.append(l)

        if not valid_sources:
            continue

        # ترتيب المصادر بناءً على الأولوية (الأقل رقماً يظهر أولاً)
        valid_sources.sort(key=lambda x: x["priority"])

        return {
            "episode_id": ep_id,
            "source_url": valid_sources[0]["url"],
            "source_name": valid_sources[0]["server_name"],
            "title": ep.get("medias", {}).get("title", "Unknown"),
            "ep_num": ep["episode_number"],
        }
    return None


# --- دالة الرفع لـ Voe (المنطق الخاص بك مع تحسينات) ---
async def upload_to_voe_logic(source_url, identifier):
    timeNow = datetime.now()
    print(f"upload_to_voe_logic: {timeNow}")
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            # تجهيز الرابط (إذا كان أرشيف نبنيه بشكل صحيح)
            remote_url = source_url
            if "archive.org" in source_url and not source_url.endswith(".mp4"):
                # محاولة استخراج المعرف إذا لم يكن رابطاً مباشراً
                id_part = source_url.rstrip("/").split("/")[-1]
                remote_url = f"https://archive.org/download/{id_part}/{id_part}.mp4"

            params = {"key": VOE_API_KEY, "url": remote_url}

            # 1. طلب الرفع (Remote Upload)
            print(f"📡 إرسال أمر الرفع عن بُعد من: {remote_url[:50]}...")
            response = await client.get("https://voe.sx/api/upload/url", params=params)
            res = response.json()

            if res.get("status") != 200:
                print(f"❌ فشل طلب الرفع: {res}")
                return None

            file_code = res.get("result", {}).get("file_code")

            # 2. متابعة الحالة (Polling)
            pbar = tqdm(total=100, desc="⏳ Voe Status: Queued")
            start_time = time.time()
            # بيجيب الوقت والتاريخ الحالي
            print(f"three_{timeNow}")
            check_count = 0

            while time.time() - start_time < 180:  # مهلة 3 دقيقة
                await asyncio.sleep(20)
                try:
                    status_check = await client.get(
                        f"https://voe.sx/api/file/status?key={VOE_API_KEY}&file_code={file_code}"
                    )
                    status_res = status_check.json()
                    status = status_res.get("result", {}).get("status")

                    check_count += 1
                    pbar.set_description(f"⏳ Voe Status: {status}")

                    if status == "finished":
                        pbar.n = 100
                        pbar.refresh()
                        pbar.close()
                        return file_code

                    if status == "downloading":
                        pbar.n = 40
                    elif status == "processing":
                        pbar.n = 80
                    pbar.refresh()

                    # صمام أمان إذا طال الانتظار جداً وتأكدنا من وجود كود الملف
                    if check_count >= 15 and file_code:
                        pbar.close()
                        return file_code

                except Exception:
                    continue

            pbar.close()
            return file_code
    except Exception as e:
        print(f"⚠️ خطأ في Voe API: {e}")
        return None


# --- المحرك الأساسي ---
async def run_voe_sync():
    # بيجيب الوقت والتاريخ الحالي
    timeNow = datetime.now()
    print(f"run_voe_sync: {timeNow}")
    print("🚀 محرك مزامنة Voe بدأ العمل...")

    while True:
        task = await get_next_voe_task()
        if not task:
            print("✅ جميع الحلقات لديها سيرفر Voe حالياً.")
            break

        ep_id = task["episode_id"]
        title = f"{task['title']} - حلقة {task['ep_num']}"
        print("______________________________________")

        print(f"\n📦 جاري معالجة: {title}")

        file_code = await upload_to_voe_logic(task["source_url"], ep_id)

        if file_code:
            voe_url = f"https://voe.sx/e/{file_code}"
            # حفظ في قاعدة البيانات
            try:
                supabase.table("links").insert(
                    {
                        "episode_id": ep_id,
                        "url": voe_url,
                        "server_name": "voe",
                        "quality": "720p",
                        "last_check_status": "pending",
                    }
                ).execute()
                print(f"✅ تم حفظ رابط Voe بنجاح: {voe_url}")
                print("______________________________________")

            except Exception as e:
                print(f"❌ خطأ أثناء حفظ الرابط: {e}")
                FAILED_TASKS.append(
                    ep_id
                )  # إضافة الحلقة هنا تمنع السكريبت من إعادة فحصها وضرب سيرفر Voe مجدداً
        else:
            print(f"❌ فشلت المهمة للحلقة {ep_id}")
            FAILED_TASKS.append(ep_id)

    print(f"last_{timeNow}")


if __name__ == "__main__":
    # في كولاب نستخدم loop.run_until_complete أو await مباشرة
    loop = asyncio.get_event_loop()
    loop.run_until_complete(run_voe_sync())
