"""
vk_rescue.py
============
مهمة إنقاذ ذكية ترفع محتوى الحلقات إلى VK
من مصادر بديلة (Archive / Telegram).
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

SUPABASE_URL    = os.getenv("SUPABASE_URL")
SUPABASE_KEY    = os.getenv("SUPABASE_KEY")
VK_ACCESS_TOKEN = os.getenv("VK_ACCESS_TOKEN")
VK_GROUP_ID     = os.getenv("VK_GROUP_ID")

TARGET_SERVER  = "vk"
SOURCE_SERVERS = ["archive", "telegram_direct"]

VK_API_BASE    = "https://api.vk.com/method"
VK_API_VERSION = "5.131"

HUNTER_MAX_ATTEMPTS = 50
HUNTER_WAIT         = 30    # ثانية بين كل فحص حالة

RETRY_COUNT    = 3
RETRY_DELAY    = 10    # ثانية بين محاولات الرفع
COOLDOWN_DELAY = 120   # ثانية بين كل حلقة وأخرى

ARCHIVE_CHECK_TIMEOUT = 15.0

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("VKRescue")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


# ===========================================================================
# Section 2: Supabase Fetchers — جلب وحفظ البيانات
# ===========================================================================

def fetch_all_episodes() -> list[dict]:
    """جلب جميع الحلقات من Supabase مع pagination لتجاوز حد الـ 1000 سجل."""
    all_episodes = []
    start, step = 0, 1000

    while True:
        res = (
            supabase.table("episodes")
            .select("id, episode_number, medias(title), links(server_name, url)")
            .range(start, start + step - 1)
            .execute()
        )
        if not res.data:
            break
        all_episodes.extend(res.data)
        if len(res.data) < step:
            break
        start += step

    log.info(f"📦 تم جلب {len(all_episodes)} حلقة من قاعدة البيانات.")
    return all_episodes


def save_vk_link(episode_id: int, embed_url: str) -> None:
    """حفظ رابط VK الناجح في جدول links."""
    supabase.table("links").upsert(
        {
            "episode_id":  episode_id,
            "server_name": TARGET_SERVER,
            "url":         embed_url,
        },
        on_conflict="episode_id, server_name",
    ).execute()
    log.info(f"✨ تم الحفظ في Supabase! (episode_id={episode_id})")


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
    """هل الحلقة تحتاج إنقاذ؟ (VK غير موجود)."""
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
    فحص سلامة رابط Archive.org عبر GET مباشر.
    للروابط غير Archive يُعيد True مباشرةً.
    """
    if "archive.org" not in url:
        return True

    log.info("🔎 [Archive] فحص سلامة الرابط...")
    try:
        resp = requests.get(url, timeout=ARCHIVE_CHECK_TIMEOUT, verify=False)

        if resp.status_code == 404:
            log.warning("❌ [Archive] الرابط محذوف (404).")
            return False

        if resp.status_code == 200 and "Item not available" in resp.text:
            log.warning("❌ [Archive] العنصر غير متاح.")
            return False

        return True

    except Exception as e:
        log.warning(f"⚠️ [Archive] خطأ أثناء فحص الرابط: {e}")
        return False


# ===========================================================================
# Section 5: VK Upload — الرفع إلى VK على مرحلتين
# ===========================================================================

def _reserve_vk_slot(episode_id: int) -> Optional[dict]:
    """
    المرحلة الأولى: حجز مكان للفيديو في VK عبر video.save.
    يُعيد dict يحتوي على video_id, owner_id, upload_url أو None عند الفشل.
    """
    try:
        res = requests.get(
            f"{VK_API_BASE}/video.save",
            params={
                "name":         f"Episode {episode_id}",
                "group_id":     VK_GROUP_ID,
                "access_token": VK_ACCESS_TOKEN,
                "v":            VK_API_VERSION,
            },
        ).json()

        if "response" not in res:
            error = res.get("error", {}).get("error_msg", "Unknown Error")
            log.error(f"❌ VK رفض الحجز: {error}")
            return None

        response = res["response"]
        log.info(f"✅ تم الحجز في VK. video_id={response['video_id']}")
        return {
            "video_id":   response["video_id"],
            "owner_id":   response["owner_id"],
            "upload_url": response["upload_url"],
        }

    except Exception as e:
        log.error(f"❌ خطأ في حجز VK slot: {e}")
        return None


def _stream_to_vk(source_url: str, upload_url: str, episode_id: int) -> bool:
    """
    المرحلة الثانية: ضخ الفيديو مباشرةً من المصدر إلى upload_url الخاص بـ VK.
    يُعيد True عند النجاح.
    """
    log.info(f"📡 [VK] جاري ضخ الفيديو من المصدر إلى VK مباشرة...")
    try:
        with requests.get(source_url, stream=True, timeout=60) as r_source:
            if r_source.status_code != 200:
                log.error(f"❌ فشل السحب من المصدر (Status: {r_source.status_code})")
                return False

            files = {"video_file": (f"ep_{episode_id}.mp4", r_source.raw, "video/mp4")}
            requests.post(upload_url, files=files, timeout=600)
            log.info("✅ انتهى ضخ البايتات بنجاح.")
            return True

    except Exception as e:
        log.error(f"❌ خطأ أثناء الضخ: {e}")
        return False


def upload_to_vk(source_url: str, episode_id: int) -> Optional[dict]:
    """
    عملية الرفع الكاملة لـ VK: حجز → ضخ.
    يُعيد dict يحتوي على video_id و owner_id لاستخدامهما في Hunter، أو None عند الفشل.
    """
    slot = _reserve_vk_slot(episode_id)
    if not slot:
        return None

    success = _stream_to_vk(source_url, slot["upload_url"], episode_id)
    if not success:
        return None

    return {"video_id": slot["video_id"], "owner_id": slot["owner_id"]}


