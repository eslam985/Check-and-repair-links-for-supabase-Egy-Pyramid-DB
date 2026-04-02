"""
watcher_voe.py — فحص روابط VOE فقط
منطق: VOE API → file/info → status 200=valid / 404=broken
"""

import os
import asyncio
import httpx
from datetime import datetime
from shared import supabase, log

VOE_API_KEY = os.getenv("VOE_API_KEY")
BATCH_SIZE  = int(os.getenv("BATCH_SIZE", "200"))
sem         = asyncio.Semaphore(5)


async def check_voe(client, link_id, url, server_name):
    async with sem:
        try:
            clean = url.strip().rstrip("/")
            if clean.endswith("/download"):
                clean = clean[:-9]
            file_code = clean.split("/")[-1].split("?")[0]

            await asyncio.sleep(0.4)

            res  = await client.get(
                f"https://voe.sx/api/file/info?key={VOE_API_KEY}&file_code={file_code}",
                timeout=12.0,
            )
            data = res.json()

            if data.get("success"):
                result = data.get("result", [{}])
                item   = result[0] if isinstance(result, list) else result
                status = str(item.get("status"))
                if status == "200":
                    return link_id, "valid",  None,               server_name, url
                if status == "404":
                    return link_id, "broken", "API: Deleted",      server_name, url

            return link_id, "broken", f"VOE Unknown ({data.get('msg')})", server_name, url
        except Exception as e:
            return link_id, "broken", f"VOE Error: {e}", server_name, url


async def run():
    log(f"🔍 [VOE Watcher] فحص أقدم {BATCH_SIZE} رابط VOE...")
    res = (
        supabase.table("links")
        .select("id, url, server_name")
        .ilike("server_name", "%voe%")
        .eq("is_fixed", False)
        .order("last_check_at", desc=False, nullsfirst=True)
        .limit(BATCH_SIZE)
        .execute()
    )
    links = res.data or []
    log(f"   ✅ {len(links)} رابط")

    if not links:
        return

    async with httpx.AsyncClient(verify=False) as client:
        tasks   = [check_voe(client, l["id"], l["url"], l["server_name"]) for l in links]
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