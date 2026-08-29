"""
mixdrop_repair.py
=================
مهمة إصلاح الروابط التالفة في MixDrop عبر Remote Upload أو
تحميل Streamtape محلياً ثم رفعه مباشرةً.

الهيكل:
  Section 1  — Configuration
  Section 2  — Supabase Fetchers
  Section 3  — Source Resolver
  Section 4  — Archive Validator
  Section 5  — Streamtape Extractor  (Playwright)
  Section 6  — MixDrop Upload
  Section 7  — Hunter Mode (polling)
  Section 8  — Link Repair (منطق حلقة واحدة)
  Section 9  — Main Orchestrator
"""

import os
import time
import random
import asyncio
import logging
from datetime import datetime
from typing import Optional
from urllib.parse import quote

import requests
from playwright.async_api import async_playwright
from shared import supabase, log as shared_log

# ===========================================================================
# Section 1: Configuration — الإعدادات المركزية
# ===========================================================================

MIXDROP_EMAIL = os.environ.get("MIXDROP_EMAIL")
MIXDROP_KEY = os.environ.get("MIXDROP_KEY")
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "5"))

TARGET_SERVER = "mixdrop"
SOURCE_SERVERS = ["archive", "streamtape"]

MIXDROP_UPLOAD_URL = "https://ul.mixdrop.ag/api"
MIXDROP_REMOTE_URL = "https://api.mixdrop.ag/remoteupload"
MIXDROP_STATUS_URL = "https://api.mixdrop.ag/remotestatus"
MIXDROP_EMBED_BASE = "https://mixdrop.ag/e"

HUNTER_MAX_ATTEMPTS = 100
HUNTER_WAIT = 30  # ثانية بين كل فحص

RETRY_COUNT = 3
RETRY_DELAY = 5  # ثانية بين محاولات الرفع
COOLDOWN_DELAY = 5  # ثانية بين كل رابط وآخر

ARCHIVE_HEADERS = {"Range": "bytes=0-50000"}

_USER_AGENTS = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
_log = logging.getLogger("MixdropRepair")


def log(msg: str) -> None:
    """جسر بين shared_log والـ logger المحلي."""
    shared_log(msg)
    _log.info(msg)


# ===========================================================================
# Section 2: Supabase Fetchers — جلب وحفظ البيانات
# ===========================================================================


def fetch_broken_links() -> list[dict]:
    """جلب الروابط التالفة المستهدفة من جدول links بحسب BATCH_SIZE."""
    response = (
        supabase.table("links")
        .select("id, episode_id, url, server_name")
        .ilike("server_name", f"%{TARGET_SERVER}%")
        .eq("last_check_status", "broken")
        .eq("error_message", "404_DELETED")
        .eq("is_fixed", False)
        .limit(BATCH_SIZE)
        .execute()
    )
    links = response.data or []
    log(f"   📥 تم العثور على {len(links)} رابط مكسور وجاهز للإصلاح.")
    return links


def fetch_valid_sources(episode_id: int) -> list[dict]:
    """جلب المصادر الصحيحة المتاحة لحلقة معيّنة."""
    response = (
        supabase.table("links")
        .select("server_name, url")
        .eq("episode_id", episode_id)
        .eq("last_check_status", "valid")
        .execute()
    )
    return response.data or []


def mark_link_fixed(link_id: int, final_url: str) -> None:
    """تحديث الرابط التالف في قاعدة البيانات بعد الإصلاح الناجح."""
    supabase.table("links").update(
        {
            "url": final_url,
            "last_check_status": "valid",
            "error_message": None,
            "is_fixed": True,
            "last_check_at": datetime.now().isoformat(),
        }
    ).eq("id", link_id).execute()
    log(f"   ✨ تم تحديث الرابط (id={link_id}) → {final_url}")

import re

