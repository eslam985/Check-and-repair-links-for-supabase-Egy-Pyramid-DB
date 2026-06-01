# /media/es/DDrive/projects/apps-python/Check-and-repair-links-for-supabase-Egy-Pyramid-DB/watchers/watcher_mixdrop.py
import os
import asyncio
from datetime import datetime
from playwright.async_api import async_playwright
from shared import supabase, log

# تقليل الحجم لأن المتصفحات تستهلك رام ومعالج بشكل مكثف جداً
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "50"))
# قصر التشغيل المتوازي على متصفحين فقط كحد أقصى لحماية موارد الجهاز
sem = asyncio.Semaphore(2)

async def check_mixdrop_link(link_id, embed_url):
    """
    تستخدم دالتك الذكية للتحقق مما إذا كان الرابط شغال أم ميت 100%
    """
    target_url = embed_url.replace("/e/", "/f/")
    if "?download" not in target_url:
        target_url += "?download"

    async with sem:  # التحكم في عدم فتح متصفحات كثيرة بالتوازي
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
            )
            page = await context.new_page()

            try:
                log(f"▶️ [Worker] جاري فحص الرابط (ID: {link_id})...")
                await page.goto(target_url, wait_until="domcontentloaded", timeout=45000)

                # --- 🔍 الفحص الحاسم والمباشر: هل الملف محذوف فعلياً؟ ---
                page_content = await page.content()
                
                # إذا وجدت رسالة الحذف، الملف تالف
                if "can't find the file you are looking for" in page_content.lower():
                    await browser.close()
                    return link_id, "broken", "404_DELETED", embed_url
                
                # طالما الصفحة فتحت ورسالة الحذف غير موجودة، الملف سليم 100% ولا داعي لأي نقرات
                await browser.close()
                return link_id, "valid", None, embed_url

            except Exception as e:
                await browser.close()
                return link_id, "broken", f"Playwright Error: {str(e)}", embed_url

async def run():
    log(f"🔍 [MixDrop Watcher] جلب أقدم {BATCH_SIZE} رابط خاص بـ MixDrop لفحصها...")

    res = (
        supabase.table("links")
        .select("id, url, server_name, last_check_status, created_at, last_check_at, check_count")
        .ilike("server_name", "%mixdrop%")
        .eq("is_fixed", False)
        .or_("last_check_status.in.(\"pending\",\"valid\"),url.ilike.%disabled%")
        
        # --- خوارزمية الترتيب متعدد المستويات لسيرفر mixdrop ---
        .order("last_check_at", desc=False, nullsfirst=True)
        .order("last_check_status", desc=True)
        .order("created_at", desc=False)
        .order("check_count", desc=False)
        .limit(BATCH_SIZE)
        .execute()
    )
    links = res.data or []
    log(f"   ✅ تم العثور على {len(links)} رابط لـ MixDrop")

    if not links:
        return

    # تشغيل الفحص بالتوازي تحت مظلة الـ Semaphore
    tasks = [check_mixdrop_link(l["id"], l["url"]) for l in links]
    results = await asyncio.gather(*tasks)

    # --- بداية التعديل الذكي للتحديث الجماعي ---
    now = datetime.now().isoformat()
    bulk_updates = []

    for link_id, status, error, url in results:
        server_name = "mixdrop"
        # 1. تحديث العداد الفردي سريعاً
        try:
            supabase.rpc("increment_check_count", {"row_id": link_id}).execute()
        except Exception:
            pass

        # 2. تجميع البيانات لتحديثها دفعة واحدة لاحقاً
        bulk_updates.append({
            "id": link_id,               
            "url": url,                  # 👈 تم إضافة هذا العمود لحل خطأ Not-Null Constraint
            "server_name": server_name,  # 👈 إضافة كإجراء وقائي في حال كان هذا العمود مطلوباً أيضاً
            "last_check_status": status,
            "error_message":     error,
            "last_check_at":     now,
        })

        # طباعة اللوج الفردية العادية لمعرفة النتيجة في الترمينال
        icon = "✅" if status == "valid" else "❌"
        log(f"{icon} {link_id:<6} | {server_name:<12} | {status:<8} | {url}")

    # 3. إرسال طلب واحد جماعي (Bulk Upsert) لـ Supabase بدلاً من مئات الطلبات
    if bulk_updates:
        try:
            # استخدام upsert يخبر سوبابيس بتحديث الصفوف بناءً على الـ id الممرر
            supabase.table("links").upsert(bulk_updates).execute()
            log(f"⚡ [Supabase]: تم حفظ وتحديث {len(bulk_updates)} رابط بنجاح في طلب واحد.")
        except Exception as e:
            log(f"⚠️ [Supabase Bulk Error]: فشل التحديث الجماعي، جاري محاولة الحفظ الفردي كخيار احتياطي: {e}")
            # Fallback: لو فشل التحديث الجماعي لأي سبب، يقوم السكريبت بالحفظ الفردي القديم تلقائياً كأمان
            for update_data in bulk_updates:
                try:
                    supabase.table("links").update(update_data).eq("id", update_data["id"]).execute()
                except Exception:
                    pass
    # --- نهاية التعديل ---


if __name__ == "__main__":
    asyncio.run(run())