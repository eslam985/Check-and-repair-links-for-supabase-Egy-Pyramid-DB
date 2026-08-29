"""
lulu_rescue.py
==============
مهمة إنقاذ ذكية ترفع محتوى الحلقات إلى LuluStream
من مصادر بديلة (Archive / Telegram / Streamtape).
مبني على مبدأ فصل المسؤوليات.
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
from supabase import create_client, Client
from playwright.async_api import async_playwright

# ===========================================================================
# Section 1: Configuration — الإعدادات المركزية
# ===========================================================================

SUPABASE_URL  = os.environ.get("SUPABASE_URL")
SUPABASE_KEY  = os.environ.get("SUPABASE_KEY")
LULU_API_KEY  = os.environ.get("LULUSTREAM_API_KEY")

TARGET_SERVER  = "lulustream"
SOURCE_SERVERS = ["archive", "telegram_direct", "streamtape"]

LULU_BASE_URL  = "https://www.lulustream.com/api"
LULU_EMBED_URL = "https://luluvdo.com/e"

HUNTER_MAX_ATTEMPTS = 100
HUNTER_INITIAL_WAIT = 10   # ثواني للمحاولات الأولى
HUNTER_NORMAL_WAIT  = 45   # ثواني للمحاولات التالية
HUNTER_INITIAL_LIMIT = 5   # عدد المحاولات بالانتظار القصير

RETRY_COUNT     = 3
RETRY_DELAY     = 60        # ثانية بين محاولات الرفع
COOLDOWN_DELAY  = 20        # ثانية بين كل حلقة وأخرى
ARCHIVE_HEADERS = {"Range": "bytes=0-50000"}
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "10"))
_USER_AGENTS = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("LuluRescue")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ===========================================================================
# Section 2: Supabase Fetchers — جلب البيانات من قاعدة البيانات
# ===========================================================================

def fetch_episodes_batch(batch_size: int = 10) -> list[dict]:
    """حجز وجلب دفعة من الحلقات التي تفتقد سيرفر LuluStream عبر RPC لتفادي Race Condition."""
    try:
        response = supabase.rpc(
            "claim_episodes_for_repair",
            {
                "p_server_name": TARGET_SERVER,
                "p_batch_size":  batch_size,
            }
        ).execute()
        return response.data or []
    except Exception as e:
        log.error(f"❌ خطأ أثناء حجز الدفعة من Supabase: {e}")
        return []

def save_lulu_link(episode_id: int, file_code: str) -> None:
    """حفظ رابط LuluStream الناجح في جدول links."""
    supabase.table("links").upsert({
        "episode_id":  episode_id,
        "server_name": TARGET_SERVER,
        "url":         f"{LULU_EMBED_URL}/{file_code}",
    }).execute()
    log.info(f"✨ تم الحفظ في Supabase بنجاح! (episode_id={episode_id})")


# ===========================================================================
# Section 3: Source Resolver — اختيار وترتيب المصادر
# ===========================================================================

def extract_available_sources(episode_links: list[dict]) -> dict[str, str]:
    """استخراج الروابط المتاحة من الحلقة مرتبةً حسب الأولوية."""
    return {
        link["server_name"].lower(): link["url"]
        for link in episode_links
        if link["server_name"].lower() in SOURCE_SERVERS
    }


def needs_rescue(episode_links: list[dict]) -> bool:
    """هل الحلقة تحتاج إنقاذ؟ (LuluStream غير موجود)."""
    return not any(
        link["server_name"].lower() == TARGET_SERVER
        for link in episode_links
    )


def pick_primary_source(available: dict[str, str]) -> tuple[Optional[str], Optional[str]]:
    """
    اختيار المصدر الأولي حسب الأولوية: archive → telegram_direct → streamtape.
    يُعيد: (source_key, source_url).
    """
    for key in SOURCE_SERVERS:
        if key in available:
            return key, available[key]
    return None, None


def pick_fallback_source(
    available: dict[str, str], exclude: str
) -> tuple[Optional[str], Optional[str]]:
    """اختيار أول مصدر بديل متاح مع استثناء مصدر محدد."""
    for key in SOURCE_SERVERS:
        if key in available and key != exclude:
            return key, available[key]
    return None, None

def is_telegram_url_locked(url: str) -> bool:
    """هل رابط Telegram محجوز ومش شغال؟"""
    return "=LOCKING" in url

# ===========================================================================
# Section 4: Archive Validator — فحص سلامة روابط Archive.org
# ===========================================================================

def _check_url_alive(url: str) -> bool:
    """فحص سريع لسلامة رابط عبر HEAD ثم GET جزئي."""
    resp = requests.head(url, timeout=7.0)
    status = resp.status_code

    if status == 200:
        resp = requests.get(url, headers=ARCHIVE_HEADERS, timeout=7.0)
        status = resp.status_code

    page_content = resp.text.lower() if status == 200 else ""

    is_dead = status in [403, 404] or (
        status == 200
        and ("item not available" in page_content or "disabled" in page_content)
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

    # ضغطة أولى لتشغيل العداد مع إغلاق أي popup ينبثق
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

            # المحاولة الأولى: الـ DOM المباشر
            result = await _try_extract_from_dom(page)
            if result:
                log.info(f"✅ Streamtape URL extracted from DOM: {result[:60]}...")
                return result

            # المحاولة الثانية: الضغط الفعلي
            log.debug("Streamtape: DOM extraction failed, falling back to click...")
            result = await _try_extract_via_click(page, ctx)
            if result:
                log.info(f"✅ Streamtape URL resolved via click: {result[:60]}...")
                return result

            # تشخيص سبب الفشل
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
# Section 6: Lulu Upload — رفع الملفات إلى LuluStream
# ===========================================================================

def _get_lulu_upload_server() -> Optional[str]:
    """جلب عنوان سيرفر الرفع النشط من Lulu API."""
    res = requests.get(f"{LULU_BASE_URL}/upload/server?key={LULU_API_KEY}").json()
    if res.get("status") == 200:
        return res["result"]
    log.error(f"❌ فشل جلب سيرفر الرفع من لولو: {res}")
    return None


def _download_to_temp(url: str, temp_file: str) -> bool:
    """تحميل ملف من رابط مباشر إلى ملف مؤقت محلي."""
    log.info("📥 جاري سحب الملف من Streamtape إلى السيرفر المحلي مؤقتاً...")
    try:
        with requests.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            total   = int(r.headers.get("content-length", 0))
            done    = 0
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


def _upload_file_to_lulu(upload_server: str, temp_file: str) -> Optional[str]:
    """رفع ملف محلي مؤقت إلى Lulu وإعادة file_code."""
    log.info(f"🚀 بدء الرفع إلى لولو: {upload_server}")
    try:
        with open(temp_file, "rb") as f:
            files    = {"file": ("video.mp4", f, "video/mp4")}
            response = requests.post(
                upload_server,
                data={"key": LULU_API_KEY},
                files=files,
                timeout=1200,
            )
            log.debug(f"📡 رد لولو الخام: {response.text}")
            up_res = response.json()

            if up_res.get("status") == 200 and up_res.get("files"):
                file_code = up_res["files"][0]["filecode"]
                log.info(f"✅ تم الرفع! الكود: {file_code}")
                return file_code

            error_msg = up_res.get("msg") or "رد غير متوقع"
            log.error(f"❌ لولو رفض الملف: {error_msg}")
    except Exception as e:
        log.error(f"❌ خطأ أثناء الرفع: {e}")
    return None


def upload_streamtape_to_lulu(resolved_url: str, episode_id: int) -> Optional[str]:
    """
    رفع محتوى Streamtape إلى Lulu عبر تحميل محلي مؤقت.
    يُعيد file_code عند النجاح أو None عند الفشل.
    """
    temp_file = f"temp_{episode_id}.mp4"
    try:
        if not _download_to_temp(resolved_url, temp_file):
            return None

        upload_server = _get_lulu_upload_server()
        if not upload_server:
            return None

        return _upload_file_to_lulu(upload_server, temp_file)

    finally:
        if os.path.exists(temp_file):
            os.remove(temp_file)
            log.info("🗑️ تم حذف الملف المؤقت.")


def _wake_up_streamer(url: str) -> None:
    """إيقاظ الـ streamer بطلبات جزئية متكررة قبل إرسال الرابط للولو."""
    headers = {"Range": "bytes=0-100"}
    for attempt in range(1, RETRY_COUNT + 1):
        log.info(f"📡 محاولة تنبيه الستريمر [{attempt}/{RETRY_COUNT}]...")
        try:
            res = requests.get(url, headers=headers, timeout=15)
            if res.status_code in [200, 206]:
                log.info(f"✅ الستريمر رد بـ {res.status_code}. الرابط شغال!")
                return
        except Exception as e:
            log.warning(f"⚠️ خطأ أثناء التنبيه: {e}")
        time.sleep(3)


def _build_lulu_remote_url(source_url: str) -> str:
    """بناء رابط Lulu Remote Upload API مع الهيدرز اللازمة."""
    headers_payload = (
        "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36\r\n"
        "Referer: https://lulustream.com/\r\n"
        "Origin: https://lulustream.com"
    )
    safe_url     = quote(source_url, safe="")
    safe_headers = quote(headers_payload)
    return f"{LULU_BASE_URL}/upload/url?key={LULU_API_KEY}&url={safe_url}&headers={safe_headers}"


def upload_remote_url_to_lulu(source_url: str) -> Optional[str]:
    """
    رفع رابط مباشر (Archive / Telegram) إلى Lulu عبر Remote Upload.
    يُعيد file_code عند النجاح أو None بعد استنفاد المحاولات.
    """
    _wake_up_streamer(source_url)

    for attempt in range(1, RETRY_COUNT + 1):
        log.info(f"📡 محاولة Lulu Remote Upload [{attempt}/{RETRY_COUNT}]...")
        try:
            api_url  = _build_lulu_remote_url(source_url)
            res_data = requests.get(api_url, timeout=30).json()

            if res_data.get("status") == 200:
                file_code = res_data["result"]["filecode"]
                log.info(f"✅ تم الحجز! الكود: {file_code}")
                return file_code

        except Exception as e:
            log.error(f"❌ خطأ تقني في المحاولة {attempt}: {e}")

        if attempt < RETRY_COUNT:
            log.info(f"⏳ انتظار {RETRY_DELAY} ثانية قبل المحاولة التالية...")
            time.sleep(RETRY_DELAY)

    return None


# ===========================================================================
# Section 7: Hunter Mode — التحقق من اكتمال معالجة Lulu
# ===========================================================================

def _rename_lulu_file(file_code: str, title: str) -> None:
    """تثبيت اسم الملف في Lulu بعد نجاح المعالجة."""
    try:
        edit_url = f"{LULU_BASE_URL}/file/edit?key={LULU_API_KEY}&file_code={file_code}&file_title={quote(title)}"
        requests.get(edit_url, timeout=10)
        log.info(f"✨ تم تثبيت الاسم: {title}")
    except Exception:
        log.warning("⚠️ فشل تعديل الاسم، لكن الملف جاهز.")


def wait_for_lulu_processing(file_code: str, episode_title: str) -> bool:
    """
    Hunter Mode: انتظار اكتمال معالجة Lulu للملف.
    يُعيد True لو الملف أصبح جاهزاً للعرض.
    """
    log.info(f"🔍 Hunter Mode: فحص حالة الكود {file_code}...")
    info_url = f"{LULU_BASE_URL}/file/info?key={LULU_API_KEY}&file_code={file_code}"

    for attempt in range(1, HUNTER_MAX_ATTEMPTS + 1):
        wait = HUNTER_INITIAL_WAIT if attempt <= HUNTER_INITIAL_LIMIT else HUNTER_NORMAL_WAIT
        time.sleep(wait)
        log.info(f"⏳ Hunter فحص ({attempt}/{HUNTER_MAX_ATTEMPTS})...")

        try:
            res = requests.get(info_url, timeout=20).json()
            if res.get("status") != 200 or not res.get("result"):
                continue

            file_info   = res["result"][0]
            status_text = str(file_info.get("status", "")).lower()
            can_play    = file_info.get("canplay")

            if can_play == 1:
                log.info("✅ الملف جاهز للعرض!")
                _rename_lulu_file(file_code, episode_title)
                return True

            if can_play == 0:
                no_thumb = not file_info.get("player_img") or "nothumb" in str(file_info.get("player_img"))
                if attempt > HUNTER_INITIAL_LIMIT and no_thumb:
                    log.warning("⚠️ مشكلة داخلية (لا توجد لقطة). إلغاء المحاولة.")
                    return False
                log.info(f"⏳ لولو يعالج الملف (Status: {status_text})...")
                continue

            if status_text in ["error", "0"]:
                log.error(f"❌ لولو أكد الفشل النهائي (Status: {status_text}).")
                return False

            log.info(f"📡 الحالة: {status_text}")

        except Exception:
            log.warning("⚠️ خطأ في طلب Info، تجاهل وإعادة المحاولة...")

    return False


# ===========================================================================
# Section 8: Episode Rescue — إنقاذ حلقة واحدة
# ===========================================================================

def _get_episode_title(episode: dict) -> str:
    """استخراج عنوان الحلقة من بيانات الدفعة."""
    title  = episode.get("media_title")
    ep_num = episode.get("episode_number")
    s_num  = episode.get("season_number")

    if s_num:
        return f"{title} (S{s_num} Ep {ep_num})" if title else f"S{s_num} Ep {ep_num}"
    return f"{title} (Ep {ep_num})" if title else f"Episode {ep_num}"

def _upload_source(source_key: str, source_url: str, episode_id: int) -> Optional[str]:
    """
    رفع مصدر معين إلى Lulu بحسب نوعه.
    يُعيد file_code عند النجاح أو None عند الفشل.
    """
    if source_key == "streamtape":
        log.info("🕵️ جاري استخراج رابط Streamtape المباشر...")
        resolved_url = asyncio.run(resolve_streamtape(source_url))
        if not resolved_url:
            log.error("❌ فشل استخراج رابط Streamtape.")
            return None
        return upload_streamtape_to_lulu(resolved_url, episode_id)

    # Archive أو Telegram: Remote Upload مباشر
    return upload_remote_url_to_lulu(source_url)


def rescue_episode(episode: dict) -> bool:
    """
    إنقاذ حلقة واحدة:
    اختيار المصدر → فحص Archive → رفع → Hunter → حفظ في DB.
    يُعيد True لو تم الإنقاذ بنجاح.
    """
    ep_id   = episode["id"]
    title   = _get_episode_title(episode)
    links   = episode.get("links") or []
    available = extract_available_sources(links)

    if not available:
        return False

    # اختيار المصدر الأولي
    source_key, source_url = pick_primary_source(available)
    # لو المصدر telegram_direct والرابط محجوز، نتجاهله ونختار بديل
    if source_key == "telegram_direct" and is_telegram_url_locked(source_url):
        log.warning("🔒 [Telegram] الرابط محجوز (LOCKING)، جاري الانتقال للبديل...")
        source_key, source_url = pick_fallback_source(available, exclude="telegram_direct")
        if not source_key:
            log.error("❌ لا توجد مصادر بديلة لهذه الحلقة.")
            return False

    # فحص Archive لو كان الاختيار الأول
    if source_key == "archive" and not is_archive_url_valid(source_url):
        log.warning("❌ [Archive] الرابط ميت! جاري الانتقال للبديل...")
        source_key, source_url = pick_fallback_source(available, exclude="archive")

        if not source_key:
            log.error("❌ لا توجد مصادر بديلة لهذه الحلقة.")
            return False

    log.info(f"✅ [Source] المصدر المختار: [{source_key}] → {source_url}")

    # الرفع إلى Lulu
    file_code = _upload_source(source_key, source_url, ep_id)
    if not file_code:
        log.error(f"❌ فشل الرفع من المصدر [{source_key}].")
        return False

    # Hunter Mode: انتظار اكتمال المعالجة
    if not wait_for_lulu_processing(file_code, title):
        log.warning(f"♻️ لولو فشل في معالجة الملف من [{source_key}].")
        return False

    # حفظ الرابط الناجح في DB
    save_lulu_link(ep_id, file_code)
    return True


# ===========================================================================
# Section 9: Main Orchestrator — المنسق الرئيسي
# ===========================================================================

def rescue_lulu_mission() -> None:
    """
    النقطة الرئيسية لمهمة الإنقاذ:
    تجلب الحلقات على دفعات ذرية → تُنقذ كل حلقة حتى استهلاك جميع الحلقات المحتاجة.
    """
    now = datetime.now().strftime("%H:%M:%S")
    log.info(f"🚀 [{now}] بدء مهمة الإنقاذ لسيرفر: {TARGET_SERVER.upper()}")

    total_rescued = 0

    while True:
        episodes = fetch_episodes_batch(BATCH_SIZE)
        if not episodes:
            log.info("🎉 لا توجد حلقات إضافية تحتاج إلى إنقاذ حالياً.")
            break

        log.info(f"📦 تم حجز دفعة جديدة من {len(episodes)} حلقة.")

        for episode in episodes:
            ep_id = episode["id"]

            log.info(f"\n{'─' * 55}")
            log.info(f"🔍 فحص حلقة ID: {ep_id} | العنوان: {_get_episode_title(episode)}")

            rescued = rescue_episode(episode)

            if rescued:
                total_rescued += 1
                log.info(f"✅ تم إنقاذ الحلقة {ep_id} بنجاح!")
            else:
                log.warning(f"⏭️ فشل إنقاذ الحلقة {ep_id}، الانتقال للتالية...")

            log.info(f"⏳ انتظار {COOLDOWN_DELAY} ثانية لتهدئة الضغط على سيرفرات لولو...")
            time.sleep(COOLDOWN_DELAY)

    now = datetime.now().strftime("%H:%M:%S")
    log.info(f"\n{'═' * 55}")
    log.info(f"✨ [{now}] المهمة انتهت! تم إنقاذ إجمالي {total_rescued} حلقة لـ {TARGET_SERVER.upper()}.")


# ===========================================================================
# Entry Point
# ===========================================================================

if __name__ == "__main__":
    rescue_lulu_mission()