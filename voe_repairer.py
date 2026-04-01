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
VOE_API_KEY  = os.getenv("VOE_API_KEY")

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
VOE_POLL_INTERVAL = 25   # ثانية بين كل فحص
VOE_POLL_MAX      = 40   # أقصى عدد محاولات (≈ 16 دقيقة)

# ══════════════════════════════════════════════
#          جلب روابط VOE المكسورة
# ══════════════════════════════════════════════
def fetch_broken_voe_links(limit: int) -> list[dict]:
    """يجيب أقدم روابط VOE اللي status = broken وis_fixed = false"""
    print(f"\n🔍 جاري جلب أقدم {limit} رابط VOE مكسور...")
    res = (
        supabase.table("links")
        .select("id, episode_id, url, server_name, quality, link_type, error_message")
        .ilike("server_name", "%voe%")           # أي اسم فيه voe
        .eq("last_check_status", "broken")
        .eq("is_fixed", False)
        .order("last_check_at", desc=False, nullsfirst=True)
        .limit(limit)
        .execute()
    )
    links = res.data or []
    print(f"   ✅ تم العثور على {len(links)} رابط")
    return links


# ══════════════════════════════════════════════
#      البحث عن مصدر الرفع (archive أو telegram)
# ══════════════════════════════════════════════
def find_source_url(episode_id: int) -> str | None:
    """
    يدور على رابط archive.org أو telegram_direct
    مرتبط بنفس الـ episode_id.
    بيجرب الاتنين ويرجع أول واحد يلاقيه.
    """
    if not episode_id:
        return None

    try:
        res = (
            supabase.table("links")
            .select("url, server_name")
            .eq("episode_id", episode_id)
            .in_("server_name", SOURCE_SERVER_NAMES)
            .limit(5)          # جيب أول 5 نتائج وفلتر محلياً
            .execute()
        )
        candidates = res.data or []
    except Exception as e:
        print(f"   ⚠️ خطأ في جلب المصدر: {e}")
        return None

    if not candidates:
        return None

    # الأولوية: archive أولاً ثم telegram_direct
    for preferred in SOURCE_SERVER_NAMES:
        for row in candidates:
            if row.get("server_name", "").lower() == preferred.lower():
                url = row.get("url", "").strip()
                if url:
                    return url

    # لو معجبتناش الأولوية، رجّع أي رابط موجود
    return candidates[0].get("url") if candidates else None


# ══════════════════════════════════════════════
#          Remote Upload إلى VOE
# ══════════════════════════════════════════════
async def remote_upload_to_voe(client: httpx.AsyncClient, source_url: str) -> str | None:
    """
    يرسل أمر Remote Upload لـ VOE
    ويرجع file_code الجديد لما الملف يخلص.
    """
    print(f"   📡 VOE Remote Upload من: {source_url[:80]}...")

    # ── 1. إرسال أمر الرفع ──
    try:
        resp = await client.get(
            "https://voe.sx/api/upload/url",
            params={"key": VOE_API_KEY, "url": source_url},
            timeout=30.0,
        )
        data = resp.json()
    except Exception as e:
        print(f"   ❌ فشل إرسال أمر VOE: {e}")
        return None

    if data.get("status") != 200:
        print(f"   ❌ VOE رفض الأمر: {data.get('msg', data)}")
        return None

    file_code = data.get("result", {}).get("file_code")
    if not file_code:
        print("   ❌ VOE ما رجعش file_code")
        return None

    print(f"   ⏳ VOE قبل الأمر | file_code: {file_code} | بدأ الانتظار...")

    # ── 2. Polling حتى يخلص ──
    for attempt in range(1, VOE_POLL_MAX + 1):
        await asyncio.sleep(VOE_POLL_INTERVAL)
        try:
            status_resp = await client.get(
                "https://voe.sx/api/file/status",
                params={"key": VOE_API_KEY, "file_code": file_code},
                timeout=15.0,
            )
            s_data = status_resp.json()
            status  = s_data.get("result", {}).get("status", "unknown")
            percent = s_data.get("result", {}).get("percent", 0)

            print(f"   🔄 محاولة {attempt}/{VOE_POLL_MAX} | status={status} | {percent}%")

            if status == "finished":
                print(f"   ✅ VOE: الرفع اكتمل! file_code={file_code}")
                return file_code

            # لو السيرفر بطيء جداً بعد عدد معقول من المحاولات، نثق بالـ file_code
            if attempt >= 8 and status in ("downloading", "processing", "converting"):
                print(f"   ⚠️ VOE بطيء (محاولة {attempt}) — نقبل الـ file_code كما هو")
                return file_code

        except Exception as e:
            print(f"   ⚠️ خطأ في polling: {e}")

    print(f"   🛑 انتهت محاولات Polling بدون نتيجة")
    return None


