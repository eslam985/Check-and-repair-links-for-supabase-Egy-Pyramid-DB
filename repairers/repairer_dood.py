"""
repairer_dood.py
================
مهمة إصلاح روابط Doodstream المكسورة عبر Remote Upload أو
تحميل Streamtape محلياً ثم رفعه مباشرةً.

الهيكل:
  Section 1  — Configuration
  Section 2  — Supabase Fetchers
  Section 3  — Source Resolver
  Section 4  — Archive Validator
  Section 5  — Streamtape Extractor  (Playwright)
  Section 6  — Doodstream Upload
  Section 7  — Link Repair (منطق رابط واحد)
  Section 8  — Main Orchestrator
"""

import os
import random
import asyncio
import urllib.parse
from datetime import datetime
from typing import Optional

import httpx
from playwright.async_api import async_playwright
from shared import supabase, log, update_link_in_db, mark_link_failed


# ===========================================================================
# Section 1: Configuration — الإعدادات المركزية
# ===========================================================================

DOOD_API_KEY = os.getenv("DOOD_API_KEY")
BATCH_SIZE   = int(os.getenv("BATCH_SIZE", "5"))

SOURCE_SERVERS = ["archive", "telegram_direct","streamtape",]

DOOD_DOMAINS  = ["doodapi.co", "doodapi.com", "dood.stream", "myvidplay.com"]
EMBED_BASE    = "https://myvidplay.com/e"

POLL_INTERVAL = 20   # ثانية بين كل فحص حالة
POLL_MAX      = 30   # أقصى عدد محاولات polling

RETRY_COUNT    = 3
RETRY_DELAY    = 5   # ثانية بين محاولات الرفع
COOLDOWN_DELAY = 3   # ثانية بين كل رابط وآخر

ARCHIVE_HEADERS = {"Range": "bytes=0-50000"}

_USER_AGENTS = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
)


# ===========================================================================
# Section 2: Supabase Fetchers — جلب وحفظ البيانات
# ===========================================================================

def fetch_broken_links() -> list[dict]:
    """جلب روابط Doodstream المكسورة بحسب BATCH_SIZE."""
    res = (
        supabase.table("links")
        .select("id, episode_id, url, server_name")
        .ilike("server_name", "%dood%")
        .eq("last_check_status", "broken")
        .eq("is_fixed", False)
        .order("last_check_at", desc=False, nullsfirst=True)
        .limit(BATCH_SIZE)
        .execute()
    )
    links = res.data or []
    log(f"   📥 روابط مكسورة جاهزة للإصلاح: {len(links)}")
    return links


def fetch_valid_sources(episode_id: int) -> list[dict]:
    """جلب المصادر الصحيحة المتاحة لحلقة معيّنة."""
    res = (
        supabase.table("links")
        .select("server_name, url")
        .eq("episode_id", episode_id)
        .in_("server_name", SOURCE_SERVERS)
        .in_("last_check_status", ["valid", "good"])
        .execute()
    )
    return res.data or []


# ===========================================================================
# Section 3: Source Resolver — اختيار وترتيب المصادر
# ===========================================================================

def extract_available_sources(raw_links: list[dict]) -> dict[str, str]:
    """استخراج الروابط المتاحة مع تنظيف اسم السيرفر."""
    return {
        link["server_name"].lower(): link["url"]
        for link in raw_links
        if link["server_name"].lower() in SOURCE_SERVERS
    }


def get_ordered_sources(available: dict[str, str]) -> list[str]:
    """إعادة المصادر المتاحة مرتبةً حسب الأولوية المحددة في SOURCE_SERVERS."""
    return [key for key in SOURCE_SERVERS if key in available]


def build_file_name(link_id: int, episode_id: int) -> str:
    """بناء اسم ملف ديناميكي موحد للتعرف عليه في Doodstream."""
    return f"link_{link_id}_ep_{episode_id}.mp4"


# ===========================================================================
# Section 4: Archive Validator — فحص سلامة روابط Archive.org
# ===========================================================================

async def _check_url_alive(client: httpx.AsyncClient, url: str) -> bool:
    """فحص سريع لسلامة رابط عبر HEAD ثم GET جزئي."""
    resp   = await client.head(url, timeout=7.0)
    status = resp.status_code

    if status == 200:
        resp   = await client.get(url, headers=ARCHIVE_HEADERS, timeout=7.0)
        status = resp.status_code

    content = resp.text.lower() if status == 200 else ""
    is_dead = status in [403, 404] or (
        status == 200
        and ("item not available" in content or "disabled" in content)
    )
    return not is_dead


