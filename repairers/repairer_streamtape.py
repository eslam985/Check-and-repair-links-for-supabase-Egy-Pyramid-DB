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
        .select("id, episode_id, url, server_name")
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

            source = find_source_url(episode_id)
            if not source:
                mark_link_failed(link_id, "No source found")
                stats["no_source"] += 1
                continue

            extid = await remote_upload_streamtape(client, source)
            if not extid:
                mark_link_failed(link_id, f"Streamtape upload failed from: {source}")
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