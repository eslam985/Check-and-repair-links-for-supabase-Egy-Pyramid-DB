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
BATCH_SIZE         = int(os.getenv("BATCH_SIZE", "50"))

LULU_API_BASE   = "https://www.lulustream.com/api"
LULU_EMBED_BASE = "https://www.lulustream.com/e"

API_TIMEOUT   = 12.0
EMBED_TIMEOUT = 8.0
API_COOLDOWN  = 1.0  # ثانية بين كل طلب API

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
    client: httpx.AsyncClient, file_code: str
) -> tuple[bool, Optional[str]]:
    """
    فحص الملف عبر Lulu API.
    يُعيد: (is_valid, failure_reason)
    - is_valid=True → الملف موجود
    - is_valid=False, failure_reason=None → API مش متأكد (soft fail) → pending
    - is_valid=False, failure_reason=str → فشل واضح
    """
    api_url = f"{LULU_API_BASE}/file/info?key={LULUSTREAM_API_KEY}&file_code={file_code}"

    await asyncio.sleep(API_COOLDOWN)

    try:
        res = await client.get(api_url, timeout=API_TIMEOUT)

        # Rate limit أو مشكلة مؤقتة
        if res.status_code in RATE_LIMIT_CODES:
            log(f"⚠️ Rate Limited ({res.status_code}) → pending")
            return False, None  # None = soft fail → pending

        try:
            data = res.json()
        except Exception:
            log(f"⚠️ Invalid JSON من API → pending")
            return False, None

        # API معطل أو مشكلة مصادقة
        api_msg = str(data.get("msg", "")).strip()
        if _is_api_soft_fail(api_msg):
            log(f"⚠️ API Soft Fail: '{api_msg}' → pending (لا نعتبره broken)")
            return False, None

        # الملف موجود وسليم
        if data.get("status") == 200 and data.get("result"):
            return True, None

        # الـ API رفض الملف برسالة واضحة
        return False, f"Lulu API: {api_msg or 'Not Found'}"

    except Exception as e:
        log(f"⚠️ خطأ في API: {e} → pending")
        return False, None  # أي خطأ شبكي → pending


# ===========================================================================
# Section 4: HTML Checker — التأكيد المزدوج عبر صفحة Embed
# ===========================================================================

async def check_via_html(
    client: httpx.AsyncClient, file_code: str
) -> tuple[bool, Optional[str]]:
    """
    فحص صفحة الـ Embed كتأكيد مزدوج بعد نجاح API.
    يُعيد: (is_valid, failure_reason)
    """
    embed_url = f"{LULU_EMBED_BASE}/{file_code}"

    try:
        res = await client.get(embed_url, timeout=EMBED_TIMEOUT)

        # Rate limit على صفحة الـ embed
        if res.status_code in RATE_LIMIT_CODES:
            log(f"⚠️ Embed Rate Limited ({res.status_code}) → pending")
            return True, None  # نعتبرها valid لأن API قال موجود

        html = res.text.lower()

        # صفحة فارغة أو تالفة = soft rate limit
        if "html" not in html and "body" not in html:
            log("⚠️ Soft Rate Limited (Corrupted HTML) → pending")
            return True, None  # نعتبرها valid لأن API قال موجود

        # فحص رسائل الحذف الصريحة
        for marker in HTML_DELETED_MARKERS:
            if marker in res.text:
                return False, "Lulu: Expired or Deleted (HTML Check)"

        return True, None

    except Exception as e:
        log(f"⚠️ خطأ في HTML Check: {e} → نعتمد على API فقط")
        return True, None  # لو HTML فشل نعتمد على API


# ===========================================================================
# Section 5: Link Status Resolver — تحديد الحالة النهائية للرابط
# ===========================================================================

async def resolve_link_status(
    client: httpx.AsyncClient, link_id: int, url: str, server_name: str
) -> tuple[int, str, Optional[str], str, str]:
    """
    تحديد الحالة النهائية للرابط بمرحلتين: API → HTML.
    القاعدة الذهبية: الشك → pending. broken بس عند يقين كامل.
    يُعيد: (link_id, status, error_msg, server_name, url)
    """
    async with sem:
        file_code = extract_file_code(url)

        # ── المرحلة الأولى: API ──────────────────────────────────────
        api_valid, api_error = await check_via_api(client, file_code)

        # API مش متأكد (soft fail) → pending مباشرةً
        if not api_valid and api_error is None:
            return link_id, "pending", "API Unavailable or Auth Issue", server_name, url

        # API قال الملف مش موجود برسالة واضحة → broken
        if not api_valid and api_error:
            return link_id, "broken", api_error, server_name, url

        # ── المرحلة الثانية: HTML (تأكيد مزدوج) ─────────────────────
        html_valid, html_error = await check_via_html(client, file_code)

        if not html_valid:
            return link_id, "broken", html_error, server_name, url

        return link_id, "valid", None, server_name, url


# ===========================================================================
# Section 6: Supabase Fetcher — جلب الروابط المطلوب فحصها
# ===========================================================================

def fetch_links_to_check() -> list[dict]:
    """جلب أقدم روابط Lulu المطلوب فحصها بخوارزمية ترتيب متعددة المستويات."""
    res = (
        supabase.table("links")
        .select("id, url, server_name, last_check_status, created_at, last_check_at, check_count")
        .ilike("server_name", "%lulu%")
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
    log(f"✅ تم جلب {len(links)} رابط للفحص.")
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
    log(f"🔍 [Lulustream Watcher] فحص أقدم {BATCH_SIZE} رابط...")

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