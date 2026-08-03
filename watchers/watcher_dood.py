"""
watcher_dood.py
===============
فحص روابط Doodstream بمرحلتين:
1. HTML Check: فحص صفحة الـ embed مباشرةً للكشف عن رسائل الحذف.
2. API Check: /api/file/info عبر عدة دومينات كـ fallback.

قاعدة الأمان: أي شك أو فشل شبكي → pending مش broken.
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

DOOD_API_KEY = os.getenv("DOOD_API_KEY")
BATCH_SIZE   = int(os.getenv("BATCH_SIZE", "50"))

DOOD_DOMAINS = [
    "doodapi.co",
    "doodapi.com",
    "dood.stream",
    "myvidplay.com",
    "playmogo.com",
]

API_TIMEOUT  = 10.0
HTML_TIMEOUT = 15.0
API_COOLDOWN = 1.0   # ثانية بين كل طلب لتفادي الحظر

# رسائل الحذف الصريحة في HTML
HTML_DELETED_MARKERS = ["no_video", "not found", "looking for is not found"]

# رسائل الحذف الصريحة من API
API_DELETED_STATUSES = {"Not found or not your file", "Deleted", "Removed", "404"}

# Headers محاكاة متصفح موبايل حقيقي لتفادي الـ 403
MOBILE_HEADERS = {
    "User-Agent":              "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Mobile Safari/537.36",
    "Accept":                  "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Language":         "ar,en-US;q=0.9,en;q=0.8",
    "Cache-Control":           "no-cache",
    "Pragma":                  "no-cache",
    "Priority":                "u=0, i",
    "Sec-Ch-Ua":               '"Google Chrome";v="147", "Not.A/Brand";v="8", "Chromium";v="147"',
    "Sec-Ch-Ua-Mobile":        "?1",
    "Sec-Ch-Ua-Platform":      '"Android"',
    "Sec-Fetch-Dest":          "document",
    "Sec-Fetch-Mode":          "navigate",
    "Sec-Fetch-Site":          "cross-site",
    "Sec-Fetch-User":          "?1",
    "Upgrade-Insecure-Requests": "1",
}

sem = asyncio.Semaphore(1)


# ===========================================================================
# Section 2: URL Parsers — استخراج بيانات الرابط
# ===========================================================================

def extract_file_code(url: str) -> str:
    """
    استخراج file_code من رابط Dood.
    يدعم: /e/CODE و /d/CODE و /f/CODE.
    """
    clean = url.strip().rstrip("/").split("?")[0]
    parts = clean.split("/")

    for marker in ("e", "d", "f"):
        if marker in parts:
            idx = parts.index(marker)
            if idx + 1 < len(parts):
                return parts[idx + 1]

    return parts[-1]


def extract_domain(url: str) -> str:
    """استخراج الدومين من الرابط للاستخدام في الـ Referer header."""
    parts = url.strip().rstrip("/").split("?")[0].split("/")
    for part in parts:
        if "." in part and not part.startswith("http"):
            return part
    return "doodstream.com"


def build_embed_url(url: str, file_code: str) -> str:
    """بناء رابط الـ embed الصحيح لفحص HTML."""
    if "/e/" in url:
        return url
    return url.replace(f"/{file_code}", f"/e/{file_code}")


# ===========================================================================
# Section 3: HTML Checker — فحص صفحة الـ Embed
# ===========================================================================

def _is_cloudflare_block(page_text: str) -> bool:
    """هل الصفحة محجوبة بـ Cloudflare؟"""
    return "just a moment" in page_text or "cloudflare" in page_text


def _is_deleted_html(page_text: str) -> bool:
    """هل الصفحة تحتوي على رسائل حذف صريحة؟"""
    return any(marker in page_text for marker in HTML_DELETED_MARKERS)


def _is_valid_html(page_text: str, body_length: int) -> bool:
    """هل الصفحة تحتوي على محتوى فيديو سليم؟"""
    if body_length < 500:
        return False
    return any(kw in page_text for kw in ("video", "download", "length"))


async def check_via_html(
    client: httpx.AsyncClient, url: str, file_code: str, domain: str
) -> tuple[Optional[bool], Optional[str]]:
    """
    فحص صفحة الـ embed.
    يُعيد: (result, error_msg)
    - True  → valid مؤكد
    - False → broken مؤكد
    - None  → غير متأكد، تابع مع API
    """
    embed_url = build_embed_url(url, file_code)
    headers   = {**MOBILE_HEADERS, "Referer": f"https://{domain}/"}

    try:
        res = await client.get(
            embed_url, headers=headers, timeout=HTML_TIMEOUT, follow_redirects=True
        )

        if res.status_code not in (200, 403):
            log(f"   ⚠️ [HTML] كود {res.status_code} للملف {file_code} → API")
            return None, None

        page_text   = res.text.lower()
        body_length = len(page_text)

        if _is_cloudflare_block(page_text):
            log(f"   ⚠️ [HTML] Cloudflare detected لـ {file_code} → API")
            return None, None

        if _is_deleted_html(page_text):
            log(f"   ❌ [HTML] ملف محذوف ({res.status_code}): {file_code}")
            return False, f"Dood: Video not found on HTML page ({res.status_code})"

        if _is_valid_html(page_text, body_length):
            log(f"   💚 [HTML] رابط سليم: {file_code}")
            return True, None

        log(f"   ⚠️ [HTML] محتوى غير كافٍ ({body_length} حرف) لـ {file_code} → API")
        return None, None

    except Exception as e:
        log(f"   ⚠️ [HTML] خطأ شبكي: {e} → API")
        return None, None


# ===========================================================================
# Section 4: API Checker — فحص Dood عبر API
# ===========================================================================

def _parse_api_file_info(file_info: dict, file_code: str) -> tuple[Optional[bool], Optional[str]]:
    """
    تفسير بيانات ملف واحد من API.
    يُعيد: (is_valid, error_msg) أو (None, None) لو غير متأكد.
    """
    if not isinstance(file_info, dict) or not file_info:
        return None, None

    file_status = file_info.get("status")

    # حذف صريح من API
    if str(file_status) in API_DELETED_STATUSES:
        return False, f"Dood API: {file_status}"

    # فيديو سليم: عنده حجم وعنوان
    has_size  = "size" in file_info or "length" in file_info
    has_title = "title" in file_info
    valid_status = (
        file_status == 200
        or str(file_status) == "200"
        or file_status is None
    )

    if valid_status and has_size and has_title:
        return True, None

    return False, f"Dood API: Missing file metadata (status: {file_status})"


async def _try_single_domain(
    client: httpx.AsyncClient, domain: str, file_code: str
) -> tuple[Optional[bool], Optional[str]]:
    """
    محاولة فحص الملف عبر دومين واحد.
    يُعيد: (is_valid, error_msg) أو (None, None) لو فشل الطلب.
    """
    try:
        res = await client.get(
            f"https://{domain}/api/file/info?key={DOOD_API_KEY}&file_code={file_code}",
            timeout=API_TIMEOUT,
        )
        if res.status_code != 200:
            return None, None

        data = res.json()
        if data.get("status") != 200:
            return None, None

        file_info_list = data.get("result")

        # result فارغ = ملف محذوف
        if not isinstance(file_info_list, list) or not file_info_list:
            return False, "Dood API: Empty or invalid result list"

        return _parse_api_file_info(file_info_list[0], file_code)

    except Exception:
        return None, None


async def check_via_api(
    client: httpx.AsyncClient, file_code: str
) -> tuple[Optional[bool], Optional[str]]:
    """
    فحص الملف عبر API على كل الدومينات المتاحة.
    يُعيد أول نتيجة واضحة، أو (None, None) لو فشلت كلها.
    """
    for domain in DOOD_DOMAINS:
        is_valid, error = await _try_single_domain(client, domain, file_code)
        if is_valid is not None:
            return is_valid, error

    return None, None  # كل الدومينات فشلت → غير متأكد


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
        await asyncio.sleep(API_COOLDOWN)

        file_code = extract_file_code(url)
        domain    = extract_domain(url)

        # ── المرحلة الأولى: HTML ─────────────────────────────────────
        html_result, html_error = await check_via_html(client, url, file_code, domain)

        if html_result is True:
            return link_id, "valid", None, server_name, url
        if html_result is False:
            return link_id, "broken", html_error, server_name, url

        # ── المرحلة الثانية: API ─────────────────────────────────────
        log(f"   🔄 [API] فحص الملف: {file_code}")
        api_result, api_error = await check_via_api(client, file_code)

        if api_result is True:
            return link_id, "valid", None, server_name, url
        if api_result is False:
            return link_id, "broken", api_error, server_name, url

        # كلاهما مش متأكد → pending
        return link_id, "pending", "Dood: HTML and API both inconclusive", server_name, url


# ===========================================================================
# Section 6: Supabase Fetcher — جلب الروابط المطلوب فحصها
# ===========================================================================

def fetch_links_to_check() -> list[dict]:
    """جلب أقدم روابط Dood المطلوب فحصها بخوارزمية ترتيب متعددة المستويات."""
    res = (
        supabase.table("links")
        .select("id, url, server_name, last_check_status, created_at, last_check_at, check_count")
        .ilike("server_name", "%dood%")
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
    log(f"✅ تم جلب {len(links)} رابط Dood للفحص.")
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
    """حفظ النتائج دفعة واحدة مع fallback للحفظ الفردي."""
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
    log(f"🔍 [Dood Watcher] فحص أقدم {BATCH_SIZE} رابط...")

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