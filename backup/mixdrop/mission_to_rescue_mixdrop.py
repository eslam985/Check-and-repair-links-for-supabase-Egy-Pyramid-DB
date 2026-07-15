import requests
import time
import os
import logging
import random
from datetime import datetime
from urllib.parse import quote
from supabase import create_client
from playwright.async_api import async_playwright
from typing import Optional

# --- الإعدادات ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
MIXDROP_EMAIL = os.environ.get("MIXDROP_EMAIL")
MIXDROP_KEY = os.environ.get("MIXDROP_API_KEY")
TARGET_SERVER = "mixdrop"
SOURCE_SERVERS = [
    "archive",
    "streamtape",
]  # الأولوية للأرشيف ثم التليجرام

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
_USER_AGENTS = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:122.0) Gecko/20100101 Firefox/122.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
)


async def _resolve_streamtape(self, embed_url: str) -> Optional[str]:
    # تحويل الرابط إلى صيغة صفحة التحميل /v/ بدلاً من الـ Embed /e/
    target = embed_url.replace("/e/", "/v/").replace("/f/", "/v/")
    logging.info(f"🕵️  Streamtape Playwright: {target}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        ctx = await browser.new_context(user_agent=random.choice(_USER_AGENTS))
        page = await ctx.new_page()

        try:
            await page.goto(target, wait_until="domcontentloaded")

            # 1. محاولة قنص الرابط مباشرة من العنصر المخفي لتفادي الكابتشا وتأخير الضغط
            try:
                raw_link = await page.locator("#norobotlink").text_content(timeout=5000)
                if raw_link and "get_video" in raw_link:
                    raw_link = raw_link.strip()
                    if raw_link.startswith("//"):
                        raw_link = f"https:{raw_link}"
                    final_url = raw_link if "dl=1" in raw_link else f"{raw_link}&dl=1"
                    logging.info(
                        f"✅ Streamtape URL extracted directly from DOM: {final_url[:60]}..."
                    )
                    return final_url
            except Exception:
                logging.debug(
                    "Streamtape: Direct DOM extraction failed, falling back to click method..."
                )

            # 2. الطريقة الاحتياطية (في حال عدم وجود الرابط في الـ DOM مباشرة)
            btn = "#downloadvideo"
            await page.wait_for_selector(btn, state="visible", timeout=15_000)

            # 1. الضغط على الزر مرة واحدة لتشغيل العداد الزمني (Counter) الخاص بالموقع
            try:
                async with ctx.expect_page(timeout=5000) as new_page_info:
                    await page.click(btn)
                # إغلاق نافذة الإنبثاق (Popup) الناتجة عن الضغطة الأولى إذا ظهرت
                ad_page = await new_page_info.value
                await ad_page.close()
            except Exception:
                pass

            await page.bring_to_front()

            # 2. الانتظار لمدة 6 ثوانٍ حتى ينتهي العداد (5 ثوانٍ) ويقوم السكربت بحقن الرابط
            logging.info("⏳ Streamtape: Waiting for 5s countdown to finish...")
            await page.wait_for_timeout(6000)

            # 3. استخراج الرابط النهائي من الخاصية href للزر
            href = await page.get_attribute(btn, "href")

            if href and "get_video" in href:
                href = href.strip()
                final_url = f"https:{href}" if href.startswith("//") else href
                logging.info(f"✅ Streamtape direct URL resolved: {final_url[:60]}...")
                return final_url

            # فحص محتوى الصفحة لمعرفة سبب الفشل بدقة
            page_text = await page.inner_text("body")
            is_dead = (
                "video no longer available" in page_text.lower()
                or "not found" in page_text.lower()
            )

            logging.warning(
                f"❌ Streamtape failed. Actual href: '{href}' | Is File Deleted: {is_dead}"
            )
            return None

        except Exception as e:
            logging.warning(f"❌ Streamtape extraction failed: {e}")
            return None
        finally:
            await browser.close()


def is_archive_url_valid(url: str) -> bool:
    """يفحص بشكل صارم وآمن سلامة رابط آرشيف دون تحميل الملف وبآلية التأكيد المزدوج"""
    if "archive.org" not in url:
        return True

    url = str(url).strip()
    if "disabled" in url.lower() or not url.startswith("http"):
        print(f"   ❌ [Source] رابط تالف أو ملغى نصياً.")
        return False

    headers = {"Range": "bytes=0-50000"}  # جلب جزء بسيط جداً من الداتا فقط لفحص الحذف

    try:
        print(f"   🔎 [Source] جاري فحص سلامة السورس المختار بشكل سريع...")

        # 1. محاولة الفحص الأولى السريعة عبر HEAD ثم GET جزئي
        resp = requests.head(url, timeout=7.0)
        status = resp.status_code

        if status == 200:
            resp = requests.get(url, headers=headers, timeout=7.0)
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
            print(
                f"   ⚠️ [Source] اشتباه بموت رابط آرشيف، جاري إعادة التأكيد بعد 3 ثوانٍ..."
            )
            time.sleep(3)

            retry_resp = requests.head(url, timeout=7.0)
            retry_status = retry_resp.status_code
            if retry_status == 200:
                retry_resp = requests.get(url, headers=headers, timeout=7.0)
                retry_status = retry_resp.status_code

            retry_content = retry_resp.text.lower() if retry_status == 200 else ""

            if retry_status in [403, 404] or (
                retry_status == 200
                and (
                    "item not available" in retry_content or "disabled" in retry_content
                )
            ):
                print(f"   ❌ [Source] تم تأكيد موت الرابط أو حذفه نهائياً من آرشيف.")
                return False
            else:
                print(
                    f"   🛡️ [Source] الرابط عاد للعمل في المحاولة الثانية، الرابط سليم."
                )
                return True

        return True

    except Exception as e:
        print(f"   ⚠️ [Source] خطأ شبكة أو تايم أوت أثناء فحص الرابط: {e}")
        # في الـ Uploader نفضل تمريره كـ True لو حدث خطأ شبكة عابر لكي لا نخسر الرابط، أو اقلبه لـ False لو أردت الصرامة المطلقة
        return True


def rescue_mixdrop_mission():
    now = datetime.now().strftime("%H:%M:%S")
    print(f"🚀 [{now}] بدء مهمة الإنقاذ الذكية لسيرفر: {TARGET_SERVER.upper()}")

    # 1. جلب جميع الحلقات باستخدام Pagination
    all_episodes = []
    start = 0
    step = 1000
    while True:
        response = (
            supabase.table("episodes")
            .select("id, episode_number, medias(title), links(server_name, url)")
            .range(start, start + step - 1)
            .execute()
        )
        if not response.data:
            break
        all_episodes.extend(response.data)
        if len(response.data) < step:
            break
        start += step

    if not all_episodes:
        print("❌ لم يتم العثور على بيانات!")
        return

    count_success = 0

    for ep in all_episodes:
        ep_id = ep["id"]
        existing_links = ep.get("links", [])

        # التأكد إن ميكس دروب مش موجود
        if any(l["server_name"].lower() == TARGET_SERVER for l in existing_links):
            continue

        # تنظيم المصادر المتاحة
        available_sources = {
            l["server_name"].lower(): l["url"]
            for l in existing_links
            if l["server_name"].lower() in SOURCE_SERVERS
        }

        # ترتيب المصادر
        sorted_sources = []

        # 1. الأولوية القصوى للأرشيف
        if "archive" in available_sources:
            sorted_sources.append("archive")

        # 3. ستريم تاب
        if "streamtape" in available_sources:
            sorted_sources.append("streamtape")

        # تخطي الحلقة لو مفيش أي مصدر متاح
        if not sorted_sources:
            continue

        # === التعديل المحصن: تدوير حقيقي على كافة السيرفرات المتاحة مع حقن باراميتر التليجرام ===
        now = datetime.now().strftime("%H:%M:%S")
        print(f"\n[{now}] 🔍 فحص حلقة ID: {ep_id} | المصادر المتاحة: {sorted_sources}")
        is_rescued = False

        for source_key in sorted_sources:
            # 1. جلب الرابط الصحيح بناءً على نوع السيرفر الحالي في اللوب
            source_url = available_sources.get(source_key)
            # 3. فحص روابط آرشيف قبل إرسالها للرفع
            if source_key == "archive" and not is_archive_url_valid(source_url):
                print(
                    f"   ❌ [Source] رابط Archive الحالي تالف! جاري التجاوز للسيرفر التالي في القائمة..."
                )
                continue

            print(f"   ✅ [Source] السورس النشط الحالي: [{source_key}] → {source_url}")

            # محاولات الرفع (3 محاولات لكل سيرفر متاح)
            for attempt in range(1, 4):
                print(f"📡 محاولة [{attempt}/3] باستخدام: [{source_key}]...")

                try:
                    # === الآلية الأولى: معالجة خاصة لـ Streamtape (تحميل مؤقت ورفع مباشر للملف) ===
                    if source_key == "streamtape":
                        print(
                            "🕵️ جاري استخراج رابط Streamtape المباشر عبر Playwright..."
                        )
                        import asyncio

                        resolved_url = asyncio.run(
                            _resolve_streamtape(None, source_url)
                        )
                        if not resolved_url:
                            print("❌ فشل استخراج رابط Streamtape المباشر.")
                            continue

                        temp_file = f"temp_ep_{ep_id}.mp4"
                        try:
                            print(
                                "📥 جاري سحب الفيلم من Streamtape إلى السيرفر المحلى مؤقتاً..."
                            )
                            with requests.get(
                                resolved_url, stream=True, timeout=60
                            ) as r:
                                r.raise_for_status()
                                total_size = int(r.headers.get("content-length", 0))
                                downloaded = 0
                                with open(temp_file, "wb") as f:
                                    for chunk in r.iter_content(chunk_size=1024 * 1024):
                                        if chunk:
                                            f.write(chunk)
                                            downloaded += len(chunk)
                                            if total_size > 0 and downloaded % (
                                                1024 * 1024 * 100
                                            ) < len(chunk):
                                                print(
                                                    f"   ⏳ تقدم السحب المحلى: {downloaded // (1024*1024)}MB / {total_size // (1024*1024)}MB"
                                                )

                            print(f"🚀 بدء الرفع المباشر الفعلي الآن إلى ميكس دروب...")
                            upload_url = "https://ul.mixdrop.ag/api"
                            with open(temp_file, 'rb') as f:
                                files = {'file': f}
                                data = {'email': MIXDROP_EMAIL, 'key': MIXDROP_KEY}
                                response = requests.post(upload_url, data=data, files=files, timeout=1200)
                                
                                print(f"📡 رد سيرفر ميكس دروب الخام (Debug): {response.text}")
                                upload_res = response.json()
                                
                                if upload_res.get("success"):
                                    final_url = upload_res["result"]["embedurl"]
                                    if not final_url.startswith("https:"):
                                        final_url = "https:" + final_url

                                    supabase.table("links").upsert(
                                        {
                                            "episode_id": ep_id,
                                            "server_name": TARGET_SERVER,
                                            "url": final_url,
                                        }
                                    ).execute()

                                    is_verified = True
                                    is_rescued = True
                                    count_success += 1
                                    break
                                else:
                                    error_msg = (
                                        upload_res.get("error")
                                        or upload_res.get("msg")
                                        or "Unknown Error"
                                    )
                                    print(
                                        f"❌ ميكس دروب رفض استقبال الملف المرفوع: {error_msg}"
                                    )
                        except Exception as e:
                            print(
                                f"❌ خطأ كارثي أثناء عملية النقل المباشر لـ Mixdrop: {e}"
                            )
                        finally:
                            if os.path.exists(temp_file):
                                os.remove(temp_file)
                                print("🗑️ تم تنظيف وحذف الملف المؤقت بنجاح.")

                        if is_rescued:
                            break

                    # === الآلية الثانية: الـ Remote Upload التقليدي لبقية السيرفرات (مثل Archive) ===
                    else:
                        api_url = "https://api.mixdrop.ag/remoteupload"
                        payload = {
                            "email": MIXDROP_EMAIL,
                            "key": MIXDROP_KEY,
                            "url": source_url,
                        }

                        response = requests.get(api_url, params=payload, timeout=30)

                    # التحقق من كود الحالة قبل محاولة القراءة كـ JSON
                    if response.status_code != 200:
                        print(
                            f"❌ خطأ في السيرفر (HTTP {response.status_code}): ميكس دروب قد يكون متوقفاً أو محظوراً."
                        )
                        continue

                    try:
                        upload_res = response.json()
                    except Exception:
                        print(f"❌ الرد ليس JSON! الرد الخام: {response.text[:150]}")
                        continue

                    if upload_res.get("success"):
                        remote_id = upload_res["result"]["id"]

                        # 2. نظام الـ Hunter (Polling) للتأكد من المعالجة
                        is_verified = False
                        for check_attempt in range(1, 200):  # 200 محاولة فحص
                            time.sleep(30)  # انتظر 30 ثانية بين كل فحص

                            # فحص الحالة باستخدام الـ id
                            # API: https://api.mixdrop.ag/reuploadstatus?email=...&key=...&id=...
                            # 1. تعديل رابط الحالة (السطر رقم 100 تقريباً في السكربت السابق)
                            status_url = f"https://api.mixdrop.ag/remotestatus?email={MIXDROP_EMAIL}&key={MIXDROP_KEY}&id={remote_id}"
                            status_res = requests.get(status_url, timeout=20).json()

                            if status_res.get("success"):
                                # ميكس دروب بيرجع الـ result كـ Object مباشر في الـ remotestatus مش لستة
                                status_info = status_res["result"]
                                result_status = status_info.get(
                                    "status"
                                )  # القيم المتوقعة: Complete, Downloading, Error

                                if result_status == "Complete":
                                    file_code = status_info.get("fileref")
                                    # الدوكمنتيشن بيقول إن الـ embedurl بيرجع جاهز في الـ result بتاع الـ remoteupload برضه
                                    # بس الأفضل نبنيه لضمان الدومين بتاعك
                                    final_url = f"https://mixdrop.ag/e/{file_code}"
                                    now = datetime.now().strftime("%H:%M:%S")
                                    print(
                                        f"[{now}] 🎉 تم الاكتمال! الرابط: {final_url}"
                                    )

                                    # حفظ في سوبابيز...

                                    # 3. الحفظ في سوبابيز
                                    supabase.table("links").upsert(
                                        {
                                            "episode_id": ep_id,
                                            "server_name": TARGET_SERVER,
                                            "url": final_url,
                                        }
                                    ).execute()

                                    is_verified = True
                                    break
                                elif result_status == "Error":
                                    print(f"❌ ميكس دروب فشل في سحب الرابط.")
                                    break
                                else:
                                    print(
                                        f"⏳ الحالة الحالية: {result_status} ({check_attempt}/200)..."
                                    )

                        if is_verified:
                            is_rescued = True
                            count_success += 1
                            break
                    else:
                        # ميكس دروب يستخدم مفتاح error وليس msg
                        error_msg = (
                            upload_res.get("error")
                            or upload_res.get("msg")
                            or "Unknown Error"
                        )
                        print(f"⚠️ ميكس دروب رفض الطلب: {error_msg}")
                        if not MIXDROP_EMAIL or not MIXDROP_KEY:
                            print(
                                "🚨 تنبيه: بيانات تسجيل الدخول MIXDROP_EMAIL أو MIXDROP_KEY فارغة!"
                            )

                except Exception as e:
                    print(f"❌ خطأ تقني: {str(e)}")

                if not is_rescued:
                    time.sleep(10)  # انتظار بسيط قبل المحاولة التالية

            if is_rescued:
                break

        # تهدئة بين الحلقات
        time.sleep(20)  # 1 دقائق بين كل حلقة لتجنب الحظر

    now = datetime.now().strftime("%H:%M:%S")
    print(f"\n✨ [{now}] المهمة انتهت! تم إنقاذ {count_success} مادة لـ MixDrop.")


if __name__ == "__main__":
    rescue_mixdrop_mission()
