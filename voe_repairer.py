"""
╔══════════════════════════════════════════════════════════════════╗
║           🔧 VOE REPAIRER - EgyPyramid Guardian Ultra           ║
║  يجيب روابط VOE المكسورة → يرفعها تاني → يحدث قاعدة البيانات  ║
╚══════════════════════════════════════════════════════════════════╝

الخطوات:
1. يجيب كل روابط VOE اللي حالتها "broken" من جدول links
2. لكل رابط مكسور → يدور على رابط archive أو telegram_direct
   في نفس الـ episode_id
3. يرفع Remote Upload لـ VOE من الرابط اللي لقاه
4. ينتظر الـ file_code الجديد
5. يحدث الـ url القديم بالجديد ويحفظ القديم في old_url
6. يحدث last_check_status → "valid" و is_fixed → true

متطلبات .env:
  SUPABASE_URL, SUPABASE_KEY, VOE_API_KEY
"""

import os
import asyncio
import httpx
from datetime import datetime
from supabase import create_client

# ══════════════════════════════════════════════
#                 الإعدادات
# ══════════════════════════════════════════════
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
VOE_API_KEY = os.getenv("VOE_API_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("❌ SUPABASE_URL أو SUPABASE_KEY ناقص!")
    exit(1)

if not VOE_API_KEY:
    print("❌ VOE_API_KEY ناقص!")
    exit(1)

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# عدد روابط يشتغل عليها في كل ران (خلّيها صغيرة في البداية)
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "10"))

# أسماء السيرفرات اللي هيدور عليها كمصدر للرفع
SOURCE_SERVER_NAMES = ["archive", "telegram_direct"]

# VOE polling config
VOE_POLL_INTERVAL = 25  # ثانية بين كل فحص
VOE_POLL_MAX = 40  # أقصى عدد محاولات (≈ 16 دقيقة)


# ══════════════════════════════════════════════
#          جلب روابط VOE المكسورة
# ══════════════════════════════════════════════
def fetch_broken_voe_links(limit: int) -> list[dict]:
    """يجيب أقدم روابط VOE اللي status = broken وis_fixed = false"""
    log(f"\n🔍 جاري جلب أقدم {limit} رابط VOE مكسور...")
    res = (
        supabase.table("links")
        .select("id, episode_id, url, server_name, quality, link_type, error_message")
        .ilike("server_name", "%voe%")
        .eq("last_check_status", "broken")
        .eq("is_fixed", False)
        .order("last_check_at", desc=False, nullsfirst=True)
        .limit(limit)
        .execute()
    )
    links = res.data or []
    log(f"   ✅ تم العثور على {len(links)} رابط")
    return links


# ══════════════════════════════════════════════
#      البحث عن مصدر الرفع (archive أو telegram)
# ══════════════════════════════════════════════
def find_source_url(episode_id: int) -> str | None:
    if not episode_id:
        log("   ⚠️ [Source] episode_id فاضي — مش هينفع يدور!")
        return None

    log(f"   🔎 [Source] بيدور على archive/telegram لـ episode_id={episode_id}")
    try:
        res = (
            supabase.table("links")
            .select("url, server_name")
            .eq("episode_id", episode_id)
            .in_("server_name", SOURCE_SERVER_NAMES)
            .limit(5)
            .execute()
        )
        candidates = res.data or []
        log(f"   🔎 [Source] النتائج اللي رجعت: {candidates}")
    except Exception as e:
        log(f"   ⚠️ [Source] خطأ في جلب المصدر: {type(e).__name__}: {e}")
        return None

    if not candidates:
        log(f"   ⚠️ [Source] مفيش archive أو telegram_direct لـ episode_id={episode_id}")
        return None

    # الأولوية: archive أولاً ثم telegram_direct
    for preferred in SOURCE_SERVER_NAMES:
        for row in candidates:
            if row.get("server_name", "").lower() == preferred.lower():
                url = row.get("url", "").strip()
                if url:
                    log(f"   ✅ [Source] اختار: server={preferred} | url={url}")
                    return url

    fallback = candidates[0].get("url")
    log(f"   ✅ [Source] Fallback: {fallback}")
    return fallback


