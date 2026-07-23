import asyncio
import re
import os
import requests
from playwright.async_api import async_playwright
from rich.console import Console
from playwright_stealth import stealth_async
from deep_translator import GoogleTranslator

# قاموس ترجمة التصنيفات الذي أرسلته
genre_map = {
    "Action": "أكشن",
    "Adventure": "مغامرة",
    "Animation": "رسوم متحركة",
    "Comedy": "كوميديا",
    "Crime": "جريمة",
    "Documentary": "وثائقي",
    "Drama": "دراما",
    "Family": "عائلي",
    "Fantasy": "فانتازيا",
    "History": "تاريخ",
    "Horror": "رعب",
    "Music": "موسيقى",
    "Mystery": "غموض",
    "Romance": "رومانسي",
    "Science Fiction": "خيال علمي",
    "TV Movie": "فيلم تلفزيوني",
    "Thriller": "إثارة",
    "War": "حرب",
    "Western": "غرب أمريكي",
    "Sport": "رياضة",
    "Short": "قصير",
    "Sci-Fi": "خيال علمي",
    "Biography": "سيرة شخصية",
    "German": "ألماني",
    "French": "فرنسي",
    "Japanese": "ياباني",
    "Whodunnit": "من فعلها",
    "Superhero": "سوبرهيرو",
    "Cyberpunk": "سايبربانك",
    "Korean": "كوري",
    "Psychological Thriller": "إثارة نفسية",  # التعديل هنا
    "Portuguese": "برتغالي",
    "Gun Fu": "كونغ فو",
    "Martial Arts": "فنون قتالية",
    "Supernatural Horror": "رعب خارق للطبيعة",
    "Spy": "جاسوس",
    "Period Drama": "دراما تاريخية",
    "Folk Horror": "رعب شعبي",
    "Vampire Horror": "رعب مصاصي الدماء",
}


def fetch_incomplete_medias(limit=20):
    """الاستعلام عن الأعمال الناقصة أو التالفة في Supabase"""
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_ANON_KEY")

    if not supabase_url or not supabase_key:
        console.print(
            "[bold red]❌ خطأ: يرجى تعيين SUPABASE_URL و SUPABASE_KEY أولاً![/bold red]"
        )
        return []

    endpoint = f"{supabase_url}/rest/v1/medias"
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Range": "0-1000",  # زيادة النطاق ليفحص حتى أول 1000 سطر لضمان صيد السطور القديمة
    }

    # الفحص الصارم: جلب السطر إذا كانت القصة أو الصورة (Null أو نص فارغ أو قيمتها غير متوفر)
    params = {
        "or": "(story.is.null,story.eq.,story.eq.غير متوفر,poster_url.is.null,poster_url.eq.,labels.is.null)",
        "order": "created_at.desc",
    }

    try:
        response = requests.get(endpoint, headers=headers, params=params)
        if response.status_code != 200:
            return []

        raw_medias = response.json()
        filtered_medias = []

        for item in raw_medias:
            is_missing = not item.get("story") or not item.get("poster_url")
            is_just_movies_label = item.get("labels", "") == "أفلام"
            is_short_runtime = False
            runtime_str = item.get("runtime")

            if runtime_str and "ساعة" not in runtime_str and "دقيقة" in runtime_str:
                try:
                    minutes = int("".join(filter(str.isdigit, runtime_str)))
                    if minutes < 30:
                        is_short_runtime = True
                except:
                    pass

            if is_missing or is_just_movies_label or is_short_runtime:
                filtered_medias.append(item)
                if len(filtered_medias) >= limit:
                    break

        return filtered_medias
    except Exception as e:
        console.print(f"[bold red]❌ خطأ في فحص النواقص: {e}[/bold red]")
        return []