async def is_archive_url_valid(client: httpx.AsyncClient, url: str) -> bool:
    """
    فحص صارم لسلامة رابط Archive.org بتأكيد مزدوج.
    للروابط غير Archive يُعيد True مباشرةً.
    """
    if "archive.org" not in url:
        return True

    url = str(url).strip()
    if "disabled" in url.lower() or not url.startswith("http"):
        log("   ❌ [Archive] رابط تالف أو ملغى نصياً.")
        return False

    try:
        log("   🔎 [Archive] فحص سلامة الرابط...")
        if not await _check_url_alive(client, url):
            log("   ⚠️ [Archive] اشتباه بالموت، إعادة التأكيد بعد 3 ثوانٍ...")
            await asyncio.sleep(3)
            if not await _check_url_alive(client, url):
                log("   ❌ [Archive] تم تأكيد موت الرابط نهائياً.")
                return False
            log("   🛡️ [Archive] الرابط عاد للعمل في المحاولة الثانية.")
        return True

    except Exception as e:
        log(f"   ⚠️ [Archive] خطأ شبكة عابر أثناء الفحص: {e}")
        return True  # نمرره عند خطأ شبكة غامض لإعطاء فرصة للمحرك


# ===========================================================================
# Section 5: Streamtape Extractor — استخراج رابط التحميل المباشر
# ===========================================================================

async def _try_extract_from_dom(page) -> Optional[str]:
    """محاولة استخراج الرابط مباشرةً من الـ DOM دون الضغط على الزر."""
    try:
        raw_link = await page.locator("#norobotlink").text_content(timeout=5000)
        if raw_link and "get_video" in raw_link:
            raw_link = raw_link.strip()
            if raw_link.startswith("//"):
                raw_link = f"https:{raw_link}"
            return raw_link if "dl=1" in raw_link else f"{raw_link}&dl=1"
    except Exception:
        pass
    return None


async def _try_extract_via_click(page, ctx) -> Optional[str]:
    """استخراج رابط Streamtape بالضغط على الزر وانتظار العداد."""
    btn = "#downloadvideo"
    await page.wait_for_selector(btn, state="visible", timeout=15_000)

    try:
        async with ctx.expect_page(timeout=5000) as new_page_info:
            await page.click(btn)
        ad_page = await new_page_info.value
        await ad_page.close()
    except Exception:
        pass

    await page.bring_to_front()
    log("   ⏳ [Streamtape] انتظار انتهاء العداد (6 ثوانٍ)...")
    await page.wait_for_timeout(6000)

    href = await page.get_attribute(btn, "href")
    if href and "get_video" in href:
        href = href.strip()
        return f"https:{href}" if href.startswith("//") else href

    return None


