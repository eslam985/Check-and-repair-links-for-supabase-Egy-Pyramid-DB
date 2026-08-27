"""
streamtape_rescue.py
====================
مهمة إنقاذ ذكية ترفع محتوى الحلقات إلى Streamtape
من مصادر بديلة (Archive / Telegram / MixDrop / VK).
مبني على مبدأ فصل المسؤوليات.
"""

import os
import time
import logging
from datetime import datetime
from typing import Optional

import requests
from supabase import create_client, Client


# ===========================================================================
# Section 1: Configuration — الإعدادات المركزية
# ===========================================================================

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
ST_LOGIN     = os.environ.get("STREAMTAPE_LOGIN")
ST_KEY       = os.environ.get("STREAMTAPE_API_KEY")

TARGET_SERVER  = "streamtape"
SOURCE_SERVERS = ["archive", "telegram_direct", "mixdrop", "vk"]

ST_BASE_URL    = "https://api.streamtape.com"
ST_EMBED_BASE  = "https://streamtape.com/e"

HUNTER_MAX_ATTEMPTS = 100
HUNTER_WAIT         = 30   # ثانية بين كل فحص حالة

RETRY_COUNT    = 3
RETRY_DELAY    = 10   # ثانية بين محاولات الرفع
COOLDOWN_DELAY = 20   # ثانية بين كل حلقة وأخرى
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "10"))
ARCHIVE_CHECK_TIMEOUT = 15.0

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("StreamtapeRescue")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ===========================================================================
# Section 2: Supabase Fetchers — جلب وحفظ البيانات
# ===========================================================================

def fetch_episodes_batch(batch_size: int = 10) -> list[dict]:
    """حجز جلب دفعة من الحلقات التي تفتقد سيرفر Streamtape عبر RPC لتفادي Race Condition."""
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


def save_streamtape_link(episode_id: int, embed_url: str) -> None:
    """حفظ رابط Streamtape الناجح في جدول links."""
    supabase.table("links").upsert(
        {
            "episode_id":  episode_id,
            "server_name": TARGET_SERVER,
            "url":         embed_url,
        },
        on_conflict="episode_id, server_name",
    ).execute()
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
    """هل الحلقة تحتاج إنقاذ؟ (Streamtape غير موجود)."""
    return not any(
        link["server_name"].lower() == TARGET_SERVER
        for link in episode_links
    )


def get_ordered_sources(available: dict[str, str]) -> list[str]:
    """إعادة المصادر المتاحة مرتبةً حسب الأولوية المحددة في SOURCE_SERVERS."""
    return [key for key in SOURCE_SERVERS if key in available]


def is_telegram_url_locked(url: str) -> bool:
    """هل رابط Telegram محجوز ومش شغال؟"""
    return "=LOCKING" in url


# ===========================================================================
# Section 4: Archive Validator — فحص سلامة روابط Archive.org
# ===========================================================================

def is_archive_url_valid(url: str) -> bool:
    """
    فحص سلامة رابط Archive.org عبر GET جزئي.
    للروابط غير Archive يُعيد True مباشرةً.
    """
    if "archive.org" not in url:
        return True

    log.info("🔎 [Archive] فحص سلامة الرابط...")
    try:
        resp   = requests.get(url, timeout=ARCHIVE_CHECK_TIMEOUT, verify=False)
        status = resp.status_code

        if status == 404:
            log.warning("❌ [Archive] الرابط محذوف (404).")
            return False

        if status == 200 and "Item not available" in resp.text:
            log.warning("❌ [Archive] العنصر غير متاح (Item not available).")
            return False

        return True

    except Exception as e:
        log.warning(f"⚠️ [Archive] خطأ أثناء فحص الرابط: {e}")
        return False  # نرفضه في Streamtape لأن الموارد أقل تسامحاً


# ===========================================================================
# Section 5: Streamtape Upload — رفع الروابط إلى Streamtape
# ===========================================================================

def submit_remote_upload(source_url: str) -> Optional[str]:
    """
    إرسال رابط للرفع عبر Streamtape Remote Download API.
    يُعيد remote_id لاستخدامه في polling أو None عند الفشل.
    """
    api_url = f"{ST_BASE_URL}/remotedl/add"
    payload = {"login": ST_LOGIN, "key": ST_KEY, "url": source_url}

    try:
        res = requests.get(api_url, params=payload, timeout=30).json()

        if res.get("status") == 200:
            remote_id = res["result"]["id"]
            log.info(f"✅ تم قبول الرابط! Remote ID: {remote_id}")
            return remote_id

        log.warning(f"⚠️ Streamtape رفض الطلب: {res.get('msg')}")

    except Exception as e:
        log.error(f"❌ خطأ تقني في Remote Upload: {e}")

    return None


# ===========================================================================
# Section 6: File Code Extractor — استخراج كود الملف من نتيجة الـ Polling
# ===========================================================================

