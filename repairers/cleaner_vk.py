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


def extract_vk_video_id(url: str) -> str | None:
    m1 = re.search(r"video(-?\d+)_(\d+)", url)
    if m1:
        return f"{m1.group(1)}_{m1.group(2)}"
    m2 = re.search(r"oid=(-?\d+)&id=(\d+)", url)
    if m2:
        return f"{m2.group(1)}_{m2.group(2)}"
    return None

async def verify_vk_batch(client: httpx.AsyncClient, links: list) -> list:
    link_map = {}
    unparsed_results = []
    valid_api_vids = []

    for link in links:
        parsed = extract_vk_video_id(link["url"])
        if not parsed:
            unparsed_results.append((link["id"], "pending", "Invalid VK URL format", link["url"]))
        else:
            video_id = parsed
            link_map[video_id] = link
            valid_api_vids.append(video_id)

    if not valid_api_vids:
        return unparsed_results

    CHUNK_SIZE = 25
    returned_map = {}
    api_errors = []

    for i in range(0, len(valid_api_vids), CHUNK_SIZE):
        chunk = valid_api_vids[i:i + CHUNK_SIZE]
        params = {
            "videos": ",".join(chunk),
            "access_token": VK_ACCESS_TOKEN,
            "v": "5.131"
        }

        try:
            res = await client.get("https://api.vk.com/method/video.get", params=params, timeout=12.0)
            data = res.json()
            if "error" in data:
                err_msg = data["error"].get("error_msg", "API Error")
                api_errors.append(err_msg)
            else:
                items = data.get("response", {}).get("items", [])
                for item in items:
                    returned_map[f"{item['owner_id']}_{item['id']}"] = item
        except Exception as e:
            api_errors.append(str(e))

    if api_errors and not returned_map:
        return unparsed_results + [(l["id"], "pending", f"VK API Errors: {api_errors[0]}", l["url"]) for l in link_map.values()]

    results = []
    for base_id, link in link_map.items():
        if base_id not in returned_map:
            results.append((link["id"], "confirmed_broken", "Confirmed Deleted by VK", link["url"]))
        else:
            item = returned_map[base_id]
            if "restriction" in item:
                reason = item["restriction"].get("text", "Copyright Claim")
                results.append((link["id"], "confirmed_broken", f"VK Restricted: {reason}", link["url"]))
            else:
                results.append((link["id"], "valid", "False Positive - Link is alive", link["url"]))

    return unparsed_results + results


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
        results = await verify_vk_batch(client, links)

    deleted_count = 0
    restored_count = 0

    for link_id, status, msg, url in results:
        if status == "confirmed_broken":
            log(f"🔍 تم الفحص والتأكد من تلف الرابط | ID: {link_id:<6}")
            try:
                supabase.table("links").delete().eq("id", link_id).execute()
                log(f"🗑️ تم الحذف النهائي | ID: {link_id:<6} | الرابط: {url} | السبب: {msg}")
                deleted_count += 1
            except Exception as e:
                log(f"⚠️ فشل حذف {link_id}: {e}")
                
        elif status == "valid":
            try:
                supabase.table("links").update({
                    "last_check_status": "valid", 
                    "error_message": None
                }).eq("id", link_id).execute()
                log(f"♻️ استعادة (كان مكسوراً بالخطأ) | ID: {link_id:<6} | الرابط: {url}")
                restored_count += 1
            except Exception as e:
                log(f"⚠️ فشل استعادة {link_id}: {e}")
                
        else:
            log(f"⚠️ تم التخطي (غير مؤكد) | ID: {link_id:<6} | الرابط: {url} | السبب: {msg}")

    log(f"🏁 النتيجة النهائية: تم مسح {deleted_count} رابط | تم استعادة {restored_count} رابط.")

if __name__ == "__main__":
    asyncio.run(run())