def update_media_data(row_id, updated_fields):
    """تحديث حقول عمل معين في Supabase بناءً على الـ ID الخاص به"""
    supabase_url = os.getenv("SUPABASE_URL")
    # التعديل هنا: توحيد اسم المتغير ليقرأ من المفتاح الصحيح المتاح لديك
    supabase_key = (
        os.getenv("SUPABASE_KEY")
        or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        or os.getenv("SUPABASE_ANON_KEY")
    )

    endpoint = f"{supabase_url}/rest/v1/medias"
    headers = {
        "apikey": supabase_key,
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    params = {"id": f"eq.{row_id}"}

    try:
        res = requests.patch(
            endpoint, headers=headers, params=params, json=updated_fields
        )
        if res.status_code in [200, 204]:
            console.print(
                f"[bold green]✅ تم تحديث بيانات العمل بنجاح في قاعدة البيانات (ID: {row_id}).[/bold green]"
            )
            return True
        else:
            console.print(
                f"[bold red]❌ فشل تحديث السطر {row_id}: {res.text}[/bold red]"
            )
            return False
    except Exception as e:
        console.print(f"[bold red]❌ خطأ أثناء تحديث البيانات: {e}[/bold red]")
        return False


console = Console()


# دالة رفع البوستر ومعالجته لكلاود ناري
def upload_poster_to_cloudinary(image_url):
    cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME")
    upload_preset = os.getenv("CLOUDINARY_UPLOAD_PRESET")

    if not cloud_name or not upload_preset:
        return image_url

    try:
        cloudinary_api = f"https://api.cloudinary.com/v1_1/{cloud_name}/image/upload"
        payload = {
            "file": image_url,
            "upload_preset": upload_preset,
            "folder": "blogger",
        }
        res = requests.post(cloudinary_api, data=payload).json()
        public_id = res.get("public_id")

        if public_id:
            transform = "c_fill,g_auto,w_300,h_450,q_auto:good,f_avif"
            return f"https://res.cloudinary.com/{cloud_name}/image/upload/{transform}/v1/{public_id}.avif"

        return image_url
    except Exception as e:
        console.print(f"[bold red]⚠️ خطأ في رفع الصورة لكلاود ناري: {e}[/bold red]")
        return image_url


# دالة ذكية لتحويل صيغ IMDb مثل (1h 33m) إلى دقائق ثم إلى ISO Duration
def parse_duration_to_iso(duration_str):
    if not duration_str or duration_str == "غير متوفر":
        return "PT01H30M"  # القيمة الافتراضية في حال الفشل

    hours = 0
    minutes = 0

    # استخراج الساعات والدقائق باستخدام الـ Regex
    hr_match = re.search(r"(\d+)\s*h", duration_str)
    min_match = re.search(r"(\d+)\s*m", duration_str)

    if hr_match:
        hours = int(hr_match.group(1))
    if min_match:
        minutes = int(min_match.group(1))

    # إذا لم يجد h ولا m ولكن وجد أرقام فقط، نعتبرها دقائق
    if not hr_match and not min_match:
        digits = re.findall(r"\d+", duration_str)
        if digits:
            minutes = int(digits[0])

    total_minutes = (hours * 60) + minutes
    if total_minutes == 0:
        return "PT01H30M"

    final_hours = total_minutes // 60
    final_mins = total_minutes % 60
    return f"PT{final_hours:02d}H{final_mins:02d}M"


# دالتك للتحقق من اللغة
def is_mostly_english(text):
    if not text:
        return True
    clean_text = re.sub(r"[^a-zA-Z\u0600-\u06FF]", "", str(text))
    if not clean_text:
        return True
    english_chars = len(re.findall(r"[a-zA-Z]", clean_text))
    arabic_chars = len(re.findall(r"[\u0600-\u06FF]", clean_text))
    return english_chars >= arabic_chars


async def get_full_imdb_data(search_query: str):
    async with async_playwright() as p:
        console.print(
            f"\n[bold yellow]🔍 جاري بدء الفحص الاستقصائي الذكي لـ: '{search_query}'...[/bold yellow]"
        )

        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--no-sandbox",
                "--disable-setuid-sandbox",
            ],
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            }
        )
        page = await context.new_page()
        
        # حقن سكريبتات التخفي في الصفحة قبل إجراء أي اتصال
        await stealth_async(page)

        # 1. تحليل وتفكيك ميتاداتا عنوان البحث
        orig_year_str = (
            search_query.split()[-1] if search_query.split()[-1].isdigit() else ""
        )
        orig_year = int(orig_year_str) if orig_year_str else None

        clean_movie_title = (
            search_query.split(orig_year_str)[0].strip()
            if orig_year_str
            else search_query.strip()
        )
        # استخراج الكلمة الأولى وأول كلمتين مع تخطي أدوات التعريف
        title_words = clean_movie_title.split()
        if (
            title_words
            and title_words[0].lower() in ["the", "a", "an", "to"]
            and len(title_words) > 1
        ):
            first_word = title_words[1]
            first_two_words = " ".join(title_words[1:3])
        else:
            first_word = title_words[0] if title_words else clean_movie_title
            first_two_words = (
                " ".join(title_words[0:2]) if title_words else clean_movie_title
            )

        target_years = (
            [orig_year, orig_year + 1, orig_year - 1] if orig_year else [None]
        )

        chosen_link = None
        year, duration, rating = "غير متوفر", "غير متوفر", "غير متوفر"

        # 2. بناء الدالة الفرعية المحدثة كلياً بناءً على كلاسات صفحة البحث المكتشفة
        async def execute_search_and_filter(
            query_text, allowed_years_list, strict_name_match=True
        ):
            # البحث في جميع أقسام العناوين (أفلام ومسلسلات) بناءً على طلبك
            search_url = f"https://www.imdb.com/find/?q={query_text.replace(' ', '%20')}&s=tt&ref_=fn_mov"
            # search_url = f"https://www.imdb.com/find/?q={query_text.replace(' ', '%20')}&ref_=hm_nv_srb_sm" #fn_tv fn_mov fn_all fn_ttl
            # https://www.imdb.com/find/?q=My%20Dearest%20Assassin%202026&ref_=hm_nv_srb_sm
            try:
                await page.goto(search_url, wait_until="commit", timeout=8000)
                await page.wait_for_selector("a.ipc-title-link-wrapper", timeout=4000)
                result_links = page.locator("a.ipc-title-link-wrapper")
                links_count = await result_links.count()

                for i in range(min(links_count, 15)):
                    try:
                        link_el = result_links.nth(i)
                        if not await link_el.is_visible():
                            continue

                        link_text = await link_el.inner_text()
                        clean_link_text = (
                            link_text.split(".", 1)[-1].strip().lower()
                            if "." in link_text
                            else link_text.strip().lower()
                        )
                        # تنظيف ذكي لكلا الاسمين من علامات الترقيم (النقاط، الفواصل، النقطتين الرأسيتين) لضمان التطابق
                        for char in [".", ":", "-", ",", "'", "’"]:
                            clean_link_text = clean_link_text.replace(char, " ")

                        target_title_clean = clean_movie_title.lower()
                        for char in [".", ":", "-", ",", "'", "’"]:
                            target_title_clean = target_title_clean.replace(char, " ")

                        # إزالة المسافات الزائدة الناتجة عن التنظيف
                        clean_link_text = " ".join(clean_link_text.split())
                        target_title_clean = " ".join(target_title_clean.split())

                        # شرط الاسم: تطابق صريح للنص المنظف، أو احتواء ذكي في حالة عدم التشدد
                        if strict_name_match:
                            name_matches = (
                                clean_link_text == target_title_clean
                                or target_title_clean in clean_link_text
                            )
                        else:
                            name_matches = True

                        parent_container = link_el.locator(
                            "xpath=ancestor::div[contains(@class, 'cli-children')]"
                        )
                        metadata_items = parent_container.locator(
                            ".cli-title-metadata ul li"
                        )

                        # تم إزالة فحص وحظر المسلسلات للسماح بجلب الأعمال المصنفة كمسلسلات بالخطأ
                        metadata_count = await metadata_items.count()

                        result_year = "غير متوفر"
                        if metadata_count > 0:
                            result_year = await metadata_items.nth(0).inner_text(
                                timeout=1000
                            )

                        year_matches = (
                            any(str(y) in result_year for y in allowed_years_list if y)
                            if allowed_years_list != [None]
                            else True
                        )

                        if name_matches and year_matches:
                            item_duration = "غير متوفر"
                            if await metadata_items.count() > 1:
                                item_duration = await metadata_items.nth(1).inner_text(
                                    timeout=1000
                                )

                            item_rating = "غير متوفر"
                            try:
                                rating_el = parent_container.locator(
                                    ".ipc-rating-star--rating"
                                ).first
                                if await rating_el.is_visible():
                                    item_rating = await rating_el.inner_text(
                                        timeout=1000
                                    )
                            except:
                                pass

                            return link_el, result_year, item_duration, item_rating
                    except:
                        continue
                return None, "غير متوفر", "غير متوفر", "غير متوفر"
            except:
                return None, "غير متوفر", "غير متوفر", "غير متوفر"

        # --------------------------------------------------------------------------
        # 3. الفحص المباشر للمعرف (ID) أو تشغيل خوارزمية التعاقب
        # --------------------------------------------------------------------------
        imdb_id_match = re.search(r"tt\d+", search_query)
        real_title = None
        duration = "غير متوفر"
        rating = "غير متوفر"

        if imdb_id_match:
            work_id = imdb_id_match.group(0)
            console.print(
                f"[bold cyan]🔗 تم رصد معرف مباشر ({work_id}). الانتقال لجلب البيانات العميقة...[/bold cyan]"
            )
            full_movie_url = f"https://www.imdb.com/title/{work_id}/"
            console.print(
                f"[bold magenta]🔗 جاري فتح الرابط: {full_movie_url}[/bold magenta]"
            )

            await page.goto(full_movie_url, wait_until="domcontentloaded")

            # --- اختبار الحظر (debugging) ---
            page_title = await page.title()
            console.print(
                f"[bold yellow]👀 عنوان الصفحة المقروء (title): {page_title}[/bold yellow]"
            )

            # التقاط صورة وحفظها في بيئة كولاب
            await page.screenshot(path="debug_imdb.png")
            console.print(
                "[bold yellow]📸 تم حفظ صورة للصفحة باسم 'debug_imdb.png'، افتحها من ملفات كولاب لترى ما حدث.[/bold yellow]"
            )
            # --------------------------------

            try:
                await page.wait_for_selector("h1", timeout=8000)
            except Exception as e:
                console.print(
                    f"[bold red]❌ المتصفح لم يجد وسم h1 (تم حظر الطلب غالباً): {e}[/bold red]"
                )

            # 1. استخراج الاسم الحقيقي
            try:
                real_title_el = page.locator(
                    "h1 span[data-testid='hero__primary-text']"
                ).first
                real_title = await real_title_el.inner_text()
            except:
                pass

            # 2. استخراج الميتاداتا (السنة والمدة) بدقة من القائمة العلوية
            # 2. استخراج الميتاداتا (السنة، المدة، والتقييم) بالاعتماد على العناصر البديلة
            try:
                # المسار الأول: القراءة من القائمة العلوية مباشرة ul.ipc-inline-list
                list_items = page.locator("ul.ipc-inline-list li")
                items_count = await list_items.count()
                for i in range(items_count):
                    text = (await list_items.nth(i).inner_text()).strip()
                    if re.match(r"^\d{4}$", text):
                        year = text
                    elif ("h" in text.lower() or "m" in text.lower()) and any(
                        c.isdigit() for c in text
                    ):
                        duration = text
            except:
                pass

            try:
                # المسار الثاني: القراءة من علامات الـ Meta (مستقرة جداً وتتحمل التغييرات)
                meta_title = await page.locator(
                    "meta[property='og:title']"
                ).get_attribute("content")
                if meta_title and (not year or year == "غير متوفر"):
                    year_match = re.search(r"\((\d{4})\)", meta_title)
                    if year_match:
                        year = year_match.group(1)

                meta_desc = await page.locator(
                    "meta[property='og:description']"
                ).get_attribute("content")
                if meta_desc and (not duration or duration == "غير متوفر"):
                    duration = meta_desc.split("|")[0].strip()  # يستخرج مثل "1h 40m"
            except:
                pass

            try:
                # المسار الثالث: تفكيك مصفوفة الـ JSON-LD (صمام الأمان المطلق لقراءة الاسكريبت الداخلي)
                import json

                json_element = page.locator("script[type='application/ld+json']").first
                if await json_element.is_visible():
                    json_data = json.loads(await json_element.inner_text())
                    if (
                        not year or year == "غير متوفر"
                    ) and "datePublished" in json_data:
                        year = json_data["datePublished"].split("-")[0]
                    if (
                        not duration or duration == "غير متوفر"
                    ) and "duration" in json_data:
                        iso_raw = json_data["duration"]  # القيمة مثل PT1H40M
                        h_match = re.search(r"(\d+)H", iso_raw)
                        m_match = re.search(r"(\d+)M", iso_raw)
                        h_part = f"{h_match.group(1)}h" if h_match else ""
                        m_part = f"{m_match.group(1)}m" if m_match else ""
                        duration = f"{h_part} {m_part}".strip()
                    if not rating or rating == "غير متوفر":
                        rating = str(
                            json_data.get("aggregateRating", {}).get(
                                "ratingValue", "غير متوفر"
                            )
                        )
            except:
                pass
            try:
                # المسار الرابع: سحب التقييم من واجهة المستخدم مباشرة كخط دفاع أخير
                if not rating or rating == "غير متوفر":
                    rating_el = page.locator(
                        "[data-testid='hero-rating-bar__aggregate-rating__score'] > span"
                    ).first
                    if await rating_el.is_visible(timeout=2000):
                        rating = await rating_el.inner_text()
            except:
                pass
        else:
            if orig_year:
                console.print(
                    f"[bold cyan]🔍 [المحاولة 1]: الفحص بالاسم الكامل والسنة الأصلية ({orig_year_str})...[/bold cyan]"
                )
                chosen_link, year, duration, rating = await execute_search_and_filter(
                    search_query, [orig_year], strict_name_match=True
                )

            if not chosen_link:
                console.print(
                    f"[bold cyan]🔄 [المحاولة 4]: الفحص بالاسم الكامل فقط وتصفية السنوات الثلاث المسموحة...[/bold cyan]"
                )
                chosen_link, year, duration, rating = await execute_search_and_filter(
                    clean_movie_title, target_years, strict_name_match=True
                )

            if not chosen_link and orig_year:
                console.print(
                    f"[bold cyan]🔄 [المحاولة 3]: تجربة الاسم الكامل مع سنة (-1): {orig_year - 1}...[/bold cyan]"
                )
                chosen_link, year, duration, rating = await execute_search_and_filter(
                    f"{clean_movie_title} {orig_year - 1}",
                    [orig_year - 1],
                    strict_name_match=True,
                )

            if not chosen_link and orig_year:
                console.print(
                    f"[bold cyan]🔄 [المحاولة 2]: تجربة الاسم الكامل مع سنة (+1): {orig_year + 1}...[/bold cyan]"
                )
                chosen_link, year, duration, rating = await execute_search_and_filter(
                    f"{clean_movie_title} {orig_year + 1}",
                    [orig_year + 1],
                    strict_name_match=True,
                )

            if not chosen_link:
                console.print(
                    f"[bold yellow]⚠️ [المحاولة 5]: الفحص بأول كلمتين '{first_two_words}' وتصفية السنوات...[/bold yellow]"
                )
                chosen_link, year, duration, rating = await execute_search_and_filter(
                    first_two_words, target_years, strict_name_match=False
                )

            if not chosen_link:
                console.print(
                    f"[bold yellow]⚠️ [المحاولة 6]: الملاذ الأخير. الفحص بالكلمة الأولى '{first_word}' وتصفية السنوات...[/bold yellow]"
                )
                chosen_link, year, duration, rating = await execute_search_and_filter(
                    first_word, target_years, strict_name_match=False
                )

            if not chosen_link:
                console.print(
                    f"[bold red]❌ خروج هندسي: استنفذت الخوارزمية كافة الحلول ولم تعثر على العمل بسلام.[/bold red]"
                )
                await browser.close()
                return {}

            # 4. استخراج المعرف الفريد للفيلم للانتقال الداخلي وجلب القصة والبوستر المفقودين
            href = await chosen_link.get_attribute("href")
            work_id = (
                href.split("/title/")[1].split("/")[0]
                if "/title/" in href
                else "غير متوفر"
            )

            full_movie_url = f"https://www.imdb.com/title/{work_id}/"
            await page.goto(full_movie_url, wait_until="domcontentloaded")
            await page.wait_for_selector("h1", timeout=8000)

        # 5. استخراج البوستر والقصة والتصنيفات من الداخل (محاذاة مستقيمة بـ 8 مسافات)
        try:
            # إضافة مهلة انتظار قصيرة وتحديث المحددات لتكون أكثر دقة وشمولية
            image_element = page.locator(
                "[data-testid='hero-media__poster'] img, .ipc-poster img.ipc-image, img.ipc-image"
            ).first
            await image_element.wait_for(state="visible", timeout=3000)
            raw_image_url = await image_element.get_attribute("src")
            high_res_url = (
                raw_image_url.split("._V1_")[0] + "._V1_FMjpg_UX1200_.jpg"
                if "._V1_" in raw_image_url
                else raw_image_url
            )
            image_url = upload_poster_to_cloudinary(high_res_url)
        except:
            image_url = "غير متوفر"

        try:
            # تحديث محددات القصة لتشمل بنية imdb الجديدة
            story_element = page.locator(
                "[data-testid='plot'] .ipc-html-content-inner-div, span[data-testid='plot-xl'], span[data-testid='plot-l'], span[data-testid='plot-xs_to_m']"
            ).first
            await story_element.wait_for(state="attached", timeout=3000)
            story = await story_element.inner_text()
        except:
            story = "غير متوفر"

        try:
            genres_elements = page.locator("span.ipc-chip__text")
            genres_count = await genres_elements.count()
            genres_list = [
                await genres_elements.nth(i).inner_text() for i in range(genres_count)
            ]
            # تنظيف التصنيفات وتحديد أول 5 فقط
            clean_genres = [
                g.strip()
                for g in genres_list
                if len(g) > 1 and "back to top" not in g.lower()
            ][:5]

            if clean_genres:
                # دمج التصنيفات الإنجليزية في نص واحد لترجمتها بطلب واحد فقط لسرعة الأداء
                english_genres_str = ", ".join(clean_genres)
                try:
                    translated_str = GoogleTranslator(
                        source="en", target="ar"
                    ).translate(english_genres_str)
                    # توحيد الفواصل لتكون فاصلة إنجليزية عادية لسهولة المعالجة في الفرونت إند
                    genres = translated_str.replace("،", ",").replace(",,", ",").strip()
                except Exception as translate_error:
                    console.print(
                        f"[bold yellow]⚠️ فشل الترجمة الديناميكية للتصنيفات، استخدام القاموس كبديل: {translate_error}[/bold yellow]"
                    )
                    # خط دفاع بديل: استخدام القاموس القديم إذا تعطلت شبكة جوجل
                    translated_genres = [genre_map.get(g, g) for g in clean_genres]
                    genres = ", ".join(translated_genres)
            else:
                genres = "غير متوفر"
        except Exception as e:
            console.print(f"[bold red]❌ خطأ أثناء معالجة التصنيفات: {e}[/bold red]")
            genres = "غير متوفر"

        iso_duration = parse_duration_to_iso(duration)

        # تنظيف المدة لتجنب التقاط حرف m من كلمة min
        clean_duration = "غير متوفر"
        if duration != "غير متوفر":
            h_match = re.search(r"(\d+)\s*h", duration.lower())
            m_match = re.search(r"(\d+)\s*m\b", duration.lower())

            parts = []
            if h_match:
                parts.append(f"{h_match.group(1)}h")
            if m_match:
                parts.append(f"{m_match.group(1)}m")
            clean_duration = " ".join(parts) if parts else duration

        translated_duration = (
            clean_duration.replace("h", " ساعة").replace("m", " دقيقة")
            if clean_duration != "غير متوفر"
            else "غير متوفر"
        )

        if story != "غير متوفر" and is_mostly_english(story):
            try:
                story = GoogleTranslator(source="en", target="ar").translate(story)
            except:
                pass

        await browser.close()

        result_data = {
            "tmdb_id": work_id if work_id != "غير متوفر" else None,
            "story": story,
            "poster_url": image_url,
            "rating": rating if rating != "غير متوفر" else None,
            "runtime": (
                translated_duration if translated_duration != "غير متوفر" else None
            ),
            "duration_iso": iso_duration,
            "labels": genres if genres != "غير متوفر" else None,
            "year": (
                int(year)
                if year and year != "غير متوفر" and str(year).isdigit()
                else None
            ),
            "is_ready": True,
        }

        # إذا تم كشط الاسم الحقيقي بنجاح (بسبب إدخال ID مباشر)، أضفه للتحديث ليمسح الـ ID من قاعدة البيانات
        if real_title:
            result_data["title"] = real_title

        return result_data


