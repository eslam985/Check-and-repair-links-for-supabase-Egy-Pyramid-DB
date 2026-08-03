"""
voe_rescue.py
=============
مهمة إنقاذ ذكية ترفع محتوى الحلقات إلى Voe.sx
من مصادر بديلة (Archive / Telegram / Streamtape).
مبني على مبدأ فصل المسؤوليات — async كامل.
"""

import os
import time
import asyncio
import logging
from datetime import datetime
from typing import Optional

import httpx
import nest_asyncio
from tqdm.notebook import tqdm
from supabase import create_client, Client

nest_asyncio.apply()


# ===========================================================================
# Section 1: Configuration — الإعدادات المركزية
# ===========================================================================

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
VOE_API_KEY  = os.environ.get("VOE_API_KEY")

TARGET_SERVER = "voe"

# الأولوية: الأقل رقماً يُختار أولاً
SOURCE_PRIORITY = {
    "archive":         1,
    "telegram_direct": 2,
    "streamtape":      3,
}

VOE_BASE_URL   = "https://voe.sx/api"
VOE_EMBED_BASE = "https://voe.sx/e"

HUNTER_TIMEOUT      = 180   # ثانية — مهلة انتظار Hunter Mode
HUNTER_POLL_INTERVAL = 20   # ثانية بين كل فحص حالة
HUNTER_MAX_CHECKS   = 15    # بعدها نعتبر الملف جاهز لو عنده file_code

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("VoeRescue")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# قائمة الحلقات الفاشلة لمنع إعادة محاولتها في نفس الجلسة
_FAILED_TASKS: list[int] = []


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
            .select("id, episode_number, media_id, medias(title), links(server_name, url)")
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


def save_voe_link(episode_id: int, file_code: str) -> bool:
    """حفظ رابط Voe الناجح في جدول links. يُعيد True عند النجاح."""
    voe_url = f"{VOE_EMBED_BASE}/{file_code}"
    try:
        supabase.table("links").insert({
            "episode_id":        episode_id,
            "url":               voe_url,
            "server_name":       TARGET_SERVER,
            "quality":           "720p",
            "last_check_status": "pending",
        }).execute()
        log.info(f"✅ تم حفظ رابط Voe: {voe_url}")
        return True
    except Exception as e:
        log.error(f"❌ خطأ أثناء حفظ الرابط: {e}")
        return False


# ===========================================================================
# Section 3: Source Resolver — اختيار أفضل مصدر للحلقة
# ===========================================================================

def needs_rescue(episode_links: list[dict]) -> bool:
    """هل الحلقة تحتاج إنقاذ؟ (Voe غير موجود)."""
    return not any(
        "voe" in str(link.get("server_name", "")).lower()
        for link in episode_links
    )


def pick_best_source(episode_links: list[dict]) -> Optional[dict]:
    """
    اختيار أفضل مصدر متاح بناءً على الأولوية.
    يُعيد dict بـ {url, server_name} أو None لو مفيش مصدر صالح.
    """
    valid_sources = [
        {**link, "priority": SOURCE_PRIORITY[link["server_name"].lower()]}
        for link in episode_links
        if link.get("server_name", "").lower() in SOURCE_PRIORITY
    ]

    if not valid_sources:
        return None

    best = min(valid_sources, key=lambda x: x["priority"])
    return {"url": best["url"], "server_name": best["server_name"]}


def build_task(episode: dict) -> Optional[dict]:
    """
    بناء task dict من بيانات الحلقة.
    يُعيد None لو الحلقة مش محتاجة إنقاذ أو مفيش مصدر.
    """
    ep_id = episode["id"]

    if ep_id in _FAILED_TASKS:
        return None

    links = episode.get("links", [])

    if not needs_rescue(links):
        return None

    best_source = pick_best_source(links)
    if not best_source:
        return None

    return {
        "episode_id":  ep_id,
        "source_url":  best_source["url"],
        "source_name": best_source["server_name"],
        "title":       episode.get("medias", {}).get("title", "Unknown"),
        "ep_num":      episode.get("episode_number"),
    }


# ===========================================================================
# Section 4: Archive URL Builder — بناء رابط Archive المباشر
# ===========================================================================

def resolve_archive_url(source_url: str) -> str:
    """
    بناء رابط Archive.org المباشر للـ MP4 لو الرابط مش direct link.
    """
    if "archive.org" not in source_url:
        return source_url

    if source_url.endswith(".mp4"):
        return source_url

    # استخراج الـ identifier وبناء رابط التحميل المباشر
    identifier = source_url.rstrip("/").split("/")[-1]
    return f"https://archive.org/download/{identifier}/{identifier}.mp4"


# ===========================================================================
# Section 5: Voe Upload — رفع الرابط إلى Voe
# ===========================================================================

