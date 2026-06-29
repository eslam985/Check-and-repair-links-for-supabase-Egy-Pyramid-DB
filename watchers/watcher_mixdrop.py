# /media/es/DDrive/projects/apps-python/Check-and-repair-links-for-supabase-Egy-Pyramid-DB/watchers/watcher_mixdrop.py
import os
import asyncio
from datetime import datetime
import httpx
from shared import supabase, log

BATCH_SIZE = int(os.getenv("BATCH_SIZE", "50"))

# إعدادات واجهة برمجة التطبيقات لـ MixDrop (قم بضبط المتغيرات في البيئة أو كتابتها هنا مباشرة)
MIXDROP_EMAIL = os.getenv("MIXDROP_EMAIL", "your_email@example.com")
MIXDROP_API_KEY = os.getenv("MIXDROP_API_KEY", "your_api_key")

def extract_fileref(url):
    """
    دالة مساعدة لاستخراج المعرف الفريد للملف (fileref) من الرابط بأمان
    """
    for part in ["/f/", "/e/"]:
        if part in url:
            return url.split(part)[1].split("?")[0].strip()
    return None

async def check_mixdrop_batch(links):
    """
    تفحص حتى 50 رابطاً دفعة واحدة عبر الـ API لضمان اليقين الكامل للحالة
    """
    results = []
    
    # تجهيز المعاملات الأساسية للطلب الجماعي
    params = [
        ("email", MIXDROP_EMAIL),
        ("key", MIXDROP_API_KEY)
    ]
    
    # ربط المعرف الفريد ببيانات الحقل الأصلي لاسترجاعه عند معالجة النتيجة
    ref_to_link = {}
    for l in links:
        ref = extract_fileref(l["url"])
        if ref:
            params.append(("ref[]", ref))
            ref_to_link[ref] = l
        else:
            # إذا كان الرابط بتنسيق خاطئ، يُحول لـ broken مباشرة لعدم قابلية معالجته
            results.append((l["id"], "broken", "INVALID_URL_FORMAT", l["url"]))

    if not ref_to_link:
        return results

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get("https://api.mixdrop.ag/fileinfo2", params=params)
            
            if response.status_code != 200:
                raise Exception(f"HTTP_ERROR_{response.status_code}")
                
            data = response.json()
            if not data.get("success"):
                raise Exception(f"API_REJECTED: {data.get('result', 'Unknown error')}")
                
            api_results = data.get("result", {})
            
            for ref, link_data in ref_to_link.items():
                file_info = api_results.get(ref)
                
                # إذا لم يُرجع السيرفر أي بيانات لهذا المعرف، نضعه pending لإعادة المحاولة حتماً
                if not file_info:
                    results.append((link_data["id"], "pending", "API_MISSING_REF_DATA", link_data["url"]))
                    continue
                    
                status = file_info.get("status")
                is_deleted = file_info.get("deleted", False)
                
                # التحقق الصارم والمشروط من الحالة المطلوبة
                if status == "OK" and not is_deleted:
                    results.append((link_data["id"], "valid", None, link_data["url"]))
                elif status == "notfound" or is_deleted:
                    results.append((link_data["id"], "broken", "404_DELETED", link_data["url"]))
                else:
                    # أي حالة أخرى غير مستقرة (Uploading | Convert Queue | Converting | Completing)
                    # يتم إعطاؤها حالة pending فوراً ليتم جدولتها بأولوية مرتفعة لاحقاً
                    results.append((link_data["id"], "pending", f"STAGING_STATUS_{status.upper()}", link_data["url"]))
                    
    except Exception as e:
        log(f"❌ [API Connection Error] فشل الاتصال بالسيرفر: {str(e)}")
        # حماية البيانات: في حال سقوط الـ API أو حدوث تيم-أوت، نحول الدفعة كاملة إلى pending
        for ref, link_data in ref_to_link.items():
            results.append((link_data["id"], "pending", f"API_FETCH_FAILED: {str(e)}", link_data["url"]))
            
    return results


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

    # تشغيل الفحص الجماعي الذكي فائق السرعة عبر الـ API
    results = await check_mixdrop_batch(links)

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