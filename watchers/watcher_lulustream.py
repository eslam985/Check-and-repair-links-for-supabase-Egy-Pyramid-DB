"""
watcher_lulustream.py — فحص روابط LuluStream فقط

المشكلة القديمة: كان بيستخدم check_generic (HTTP GET) على رابط الـ embed
وده بيرجع 200 حتى لو الملف محذوف لأن صفحة الـ embed بتتحمل دايماً.

المنطق الصحيح:
استخدم LuluStream API → /api/file/info?key=...&file_code=...
لو status=200 ورجع result → valid
غير كده → broken
"""

import os
import asyncio
import httpx
from datetime import datetime
from shared import supabase, log

LULUSTREAM_API_KEY = os.getenv("LULUSTREAM_API_KEY")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "50"))
sem = asyncio.Semaphore(1)


async def check_lulustream(client, link_id, url, server_name):
    async with sem:
        try:
            # استخراج file_code — يدعم /e/CODE و /d/CODE
            clean = url.strip().rstrip("/").split("?")[0]
            parts = clean.split("/")
            file_code = None
            for marker in ("e", "d", "f"):
                if marker in parts:
                    idx = parts.index(marker)
                    if idx + 1 < len(parts):
                        file_code = parts[idx + 1]
                        break
            if not file_code:
                file_code = parts[-1]

            await asyncio.sleep(1.0)

            api_url = f"https://www.lulustream.com/api/file/info?key={LULUSTREAM_API_KEY}&file_code={file_code}"
            res = await client.get(api_url, timeout=12.0)

            # 1. فحص كود الحالة أولاً لحماية الروابط من الحظر المؤقت
            if res.status_code in (403, 429, 503):
                return link_id, "pending", f"Rate Limited ({res.status_code})", server_name, url

            try:
                data = res.json()
            except Exception:
                return link_id, "pending", f"Invalid JSON ({res.status_code})", server_name, url

            # 2. إذا كانت استجابة الـ API سليمة، نقوم بالفحص المزدوج
            if data.get("status") == 200 and data.get("result"):
                embed_url = f"https://www.lulustream.com/e/{file_code}"
                html_res = await client.get(embed_url, timeout=8.0)
                
                # 1. التأكد أن صفحة الـ embed لم تعط حظراً صريحاً
                if html_res.status_code in (403, 429):
                    return link_id, "pending", "Embed Rate Limited", server_name, url

                # 2. كشف الـ Soft Rate Limit (إذا عادت الصفحة كود 200 ولكنها فارغة أو تالفة بسبب الخنق)
                # صفحات لولو السليمة (سواء محذوفة أو شغالة) تحتوي دائماً على وسم body أو html أو doctype
                if "html" not in html_res.text.lower() and "body" not in html_res.text.lower():
                    return link_id, "pending", "Soft Rate Limited (Corrupted HTML)", server_name, url

                # 3. الآن نقوم بالفحص الفعلي للمحتوى بعد التأكد من سلامة الصفحة
                if (
                    "File is no longer available" in html_res.text
                    or "has been deleted" in html_res.text
                ):
                    return (
                        link_id,
                        "broken",
                        "Lulu: Expired or Deleted (HTML Check)",
                        server_name,
                        url,
                    )

                return link_id, "valid", None, server_name, url

            return (
                link_id,
                "broken",
                f"Lulu: {data.get('msg', 'Not Found')}",
                server_name,
                url,
            )

        except Exception as e:
            return link_id, "broken", f"Lulu Error: {e}", server_name, url


async def run():
    log(f"🔍 [Lulustream Watcher] فحص أقدم {BATCH_SIZE} رابط...")
    res = (
        supabase.table("links")
        .select("id, url, server_name, last_check_status, created_at, last_check_at, check_count")
        .ilike("server_name", "%lulu%")
        .eq("is_fixed", False)
        .or_("last_check_status.in.(\"pending\",\"valid\"),url.ilike.%disabled%")
        
        # --- خوارزمية الترتيب متعدد المستويات لسيرفر lulu ---
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
        tasks = [
            check_lulustream(client, l["id"], l["url"], l["server_name"]) for l in links
        ]
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