"""
cleaner_archive.py — تنظيف وفحص روابط Archive.org الميتة في قاعدة البيانات

المنطق:
1. جلب كل روابط archive التي حالتها "valid" لتحديثها.
2. فحص الروابط بشكل متوازي (Async/Concurrency) لتوفير الوقت.
3. إذا وجدنا جملة "Item not available" أو كان الـ Status Code هو 404:
   - يتم تحديث الروابط في الداتابيز إلى broken.
   - يتم كتابة "404_DELETED" في خانة الـ error_message.
"""

import os
import asyncio
from datetime import datetime
import httpx
from shared import supabase, log

# الإعدادات: فحص 20 رابط في نفس اللحظة، والـ Batch الواحد 100 رابط
CONCURRENT_LIMIT = 20
BATCH_SIZE       = int(os.getenv("CLEANER_BATCH_SIZE", "100"))


async def check_single_archive_link(sem, client, link_record):
    """يفحص رابط آرشيف فردي ويتأكد مرتين قبل اتخاذ قرار الحذف النهائي"""
    link_id = link_record["id"]
    url     = link_record["url"]

    async with sem:
        try:
            # المحاولة الأولى
            resp = await client.get(url, timeout=15.0)
            
            is_dead = False
            reason = ""

            if resp.status_code == 404:
                is_dead = True
                reason = "404 Not Found"
            elif resp.status_code == 200 and "Item not available" in resp.text:
                is_dead = True
                reason = "Item not available"

            # جدار الحماية: لو اشتبهنا إنه ميت، نعيد الفحص بعد 3 ثوانٍ للتأكد التام
            if is_dead:
                await asyncio.sleep(3)
                retry_resp = await client.get(url, timeout=15.0)
                
                # تأكيد الموت في المحاولة الثانية
                if retry_resp.status_code == 404 or (retry_resp.status_code == 200 and "Item not available" in retry_resp.text):
                    return {"id": link_id, "url": url, "is_dead": True, "reason": reason}
                else:
                    # لو اشتغل في المحاولة الثانية، نلغي الحذف فوراً لحمايته
                    return {"id": link_id, "url": url, "is_dead": False}

            return {"id": link_id, "url": url, "is_dead": False}

        except Exception as e:
            # أي خطأ شبكة أو تائم أوت = أمان (تخطي تماماً ولا تحذف)
            return {"id": link_id, "url": url, "is_dead": None, "error": str(e)}


async def run_cleaner():
    now_str = datetime.now().strftime("%H:%M:%S")
    log(f"🚀 [{now_str}] بدء مهمة التطهير والحذف الفوري لروابط ARCHIVE.ORG الميتة...")

    # جلب الروابط النشطة
    res = (
        supabase.table("links")
        .select("id, url")
        .ilike("server_name", "%archive%")
        .in_("last_check_status", ["valid"])
        .limit(BATCH_SIZE)
        .execute()
    )
    
    archive_links = res.data or []
    log(f"   📥 تم جلب {len(archive_links)} رابط آرشيف للفحص الدقيق.")

    if not archive_links:
        log("   ✅ لا توجد روابط تحتاج لفحص حالياً!")
        return

    sem = asyncio.Semaphore(CONCURRENT_LIMIT)
    stats = {"scanned": 0, "deleted": 0, "skipped": 0}

    async with httpx.AsyncClient(verify=False, follow_redirects=True) as client:
        tasks = [check_single_archive_link(sem, client, link) for link in archive_links]
        results = await asyncio.gather(*tasks)

        for res in results:
            stats["scanned"] += 1
            link_id = res["id"]
            url     = res["url"]

            if res["is_dead"] is True:
                log(f"   🔥 [DELETE] رابط ميت مؤكد تماماً ({res['reason']}): {url}")
                
                # إجراء الحذف الفوري الصارم من قاعدة البيانات
                supabase.table("links").delete().eq("id", link_id).execute()
                
                stats["deleted"] += 1
                
            elif res["is_dead"] is False:
                # الرابط سليم، نحدث وقت الفحص فقط لتوثيق حالته
                supabase.table("links").update({
                    "last_check_at": datetime.now().isoformat()
                }).eq("id", link_id).execute()
            else:
                # خطأ شبكة، تجاوز آمن
                stats["skipped"] += 1

    log(f"\n{'═'*55}")
    log(f"📊 حصيلة التطهير الفوري: فحص {stats['scanned']} | 🧹 تم حذف {stats['deleted']} رابط ميت نهائياً | ⏳ تخطي {stats['skipped']}")
    log(f"{'═'*55}\n")


if __name__ == "__main__":
    asyncio.run(run_cleaner())