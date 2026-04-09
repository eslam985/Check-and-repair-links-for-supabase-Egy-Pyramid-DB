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
            if not file_code:
                file_code = parts[-1]

            login = get_login()

            # الطريقة الصحيحة: file/info مع login + key + file (مش file_code)
            api_url = (
                f"https://api.streamtape.com/file/info"
                f"?login={login}&key={STREAMTAPE_API_KEY}&file={file_code}"
            )
            res  = await client.get(api_url, timeout=12.0)
            data = res.json()

            if data.get("status") == 200:
                result = data.get("result", {})
                # result هي dict مفتاحها file_code
                file_info = result.get(file_code, {})
                # الفحص الدقيق: التأكد أن الملف له حجم وحالته ليست "محذوف"
                if file_info and file_info.get("size") is not None:
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
        .select("id, url, server_name")
        .ilike("server_name", "%streamtape%")
        .eq("is_fixed", False)
        .order("last_check_at", desc=False, nullsfirst=True)
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

    now = datetime.now().isoformat()
    for link_id, status, error, server_name, url in results:
        try:
            supabase.rpc("increment_check_count", {"row_id": link_id}).execute()
            supabase.table("links").update({
                "last_check_status": status,
                "error_message":     error,
                "last_check_at":     now,
            }).eq("id", link_id).execute()
        except Exception:
            pass
        icon = "✅" if status == "valid" else "❌"
        log(f"{icon} {link_id:<6} | {server_name:<12} | {status:<8} | {url}")


if __name__ == "__main__":
    asyncio.run(run())