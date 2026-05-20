"""
repairer_dood.py — إصلاح روابط Doodstream المكسورة

المنطق:
1. upload/url → يرجع filecode فوراً
2. نستني file/info → لما status 200 نبني الرابط
3. نجرب أكثر من domain تجنباً للحظر
"""

import os
import asyncio
import urllib.parse
import httpx
from shared import supabase, log, find_source_url, update_link_in_db, mark_link_failed

DOOD_API_KEY = os.getenv("DOOD_API_KEY")
BATCH_SIZE   = int(os.getenv("BATCH_SIZE", "5"))

POLL_INTERVAL = 20
POLL_MAX      = 30

DOOD_DOMAINS = ["doodapi.co", "doodapi.com", "dood.stream", "myvidplay.com"]


async def is_archive_url_valid(client: httpx.AsyncClient, url: str) -> bool:
    """يفحص بشكل صارم سلامة رابط آرشيف ويكتشف الحظر والحذف"""
    if "archive.org" not in url:
        return True
    try:
        log(f"   🔎 [Source] جاري فحص سلامة السورس المختار...")
        # نرسل طلب بمجموعة أخطاء مقبولة برمجياً لكي لا ينهار السكريبت وتلتقط الحالات المختلفة
        resp = await client.get(url, timeout=15.0)
        
        # 1. إذا أرجع السيرفر منع دخول أو مفقود (403 أو 404) فهو تالف فوراً
        if resp.status_code in [403, 404]:
            log(f"   ❌ [Source] فحص آرشيف فشل بكود حالة: {resp.status_code}")
            return False
            
        # 2. فحص محتوى الصفحة حتى لو رجعت بـ 200 أو أي كود آخر
        page_text = resp.text
        if "Item not available" in page_text or "The item is not available" in page_text:
            log(f"   ❌ [Source] فحص آرشيف اكتشف جملة الحذف (Item not available)")
            return False
            
        return True
    except Exception as e:
        log(f"   ⚠️ [Source] خطأ أثناء فحص الرابط: {e}")
        return False

async def remote_upload_dood(client, source_url, file_name="video.mp4"):
    log(f"   📡 [Dood] Remote Upload من: {source_url}")

    safe_title = urllib.parse.quote(file_name)
    data       = None

    for domain in DOOD_DOMAINS:
        try:
            add_url = (
                f"https://{domain}/api/upload/url"
                f"?key={DOOD_API_KEY}&url={urllib.parse.quote(source_url, safe='')}&new_title={safe_title}"
            )
            resp = await client.get(add_url, timeout=20.0)
            data = resp.json()
            log(f"   📡 [Dood] {domain}: {data}")
            if data.get("msg") == "OK":
                log(f"   ✅ [Dood] قبل الأمر عبر {domain}")
                break
        except Exception as e:
            log(f"   ⚠️ [Dood] {domain} فشل: {e}")
            continue

    if not data or data.get("msg") != "OK":
        log(f"   ❌ [Dood] كل الـ domains فشلت")
        return None

    f_code = data.get("result", {}).get("filecode")
    if not f_code:
        log(f"   ❌ [Dood] ما رجعش filecode!")
        return None

    log(f"   ⏳ [Dood] filecode={f_code} | Polling...")

    for attempt in range(1, POLL_MAX + 1):
        await asyncio.sleep(POLL_INTERVAL)
        log(f"   🔄 [Dood] محاولة {attempt}/{POLL_MAX}...")

        for domain in DOOD_DOMAINS:
            try:
                # 1. الفحص عبر api/file/info
                info_url = f"https://{domain}/api/file/info?key={DOOD_API_KEY}&file_code={f_code}"
                res = await client.get(info_url, timeout=10.0)
                info_data = res.json()

                if info_data.get("status") == 200:
                    result_list = info_data.get("result", [{}])
                    result = result_list[0] if isinstance(result_list, list) and result_list else {}
                    
                    # الفحص الذكي للمفتاحين (بشرطة وبدون شرطة) لضمان التطابق
                    resp_code = result.get("filecode") or result.get("file_code")
                    if resp_code == f_code:
                        log(f"   ✅ [Dood] Success: الملف موجود وبدأ المعالجة في محاولة {attempt}")
                        return f_code

                # 2. خط الدفاع الثاني: الفحص السريع عبر api/file/check
                try:
                    check_url = f"https://{domain}/api/file/check?key={DOOD_API_KEY}&file_code={f_code}"
                    c_res = await client.get(check_url, timeout=10.0)
                    check_data = c_res.json()

                    if check_data.get("status") == 200 and check_data.get("result"):
                        log(f"   ✅ [Dood] Success: تم تأكيد وجود الملف عبر Check في محاولة {attempt}")
                        return f_code
                except:
                    pass

            except Exception:
                continue

        # 3. خط الدفاع الثالث: البحث بالاسم المنسق في المحاولات الزوجية لتقليل الضغط
        if attempt % 2 == 0:
            try:
                list_url = f"https://doodapi.co/api/file/list?key={DOOD_API_KEY}&per_page=10"
                l_res = await client.get(list_url, timeout=10.0)
                files = l_res.json().get("result", {}).get("files", [])
                
                # تطهير الاسم للبحث به (مثل: link_14456_ep_2016)
                search_term = file_name.split(".")[0].strip()
                for f in files:
                    server_title = f.get("title", "")
                    if search_term in server_title:
                        found_code = f.get("file_code") or f.get("filecode")
                        log(f"   ✅ [Dood] Success: تم العثور على الملف بالاسم المتطابق: {server_title}")
                        return found_code
            except:
                pass

    log(f"   🛑 [Dood] انتهت المحاولات")
    return None


