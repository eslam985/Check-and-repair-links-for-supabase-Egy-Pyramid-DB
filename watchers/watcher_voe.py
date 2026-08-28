"""
watcher_voe.py — فحص روابط VOE فقط
منطق: VOE API → file/info → status 200=valid / 404=broken
"""

import os
import asyncio
import httpx
from datetime import datetime
from typing import Optional
from shared import supabase, log

VOE_API_KEY = os.getenv("VOE_API_KEY")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "100"))
MAX_API_BATCH = 100  # الحد الأقصى للملفات في الطلب الواحد لـ VOE
API_TIMEOUT = 12.0

# ===========================================================================
# Section 2 & 3: File Extractor & Batch API Checker
# ===========================================================================


def extract_file_code(url: str) -> Optional[str]:
    """استخراج file_code من رابط VOE."""
    clean_url = url.strip().rstrip("/").split("?")[0]
    if clean_url.endswith("/download"):
        clean_url = clean_url[:-9]
    code = clean_url.split("/")[-1].strip()
    return code if code else None


def parse_file_status(
    link_id: int, url: str, server_name: str, file_info: Optional[dict]
) -> tuple[int, str, Optional[str], str, str]:
    """تحليل استجابة VOE لملف واحد."""
    if not file_info or not isinstance(file_info, dict):
        return link_id, "pending", "API_NO_DATA", server_name, url

    status = str(file_info.get("status"))

    if status == "200":
        return link_id, "valid", None, server_name, url
    elif status == "404":
        return link_id, "broken", "VOE: Deleted (404)", server_name, url
    else:
        return link_id, "pending", f"VOE_STATUS_{status}", server_name, url


def _build_chunk_params(chunk_links: list[dict]) -> tuple[dict, dict[str, list[dict]]]:
    """تجميع الأكواد لطلب الـ API المجمع."""
    ref_to_links: dict[str, list[dict]] = {}

    for link in chunk_links:
        code = extract_file_code(link["url"])
        if code:
            if code not in ref_to_links:
                ref_to_links[code] = []
            ref_to_links[code].append(link)

    file_ids_str = ",".join(ref_to_links.keys())
    return {"file_code": file_ids_str}, ref_to_links


async def _fetch_chunk_results(
    client: httpx.AsyncClient, ref_to_links: dict[str, list[dict]]
) -> list[tuple]:
    """إرسال طلب مجمع لـ API VOE وتحليل الإجابات."""
    if not ref_to_links:
        return []

    file_codes_str = ",".join(ref_to_links.keys())
    api_url = (
        f"https://voe.sx/api/file/info?key={VOE_API_KEY}&file_code={file_codes_str}"
    )

    try:
        res = await client.get(api_url, timeout=API_TIMEOUT)
        if res.status_code != 200:
            raise Exception(f"HTTP_ERROR_{res.status_code}")

        data = res.json()
        if not data.get("success"):
            msg = data.get("msg", "Unknown API Error")
            raise Exception(f"API_REJECTED: {msg}")

        raw_results = data.get("result", [])
        if isinstance(raw_results, dict):
            raw_results = [raw_results]

        # تحويل القائمة إلى dictionary للبحث السريع باستخدام fileCode أو file_code
        api_results_map = {}
        for item in raw_results:
            if isinstance(item, dict):
                code = item.get("fileCode") or item.get("file_code")
                if code:
                    api_results_map[code] = item

        results = []
        for code, links in ref_to_links.items():
            file_info = api_results_map.get(code)
            for link in links:
                results.append(
                    parse_file_status(
                        link["id"], link["url"], link["server_name"], file_info
                    )
                )
        return results

    except Exception as e:
        log(f"❌ [VOE API Chunk Error] فشل فحص دفعة VOE: {e}")
        results = []
        for links in ref_to_links.values():
            for link in links:
                results.append(
                    (
                        link["id"],
                        "pending",
                        f"API_FETCH_FAILED: {e}",
                        link["server_name"],
                        link["url"],
                    )
                )
        return results


# ===========================================================================
# Section 7: Supabase Writer — حفظ النتائج
# ===========================================================================


def _increment_check_counts(link_ids: list[int]) -> None:
    """تحديث عداد الفحص لكل الروابط."""
    for link_id in link_ids:
        try:
            supabase.rpc("increment_check_count", {"row_id": link_id}).execute()
        except Exception:
            pass


def _bulk_upsert(updates: list[dict]) -> None:
    """حفظ النتائج دفعة واحدة عبر Supabase."""
    try:
        supabase.table("links").upsert(updates).execute()
        log(f"⚡ [Supabase] تم تحديث {len(updates)} رابط VOE في طلب واحد.")
    except Exception as e:
        log(f"⚠️ [Supabase Bulk Error] جاري الحفظ الفردي كـ fallback: {e}")
        for update in updates:
            try:
                supabase.table("links").update(update).eq("id", update["id"]).execute()
            except Exception:
                pass


def save_results(results: list[tuple]) -> None:
    """تجميع النتائج وطباعة اللوج وحفظها في Supabase."""
    now = datetime.now().isoformat()
    bulk_updates = []
    link_ids = []

    for link_id, status, error, server_name, url in results:
        link_ids.append(link_id)

        icon = "✅" if status == "valid" else ("⏳" if status == "pending" else "❌")
        log(f"{icon} {link_id:<6} | {server_name:<12} | {status:<8} | {url}")

        # 1. نحدد قيمة الـ is_fixed أولاً بناءً على الحالة
        is_fixed_value = None
        if status == "broken":
            is_fixed_value = False
        elif status == "valid":
            
            is_fixed_value = True
        bulk_updates.append(
            {
                "id": link_id,
                "url": url,
                "server_name": server_name,
                "last_check_status": status,
                "error_message": error,
                "last_check_at": now,
                "is_fixed": is_fixed_value #  تمت الإضافة هنا بشكل صحيح داخل القاموس
            }
        )

    _increment_check_counts(link_ids)
    _bulk_upsert(bulk_updates)

def fetch_links_to_check() -> list[dict]:
    """حجز وجلب أقدم روابط VOE المطلوب فحصها بشكل ذري."""
    try:
        res = supabase.rpc("claim_links_by_server", {
            "p_server_name": "voe",
            "p_batch_limit": BATCH_SIZE
        }).execute()
        links = res.data or []
        log(f"✅ تم حجز وجلب {len(links)} رابط VOE للفحص.")
        return links
    except Exception as e:
        log(f"❌ [Supabase Error] فشل حجز روابط VOE: {e}")
        return []
    
# ===========================================================================
# Section 8: Main Runner — المنسق الرئيسي
# ===========================================================================


async def run() -> None:
    """جلب الروابط → تقطيعها لدفعات (100) → فحصها عبر API → حفظ النتائج."""
    log(f"🔍 [VOE Watcher] فحص أقدم {BATCH_SIZE} رابط VOE...")

    links = fetch_links_to_check()

    if not links:
        return

    all_results = []
    async with httpx.AsyncClient(verify=False, follow_redirects=True) as client:
        for i in range(0, len(links), MAX_API_BATCH):
            chunk = links[i : i + MAX_API_BATCH]
            _, ref_to_links = _build_chunk_params(chunk)
            chunk_results = await _fetch_chunk_results(client, ref_to_links)
            all_results.extend(chunk_results)

    save_results(all_results)


if __name__ == "__main__":
    asyncio.run(run())
