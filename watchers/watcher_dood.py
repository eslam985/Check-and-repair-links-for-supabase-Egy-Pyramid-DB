"""
watcher_dood.py — فحص روابط Doodstream فقط
منطق: /api/file/info → status 200 + result → valid
"""

import os
import asyncio
import httpx
from datetime import datetime
from shared import supabase, log

DOOD_API_KEY = os.getenv("DOOD_API_KEY")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "200"))
sem = asyncio.Semaphore(1)

DOOD_DOMAINS = [ "doodapi.co", "doodapi.com", "dood.stream", "myvidplay.com","playmogo.com",]


async def check_dood(client, link_id, url, server_name):
    async with sem:
        try:
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

            # 1. الحكم الصارم والمطلق: فحص البودي الفعلي للصفحة HTML
            try:
                # نستخدم نفس الدومين الممرر في الرابط (مثل myvidplay.com أو playmogo.com) مع التأكد من وجود /e/
                check_url = url if "/e/" in url else url.replace(f"/{file_code}", f"/e/{file_code}")
                
                # ترويسات قوية ومكتملة تماماً لمحاكاة متصفح حقيقي وتخطي فلاتر الحظر الصامتة
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.5",
                }
                
                page_resp = await client.get(check_url, headers=headers, timeout=12.0)
                
                if page_resp.status_code == 200:
                    page_text = page_resp.text.lower()
                    
                    if (
                        "no_video_3.svg" in page_text
                        or "video you are looking for is not found" in page_text
                        or "not found" in page_text
                    ):
                        log(f"   ❌ [Dood HTML] تأكيد حتمي: الرابط محذوف وميت (Not Found) لـ {file_code}")
                        return link_id, "broken", "Dood: Video not found! Deleted from server", server_name, url
                    
                    # إذا فتحت الصفحة بنجاح ولم نجد عبارات الحذف، والصفحة تحتوي على وسم الفيديو أو داتا التحميل
                    if "video" in page_text or "download" in page_text or "length" in page_text:
                        return link_id, "valid", None, server_name, url

            except Exception as html_err:
                log(f"   ⚠️ [Dood HTML] فشل كشط البودي بسبب حماية الشبكة: {html_err} — جاري محاولة الفحص الاحتياطي عبر الـ API")

            # 2. الفحص الاحتياطي (Fallback): الـ API يُستخدم فقط إذا فشل جلب الـ HTML تماماً
            for domain in DOOD_DOMAINS:
                try:
                    res = await client.get(
                        f"https://{domain}/api/file/info?key={DOOD_API_KEY}&file_code={file_code}",
                        timeout=10.0,
                    )
                    if res.status_code != 200:
                        continue
                        
                    data = res.json()
                    if data.get("status") == 200:
                        file_info = data.get("result")
                        
                        if isinstance(file_info, list) and len(file_info) > 0:
                            file_info = file_info[0]
                            
                        if isinstance(file_info, dict) and file_info:
                            file_status = str(file_info.get("status", ""))
                            # لو الـ API نطق صراحة بالحذف
                            if file_status in ["Deleted", "Removed"]:
                                return link_id, "broken", "Dood: Deleted by API status", server_name, url
                            
                            # لولا الحذف الصريح، نعتبره سليم كملجأ أخير فقط
                            return link_id, "valid", None, server_name, url
                except Exception:
                    continue

            # إذا فشل فحص الـ HTML وفشل الـ API في إيجاد داتا، نعتبره مكسوراً حمايةً للنظام
            return link_id, "broken", "Dood: Unverifiable link, fallback to broken", server_name, url

        except Exception as e:
            return link_id, "broken", f"Dood Error: {e}", server_name, url

async def run():
    log(f"🔍 [Dood Watcher] فحص أقدم {BATCH_SIZE} رابط...")
    res = (
        supabase.table("links")
        .select("id, url, server_name")
        .ilike("server_name", "%dood%")
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
        tasks = [check_dood(client, l["id"], l["url"], l["server_name"]) for l in links]
        results = await asyncio.gather(*tasks)

    now = datetime.now().isoformat()
    for link_id, status, error, server_name, url in results:
        try:
            supabase.rpc("increment_check_count", {"row_id": link_id}).execute()
            supabase.table("links").update(
                {
                    "last_check_status": status,
                    "error_message": error,
                    "last_check_at": now,
                }
            ).eq("id", link_id).execute()
        except Exception:
            pass
        icon = "✅" if status == "valid" else "❌"
        log(f"{icon} {link_id:<6} | {server_name:<12} | {status:<8} | {url}")


if __name__ == "__main__":
    asyncio.run(run())
