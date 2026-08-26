"""
watcher_vk.py — فحص روابط VK

المنطق:
يقوم السكربت بعمل HTTP GET لصفحة الـ embed الخاصة بـ VK.
يتم الفحص بناءً على وجود عناصر محددة في الـ HTML (مثل وسم light_cry_dog أو رسائل الخصوصية/الحذف)
للتأكد مما إذا كان الفيديو متاحاً أو تم حذفه/حجبه.
"""

import os
import re
import sys
import asyncio
import httpx
from datetime import datetime

try:
    from dotenv import load_dotenv
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
    load_dotenv(dotenv_path=env_path)
except ImportError:
    pass

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared import supabase, log

# يمكن زيادة الحجم إلى 100 لأن API VK يتحمل حتى 100 فيديو في الطلب الواحد
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "100"))
VK_ACCESS_TOKEN = os.getenv("VK_SERVICE_KEY")
if not VK_ACCESS_TOKEN:
    log("⚠️ تنبيه: VK_SERVICE_KEY غير موجود في .env!")

def extract_vk_video_id(url: str) -> tuple[str, str] | None:
    """
    تستخرج:
    1. api_id: المعرف الممرر للـ API (مرفقاً بـ access_key إن وجد)
    2. base_id: المعرف الأساسي المقارن في استجابة JSON {owner_id}_{video_id}
    """
    oid_m = re.search(r"[?&]oid=(-?\d+)", url)
    id_m = re.search(r"[?&]id=(\d+)", url)
    hash_m = re.search(r"[?&]hash=([a-fA-F0-9]+)", url)

    if oid_m and id_m:
        oid, vid = oid_m.group(1), id_m.group(1)
        base_id = f"{oid}_{vid}"
        if hash_m:
            return f"{base_id}_{hash_m.group(1)}", base_id
        return base_id, base_id

    vid_m = re.search(r"video(-?\d+)_(\d+)(?:_([a-fA-F0-9]+))?", url)
    if vid_m:
        oid, vid = vid_m.group(1), vid_m.group(2)
        base_id = f"{oid}_{vid}"
        if vid_m.group(3):
            return f"{base_id}_{vid_m.group(3)}", base_id
        return base_id, base_id

    return None

async def check_vk_batch(client: httpx.AsyncClient, links: list) -> list:
    link_map = {}
    unparsed_results = []
    valid_api_vids = []

    for link in links:
        parsed = extract_vk_video_id(link["url"])
        if not parsed:
            unparsed_results.append((link["id"], "pending", "Invalid VK URL format", link["server_name"], link["url"]))
        else:
            api_id, base_id = parsed
            link_map[base_id] = link
            valid_api_vids.append(api_id)

    if not valid_api_vids:
        return unparsed_results

    # تقسيم الروابط لدفعة فرعية بحجم 25 لتفادي تجاوز طول الـ URL
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
        return unparsed_results + [
            (l["id"], "pending", f"VK API Errors: {api_errors[0]}", l["server_name"], l["url"])
            for l in link_map.values()
        ]

    api_results = []
    for base_id, link in link_map.items():
        if base_id not in returned_map:
            api_results.append((link["id"], "broken", "VK: Video deleted or not found", link["server_name"], link["url"]))
        else:
            item = returned_map[base_id]
            if "restriction" in item:
                reason = item["restriction"].get("text", "Copyright Claim")
                api_results.append((link["id"], "broken", f"VK Restricted: {reason}", link["server_name"], link["url"]))
            else:
                api_results.append((link["id"], "valid", None, link["server_name"], link["url"]))

    return unparsed_results + api_results

def fetch_links_to_check() -> list[dict]:
    """جلب أقدم روابط VK المطلوب فحصها بخوارزمية ترتيب متعددة المستويات."""
    res = (
        supabase.table("links")
        .select("id, url, server_name, last_check_status, created_at, last_check_at, check_count")
        .ilike("server_name", "%vk%")
        .eq("is_fixed", False)
        .or_('last_check_status.in.(pending,valid),url.ilike.*disabled*')
        .order("last_check_at",     desc=False, nullsfirst=True)
        .order("last_check_status", desc=True)
        .order("created_at",        desc=False)
        .order("check_count",       desc=False)
        .limit(BATCH_SIZE)
        .execute()
    )
    return res.data or []
async def run():
    log(f"🔍 [VK Watcher] فحص أقدم {BATCH_SIZE} رابط...")
    links = fetch_links_to_check()
    log(f"   ✅ {len(links)} رابط")

    if not links:
        return

    async with httpx.AsyncClient(verify=False, follow_redirects=True) as client:
        results = await check_vk_batch(client, links)

    now = datetime.now().isoformat()
    bulk_updates = []

    for link_id, status, error, server_name, url in results:
        # 1. تحديث العداد الفردي سريعاً
        try:
            supabase.rpc("increment_check_count", {"row_id": link_id}).execute()
        except Exception:
            pass

        # 2. تجميع البيانات مع ضمان وجود url و server_name لتفادي خطأ Not-Null Constraint
        bulk_updates.append({
            "id": link_id,
            "url": url,
            "server_name": server_name,
            "last_check_status": status,
            "error_message": error,
            "last_check_at": now,
        })

        icon = "✅" if status == "valid" else "❌"
        if status == "pending": icon = "⏳"
        log(f"{icon} {link_id:<6} | {server_name:<12} | {status:<8} | {error} | {url}")

    # 3. تحديث الصفوف المحددة فقط في الداتا بيز دون التسبب في تعارض NOT NULL
    if bulk_updates:
        for update_data in bulk_updates:
            try:
                supabase.table("links").update({
                    "last_check_status": update_data["last_check_status"],
                    "error_message": update_data["error_message"],
                    "last_check_at": update_data["last_check_at"]
                }).eq("id", update_data["id"]).execute()
            except Exception as e:
                log(f"⚠️ [Supabase Error]: فشل تحديث الرابط {update_data['id']}: {e}")
        log(f"⚡ [Supabase]: تم تحديث حالة {len(bulk_updates)} رابط بنجاح.")

if __name__ == "__main__":
    asyncio.run(run())