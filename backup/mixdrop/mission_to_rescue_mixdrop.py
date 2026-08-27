"""
mixdrop_rescue.py
=================
مهمة إنقاذ ذكية ترفع محتوى الحلقات إلى MixDrop
من مصادر بديلة (Archive / Streamtape).
مبني على مبدأ فصل المسؤوليات.
"""

import os
import time
import random
import asyncio
import logging
from datetime import datetime
from typing import Optional

import requests
from supabase import create_client, Client
from playwright.async_api import async_playwright


# ===========================================================================
# Section 1: Configuration — الإعدادات المركزية
# ===========================================================================

SUPABASE_URL   = os.environ.get("SUPABASE_URL")
SUPABASE_KEY   = os.environ.get("SUPABASE_KEY")
MIXDROP_EMAIL  = os.environ.get("MIXDROP_EMAIL")
MIXDROP_KEY    = os.environ.get("MIXDROP_API_KEY")

TARGET_SERVER  = "mixdrop"
SOURCE_SERVERS = ["archive", "streamtape"]

MIXDROP_UPLOAD_URL  = "https://ul.mixdrop.ag/api"
MIXDROP_REMOTE_URL  = "https://api.mixdrop.ag/remoteupload"
MIXDROP_STATUS_URL  = "https://api.mixdrop.ag/remotestatus"
MIXDROP_EMBED_BASE  = "https://mixdrop.ag/e"

HUNTER_MAX_ATTEMPTS = 200
HUNTER_WAIT         = 30   # ثانية بين كل فحص حالة

RETRY_COUNT    = 3
RETRY_DELAY    = 10   # ثانية بين محاولات الرفع
COOLDOWN_DELAY = 20   # ثانية بين كل حلقة وأخرى
# استبدل أو أضف المتغيرة التالية في قسم الإعدادات
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "10"))
ARCHIVE_HEADERS = {"Range": "bytes=0-50000"}

_USER_AGENTS = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("MixdropRescue")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ===========================================================================
# Section 2: Supabase Fetchers — جلب وحفظ البيانات
# ===========================================================================

def fetch_rescue_batch() -> list[dict]:
    """حجز وجلب دفعة من الحلقات التي تحتاج إنقاذ بشكل ذري باستخدام الدالة العامة."""
    try:
        res = supabase.rpc("claim_rescue_episodes", {
            "p_target_server": TARGET_SERVER,
            "p_source_servers": SOURCE_SERVERS,
            "p_batch_size": BATCH_SIZE
        }).execute()
        episodes = res.data or []
        if episodes:
            log.info(f"📦 تم حجز وجلب {len(episodes)} حلقة للإنقاذ.")
        return episodes
    except Exception as e:
        log.error(f"❌ [Supabase Error] فشل حجز الدفعة: {e}")
        return []


def save_mixdrop_link(episode_id: int, embed_url: str) -> None:
    """حفظ رابط MixDrop الناجح في جدول links."""
    supabase.table("links").upsert({
        "episode_id":  episode_id,
        "server_name": TARGET_SERVER,
        "url":         embed_url,
    }).execute()
    log.info(f"✨ تم الحفظ في Supabase! (episode_id={episode_id}, url={embed_url})")


# ===========================================================================
# Section 3: Source Resolver — اختيار وترتيب المصادر
# ===========================================================================

def extract_available_sources(episode_links: list[dict]) -> dict[str, str]:
    """استخراج الروابط المتاحة من الحلقة حسب قائمة المصادر المسموحة."""
    return {
        link["server_name"].lower(): link["url"]
        for link in episode_links
        if link["server_name"].lower() in SOURCE_SERVERS
    }


def needs_rescue(episode_links: list[dict]) -> bool:
    """هل الحلقة تحتاج إنقاذ؟ (MixDrop غير موجود)."""
    return not any(
        link["server_name"].lower() == TARGET_SERVER
        for link in episode_links
    )


def get_ordered_sources(available: dict[str, str]) -> list[str]:
    """إعادة المصادر المتاحة مرتبةً حسب الأولوية."""
    return [key for key in SOURCE_SERVERS if key in available]


# ===========================================================================
# Section 4: Archive Validator — فحص سلامة روابط Archive.org
# ===========================================================================

