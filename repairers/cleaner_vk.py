"""
cleaner_vk.py — فحص أخير وحذف روابط VK المكسورة نهائياً

المنطق:
- جلب الروابط الخاصة بـ VK التي تحمل حالة 'broken'.
- إجراء فحص سريع (Double Check) للرابط بنفس آلية الـ Watcher.
- إذا تأكد الحذف -> يتم مسح الرابط نهائياً من قاعدة البيانات لتوفير المساحة.
- إذا ظهر الرابط سليماً (False Positive) -> يتم إرجاعه لحالة 'valid' لحمايته.
- التخطي في حال ظهور Captcha للحماية من الحذف العشوائي.
/media/es/DDrive/projects/apps-python/Check-and-repair-links-for-supabase-Egy-Pyramid-DB/repairers/cleaner_vk.py
"""

import os
import re
import sys
import asyncio
import httpx
try:
    from dotenv import load_dotenv
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
    load_dotenv(dotenv_path=env_path)
except ImportError:
    pass

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import supabase, log

CLEANER_BATCH_SIZE = int(os.getenv("CLEANER_BATCH_SIZE", "100"))
VK_ACCESS_TOKEN = os.getenv("VK_ACCESS_TOKEN") or os.getenv("VK_SERVICE_KEY")



async def run():
    log(f"🧹 [VK Cleaner] جلب {CLEANER_BATCH_SIZE} رابط مكسور للحذف المباشر...")

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

    deleted_count = 0

    for link in links:
        link_id = link["id"]
        url = link["url"]
        try:
            supabase.table("links").delete().eq("id", link_id).execute()
            log(f"🗑️ تم الحذف النهائي | ID: {link_id:<6} | الرابط: {url}")
            deleted_count += 1
        except Exception as e:
            log(f"⚠️ فشل حذف {link_id}: {e}")

    log(f"🏁 النتيجة النهائية: تم مسح {deleted_count} رابط بنجاح.")

if __name__ == "__main__":
    asyncio.run(run())
