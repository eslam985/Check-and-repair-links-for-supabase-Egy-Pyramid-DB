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

BATCH_SIZE = int(os.getenv("BATCH_SIZE", "50"))

ST_API_BASE  = "https://api.streamtape.com"
HTML_TIMEOUT = 10.0
API_TIMEOUT  = 12.0

# رسائل الحذف الصريحة في HTML صفحة Streamtape
HTML_DELETED_MARKERS = [
    "Video not found!",
    "Maybe it got deleted by the creator!",
]

sem = asyncio.Semaphore(3)


# ===========================================================================
# Section 2: File Code Extractor — استخراج كود الملف من الرابط
# ===========================================================================

def extract_file_code(url: str) -> str:
    """
    استخراج file_code من رابط Streamtape.
    يدعم: /e/CODE و /v/CODE، وإلا يأخذ آخر جزء من الرابط.
    """
    clean = url.strip().rstrip("/").split("?")[0]
    parts = clean.split("/")

    for marker in ("e", "v"):
        if marker in parts:
            idx = parts.index(marker)
            if idx + 1 < len(parts):
                return parts[idx + 1]

    return parts[-1]


# ===========================================================================
# Section 3: HTML Checker — فحص صفحة الـ Embed مباشرةً
# ===========================================================================

async def check_via_html(
    client: httpx.AsyncClient, url: str, file_code: str
) -> tuple[bool, Optional[str]]:
    """
    فحص صفحة الـ embed للكشف عن رسائل الحذف الصريحة.
    يُعيد: (is_deleted, error_msg)
    - is_deleted=True → broken مؤكد
    - is_deleted=False → تابع مع API
    - exception → تابع مع API (نتجاهل فشل HTML)
    """
    try:
        res = await client.get(url, timeout=HTML_TIMEOUT)

        if res.status_code != 200:
            return False, None  # مش متأكد → تابع مع API

        for marker in HTML_DELETED_MARKERS:
            if marker in res.text:
                log(f"   ❌ [HTML] ملف محذوف: {file_code}")
                return True, f"Streamtape: {marker}"

        return False, None

    except Exception as e:
        log(f"   ⚠️ [HTML] فشل فحص الصفحة: {e} — جاري الانتقال للـ API")
        return False, None


# ===========================================================================
# Section 4: API Checker — فحص Streamtape عبر API
# ===========================================================================

async def _check_file_info(
    client: httpx.AsyncClient, file_code: str
) -> bool:
    """
    فحص الملف عبر /file/info.
    يُعيد True لو الملف موجود وله حجم.
    """
    api_url = (
        f"{ST_API_BASE}/file/info"
        f"?login={STREAMTAPE_LOGIN}&key={STREAMTAPE_API_KEY}&file={file_code}"
    )
    res  = await client.get(api_url, timeout=API_TIMEOUT)
    data = res.json()

    if data.get("status") != 200:
        return False

    result = data.get("result", {})
    if not isinstance(result, dict) or not result:
        return False

    file_info = next(iter(result.values()))
    return (
        file_info.get("status") == 200
        and file_info.get("size") is not None
    )


async def _check_listfolder(
    client: httpx.AsyncClient, file_code: str
) -> bool:
    """
    Fallback: البحث عن الملف في /file/listfolder بالـ linkid.
    يُعيد True لو وجده.
    """
    res  = await client.get(
        f"{ST_API_BASE}/file/listfolder"
        f"?login={STREAMTAPE_LOGIN}&key={STREAMTAPE_API_KEY}",
        timeout=API_TIMEOUT,
    )
    data  = res.json()
    files = data.get("result", {}).get("files", [])
    return any(f.get("linkid") == file_code for f in files)


async def check_via_api(
    client: httpx.AsyncClient, file_code: str
) -> tuple[bool, Optional[str]]:
    """
    فحص الملف عبر API بمرحلتين: file/info → listfolder كـ fallback.
    يُعيد: (is_valid, error_msg)
    - is_valid=True → valid
    - is_valid=False, error=None → pending (خطأ شبكي أو استجابة غير متوقعة)
    - is_valid=False, error=str → broken مؤكد
    """
    try:
        # المرحلة الأولى: file/info
        if await _check_file_info(client, file_code):
            return True, None

        # المرحلة الثانية: listfolder كـ fallback
        if await _check_listfolder(client, file_code):
            return True, None

        # كلاهما قال مش موجود → broken
        return False, "Streamtape: Not Found"

    except Exception as e:
        log(f"   ⚠️ [API] خطأ: {e} → pending")
        return False, None  # خطأ شبكي → pending


# ===========================================================================
# Section 5: Link Status Resolver — تحديد الحالة النهائية للرابط
# ===========================================================================

async def resolve_link_status(
    client: httpx.AsyncClient, link_id: int, url: str, server_name: str
) -> tuple[int, str, Optional[str], str, str]:
    """
    تحديد الحالة النهائية للرابط بمرحلتين: HTML → API.
    القاعدة الذهبية: الشك → pending. broken بس عند يقين كامل.
    يُعيد: (link_id, status, error_msg, server_name, url)
    """
    async with sem:
        file_code = extract_file_code(url)

        # ── المرحلة الأولى: HTML ─────────────────────────────────────
        is_deleted, html_error = await check_via_html(client, url, file_code)
        if is_deleted:
            return link_id, "broken", html_error, server_name, url

        # ── المرحلة الثانية: API ─────────────────────────────────────
        is_valid, api_error = await check_via_api(client, file_code)

        if is_valid:
            return link_id, "valid", None, server_name, url

        if api_error is None:
            # خطأ شبكي أو استجابة غير متوقعة → pending
            return link_id, "pending", "API_FETCH_FAILED", server_name, url

        return link_id, "broken", api_error, server_name, url


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
        .or_('last_check_status.in.("pending","valid"),url.ilike.%disabled%')
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
    """جلب الروابط → فحصها → حفظ النتائج."""
    log(f"🔍 [Streamtape Watcher] فحص أقدم {BATCH_SIZE} رابط...")

    links = fetch_links_to_check()
    if not links:
        log("✅ لا توجد روابط تحتاج فحصاً.")
        return

    async with httpx.AsyncClient(verify=False, follow_redirects=True) as client:
        tasks   = [
            resolve_link_status(client, l["id"], l["url"], l["server_name"])
            for l in links
        ]
        results = await asyncio.gather(*tasks)

    save_results(results)


# ===========================================================================
# Entry Point
# ===========================================================================

if __name__ == "__main__":
    asyncio.run(run())