def build_filename_for_episode(episode_id: int) -> str:
    """جلب بيانات الحلقة والميديا وتشكيل اسم الملف الديناميكي."""
    try:
        res = (
            supabase.table("episodes")
            .select("episode_number, media_id, season_id, medias(id, title, slug, media_type), seasons(season_number)")
            .eq("id", episode_id)
            .single()
            .execute()
        )
        data = res.data
        if not data:
            return f"temp_ep_{episode_id}.mp4"

        media = data.get("medias") or {}
        if isinstance(media, list) and media:
            media = media[0]

        season = data.get("seasons") or {}
        if isinstance(season, list) and season:
            season = season[0]

        media_id = media.get("id") or data.get("media_id")
        media_type = media.get("media_type", "movie")
        raw_title = media.get("slug") or media.get("title") or "media"
        clean_title = re.sub(r"[^\w\.-]", "_", raw_title)

        ep_num = data.get("episode_number")
        season_num = season.get("season_number")

        if media_type in ["series", "tv"]:
            ep_val = ep_num if ep_num is not None else 1
            if season_num is not None:
                return f"media_{media_id}-season_{season_num}-ep_{ep_val}-{clean_title}.mp4"
            return f"media_{media_id}-ep_{ep_val}-{clean_title}.mp4"
        else:
            return f"media_{media_id}-{clean_title}.mp4"

    except Exception as e:
        log(f"   ⚠️ فشل جلب تفاصيل الحلقة {episode_id} للتسمية: {e}")
        return f"temp_ep_{episode_id}.mp4"
    
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


def _check_url_alive(url: str) -> bool:
    """فحص سريع لسلامة رابط عبر HEAD ثم GET جزئي."""
    resp = requests.head(url, timeout=7.0, verify=False, allow_redirects=True)
    status = resp.status_code

    if status == 200:
        resp = requests.get(
            url,
            headers=ARCHIVE_HEADERS,
            timeout=7.0,
            verify=False,
            allow_redirects=True,
        )
        status = resp.status_code

    content = resp.text.lower() if status == 200 else ""
    is_dead = status in [403, 404] or (
        status == 200 and ("item not available" in content or "disabled" in content)
    )
    return not is_dead


def is_archive_url_valid(url: str) -> bool:
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
        if not _check_url_alive(url):
            log("   ⚠️ [Archive] اشتباه بالموت، إعادة التأكيد بعد 3 ثوانٍ...")
            time.sleep(3)
            if not _check_url_alive(url):
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
        ctx = await browser.new_context(user_agent=random.choice(_USER_AGENTS))
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
            is_dead = (
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


# ===========================================================================
# Section 6: MixDrop Upload — رفع الملفات إلى MixDrop
# ===========================================================================


def _download_to_temp(url: str, temp_file: str) -> bool:
    """تحميل ملف من رابط مباشر إلى ملف مؤقت محلي."""
    log("   📥 [Streamtape] جاري سحب الملف مؤقتاً...")
    try:
        with requests.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0))
            done = 0
            chunk_mb = 1024 * 1024

            with open(temp_file, "wb") as f:
                for chunk in r.iter_content(chunk_size=chunk_mb):
                    if chunk:
                        f.write(chunk)
                        done += len(chunk)
                        if total > 0 and done % (chunk_mb * 100) < len(chunk):
                            log(f"   ⏳ {done // chunk_mb}MB / {total // chunk_mb}MB")
        return True
    except Exception as e:
        log(f"   ❌ [Streamtape] فشل التحميل المحلي: {e}")
        return False


def _upload_file_to_mixdrop(temp_file: str) -> Optional[str]:
    """
    رفع ملف محلي مباشرةً إلى MixDrop.
    يُعيد embed_url عند النجاح أو None عند الفشل.
    """
    log("   🚀 [MixDrop] بدء الرفع المباشر...")
    try:
        with open(temp_file, "rb") as f:
            response = requests.post(
                MIXDROP_UPLOAD_URL,
                data={"email": MIXDROP_EMAIL, "key": MIXDROP_KEY},
                files={"file": f},
                timeout=1200,
            )
            res = response.json()

            if res.get("success"):
                embed_url = res["result"]["embedurl"]
                if not embed_url.startswith("https:"):
                    embed_url = f"https:{embed_url}"
                log(f"   ✅ [MixDrop] تم الرفع! {embed_url}")
                return embed_url

            error = res.get("error") or res.get("msg") or "Unknown Error"
            log(f"   ❌ [MixDrop] رفض الملف: {error}")

    except Exception as e:
        log(f"   ❌ [MixDrop] خطأ أثناء الرفع المباشر: {e}")

    return None


