"""
watcher_generic.py — فحص الروابط العامة (VK, archive, telegram_direct, mixdrop, download)
منطق: HTTP GET → 200/206 = valid
مع كشف روابط telegram_direct الناقصة (missing hash)
"""

import os
import asyncio
import httpx
from datetime import datetime
from shared import supabase, log

BATCH_SIZE = int(os.getenv("BATCH_SIZE", "200"))
sem         = asyncio.Semaphore(2)

# السيرفرات اللي هيتعامل معاها الـ generic
# === التعديل الجديد ===
GENERIC_SERVERS = ["archive"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer":    "https://egypyramid.vercel.app/",
}


async def check_generic(client, link_id, url, server_name):
    url_str = str(url or "").strip()

    # 1. جدار الحماية المبكر: لو الرابط مشوه أو ملغى نصياً في قاعدة البيانات
    if "disabled" in url_str.lower() or not url_str.startswith("http"):
        return link_id, "broken", "Text Disabled/Invalid URL", server_name, url_str

    async with sem:
        try:
            # 2. الفحص الخارق السريع: طلب رأس الملف فقط (HEAD) لحماية الذاكرة والوقت
            resp = await client.head(url_str, headers=HEADERS, timeout=7.0)
            status = resp.status_code

            # لو رجع 200، نختبر قطاع صغير جداً (Range) للتأكد من سلامة الصفحة النصية وقراءة المحتوى
            if status == 200:
                resp = await client.get(url_str, headers={**HEADERS, "Range": "bytes=0-10000"}, timeout=7.0)
                status = resp.status_code

            # التحقق من شروط الحياة والموت النصية في آرشيف
            page_content = resp.text.lower() if status == 200 else ""
            if status in (200, 206) and "item not available" not in page_content and "disabled" not in page_content:
                return link_id, "valid", None, server_name, url_str

            # 3. الـ Retry الذكي في حالة الفشل المحتمل (محاولة أخيرة قبل الحكم)
            await asyncio.sleep(3)
            retry = await client.head(url_str, headers=HEADERS, timeout=7.0)
            retry_status = retry.status_code
            if retry_status == 200:
                retry = await client.get(url_str, headers={**HEADERS, "Range": "bytes=0-10000"}, timeout=7.0)
                retry_status = retry.status_code

            retry_content = retry.text.lower() if retry_status == 200 else ""
            if retry_status in (200, 206) and "item not available" not in retry_content and "disabled" not in retry_content:
                return link_id, "valid", None, server_name, url_str

            # إذا تم تأكيد الموت أو الحجب
            err_msg = f"HTTP {status}" if status not in (200, 206) else "Item Disabled/Unavailable"
            return link_id, "broken", err_msg, server_name, url_str

        except Exception as e:
            # أخطاء الشبكة الحقيقية تحسب مكسورة في الـ Watcher العام ليعاد إصلاحها
            return link_id, "broken", str(e), server_name, url_str


def _build_filter():
    """بيبني فلتر OR لجلب السيرفرات الـ generic من الداتابيز"""
    # Supabase Python: استخدام or_ filter
    conditions = ",".join([f"server_name.ilike.%{s}%" for s in GENERIC_SERVERS])
    return conditions


async def run():
    log(f"🔍 [Archive Watcher] فحص أقدم {BATCH_SIZE} رابط آرشيف فقط...")

    # نجيب الروابط اللي server_name مش فيه voe ولا dood ولا streamtape ولا lulu
    # === التعديل الجديد: جلب روابط آرشيف فقط بشكل مباشر ===
    res = (
        supabase.table("links")
        .select("id, url, server_name")
        .ilike("server_name", "%archive%")
        .eq("is_fixed", False)
        .order("last_check_at", desc=False, nullsfirst=True)
        .limit(BATCH_SIZE)
        .execute()
    )
# =======================================================
    links = res.data or []
    log(f"   ✅ {len(links)} رابط")

    if not links:
        return

    async with httpx.AsyncClient(verify=False, follow_redirects=True) as client:
        tasks   = [check_generic(client, l["id"], l["url"], l["server_name"]) for l in links]
        results = await asyncio.gather(*tasks)

    now = datetime.now().isoformat()
    for link_id, status, error, server_name, url in results:
        try:
            supabase.rpc("increment_check_count", {"row_id": link_id}).execute()
            supabase.table("links").update({
                "last_check_status": status,
                "error_message":     error,
                "last_check_at":     now,
            }).eq("id", link_id).execute()
        except Exception:
            pass
        icon = "✅" if status == "valid" else "❌"
        log(f"{icon} {link_id:<6} | {server_name:<12} | {status:<8} | {url}")


if __name__ == "__main__":
    asyncio.run(run())