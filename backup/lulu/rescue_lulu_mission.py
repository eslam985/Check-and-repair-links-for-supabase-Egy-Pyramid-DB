import requests
import time
import os
import random
from urllib.parse import quote
import logging
import random
import httpx
import asyncio
from supabase import create_client, Client
from playwright.async_api import async_playwright
from typing import Optional

# --- الإعدادات المباشرة ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

LULU_API_KEY = os.environ.get("LULUSTREAM_API_KEY")
TARGET_SERVER = "lulustream"
SOURCE_SERVERS = ["archive", "telegram_direct", "streamtape"]  # تليجرام أولاً

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


def rescue_lulu_mission():
    print(f"🚀 بدء مهمة الإنقاذ الذكية لسيرفر: {TARGET_SERVER.upper()}")

    # 1. جلب جميع الحلقات باستخدام Pagination (تجاوز حد الـ 1000 سجل)
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

        # التأكد إن لولو مش موجود
        if any(l["server_name"].lower() == TARGET_SERVER for l in existing_links):
            continue

        # تنظيم الروابط المتاحة (أرشيف وتليجرام)
        # تنظيم الروابط المتاحة
        # تنظيم الروابط المتاحة
        available_sources = {
            l["server_name"].lower(): l["url"]
            for l in existing_links
            if l["server_name"].lower() in SOURCE_SERVERS
        }

        # ترتيب المصادر يدوياً للتأكد أن تليجرام له الأولوية المطلقة
        sorted_sources = []
        if "archive" in available_sources:
            sorted_sources.append("archive")
        if "telegram_direct" in available_sources:
            sorted_sources.append("telegram_direct")
        if "streamtape" in available_sources:
            sorted_sources.append("streamtape")

        if not available_sources:
            continue  # لا يوجد مصدر صالح لهذه الحلقة

        # السطر الأفضل (عشان تتابع الترتيب الصح):
        # === التعديل الجديد: الفحص المسبق والالتفاف التلقائي لـ Lulustream ===
        print(f"🔍 فحص حلقة ID: {ep_id} | المصادر المرتبة المتاحة: {sorted_sources}")

        is_rescued = False

        # جلب المصدر الأول المختار مبدئياً بناءً على مصفوفة الأولويات المرتبة
        primary_source_key = sorted_sources[0]
        source_url = available_sources[primary_source_key]
        print(
            f"   ✅ [Source] السورس الأولي المختار: [{primary_source_key}] → {source_url}"
        )

        # إذا كان الاختيار الأول هو آرشيف، نتأكد من سلامته قبل توليد التوقيت والتنبيه
        if primary_source_key == "archive":
            if not is_archive_url_valid(source_url):
                print(
                    f"   ❌ [Source] رابط Archive تالف ومحذوف! جاري التبديل للبديل التالي..."
                )

                # البحث عن أول بديل متاح في القائمة ليس archive
                fallback_key = next((k for k in sorted_sources if k != "archive"), None)
                if fallback_key:
                    primary_source_key = fallback_key
                    source_url = available_sources[fallback_key]
                    print(
                        f"   ✅ [Source] تم التحويل تلقائياً للسورس البديل: [{primary_source_key}] → {source_url}"
                    )
                else:
                    print(
                        f"   ❌ [Source] رابط Archive ميت ولا توجد مصادر بديلة أخرى لهذه الحلقة."
                    )
                    continue

        # حصر التكرار التنفيذي على السورس السليم والنهائي المقبول
        active_sources = [primary_source_key]

        for source in active_sources:
            source_url = available_sources[source]
            file_code = None

            # === معالجة خاصة لـ Streamtape: رفع بالتدفق المباشر لتجاوز قفل الـ IP ===
            # === معالجة خاصة لـ Streamtape: تحميل وسيط مؤقت لمنع تجمد السيرفر ===
            if source == "streamtape":
                print("🕵️ جاري استخراج رابط Streamtape المباشر عبر Playwright...")
                resolved_url = asyncio.run(_resolve_streamtape(None, source_url))
                if not resolved_url:
                    print("❌ فشل استخراج رابط Streamtape المباشر.")
                    continue
                
                temp_file = f"temp_{ep_id}.mp4"
                try:
                    print("📥 جاري سحب الفيلم من Streamtape إلى السيرفر المحلى مؤقتاً...")
                    with requests.get(resolved_url, stream=True, timeout=60) as r:
                        r.raise_for_status()
                        total_size = int(r.headers.get('content-length', 0))
                        downloaded = 0
                        with open(temp_file, 'wb') as f:
                            for chunk in r.iter_content(chunk_size=1024 * 1024): # القراءة بحجم 1 ميجا
                                if chunk:
                                    f.write(chunk)
                                    downloaded += len(chunk)
                                    if total_size > 0 and downloaded % (1024 * 1024 * 100) < len(chunk): # طباعة تقرير كل 20 ميجا
                                        print(f"   ⏳ تقدم السحب المحلى: {downloaded // (1024*1024)}MB / {total_size // (1024*1024)}MB")
                    
                    print("📡 جاري جلب سيرفر الرفع النشط من لولو...")
                    srv_res = requests.get(f"https://www.lulustream.com/api/upload/server?key={LULU_API_KEY}").json()
                    
                    if srv_res.get("status") == 200:
                        upload_server_url = srv_res["result"]
                        print(f"🚀 بدء الرفع الفعلي الآن بالـ Content-Length الصحيح لـ لولو: {upload_server_url}")
                        
                        with open(temp_file, 'rb') as f:
                            files = {'file': ('video.mp4', f, 'video/mp4')}
                            response = requests.post(upload_server_url, data={'key': LULU_API_KEY}, files=files, timeout=1200)
                            
                            print(f"📡 رد سيرفر لولو الخام (Debug): {response.text}")
                            up_res = response.json()
                            if up_res.get("status") == 200 and up_res.get("files"):
                                file_code = up_res["files"][0]["filecode"]
                                print(f"✅ تم الرفع بنجاح! كود الملف المحجوز: {file_code}")
                            else:
                                error_msg = up_res.get('msg') or "رد غير متوقع من السيرفر"
                                print(f"❌ سيرفر لولو رفض استقبال الملف المرفوع: {error_msg}")
                    else:
                        print(f"❌ فشل الاتصال بـ API سيرفرات لولو: {srv_res}")

                except Exception as e:
                    print(f"❌ خطأ كارثي أثناء عملية النقل لـ Streamtape: {e}")
                finally:
                    if os.path.exists(temp_file):
                        os.remove(temp_file)
                        print("🗑️ تم تنظيف وحذف الملف المؤقت بنجاح.")
                
                if not file_code:
                    print("⏭️ فشل التعامل مع Streamtape، الانتقال للمصدر التالي...")
                    continue

            # === المعالجة العادية لبقية السيرفرات (Archive / Telegram) عبر الـ Remote URL ===
            else:
                final_source_url = source_url
                for attempt in range(1, 4):
                    print(
                        f"📡 محاولة [{attempt}/3] باستخدام المصدر الموثوق: [{source}]..."
                    )
                    try:
                        print(f"🔗 الرابط المستخدم (ثابت): {final_source_url}")
                        is_link_alive = False
                        headers = {"Range": "bytes=0-100"}

                        for wake_up in range(1, 4):
                            print(
                                f"📡 محاولة تنبيه الستريمر [{wake_up}/3] لـ ID: {ep_id}..."
                            )
                            try:
                                check_res = requests.get(
                                    final_source_url, headers=headers, timeout=15
                                )
                                if check_res.status_code in [200, 206]:
                                    print(
                                        f"✅ الستريمر رد بـ {check_res.status_code}.. الرابط شغال!"
                                    )
                                    is_link_alive = True
                                    break
                            except Exception as e:
                                print(f"⚠️ خطأ أثناء التنبيه: {str(e)}")
                            time.sleep(3)

                        print(
                            f"🔗 إرسال الرابط لـ لولو (محاولة {attempt}/3): {final_source_url}"
                        )
                        safe_source_url = quote(final_source_url, safe="")
                        headers_payload = "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36\r\nReferer: https://lulustream.com/\r\nOrigin: https://lulustream.com"
                        custom_header = quote(headers_payload)

                        api_url = f"https://www.lulustream.com/api/upload/url?key={LULU_API_KEY}&url={safe_source_url}&headers={custom_header}"
                        res_data = requests.get(api_url, timeout=30).json()

                        if res_data.get("status") == 200:
                            file_code = res_data["result"]["filecode"]
                            break
                    except Exception as e:
                        print(f"❌ خطأ تقني: {str(e)}")
                    if attempt < 3:
                        time.sleep(60)

                if not file_code:
                    print(f"⏭️ فشل {source} تماماً، الانتقال للمصدر التالي إن وجد...")
                    continue

            # يكمل السكريبت طبيعياً هنا للدخول في نظام الـ Hunter Mode الموحد لجميع الحالات
            if file_code:
                print(f"✅ تم حجز الكود {file_code}.. جاري التأكد (Hunter)...")

                is_verified = False
                # نرفع عدد الفحصات لـ 100 محاولة
                for check_attempt in range(1, 101):
                    time.sleep(10 if check_attempt <= 5 else 45)
                    print(f"⏳ فحص حالة السحب ({check_attempt}/100)...")

                    info_url = f"https://www.lulustream.com/api/file/info?key={LULU_API_KEY}&file_code={file_code}"
                    try:
                        info_res = requests.get(info_url, timeout=20).json()
                        if info_res.get("status") == 200 and info_res.get("result"):
                            file_info = info_res["result"][0]
                            status_text = str(file_info.get("status", "")).lower()
                            can_play = file_info.get("canplay")

                            if can_play == 1:
                                print(f"✅ تم التأكيد بنجاح! الملف جاهز للعرض.")

                                # --- تثبيت الاسم من السكريبت الأساسي ---
                                try:
                                    target_title = ep.get("medias", {}).get(
                                        "title",
                                        f"Episode {ep.get('episode_number')}",
                                    )
                                    edit_url = f"https://www.lulustream.com/api/file/edit?key={LULU_API_KEY}&file_code={file_code}&file_title={quote(target_title)}"
                                    requests.get(edit_url, timeout=10)
                                    print(f"✨ تم تثبيت الاسم النظيف: {target_title}")
                                except:
                                    print("⚠️ فشل تعديل الاسم ولكن الملف جاهز.")
                                # ------------------------------------------

                                is_verified = True
                                break

                            # إضافة حالة الـ Uploading
                            if can_play == 0:
                                if check_attempt > 10 and (
                                    not file_info.get("player_img")
                                    or "nothumb" in str(file_info.get("player_img"))
                                ):
                                    print(
                                        f"⚠️ مؤشرات Internal Problem (لا توجد لقطة للفيديو).. إلغاء المحاولة."
                                    )
                                    break

                                print(
                                    f"⏳ لولو استلم الملف ويقوم بالمعالجة حالياً (Status: {status_text})..."
                                )
                                continue

                            if status_text == "error" or status_text == "0":
                                print(
                                    f"❌ لولو أكد الفشل النهائي (Status: {status_text})."
                                )
                                break
                            print(f"📡 الحالة الحالية: {status_text}..")
                    except:
                        print("⚠️ خطأ في طلب الـ Info")
                # --- نهاية الـ Hunter Mode ---

                if is_verified:
                    supabase.table("links").upsert(
                        {
                            "episode_id": ep_id,
                            "server_name": TARGET_SERVER,
                            "url": f"https://luluvdo.com/e/{file_code}",
                        }
                    ).execute()
                    print(f"✨ تم الحفظ في سوبابيز بنجاح!")
                    is_rescued = True
                    count_success += 1
                else:
                    print(f"♻️ لولو فشل في معالجة الملف النهائي للمصدر [{source}].")

            if is_rescued:
                break  # اخرج من لوب السيرفرات (خلاص أنقذنا الحلقة)
            else:
                print(f"⏭️ فشل {source} تماماً، الانتقال للمصدر التالي إن وجد...")

        # --- السطر الأهم في نهاية اللوب لتهدئة الضغط ---
        print(f"⏳ انتظار 20 ثانية لتهدئة الضغط على سيرفرات لولو...")
        time.sleep(20)

    print(f"\n✨ المهمة انتهت! تم إنقاذ {count_success} مادة.")


if __name__ == "__main__":
    rescue_lulu_mission()
