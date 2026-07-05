"""
repairer_streamtape.py — إصلاح روابط Streamtape المكسورة

المنطق:
1. remotedl/add → يرجع remote_id فوراً
2. نستني remotedl/status → لما يظهر extid نبني الرابط ونكتبه
3. extid هو الـ file_code النهائي للمشاهدة
"""

import os
import asyncio
import urllib.parse
import httpx
from shared import supabase, log, find_source_url, update_link_in_db, mark_link_failed

STREAMTAPE_API_KEY = os.getenv("STREAMTAPE_API_KEY")
STREAMTAPE_LOGIN = os.getenv("STREAMTAPE_LOGIN")  # أضف هذا
MIXDROP_EMAIL = os.getenv("MIXDROP_EMAIL", "")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "5"))

POLL_INTERVAL = 30
POLL_MAX = 80


async def is_archive_url_valid(client: httpx.AsyncClient, url: str) -> bool:
    """يفحص بشكل صارم وآمن سلامة رابط آرشيف دون تحميل الملف وبآلية التأكيد المزدوج"""
    if "archive.org" not in url:
        return True

    url = str(url).strip()
    if "disabled" in url.lower() or not url.startswith("http"):
        log(f"   ❌ [Source] رابط تالف أو ملغى نصياً.")
        return False

    headers = {"Range": "bytes=0-50000"}  # جلب جزء بسيط جداً من الداتا فقط لفحص الحذف

    try:
        log(f"   🔎 [Source] جاري فحص سلامة السورس المختار بشكل سريع...")

        # 1. محاولة الفحص الأولى السريعة عبر HEAD ثم GET جزئي
        resp = await client.head(url, timeout=7.0)
        status = resp.status_code

        if status == 200:
            resp = await client.get(url, headers=headers, timeout=7.0)
            status = resp.status_code

        is_dead = False
        page_content = resp.text.lower() if status == 200 else ""

        if status in [403, 404] or (
            status == 200
            and ("item not available" in page_content or "disabled" in page_content)
        ):
            is_dead = True

        # 2. جدار الحماية والتأكيد المزدوج: لو اشتبهنا بموته، ننتظر ونعيد الفحص للتأكد من عدم السقوط المؤقت لآرشيف
        if is_dead:
            log(
                f"   ⚠️ [Source] اشتباه بموت رابط آرشيف، جاري إعادة التأكيد بعد 3 ثوانٍ..."
            )
            await asyncio.sleep(3)

            retry_resp = await client.head(url, timeout=7.0)
            retry_status = retry_resp.status_code
            if retry_status == 200:
                retry_resp = await client.get(url, headers=headers, timeout=7.0)
                retry_status = retry_resp.status_code

            retry_content = retry_resp.text.lower() if retry_status == 200 else ""

            if retry_status in [403, 404] or (
                retry_status == 200
                and (
                    "item not available" in retry_content or "disabled" in retry_content
                )
            ):
                log(f"   ❌ [Source] تم تأكيد موت الرابط أو حذفه نهائياً من آرشيف.")
                return False
            else:
                log(
                    f"   🛡️ [Source] الرابط عاد للعمل في المحاولة الثانية، الرابط سليم."
                )
                return True

        return True

    except Exception as e:
        log(f"   ⚠️ [Source] خطأ شبكة أو تايم أوت أثناء فحص الرابط: {e}")
        # في الـ Uploader نفضل تمريره كـ True لو حدث خطأ شبكة عابر لكي لا نخسر الرابط، أو اقلبه لـ False لو أردت الصرامة المطلقة
        return True


async def remote_upload_streamtape(client, source_url, file_name="video.mp4"):
    login = STREAMTAPE_LOGIN  # الاستخدام المباشر للـ Login الصحيح
    log(f"   📡 [ST] Remote Upload | login={login}")
    log(f"   📡 [ST] source: {source_url}")

    safe_name = urllib.parse.quote(file_name)
    add_url = (
        f"https://api.streamtape.com/remotedl/add"
        f"?login={login}&key={STREAMTAPE_API_KEY}"
        f"&url={urllib.parse.quote(source_url, safe='')}&name={safe_name}"
    )

    try:
        resp = await client.get(add_url, timeout=30.0)
        data = resp.json()
        log(f"   📡 [ST] رد API: {data}")
    except Exception as e:
        log(f"   ❌ [ST] فشل الإرسال: {e}")
        return None

    if data.get("status") != 200:
        log(f"   ❌ [ST] رفض: {data}")
        return None

    remote_id = data.get("result", {}).get("id")
    if not remote_id:
        log(f"   ❌ [ST] ما رجعش remote_id!")
        return None

    log(f"   ⏳ [ST] remote_id={remote_id} | بدأ Polling...")

    for attempt in range(1, POLL_MAX + 1):
        await asyncio.sleep(POLL_INTERVAL)
        try:
            s_resp = await client.get(
                f"https://api.streamtape.com/remotedl/status"
                f"?login={login}&key={STREAMTAPE_API_KEY}&id={remote_id}",
                timeout=15.0,
            )
            s_data = s_resp.json() or {}

            # حماية كاملة لاستخراج الداتا وتجنب الأخطاء إذا كانت الاستجابة مقطوعة أو False
            res_dict = s_data.get("result") if isinstance(s_data, dict) else {}
            if not isinstance(res_dict, dict):
                res_dict = {}

            task_info = res_dict.get(remote_id) if isinstance(res_dict, dict) else {}
            if not isinstance(task_info, dict):
                task_info = {}

            # تحويل الـ url إلى نص بأمان لتجنب خطأ 'bool' object is not subscriptable عند عمل slice
            raw_url = task_info.get("url")
            url_str = (
                str(raw_url) if (raw_url and not isinstance(raw_url, bool)) else ""
            )

            log(
                f"   🔄 [ST] محاولة {attempt}/{POLL_MAX} | task={task_info.get('status')} | url={url_str[:40]}"
            )

            # [ميزة السكريبت الأول]: التحقق الفوري من فشل المهمة على السيرفر لإنهاء الفحص فوراً دون إضاعة وقت المحاولات
            if task_info.get("status") == "error":
                log(
                    f"   ⚠️ [ST] المهمة الحالية فشلت على السيرفر (Status: error). إلغاء الفحص لبدء محاولة جديدة..."
                )
                break

            if task_info.get("url") and not isinstance(task_info.get("url"), bool):
                # نستخدم extid — هو الـ file_code النهائي
                extid = task_info.get("extid")
                if not extid:
                    # نستخرجه من الـ url لو مش موجود
                    raw_url = task_info.get("url", "")
                    if "/v/" in raw_url:
                        extid = raw_url.split("/v/")[1].split("/")[0]

                if extid:
                    log(f"   ✅ [ST] extid={extid} — نبني الرابط")
                    return extid

        except Exception as e:
            log(f"   ⚠️ [ST] خطأ polling {attempt}: {e}")

    log(f"   🛑 [ST] انتهت المحاولات")
    return None


