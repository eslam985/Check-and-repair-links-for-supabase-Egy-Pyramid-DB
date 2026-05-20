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
BATCH_SIZE = int(os.getenv("CLEANER_BATCH_SIZE", "100"))


async def check_single_archive_link(sem, client, link_record):
    """يفحص رابط آرشيف فردي، ويحذف الروابط المشوهة أو المكتوب فيها Disabled صراحة فوراً"""
    link_id = link_record["id"]
    url = str(link_record.get("url", "")).strip()

    # جدار حماية مبكر: لو الرابط مكتوب فيه Disabled صراحة أو ليس رابطاً حقيقياً
    if "disabled" in url.lower() or not url.startswith("http"):
        log(f"   🔥 [Direct Dead] تم رصد رابط تالف أو ملغى نصياً في الـ DB: {url}")
        return {
            "id": link_id,
            "url": url,
            "is_dead": True,
            "reason": "Text Disabled/Invalid URL",
        }

    async with sem:
        log(f"   🔎 [Checking] جاري فحص الرابط الآن: {url}")
        try:
            # نرسل طلب HEAD سريع جداً لجلب الكود (403/404) دون تحميل أي بايت من الفيلم
            headers = {"Range": "bytes=0-50000"}
            resp = await client.head(url, timeout=7.0)

            status = resp.status_code

            # لو كان الرابط صفحة ويرجع 200، نحتاج للـ GET لقراءة النص بقطاع محدد (Range)
            if status == 200:
                resp = await client.get(url, headers=headers, timeout=7.0)
                status = resp.status_code

            is_dead = False
            reason = ""

            page_content = resp.text.lower() if status == 200 else ""

            if status in [403, 404]:
                is_dead = True
                reason = f"{status} Dead/Blocked"
            elif status == 200 and (
                "item not available" in page_content or "disabled" in page_content
            ):
                is_dead = True
                reason = "Item Disabled/Unavailable"

            # جدار الحماية: للتأكد مرتين قبل اتخاذ القرار النهائي
            if is_dead:
                log(
                    f"   ⚠️ [Suspicious] اشتباه بموت الرابط ({reason})، جاري إعادة التأكيد بعد 3 ثوانٍ..."
                )
                await asyncio.sleep(3)

                retry_resp = await client.head(url, timeout=7.0)
                retry_status = retry_resp.status_code
                if retry_status == 200:
                    retry_resp = await client.get(url, headers=headers, timeout=7.0)
                    retry_status = retry_resp.status_code

                retry_content = retry_resp.text.lower() if retry_status == 200 else ""

                if retry_status in [403, 404] or (
                    retry_status == 200
                    and (
                        "item not available" in retry_content
                        or "disabled" in retry_content
                    )
                ):
                    log(f"   🚨 [Dead Confirm] تم تأكيد موت الرابط!")
                    return {
                        "id": link_id,
                        "url": url,
                        "is_dead": True,
                        "reason": reason,
                    }
                else:
                    log(
                        f"   🛡️ [Saved] الرابط عاد للعمل في المحاولة الثانية، تم حمايته."
                    )
                    return {"id": link_id, "url": url, "is_dead": False}

            log(f"   🟢 [Valid] الرابط سليم تماماً. : {url}")
            return {"id": link_id, "url": url, "is_dead": False}

        except Exception as e:
            log(f"   ⏳ [Skipped] تجاوز الرابط مؤقتاً بسبب خطأ شبكة أو تايم أوت: {e}")
            return {"id": link_id, "url": url, "is_dead": None, "error": str(e)}


async def run_cleaner():
    now_str = datetime.now().strftime("%H:%M:%S")
    log(f"🚀 [{now_str}] بدء مهمة التطهير والحذف الفوري لروابط ARCHIVE.ORG الميتة...")

    # جلب الروابط النشطة
    # خوارزمية الأولوية والجدولة الذكية للروابط
    res = (
    supabase.table("links")
    .select("id, url, server_name, last_check_status, created_at, last_check_at, check_count")
    .ilike("server_name", "%archive%")
    .eq("is_fixed", False)
    
    # الفلتر الذكي: جلب الجديد (pending)، السليم لإعادة التدوير (valid)، أو المشوه نصياً للتطهير الفوري
    .or_("last_check_status.in.(\"pending\",\"valid\"),url.ilike.%disabled%")
    
    # --- خوارزمية الترتيب متعدد المستويات ---
    .order("last_check_at", desc=False, nullsfirst=True) # 1. الجديد تماماً أو الغائب أولاً
    .order("last_check_status", desc=True)               # 2. تقديم الـ pending على الـ valid
    .order("created_at", desc=False)                     # 3. الأقدمية الزمنية من تاريخ الإنشاء
    .order("check_count", desc=False)                    # 4. الأقل فحصاً لعدالة التوزيع
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
            chunk = archive_links[chunk_idx : chunk_idx + CHUNK_SIZE]

            log(
                f"   🔄 [Batch] جاري فحص الحزمة رقم {chunk_idx // CHUNK_SIZE + 1} وتضم {len(chunk)} روابط..."
            )

            tasks = [check_single_archive_link(sem, client, link) for link in chunk]
            results = await asyncio.gather(*tasks)

            for result in results:
                stats["scanned"] += 1
                link_id = result["id"]
                url = result["url"]

                if result["is_dead"] is True:
                    log(
                        f"   🔥 [DELETE] رابط ميت مؤكد تماماً ({result['reason']}): {url}"
                    )
                    # إجراء الحذف الفوري من قاعدة البيانات
                    supabase.table("links").delete().eq("id", link_id).execute()
                    stats["deleted"] += 1

                elif result["is_dead"] is False:
                    # الرابط سليم، نحدث وقت الفحص فقط لتوثيق حالته
                    supabase.table("links").update(
                        {"last_check_at": datetime.now().isoformat()}
                    ).eq("id", link_id).execute()
                else:
                    # خطأ شبكة أو تائم أوت
                    log(
                        f"   ⏳ [SKIPPED] تم التخطي بسبب خطأ شبكة: {result.get('error', 'Timeout')}"
                    )
                    stats["skipped"] += 1

            # فترة راحة قصيرة جداً بين المجموعات لحماية الـ IP من الحظر
            await asyncio.sleep(1)

    log(f"\n{'═'*55}")
    log(
        f"📊 حصيلة التطهير الفوري: فحص {stats['scanned']} | 🧹 تم حذف {stats['deleted']} رابط ميت نهائياً | ⏳ تخطي {stats['skipped']}"
    )
    log(f"{'═'*55}\n")


if __name__ == "__main__":
    asyncio.run(run_cleaner())