def _check_url_alive(url: str) -> bool:
    """فحص سريع لسلامة رابط عبر HEAD ثم GET جزئي."""
    resp   = requests.head(url, timeout=7.0)
    status = resp.status_code

    if status == 200:
        resp   = requests.get(url, headers=ARCHIVE_HEADERS, timeout=7.0)
        status = resp.status_code

    content  = resp.text.lower() if status == 200 else ""
    is_dead  = status in [403, 404] or (
        status == 200
        and ("item not available" in content or "disabled" in content)
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
        log.warning("❌ [Archive] رابط تالف أو ملغى نصياً.")
        return False

    try:
        log.info("🔎 [Archive] فحص سلامة الرابط...")
        if not _check_url_alive(url):
            log.warning("⚠️ [Archive] اشتباه بالموت، إعادة التأكيد بعد 3 ثوانٍ...")
            time.sleep(3)
            if not _check_url_alive(url):
                log.error("❌ [Archive] تم تأكيد موت الرابط نهائياً.")
                return False
            log.info("🛡️ [Archive] الرابط عاد للعمل في المحاولة الثانية.")
        return True

    except Exception as e:
        log.warning(f"⚠️ [Archive] خطأ شبكة أثناء الفحص: {e}")
        return True  # نمرره في حالة خطأ شبكة عابر


# ===========================================================================
# Section 5: Streamtape Extractor — استخراج رابط Streamtape
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
    log.info("⏳ Streamtape: انتظار انتهاء العداد (6 ثوانٍ)...")
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
    log.info(f"🕵️ Streamtape Playwright: {target}")

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
                log.info(f"✅ Streamtape URL extracted from DOM: {result[:60]}...")
                return result

            log.debug("Streamtape: DOM extraction failed, falling back to click...")
            result = await _try_extract_via_click(page, ctx)
            if result:
                log.info(f"✅ Streamtape URL resolved via click: {result[:60]}...")
                return result

            page_text = await page.inner_text("body")
            is_dead   = "video no longer available" in page_text.lower() or "not found" in page_text.lower()
            log.warning(f"❌ Streamtape failed. File deleted: {is_dead}")
            return None

        except Exception as e:
            log.warning(f"❌ Streamtape extraction error: {e}")
            return None
        finally:
            await browser.close()


# ===========================================================================
# Section 6: MixDrop Upload — رفع الملفات إلى MixDrop
# ===========================================================================

def _download_to_temp(url: str, temp_file: str) -> bool:
    """تحميل ملف من رابط مباشر إلى ملف مؤقت محلي."""
    log.info("📥 جاري سحب الملف من Streamtape إلى السيرفر المحلي مؤقتاً...")
    try:
        with requests.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            total    = int(r.headers.get("content-length", 0))
            done     = 0
            chunk_mb = 1024 * 1024

            with open(temp_file, "wb") as f:
                for chunk in r.iter_content(chunk_size=chunk_mb):
                    if chunk:
                        f.write(chunk)
                        done += len(chunk)
                        if total > 0 and done % (chunk_mb * 100) < len(chunk):
                            log.info(f"   ⏳ {done // chunk_mb}MB / {total // chunk_mb}MB")
        return True
    except Exception as e:
        log.error(f"❌ فشل التحميل المحلي: {e}")
        return False


def upload_file_to_mixdrop(temp_file: str) -> Optional[str]:
    """
    رفع ملف محلي مباشرةً إلى MixDrop.
    يُعيد embed_url عند النجاح أو None عند الفشل.
    """
    log.info("🚀 بدء الرفع المباشر إلى MixDrop...")
    try:
        with open(temp_file, "rb") as f:
            response = requests.post(
                MIXDROP_UPLOAD_URL,
                data={"email": MIXDROP_EMAIL, "key": MIXDROP_KEY},
                files={"file": f},
                timeout=1200,
            )
            log.debug(f"📡 رد MixDrop الخام: {response.text}")
            res = response.json()

            if res.get("success"):
                embed_url = res["result"]["embedurl"]
                if not embed_url.startswith("https:"):
                    embed_url = f"https:{embed_url}"
                log.info(f"✅ تم الرفع! الرابط: {embed_url}")
                return embed_url

            error = res.get("error") or res.get("msg") or "Unknown Error"
            log.error(f"❌ MixDrop رفض الملف: {error}")

    except Exception as e:
        log.error(f"❌ خطأ أثناء الرفع المباشر: {e}")

    return None


def upload_streamtape_to_mixdrop(resolved_url: str, episode_id: int) -> Optional[str]:
    """
    رفع محتوى Streamtape إلى MixDrop عبر تحميل محلي مؤقت.
    يُعيد embed_url عند النجاح أو None عند الفشل.
    """
    temp_file = f"temp_ep_{episode_id}.mp4"
    try:
        if not _download_to_temp(resolved_url, temp_file):
            return None
        return upload_file_to_mixdrop(temp_file)
    finally:
        if os.path.exists(temp_file):
            os.remove(temp_file)
            log.info("🗑️ تم حذف الملف المؤقت.")


def upload_remote_url_to_mixdrop(source_url: str) -> Optional[str]:
    """
    رفع رابط مباشر (Archive) إلى MixDrop عبر Remote Upload.
    يُعيد remote_id لاستخدامه في polling أو None عند الفشل.
    """
    payload = {
        "email": MIXDROP_EMAIL,
        "key":   MIXDROP_KEY,
        "url":   source_url,
    }
    try:
        response = requests.get(MIXDROP_REMOTE_URL, params=payload, timeout=30)

        if response.status_code != 200:
            log.error(f"❌ HTTP {response.status_code} من MixDrop Remote Upload.")
            return None

        res = response.json()
        if res.get("success"):
            remote_id = res["result"]["id"]
            log.info(f"✅ تم قبول الرابط! Remote ID: {remote_id}")
            return remote_id

        error = res.get("error") or res.get("msg") or "Unknown Error"
        log.warning(f"⚠️ MixDrop رفض الطلب: {error}")
        if not MIXDROP_EMAIL or not MIXDROP_KEY:
            log.critical("🚨 MIXDROP_EMAIL أو MIXDROP_KEY فارغة!")

    except Exception as e:
        log.error(f"❌ خطأ تقني في Remote Upload: {e}")

    return None


# ===========================================================================
# Section 7: Hunter Mode — Polling على حالة Remote Upload
# ===========================================================================

def wait_for_mixdrop_processing(remote_id: str) -> Optional[str]:
    """
    Hunter Mode: انتظار اكتمال معالجة MixDrop للـ Remote Upload.
    يُعيد embed_url عند النجاح أو None عند الفشل.
    """
    log.info(f"🔍 Hunter Mode: فحص حالة Remote ID: {remote_id}...")

    for attempt in range(1, HUNTER_MAX_ATTEMPTS + 1):
        time.sleep(HUNTER_WAIT)
        log.info(f"⏳ Hunter فحص ({attempt}/{HUNTER_MAX_ATTEMPTS})...")

        try:
            status_url = f"{MIXDROP_STATUS_URL}?email={MIXDROP_EMAIL}&key={MIXDROP_KEY}&id={remote_id}"
            res        = requests.get(status_url, timeout=20).json()

            if not res.get("success"):
                continue

            status_info   = res["result"]
            result_status = status_info.get("status")  # Complete / Downloading / Error

            if result_status == "Complete":
                file_code = status_info.get("fileref")
                embed_url = f"{MIXDROP_EMBED_BASE}/{file_code}"
                now       = datetime.now().strftime("%H:%M:%S")
                log.info(f"[{now}] 🎉 اكتمل! الرابط: {embed_url}")
                return embed_url

            if result_status == "Error":
                log.error("❌ MixDrop فشل في سحب الرابط.")
                return None

            log.info(f"⏳ الحالة: {result_status} ({attempt}/{HUNTER_MAX_ATTEMPTS})...")

        except Exception:
            log.warning("⚠️ خطأ في طلب الحالة، تجاهل وإعادة المحاولة...")

    log.error("❌ Hunter Mode استنفد كل المحاولات.")
    return None


# ===========================================================================
# Section 8: Episode Rescue — إنقاذ حلقة واحدة
# ===========================================================================

def _get_episode_title(episode: dict) -> str:
    """استخراج عنوان الحلقة من بيانات الدفعة."""
    title = episode.get("media_title")
    ep_num = episode.get("episode_number")
    return f"{title} (Ep {ep_num})" if title else f"Episode {ep_num}"


def _upload_streamtape(source_url: str, episode_id: int) -> Optional[str]:
    """
    معالجة مصدر Streamtape: استخراج الرابط المباشر → تحميل مؤقت → رفع لـ MixDrop.
    يُعيد embed_url عند النجاح.
    """
    log.info("🕵️ جاري استخراج رابط Streamtape المباشر...")
    resolved_url = asyncio.run(resolve_streamtape(source_url))
    if not resolved_url:
        log.error("❌ فشل استخراج رابط Streamtape.")
        return None
    return upload_streamtape_to_mixdrop(resolved_url, episode_id)


def _upload_archive(source_url: str) -> Optional[str]:
    """
    معالجة مصدر Archive: Remote Upload → Hunter Mode polling.
    يُعيد embed_url عند النجاح.
    """
    remote_id = upload_remote_url_to_mixdrop(source_url)
    if not remote_id:
        return None
    return wait_for_mixdrop_processing(remote_id)


def _upload_source(source_key: str, source_url: str, episode_id: int) -> Optional[str]:
    """
    توجيه عملية الرفع للدالة المناسبة بحسب نوع المصدر.
    يُعيد embed_url عند النجاح أو None عند الفشل.
    """
    if source_key == "streamtape":
        return _upload_streamtape(source_url, episode_id)
    return _upload_archive(source_url)


def rescue_episode(episode: dict) -> bool:
    """
    إنقاذ حلقة واحدة: يجرب كل مصدر متاح بالترتيب حتى ينجح أحدهم.
    يُعيد True لو تم الإنقاذ بنجاح.
    """
    ep_id     = episode["id"]
    links     = episode.get("links") or []
    available = extract_available_sources(links)
    ordered   = get_ordered_sources(available)

    if not ordered:
        return False

    for source_key in ordered:
        source_url = available[source_key]
        log.info(f"   ✅ [Source] المصدر الحالي: [{source_key}] → {source_url}")

        # فحص Archive قبل أي شيء
        if source_key == "archive" and not is_archive_url_valid(source_url):
            log.warning("❌ [Archive] الرابط ميت! جاري التجاوز للتالي...")
            continue

        # محاولات الرفع
        embed_url = None
        for attempt in range(1, RETRY_COUNT + 1):
            log.info(f"📡 محاولة [{attempt}/{RETRY_COUNT}] من المصدر [{source_key}]...")
            embed_url = _upload_source(source_key, source_url, ep_id)
            if embed_url:
                break
            if attempt < RETRY_COUNT:
                time.sleep(RETRY_DELAY)

        if embed_url:
            save_mixdrop_link(ep_id, embed_url)
            return True

        log.warning(f"⏭️ فشل المصدر [{source_key}] تماماً، الانتقال للتالي...")

    return False


# ===========================================================================
# Section 9: Main Orchestrator — المنسق الرئيسي
# ===========================================================================

def rescue_mixdrop_mission() -> None:
    """
    النقطة الرئيسية لمهمة الإنقاذ:
    تجلب الحلقات على دفعت حذرية متتالية وتنفذ العملية حتى لا تتبقى حلقات.
    """
    now = datetime.now().strftime("%H:%M:%S")
    log.info(f"🚀 [{now}] بدء مهمة الإنقاذ لسيرفر: {TARGET_SERVER.upper()}")

    count_success = 0

    while True:
        episodes = fetch_rescue_batch()
        if not episodes:
            log.info("✨ لا توجد المزيد من الحلقات المحجوزة أو التي تحتاج إنقاذ حالياً.")
            break

        for episode in episodes:
            ep_id = episode["id"]
            log.info(f"\n{'─' * 55}")
            log.info(f"🔍 حلقة ID: {ep_id} | {_get_episode_title(episode)}")

            rescued = rescue_episode(episode)

            if rescued:
                count_success += 1
                log.info(f"✅ تم إنقاذ الحلقة {ep_id}!")
            else:
                log.warning(f"⏭️ فشل إنقاذ الحلقة {ep_id}، الانتقال للتالية...")

            log.info(f"⏳ انتظار {COOLDOWN_DELAY} ثانية لتهدئة الضغط...")
            time.sleep(COOLDOWN_DELAY)

    now = datetime.now().strftime("%H:%M:%S")
    log.info(f"\n{'═' * 55}")
    log.info(f"✨ [{now}] المهمة انتهت! تم إنقاذ {count_success} حلقة لـ MixDrop.")


# ===========================================================================
# Entry Point
# ===========================================================================

if __name__ == "__main__":
    rescue_mixdrop_mission()