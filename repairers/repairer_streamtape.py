"""
repairer_streamtape.py
======================
مهمة إصلاح روابط Streamtape المكسورة عبر Remote Upload.

المنطق:
  remotedl/add → يرجع remote_id فوراً
  remotedl/status → نستني لما يظهر extid ونبني الرابط
  extid هو الـ file_code النهائي للمشاهدة

الهيكل:
  Section 1  — Configuration
  Section 2  — Supabase Fetchers
  Section 3  — Source Resolver
  Section 4  — Archive Validator
  Section 5  — Streamtape Remote Upload
  Section 6  — Link Repair (منطق رابط واحد)
  Section 7  — Main Orchestrator
"""

import os
import asyncio
import urllib.parse
from datetime import datetime
from typing import Optional

import httpx
from shared import supabase, log, update_link_in_db, mark_link_failed


# ===========================================================================
# Section 1: Configuration — الإعدادات المركزية
# ===========================================================================

STREAMTAPE_LOGIN   = os.getenv("STREAMTAPE_LOGIN")
STREAMTAPE_API_KEY = os.getenv("STREAMTAPE_API_KEY")
BATCH_SIZE         = int(os.getenv("BATCH_SIZE", "5"))

SOURCE_SERVERS = ["archive", "telegram_direct"]

ST_API_BASE  = "https://api.streamtape.com"
EMBED_BASE   = "https://streamtape.com/e"

POLL_INTERVAL  = 30   # ثانية بين كل فحص حالة
POLL_MAX       = 80   # أقصى عدد محاولات polling

RETRY_COUNT    = 3
RETRY_DELAY    = 5    # ثانية بين محاولات الرفع
COOLDOWN_DELAY = 3    # ثانية بين كل رابط وآخر

ARCHIVE_HEADERS = {"Range": "bytes=0-50000"}


# ===========================================================================
# Section 2: Supabase Fetchers — جلب وحفظ البيانات
# ===========================================================================