# ══════════════════════════════════════════════
#          Remote Upload إلى VOE (إصدار السرعة القصوى)
# ══════════════════════════════════════════════
async def remote_upload_to_voe(
    client: httpx.AsyncClient, source_url: str
) -> str | None:
    log(f"   📡 [VOE] إرسال Remote Upload من: {source_url}")

    # ── 1. إرسال أمر الرفع ──
    try:
        resp = await client.get(
            "https://voe.sx/api/upload/url",
            params={"key": VOE_API_KEY, "url": source_url},
            timeout=30.0,
        )
        data = resp.json()
        log(f"   📡 [VOE] رد الـ API: {data}")
    except Exception as e:
        log(f"   ❌ [VOE] فشل إرسال الأمر: {type(e).__name__}: {e}")
        return None

    if data.get("status") != 200:
        log(
            f"   ❌ [VOE] رفض الأمر | status={data.get('status')} | msg={data.get('msg')}"
        )
        return None

    # استخراج الـ file_code فوراً
    file_code = data.get("result", {}).get("file_code")

    if file_code:
        log(f"   🎯 [VOE] نجاح فوري! تم استلام الكود: {file_code}")
        log(f"   🚀 [VOE] سيتم التحديث في قاعدة البيانات فوراً دون انتظار المعالجة.")
        return file_code
    else:
        log(f"   ❌ [VOE] ما رجعش file_code! الـ result: {data.get('result')}")
        return None


# ══════════════════════════════════════════════
#              Helper: طباعة فورية مضمونة
# ══════════════════════════════════════════════
import sys
import traceback


def log(msg: str):
    """طباعة فورية مضمونة — لا تنتظر GitHub buffer"""
    print(msg, flush=True)
    sys.stdout.flush()


# ══════════════════════════════════════════════
#         تحديث قاعدة البيانات بالرابط الجديد
# ══════════════════════════════════════════════
def update_link_in_db(link_id: int, old_url: str, new_file_code: str) -> bool:
    """
    يحفظ الرابط القديم في old_url
    ويحدث url بالرابط الجديد
    ويعلّم الرابط كـ valid و is_fixed
    """
    new_url = f"https://voe.sx/e/{new_file_code}"
    now_iso = datetime.now().isoformat()

    payload = {
        "url": new_url,
        "old_url": old_url,
        "last_check_status": "valid",
        "last_check_at": now_iso,
        "last_success_at": now_iso,
        "is_fixed": True,
        "error_message": None,
    }

    log(f"   🔄 [DB] محاولة تحديث | link_id={link_id}")
    log(f"   🔄 [DB] payload = {payload}")

    try:
        response = supabase.table("links").update(payload).eq("id", link_id).execute()

        # supabase-py بيرجع response.data قائمة — لو فاضية يبقى ما لاقيش الـ row
        log(f"   🔄 [DB] raw response.data = {response.data}")

        if response.data:
            log(f"   ✅ [DB] نجح التحديث | id={link_id} | URL الجديد: {new_url}")
            return True
        else:
            log(
                f"   ⚠️ [DB] response.data فاضي — ممكن الـ link_id={link_id} مش موجود في الجدول أو RLS بيمنع الكتابة!"
            )
            return False

    except Exception as e:
        log(f"   ❌ [DB] Exception أثناء update | link_id={link_id}")
        log(f"   ❌ [DB] نوع الخطأ: {type(e).__name__}")
        log(f"   ❌ [DB] الرسالة: {str(e)}")
        log(f"   ❌ [DB] Traceback كامل:")
        traceback.print_exc(file=sys.stdout)
        sys.stdout.flush()
        return False


