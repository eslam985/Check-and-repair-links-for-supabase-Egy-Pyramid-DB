"""
watcher_mixdrop.py
==================
فحص روابط MixDrop عبر API الجماعي (fileinfo2).
يدعم فحص 50 رابط في طلب واحد.

قاعدة الأمان: أي فشل في API أو بيانات ناقصة → pending مش broken.
broken بس لما API يؤكد: status=notfound أو deleted=True.
"""

import os
import asyncio
from datetime import datetime
from typing import Optional

import httpx
from shared import supabase, log


# ===========================================================================
# Section 1: Configuration — الإعدادات المركزية
# ===========================================================================

BATCH_SIZE      = int(os.getenv("BATCH_SIZE", "50"))
MIXDROP_EMAIL   = os.getenv("MIXDROP_EMAIL")
MIXDROP_API_KEY = os.getenv("MIXDROP_KEY")
# https://api.mixdrop.ag/fileinfo2?email=
MIXDROP_API_URL = "https://api.mixdrop.ag/fileinfo2"
CHUNK_SIZE      = 50    # الحد الأقصى لعدد الملفات في طلب API واحد
API_TIMEOUT     = 30.0


# ===========================================================================
# Section 2: File Ref Extractor — استخراج معرف الملف من الرابط
# ===========================================================================

def extract_fileref(url: str) -> Optional[str]:
    """
    استخراج الـ fileref من رابط MixDrop.
    يدعم: /f/REF و /e/REF.
    يُعيد None لو الرابط مش بالصيغة الصحيحة.
    """
    for marker in ("/f/", "/e/"):
        if marker in url:
            ref = url.split(marker)[1].split("?")[0].split("/")[0].strip()
            return ref if ref else None
    return None


# ===========================================================================
# Section 3: API Result Parser — تفسير نتيجة API لكل ملف
# ===========================================================================

def parse_file_status(
    link_id: int, url: str, file_info: Optional[dict], episode_id: Optional[int] = None
) -> tuple[int, str, Optional[str], str, Optional[int]]:
    """
    تحويل بيانات ملف واحد من API إلى (link_id, status, error, url).

    المنطق:
    - file_info مفيش → pending (API لم يرجع بيانات = شك)
    - status=OK و deleted=False → valid
    - status=notfound أو deleted=True → broken
    - أي حالة تانية → pending
    """
    if not file_info:
        return link_id, "pending", "API_MISSING_REF_DATA", url, episode_id

    status     = file_info.get("status", "")
    is_deleted = file_info.get("deleted", False)

    if status == "OK" and not is_deleted:
        return link_id, "valid", None, url, episode_id

    if status == "notfound" or is_deleted:
        return link_id, "broken", "404_DELETED", url, episode_id

    return link_id, "pending", f"STAGING_STATUS_{status.upper()}", url, episode_id


# ===========================================================================
# Section 4: Chunk Processor — فحص مجموعة روابط في طلب API واحد
# ===========================================================================

def _build_chunk_params(chunk_links: list[dict]) -> tuple[list, dict[str, list[dict]]]:
    """
    بناء params لطلب API من مجموعة روابط.
    يُعيد: (params_list, ref_to_links_map).
    """
    params = [
        ("email", MIXDROP_EMAIL),
        ("key",   MIXDROP_API_KEY),
    ]
    ref_to_links: dict[str, list[dict]] = {}

    for link in chunk_links:
        ref = extract_fileref(link["url"])
        if ref:
            if ref not in ref_to_links:
                params.append(("ref[]", ref))
                ref_to_links[ref] = []
            ref_to_links[ref].append(link)

    return params, ref_to_links


async def _fetch_chunk_results(
    client: httpx.AsyncClient, params: list, ref_to_links: dict[str, list[dict]]
) -> list[tuple]:
    """
    إرسال طلب API لمجموعة وتحويل النتائج.
    لو فشل الطلب → كل الروابط تاخد pending.
    """
    try:
        response = await client.get(MIXDROP_API_URL, params=params)

        if response.status_code != 200:
            raise Exception(f"HTTP_ERROR_{response.status_code}")

        data = response.json()
        if not data.get("success"):
            error_detail = data.get("result", data.get("msg", data))
            raise Exception(f"API_REJECTED: {error_detail}")

        api_results = data.get("result", {})

        results = []
        for ref, links in ref_to_links.items():
            file_info = api_results.get(ref)
            for link in links:
                results.append(parse_file_status(link["id"], link["url"], file_info, link.get("episode_id")))
        return results

    except Exception as e:
        log(f"❌ [API Chunk Error] فشل فحص مجموعة: {e}")
        results = []
        for links in ref_to_links.values():
            for link in links:
                results.append((link["id"], "pending", f"API_FETCH_FAILED: {e}", link["url"], link.get("episode_id")))
        return results


