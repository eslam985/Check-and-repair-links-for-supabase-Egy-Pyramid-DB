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
) -> tuple[str, Optional[str]]:
    """
    فحص صفحة الـ embed كتأكيد مزدوج.
    يُعيد: (status, failure_reason) -> status: 'valid' | 'broken' | 'pending'
    """
    embed_url = build_embed_url(url, file_code)
    headers = {**MOBILE_HEADERS, "Referer": f"https://{domain}/"}

    try:
        res = await client.get(
            embed_url, headers=headers, timeout=HTML_TIMEOUT, follow_redirects=True
        )

        if res.status_code in (403, 429, 503):
            log(f"⚠️ Embed HTTP {res.status_code} لـ {file_code} → pending")
            return "pending", f"Embed HTTP {res.status_code}"

        page_text = res.text.lower()

        if _is_cloudflare_block(page_text):
            log(f"⚠️ Cloudflare detected لـ {file_code} → pending")
            return "pending", "Cloudflare Block"

        if "maintenance mode" in page_text:
            log(f"⚠️ Server Maintenance Mode لـ {file_code} → pending")
            return "pending", "Server Maintenance Mode"

        if _is_deleted_html(page_text):
            return "broken", f"Dood: Video not found on HTML page ({res.status_code})"

        if _is_valid_html(page_text, len(page_text)):
            return "valid", None

        return "pending", "Inconclusive HTML Content"

    except Exception as e:
        log(f"⚠️ خطأ في HTML Check: {e} → pending")
        return "pending", f"HTML Check Error: {e}"


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
    client: httpx.AsyncClient, file_codes: list[str]
) -> dict[str, tuple[bool, Optional[str]]]:
    """
    فحص مجموعة ملفات عبر API في طلب واحد.
    يُعيد: قاموس يربط file_code بنتيجته (is_valid, failure_reason)
    """
    results_map = {fc: (False, None) for fc in file_codes}
    if not file_codes:
        return results_map

    codes_str = ",".join(file_codes)

    for domain in DOOD_DOMAINS:
        api_url = f"https://{domain}/api/file/info?key={DOOD_API_KEY}&file_code={codes_str}"

        try:
            res = await client.get(api_url, timeout=API_TIMEOUT)

            # معالجة Rate Limit
            if res.status_code == 429:
                log(f"⚠️ Dood API Rate Limited (429) على الدومين {domain} → pending")
                return results_map

            data = res.json()
            if data.get("msg") == "Too Many Requests" or data.get("status") == "429":
                log("⚠️ Dood API: Too Many Requests → pending")
                return results_map

            if data.get("status") != 200 or not isinstance(data.get("result"), list):
                continue  # جرب الدومين التالي كـ fallback

            # قراءة نتائج الدفعة
            for item in data["result"]:
                fc = item.get("filecode") or item.get("file_code")
                if not fc:
                    continue

                status_val = str(item.get("status", ""))

                if status_val in ("200", "Active") or item.get("status") == 200:
                    results_map[fc] = (True, None)
                elif status_val in API_DELETED_STATUSES or "not found" in status_val.lower():
                    results_map[fc] = (False, f"Dood API: {status_val}")
                else:
                    results_map[fc] = (False, f"Dood API: Unexpected status {status_val}")

            return results_map  # نجح الفحص عبر هذا الدومين

        except Exception as e:
            log(f"⚠️ خطأ أثناء الاتصال بالـ API ({domain}): {e}")
            continue

    return results_map  # إذا فشلت كل الدومينات، تُرجع pending


# ===========================================================================
# Section 5: Link Status Resolver — تحديد الحالة النهائية للرابط
# ===========================================================================

async def process_links_batch(
    client: httpx.AsyncClient, links: list[dict]
) -> list[tuple]:
    """
    معالجة دفعة من الروابط: API مجمع → HTML فردي للملفات السليمة.
    """
    final_results = []

    code_to_links = {}
    for link in links:
        fc = extract_file_code(link["url"])
        if fc not in code_to_links:
            code_to_links[fc] = []
        code_to_links[fc].append(link)

    file_codes = list(code_to_links.keys())

    # 1. المرحلة الأولى: فحص الدفعة عبر API
    api_results = await check_via_api(client, file_codes)

    # 2. المرحلة الثانية: توجيه الملفات المقبولة مبدئياً إلى فحص HTML
    async def resolve_single_fc(fc: str, api_valid: bool, api_error: Optional[str]):
        if not api_valid:
            status = "broken" if api_error else "pending"
            error_msg = api_error if api_error else "API Unavailable or Inconclusive"
            return fc, status, error_msg

        # أخذ أول رابط متاح للحصول على الدومين ورابط الـ embed
        sample_link = code_to_links[fc][0]
        domain = extract_domain(sample_link["url"])

        async with sem:
            html_status, html_error = await check_via_html(
                client, sample_link["url"], fc, domain
            )

        # إذا أكد الـ api وجود الملف ورُفض طلب الـ html بحظر (403/pending)، نعتمد نتيجة الـ api
        if html_status == "pending":
            return fc, "valid", None

        return fc, html_status, html_error

    tasks = [
        resolve_single_fc(fc, api_results[fc][0], api_results[fc][1])
        for fc in file_codes
    ]
    resolved_codes = await asyncio.gather(*tasks)

    # 3. تجميع النتائج لربطها بـ link_id
    for fc, status, error_msg in resolved_codes:
        for link in code_to_links[fc]:
            final_results.append(
                (link["id"], status, error_msg, link["server_name"], link["url"])
            )

    return final_results


# ===========================================================================
# Section 6: Supabase Fetcher — جلب الروابط المطلوب فحصها
# ===========================================================================

def fetch_links_to_check() -> list[dict]:
    """حجز وجلب أقدم روابط Dood المطلوب فحصها بشكل ذري."""
    try:
        res = supabase.rpc("claim_links_by_server", {
            "p_server_name": "dood",
            "p_batch_limit": BATCH_SIZE
        }).execute()
        links = res.data or []
        log(f"✅ تم حجز وجلب {len(links)} رابط Dood للفحص.")
        return links
    except Exception as e:
        log(f"❌ [Supabase Error] فشل حجز روابط Dood: {e}")
        return []

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
    """جلب الروابط → فحصها عبر دفعات (Batch) → حفظ النتائج."""
    log(f"🔍 [Dood Watcher] فحص أقدم {BATCH_SIZE} رابط...")

    links = fetch_links_to_check()
    if not links:
        log("✅ لا توجد روابط تحتاج فحصاً.")
        return

    all_results = []
    CHUNK_SIZE = 50

    async with httpx.AsyncClient(verify=False, follow_redirects=True) as client:
        for i in range(0, len(links), CHUNK_SIZE):
            chunk = links[i : i + CHUNK_SIZE]
            log(f"⚡ جاري فحص دفعة من {len(chunk)} روابط في طلب API واحد...")
            chunk_results = await process_links_batch(client, chunk)
            all_results.extend(chunk_results)

    save_results(all_results)


# ===========================================================================
# Entry Point
# ===========================================================================

if __name__ == "__main__":
    asyncio.run(run())