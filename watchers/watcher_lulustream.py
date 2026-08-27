"""
watcher_lulustream.py
=====================
فحص روابط LuluStream عبر API أولاً ثم HTML كتأكيد مزدوج.

⚠️ قاعدة الأمان الذهبية:
لو الـ API معطل أو رجع خطأ مصادقة أو أي شك → pending مش broken.
broken بس لما نتأكد 100% إن الملف محذوف من فحص HTML.
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

LULUSTREAM_API_KEY = os.getenv("LULUSTREAM_API_KEY")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "50"))

LULU_API_BASE = "https://www.lulustream.com/api"
LULU_EMBED_BASE = "https://www.lulustream.com/e"

API_TIMEOUT = 12.0
EMBED_TIMEOUT = 8.0
API_COOLDOWN = 1.0  # ثانية بين كل طلب API
# مفتاح إيقاف عام عند نفاذ رصيد الـ API
API_QUOTA_EXHAUSTED = False
# رسائل تدل على تعطل API أو مشكلة مصادقة — لا نعتبرها broken
API_SOFT_FAIL_MSGS = {
    "wrong auth",
    "api disabled",
    "invalid key",
    "access denied",
    "unauthorized",
}

# رسائل تدل على حذف الملف فعلاً في HTML
HTML_DELETED_MARKERS = [
    "File is no longer available",
    "has been deleted",
    "File is no longer available as it expired or has been deleted.",
]

# كودات HTTP تعني rate limit أو مشكلة مؤقتة → pending
RATE_LIMIT_CODES = {403, 429, 503}

sem = asyncio.Semaphore(1)


# ===========================================================================
# Section 2: File Code Extractor — استخراج كود الملف من الرابط
# ===========================================================================


def extract_file_code(url: str) -> str:
    """
    استخراج file_code من رابط Lulu.
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


# ===========================================================================
# Section 3: API Checker — فحص Lulu عبر API
# ===========================================================================


def _is_api_soft_fail(msg: str) -> bool:
    """هل رسالة الـ API تدل على تعطل أو مشكلة مصادقة (مش حذف فعلي)؟"""
    return any(keyword in msg.lower() for keyword in API_SOFT_FAIL_MSGS)


async def check_via_api(
    client: httpx.AsyncClient, file_codes: list[str]
) -> dict[str, tuple[bool, Optional[str]]]:
    """
    فحص مجموعة ملفات عبر API في طلب واحد.
    يُعيد: قاموس يربط file_code بنتيجته (is_valid, failure_reason)
    """
    global API_QUOTA_EXHAUSTED
    # الحالة الافتراضية لكل الملفات هي pending
    results_map = {fc: (False, None) for fc in file_codes}

    if not file_codes or API_QUOTA_EXHAUSTED:
        return results_map

    # دمج الأكواد بفاصلة للطلب المجمع
    codes_str = ",".join(file_codes)
    api_url = f"{LULU_API_BASE}/file/info?key={LULUSTREAM_API_KEY}&file_code={codes_str}"

    await asyncio.sleep(API_COOLDOWN)

    try:
        res = await client.get(api_url, timeout=API_TIMEOUT)

        if res.status_code in RATE_LIMIT_CODES:
            log(f"⚠️ Rate Limited ({res.status_code}) → pending")
            return results_map

        try:
            data = res.json()
        except Exception:
            log("⚠️ Invalid JSON من API → pending")
            return results_map

        api_msg = str(data.get("msg", "")).strip()
        if _is_api_soft_fail(api_msg):
            log(f"⚠️ API Soft Fail: '{api_msg}' → pending")
            return results_map

        requests_left = data.get("requests_available")
        if requests_left is not None and int(requests_left) <= 0:
            log("🚫 API Quota Exhausted (requests_available = 0) → تفعيل إيقاف السكربت بالكامل")
            API_QUOTA_EXHAUSTED = True
            return results_map

        if data.get("status") == 200 and isinstance(data.get("result"), list):
            for file_info in data["result"]:
                fc = file_info.get("file_code")
                if not fc:
                    continue
                
                file_status = file_info.get("status")
                if file_status == 200:
                    results_map[fc] = (True, None)
                elif file_status == 404:
                    results_map[fc] = (False, "Lulu API: File Not Found (404)")
                else:
                    results_map[fc] = (False, f"Lulu API: Unexpected status {file_status}")
                    
        return results_map

    except Exception as e:
        log(f"⚠️ خطأ في API: {e} → pending")
        return results_map


# ===========================================================================
# Section 4: HTML Checker — التأكيد المزدوج عبر صفحة Embed
# ===========================================================================