# ══════════════════════════════════════════════
#         تحديث قاعدة البيانات بالرابط الجديد
# ══════════════════════════════════════════════
def update_link_in_db(link_id: int, old_url: str, new_file_code: str):
    """
    يحفظ الرابط القديم في old_url
    ويحدث url بالرابط الجديد
    ويعلّم الرابط كـ valid و is_fixed
    """
    new_url = f"https://voe.sx/e/{new_file_code}"
    now_iso = datetime.now().isoformat()

    try:
        supabase.table("links").update({
            "url":               new_url,
            "old_url":           old_url,        # ← الرابط القديم محفوظ هنا
            "last_check_status": "valid",
            "last_check_at":     now_iso,
            "last_success_at":   now_iso,
            "is_fixed":          True,
            "error_message":     None,
        }).eq("id", link_id).execute()

        print(f"   💾 DB محدّث | id={link_id} | {old_url[:50]}... → {new_url}")
        return True
    except Exception as e:
        print(f"   ❌ فشل تحديث DB: {e}")
        return False


# ══════════════════════════════════════════════
#         تحديث حالة الفشل في قاعدة البيانات
# ══════════════════════════════════════════════
def mark_link_failed(link_id: int, reason: str):
    """يحدث error_message لو فشل الإصلاح"""
    try:
        supabase.table("links").update({
            "error_message":  f"[Repairer] {reason}",
            "last_check_at":  datetime.now().isoformat(),
        }).eq("id", link_id).execute()
    except Exception:
        pass


# ══════════════════════════════════════════════
#                 الحلقة الرئيسية
# ══════════════════════════════════════════════
async def run_voe_repairer():
    print("╔══════════════════════════════════════╗")
    print("║       🔧 VOE REPAIRER بدأ            ║")
    print(f"║  Batch Size: {BATCH_SIZE:<24}║")
    print("╚══════════════════════════════════════╝\n")

    broken_links = fetch_broken_voe_links(BATCH_SIZE)

    if not broken_links:
        print("✅ لا توجد روابط VOE مكسورة تحتاج إصلاح. كل شيء تمام!")
        return

    stats = {"fixed": 0, "no_source": 0, "upload_failed": 0}

    async with httpx.AsyncClient(verify=False, follow_redirects=True) as client:
        for i, link in enumerate(broken_links, 1):
            link_id    = link["id"]
            episode_id = link.get("episode_id")
            old_url    = link["url"]
            server     = link.get("server_name", "voe")

            print(f"\n{'─'*55}")
            print(f"[{i}/{len(broken_links)}] 🔗 Link ID={link_id} | server={server}")
            print(f"   📍 Episode ID: {episode_id}")
            print(f"   🔴 الرابط المكسور: {old_url[:70]}...")

            # ── الخطوة 1: ابحث عن مصدر الرفع ──
            source_url = find_source_url(episode_id)

            if not source_url:
                print(f"   ⚠️ لا يوجد رابط archive/telegram لهذه الحلقة — تخطي")
                mark_link_failed(link_id, "No source URL found (archive/telegram_direct)")
                stats["no_source"] += 1
                continue

            print(f"   🟢 مصدر الرفع: {source_url[:80]}...")

            # ── الخطوة 2: Remote Upload إلى VOE ──
            new_file_code = await remote_upload_to_voe(client, source_url)

            if not new_file_code:
                print(f"   ❌ فشل الرفع على VOE — تخطي")
                mark_link_failed(link_id, f"VOE remote upload failed from: {source_url[:60]}")
                stats["upload_failed"] += 1
                continue

            # ── الخطوة 3: تحديث قاعدة البيانات ──
            success = update_link_in_db(link_id, old_url, new_file_code)
            if success:
                stats["fixed"] += 1
                print(f"   🎉 تم إصلاح الرابط بنجاح!")
            else:
                stats["upload_failed"] += 1

            # استراحة صغيرة بين كل رابط لتجنب Rate Limit
            await asyncio.sleep(3)

    # ── الملخص النهائي ──
    print(f"\n{'═'*55}")
    print(f"📊 ملخص الجلسة:")
    print(f"   ✅ تم إصلاحه:        {stats['fixed']}")
    print(f"   ⚠️ بدون مصدر:        {stats['no_source']}")
    print(f"   ❌ فشل الرفع:        {stats['upload_failed']}")
    print(f"{'═'*55}")


# ══════════════════════════════════════════════
#                   Entry Point
# ══════════════════════════════════════════════
if __name__ == "__main__":
    asyncio.run(run_voe_repairer())