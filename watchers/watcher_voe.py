"""
watcher_voe.py — فحص روابط VOE فقط
منطق: VOE API → file/info → status 200=valid / 404=broken
"""

import os
import asyncio
import httpx
from datetime import datetime
from shared import supabase, log

VOE_API_KEY = os.getenv("VOE_API_KEY")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "50"))
sem = asyncio.Semaphore(2)


async def check_voe_html_dead(client, url: str) -> bool:
    """يفحص صفحة الـ HTML الخاصة بـ VOE مسبقاً للتأكد من أنها لا تعرض رسالة 404 الميتة مع تتبع التوجيهات"""
    try:
        # تفعيل follow_redirects=True إجباري هنا لأن روابط الـ Embed تقوم بعمل Redirect
        resp = await client.get(url, timeout=10.0, follow_redirects=True)
        if resp.status_code == 404:
            return True
        if resp.status_code == 200 and "404 - Not found" in resp.text:
            return True
        return False
    except Exception:
        # خطأ الشبكة المؤقت لا يعود بـ True لضمان عدم حذف روابط سليمة بالخطأ
        return False


async def check_voe(client, url, link_id, server_name):
    try:
        # فحص حر وسريع خارج الـ Semaphore لفلترة الميت فوراً دون تعطيل الطابور
        if await check_voe_html_dead(client, url):
            return link_id, "broken", "HTML: 404 Not Found", server_name, url

    except Exception as e:
        return link_id, "broken", f"HTML Check Error: {str(e)}", server_name, url

    # الروابط التي اجتازت الفحص بنجاح فقط تدخل طابور المعالجة والـ API الحذر
    async with sem:
        try:
            # --- منطق إيسلام القديم والدقيق في استخراج الكود ---
            clean_url = url.strip().rstrip("/")
            if clean_url.endswith("/download"):
                clean_url = clean_url[:-9]

            # استخراج الكود مع شيل أي بارامترات بعد الـ ?
            file_code = clean_url.split("/")[-1].split("?")[0]

            # التأخير اللي أنت كنت عامله (0.4 ثانية)
            await asyncio.sleep(5)

            api_url = (
                f"https://voe.sx/api/file/info?key={VOE_API_KEY}&file_code={file_code}"
            )

            # استخدام verify=False زي سكريبتك القديم بالظبط
            res = await client.get(api_url, timeout=12.0)
            data = res.json()

            if data.get("success"):
                result = data.get("result", [{}])
                # التعامل الذكي مع نوع البيانات (list أو dict) اللي كان في سكريبتك
                item = result[0] if isinstance(result, list) else result
                status = str(item.get("status"))

                if status == "200":
                    return link_id, "valid", None, server_name, url
                if status == "404":
                    return link_id, "broken", "API: Deleted", server_name, url

            # لو رجع حاجة تانية غير 200 أو 404
            msg = data.get("msg", "No message")
            return link_id, "broken", f"VOE Unknown ({msg})", server_name, url

        except Exception as e:
            return link_id, "broken", f"VOE Error: {str(e)}", server_name, url


async def run():
    log(f"🔍 [VOE Watcher] فحص أقدم {BATCH_SIZE} رابط VOE...")
    res = (
        supabase.table("links")
        .select(
            "id, url, server_name, last_check_status, created_at, last_check_at, check_count"
        )
        .ilike("server_name", "%voe%")
        .eq("is_fixed", False)
        .or_('last_check_status.in.("pending","valid"),url.ilike.%disabled%')
        # --- خوارزمية الترتيب متعدد المستويات لسيرفر voe ---
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

    async with httpx.AsyncClient(verify=False) as client:
        tasks = [check_voe(client, l["url"], l["id"], l["server_name"]) for l in links]
        results = await asyncio.gather(*tasks)

    # --- بداية التعديل الذكي للتحديث الجماعي ---
    now = datetime.now().isoformat()
    bulk_updates = []

    for link_id, status, error, server_name, url in results:
        # 1. تحديث العداد الفردي سريعاً
        try:
            supabase.rpc("increment_check_count", {"row_id": link_id}).execute()
        except Exception:
            pass

        # 2. تجميع البيانات لتحديثها دفعة واحدة لاحقاً
        bulk_updates.append(
            {
                "id": link_id,
                "url": url,  # 👈 تم إضافة هذا العمود لحل خطأ Not-Null Constraint
                "server_name": server_name,  # 👈 إضافة كإجراء وقائي في حال كان هذا العمود مطلوباً أيضاً
                "last_check_status": status,
                "error_message": error,
                "last_check_at": now,
            }
        )

        # طباعة اللوج الفردية العادية لمعرفة النتيجة في الترمينال
        icon = "✅" if status == "valid" else "❌"
        log(f"{icon} {link_id:<6} | {server_name:<12} | {status:<8} | {url}")

    # 3. إرسال طلب واحد جماعي (Bulk Upsert) لـ Supabase بدلاً من مئات الطلبات
    if bulk_updates:
        try:
            # استخدام upsert يخبر سوبابيس بتحديث الصفوف بناءً على الـ id الممرر
            supabase.table("links").upsert(bulk_updates).execute()
            log(
                f"⚡ [Supabase]: تم حفظ وتحديث {len(bulk_updates)} رابط بنجاح في طلب واحد."
            )
        except Exception as e:
            log(
                f"⚠️ [Supabase Bulk Error]: فشل التحديث الجماعي، جاري محاولة الحفظ الفردي كخيار احتياطي: {e}"
            )
            # Fallback: لو فشل التحديث الجماعي لأي سبب، يقوم السكريبت بالحفظ الفردي القديم تلقائياً كأمان
            for update_data in bulk_updates:
                try:
                    supabase.table("links").update(update_data).eq(
                        "id", update_data["id"]
                    ).execute()
                except Exception:
                    pass
    # --- نهاية التعديل ---


if __name__ == "__main__":
    asyncio.run(run())