def upload_streamtape_to_mixdrop(resolved_url: str, filename: str) -> Optional[str]:
    
    temp_file = filename
    try:
        if not _download_to_temp(resolved_url, temp_file):
            return None
        return _upload_file_to_mixdrop(temp_file)
    finally:
        if os.path.exists(temp_file):
            os.remove(temp_file)
            log("   🗑️ [Streamtape] تم حذف الملف المؤقت.")


def upload_remote_url_to_mixdrop(
    source_url: str,
) -> tuple[Optional[str], Optional[str]]:
    """
    إرسال رابط مباشر لـ MixDrop عبر Remote Upload.
    يُعيد (remote_id, embed_url) عند النجاح، أو (None, None) عند الفشل.
    """
    api_url = (
        f"{MIXDROP_REMOTE_URL}"
        f"?email={MIXDROP_EMAIL}&key={MIXDROP_KEY}&url={quote(source_url)}"
    )
    try:
        response = requests.get(api_url, timeout=30)

        if response.status_code != 200:
            log(f"   ❌ [MixDrop Remote] HTTP {response.status_code}.")
            return None, None

        res = response.json()
        if res.get("success"):
            remote_id = res["result"]["id"]
            embed_url = res["result"]["embedurl"]
            if not embed_url.startswith("https:"):
                embed_url = f"https:{embed_url}"
            log(f"   ✅ [MixDrop Remote] تم القبول! Remote ID: {remote_id}")
            return remote_id, embed_url

        error = res.get("error") or res.get("msg") or "Unknown Error"
        log(f"   ⚠️ [MixDrop Remote] رُفض الطلب: {error}")

    except Exception as e:
        log(f"   ❌ [MixDrop Remote] خطأ تقني: {e}")

    return None, None


# ===========================================================================
# Section 7: Hunter Mode — Polling على حالة Remote Upload
# ===========================================================================


def wait_for_mixdrop_processing(remote_id: str, embed_url: str) -> Optional[str]:
    """
    التحقق السريع: الانتظار حتى تحول الحالة إلى Downloading لضمان بدء السحب،
    ثم إرجاع embed_url مباشرة دون انتظار اكتمال التحميل.
    """
    log(f"   🔍 [Hunter] فحص بدء السحب لـ Remote ID: {remote_id}...")

    # فحص سريع لمدة 25 ثانية كحد أقصى (5 محاولات × 5 ثوانٍ)
    for attempt in range(1, 6):
        time.sleep(5)
        try:
            status_url = (
                f"{MIXDROP_STATUS_URL}"
                f"?email={MIXDROP_EMAIL}&key={MIXDROP_KEY}&id={remote_id}"
            )
            res = requests.get(status_url, timeout=15).json()

            if not res.get("success"):
                continue

            status_info = res["result"]
            result_status = status_info.get("status")

            if result_status in ["Downloading", "Complete"]:
                log(
                    f"   🚀 [Hunter] السحب شغال حالياً بحالة ({result_status})! اعتماد الرابط فوراً: {embed_url}"
                )
                return embed_url

            if result_status == "Error":
                log("   ❌ [Hunter] MixDrop فشل في بدء سحب الرابط.")
                return None

            log(f"   ⏳ [Hunter] حالة السحب: {result_status} (محاولة {attempt}/5)...")

        except Exception as e:
            log(f"   ⚠️ [Hunter] خطأ في فحص الحالة: {e}")

    # في حال استمرار حالة Queue بعد 25 ثانية، نعتمد الرابط طالما لم يعطِ Error
    log(f"   ⚡ [Hunter] اعتماد الرابط مباشرة واستكمال المهمة: {embed_url}")
    return embed_url


# ===========================================================================
# Section 8: Link Repair — إصلاح رابط واحد
# ===========================================================================


def _smart_upload_to_mixdrop(direct_url: str, filename: str) -> Optional[str]:
    """
    الخطة أ: محاولة الرفع عبر Remote Upload (حتى 3 محاولات).
    الخطة ب: في حال فشل كل محاولات الخطة أ، التحميل المحلي والرفع المباشر.
    """
    max_plan_a_retries = 3

    for attempt in range(1, max_plan_a_retries + 1):
        log(
            f"   🌐 [الخطة أ - محاولة {attempt}/{max_plan_a_retries}] محاولة الرفع المباشر (Remote Upload)..."
        )
        remote_id, embed_url = upload_remote_url_to_mixdrop(direct_url)

        if remote_id and embed_url:
            final_url = wait_for_mixdrop_processing(remote_id, embed_url)
            if final_url:
                return final_url

        if attempt < max_plan_a_retries:
            time.sleep(3)

    log(
        "   ⚠️ [الخطة أ] استنفدت جميع المحاولات، الانتقال إلى [الخطة ب] (تحميل محلي ثم رفع)..."
    )
    return upload_streamtape_to_mixdrop(direct_url, filename)