async def submit_remote_upload(client: httpx.AsyncClient, source_url: str) -> Optional[str]:
    """
    إرسال رابط للرفع عبر Voe Remote Upload API.
    يُعيد file_code أو None عند الفشل.
    """
    remote_url = resolve_archive_url(source_url)
    log.info(f"📡 إرسال أمر الرفع من: {remote_url[:60]}...")

    try:
        res = await client.get(
            f"{VOE_BASE_URL}/upload/url",
            params={"key": VOE_API_KEY, "url": remote_url},
        )
        data = res.json()

        if data.get("status") != 200:
            log.error(f"❌ فشل طلب الرفع: {data}")
            return None

        file_code = data.get("result", {}).get("file_code")
        log.info(f"✅ تم قبول الرابط! File Code: {file_code}")
        return file_code

    except Exception as e:
        log.error(f"❌ خطأ في Voe Upload API: {e}")
        return None


# ===========================================================================
# Section 6: Hunter Mode — Polling على حالة الملف
# ===========================================================================

def _update_progress_bar(pbar, status: str) -> None:
    """تحديث شريط التقدم بناءً على حالة Voe."""
    status_progress = {
        "downloading": 40,
        "processing":  80,
        "finished":    100,
    }
    pbar.n = status_progress.get(status, pbar.n)
    pbar.set_description(f"⏳ Voe Status: {status}")
    pbar.refresh()


async def wait_for_voe_processing(
    client: httpx.AsyncClient, file_code: str
) -> Optional[str]:
    """
    Hunter Mode: انتظار اكتمال معالجة Voe للملف.
    يُعيد file_code عند النجاح أو None عند انتهاء المهلة بدون نتيجة.
    """
    log.info(f"🔍 Hunter Mode: فحص حالة File Code: {file_code}...")
    status_url  = f"{VOE_BASE_URL}/file/status?key={VOE_API_KEY}&file_code={file_code}"
    start_time  = time.time()
    check_count = 0

    pbar = tqdm(total=100, desc="⏳ Voe Status: Queued")

    try:
        while time.time() - start_time < HUNTER_TIMEOUT:
            await asyncio.sleep(HUNTER_POLL_INTERVAL)
            check_count += 1

            try:
                res    = await client.get(status_url)
                status = res.json().get("result", {}).get("status")

                _update_progress_bar(pbar, status)

                if status == "finished":
                    log.info("✅ الملف جاهز!")
                    return file_code

                # صمام أمان: لو تأخر كثيراً والـ file_code موجود نعتبره جاهزاً
                if check_count >= HUNTER_MAX_CHECKS:
                    log.warning(f"⚠️ تجاوز الحد الأقصى للفحص ({HUNTER_MAX_CHECKS}). اعتبار الملف جاهزاً.")
                    return file_code

            except Exception:
                continue

    finally:
        pbar.close()

    log.error("❌ Hunter Mode انتهت المهلة بدون نتيجة.")
    return None


# ===========================================================================
# Section 7: Episode Processor — معالجة حلقة واحدة
# ===========================================================================

async def process_episode(task: dict) -> bool:
    """
    معالجة حلقة واحدة: رفع → Hunter → حفظ في DB.
    يُعيد True عند النجاح.
    """
    ep_id = task["episode_id"]
    title = f"{task['title']} - حلقة {task['ep_num']}"
    log.info(f"\n{'─' * 55}")
    log.info(f"📦 جاري معالجة: {title} | Source: {task['source_name']}")

    async with httpx.AsyncClient(timeout=60.0) as client:
        # الرفع
        file_code = await submit_remote_upload(client, task["source_url"])
        if not file_code:
            return False

        # Hunter Mode
        confirmed_code = await wait_for_voe_processing(client, file_code)
        if not confirmed_code:
            return False

    # الحفظ في DB
    return save_voe_link(ep_id, confirmed_code)


# ===========================================================================
# Section 8: Main Orchestrator — المنسق الرئيسي
# ===========================================================================

async def run_voe_sync() -> None:
    """
    النقطة الرئيسية: تجلب الحلقات → تبني المهام → تعالج كل واحدة.
    """
    log.info(f"🚀 محرك مزامنة Voe بدأ العمل... [{datetime.now().strftime('%H:%M:%S')}]")

    all_episodes = fetch_all_episodes()
    tasks        = [build_task(ep) for ep in all_episodes]
    tasks        = [t for t in tasks if t is not None]

    if not tasks:
        log.info("✅ جميع الحلقات لديها سيرفر Voe حالياً.")
        return

    log.info(f"🎯 يوجد {len(tasks)} حلقة تحتاج إنقاذ.")
    count_success = 0

    for task in tasks:
        ep_id = task["episode_id"]

        success = await process_episode(task)

        if success:
            count_success += 1
        else:
            log.error(f"❌ فشلت المهمة للحلقة {ep_id}")
            _FAILED_TASKS.append(ep_id)

    log.info(f"\n{'═' * 55}")
    log.info(f"✨ المهمة انتهت! تم إنقاذ {count_success} حلقة لـ {TARGET_SERVER.upper()}.")


# ===========================================================================
# Entry Point
# ===========================================================================

if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    loop.run_until_complete(run_voe_sync())