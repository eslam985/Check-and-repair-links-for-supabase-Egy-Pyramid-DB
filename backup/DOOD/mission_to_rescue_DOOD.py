"""
doodstream_rescue.py
====================
مهمة إنقاذ ذكية ترفع محتوى الحلقات إلى DoodStream
من مصادر بديلة (Archive / Telegram / Streamtape / LuluStream).
مبني على مبدأ فصل المسؤوليات.
"""

import os
import time
import logging
from datetime import datetime
from typing import Optional
from urllib.parse import quote

import requests
from supabase import create_client, Client


# ===========================================================================
# Section 1: Configuration — الإعدادات المركزية
# ===========================================================================

SUPABASE_URL  = os.environ.get("SUPABASE_URL")
SUPABASE_KEY  = os.environ.get("SUPABASE_KEY")
DOOD_API_KEY  = os.environ.get("DOOD_API_KEY")

TARGET_SERVER  = "doodstream"
SOURCE_SERVERS = ["archive", "telegram_direct", "streamtape", "lulustream"]

DOOD_BASE_URL  = "https://doodapi.com/api"
DOOD_EMBED_BASE = "https://myvidplay.com/e"

HUNTER_MAX_ATTEMPTS = 30
HUNTER_WAIT         = 30    # ثانية بين كل فحص حالة

