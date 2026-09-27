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
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "50"))

DOOD_DOMAINS = [
    "doodapi.co",
    "doodapi.com",
    "dood.stream",
    "myvidplay.com",
    "playmogo.com",
]

API_TIMEOUT = 10.0
API_COOLDOWN = 8.0  # انتظار 8 ثوانٍ بين كل دفعة لتفادي الحظر

# رسائل الحذف الصريحة من API
API_DELETED_STATUSES = {"Not found or not your file", "Deleted", "Removed", "404"}
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


# ===========================================================================
# Section 4: API Checker — فحص Dood عبر API
# ===========================================================================


def _parse_api_file_info(
    file_info: dict, file_code: str
) -> tuple[Optional[bool], Optional[str]]:
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
    has_size = "size" in file_info or "length" in file_info
    has_title = "title" in file_info
    valid_status = (
        file_status == 200 or str(file_status) == "200" or file_status is None
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
) -> dict[str, tuple[Optional[bool], Optional[str]]]:
    """
    فحص مجموعة ملفات عبر API في طلب واحد.
    يُعيد: قاموس يربط file_code بنتيجته (is_valid, failure_reason)
    """
    results_map = {fc: (None, "API check failed or inconclusive") for fc in file_codes}
    if not file_codes:
        return results_map

    codes_str = ",".join(file_codes)

    for domain in DOOD_DOMAINS:
        api_url = (
            f"https://{domain}/api/file/info?key={DOOD_API_KEY}&file_code={codes_str}"
        )

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
                elif (
                    status_val in API_DELETED_STATUSES
                    or "not found" in status_val.lower()
                ):
                    results_map[fc] = (False, f"Dood API: {status_val}")
                else:
                    results_map[fc] = (
                        None,
                        f"Dood API: Unexpected status {status_val}",
                    )

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
    معالجة دفعة من الروابط بالاعتماد كلياً على فحص API فقط.
    """
    final_results = []

    code_to_links = {}
    for link in links:
        fc = extract_file_code(link["url"])
        if fc not in code_to_links:
            code_to_links[fc] = []
        code_to_links[fc].append(link)

    file_codes = list(code_to_links.keys())

    # الفحص عبر API فقط
    api_results = await check_via_api(client, file_codes)

    # تجميع النتائج لربطها بـ link_id مباشرة
    for fc, (api_valid, api_error) in api_results.items():
        if api_valid is True:
            status = "valid"
            error_msg = None
        elif api_valid is False:
            status = "broken"
            error_msg = api_error
        else:
            status = "pending"
            error_msg = api_error or "API Unavailable or Inconclusive"

        for link in code_to_links[fc]:
            final_results.append(
                (
                    link["id"],
                    status,
                    error_msg,
                    link["server_name"],
                    link["url"],
                    link.get("episode_id"),
                    link.get("check_count", 0),
                )
            )

    return final_results

# ===========================================================================
# Section 6: Supabase Fetcher — جلب الروابط المطلوب فحصها
# ===========================================================================


def fetch_links_to_check() -> list[dict]:
    """حجز وجلب أقدم روابط Dood المطلوب فحصها بشكل ذري."""
    try:
        res = supabase.rpc(
            "claim_links_by_server",
            {"p_server_name": "dood", "p_batch_limit": BATCH_SIZE},
        ).execute()
        links = res.data or []
        log(f"✅ تم حجز وجلب {len(links)} رابط Dood للفحص.")
        return links
    except Exception as e:
        log(f"❌ [Supabase Error] فشل حجز روابط Dood: {e}")
        return []


# ===========================================================================
# Section 7: Supabase Writer — حفظ النتائج
# ===========================================================================


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
    now = datetime.now().isoformat()
    bulk_updates = []

    for link_id, status, error, server_name, url, episode_id, check_count in results:
        icon = "✅" if status == "valid" else ("⏳" if status == "pending" else "❌")
        log(f"{icon} {link_id:<6} | {server_name:<12} | {status:<8} | {url} | 🔍 {error}")

        update_data = {
            "id": link_id,
            "episode_id": episode_id,
            "url": url,
            "server_name": server_name,
            "last_check_status": status,
            "error_message": error,
            "last_check_at": now,
            "is_fixed": status == "valid",
            "check_count": (check_count or 0) + 1,
        }

        bulk_updates.append(update_data)

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
            
            # تفعيل الانتظار إذا لم تكن هذه هي الدفعة الأخيرة
            if i + CHUNK_SIZE < len(links):
                log(f"⏳ انتظار {API_COOLDOWN} ثوانٍ لتفادي حظر الـ API...")
                await asyncio.sleep(API_COOLDOWN)

    save_results(all_results)


# ===========================================================================
# Entry Point
# ===========================================================================

if __name__ == "__main__":
    asyncio.run(run())
