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
            await asyncio.sleep(1)

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
                # قبول كود 200 و 403 لأن دودستريم يرجع 403 للملفات المحذوفة
                if page_resp.status_code in [200, 403]:
                    page_text = page_resp.text.lower()
                    body_length = len(page_text) # تعريف المتغير مبكراً لمنع خطأ الـ NameError حتماً
                    
                    # 1. فحص جدار الحماية أولاً
                    if "just a moment" in page_text or "cloudflare" in page_text:
                        log(f"   ⚠️ [Dood HTML] تم كشف جدار الحماية (Cloudflare)! الـ HTML مضلل، جاري التخطّي المباشر للـ API لـ {file_code}")
                        # هنا نترك البلوك فارغاً، لكن السطور بالأسفل ستحمي السكريبت من اعتباره سليماً وتدفعه للـ API فوراً
                        
                    # 2. الفحص القاطع للملفات المحذوفة (إذا لم نكن داخل جدار الحماية)
                    elif (
                        "no_video" in page_text
                        or "not found" in page_text
                        or "looking for is not found" in page_text
                    ):
                        log(f"   ❌ [Dood HTML] تم الإمساك بالرابط الميت حتماً (كود {page_resp.status_code}): {file_code}")
                        return link_id, "broken", f"Dood: Video not found on HTML page ({page_resp.status_code})", server_name, url

                    # 3. الفحص الإيجابي للروابط السليمة (فقط إذا لم يكن حماية ولم يكن ميت حتماً)
                    else:
                        log(f"   📊 [Dood HTML] تم جلب البودي بنجاح لـ {file_code} | الحجم: {body_length} حرف")
                        log(f"   🔍 [Dood Debug] بداية النص الراجع: {page_text[:300]}")
                        
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
                        file_info_list = data.get("result")
                        
                        # إذا كان الـ result فارغاً تماماً أو ليس قائمة، فالملف ميت حتماً
                        if not isinstance(file_info_list, list) or len(file_info_list) == 0:
                            return link_id, "broken", "Dood API: Empty or invalid result list", server_name, url
                            
                        file_info = file_info_list[0]
                        
                        if isinstance(file_info, dict) and file_info:
                            # باقي الكود الخاص بالشروط (1 و 2 و 3) يكمل هنا بشكل طبيعي...
                            file_status = file_info.get("status")
                            
                            # 1. الشرط الأول: الإمساك بالرابط الميت حتماً (النص الصريح المضلل)
                            if file_status == "Not found or not your file" or str(file_status) in ["Deleted", "Removed", "404"]:
                                return link_id, "broken", f"Dood API: {file_status}", server_name, url
                            
                            # 2. الشرط الثاني القاطع: التحقق من بنية الفيديو الشغال (الوجود الفعلي للملف)
                            # الفيديو السليم يحتوي حتماً على حجم وعنوان وقابلية تشغيل، حتى لو اختلفت الـ status بين رقم ونص
                            has_size = "size" in file_info or "length" in file_info
                            has_title = "title" in file_info
                            can_play = file_info.get("canplay") == 1 or str(file_info.get("canplay")) == "1"
                            
                            if (file_status == 200 or str(file_status) == "200" or file_status is None) and has_size and has_title:
                                return link_id, "valid", None, server_name, url
                            
                            # 3. إذا لم يطابق شروط الفيديو الشغال
                            return link_id, "broken", f"Dood API: Missing file metadata (status: {file_status})", server_name, url
                except Exception:
                    continue

            return link_id, "broken", "Dood: Unverifiable link, HTML failed and API empty", server_name, url

        except Exception as e:
            return link_id, "broken", f"Dood General Error: {e}", server_name, url
        
        
async def run():
    log(f"🔍 [Dood Watcher] فحص أقدم {BATCH_SIZE} رابط...")
    res = (
        supabase.table("links")
        .select("id, url, server_name, last_check_status, created_at, last_check_at, check_count")
        .ilike("server_name", "%dood%")
        .eq("is_fixed", False)
        .or_("last_check_status.in.(\"pending\",\"valid\"),url.ilike.%disabled%")
        
        # --- خوارزمية الترتيب متعدد المستويات لسيرفر Dood ---
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
        tasks = [check_dood(client, l["id"], l["url"], l["server_name"]) for l in links]
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