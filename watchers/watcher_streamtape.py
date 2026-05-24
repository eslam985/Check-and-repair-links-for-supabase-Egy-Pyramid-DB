"""
watcher_streamtape.py — فحص روابط Streamtape فقط

المشكلة القديمة: كان بيستخدم file/info بالـ file_code مباشرة
وده مش الطريقة الصح لـ Streamtape.

المنطق الصحيح:
1. استخرج file_code من الرابط
2. استخدم /file/listfolder للحصول على قائمة الملفات
3. ابحث عن الـ linkid المطابق
"""

import os
import asyncio
import httpx
from datetime import datetime
from shared import supabase, log

STREAMTAPE_API_KEY = os.getenv("STREAMTAPE_API_KEY")
STREAMTAPE_LOGIN   = os.getenv("STREAMTAPE_LOGIN") # ضيف السطر ده
MIXDROP_EMAIL      = os.getenv("MIXDROP_EMAIL", "")
BATCH_SIZE         = int(os.getenv("BATCH_SIZE", "200"))
sem                = asyncio.Semaphore(3)


def get_login():
    return MIXDROP_EMAIL.split("@")[0] if MIXDROP_EMAIL else ""


async def check_streamtape(client, link_id, url, server_name):
    async with sem:
        try:
            # استخراج file_code من الرابط
            # يدعم: /e/CODE  /v/CODE  /CODE مباشرة
            clean = url.strip().rstrip("/").split("?")[0]
            parts = clean.split("/")
            file_code = None
            for marker in ("e", "v"):
                if marker in parts:
                    idx = parts.index(marker)
                    if idx + 1 < len(parts):
                        file_code = parts[idx + 1]
                        break
            # === التعديل الجديد: الفحص المباشر لمحتوى الصفحة لمنع الكاش والتأكد من الحذف ===
            if not file_code:
                file_code = parts[-1]

            # 1. فحص الصفحة مباشرة بالـ Scraper السريع للتأكد من نص الحذف المكتوب في البودي
            try:
                page_resp = await client.get(url, timeout=10.0)
                if page_resp.status_code == 200:
                    page_text = page_resp.text
                    if "Video not found!" in page_text or "Maybe it got deleted by the creator!" in page_text:
                        log(f"   ❌ [Streamtape HTML] الرابط ميت ومحذوف من السيرفر (Video not found) لـ {file_code}")
                        return link_id, "broken", "Streamtape: Video not found! Deleted by creator", server_name, url
            except Exception as e:
                log(f"   ⚠️ [Streamtape HTML] فشل فحص الصفحة المباشر: {e} — جاري الانتقال للـ API")

            # بدل login = get_login()
            login = STREAMTAPE_LOGIN
# =============================================================================
            # الطريقة الصحيحة: file/info مع login + key + file (مش file_code)
            api_url = (
                f"https://api.streamtape.com/file/info"
                f"?login={login}&key={STREAMTAPE_API_KEY}&file={file_code}"
            )
            res  = await client.get(api_url, timeout=12.0)
            data = res.json()
            print(f"DEBUG: Data for {file_code}: {data}") # ضيف السطر ده

            if data.get("status") == 200:
                result = data.get("result", {})
                
                # فحص مرن: لو الـ result جواه داتا، هناخد أول عنصر فيه بغض النظر عن الـ Key
                if isinstance(result, dict) and len(result) > 0:
                    # سحب أول قيمة (معلومات الملف) آلياً
                    file_info = next(iter(result.values()))
                    
                    # التأكد أن حالة الملف نفسه 200 (موجود) وله حجم
                    if file_info.get("status") == 200 and file_info.get("size") is not None:
                        return link_id, "valid", None, server_name, url

            # Fallback: /file/listfolder وابحث بالـ linkid
            list_res   = await client.get(
                f"https://api.streamtape.com/file/listfolder"
                f"?login={login}&key={STREAMTAPE_API_KEY}",
                timeout=12.0,
            )
            list_data  = list_res.json()
            files      = list_data.get("result", {}).get("files", [])
            for f in files:
                if f.get("linkid") == file_code:
                    return link_id, "valid", None, server_name, url

            return link_id, "broken", "Streamtape: Not Found", server_name, url

        except Exception as e:
            return link_id, "broken", f"Streamtape Error: {e}", server_name, url


async def run():
    log(f"🔍 [Streamtape Watcher] فحص أقدم {BATCH_SIZE} رابط...")
    res = (
        supabase.table("links")
        .select("id, url, server_name, last_check_status, created_at, last_check_at, check_count")
        .ilike("server_name", "%streamtape%")
        .eq("is_fixed", False)
        .or_("last_check_status.in.(\"pending\",\"valid\"),url.ilike.%disabled%")
        
        # --- خوارزمية الترتيب متعدد المستويات لسيرفر streamtape ---
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
        tasks   = [check_streamtape(client, l["id"], l["url"], l["server_name"]) for l in links]
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