async def run():
    log("╔══════════════════════════════════════╗")
    log("║      🔧 DOODSTREAM REPAIRER          ║")
    log(f"║  Batch: {BATCH_SIZE:<28}║")
    log("╚══════════════════════════════════════╝\n")

    res = (
        supabase.table("links")
        .select("id, episode_id, url, server_name")
        .ilike("server_name", "%dood%")
        .eq("last_check_status", "broken")
        .eq("is_fixed", False)
        .order("last_check_at", desc=False, nullsfirst=True)
        .limit(BATCH_SIZE)
        .execute()
    )
    broken = res.data or []
    log(f"🔴 روابط مكسورة: {len(broken)}\n")

    if not broken:
        log("✅ لا يوجد شيء!")
        return

    stats = {"fixed": 0, "no_source": 0, "failed": 0}

    async with httpx.AsyncClient(verify=False, follow_redirects=True) as client:
        for i, link in enumerate(broken, 1):
            link_id    = link["id"]
            episode_id = link.get("episode_id")
            old_url    = link["url"]

            log(f"\n{'─'*55}")
            log(f"[{i}/{len(broken)}] link_id={link_id} | episode_id={episode_id}")
            log(f"   🔴 {old_url}")

            # === التعديل الجديد: فحص السورس والالتفاف التلقائي لـ Doodstream ===
            log(f"   🔎 [Source] بيدور على archive/telegram لـ episode_id={episode_id}")
            
            # 1. جلب المصادر المتاحة مباشرة من الجدول لضمان المرونة
            res_sources = (
                supabase.table("links")
                .select("url, server_name")
                .eq("episode_id", episode_id)
                .in_("server_name", ["archive", "telegram_direct"])
                .eq("last_check_status", "valid")
                .execute()
            )
            sources_list = res_sources.data or []
            log(f"   🔎 [Source] النتائج: {sources_list}")

            if not sources_list:
                await mark_link_failed(link_id, "No active archive/telegram_direct source found in DB")
                stats["no_source"] += 1
                continue

            # ترتيب المصادر لتقديم الـ archive أولاً كالعادة
            sources_list.sort(key=lambda x: 0 if x["server_name"] == "archive" else 1)
            
            selected_source = sources_list[0]
            source_url = selected_source["url"]
            log(f"   ✅ [Source] اختار: {selected_source['server_name']} → {source_url}")

            # 2. الفحص المسبق لروابط آرشيف والتحويل لتليجرام إذا كان معطوباً
            if selected_source["server_name"] == "archive":
                is_valid = await is_archive_url_valid(client, source_url)
                if not is_valid:
                    log(f"   ❌ [Source] رابط Archive تالف ومحذوف! جاري البحث عن البديل...")
                    tg_source = next((s for s in sources_list if s["server_name"] == "telegram_direct"), None)
                    if tg_source:
                        source_url = tg_source["url"]
                        log(f"   ✅ [Source] تم التحويل إلى السورس البديل: telegram_direct → {source_url}")
                    else:
                        await mark_link_failed(link_id, "Archive source is dead and no telegram_direct backup found")
                        stats["failed"] += 1
                        continue

            # 3. بناء اسم ديناميكي للملف وتمرير السورس النهائي النظيف إلى Doodstream
            dynamic_file_name = f"link_{link_id}_ep_{episode_id}.mp4"
            f_code = await remote_upload_dood(client, source_url, file_name=dynamic_file_name)
# ===================================================================
            if not f_code:
                await mark_link_failed(link_id, f"Dood upload failed from: {source_url}")
                stats["failed"] += 1
                continue

            new_url = f"https://myvidplay.com/e/{f_code}"
            if update_link_in_db(link_id, old_url, new_url):
                stats["fixed"] += 1
                log(f"   🎉 تم! {new_url}")
            else:
                stats["failed"] += 1

            await asyncio.sleep(3)

    log(f"\n{'═'*55}")
    log(f"📊 ✅ {stats['fixed']} | ⚠️ {stats['no_source']} | ❌ {stats['failed']}")
    log(f"{'═'*55}")


if __name__ == "__main__":
    asyncio.run(run())