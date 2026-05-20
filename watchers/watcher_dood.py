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
            # 1. إضافة سليب بسيط (ثانية ونصف) لتفادي حظر السيرفر واعتباره هجوم
            await asyncio.sleep(1.5)

            clean = url.strip().rstrip("/").split("?")[0]
            parts = clean.split("/")
            
            domain_matched = "doodstream.com"
            for p in parts:
                if "." in p and not p.startswith("http"):
                    domain_matched = p
                    break

            file_code = None
            for marker in ("e", "d", "f"):
                if marker in parts:
                    idx = parts.index(marker)
                    if idx + 1 < len(parts):
                        file_code = parts[idx + 1]
                        break
            if not file_code:
                file_code = parts[-1]

            # 2. فحص البودي بطريقة GET
            # 2. فحص البودي بطريقة GET
            try:
                check_url = url if "/e/" in url else url.replace(f"/{file_code}", f"/e/{file_code}")
                
                # ترويسات متطابقة تماماً مع متصفح كروم حقيقي لتفادي 403
                # ترويسات متناسقة 100% مع بصمة متصفح الموبايل الخاص بك لمنع الـ 403
                headers = {
                    "User-Agent": "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Mobile Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                    "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
                    "Referer": f"https://{domain_matched}/",
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache",
                    "Priority": "u=0, i",
                    "Sec-Ch-Ua": '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
                    "Sec-Ch-Ua-Mobile": "?1",
                    "Sec-Ch-Ua-Platform": '"Android"',
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "cross-site",
                    "Sec-Fetch-User": "?1",
                    "Upgrade-Insecure-Requests": "1"
                }
                # تفعيل تتبع التحويلات (follow_redirects=True) لأن 403 أحياناً تأتي من تحويل خاطئ
                page_resp = await client.get(check_url, headers=headers, timeout=15.0, follow_redirects=True)
                
                # قبول كود 200 و 403 لأن دودستريم يرجع 403 للملفات المحذوفة
                if page_resp.status_code in [200, 403]:
                    page_text = page_resp.text.lower()
                    
                    # الفحص القاطع للملفات المحذوفة بناءً على البنية الراجعة
                    if (
                        "no_video_3.svg" in page_text
                        or "video you are looking for is not found" in page_text
                        or "video not found" in page_text
                        or "not found" in page_text
                    ):
                        log(f"   ❌ [Dood HTML] تم الإمساك بالرابط الميت حتماً (كود {page_resp.status_code}): {file_code}")
                        return link_id, "broken", f"Dood: Video not found on HTML page ({page_resp.status_code})", server_name, url

                    body_length = len(page_text)
                    log(f"   📊 [Dood HTML] تم جلب البودي بنجاح لـ {file_code} | الحجم: {body_length} حرف")
                    
                    if body_length < 500:
                        log(f"   ⚠️ [Dood HTML] البودي مشكوك فيه لـ {file_code} — جاري التحويل للـ API")
                    else:
                        if "video" in page_text or "download" in page_text or "length" in page_text:
                            log(f"   💚 [Dood HTML] الرابط سليم ومفتوح بالـ HTML: {file_code}")
                            return link_id, "valid", None, server_name, url
                else:
                    log(f"   ⚠️ [Dood HTML] السيرفر رجع كود {page_resp.status_code} للرابط {file_code}")

            except Exception as html_err:
                log(f"   ⚠️ [Dood HTML] فشل الكشط (إيرور شبكة): {html_err} | كود: {file_code}")

            # 3. الفحص الاحتياطي عبر الـ API (الملجأ الأخير)
            log(f"   🔄 [Dood API] جاري الاضطرار لفحص الـ API للملف: {file_code}")
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
                            if file_status in ["Deleted", "Removed"]:
                                return link_id, "broken", "Dood: Deleted by API status", server_name, url
                            
                            return link_id, "valid", None, server_name, url
                except Exception:
                    continue

            return link_id, "broken", "Dood: Unverifiable link, HTML failed and API empty", server_name, url

        except Exception as e:
            return link_id, "broken", f"Dood General Error: {e}", server_name, url
        
        
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
