"""
repairer_voe.py — إصلاح روابط VOE المكسورة

الميزة: VOE بيدي file_code فوراً في رد الـ API
مش محتاجين polling طويل — نأخذ الـ file_code ونبني الرابط ونكتبه فوراً
(نستني polling بسيط 2-3 محاولات للتأكد إن الملف بدأ يتحمل فعلاً)
"""

import os
import asyncio
import httpx
from shared import supabase, log, find_source_url, update_link_in_db, mark_link_failed

VOE_API_KEY = os.getenv("VOE_API_KEY")
BATCH_SIZE  = int(os.getenv("BATCH_SIZE", "10"))

VOE_QUICK_POLLS   = 3
VOE_POLL_INTERVAL = 20


async def remote_upload_voe(client, source_url):
    log(f"   📡 [VOE] Remote Upload من: {source_url}")

    try:
        resp = await client.get(
            "https://voe.sx/api/upload/url",
            params={"key": VOE_API_KEY, "url": source_url},
            timeout=30.0,
        )
        data = resp.json()
        log(f"   📡 [VOE] رد API: {data}")
    except Exception as e:
        log(f"   ❌ [VOE] فشل الإرسال: {type(e).__name__}: {e}")
        return None

    if data.get("status") != 200:
        log(f"   ❌ [VOE] رفض | status={data.get('status')} msg={data.get('msg')}")
        return None

    file_code = data.get("result", {}).get("file_code")
    if not file_code:
        log(f"   ❌ [VOE] ما رجعش file_code! result={data.get('result')}")
        return None

    log(f"   ✅ [VOE] file_code={file_code} — تأكيد سريع...")

    for attempt in range(1, VOE_QUICK_POLLS + 1):
        await asyncio.sleep(VOE_POLL_INTERVAL)
        try:
            s_resp  = await client.get(
                "https://voe.sx/api/file/status",
                params={"key": VOE_API_KEY, "file_code": file_code},
                timeout=15.0,
            )
            s_data  = s_resp.json()
            status  = s_data.get("result", {}).get("status", "unknown")
            percent = s_data.get("result", {}).get("percent", 0)
            log(f"   🔄 [VOE] تأكيد {attempt}/{VOE_QUICK_POLLS} | {status} | {percent}%")

            if status in ("finished", "downloading", "processing", "converting", "queued"):
                log(f"   ✅ [VOE] مقبول ({status})")
                return file_code
        except Exception as e:
            log(f"   ⚠️ [VOE] خطأ تأكيد {attempt}: {e}")

    log(f"   ⚠️ [VOE] نكتب file_code على المسؤولية")
    return file_code


async def run():
    log("╔══════════════════════════════════════╗")
    log("║       🔧 VOE REPAIRER                ║")
    log(f"║  Batch: {BATCH_SIZE:<28}║")
    log("╚══════════════════════════════════════╝\n")

    res = (
        supabase.table("links")
        .select("id, episode_id, url, server_name")
        .ilike("server_name", "%voe%")
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
                mark_link_failed(link_id, "No archive/telegram_direct source found")
                stats["no_source"] += 1
                continue

            file_code = await remote_upload_voe(client, source)
            if not file_code:
                mark_link_failed(link_id, f"VOE upload failed from: {source}")
                stats["failed"] += 1
                continue

            new_url = f"https://voe.sx/e/{file_code}"
            if update_link_in_db(link_id, old_url, new_url):
                stats["fixed"] += 1
                log(f"   🎉 تم! {new_url}")
            else:
                stats["failed"] += 1

            await asyncio.sleep(3)

    log(f"\n{'═'*55}")
    log(f"📊 ✅ {stats['fixed']} | ⚠️ بدون مصدر: {stats['no_source']} | ❌ فشل: {stats['failed']}")
    log(f"{'═'*55}")


if __name__ == "__main__":
    asyncio.run(run())