# ══════════════════════════════════════════════
#         تحديث حالة الفشل في قاعدة البيانات
# ══════════════════════════════════════════════
def mark_link_failed(link_id: int, reason: str):
    """يحدث error_message لو فشل الإصلاح"""
    log(f"   📝 [DB] تسجيل فشل | link_id={link_id} | السبب: {reason}")
    try:
        response = (
            supabase.table("links")
            .update(
                {
                    "error_message": f"[Repairer] {reason}",
                    "last_check_at": datetime.now().isoformat(),
                }
            )
            .eq("id", link_id)
            .execute()
        )
        if response.data:
            log(f"   ✅ [DB] تم تسجيل الفشل بنجاح | id={link_id}")
        else:
            log(f"   ⚠️ [DB] mark_failed: response.data فاضي | id={link_id}")
    except Exception as e:
        log(f"   ❌ [DB] فشل تسجيل الخطأ نفسه! | {type(e).__name__}: {e}")
        sys.stdout.flush()


# ══════════════════════════════════════════════
#                 الحلقة الرئيسية
# ══════════════════════════════════════════════
async def run_voe_repairer():
    log("╔══════════════════════════════════════╗")
    log("║       🔧 VOE REPAIRER بدأ            ║")
    log(f"║  Batch Size: {BATCH_SIZE:<24}║")
    log("╚══════════════════════════════════════╝\n")

    broken_links = fetch_broken_voe_links(BATCH_SIZE)

    if not broken_links:
        log("✅ لا توجد روابط VOE مكسورة تحتاج إصلاح. كل شيء تمام!")
        return

    stats = {"fixed": 0, "no_source": 0, "upload_failed": 0}

    async with httpx.AsyncClient(verify=False, follow_redirects=True) as client:
        for i, link in enumerate(broken_links, 1):
            link_id = link["id"]
            episode_id = link.get("episode_id")
            old_url = link["url"]
            server = link.get("server_name", "voe")

            log(f"\n{'─'*55}")
            log(f"[{i}/{len(broken_links)}] 🔗 Link ID={link_id} | server={server}")
            log(f"   📍 Episode ID: {episode_id}")
            log(f"   🔴 الرابط المكسور: {old_url}")

            # ── الخطوة 1: ابحث عن مصدر الرفع ──
            source_url = find_source_url(episode_id)

            if not source_url:
                log(f"   ⚠️ لا يوجد رابط archive/telegram لهذه الحلقة — تخطي")
                mark_link_failed(
                    link_id, "No source URL found (archive/telegram_direct)"
                )
                stats["no_source"] += 1
                continue

            log(f"   🟢 مصدر الرفع: {source_url}")

            # ── الخطوة 2: Remote Upload إلى VOE ──
            new_file_code = await remote_upload_to_voe(client, source_url)

            if not new_file_code:
                log(f"   ❌ فشل الرفع على VOE — تخطي")
                mark_link_failed(
                    link_id, f"VOE remote upload failed from: {source_url}"
                )
                stats["upload_failed"] += 1
                continue

            log(f"   🎯 file_code الجديد: {new_file_code} | جاري كتابته في DB الآن...")

            # ── الخطوة 3: تحديث قاعدة البيانات فوراً ──
            success = update_link_in_db(link_id, old_url, new_file_code)
            if success:
                stats["fixed"] += 1
                log(f"   🎉 تم إصلاح الرابط بنجاح!")
            else:
                log(f"   ❌ فشل كتابة DB رغم نجاح الرفع! راجع الـ logs أعلاه.")
                stats["upload_failed"] += 1

            # استراحة صغيرة بين كل رابط لتجنب Rate Limit
            await asyncio.sleep(3)

    # ── الملخص النهائي ──
    log(f"\n{'═'*55}")
    log(f"📊 ملخص الجلسة:")
    log(f"   ✅ تم إصلاحه:        {stats['fixed']}")
    log(f"   ⚠️ بدون مصدر:        {stats['no_source']}")
    log(f"   ❌ فشل الرفع/DB:     {stats['upload_failed']}")
    log(f"{'═'*55}")


# ══════════════════════════════════════════════
#                   Entry Point
# ══════════════════════════════════════════════
if __name__ == "__main__":
    asyncio.run(run_voe_repairer())