def _upload_source(source_key: str, source_url: str, filename: str) -> Optional[str]:
    """
    تجهيز الرابط المباشر بحسب المصدر، ثم توجيهه لآلية الرفع الذكية.
    """
    direct_url = source_url

    if source_key == "streamtape":
        log("   🕵️ [Repair] استخراج رابط Streamtape المباشر...")
        direct_url = asyncio.run(resolve_streamtape(source_url))
        if not direct_url:
            log("   ❌ [Repair] فشل استخراج رابط Streamtape.")
            return None

    # تطبيق الخطة أ ثم الخطة ب على الرابط المباشر النهائي
    return _smart_upload_to_mixdrop(direct_url, filename)


def repair_link(link: dict) -> bool:
    """
    إصلاح رابط واحد: يجرب المصادر المتاحة بالترتيب حتى ينجح أحدها.
    يُعيد True عند نجاح الإصلاح.
    """
    link_id = link["id"]
    ep_id = link["episode_id"]

    raw_sources = fetch_valid_sources(ep_id)
    available = extract_available_sources(raw_sources)
    ordered = get_ordered_sources(available)

    if not ordered:
        log(f"   ⚠️ [Repair] لا يوجد أي مصدر متاح للحلقة {ep_id}. تخطي.")
        return False

    log(f"   🔍 [Repair] حلقة ID: {ep_id} | المصادر: {ordered}")

    for source_key in ordered:
        source_url = available[source_key]
        log(f"   ✅ [Source] المصدر الحالي: [{source_key}] → {source_url}")

        # فحص Archive قبل أي شيء
        if source_key == "archive" and not is_archive_url_valid(source_url):
            log("   ❌ [Archive] الرابط ميت! الانتقال للمصدر التالي...")
            continue

        # محاولات الرفع مع Retry
        embed_url = None
        # تجهيز اسم الملف التلقائي حسب النوع
        filename = build_filename_for_episode(ep_id)

        for attempt in range(1, RETRY_COUNT + 1):
            log(f"   📡 محاولة [{attempt}/{RETRY_COUNT}] مصدر [{source_key}]...")
            embed_url = _upload_source(source_key, source_url, filename)
            if embed_url:
                break
            if attempt < RETRY_COUNT:
                time.sleep(RETRY_DELAY)

        if embed_url:
            mark_link_fixed(link_id, embed_url)
            return True

        log(f"   ⏭️ [Repair] فشل المصدر [{source_key}] تماماً، الانتقال للتالي...")

    return False


# ===========================================================================
# Section 9: Main Orchestrator — المنسق الرئيسي
# ===========================================================================


def rescue_mixdrop_mission() -> None:
    """
    النقطة الرئيسية لمهمة الإصلاح:
    جلب الروابط التالفة → إصلاح كل رابط → تسجيل النتائج.
    """
    now = datetime.now().strftime("%H:%M:%S")
    log(f"🚀 [{now}] بدء مهمة الإصلاح لسيرفر: {TARGET_SERVER.upper()}")

    links_to_repair = fetch_broken_links()
    if not links_to_repair:
        return

    count_success = 0

    for link in links_to_repair:
        log(f"\n{'─' * 55}")
        log(f"🔧 رابط ID: {link['id']} | حلقة ID: {link['episode_id']}")

        repaired = repair_link(link)

        if repaired:
            count_success += 1
            log(f"   ✅ تم إصلاح الرابط {link['id']}!")
        else:
            log(f"   ⏭️ فشل إصلاح الرابط {link['id']}، الانتقال للتالي...")

        time.sleep(COOLDOWN_DELAY)

    now = datetime.now().strftime("%H:%M:%S")
    log(f"\n{'═' * 55}")
    log(
        f"✨ [{now}] المهمة انتهت! تم إصلاح {count_success}/{len(links_to_repair)} رابط."
    )


# ===========================================================================
# Entry Point
# ===========================================================================

if __name__ == "__main__":
    rescue_mixdrop_mission()
