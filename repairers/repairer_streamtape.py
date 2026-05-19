"""
repairer_streamtape.py — إصلاح روابط Streamtape المكسورة

المنطق:
1. remotedl/add → يرجع remote_id فوراً
2. نستني remotedl/status → لما يظهر extid نبني الرابط ونكتبه
3. extid هو الـ file_code النهائي للمشاهدة
"""

import os
import asyncio
import urllib.parse
import httpx
from shared import supabase, log, find_source_url, update_link_in_db, mark_link_failed

STREAMTAPE_API_KEY = os.getenv("STREAMTAPE_API_KEY")
STREAMTAPE_LOGIN   = os.getenv("STREAMTAPE_LOGIN") # أضف هذا
MIXDROP_EMAIL      = os.getenv("MIXDROP_EMAIL", "")
BATCH_SIZE         = int(os.getenv("BATCH_SIZE", "5"))

POLL_INTERVAL = 20
POLL_MAX      = 30



async def is_archive_url_valid(client: httpx.AsyncClient, url: str) -> bool:
    """يفحص إذا كان رابط آرشيف يحتوي على جملة تفيد بحذفه أو إغلاقه"""
    if "archive.org" not in url:
        return True
    try:
        log(f"   🔎 [Source] جاري فحص سلامة السورس المختار...")
        resp = await client.get(url, timeout=15.0)
        if resp.status_code == 200 and "Item not available" in resp.text:
            return False
        return True
    except Exception as e:
        log(f"   ⚠️ [Source] خطأ أثناء فحص الرابط: {e}")
        return False

async def remote_upload_streamtape(client, source_url, file_name="video.mp4"):
    login = STREAMTAPE_LOGIN # الاستخدام المباشر للـ Login الصحيح
    log(f"   📡 [ST] Remote Upload | login={login}")
    log(f"   📡 [ST] source: {source_url}")

    safe_name = urllib.parse.quote(file_name)
    add_url   = (
        f"https://api.streamtape.com/remotedl/add"
        f"?login={login}&key={STREAMTAPE_API_KEY}"
        f"&url={urllib.parse.quote(source_url, safe='')}&name={safe_name}"
    )

    try:
        resp = await client.get(add_url, timeout=30.0)
        data = resp.json()
        log(f"   📡 [ST] رد API: {data}")
    except Exception as e:
        log(f"   ❌ [ST] فشل الإرسال: {e}")
        return None

    if data.get("status") != 200:
        log(f"   ❌ [ST] رفض: {data}")
        return None

    remote_id = data.get("result", {}).get("id")
    if not remote_id:
        log(f"   ❌ [ST] ما رجعش remote_id!")
        return None

    log(f"   ⏳ [ST] remote_id={remote_id} | بدأ Polling...")

    for attempt in range(1, POLL_MAX + 1):
        await asyncio.sleep(POLL_INTERVAL)
        try:
            s_resp    = await client.get(
                f"https://api.streamtape.com/remotedl/status"
                f"?login={login}&key={STREAMTAPE_API_KEY}&id={remote_id}",
                timeout=15.0,
            )
            s_data    = s_resp.json()
            task_info = s_data.get("result", {}).get(remote_id, {})
            log(f"   🔄 [ST] محاولة {attempt}/{POLL_MAX} | task={task_info.get('status')} | url={task_info.get('url', '')[:40]}")

            if task_info.get("url"):
                # نستخدم extid — هو الـ file_code النهائي
                extid = task_info.get("extid")
                if not extid:
                    # نستخرجه من الـ url لو مش موجود
                    raw_url = task_info.get("url", "")
                    if "/v/" in raw_url:
                        extid = raw_url.split("/v/")[1].split("/")[0]

                if extid:
                    log(f"   ✅ [ST] extid={extid} — نبني الرابط")
                    return extid

        except Exception as e:
            log(f"   ⚠️ [ST] خطأ polling {attempt}: {e}")

    log(f"   🛑 [ST] انتهت المحاولات")
    return None


async def run():
    log("╔══════════════════════════════════════╗")
    log("║    🔧 STREAMTAPE REPAIRER            ║")
    log(f"║  Batch: {BATCH_SIZE:<28}║")
    log("╚══════════════════════════════════════╝\n")

    res = (
        supabase.table("links")
        .select("id, episode_id, url, server_name, episodes(id, media_id, episode_number)")
        .ilike("server_name", "%streamtape%")
        .eq("last_check_status", "broken")
        .eq("is_fixed", False)
        .order("last_check_at", desc=False, nullsfirst=True)
        .limit(BATCH_SIZE)
        .execute()
    )
    broken = res.data or []
    log(f"🔴 روابط مكسورة: {len(broken)}\n")

    if not broken:
        log("✅ لا يوجد شيء للإصلاح!")
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

            # === التعديل الجديد: جلب وفحص السورس مدمجاً مع التسمية الذكية ===
            log(f"   🔎 [Source] بيدور على archive/telegram لـ episode_id={episode_id}")
            
            # 1. جلب المصادر الصالحة مباشرة من الداتابيز
            res_sources = (
                supabase.table("links")
                .select("url, server_name")
                .eq("episode_id", episode_id)
                .in_("server_name", ["archive", "telegram_direct"])
                .eq("last_check_status", "good")
                .execute()
            )
            sources_list = res_sources.data or []
            log(f"   🔎 [Source] النتائج: {sources_list}")

            if not sources_list:
                mark_link_failed(link_id, "No active archive/telegram_direct source found in DB")
                stats["no_source"] += 1
                continue

            # ترتيب المصادر لتقديم archive أولاً
            sources_list.sort(key=lambda x: 0 if x["server_name"] == "archive" else 1)
            
            selected_source = sources_list[0]
            source_url = selected_source["url"]
            log(f"   ✅ [Source] اختار: {selected_source['server_name']} → {source_url}")

            # 2. الفحص المسبق لروابط آرشيف والتحويل لتليجرام إذا لزم الأمر
            if selected_source["server_name"] == "archive":
                is_valid = await is_archive_url_valid(client, source_url)
                if not is_valid:
                    log(f"   ❌ [Source] رابط Archive تالف ومحذوف! جاري البحث عن البديل...")
                    tg_source = next((s for s in sources_list if s["server_name"] == "telegram_direct"), None)
                    if tg_source:
                        source_url = tg_source["url"]
                        log(f"   ✅ [Source] تم التحويل إلى السورس البديل: telegram_direct → {source_url}")
                    else:
                        mark_link_failed(link_id, "Archive source is dead and no telegram_direct backup found")
                        stats["failed"] += 1
                        continue

            # 3. منطق التسمية الذكية
            ep_data = link.get("episodes") or {}
            e_id    = ep_data.get("id", "Unknown")
            m_id    = ep_data.get("media_id", "Unknown")
            e_num   = ep_data.get("episode_number", 0)

            if e_num in [0, 1]:
                generated_name = f"Media-{m_id}-ID-{e_id}.mp4"
            else:
                generated_name = f"Media-{m_id}-Ep-{e_num}-ID-{e_id}.mp4"

            log(f"   📝 [ST] التسمية الجديدة: {generated_name}")
            
            # 4. إرسال السورس النظيف المختار والاسم المولد لـ Streamtape
            extid = await remote_upload_streamtape(client, source_url, file_name=generated_name)
# ===================================================================

            if not extid:
                mark_link_failed(link_id, f"Streamtape upload failed from: {source_url}")
                stats["failed"] += 1
                continue

            new_url = f"https://streamtape.com/e/{extid}"
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