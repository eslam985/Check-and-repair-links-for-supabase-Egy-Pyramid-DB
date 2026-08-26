"""
watcher_streamtape.py
=====================
فحص روابط Streamtape بمرحلتين:
1. HTML Check: فحص صفحة الـ embed مباشرةً للكشف عن رسائل الحذف الصريحة.
2. API Check: file/info للتأكد من وجود الملف، ثم listfolder كـ fallback.

قاعدة الأمان: أي خطأ شبكي أو استجابة غير متوقعة → pending مش broken.
broken بس عند يقين كامل من HTML أو API.
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

STREAMTAPE_LOGIN   = os.getenv("STREAMTAPE_LOGIN")
STREAMTAPE_API_KEY = os.getenv("STREAMTAPE_API_KEY")

BATCH_SIZE = int(os.getenv("BATCH_SIZE", "100"))
MAX_API_BATCH = 100  # الحد الأقصى المسموح به من Streamtape في طلب الـ API الواحد
ST_API_BASE  = "https://api.streamtape.com"
API_TIMEOUT  = 12.0


# ===========================================================================
# Section 2: File Code Extractor — استخراج كود الملف من الرابط
# ===========================================================================

def extract_file_code(url: str) -> Optional[str]:
    """
    استخراج file_code من رابط Streamtape.
    يدعم: /e/CODE و /v/CODE و /f/CODE.
    """
    clean = url.strip().rstrip("/").split("?")[0]
    parts = clean.split("/")

    for marker in ("e", "v", "f"):
        if marker in parts:
            idx = parts.index(marker)
            if idx + 1 < len(parts):
                code = parts[idx + 1].strip()
                return code if code else None

    code = parts[-1].strip()
    return code if code else None


# ===========================================================================
# Section 3: HTML Checker — فحص صفحة الـ Embed مباشرةً
# ===========================================================================

# ===========================================================================
# Section 3 & 4: Batch API Checker & Parser — الفحص المجمع عبر API
# ===========================================================================

def parse_file_status(
    link_id: int, url: str, server_name: str, file_info: Optional[dict]
) -> tuple[int, str, Optional[str], str, str]:
    """تحليل استجابة Streamtape لملف واحد."""
    if not file_info or not isinstance(file_info, dict):
        return link_id, "pending", "API_NO_DATA", server_name, url

    st_status = file_info.get("status")

    if st_status == 200:
        return link_id, "valid", None, server_name, url
    elif st_status == 404:
        return link_id, "broken", "Streamtape: File Not Found (404)", server_name, url
    else:
        return link_id, "pending", f"STREAMTAPE_STATUS_{st_status}", server_name, url


def _build_chunk_params(chunk_links: list[dict]) -> tuple[dict, dict[str, list[dict]]]:
    """
    تجميع الأكواد وتشكيل URL parameters لطلب الـ API المجمع.
    تستوعب الروابط المكررة بنفس الـ file_code داخل الدفعة.
    """
    ref_to_links: dict[str, list[dict]] = {}

    for link in chunk_links:
        code = extract_file_code(link["url"])
        if code:
            if code not in ref_to_links:
                ref_to_links[code] = []
            ref_to_links[code].append(link)

    file_ids_str = ",".join(ref_to_links.keys())
    params = {
        "login": STREAMTAPE_LOGIN,
        "key":   STREAMTAPE_API_KEY,
        "file":  file_ids_str,
    }
    return params, ref_to_links


async def _fetch_chunk_results(
    client: httpx.AsyncClient, params: dict, ref_to_links: dict[str, list[dict]]
) -> list[tuple]:
    """إرسال طلب مجمع لـ API Streamtape وتحليل الإجابات."""
    if not ref_to_links:
        return []

    try:
        res = await client.get(f"{ST_API_BASE}/file/info", params=params, timeout=API_TIMEOUT)

        if res.status_code != 200:
            raise Exception(f"HTTP_ERROR_{res.status_code}")

        data = res.json()
        if data.get("status") != 200:
            msg = data.get("msg", "Unknown API Error")
            raise Exception(f"API_REJECTED: {msg}")

        api_results = data.get("result", {})
        if not isinstance(api_results, dict):
            api_results = {}

        results = []
        for code, links in ref_to_links.items():
            file_info = api_results.get(code)
            for link in links:
                results.append(
                    parse_file_status(link["id"], link["url"], link["server_name"], file_info)
                )
        return results

    except Exception as e:
        log(f"❌ [API Chunk Error] فشل فحص دفعة Streamtape: {e}")
        results = []
        for links in ref_to_links.values():
            for link in links:
                results.append(
                    (link["id"], "pending", f"API_FETCH_FAILED: {e}", link["server_name"], link["url"])
                )
        return results


# ===========================================================================
# Section 6: Supabase Fetcher — جلب الروابط المطلوب فحصها
# ===========================================================================

def fetch_links_to_check() -> list[dict]:
    """جلب أقدم روابط Streamtape المطلوب فحصها بخوارزمية ترتيب متعددة المستويات."""
    res = (
        supabase.table("links")
        .select("id, url, server_name, last_check_status, created_at, last_check_at, check_count")
        .ilike("server_name", "%streamtape%")
        .eq("is_fixed", False)
        .or_('last_check_status.in.(pending,valid),url.ilike.*disabled*')
        .order("last_check_at",     desc=False, nullsfirst=True)
        .order("last_check_status", desc=True)
        .order("created_at",        desc=False)
        .order("check_count",       desc=False)
        .limit(BATCH_SIZE)
        .execute()
    )
    links = res.data or []
    log(f"✅ تم جلب {len(links)} رابط Streamtape للفحص.")
    return links


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
    """تجميع النتائج وطباعة اللوج وحفظها في Supabase."""
    now          = datetime.now().isoformat()
    bulk_updates = []
    link_ids     = []

    for link_id, status, error, server_name, url in results:
        link_ids.append(link_id)

        icon = "✅" if status == "valid" else ("⏳" if status == "pending" else "❌")
        log(f"{icon} {link_id:<6} | {server_name:<12} | {status:<8} | {url}")

        bulk_updates.append({
            "id":                link_id,
            "url":               url,
            "server_name":       server_name,
            "last_check_status": status,
            "error_message":     error,
            "last_check_at":     now,
        })

    _increment_check_counts(link_ids)
    _bulk_upsert(bulk_updates)


# ===========================================================================
# Section 8: Main Runner — المنسق الرئيسي
# ===========================================================================

async def run() -> None:
    """جلب الروابط → تقطيعها لمجموعات (100 ملف) → فحصها عبر API → حفظ النتائج."""
    log(f"🔍 [Streamtape Watcher] فحص أقدم {BATCH_SIZE} رابط...")

    links = fetch_links_to_check()
    if not links:
        log("✅ لا توجد روابط تحتاج فحصاً.")
        return

    all_results = []

    async with httpx.AsyncClient(verify=False, follow_redirects=True) as client:
        # تقطيع القائمة إلى دفعات لا تتجاوز MAX_API_BATCH (100)
        for i in range(0, len(links), MAX_API_BATCH):
            chunk = links[i : i + MAX_API_BATCH]
            params, ref_to_links = _build_chunk_params(chunk)
            chunk_results = await _fetch_chunk_results(client, params, ref_to_links)
            all_results.extend(chunk_results)

    save_results(all_results)


# ===========================================================================
# Entry Point
# ===========================================================================

if __name__ == "__main__":
    asyncio.run(run())