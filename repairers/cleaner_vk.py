"""
cleaner_vk.py — فحص أخير وحذف روابط VK المكسورة نهائياً

المنطق:
- جلب الروابط الخاصة بـ VK التي تحمل حالة 'broken'.
- إجراء فحص سريع (Double Check) للرابط بنفس آلية الـ Watcher.
- إذا تأكد الحذف -> يتم مسح الرابط نهائياً من قاعدة البيانات لتوفير المساحة.
- إذا ظهر الرابط سليماً (False Positive) -> يتم إرجاعه لحالة 'valid' لحمايته.
- التخطي في حال ظهور Captcha للحماية من الحذف العشوائي.
"""

import os
import asyncio
import httpx
from shared import supabase, log

# عدد الروابط التي سيتم تنظيفها في الجولة الواحدة
CLEANER_BATCH_SIZE = int(os.getenv("CLEANER_BATCH_SIZE", "50"))
sem = asyncio.Semaphore(2)


async def verify_and_clean(client, link_id, url):
    async with sem:
        try:
            await asyncio.sleep(1.0)
            headers = {
                "User-Agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Mobile Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9",
            }

            res = await client.get(url, headers=headers, timeout=12.0)

            # 1. تخطي في حالة الحظر المؤقت
            if res.status_code in (403, 429, 503):
                return link_id, "skipped", f"Rate Limited ({res.status_code})"

            html_text = res.text

            # 2. تخطي في حالة الـ Captcha (عدم يقين)
            if (
                "vk.com/captcha" in html_text
                or "Please complete the security check" in html_text
            ):
                return link_id, "skipped", "VK Captcha / Security Check"

            # 3. التأكيد النهائي للحذف
            if (
                "light_cry_dog" in html_text
                or "video_ext_msg" in html_text
                or "protected by privacy settings" in html_text
                or "isn't available for viewing" in html_text
                or "This video has been deleted" in html_text
                or "Video deleted" in html_text
            ):
                return link_id, "confirmed_broken", "Confirmed Deleted by VK"

            # 4. الرابط يعمل فعلياً (إنقاذ الرابط)
            return link_id, "valid", "False Positive - Link is alive"

        except Exception as e:
            return link_id, "skipped", f"Error during verification: {e}"


async def run():
    log(f"🧹 [VK Cleaner] جلب {CLEANER_BATCH_SIZE} رابط مكسور للفحص النهائي والحذف...")

    # جلب الروابط المكسورة فقط
    res = (
        supabase.table("links")
        .select("id, url")
        .ilike("server_name", "%vk%")
        .eq("last_check_status", "broken")
        .limit(CLEANER_BATCH_SIZE)
        .execute()
    )

    links = res.data or []
    log(f"   ✅ تم العثور على {len(links)} رابط مكسور في قاعدة البيانات.")

    if not links:
        return

    async with httpx.AsyncClient(verify=False, follow_redirects=True) as client:
        tasks = [verify_and_clean(client, l["id"], l["url"]) for l in links]
        results = await asyncio.gather(*tasks)

    deleted_count = 0
    restored_count = 0

    for link_id, status, msg in results:
        if status == "confirmed_broken":
            try:
                # حذف الصف نهائياً من قاعدة البيانات
                supabase.table("links").delete().eq("id", link_id).execute()
                log(f"🗑️ تم الحذف النهائي | ID: {link_id:<6} | السبب: {msg}")
                deleted_count += 1
            except Exception as e:
                log(f"⚠️ فشل حذف {link_id}: {e}")

        elif status == "valid":
            try:
                # إرجاع الرابط لحالة 'valid' لأنه يعمل
                supabase.table("links").update(
                    {"last_check_status": "valid", "error_message": None}
                ).eq("id", link_id).execute()
                log(f"♻️ استعادة (كان مكسوراً بالخطأ) | ID: {link_id:<6}")
                restored_count += 1
            except Exception as e:
                log(f"⚠️ فشل استعادة {link_id}: {e}")

        else:
            log(f"⚠️ تم التخطي (غير مؤكد) | ID: {link_id:<6} | السبب: {msg}")

    log(
        f"🏁 النتيجة النهائية: تم مسح {deleted_count} رابط | تم استعادة {restored_count} رابط."
    )


if __name__ == "__main__":
    asyncio.run(run())
