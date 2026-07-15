import os
import httpx
import random  # ضيف ده فوق خالص
import asyncio
import re
from tqdm import tqdm
import sys
import urllib.parse
import subprocess
import math
from supabase import create_client
from telethon import TelegramClient, events
from playwright.async_api import async_playwright

# --- 1. الإعدادات الصحيحة (بناءً على بياناتك) ---

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

BOT_TOKEN = os.environ.get("BOT_TOKEN")
API_ID = int(os.environ.get('TG_API_ID', 0))
API_HASH = os.environ.get('TG_API_HASH')
TARGET_CHAT = "@EgyPyramid_stream_bot"  # البوت اللي بيدي اللينكات
SOURCE_SERVERS = [
    "streamtape",
    "mixdrop",
    "vk",
    "download",
    # "archive",
]
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
# استخدام اسم جلسة ثابت عشان ميسألش عن الكود كل مرة
client = TelegramClient("egy_sync_session", API_ID, API_HASH, sequential_updates=True)
FAILED_TASKS = []  # قائمة المهام اللي فشلت في الجلسة الحالية


async def get_mixdrop_direct_link(embed_url):
    target_url = embed_url.replace("/e/", "/f/")
    if "?download" not in target_url:
        target_url += "?download"

    print(f"🕵️ محاكاة سلوك بشري على: {target_url}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True
        )  # يمكن جعلها False لو بتجرب محلياً
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
        )
        page = await context.new_page()

        try:
            await page.goto(target_url, wait_until="domcontentloaded")

            # --- 🔍 فحص هل الملف محذوف فعلياً من المصدر ---
            page_content = await page.content()
            if "can't find the file you are looking for" in page_content:
                print("🚫 الرابط ميت: MixDrop بيقول We can't find the file")
                await browser.close()
                return "404_DELETED"

            btn_selector = "a.download-btn"

            # رفعنا المدى لـ 10 لضمان وجود محاولات كافية بعد الـ Reload
            for i in range(1, 11):
                try:
                    await page.wait_for_selector(
                        btn_selector, state="visible", timeout=10000
                    )
                    print(f"🖱️ نقرة رقم {i}...")

                    # --- ⚡ تعديل الـ Reload الذكي ⚡ ---
                    if i == 5:
                        print(
                            "🔄 الموقع يبدو متجمداً.. جاري إعادة تحميل الصفحة (Reload) للتنشيط..."
                        )
                        await page.reload(wait_until="domcontentloaded")
                        await page.wait_for_timeout(3000)
                        continue
                    # ----------------------------------
                    try:
                        async with context.expect_page(timeout=10000) as new_page_info:
                            await page.click(btn_selector)

                        ad_page = await new_page_info.value
                        print(f"📺 إعلان ظهر، ننتظره قليلاً...")
                        await page.wait_for_timeout(5000)
                        await ad_page.close()
                    except Exception:
                        print(f"⚠️ النقرة {i} لم تفتح إعلاناً.")
                    # ----------------------------------

                    await page.bring_to_front()

                    # فحص الرابط المباشر - صيد الدومينات الفرعية الجديدة
                    href = await page.get_attribute(btn_selector, "href")

                    if href and href.startswith("http"):
                        # فحص ذكي: هل الرابط يحتوي على كلمة mxcontent (بأي شكل) أو ليس له علاقة بـ mixdrop؟
                        is_valid_direct = "mxcontent" in href or (
                            not ("?download" in href or "mixdrop" in href)
                        )

                        if is_valid_direct:
                            print(f"✅ تم صيد الرابط بنجاح: {href[:60]}...")

                            await browser.close()
                            return href

                    print("⏳ الرابط لم يظهر بعد، ننتظر ثواني للنقرة التالية...")
                    await page.wait_for_timeout(
                        5000
                    )  # زودنا الانتظار لـ 5 ثواني عشان ندي فرصة للسيرفر
                except Exception as e:
                    print(f"⚠️ خطأ في المحاولة {i}: {str(e)}")
                    continue  # لو محاولة فشلت يكمل للي بعدها ميفصلش السكريبت

            await browser.close()
            return None

        except Exception as e:
            print(f"❌ خطأ أثناء المحاكاة البشرية: {str(e)}")
            await browser.close()
            return None


