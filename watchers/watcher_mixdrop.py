import os
import asyncio
from datetime import datetime
from playwright.async_api import async_playwright
from shared import supabase, log

# تقليل الحجم لأن المتصفحات تستهلك رام ومعالج بشكل مكثف جداً
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "20"))
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
                # تقليل مهلة الانتظار الافتراضية للشبكة لتجنب التعليق اللانهائي
                await page.goto(target_url, wait_until="domcontentloaded", timeout=45000)

                # --- 🔍 الفحص الحاسم: هل الملف محذوف فعلياً؟ ---
                page_content = await page.content()
                if "can't find the file you are looking for" in page_content:
                    await browser.close()
                    return link_id, "broken", "404_DELETED", embed_url

                btn_selector = "a.download-btn"

                # دوران محاكاة النقرات للصيد والتأكد التام
                for i in range(1, 11):
                    try:
                        await page.wait_for_selector(btn_selector, state="visible", timeout=10000)
                        
                        if i == 5:
                            await page.reload(wait_until="domcontentloaded")
                            await page.wait_for_timeout(3000)
                            continue

                        try:
                            async with context.expect_page(timeout=10000) as new_page_info:
                                await page.click(btn_selector)
                            ad_page = await new_page_info.value
                            await page.wait_for_timeout(5000)
                            await ad_page.close()
                        except Exception:
                            pass

                        await page.bring_to_front()
                        href = await page.get_attribute(btn_selector, "href")

                        if href and href.startswith("http"):
                            is_valid_direct = "mxcontent" in href or (not ("?download" in href or "mixdrop" in href))
                            if is_valid_direct:
                                # الرابط شغال تماماً وقدرنا نجيب اللينك المباشر بتاعه
                                await browser.close()
                                return link_id, "valid", None, embed_url

                        await page.wait_for_timeout(5000)
                    except Exception:
                        continue

                # لو لفت اللفة كلها ومعرفتش تصطاد اللينك المباشر، نعتبره غير مستجيب أو تالف
                await browser.close()
                return link_id, "broken", "Failed to extract direct link", embed_url

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

    now = datetime.now().isoformat()
    
    # تحديث قاعدة البيانات بالنتائج الحتمية
    for link_id, status, error, url in results:
        try:
            supabase.rpc("increment_check_count", {"row_id": link_id}).execute()
            supabase.table("links").update({
                "last_check_status": status,
                "error_message":     error,
                "last_check_at":     now,
            }).eq("id", link_id).execute()
        except Exception as db_err:
            log(f"⚠️ خطأ أثناء تحديث قاعدة البيانات للرابط {link_id}: {str(db_err)}")
            
        icon = "✅" if status == "valid" else "❌"
        log(f"{icon} {link_id:<6} | MixDrop | {status:<8} | {error if error else 'No Errors'} | {url}")

if __name__ == "__main__":
    asyncio.run(run())