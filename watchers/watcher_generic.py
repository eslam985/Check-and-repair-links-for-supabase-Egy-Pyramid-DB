"""
watcher_generic.py — فحص الروابط العامة (VK, archive, telegram_direct, mixdrop, download)
منطق: HTTP GET → 200/206 = valid
مع كشف روابط telegram_direct الناقصة (missing hash)
"""

import os
import asyncio
import httpx
from datetime import datetime
from shared import supabase, log

BATCH_SIZE = int(os.getenv("BATCH_SIZE", "200"))
sem        = asyncio.Semaphore(5)

# السيرفرات اللي هيتعامل معاها الـ generic
GENERIC_SERVERS = ["vk", "archive", "telegram_direct", "mixdrop", "download"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer":    "https://egypyramid.vercel.app/",
}


async def check_generic(client, link_id, url, server_name):
    # كشف روابط telegram_direct الناقصة (hf.space بدون hash)
    if "hf.space" in url and "hash=" not in url:
        return link_id, "broken", "Missing Hash Param", server_name, url

    async with sem:
        try:
            resp = await client.get(url, headers=HEADERS, follow_redirects=True, timeout=20.0)
            if resp.status_code in (200, 206):
                return link_id, "valid", None, server_name, url

            # Retry واحدة
            await asyncio.sleep(2)
            retry = await client.get(url, headers=HEADERS, follow_redirects=True, timeout=15.0)
            if retry.status_code in (200, 206):
                return link_id, "valid", None, server_name, url

            return link_id, "broken", f"HTTP {resp.status_code}", server_name, url
        except Exception as e:
            return link_id, "broken", str(e), server_name, url


def _build_filter():
    """بيبني فلتر OR لجلب السيرفرات الـ generic من الداتابيز"""
    # Supabase Python: استخدام or_ filter
    conditions = ",".join([f"server_name.ilike.%{s}%" for s in GENERIC_SERVERS])
    return conditions


async def run():
    log(f"🔍 [Generic Watcher] فحص أقدم {BATCH_SIZE} رابط (VK/Archive/Telegram/Mixdrop/Download)...")

    # نجيب الروابط اللي server_name مش فيه voe ولا dood ولا streamtape ولا lulu
    res = (
        supabase.table("links")
        .select("id, url, server_name")
        .not_.ilike("server_name", "%voe%")
        .not_.ilike("server_name", "%dood%")
        .not_.ilike("server_name", "%streamtape%")
        .not_.ilike("server_name", "%lulu%")
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
        tasks   = [check_generic(client, l["id"], l["url"], l["server_name"]) for l in links]
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