async def get_next_task():
    # 1. إضافة تأخير عشوائي بسيط (0-3 ثواني) لفك تلاحم النسخ
    await asyncio.sleep(random.uniform(0, 3))

    print("🔍 البحث عن مهمة غير محجوزة...")

    # Pagination لتغطية كل الحلقات
    for offset in range(0, 15000, 1000):
        res = (
            supabase.table("episodes")
            .select("id, episode_number, media_id, medias(title), links(server_name)")
            .range(offset, offset + 999)
            .execute()
        )

        if not res.data:
            break

        for ep in res.data:
            ep_id = ep["id"]
            if ep_id in FAILED_TASKS:
                continue

            # فحص هل تم حجزها بالفعل
            existing_links = ep.get("links", [])
            has_lock = any(
                "telegram_direct" in str(l.get("server_name", "")).lower()
                for l in existing_links
            )

            if has_lock:
                continue

            fake_url = f"https://eslam315-egy-streamer.hf.space/stream/1?hash=LOCKING_{random.randint(1000,9999)}"

            try:
                # محاولة الحجز - لو نوت بوك تانية سبقتك، السطر ده هيضرب Error بسبب الـ Unique Constraint
                supabase.table("links").insert(
                    {
                        "episode_id": ep_id,
                        "url": fake_url,
                        "server_name": "telegram_direct",
                        "quality": "720p",
                        "last_check_status": "processing",
                    }
                ).execute()

                print(f"🔒 نجحت في حجز الحلقة {ep_id}")

                # جلب المصادر المتاحة للتحميل بعد الحجز الناجح
                sources_res = (
                    supabase.table("links")
                    .select("*")
                    .eq("episode_id", ep_id)
                    .execute()
                )
                available_sources = [
                    l
                    for l in sources_res.data
                    if any(
                        srv.lower() in str(l.get("server_name", "")).lower()
                        for srv in SOURCE_SERVERS
                    )
                    and "telegram_direct" not in str(l.get("server_name", "")).lower()
                ]

                return {
                    "episode_id": ep_id,
                    "sources": available_sources,
                    "title": ep.get("medias", {}).get("title", "Unknown"),
                    "ep_num": ep["episode_number"],
                    "fake_url": fake_url,
                }
            except Exception as e:
                # لو حصل خطأ "تكرار" يعني فيه نوت بوك تانية حجزتها في نفس اللحظة
                print(f"⚠️ فشل الحجز (مأخوذة بالفعل): {ep_id}")
                continue

    return None


