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
BATCH_SIZE   = int(os.getenv("BATCH_SIZE", "200"))
sem          = asyncio.Semaphore(5)

DOOD_DOMAINS = ["doodapi.co", "doodapi.com", "dood.stream", "myvidplay.com"]


async def check_dood(client, link_id, url, server_name):
    async with sem:
        try:
            clean     = url.strip().rstrip("/").split("?")[0]
            parts     = clean.split("/")
            file_code = None
            for marker in ("e", "d", "f"):
                if marker in parts:
                    idx = parts.index(marker)
                    if idx + 1 < len(parts):
                        file_code = parts[idx + 1]
                        break
            if not file_code:
                file_code = parts[-1]

            for domain in DOOD_DOMAINS:
                try:
                    res  = await client.get(
                        f"https://{domain}/api/file/info?key={DOOD_API_KEY}&file_code={file_code}",
                        timeout=10.0,
                    )
                    data = res.json()
                    if data.get("status") == 200 and data.get("result"):
                        return link_id, "valid", None, server_name, url
                except Exception:
                    continue

            return link_id, "broken", "Dood: Deleted or Not Found", server_name, url

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
        tasks   = [check_dood(client, l["id"], l["url"], l["server_name"]) for l in links]
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