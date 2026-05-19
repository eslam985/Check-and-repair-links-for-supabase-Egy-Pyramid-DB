"""
repairer_lulustream.py — إصلاح روابط LuluStream المكسورة

المنطق:
1. /api/upload/url → يرجع filecode
2. /api/file/info → نتأكد إن الملف بدأ
3. نبني الرابط ونكتبه
"""

import os
import asyncio
import urllib.parse
import httpx
from shared import supabase, log, find_source_url, update_link_in_db, mark_link_failed

LULUSTREAM_API_KEY = os.getenv("LULUSTREAM_API_KEY")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "5"))

BASE_API = "https://www.lulustream.com/api"
POLL_INTERVAL = 25
POLL_MAX = 20


async def is_archive_url_valid(client, url):
    """يفحص محتوى رابط أرشيف للتأكد أنه غير محذوف أو مغلق"""
    if "archive.org" not in url:
        return True  # الروابط الأخرى مثل تليجرام نعتبرها سليمة مبدئياً
    try:
        # نقوم بعمل طلب سريع لقراءة أول جزء من الصفحة أو الاستجابة
        resp = await client.get(url, timeout=10.0)
        # إذا وجدنا جملة الحذف الصريحة أو كود الخطأ
        if (
            "Item not available" in resp.text
            or "issues with the item's content" in resp.text
        ):
            return False
        return True
    except Exception as e:
        log(f"   ⚠️ [Check Archive] خطأ أثناء فحص الرابط: {e}")
        return False


async def remote_upload_lulu(client, source_url):
    log(f"   📡 [Lulu] Remote Upload من: {source_url}")

    try:
        add_url = f"{BASE_API}/upload/url?key={LULUSTREAM_API_KEY}&url={urllib.parse.quote(source_url, safe='')}"
        resp = await client.get(add_url, timeout=30.0)
        data = resp.json()
        log(f"   📡 [Lulu] رد: {data}")
    except Exception as e:
        log(f"   ❌ [Lulu] فشل الإرسال: {e}")
        return None

    if data.get("status") != 200:
        log(f"   ❌ [Lulu] رفض: {data}")
        return None

    file_code = data.get("result", {}).get("filecode")
    if not file_code:
        log(f"   ❌ [Lulu] ما رجعش filecode!")
        return None

    log(f"   ⏳ [Lulu] filecode={file_code} | Polling...")

    for attempt in range(1, POLL_MAX + 1):
        await asyncio.sleep(POLL_INTERVAL)
        try:
            info_resp = await client.get(
                f"{BASE_API}/file/info?key={LULUSTREAM_API_KEY}&file_code={file_code}",
                timeout=15.0,
            )
            info_data = info_resp.json()
            log(f"   🔄 [Lulu] محاولة {attempt}/{POLL_MAX} | {info_data.get('status')}")

            if info_data.get("status") == 200 and info_data.get("result"):
                log(f"   ✅ [Lulu] جاهز!")
                return file_code
        except Exception as e:
            log(f"   ⚠️ [Lulu] خطأ {attempt}: {e}")

    # نكتب الـ filecode حتى لو انتهت المحاولات — الملف ممكن يكون عم يتحمل
    log(f"   ⚠️ [Lulu] نكتب على المسؤولية")
    return file_code


async def run():
    log("╔══════════════════════════════════════╗")
    log("║    🔧 LULUSTREAM REPAIRER            ║")
    log(f"║  Batch: {BATCH_SIZE:<28}║")
    log("╚══════════════════════════════════════╝\n")

    res = (
        supabase.table("links")
        .select("id, episode_id, url, server_name")
        .ilike("server_name", "%lulu%")
        .eq("last_check_status", "broken")
        .eq("is_fixed", False)
        .order("last_check_at", desc=False, nullsfirst=True)
        .limit(BATCH_SIZE)
        .execute()
    )
    broken = res.data or []
    log(f"🔴 روابط مكسورة: {len(broken)}\n")

    if not broken:
        log("✅ لا يوجد شيء!")
        return

    stats = {"fixed": 0, "no_source": 0, "failed": 0}

    async with httpx.AsyncClient(verify=False, follow_redirects=True) as client:
        for i, link in enumerate(broken, 1):
            link_id = link["id"]
            episode_id = link.get("episode_id")
            old_url = link["url"]

            log(f"\n{'─'*55}")
            log(f"[{i}/{len(broken)}] link_id={link_id} | episode_id={episode_id}")
            log(f"   🔴 {old_url}")

            # جلب كل الروابط المتاحة للحلقة بدلاً من رابط واحد إذا كانت الدالة تدعم ذلك،
            # أو فحص الرابط المختار وتوفير آلية تخطي لأرشيف التالف
            # === التعديل الجديد والموحد: فحص السورس والالتفاف التلقائي المستقر لـ Lulustream ===
            log(f"   🔎 [Source] بيدور على archive/telegram لـ episode_id={episode_id}")

            try:
                res_sources = (
                    supabase.table("links")
                    .select("url, server_name")
                    .eq("episode_id", episode_id)
                    .in_("server_name", ["archive", "telegram_direct"])
                    .in_("last_check_status", ["valid", "good"])
                    .execute()
                )
                sources_list = res_sources.data or []
            except Exception as e:
                log(f"   ❌ [DB Error] فشل جلب المصادر: {e}")
                sources_list = []

            if not sources_list:
                mark_link_failed(
                    link_id, "No active archive/telegram_direct source found in DB"
                )
                stats["no_source"] += 1
                continue

            # ترتيب المصادر (الأرشيف أولاً ثم التيليجرام)
            sources_list.sort(key=lambda x: 0 if x["server_name"] == "archive" else 1)

            selected_source = sources_list[0]
            source = selected_source["url"]
            log(
                f"   ✅ [Source] اختار أولي: {selected_source['server_name']} → {source}"
            )

            # الفحص المسبق لروابط آرشيف والتحويل لتليجرام إذا لزم الأمر
            if selected_source["server_name"] == "archive":
                if not await is_archive_url_valid(client, source):
                    log(
                        f"   ❌ [Source] رابط Archive تالف ومحذوف! جاري التبديل للبديل..."
                    )
                    tg_source = next(
                        (
                            s
                            for s in sources_list
                            if s["server_name"] == "telegram_direct"
                        ),
                        None,
                    )
                    if tg_source:
                        source = tg_source["url"]
                        log(
                            f"   ✅ [Source] تم التحويل تلقائياً للسورس البديل: telegram_direct → {source}"
                        )
                    else:
                        mark_link_failed(
                            link_id,
                            "Archive source is dead and no telegram_direct backup found",
                        )
                        stats["failed"] += 1
                        continue
            # ===================================================================================

            file_code = await remote_upload_lulu(client, source)
            if not file_code:
                mark_link_failed(link_id, f"Lulu upload failed")
                stats["failed"] += 1
                continue

            new_url = f"https://lulustream.com/e/{file_code}"
            if update_link_in_db(link_id, old_url, new_url):
                stats["fixed"] += 1
                log(f"   🎉 تم! {new_url}")
            else:
                stats["failed"] += 1

            await asyncio.sleep(3)

    log(f"\n{'═'*55}")
    log(f"📊 ✅ {stats['fixed']} | ⚠️ {stats['no_source']} | ❌ {stats['failed']}")
    log(f"{'═'*55}")


if __name__ == "__main__":
    asyncio.run(run())
