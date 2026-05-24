"""
watcher_vk.py — فحص روابط VK

المنطق:
يقوم السكربت بعمل HTTP GET لصفحة الـ embed الخاصة بـ VK.
يتم الفحص بناءً على وجود عناصر محددة في الـ HTML (مثل وسم light_cry_dog أو رسائل الخصوصية/الحذف)
للتأكد مما إذا كان الفيديو متاحاً أو تم حذفه/حجبه.
"""

import os
import asyncio
import httpx
from datetime import datetime
from shared import supabase, log

BATCH_SIZE = int(os.getenv("BATCH_SIZE", "50"))
# نستخدم 2 بدلاً من 1 هنا لأن VK يتحمل الطلبات أفضل قليلاً من السيرفرات الأخرى
sem = asyncio.Semaphore(2)

async def check_vk(client, link_id, url, server_name):
    async with sem:
        try:
            # تأخير بسيط لتجنب الحظر
            await asyncio.sleep(1.0)

            # استخدام User-Agent واقعي لأن VK قد يمنع الطلبات الآلية البحتة
            headers = {
                "User-Agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Mobile Safari/537.36",
                "Accept-Language": "en-US,en;q=0.9"
            }
            
            res = await client.get(url, headers=headers, timeout=12.0)

            # 1. فحص كود الحالة لحماية الروابط من الحظر المؤقت
            if res.status_code in (403, 429, 503):
                return link_id, "skipped", f"Rate Limited ({res.status_code})", server_name, url

            html_text = res.text

            # 2. كشف الـ Captcha أو طلبات تسجيل الدخول (Soft Rate Limit)
            if "vk.com/captcha" in html_text or "Please complete the security check" in html_text:
                return link_id, "skipped", "VK Captcha / Security Check", server_name, url

            # 3. الفحص الفعلي للمحتوى بناءً على الـ HTML المُقدم
            # نبحث عن معرفات الـ CSS أو النصوص الصريحة التي تؤكد حظر/حذف الفيديو
            if (
                "light_cry_dog" in html_text
                or "video_ext_msg" in html_text
                or "protected by privacy settings" in html_text
                or "isn't available for viewing" in html_text
                or "This video has been deleted" in html_text
                or "Video deleted" in html_text
            ):
                return (
                    link_id,
                    "broken",
                    "VK: Protected or Deleted (Privacy/CryDog Check)",
                    server_name,
                    url,
                )

            # إذا لم تظهر أي علامة من علامات الحذف، فالرابط سليم
            return link_id, "valid", None, server_name, url

        except Exception as e:
            return link_id, "broken", f"VK Error: {e}", server_name, url


async def run():
    log(f"🔍 [VK Watcher] فحص أقدم {BATCH_SIZE} رابط...")
    res = (
        supabase.table("links")
        .select("id, url, server_name, last_check_status, created_at, last_check_at, check_count")
        .ilike("server_name", "%vk%")
        .eq("is_fixed", False)
        .or_("last_check_status.in.(\"pending\",\"valid\"),url.ilike.%disabled%")
        
        .order("last_check_at", desc=False, nullsfirst=True)
        .order("last_check_status", desc=True)
        .order("created_at", desc=False)
        .order("check_count", desc=False)
        .limit(BATCH_SIZE)
        .execute()
    )
    links = res.data or []
    log(f"   ✅ {len(links)} رابط")

    if not links:
        return

    async with httpx.AsyncClient(verify=False, follow_redirects=True) as client:
        tasks = [
            check_vk(client, l["id"], l["url"], l["server_name"]) for l in links
        ]
        results = await asyncio.gather(*tasks)

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
        if status == "skipped": icon = "⚠️"
        log(f"{icon} {link_id:<6} | {server_name:<12} | {status:<8} | {url}")

    # 3. إرسال طلب التحديث الجماعي (Bulk Upsert)
    if bulk_updates:
        try:
            supabase.table("links").upsert(bulk_updates).execute()
            log(f"⚡ [Supabase]: تم حفظ وتحديث {len(bulk_updates)} رابط بنجاح في طلب واحد.")
        except Exception as e:
            log(f"⚠️ [Supabase Bulk Error]: فشل التحديث الجماعي: {e}")
            # Fallback
            for update_data in bulk_updates:
                try:
                    supabase.table("links").update(update_data).eq("id", update_data["id"]).execute()
                except Exception:
                    pass

if __name__ == "__main__":
    asyncio.run(run())