async def main_automation_engine():
    # 1. جلب الأعمال الناقصة أو المعطوبة (بحد أقصى 10 أعمال لكل دورة تشغيل)

    target_medias = fetch_incomplete_medias(limit=1)

    if not target_medias:
        console.print(
            "[bold green]✨ المنظومة مستقرة: لا توجد أعمال ناقصة أو تالفة تحتاج إلى صيانة حالياً.[/bold green]"
        )
        return

    console.print(
        f"[bold yellow]🚀 تم رصد {len(target_medias)} عمل تالف أو ناقص. جاري بدء أعمال الصيانة التلقائية...[/bold yellow]\n"
    )

    for media in target_medias:
        row_id = media.get("id")
        title = media.get("title")
        year = media.get("year", "")

        # منع تكرار السنة في البحث إذا كان العنوان يحتوي عليها بالفعل
        if year and str(year) in str(title):
            search_query = title.strip()
        else:
            search_query = f"{title} {year}".strip()

        try:
            # 2. كشط البيانات وترجمتها ومعالجة صورتها
            fresh_data = await get_full_imdb_data(search_query)

            # --- التعديل هنا: طباعة البيانات المجلوبة للتصحيح (debugging) ---
            console.print(f"\n[bold cyan]🔍 نتيجة الكشط التفصيلية من imdb:[/bold cyan]")
            if fresh_data:
                console.print(f"  - المعرف (tmdb_id): {fresh_data.get('tmdb_id')}")
                console.print(f"  - القصة (story): {fresh_data.get('story')}")
                console.print(f"  - البوستر (poster): {fresh_data.get('poster_url')}")
                console.print(f"  - التقييم (rating): {fresh_data.get('rating')}")
                console.print(f"  - المدة (runtime): {fresh_data.get('runtime')}")
                console.print(f"  - السنة (year): {fresh_data.get('year')}")
                console.print(f"  - التصنيفات (labels): {fresh_data.get('labels')}")
            else:
                console.print(
                    "[bold red]  - لا توجد بيانات (الدالة أعادت قاموساً فارغاً).[/bold red]"
                )
            console.print("-" * 50)
            # ----------------------------------------------------

            # شرط صارم معدل: التأكد من جلب القصة أو البوستر، ومعرف العمل (tmdb_id)
            if (
                fresh_data
                and (
                    fresh_data.get("story") != "غير متوفر"
                    or fresh_data.get("poster_url") != "غير متوفر"
                )
                and fresh_data.get("tmdb_id") is not None
            ):
                # 1. تحديد الاسم الأساسي للعمل
                base_title = fresh_data.get("title", title)

                # 2. بناء العنوان النهائي مع السنة (لقاعدة البيانات)
                final_year = fresh_data.get("year") or year
                if final_year and str(final_year) not in base_title:
                    final_title = f"{base_title} {final_year}"
                else:
                    final_title = base_title

                # حفظ العنوان بالسنة في قاعدة البيانات
                fresh_data["title"] = final_title

                # 3. بناء الـ Slug (بدون سنة تماماً)
                # سنقوم بحذف أي 4 أرقام متتالية (تمثل السنة) من الاسم المخصص للـ Slug
                slug_title = re.sub(r"\b\d{4}\b", "", base_title).strip()

                # معالجة الاسم المتبقي لتحويله إلى Slug نظيف
                clean_title = slug_title.lower()
                clean_title = re.sub(
                    r"[^\w\s-]", "", clean_title
                )  # إزالة الرموز وعلامات الترقيم
                clean_title = re.sub(r"[-\s]+", "-", clean_title).strip(
                    "-"
                )  # استبدال الفراغات بـ -

                # دمج الـ ID في بداية الـ Slug الخالي من السنة
                fresh_data["slug"] = f"{row_id}-{clean_title}"

                # 4. إرسال البيانات المستخرجة والنقية إلى دالة التحديث
                update_media_data(row_id, fresh_data)
            else:
                console.print(
                    f"[bold red]⚠️ تم إلغاء تحديث العمل '{title}': البيانات المجلوبة تالفة أو غير مطابقة لـ IMDb.[/bold red]"
                )

        except Exception as err:
            console.print(
                f"[bold red]💥 خطأ أثناء معالجة العمل {title}: {err}[/bold red]"
            )

        # 4. فاصل أمان زمني (5 ثوانٍ) لمنع الحظر وحماية السيرفرات
        await asyncio.sleep(5)


# هذا التعديل سيجعل السكريبت قابلاً للتشغيل من الطرفية (Terminal) ومن داخل كولاب باستخدام الأمر !python دون أي أخطاء برمجية.
# تشغيل منظومة الأتمتة المباشرة باستخدام حلقة الأحداث القياسية
if __name__ == "__main__":
    import asyncio

    asyncio.run(main_automation_engine())