async def run():
    log("╔══════════════════════════════════════╗")
    log("║    🔧 STREAMTAPE REPAIRER            ║")
    log(f"║  Batch: {BATCH_SIZE:<28}║")
    log("╚══════════════════════════════════════╝\n")

    res = (
        supabase.table("links")
        .select(
            "id, episode_id, url, server_name, episodes(id, media_id, episode_number)"
        )
        .ilike("server_name", "%streamtape%")
        .eq("last_check_status", "broken")
        .eq("is_fixed", False)
        .order("last_check_at", desc=False, nullsfirst=True)
        .limit(BATCH_SIZE)
        .execute()
    )
    broken = res.data or []
    log(f"🔴 روابط مكسورة: {len(broken)}\n")

    if not broken:
        log("✅ لا يوجد شيء للإصلاح!")
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

            # === التعديل الجديد: جلب وفحص السورس مدمجاً مع التسمية الذكية ===
            log(f"   🔎 [Source] بيدور على archive/telegram لـ episode_id={episode_id}")

            # 1. جلب المصادر الصالحة مباشرة من الداتابيز
            res_sources = (
                supabase.table("links")
                .select("url, server_name")
                .eq("episode_id", episode_id)
                .in_("server_name", ["archive", "telegram_direct"])
                .eq("last_check_status", "valid")
                .execute()
            )
            sources_list = res_sources.data or []
            log(f"   🔎 [Source] النتائج: {sources_list}")

            if not sources_list:
                await mark_link_failed(
                    link_id, "No active archive/telegram_direct source found in DB"
                )
                stats["no_source"] += 1
                continue

            # ترتيب المصادر لتقديم archive أولاً
            sources_list.sort(key=lambda x: 0 if x["server_name"] == "archive" else 1)

            # توليد الاسم الذكي للملف مرة واحدة للحلقة الحالية
            ep_data = link.get("episodes") or {}
            e_id = ep_data.get("id", "Unknown")
            m_id = ep_data.get("media_id", "Unknown")
            e_num = ep_data.get("episode_number", 0)
            if e_num in [0, 1]:
                generated_name = f"Media-{m_id}-ID-{e_id}.mp4"
            else:
                generated_name = f"Media-{m_id}-Ep-{e_num}-ID-{e_id}.mp4"

            extid = None
            is_rescued = False

            # 1. الدوران الشامل على السيرفرات المتاحة لهذه الحلقة بالترتيب
            for source_item in sources_list:
                source_name = source_item["server_name"]
                source_url = source_item["url"]

                # الفحص المسبق الوجوبي لروابط آرشيف
                if source_name == "archive":
                    is_valid = await is_archive_url_valid(client, source_url)
                    if not is_valid:
                        log(
                            f"   ❌ [Source] سورس Archive الحالي ميت! تخطي والانتقال للسيرفر التالي..."
                        )
                        continue

                log(f"   ✅ [Source] السورس النشط الحالي: [{source_name}]")
                log(f"   📝 [ST] التسمية المعتمدة: {generated_name}")

                # 2. نظام الـ Retry: ثلاث محاولات متتالية لنفس السورس قبل إعلان فشله والانتقال للآخر
                for attempt in range(1, 4):
                    log(
                        f"   📡 محاولة [{attempt}/3] للرفع عن بعد باستخدام: [{source_name}]..."
                    )
                    extid = await remote_upload_streamtape(
                        client, source_url, file_name=generated_name
                    )

                    if extid:
                        is_rescued = True
                        break

                    # انتظار تكتيكي قصير لمدة 5 ثوانٍ بين محاولات الفشل لنفس السورس لمنع رفض السيرفر
                    if attempt < 3:
                        await asyncio.sleep(5)

                if is_rescued:
                    break

            # إذا انتهت جميع السيرفرات المتاحة وجميع محاولاتها بالفشل
            if not is_rescued:
                await mark_link_failed(
                    link_id,
                    "Streamtape upload failed after trying all available sources with retries",
                )
                stats["failed"] += 1
                continue

            new_url = f"https://streamtape.com/e/{extid}"
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
