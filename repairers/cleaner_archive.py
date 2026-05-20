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
    """يفحص رابط آرشيف فردي بسرعة خارقة عن طريق رأس الطلب ويطبع الرابط فوراً"""
    link_id = link_record["id"]
    url     = link_record["url"]

    async with sem:
        log(f"   🔎 [Checking] جاري فحص الرابط الآن: {url}")
        try:
            # 1. جدار الحماية الأول: نرسل طلب HEAD سريع جداً لجلب الكود (403/404) دون تحميل أي بايت من الفيلم
            headers = {"Range": "bytes=0-50000"} # لطلب قطاع صغير جداً لو كان الرابط صفحة
            resp = await client.head(url, timeout=7.0)
            
            status = resp.status_code
            
            # لو كان الرابط صفحة ويرجع 200، نحتاج للـ GET لقراءة النص، لكن بقطاع محدد (Range) لحماية الذاكرة
            if status == 200:
                resp = await client.get(url, headers=headers, timeout=7.0)
                status = resp.status_code

            is_dead = False
            reason = ""

            if status in [403, 404]:
                is_dead = True
                reason = f"{status} Dead/Blocked"
            elif status == 200 and "Item not available" in resp.text:
                is_dead = True
                reason = "Item not available"

            # جدار الحماية: للتأكد مرتين قبل اتخاذ القرار النهائي
            if is_dead:
                log(f"   ⚠️ [Suspicious] اشتباه بموت الرابط ({reason})، جاري إعادة التأكيد بعد 3 ثوانٍ...")
                await asyncio.sleep(3)
                
                retry_resp = await client.head(url, timeout=7.0)
                retry_status = retry_resp.status_code
                if retry_status == 200:
                    retry_resp = await client.get(url, headers=headers, timeout=7.0)
                    retry_status = retry_resp.status_code
                
                if retry_status in [403, 404] or (retry_status == 200 and "Item not available" in retry_resp.text):
                    log(f"   🚨 [Dead Confirm] تم تأكيد موت الرابط!")
                    return {"id": link_id, "url": url, "is_dead": True, "reason": reason}
                else:
                    log(f"   🛡️ [Saved] الرابط عاد للعمل في المحاولة الثانية، تم حمايته.")
                    return {"id": link_id, "url": url, "is_dead": False}

            log(f"   🟢 [Valid] الرابط سليم تماماً.")
            return {"id": link_id, "url": url, "is_dead": False}

        except Exception as e:
            # أي خطأ شبكة أو تايم أوت = تخطي آمن
            log(f"   ⏳ [Skipped] تجاوز الرابط مؤقتاً بسبب خطأ شبكة أو تايم أوت: {e}")
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
        # تقسيم الـ 100 رابط إلى مجموعات صغيرة (مثلاً كل مجموعة 10 روابط) لتجنب الصمت الطويل وحظر الشبكة
        CHUNK_SIZE = 10
        for chunk_idx in range(0, len(archive_links), CHUNK_SIZE):
            chunk = archive_links[chunk_idx:chunk_idx + CHUNK_SIZE]
            
            log(f"   🔄 [Batch] جاري فحص الحزمة رقم {chunk_idx // CHUNK_SIZE + 1} وتضم {len(chunk)} روابط...")
            
            tasks = [check_single_archive_link(sem, client, link) for link in chunk]
            results = await asyncio.gather(*tasks)

            for result in results:
                stats["scanned"] += 1
                link_id = result["id"]
                url     = result["url"]

                if result["is_dead"] is True:
                    log(f"   🔥 [DELETE] رابط ميت مؤكد تماماً ({result['reason']}): {url}")
                    # إجراء الحذف الفوري من قاعدة البيانات
                    supabase.table("links").delete().eq("id", link_id).execute()
                    stats["deleted"] += 1
                    
                elif result["is_dead"] is False:
                    # الرابط سليم، نحدث وقت الفحص فقط لتوثيق حالته
                    supabase.table("links").update({
                        "last_check_at": datetime.now().isoformat()
                    }).eq("id", link_id).execute()
                else:
                    # خطأ شبكة أو تائم أوت
                    log(f"   ⏳ [SKIPPED] تم التخطي بسبب خطأ شبكة: {result.get('error', 'Timeout')}")
                    stats["skipped"] += 1
            
            # فترة راحة قصيرة جداً بين المجموعات لحماية الـ IP من الحظر
            await asyncio.sleep(1)

    log(f"\n{'═'*55}")
    log(f"📊 حصيلة التطهير الفوري: فحص {stats['scanned']} | 🧹 تم حذف {stats['deleted']} رابط ميت نهائياً | ⏳ تخطي {stats['skipped']}")
    log(f"{'═'*55}\n")


if __name__ == "__main__":
    asyncio.run(run_cleaner())