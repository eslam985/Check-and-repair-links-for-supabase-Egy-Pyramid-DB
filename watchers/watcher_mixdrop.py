# /media/es/DDrive/projects/apps-python/Check-and-repair-links-for-supabase-Egy-Pyramid-DB/watchers/watcher_mixdrop.py
import os
import asyncio
from datetime import datetime
import httpx
from shared import supabase, log

BATCH_SIZE = int(os.getenv("BATCH_SIZE", "50"))

# إعدادات واجهة برمجة التطبيقات لـ MixDrop (قم بضبط المتغيرات في البيئة أو كتابتها هنا مباشرة)
MIXDROP_EMAIL = os.getenv("MIXDROP_EMAIL")
MIXDROP_API_KEY = os.getenv("MIXDROP_KEY")

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
    تفحص الروابط عبر الـ API مع تقسيمها تلقائياً إلى مجموعات 
    لا تتعدى 50 ملفاً لكل طلب لتفادي قيود السيرفر.
    """
    results = []
    
    # تقسيم الروابط إلى مجموعات، كل مجموعة تحتوي على 50 رابطاً كحد أقصى
    for chunk_index in range(0, len(links), 50):
        chunk_links = links[chunk_index:chunk_index + 50]
        
        # تجهيز المعاملات الأساسية للمجموعة الحالية
        params = [
            ("email", MIXDROP_EMAIL),
            ("key", MIXDROP_API_KEY)
        ]
        
        ref_to_link = {}
        for l in chunk_links:
            ref = extract_fileref(l["url"])
            if ref:
                params.append(("ref[]", ref))
                ref_to_link[ref] = l
            else:
                results.append((l["id"], "broken", "INVALID_URL_FORMAT", l["url"]))

        if not ref_to_link:
            continue

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get("https://api.mixdrop.ag/fileinfo2", params=params)
                
                if response.status_code != 200:
                    raise Exception(f"HTTP_ERROR_{response.status_code}")
                    
                data = response.json()
                if not data.get("success"):
                    # استخراج رسالة الرفض سواء كانت داخل حقل result أو msg
                    error_detail = data.get("result", data.get("msg", data))
                    raise Exception(f"API_REJECTED: {error_detail}")
                    
                api_results = data.get("result", {})
                
                for ref, link_data in ref_to_link.items():
                    file_info = api_results.get(ref)
                    
                    if not file_info:
                        results.append((link_data["id"], "pending", "API_MISSING_REF_DATA", link_data["url"]))
                        continue
                        
                    status = file_info.get("status")
                    is_deleted = file_info.get("deleted", False)
                    
                    if status == "OK" and not is_deleted:
                        results.append((link_data["id"], "valid", None, link_data["url"]))
                    elif status == "notfound" or is_deleted:
                        results.append((link_data["id"], "broken", "404_DELETED", link_data["url"]))
                    else:
                        results.append((link_data["id"], "pending", f"STAGING_STATUS_{status.upper()}", link_data["url"]))
                        
        except Exception as e:
            log(f"❌ [API Chunk Error] فشل فحص مجموعة من الروابط: {str(e)}")
            for ref, link_data in ref_to_link.items():
                results.append((link_data["id"], "pending", f"API_FETCH_FAILED: {str(e)}", link_data["url"]))
                
    return results


async def run():
    log(f"🔍 [MixDrop Watcher] جلب أقدم {BATCH_SIZE} رابط خاص بـ MixDrop لفحصها...")

    res = (
        supabase.table("links")
        .select("id, url, server_name, last_check_status, created_at, last_check_at, check_count")
        .ilike("server_name", "%mixdrop%")
        .or_("last_check_status.in.(\"pending\",\"valid\"),url.ilike.%disabled%,is_fixed.eq.true")
        
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
        update_data = {
            "id": link_id,               
            "url": url,                  
            "server_name": server_name,  
            "last_check_status": status,
            "error_message":     error,
            "last_check_at":     now,
        }

        # إذا ثبت أن الرابط المصلح قد كُسر مجدداً، نقوم بإلغاء علامة الإصلاح ليعود لإسكربت الصيانة
        if status == "broken":
            update_data["is_fixed"] = False

        bulk_updates.append(update_data)

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