def fetch_broken_links() -> list[dict]:
    """جلب روابط Streamtape المكسورة بحسب BATCH_SIZE."""
    res = (
        supabase.table("links")
        .select("id, episode_id, url, server_name, episodes(id, media_id, episode_number)")
        .ilike("server_name", "%streamtape%")
        .ilike("last_check_status", "broken")
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
        .eq("last_check_status", "valid")
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


def build_file_name(link: dict) -> str:
    """
    بناء اسم ملف ذكي من بيانات الحلقة.
    الحلقة الأولى أو الفيلم: Media-{m_id}-ID-{e_id}.mp4
    غير ذلك: Media-{m_id}-Ep-{e_num}-ID-{e_id}.mp4
    """
    ep_data = link.get("episodes") or {}
    e_id    = ep_data.get("id", "Unknown")
    m_id    = ep_data.get("media_id", "Unknown")
    e_num   = ep_data.get("episode_number", 0)

    if e_num in [0, 1]:
        return f"Media-{m_id}-ID-{e_id}.mp4"
    return f"Media-{m_id}-Ep-{e_num}-ID-{e_id}.mp4"


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
# Section 5: Streamtape Remote Upload — الرفع عن بعد
# ===========================================================================

def _extract_extid(task_info: dict) -> Optional[str]:
    """
    استخراج extid من task_info.
    يحاول مباشرةً من حقل extid، ثم من الـ url كـ fallback.
    """
    extid = task_info.get("extid")
    if extid:
        return str(extid)

    raw_url = task_info.get("url", "")
    if isinstance(raw_url, str) and "/v/" in raw_url:
        return raw_url.split("/v/")[1].split("/")[0]

    return None


async def _poll_remote_status(
    client: httpx.AsyncClient, remote_id: str
) -> Optional[str]:
    """
    Polling على حالة Remote Upload في Streamtape.
    يُعيد extid عند النجاح أو None عند الفشل / انتهاء المحاولات.
    """
    status_url = (
        f"{ST_API_BASE}/remotedl/status"
        f"?login={STREAMTAPE_LOGIN}&key={STREAMTAPE_API_KEY}&id={remote_id}"
    )

    for attempt in range(1, POLL_MAX + 1):
        await asyncio.sleep(POLL_INTERVAL)
        try:
            s_data = (await client.get(status_url, timeout=15.0)).json()

            # استخراج آمن لبيانات المهمة
            res_dict  = s_data.get("result") if isinstance(s_data, dict) else {}
            res_dict  = res_dict if isinstance(res_dict, dict) else {}
            task_info = res_dict.get(remote_id, {})
            task_info = task_info if isinstance(task_info, dict) else {}

            # url_str للعرض فقط — نتجنب خطأ bool slice
            raw_url = task_info.get("url")
            url_str = str(raw_url) if (raw_url and not isinstance(raw_url, bool)) else ""

            log(
                f"   🔄 [ST] محاولة {attempt}/{POLL_MAX}"
                f" | status={task_info.get('status')}"
                f" | url={url_str[:40]}"
            )

            # فشل سريع لو السيرفر أبلغ عن error
            if task_info.get("status") == "error":
                log("   ⚠️ [ST] المهمة فشلت على السيرفر. إلغاء الـ Polling...")
                return None

            # نجاح: url موجود وليس bool
            if url_str:
                extid = _extract_extid(task_info)
                if extid:
                    log(f"   ✅ [ST] extid={extid}")
                    return extid

        except Exception as e:
            log(f"   ⚠️ [ST] خطأ polling {attempt}: {e}")

    log("   🛑 [ST] انتهت محاولات الـ Polling.")
    return None


async def remote_upload_streamtape(
    client: httpx.AsyncClient, source_url: str, file_name: str
) -> Optional[str]:
    """
    Remote Upload رابط مباشر إلى Streamtape.
    يُعيد extid عند النجاح أو None عند الفشل.
    """
    log(f"   📡 [ST] Remote Upload | login={STREAMTAPE_LOGIN}")
    log(f"   📡 [ST] source: {source_url}")

    add_url = (
        f"{ST_API_BASE}/remotedl/add"
        f"?login={STREAMTAPE_LOGIN}&key={STREAMTAPE_API_KEY}"
        f"&url={urllib.parse.quote(source_url, safe='')}"
        f"&name={urllib.parse.quote(file_name)}"
    )

    try:
        resp = await client.get(add_url, timeout=30.0)
        data = resp.json()
        log(f"   📡 [ST] رد API: {data}")
    except Exception as e:
        log(f"   ❌ [ST] فشل الإرسال: {e}")
        return None

    if data.get("status") != 200:
        log(f"   ❌ [ST] رُفض: {data}")
        return None

    remote_id = data.get("result", {}).get("id")
    if not remote_id:
        log("   ❌ [ST] ما رجعش remote_id!")
        return None

    log(f"   ⏳ [ST] remote_id={remote_id} | بدء Polling...")
    return await _poll_remote_status(client, remote_id)


# ===========================================================================
# Section 6: Link Repair — إصلاح رابط واحد
# ===========================================================================

async def repair_link(client: httpx.AsyncClient, link: dict) -> bool:
    """
    إصلاح رابط واحد: يجرب المصادر المتاحة بالترتيب حتى ينجح أحدها.
    يُعيد True عند نجاح الإصلاح.
    """
    link_id   = link["id"]
    ep_id     = link["episode_id"]
    old_url   = link["url"]
    file_name = build_file_name(link)

    raw_sources = fetch_valid_sources(ep_id)
    available   = extract_available_sources(raw_sources)
    ordered     = get_ordered_sources(available)

    if not ordered:
        log(f"   ⚠️ [Repair] لا يوجد أي مصدر متاح للحلقة {ep_id}. تخطي.")
        mark_link_failed(link_id, "No active source found in DB")
        return False

    log(f"   🔍 [Repair] حلقة ID: {ep_id} | المصادر: {ordered}")
    log(f"   📝 [ST] اسم الملف: {file_name}")

    for source_key in ordered:
        source_url = available[source_key]
        log(f"   ✅ [Source] المصدر الحالي: [{source_key}] → {source_url}")

        # فحص Archive قبل أي شيء
        if source_key == "archive" and not await is_archive_url_valid(client, source_url):
            log("   ❌ [Archive] الرابط ميت! الانتقال للمصدر التالي...")
            continue

        # محاولات الرفع مع Retry
        extid = None
        for attempt in range(1, RETRY_COUNT + 1):
            log(f"   📡 محاولة [{attempt}/{RETRY_COUNT}] مصدر [{source_key}]...")
            extid = await remote_upload_streamtape(client, source_url, file_name)
            if extid:
                break
            if attempt < RETRY_COUNT:
                await asyncio.sleep(RETRY_DELAY)

        if extid:
            new_url = f"{EMBED_BASE}/{extid}"
            if update_link_in_db(link_id, old_url, new_url):
                log(f"   🎉 تم الإصلاح! {new_url}")
                return True
            mark_link_failed(link_id, "DB update failed after successful upload")
            return False

        log(f"   ⏭️ [Repair] فشل المصدر [{source_key}] تماماً، الانتقال للتالي...")

    mark_link_failed(link_id, "All sources failed")
    return False


# ===========================================================================
# Section 7: Main Orchestrator — المنسق الرئيسي
# ===========================================================================

async def run() -> None:
    """
    النقطة الرئيسية لمهمة الإصلاح:
    جلب الروابط التالفة → إصلاح كل رابط → تسجيل النتائج.
    """
    now = datetime.now().strftime("%H:%M:%S")
    log("╔══════════════════════════════════════╗")
    log("║    🔧 STREAMTAPE REPAIRER            ║")
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