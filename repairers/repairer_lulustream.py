"""
repairer_lulustream.py
======================
مهمة إصلاح روابط LuluStream المكسورة عبر Remote Upload أو
تحميل Streamtape محلياً ثم رفعه مباشرةً.

الهيكل:
  Section 1  — Configuration
  Section 2  — Supabase Fetchers
  Section 3  — Source Resolver
  Section 4  — Archive Validator
  Section 5  — Streamtape Extractor  (Playwright)
  Section 6  — LuluStream Upload
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

LULUSTREAM_API_KEY = os.getenv("LULUSTREAM_API_KEY")
BATCH_SIZE         = int(os.getenv("BATCH_SIZE", "5"))

SOURCE_SERVERS = ["archive", "telegram_direct", "streamtape"]

BASE_API       = "https://www.lulustream.com/api"
EMBED_BASE     = "https://lulustream.com/e"

POLL_INTERVAL = 25   # ثانية بين كل فحص حالة
POLL_MAX      = 20   # أقصى عدد محاولات polling

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
    """جلب روابط LuluStream المكسورة بحسب BATCH_SIZE."""
    res = (
        supabase.table("links")
        .select("id, episode_id, url, server_name")
        .ilike("server_name", "%lulu%")
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
# Section 6: LuluStream Upload — رفع الملفات إلى LuluStream
# ===========================================================================

async def _upload_file_to_lulu(client: httpx.AsyncClient, temp_file: str) -> Optional[str]:
    """
    رفع ملف محلي مباشرةً إلى LuluStream.
    يُعيد file_code عند النجاح أو None عند الفشل.
    """
    log("   🚀 [Lulu] بدء الرفع المباشر للملف...")
    
    # 1. جلب رابط الرفع المؤقت
    try:
        url_resp = await client.get(f"{BASE_API}/upload/server?key={LULUSTREAM_API_KEY}")
        url_data = url_resp.json()
        upload_server = url_data.get("result")
        
        if not upload_server:
            log(f"   ❌ [Lulu] فشل جلب خادم الرفع: {url_data}")
            return None
    except Exception as e:
         log(f"   ❌ [Lulu] خطأ أثناء جلب خادم الرفع: {e}")
         return None

    # 2. الرفع المباشر على الخادم المخصص مع تمرير الـ API Key في Data
    try:
        with open(temp_file, "rb") as f:
            resp = await client.post(
                upload_server,
                data={"key": LULUSTREAM_API_KEY},
                files={"file": f},
                timeout=1200,
            )
            data = resp.json()
            log(f"   📡 [Lulu] رد الرفع: {data}")

            if data.get("status") == 200:
                file_code = data.get("result", {}).get("filecode")
                if file_code:
                    log(f"   ✅ [Lulu] تم الرفع! file_code={file_code}")
                    return file_code

            log(f"   ❌ [Lulu] رُفض الملف: {data.get('msg') or data}")

    except Exception as e:
        log(f"   ❌ [Lulu] خطأ أثناء الرفع المباشر: {e}")

    return None


async def upload_streamtape_to_lulu(
    client: httpx.AsyncClient, resolved_url: str, episode_id: int
) -> Optional[str]:
    """
    رفع محتوى Streamtape إلى LuluStream عبر تحميل مؤقت ثم رفع مباشر.
    يُعيد file_code عند النجاح أو None عند الفشل.
    """
    temp_file = f"temp_ep_{episode_id}.mp4"
    try:
        if not await _download_to_temp(resolved_url, temp_file):
            return None
        return await _upload_file_to_lulu(client, temp_file)
    finally:
        if os.path.exists(temp_file):
            os.remove(temp_file)
            log("   🗑️ [Streamtape] تم حذف الملف المؤقت.")


async def remote_upload_lulu(
    client: httpx.AsyncClient, source_url: str
) -> Optional[str]:
    """
    Remote Upload رابط مباشر (Archive / Telegram) إلى LuluStream مع polling.
    يُعيد file_code عند النجاح أو None عند الفشل.
    """
    log(f"   📡 [Lulu] Remote Upload من: {source_url}")

    try:
        add_url = (
            f"{BASE_API}/upload/url"
            f"?key={LULUSTREAM_API_KEY}"
            f"&url={urllib.parse.quote(source_url, safe='')}"
        )
        resp = await client.get(add_url, timeout=30.0)
        data = resp.json()
        log(f"   📡 [Lulu] رد: {data}")
    except Exception as e:
        log(f"   ❌ [Lulu] فشل الإرسال: {e}")
        return None

    if data.get("status") != 200:
        log(f"   ❌ [Lulu] رُفض: {data}")
        return None

    file_code = data.get("result", {}).get("filecode")
    if not file_code:
        log("   ❌ [Lulu] ما رجعش filecode!")
        return None

    log(f"   ⏳ [Lulu] file_code={file_code} | بدء Polling...")

    for attempt in range(1, POLL_MAX + 1):
        await asyncio.sleep(POLL_INTERVAL)
        try:
            info_resp = await client.get(
                f"{BASE_API}/file/info?key={LULUSTREAM_API_KEY}&file_code={file_code}",
                timeout=15.0,
            )
            info_data = info_resp.json()
            log(f"   🔄 [Lulu] محاولة {attempt}/{POLL_MAX} | status={info_data.get('status')}")

            if info_data.get("status") == 200 and info_data.get("result"):
                log("   ✅ [Lulu] الملف جاهز!")
                return file_code

        except Exception as e:
            log(f"   ⚠️ [Lulu] خطأ polling {attempt}: {e}")

    # نكتب الـ file_code حتى لو انتهت المحاولات — الملف ممكن يكون لسه بيتحمل
    log("   ⚠️ [Lulu] انتهت المحاولات، نكتب على المسؤولية.")
    return file_code


# ===========================================================================
# Section 7: Link Repair — إصلاح رابط واحد
# ===========================================================================

async def _upload_via_streamtape(
    client: httpx.AsyncClient, source_url: str, episode_id: int
) -> Optional[str]:
    """
    معالجة مصدر Streamtape:
      1. استخراج الرابط المباشر عبر Playwright
      2. تحميل مؤقت محلي
      3. رفع مباشر لـ LuluStream
    """
    log("   🕵️ [Repair] استخراج رابط Streamtape المباشر...")
    resolved_url = await resolve_streamtape(source_url)
    if not resolved_url:
        log("   ❌ [Repair] فشل استخراج رابط Streamtape.")
        return None
    return await upload_streamtape_to_lulu(client, resolved_url, episode_id)


async def _upload_via_remote(
    client: httpx.AsyncClient, source_url: str
) -> Optional[str]:
    """
    معالجة مصدر Archive أو Telegram عبر Remote Upload + Polling.
    """
    return await remote_upload_lulu(client, source_url)


async def _upload_source(
    client: httpx.AsyncClient,
    source_key: str,
    source_url: str,
    episode_id: int,
) -> Optional[str]:
    """توجيه عملية الرفع للدالة المناسبة بحسب نوع المصدر."""
    if source_key == "streamtape":
        return await _upload_via_streamtape(client, source_url, episode_id)
    return await _upload_via_remote(client, source_url)


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
        await mark_link_failed(link_id, "No active source found in DB")
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
        file_code = None
        for attempt in range(1, RETRY_COUNT + 1):
            log(f"   📡 محاولة [{attempt}/{RETRY_COUNT}] مصدر [{source_key}]...")
            file_code = await _upload_source(client, source_key, source_url, ep_id)
            if file_code:
                break
            if attempt < RETRY_COUNT:
                await asyncio.sleep(RETRY_DELAY)

        if file_code:
            new_url = f"{EMBED_BASE}/{file_code}"
            if update_link_in_db(link_id, old_url, new_url):
                log(f"   🎉 تم الإصلاح! {new_url}")
                return True
            await mark_link_failed(link_id, "DB update failed after successful upload")
            return False

        log(f"   ⏭️ [Repair] فشل المصدر [{source_key}] تماماً، الانتقال للتالي...")

    await mark_link_failed(link_id, "All sources failed")
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
    log("║    🔧 LULUSTREAM REPAIRER            ║")
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