RETRY_COUNT     = 3
RETRY_DELAY     = 10    # ثانية بين محاولات الرفع
COOLDOWN_DELAY  = 360   # ثانية بين كل حلقة وأخرى (6 دقائق — Dood يحتاج وقت أطول)
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "10"))
ARCHIVE_HEADERS = {"Range": "bytes=0-50000"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("DoodRescue")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ===========================================================================
# Section 2: Supabase Fetchers — جلب وحفظ البيانات
# ===========================================================================

def fetch_episodes_batch(batch_size: int = 10) -> list[dict]:
    """حجز وجلب دفعة من الحلقات التي تفتقد سيرفر DoodStream عبر RPC لتفادي Race Condition."""
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


def save_dood_link(episode_id: int, file_code: str) -> None:
    """حفظ رابط DoodStream الناجح في جدول links."""
    embed_url = f"{DOOD_EMBED_BASE}/{file_code}"
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
    """هل الحلقة تحتاج إنقاذ؟ (DoodStream غير موجود)."""
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

def _check_url_alive(url: str) -> bool:
    """فحص سريع لسلامة رابط عبر HEAD ثم GET جزئي."""
    resp   = requests.head(url, timeout=7.0)
    status = resp.status_code

    if status == 200:
        resp   = requests.get(url, headers=ARCHIVE_HEADERS, timeout=7.0)
        status = resp.status_code

    content = resp.text.lower() if status == 200 else ""
    is_dead = status in [403, 404] or (
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
# Section 5: DoodStream Upload — رفع الروابط إلى DoodStream
# ===========================================================================

def submit_remote_upload(source_url: str) -> Optional[str]:
    """
    إرسال رابط للرفع عبر DoodStream Remote Upload API.
    يُعيد file_code مباشرةً أو None عند الفشل.
    """
    api_url = f"{DOOD_BASE_URL}/upload/url?key={DOOD_API_KEY}&url={quote(source_url)}"

    try:
        res = requests.get(api_url, timeout=30).json()

        if res.get("success") or res.get("msg") == "OK":
            file_code = res["result"]["filecode"]
            log.info(f"✅ تم قبول الرابط! File Code: {file_code}")
            return file_code

        log.warning(f"⚠️ DoodStream رفض الطلب: {res.get('msg')}")

    except Exception as e:
        log.error(f"❌ خطأ تقني في Remote Upload: {e}")

    return None


# ===========================================================================
# Section 6: Hunter Mode — Polling على حالة الملف
# ===========================================================================

def _parse_file_size(result_data: list) -> str:
    """استخراج وتحويل حجم الملف لنص مقروء."""
    try:
        raw_size = result_data[0].get("size", 0) if result_data else 0
        size_mb  = float(raw_size) / (1024 * 1024)
        return f"{size_mb:.2f} MB"
    except Exception:
        return "Unknown"


def wait_for_dood_processing(file_code: str) -> bool:
    """
    Hunter Mode: انتظار اكتمال معالجة DoodStream للملف.
    يُعيد True فور ظهور بيانات الملف (status 200) دون انتظار canplay.
    """
    log.info(f"🔍 Hunter Mode: فحص حالة File Code: {file_code}...")
    status_url = f"{DOOD_BASE_URL}/file/info?key={DOOD_API_KEY}&file_code={file_code}"

    for attempt in range(1, HUNTER_MAX_ATTEMPTS + 1):
        time.sleep(HUNTER_WAIT)
        log.info(f"⏳ Hunter فحص ({attempt}/{HUNTER_MAX_ATTEMPTS})...")

        try:
            res = requests.get(status_url, timeout=20).json()

            if res.get("status") == 200:
                result_data = res.get("result", [{}])
                size_str    = _parse_file_size(result_data)
                now         = datetime.now().strftime("%H:%M:%S")
                log.info(f"[{now}] ✅ الملف وصل وجاهز! ({size_str})")
                return True

            # الملف لسه في مرحلة المعالجة
            result_data = res.get("result", [{}])
            size_str    = _parse_file_size(result_data)
            log.info(f"⏳ الملف في مرحلة المعالجة (الحجم: {size_str}) ({attempt}/{HUNTER_MAX_ATTEMPTS})...")

        except Exception:
            log.warning("⚠️ خطأ في طلب الحالة، تجاهل وإعادة المحاولة...")

    log.error("❌ Hunter Mode استنفد كل المحاولات.")
    return False


# ===========================================================================
# Section 7: Episode Rescue — إنقاذ حلقة واحدة
# ===========================================================================

def _get_episode_title(episode: dict) -> str:
    """استخراج عنوان الحلقة من بيانات الدفعة."""
    title = episode.get("media_title")
    ep_num = episode.get("episode_number")
    return f"{title} (Ep {ep_num})" if title else f"Episode {ep_num}"


def _upload_and_verify(source_url: str, episode_id: int) -> bool:
    """
    رفع رابط المصدر إلى DoodStream وانتظار تأكيد المعالجة ثم الحفظ.
    يُعيد True عند النجاح الكامل.
    """
    file_code = submit_remote_upload(source_url)
    if not file_code:
        return False

    verified = wait_for_dood_processing(file_code)
    if verified:
        save_dood_link(episode_id, file_code)
    return verified


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
        for attempt in range(1, RETRY_COUNT + 1):
            log.info(f"📡 محاولة [{attempt}/{RETRY_COUNT}] من المصدر [{source_key}]...")
            success = _upload_and_verify(source_url, ep_id)
            if success:
                return True
            if attempt < RETRY_COUNT:
                time.sleep(RETRY_DELAY)

        log.warning(f"⏭️ فشل المصدر [{source_key}] تماماً، الانتقال للتالي...")

    return False


# ===========================================================================
# Section 8: Main Orchestrator — المنسق الرئيسي
# ===========================================================================

def rescue_doodstream_mission() -> None:
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

            log.info(f"⏳ انتظار {COOLDOWN_DELAY} ثانية ({COOLDOWN_DELAY // 60} دقيقة) قبل الحلقة التالية...")
            time.sleep(COOLDOWN_DELAY)

    now = datetime.now().strftime("%H:%M:%S")
    log.info(f"\n{'═' * 55}")
    log.info(f"✨ [{now}] المهمة انتهت! تم إنقاذ إجمالي {total_rescued} حلقة لـ {TARGET_SERVER.upper()}.")


# ===========================================================================
# Entry Point
# ===========================================================================

if __name__ == "__main__":
    rescue_doodstream_mission()