async def check_via_html(
    client: httpx.AsyncClient, file_code: str
) -> tuple[str, Optional[str]]:
    """
    فحص صفحة الـ Embed كتأكيد مزدوج.
    يُعيد: (status, failure_reason) -> status: 'valid' | 'broken' | 'pending'
    """
    embed_url = f"{LULU_EMBED_BASE}/{file_code}"

    try:
        res = await client.get(embed_url, timeout=EMBED_TIMEOUT)

        # Rate limit على صفحة الـ embed
        if res.status_code in RATE_LIMIT_CODES:
            log(f"⚠️ Embed Rate Limited ({res.status_code}) → pending")
            return "pending", "Embed Rate Limited"

        html = res.text.lower()
        # فحص وضع الصيانة
        if "maintenance mode" in html:
            log("⚠️ Server in Maintenance Mode → pending")
            return "pending", "Server Maintenance Mode"
        
        # صفحة فارغة أو حظر من Cloudflare
        if "html" not in html and "body" not in html:
            log("⚠️ Soft Rate Limited (Corrupted HTML) → pending")
            return "pending", "Corrupted HTML / Soft Rate Limit"

        # فحص رسائل الحذف الصريحة
        for marker in HTML_DELETED_MARKERS:
            if marker in res.text:
                return "broken", "Lulu: Expired or Deleted (HTML Check)"

        return "valid", None

    except Exception as e:
        log(f"⚠️ خطأ في HTML Check: {e} → pending")
        return "pending", f"HTML Check Error: {e}"


# ===========================================================================
# Section 5: Link Status Resolver — تحديد الحالة النهائية للرابط
# ===========================================================================


async def process_links_batch(
    client: httpx.AsyncClient, links: list[dict]
) -> list[tuple]:
    """
    معالجة دفعة من الروابط: API مجمع → HTML فردي للملفات السليمة.
    """
    global API_QUOTA_EXHAUSTED
    final_results = []
    
    # 1. استخراج file_codes وتجنب التكرار (قد يتكرر الرابط لنفس الملف)
    code_to_links = {}
    for link in links:
        fc = extract_file_code(link["url"])
        if fc not in code_to_links:
            code_to_links[fc] = []
        code_to_links[fc].append(link)

    file_codes = list(code_to_links.keys())
    
    # 2. فحص الدفعة بالكامل عبر طلب API واحد
    api_results = await check_via_api(client, file_codes)

    # 3. توجيه الملفات التي اجتازت الـ API إلى فحص HTML
    async def resolve_single_fc(fc: str, api_valid: bool, api_error: Optional[str]):
        if not api_valid:
            status = "broken" if api_error else "pending"
            error_msg = api_error if api_error else "API Unavailable or Auth Issue"
            if API_QUOTA_EXHAUSTED:
                error_msg = "API Quota Exhausted"
            return fc, status, error_msg

        # حماية الطلبات المتزامنة للـ HTML بالـ Semaphore
        async with sem:
            html_status, html_error = await check_via_html(client, fc)
            
        return fc, html_status, html_error

    # تشغيل فحص HTML للملفات السليمة بشكل متوازٍ
    tasks = [
        resolve_single_fc(fc, api_results[fc][0], api_results[fc][1])
        for fc in file_codes
    ]
    resolved_codes = await asyncio.gather(*tasks)

    # 4. إعادة تجميع النتائج لربطها بـ link_id في قاعدة البيانات
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
    """حجز وجلب أقدم روابط Lulu المطلوب فحصها بشكل ذري."""
    try:
        res = supabase.rpc("claim_links_by_server", {
            "p_server_name": "lulu",
            "p_batch_limit": BATCH_SIZE
        }).execute()
        links = res.data or []
        log(f"✅ تم حجز وجلب {len(links)} رابط Lulu للفحص.")
        return links
    except Exception as e:
        log(f"❌ [Supabase Error] فشل حجز روابط Lulu: {e}")
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
    """
    حفظ النتائج دفعة واحدة (Bulk Upsert).
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
    """
    تجميع النتائج وحفظها في Supabase.
    يطبع لوج لكل رابط ثم يحفظ الكل دفعة واحدة.
    """
    now = datetime.now().isoformat()
    bulk_updates = []
    link_ids = []

    for link_id, status, error, server_name, url in results:
        link_ids.append(link_id)

        icon = "✅" if status == "valid" else ("⏳" if status == "pending" else "❌")
        log(f"{icon} {link_id:<6} | {server_name:<12} | {status:<8} | {url}")

        bulk_updates.append(
            {
                "id": link_id,
                "url": url,
                "server_name": server_name,
                "last_check_status": status,
                "error_message": error,
                "last_check_at": now,
            }
        )

    _increment_check_counts(link_ids)
    _bulk_upsert(bulk_updates)


# ===========================================================================
# Section 8: Main Runner — المنسق الرئيسي
# ===========================================================================


async def run() -> None:
    """جلب الروابط → فحصها عبر دفعات (Batch) → حفظ النتائج."""
    log(f"🔍 [Lulustream Watcher] فحص أقدم {BATCH_SIZE} رابط...")

    links = fetch_links_to_check()
    if not links:
        log("✅ لا توجد روابط تحتاج فحصاً.")
        return

    all_results = []
    # تقسيم الروابط إلى دفعات كحد أقصى 50 لتجنب تجاوز الحد الأقصى لطول مسار الـ URL
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