async def resolve_streamtape(embed_url: str) -> Optional[str]:
    """
    استخراج رابط التحميل المباشر من Streamtape عبر Playwright.
    يجرب DOM أولاً، ثم يلجأ للضغط الفعلي على الزر.
    """
    target = embed_url.replace("/e/", "/v/").replace("/f/", "/v/")
    log(f"   🕵️ [Streamtape] Playwright → {target}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        ctx  = await browser.new_context(user_agent=random.choice(_USER_AGENTS))
        page = await ctx.new_page()

        try:
            await page.goto(target, wait_until="domcontentloaded")

            result = await _try_extract_from_dom(page)
            if result:
                log(f"   ✅ [Streamtape] رابط من DOM: {result[:60]}...")
                return result

            result = await _try_extract_via_click(page, ctx)
            if result:
                log(f"   ✅ [Streamtape] رابط عبر الضغط: {result[:60]}...")
                return result

            page_text = await page.inner_text("body")
            is_dead   = (
                "video no longer available" in page_text.lower()
                or "not found" in page_text.lower()
            )
            log(f"   ❌ [Streamtape] فشل الاستخراج. محذوف: {is_dead}")
            return None

        except Exception as e:
            log(f"   ❌ [Streamtape] خطأ: {e}")
            return None
        finally:
            await browser.close()


async def _download_to_temp(url: str, temp_file: str) -> bool:
    """تحميل ملف من رابط مباشر إلى ملف مؤقت محلي."""
    log("   📥 [Streamtape] جاري سحب الملف مؤقتاً...")
    try:
        async with httpx.AsyncClient(verify=False, follow_redirects=True) as client:
            async with client.stream("GET", url, timeout=120) as r:
                r.raise_for_status()
                total    = int(r.headers.get("content-length", 0))
                done     = 0
                chunk_mb = 1024 * 1024

                with open(temp_file, "wb") as f:
                    async for chunk in r.aiter_bytes(chunk_size=chunk_mb):
                        if chunk:
                            f.write(chunk)
                            done += len(chunk)
                            if total > 0 and done % (chunk_mb * 100) < len(chunk):
                                log(f"   ⏳ {done // chunk_mb}MB / {total // chunk_mb}MB")
        return True
    except Exception as e:
        log(f"   ❌ [Streamtape] فشل التحميل المحلي: {e}")
        return False


# ===========================================================================
# Section 6: Doodstream Upload — رفع الملفات إلى Doodstream
# ===========================================================================

async def _poll_dood_status(
    client: httpx.AsyncClient, f_code: str, file_name: str
) -> Optional[str]:
    """
    Polling على حالة الملف في Doodstream بثلاثة خطوط دفاع:
      1. api/file/info   — الفحص الرئيسي
      2. api/file/check  — خط الدفاع الثاني
      3. api/file/list   — البحث بالاسم (كل محاولتين)
    يُعيد f_code عند التأكيد أو None عند انتهاء المحاولات.
    """
    search_term = file_name.split(".")[0].strip()

    for attempt in range(1, POLL_MAX + 1):
        await asyncio.sleep(POLL_INTERVAL)
        log(f"   🔄 [Dood] محاولة {attempt}/{POLL_MAX}...")

        for domain in DOOD_DOMAINS:
            try:
                # خط الدفاع الأول: api/file/info
                info_url = f"https://{domain}/api/file/info?key={DOOD_API_KEY}&file_code={f_code}"
                res      = await client.get(info_url, timeout=10.0)
                info     = res.json()

                if info.get("status") == 200:
                    result_list = info.get("result", [{}])
                    result      = result_list[0] if isinstance(result_list, list) and result_list else {}
                    resp_code   = result.get("filecode") or result.get("file_code")
                    if resp_code == f_code:
                        log(f"   ✅ [Dood] مؤكد عبر file/info في محاولة {attempt}")
                        return f_code

                # خط الدفاع الثاني: api/file/check
                check_url  = f"https://{domain}/api/file/check?key={DOOD_API_KEY}&file_code={f_code}"
                check_res  = await client.get(check_url, timeout=10.0)
                check_data = check_res.json()
                if check_data.get("status") == 200 and check_data.get("result"):
                    log(f"   ✅ [Dood] مؤكد عبر file/check في محاولة {attempt}")
                    return f_code

            except Exception:
                continue

        # خط الدفاع الثالث: البحث بالاسم (كل محاولتين لتقليل الضغط)
        if attempt % 2 == 0:
            try:
                list_url = f"https://doodapi.co/api/file/list?key={DOOD_API_KEY}&per_page=10"
                l_res    = await client.get(list_url, timeout=10.0)
                files    = l_res.json().get("result", {}).get("files", [])
                for f in files:
                    if search_term in f.get("title", ""):
                        found = f.get("file_code") or f.get("filecode")
                        log(f"   ✅ [Dood] عُثر على الملف بالاسم: {f.get('title')}")
                        return found
            except Exception:
                pass

    log("   🛑 [Dood] انتهت محاولات الـ Polling.")
    return None


async def _upload_file_to_dood(
    client: httpx.AsyncClient, temp_file: str, file_name: str
) -> Optional[str]:
    """
    رفع ملف محلي مباشرةً إلى Doodstream عبر أول domain متاح.
    يُعيد f_code عند النجاح أو None عند الفشل.
    """
    log("   🚀 [Dood] بدء الرفع المباشر للملف...")
    safe_title = urllib.parse.quote(file_name)

    for domain in DOOD_DOMAINS:
        try:
            upload_url = (
                f"https://{domain}/api/upload/server"
                f"?key={DOOD_API_KEY}"
            )
            # جلب سيرفر الرفع أولاً
            srv_res  = await client.get(upload_url, timeout=15.0)
            srv_data = srv_res.json()
            if srv_data.get("status") != 200:
                continue

            server_url = srv_data.get("result", "")
            with open(temp_file, "rb") as f:
                resp = await client.post(
                    server_url,
                    data={"api_key": DOOD_API_KEY, "new_title": safe_title},
                    files={"file": (file_name, f, "video/mp4")},
                    timeout=1200,
                )
            data = resp.json()
            log(f"   📡 [Dood] رد الرفع: {data}")

            if data.get("status") == 200:
                f_code = data.get("result", {}).get("filecode") or data.get("result", {}).get("file_code")
                if f_code:
                    log(f"   ✅ [Dood] تم الرفع! f_code={f_code}")
                    return f_code

        except Exception as e:
            log(f"   ⚠️ [Dood] {domain} فشل في الرفع المباشر: {e}")
            continue

    log("   ❌ [Dood] فشل الرفع المباشر على جميع الـ domains.")
    return None


async def upload_streamtape_to_dood(
    client: httpx.AsyncClient,
    resolved_url: str,
    link_id: int,
    episode_id: int,
) -> Optional[str]:
    """
    رفع محتوى Streamtape إلى Doodstream عبر تحميل مؤقت ثم رفع مباشر.
    يُعيد f_code عند النجاح أو None عند الفشل.
    """
    file_name = build_file_name(link_id, episode_id)
    temp_file = f"temp_{file_name}"
    try:
        if not await _download_to_temp(resolved_url, temp_file):
            return None
        return await _upload_file_to_dood(client, temp_file, file_name)
    finally:
        if os.path.exists(temp_file):
            os.remove(temp_file)
            log("   🗑️ [Streamtape] تم حذف الملف المؤقت.")


async def remote_upload_dood(
    client: httpx.AsyncClient, source_url: str, file_name: str
) -> Optional[str]:
    """
    Remote Upload رابط مباشر (Archive / Telegram) إلى Doodstream مع polling.
    يُعيد f_code عند النجاح أو None عند الفشل.
    """
    log(f"   📡 [Dood] Remote Upload من: {source_url}")
    safe_title = urllib.parse.quote(file_name)
    data       = None

    for domain in DOOD_DOMAINS:
        try:
            add_url = (
                f"https://{domain}/api/upload/url"
                f"?key={DOOD_API_KEY}"
                f"&url={urllib.parse.quote(source_url, safe='')}"
                f"&new_title={safe_title}"
            )
            resp = await client.get(add_url, timeout=20.0)
            data = resp.json()
            log(f"   📡 [Dood] {domain}: {data}")
            if data.get("msg") == "OK":
                log(f"   ✅ [Dood] قُبل الأمر عبر {domain}")
                break
        except Exception as e:
            log(f"   ⚠️ [Dood] {domain} فشل: {e}")
            continue

    if not data or data.get("msg") != "OK":
        log("   ❌ [Dood] كل الـ domains فشلت في Remote Upload.")
        return None

    f_code = data.get("result", {}).get("filecode")
    if not f_code:
        log("   ❌ [Dood] ما رجعش filecode!")
        return None

    log(f"   ⏳ [Dood] f_code={f_code} | بدء Polling...")
    return await _poll_dood_status(client, f_code, file_name)


# ===========================================================================
# Section 7: Link Repair — إصلاح رابط واحد
# ===========================================================================

async def _upload_via_streamtape(
    client: httpx.AsyncClient,
    source_url: str,
    link_id: int,
    episode_id: int,
) -> Optional[str]:
    """
    معالجة مصدر Streamtape:
      1. استخراج الرابط المباشر عبر Playwright
      2. تحميل مؤقت محلي
      3. رفع مباشر لـ Doodstream
    """
    log("   🕵️ [Repair] استخراج رابط Streamtape المباشر...")
    resolved_url = await resolve_streamtape(source_url)
    if not resolved_url:
        log("   ❌ [Repair] فشل استخراج رابط Streamtape.")
        return None
    return await upload_streamtape_to_dood(client, resolved_url, link_id, episode_id)


async def _upload_via_remote(
    client: httpx.AsyncClient,
    source_url: str,
    link_id: int,
    episode_id: int,
) -> Optional[str]:
    """
    معالجة مصدر Archive أو Telegram عبر Remote Upload + Polling.
    """
    file_name = build_file_name(link_id, episode_id)
    return await remote_upload_dood(client, source_url, file_name)


async def _upload_source(
    client: httpx.AsyncClient,
    source_key: str,
    source_url: str,
    link_id: int,
    episode_id: int,
) -> Optional[str]:
    """توجيه عملية الرفع للدالة المناسبة بحسب نوع المصدر."""
    if source_key == "streamtape":
        return await _upload_via_streamtape(client, source_url, link_id, episode_id)
    return await _upload_via_remote(client, source_url, link_id, episode_id)


async def repair_link(client: httpx.AsyncClient, link: dict) -> bool:
    """
    إصلاح رابط واحد: يجرب المصادر المتاحة بالترتيب حتى ينجح أحدها.
    يُعيد True عند نجاح الإصلاح.
    """
    link_id  = link["id"]
    ep_id    = link["episode_id"]
    old_url  = link["url"]

    raw_sources = fetch_valid_sources(ep_id)
    available   = extract_available_sources(raw_sources)
    ordered     = get_ordered_sources(available)

    if not ordered:
        log(f"   ⚠️ [Repair] لا يوجد أي مصدر متاح للحلقة {ep_id}. تخطي.")
        mark_link_failed(link_id, "No active source found in DB")
        return False

    log(f"   🔍 [Repair] حلقة ID: {ep_id} | المصادر: {ordered}")

    for source_key in ordered:
        source_url = available[source_key]
        log(f"   ✅ [Source] المصدر الحالي: [{source_key}] → {source_url}")

        # فحص Archive قبل أي شيء
        if source_key == "archive" and not await is_archive_url_valid(client, source_url):
            log("   ❌ [Archive] الرابط ميت! الانتقال للمصدر التالي...")
            continue

        # محاولات الرفع مع Retry
        f_code = None
        for attempt in range(1, RETRY_COUNT + 1):
            log(f"   📡 محاولة [{attempt}/{RETRY_COUNT}] مصدر [{source_key}]...")
            f_code = await _upload_source(client, source_key, source_url, link_id, ep_id)
            if f_code:
                break
            if attempt < RETRY_COUNT:
                await asyncio.sleep(RETRY_DELAY)

        if f_code:
            new_url = f"{EMBED_BASE}/{f_code}"
            if update_link_in_db(link_id, old_url, new_url):
                log(f"   🎉 تم الإصلاح! {new_url}")
                return True
            mark_link_failed(link_id, "DB update failed after successful upload")
            return False

        log(f"   ⏭️ [Repair] فشل المصدر [{source_key}] تماماً، الانتقال للتالي...")

    mark_link_failed(link_id, "All sources failed")
    return False


# ===========================================================================
# Section 8: Main Orchestrator — المنسق الرئيسي
# ===========================================================================

async def run() -> None:
    """
    النقطة الرئيسية لمهمة الإصلاح:
    جلب الروابط التالفة → إصلاح كل رابط → تسجيل النتائج.
    """
    now = datetime.now().strftime("%H:%M:%S")
    log("╔══════════════════════════════════════╗")
    log("║      🔧 DOODSTREAM REPAIRER          ║")
    log(f"║  [{now}] Batch: {BATCH_SIZE:<22}║")
    log("╚══════════════════════════════════════╝\n")

    links_to_repair = fetch_broken_links()
    if not links_to_repair:
        log("✅ لا يوجد روابط مكسورة!")
        return

    stats = {"fixed": 0, "failed": 0}

    async with httpx.AsyncClient(verify=False, follow_redirects=True) as client:
        for i, link in enumerate(links_to_repair, 1):
            log(f"\n{'─' * 55}")
            log(f"[{i}/{len(links_to_repair)}] link_id={link['id']} | episode_id={link['episode_id']}")
            log(f"   🔴 {link['url']}")

            repaired = await repair_link(client, link)

            if repaired:
                stats["fixed"] += 1
            else:
                stats["failed"] += 1

            await asyncio.sleep(COOLDOWN_DELAY)

    now = datetime.now().strftime("%H:%M:%S")
    log(f"\n{'═' * 55}")
    log(f"✨ [{now}] المهمة انتهت! ✅ {stats['fixed']} | ❌ {stats['failed']}")
    log(f"{'═' * 55}")


# ===========================================================================
# Entry Point
# ===========================================================================

if __name__ == "__main__":
    asyncio.run(run())