def split_video_by_size(input_file, max_size_gb=1.9):
    # 1. حساب حجم الملف الحالي بالبايت
    file_size = os.path.getsize(input_file)
    max_size_bytes = max_size_gb * 1024 * 1024 * 1024

    if file_size <= max_size_bytes:
        size_mb = file_size / (1024 * 1024)
        print(f"✅ الملف حجمه مناسب ({size_mb:.2f} MB)، لا داعي للتقسيم.")
        return [input_file]

    # 2. حساب عدد الأجزاء المطلوبة
    num_parts = math.ceil(file_size / max_size_bytes)
    print(f"📦 الملف كبير جداً، سيتم تقسيمه إلى {num_parts} أجزاء...")

    # 3. جلب مدة الفيديو الكلية (Duration) باستخدام ffprobe
    cmd_duration = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        input_file,
    ]
    duration = float(subprocess.check_output(cmd_duration).decode().strip())

    # الوقت التقريبي لكل جزء (بالثواني)
    part_duration = duration / num_parts

    output_files = []
    base_name = input_file.replace(".mp4", "")

    # 4. عملية التقطيع بدون فقدان جودة (-c copy)
    for i in range(num_parts):
        start_time = i * part_duration
        output_part = f"{base_name}_part{i+1}.mp4"

        # أمر التقطيع: -ss للبداية، -t للمدة، -c copy لعدم تغيير الجودة
        cmd_split = [
            "ffmpeg",
            "-y",
            "-ss",
            str(start_time),
            "-t",
            str(part_duration),
            "-i",
            input_file,
            "-c",
            "copy",
            "-map",
            "0",
            output_part,
        ]

        print(f"🎬 جاري استخراج الجزء {i+1}...")
        subprocess.run(cmd_split, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        output_files.append(output_part)

    return output_files


async def download_general(url, filename, max_retries=3):
    """دالة تحميل ذكية تتعامل مع الأرشيف أو الروابط المباشرة الأخرى"""
    final_url = url

    # 1. منطق خاص بالأرشيف فقط لجلب الرابط المباشر
    if "archive.org" in url:
        identifier = url.rstrip("/").split("/")[-1]
        api_url = f"https://archive.org/metadata/{identifier}"
        try:
            async with httpx.AsyncClient(timeout=20.0) as ac:
                resp = await ac.get(api_url)
                data = resp.json()
                files = data.get("files", [])
                video_file = next(
                    (f["name"] for f in files if f["name"].lower().endswith(".mp4")),
                    None,
                )
                if video_file:
                    final_url = f"https://archive.org/download/{identifier}/{urllib.parse.quote(video_file)}"
                elif not url.lower().endswith(".mp4"):
                    final_url = f"{url.rstrip('/')}/{identifier}.mp4"
        except:
            print("⚠️ فشل فحص ميتاداتا الأرشيف، سنحاول بالرابط المتاح.")

    # 2. تنفيذ التحميل
    for attempt in range(1, max_retries + 1):
        try:
            print(f"📥 محاولة تحميل ({attempt}/{max_retries}): {final_url}")
            async with httpx.AsyncClient(timeout=None, follow_redirects=True) as c:
                async with c.stream("GET", final_url) as r:
                    r.raise_for_status()

                    content_type = r.headers.get("Content-Type", "").lower()
                    if (
                        "video" not in content_type
                        and "application/octet-stream" not in content_type
                    ):
                        print(f"❌ فشل: نوع الملف غير مدعوم ({content_type})")
                        return False

                    total = int(r.headers.get("Content-Length", 0))
                    if total < 5 * 1024 * 1024:
                        print(f"❌ فشل: الحجم صغير جداً ({total/(1024*1024):.2f} MB)")
                        return False

                    with open(filename, "wb") as f, tqdm(
                        total=total,
                        unit="B",
                        unit_scale=True,
                        desc=f"📥 {filename}",
                        leave=False,
                    ) as bar:
                        async for chunk in r.aiter_bytes():
                            f.write(chunk)
                            bar.update(len(chunk))
            return True
        except Exception as e:
            print(f"⚠️ خطأ في المحاولة {attempt}: {str(e)}")
            await asyncio.sleep(5)
    return False


# دالة لمراقبة تقدم الرفع (تتحط فوق خالص)


def progress_callback(current, total):
    percent = (current / total) * 100
    # بنحسب الميجات
    curr_mb = current // (1024 * 1024)
    tot_mb = total // (1024 * 1024)

    # كتابة مباشرة في الـ stdout عشان نتخطى "تأخير" الكولاب
    output = f"\r📤 جاري الرفع: {percent:.1f}% ({curr_mb}MB / {tot_mb}MB)"
    sys.stdout.write(output)
    sys.stdout.flush()  # إجبار على الظهور فوراً


async def run_sync():
    await client.start()
    print("🚀 محرك المزامنة بدأ العمل...")

    while True:
        task = await get_next_task()
        if not task:
            print("✅ انتهت جميع المهام المتاحة.")
            break

        ep_id = task["episode_id"]
        raw_title = task.get("title", "Unknown")
        ep_num = str(task.get("ep_num", "1"))
        display_title = (
            raw_title if ep_num in ["0", "1"] else f"{raw_title} - حلقة {ep_num}"
        )
        temp_file = f"sync_{ep_id}.mp4"

        print(f"\n" + "=" * 50)
        print(f"📦 مهمة جديدة: {display_title} (ID: {ep_id})")

        try:
            download_success = False
            for source in task["sources"]:
                source_url = source["url"]
                source_name = source["server_name"].lower()
                print(f"📡 محاولة من سيرفر: {source_name}...")

                target_download_url = source_url
                if "mixdrop" in source_name or "mixdrop" in source_url:
                    print("🛠️ تشغيل Playwright لـ MixDrop...")
                    direct_link = await get_mixdrop_direct_link(source_url)
                    if direct_link == "404_DELETED" or not direct_link:
                        continue
                    target_download_url = direct_link

                if await download_general(target_download_url, temp_file):
                    download_success = True
                    break

            if not download_success:
                # حذف الرابط الوهمي
                supabase.table("links").delete().eq("url", task["fake_url"]).execute()
                # إضافة المهمة للقائمة السوداء المؤقتة في السكربت ده بس
                FAILED_TASKS.append(ep_id)
                print(f"🔓 تم فك الحجز وإضافة الحلقة {ep_id} لقائمة التجاهل المؤقتة.")
                continue

            video_parts = split_video_by_size(temp_file)

            for i, part_file in enumerate(video_parts):
                part_label = f" (الجزء {i+1})" if len(video_parts) > 1 else ""
                current_title = f"{display_title}{part_label}"
                part_num = i + 1
                current_server = (
                    f"telegram_direct_P{part_num}"
                    if len(video_parts) > 1
                    else "telegram_direct"
                )
                current_quality = (
                    f"720p - Part {part_num}" if len(video_parts) > 1 else "720p"
                )

                async with client.action(TARGET_CHAT, "document"):
                    sent_msg = await client.send_file(
                        "me",
                        part_file,
                        caption=f"🎬 {current_title} | ID: {ep_id}",
                        progress_callback=progress_callback,
                    )
                    await sent_msg.forward_to(TARGET_CHAT)
                    await asyncio.sleep(12)

                    async for message in client.iter_messages(TARGET_CHAT, limit=5):
                        if message.text and "hf.space" in message.text:
                            new_url = re.search(
                                r"(https?://[^\s`]+hf\.space[^\s`]+)", message.text
                            )
                            if new_url:
                                final_link = new_url.group(1).strip().replace("`", "")
                                # إذا كان الجزء الأول، نحدث القفل الوهمي. إذا كان جزء إضافي، ننشئ سجلاً جديداً.
                                # تحديث الرابط الوهمي بالرابط الحقيقي (أول جزء) أو إضافة أجزاء جديدة
                                if i == 0:
                                    supabase.table("links").update(
                                        {
                                            "url": final_link,
                                            "server_name": current_server,
                                            "quality": current_quality,
                                            "last_check_status": "valid",
                                        }
                                    ).eq("url", task["fake_url"]).execute()
                                    print(f"✅ تم استبدال الرابط الوهمي بالحقيقي.")
                                else:
                                    supabase.table("links").insert(
                                        {
                                            "episode_id": ep_id,
                                            "url": final_link,
                                            "server_name": current_server,
                                            "quality": current_quality,
                                            "last_check_status": "valid",
                                        }
                                    ).execute()
                                    print(f"✅ تم إضافة جزء إضافي.")
                                break
                if part_file != temp_file and os.path.exists(part_file):
                    os.remove(part_file)

            if os.path.exists(temp_file):
                os.remove(temp_file)

        except Exception as e:
            print(f"❌ خطأ: {str(e)}")
            if os.path.exists(temp_file):
                os.remove(temp_file)


if __name__ == "__main__":
    try:
        await run_sync()
    finally:
        # دي بتضمن إن الملف يتساب حتى لو حصل إيرور
        await client.disconnect()
        print("🔌 تم فصل الاتصال وإغلاق ملف الجلسة.")