def extract_file_code(task_info: dict) -> Optional[str]:
    """
    استخراج file_code من نتيجة الـ status API.
    يجرب `fileid` أولاً، ثم يقنصه من الـ `url` كـ fallback.
    """
    file_code = task_info.get("fileid")
    if file_code:
        return file_code

    # Fallback: استخراجه من الرابط (https://streamtape.com/v/XXXX/name.mp4)
    raw_url = task_info.get("url", "")
    if raw_url and "/v/" in raw_url:
        try:
            return raw_url.split("/v/")[1].split("/")[0]
        except (IndexError, AttributeError):
            pass

    return None


# ===========================================================================
# Section 7: Hunter Mode — Polling على حالة Remote Download
# ===========================================================================

def _log_download_progress(task_info: dict, attempt: int) -> None:
    """طباعة تقرير تقدم التحميل الحالي."""
    bytes_loaded  = task_info.get("bytes_loaded", 0)
    size_mb       = float(bytes_loaded) / (1024 * 1024)
    current_status = task_info.get("status", "unknown")
    log.info(f"⏳ جاري السحب (Status: {current_status}) | المحمل: {size_mb:.2f} MB ({attempt}/{HUNTER_MAX_ATTEMPTS})...")


def wait_for_streamtape_processing(remote_id: str) -> Optional[str]:
    """
    Hunter Mode: انتظار اكتمال معالجة Streamtape للـ Remote Download.
    يُعيد embed_url عند النجاح أو None عند الفشل.
    """
    log.info(f"🔍 Hunter Mode: فحص حالة Remote ID: {remote_id}...")
    status_url = f"{ST_BASE_URL}/remotedl/status?login={ST_LOGIN}&key={ST_KEY}&id={remote_id}"

    for attempt in range(1, HUNTER_MAX_ATTEMPTS + 1):
        time.sleep(HUNTER_WAIT)
        log.info(f"⏳ Hunter فحص ({attempt}/{HUNTER_MAX_ATTEMPTS})...")

        try:
            res = requests.get(status_url, timeout=20).json()

            if res.get("status") != 200:
                continue

            task_info = res.get("result", {}).get(remote_id, {})
            file_code = extract_file_code(task_info)

            # لو ظهر file_code يبقى الملف جاهز
            if file_code:
                embed_url = f"{ST_EMBED_BASE}/{file_code}"
                raw_size  = task_info.get("bytes_total", 0)
                size_mb   = float(raw_size) / (1024 * 1024)
                now       = datetime.now().strftime("%H:%M:%S")
                log.info(f"[{now}] ✅ تم القنص! ({size_mb:.2f} MB) | الرابط: {embed_url}")
                return embed_url

            # فشل نهائي من السيرفر
            if task_info.get("status") == "error":
                log.error("⚠️ المهمة فشلت على السيرفر (Status: error). إلغاء الفحص.")
                return None

            # لسه بيتحمل
            _log_download_progress(task_info, attempt)

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


def _upload_source(source_url: str) -> Optional[str]:
    """
    إرسال رابط المصدر لـ Streamtape Remote Download وانتظار اكتماله.
    يُعيد embed_url عند النجاح أو None عند الفشل.
    """
    remote_id = submit_remote_upload(source_url)
    if not remote_id:
        return None
    return wait_for_streamtape_processing(remote_id)


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
            log.warning("❌ [Archive] الرابط ميت! الانتقال للتالي...")
            continue

        # فحص Telegram المحجوز
        if source_key == "telegram_direct" and is_telegram_url_locked(source_url):
            log.warning("🔒 [Telegram] الرابط محجوز (LOCKING)، الانتقال للتالي...")
            continue

        # محاولات الرفع
        embed_url = None
        for attempt in range(1, RETRY_COUNT + 1):
            log.info(f"📡 محاولة [{attempt}/{RETRY_COUNT}] من المصدر [{source_key}]...")
            embed_url = _upload_source(source_url)
            if embed_url:
                break
            if attempt < RETRY_COUNT:
                time.sleep(RETRY_DELAY)

        if embed_url:
            save_streamtape_link(ep_id, embed_url)
            return True

        log.warning(f"⏭️ فشل المصدر [{source_key}] تماماً، الانتقال للتالي...")

    return False


# ===========================================================================
# Section 9: Main Orchestrator — المنسق الرئيسي
# ===========================================================================

def rescue_streamtape_mission() -> None:
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
            log.info(f"🔍 حلقة ID: {ep_id} | {_get_episode_title(episode)}")

            rescued = rescue_episode(episode)

            if rescued:
                total_rescued += 1
                log.info(f"✅ تم إنقاذ الحلقة {ep_id}!")
            else:
                log.warning(f"⏭️ فشل إنقاذ الحلقة {ep_id}، الانتقال للتالية...")

            log.info(f"⏳ انتظار {COOLDOWN_DELAY} ثانية لتهدئة الضغط...")
            time.sleep(COOLDOWN_DELAY)

    now = datetime.now().strftime("%H:%M:%S")
    log.info(f"\n{'═' * 55}")
    log.info(f"✨ [{now}] المهمة انتهت! تم إنقاذ إجمالي {total_rescued} حلقة لـ {TARGET_SERVER.upper()}.")


# ===========================================================================
# Entry Point
# ===========================================================================

if __name__ == "__main__":
    rescue_streamtape_mission()