async def process_chunk(
    client: httpx.AsyncClient, chunk_links: list[dict]
) -> list[tuple]:
    """
    فحص مجموعة روابط (chunk) واحدة:
    بناء params → إرسال → تحويل النتائج.
    الروابط ذات صيغة خاطئة تاخد broken فوراً.
    """
    params, ref_to_link = _build_chunk_params(chunk_links)

    # الروابط اللي مش عارفين نستخرج منها fileref
    invalid_links = [l for l in chunk_links if extract_fileref(l["url"]) is None]
    invalid_results = [
        (l["id"], "broken", "INVALID_URL_FORMAT", l["url"], l.get("episode_id"))
        for l in invalid_links
    ]

    if not ref_to_link:
        return invalid_results

    api_results = await _fetch_chunk_results(client, params, ref_to_link)
    return invalid_results + api_results


# ===========================================================================
# Section 5: Batch Checker — فحص كل الروابط على دفعات
# ===========================================================================

async def check_mixdrop_batch(links: list[dict]) -> list[tuple]:
    """
    فحص كل الروابط مقسمةً إلى chunks بحجم CHUNK_SIZE.
    يُعيد قائمة نتائج موحدة.
    """
    all_results = []

    async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
        for start in range(0, len(links), CHUNK_SIZE):
            chunk   = links[start: start + CHUNK_SIZE]
            results = await process_chunk(client, chunk)
            all_results.extend(results)

    return all_results


# ===========================================================================
# Section 6: Supabase Fetcher — جلب الروابط المطلوب فحصها
# ===========================================================================

def fetch_links_to_check() -> list[dict]:
    """حجز وجلب أقدم روابط MixDrop بشكل ذري لمنع التضارب بين السكربتات المتزامنة."""
    try:
        res = supabase.rpc(
            "claim_links_by_server",
            {"p_server_name": "mixdrop","p_batch_limit": BATCH_SIZE }
            ).execute()
        
        links = res.data or []
        log(f"✅ تم حجز وجلب {len(links)} رابط MixDrop للفحص.")
        return links
    except Exception as e:
        log(f"❌ [Supabase Error] فشل حجز الروابط: {e}")
        return []


# ===========================================================================
# Section 7: Supabase Writer — حفظ النتائج
# ===========================================================================

def _build_update_payload(
    link_id: int, status: str, error: Optional[str], url: str, now: str, episode_id: Optional[int] = None
) -> dict:
    is_fixed_value = None
    if status == "broken":
        is_fixed_value = False
    if status == "valid":
        is_fixed_value = True
    payload = {
        "id":                link_id,
        "episode_id":         episode_id,
        "url":               url,
        "server_name":       "mixdrop",
        "last_check_status": status,
        "error_message":     error,
        "last_check_at":     now,
        "is_fixed":          is_fixed_value
    }

    return payload


def _increment_check_counts(link_ids: list[int]) -> None:
    """تحديث عداد الفحص لكل الروابط."""
    for link_id in link_ids:
        try:
            supabase.rpc("increment_check_count", {"row_id": link_id}).execute()
        except Exception:
            pass


def _bulk_upsert(updates: list[dict]) -> None:
    """
    حفظ النتائج دفعة واحدة.
    يلجأ للحفظ الفردي كـ fallback لو فشل.
    """
    try:
        supabase.table("links").upsert(updates).execute()
        log(f"⚡ [Supabase] تم تحديث {len(updates)} رابط في طلب واحد.")
    except Exception as e:
        log(f"⚠️ [Supabase Bulk Error] جاري الحفظ الفردي كـ fallback: {e}")
        for update in updates:
            try:
                supabase.table("links").update(update).eq("id", update["id"]).execute()
            except Exception:
                pass


def save_results(results: list[tuple]) -> None:
    now          = datetime.now().isoformat()
    bulk_updates = []
    link_ids     = []

    for link_id, status, error, url, episode_id in results:
        link_ids.append(link_id)
        bulk_updates.append(_build_update_payload(link_id, status, error, url, now, episode_id))

        icon = "✅" if status == "valid" else ("⏳" if status == "pending" else "❌")
        log(f"{icon} {link_id:<6} | mixdrop       | {status:<8} | {url}")

    _increment_check_counts(link_ids)
    _bulk_upsert(bulk_updates)


# ===========================================================================
# Section 8: Main Runner — المنسق الرئيسي
# ===========================================================================

async def run() -> None:
    """جلب الروابط → فحصها → حفظ النتائج."""
    log(f"🔍 [MixDrop Watcher] فحص أقدم {BATCH_SIZE} رابط...")

    links = fetch_links_to_check()
    if not links:
        log("✅ لا توجد روابط تحتاج فحصاً.")
        return

    results = await check_mixdrop_batch(links)
    save_results(results)


# ===========================================================================
# Entry Point
# ===========================================================================

if __name__ == "__main__":
    asyncio.run(run())