# ===========================================================================
# Section 6: VK URL Builder — بناء رابط Embed النهائي
# ===========================================================================

def build_vk_embed_url(player_url: str) -> str:
    """
    تحويل رابط VK player إلى رابط Embed نهائي:
    - استبدال vk.com بـ vkvideo.ru
    - إضافة باراميترات HD والـ autoplay
    """
    embed_url = player_url.replace("vk.com", "vkvideo.ru")
    connector = "&" if "?" in embed_url else "?"
    return f"{embed_url}{connector}hd=2&autoplay=0"


# ===========================================================================
# Section 7: Hunter Mode — Polling على حالة الفيديو في VK
# ===========================================================================

def wait_for_vk_processing(video_id: str, owner_id: str) -> Optional[str]:
    """
    Hunter Mode: انتظار ظهور player URL في VK (علامة اكتمال المعالجة).
    يُعيد embed_url النهائي عند النجاح أو None عند انتهاء المحاولات.
    """
    log.info(f"🔍 Hunter Mode: فحص حالة video_id={video_id}...")
    video_key = f"{owner_id}_{video_id}"

    for attempt in range(1, HUNTER_MAX_ATTEMPTS + 1):
        time.sleep(HUNTER_WAIT)
        log.info(f"⏳ Hunter فحص ({attempt}/{HUNTER_MAX_ATTEMPTS})...")

        try:
            res = requests.get(
                f"{VK_API_BASE}/video.get",
                params={
                    "videos":        video_key,
                    "access_token":  VK_ACCESS_TOKEN,
                    "v":             VK_API_VERSION,
                },
            ).json()

            if "response" not in res or not res["response"].get("items"):
                continue

            video_data = res["response"]["items"][0]
            player_url = video_data.get("player")

            if player_url:
                embed_url = build_vk_embed_url(player_url)
                now       = datetime.now().strftime("%H:%M:%S")
                log.info(f"[{now}] ✅ تم القنص بنجاح! | الرابط: {embed_url}")
                return embed_url

            log.info(f"⏳ VK يعالج الفيديو حالياً ({attempt}/{HUNTER_MAX_ATTEMPTS})...")

        except Exception:
            log.warning("⚠️ خطأ في طلب الحالة، تجاهل وإعادة المحاولة...")

    log.error("❌ Hunter Mode استنفد كل المحاولات.")
    return None


# ===========================================================================
# Section 8: Episode Rescue — إنقاذ حلقة واحدة
# ===========================================================================

def _get_episode_title(episode: dict) -> str:
    """استخراج عنوان الحلقة من بيانات الـ join."""
    return episode.get("medias", {}).get(
        "title", f"Episode {episode.get('episode_number')}"
    )


def _upload_and_verify(source_url: str, episode_id: int) -> Optional[str]:
    """
    رفع المصدر إلى VK وانتظار اكتمال المعالجة.
    يُعيد embed_url عند النجاح أو None عند الفشل.
    """
    vk_info = upload_to_vk(source_url, episode_id)
    if not vk_info:
        return None

    return wait_for_vk_processing(vk_info["video_id"], vk_info["owner_id"])


def rescue_episode(episode: dict) -> bool:
    """
    إنقاذ حلقة واحدة: يجرب كل مصدر متاح بالترتيب حتى ينجح أحدهم.
    يُعيد True لو تم الإنقاذ بنجاح.
    """
    ep_id     = episode["id"]
    links     = episode.get("links", [])
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
            embed_url = _upload_and_verify(source_url, ep_id)
            if embed_url:
                break
            if attempt < RETRY_COUNT:
                time.sleep(RETRY_DELAY)

        if embed_url:
            save_vk_link(ep_id, embed_url)
            return True

        log.warning(f"⏭️ فشل المصدر [{source_key}] تماماً، الانتقال للتالي...")

    return False


# ===========================================================================
# Section 9: Main Orchestrator — المنسق الرئيسي
# ===========================================================================

def rescue_vk_mission() -> None:
    """
    النقطة الرئيسية لمهمة الإنقاذ:
    تجلب الحلقات → تفلتر المحتاجة → تُنقذ كل واحدة.
    """
    now = datetime.now().strftime("%H:%M:%S")
    log.info(f"🚀 [{now}] بدء مهمة الإنقاذ لسيرفر: {TARGET_SERVER.upper()}")

    all_episodes = fetch_all_episodes()
    if not all_episodes:
        log.error("❌ لم يتم العثور على بيانات!")
        return

    count_success = 0

    for episode in all_episodes:
        ep_id = episode["id"]
        links = episode.get("links", [])

        if not needs_rescue(links):
            continue

        log.info(f"\n{'─' * 55}")
        log.info(f"🔍 حلقة ID: {ep_id} | {_get_episode_title(episode)}")

        rescued = rescue_episode(episode)

        if rescued:
            count_success += 1
            log.info(f"✅ تم إنقاذ الحلقة {ep_id}!")
        else:
            log.warning(f"⏭️ فشل إنقاذ الحلقة {ep_id}، الانتقال للتالية...")

        log.info(f"⏳ انتظار {COOLDOWN_DELAY} ثانية ({COOLDOWN_DELAY // 60} دقيقة) قبل الحلقة التالية...")
        time.sleep(COOLDOWN_DELAY)

    now = datetime.now().strftime("%H:%M:%S")
    log.info(f"\n{'═' * 55}")
    log.info(f"✨ [{now}] المهمة انتهت! تم إنقاذ {count_success} حلقة لـ {TARGET_SERVER.upper()}.")


# ===========================================================================
# Entry Point
# ===========================================================================

if __name__ == "__main__":
    rescue_vk_mission()