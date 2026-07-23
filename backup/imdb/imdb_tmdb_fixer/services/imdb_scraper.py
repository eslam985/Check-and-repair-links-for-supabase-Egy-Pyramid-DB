import asyncio
import re
import json
from playwright.async_api import async_playwright
from logger import console
from models import ScrapedData
from services.translator import is_mostly_english, translate_text, translate_genres
from services.cloudinary_client import CloudinaryClient
from utils.helpers import parse_duration_to_iso, clean_duration_text

class IMDbScraper:
    def __init__(self):
        self.cloudinary = CloudinaryClient()

    async def fetch_media_data(self, search_query: str) -> ScrapedData:
        """الوظيفة الرئيسية لكشط IMDb، بنفس المنطق الأصلي"""
        async with async_playwright() as p:
            console.print(f"\n[yellow]🔍 جاري البحث عن: '{search_query}' في IMDb...[/yellow]")
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 720},
            )
            page = await context.new_page()

            # تحليل المدخلات
            orig_year_str = search_query.split()[-1] if search_query.split()[-1].isdigit() else ""
            orig_year = int(orig_year_str) if orig_year_str else None
            clean_title = search_query.split(orig_year_str)[0].strip() if orig_year_str else search_query.strip()

            # استخراج أول كلمتين (للفشل)
            words = clean_title.split()
            if words and words[0].lower() in ["the", "a", "an", "to"] and len(words) > 1:
                first_two = " ".join(words[1:3])
                first_one = words[1]
            else:
                first_two = " ".join(words[:2])
                first_one = words[0] if words else clean_title

            target_years = [orig_year, orig_year+1, orig_year-1] if orig_year else [None]

            chosen_link = None
            year = "غير متوفر"
            duration = "غير متوفر"
            rating = "غير متوفر"

            # دالة البحث الفرعية
            async def execute_search(query, allowed_years, strict_match=True):
                search_url = f"https://www.imdb.com/find/?q={query.replace(' ', '%20')}&s=tt&ref_=fn_mov"
                try:
                    await page.goto(search_url, wait_until="commit", timeout=8000)
                    await page.wait_for_selector("a.ipc-title-link-wrapper", timeout=4000)
                    links = page.locator("a.ipc-title-link-wrapper")
                    count = await links.count()
                    for i in range(min(count, 15)):
                        try:
                            link_el = links.nth(i)
                            if not await link_el.is_visible():
                                continue
                            link_text = await link_el.inner_text()
                            clean_link = link_text.split(".", 1)[-1].strip().lower() if "." in link_text else link_text.strip().lower()
                            # إزالة علامات الترقيم
                            for ch in [".", ":", "-", ",", "'", "’"]:
                                clean_link = clean_link.replace(ch, " ")
                            target_clean = clean_title.lower()
                            for ch in [".", ":", "-", ",", "'", "’"]:
                                target_clean = target_clean.replace(ch, " ")
                            clean_link = " ".join(clean_link.split())
                            target_clean = " ".join(target_clean.split())

                            if strict_match:
                                name_ok = (clean_link == target_clean) or (target_clean in clean_link)
                            else:
                                name_ok = True

                            parent = link_el.locator("xpath=ancestor::div[contains(@class, 'cli-children')]")
                            metadata = parent.locator(".cli-title-metadata ul li")
                            meta_count = await metadata.count()
                            result_year = "غير متوفر"
                            if meta_count > 0:
                                result_year = await metadata.nth(0).inner_text(timeout=1000)
                            year_ok = any(str(y) in result_year for y in allowed_years if y) if allowed_years != [None] else True

                            if name_ok and year_ok:
                                item_duration = "غير متوفر"
                                if meta_count > 1:
                                    item_duration = await metadata.nth(1).inner_text(timeout=1000)
                                item_rating = "غير متوفر"
                                try:
                                    rating_el = parent.locator(".ipc-rating-star--rating").first
                                    if await rating_el.is_visible():
                                        item_rating = await rating_el.inner_text(timeout=1000)
                                except:
                                    pass
                                return link_el, result_year, item_duration, item_rating
                        except:
                            continue
                    return None, "غير متوفر", "غير متوفر", "غير متوفر"
                except:
                    return None, "غير متوفر", "غير متوفر", "غير متوفر"

            # الفحص المباشر للمعرف (ID) أو تشغيل خوارزمية البحث
            imdb_id_match = re.search(r"tt\d+", search_query)
            
            if imdb_id_match:
                work_id = imdb_id_match.group(0)
                console.print(f"[cyan]🔗 تم رصد معرف مباشر ({work_id}). الانتقال لجلب البيانات...[/cyan]")
                full_url = f"https://www.imdb.com/title/{work_id}/"
                await page.goto(full_url, wait_until="domcontentloaded")
                await page.wait_for_selector("h1", timeout=8000)
                
                # استخراج السنة والمدة من الصفحة مباشرة نظراً لتخطي خوارزمية البحث
                try:
                    list_items = page.locator("ul.ipc-inline-list li")
                    items_count = await list_items.count()
                    for i in range(items_count):
                        text = (await list_items.nth(i).inner_text()).strip()
                        if re.match(r"^\d{4}$", text):
                            year = text
                        elif ("h" in text.lower() or "m" in text.lower()) and any(c.isdigit() for c in text):
                            duration = text
                except:
                    pass
            else:
                # محاولات البحث
                if orig_year:
                    chosen_link, year, duration, rating = await execute_search(search_query, [orig_year], True)
                if not chosen_link:
                    chosen_link, year, duration, rating = await execute_search(clean_title, target_years, True)
                if not chosen_link and orig_year:
                    chosen_link, year, duration, rating = await execute_search(f"{clean_title} {orig_year-1}", [orig_year-1], True)
                if not chosen_link and orig_year:
                    chosen_link, year, duration, rating = await execute_search(f"{clean_title} {orig_year+1}", [orig_year+1], True)
                if not chosen_link:
                    chosen_link, year, duration, rating = await execute_search(first_two, target_years, False)
                if not chosen_link:
                    chosen_link, year, duration, rating = await execute_search(first_one, target_years, False)

                if not chosen_link:
                    console.print("[red]❌ لم يتم العثور على العمل في IMDb[/red]")
                    await browser.close()
                    return None

                # استخراج المعرف من رابط البحث
                href = await chosen_link.get_attribute("href")
                work_id = href.split("/title/")[1].split("/")[0] if "/title/" in href else "غير متوفر"
                full_url = f"https://www.imdb.com/title/{work_id}/"
                await page.goto(full_url, wait_until="domcontentloaded")
                await page.wait_for_selector("h1", timeout=8000)

            # جلب البيانات العميقة
            # الصورة
            try:
                img_el = page.locator("img.ipc-image").first
                raw_url = await img_el.get_attribute("src")
                high_res = raw_url.split("._V1_")[0] + "._V1_FMjpg_UX1200_.jpg" if "._V1_" in raw_url else raw_url
                poster = self.cloudinary.upload_poster(high_res)
            except:
                poster = "غير متوفر"

            # القصة
            story = "غير متوفر"
            try:
                story_el = page.locator("[data-testid='plot'] [data-testid='plot-xl']").first
                story = await story_el.inner_text()
            except:
                try:
                    story_el = page.locator("[data-testid='plot-xs_to_m'] span").first
                    story = await story_el.inner_text()
                except:
                    pass

            # التصنيفات
            genres_list = []
            try:
                genre_els = page.locator("span.ipc-chip__text")
                count = await genre_els.count()
                for i in range(count):
                    txt = await genre_els.nth(i).inner_text()
                    if txt.strip() and "back to top" not in txt.lower():
                        genres_list.append(txt.strip())
            except:
                pass
            genres_ar = translate_genres(genres_list)

            # المدة
            duration_clean = clean_duration_text(duration)
            iso_duration = parse_duration_to_iso(duration)

            # التقييم (إن لم يكن موجوداً)
            if rating == "غير متوفر":
                try:
                    rating_el = page.locator("[data-testid='hero-rating-bar__aggregate-rating__score'] > span").first
                    if await rating_el.is_visible(timeout=2000):
                        rating = await rating_el.inner_text()
                except:
                    pass

            # ترجمة القصة إذا كانت إنجليزية
            if story != "غير متوفر" and is_mostly_english(story):
                story = translate_text(story)

            # استخراج الاسم الحقيقي (قد يكون مفيداً)
            real_title = None
            try:
                title_el = page.locator("h1 span[data-testid='hero__primary-text']").first
                real_title = await title_el.inner_text()
            except:
                pass

            await browser.close()

            # بناء النتيجة
            return ScrapedData(
                tmdb_id=work_id if work_id != "غير متوفر" else None,
                story=story,
                poster_url=poster,
                rating=float(rating) if rating not in ["غير متوفر", ""] else None,
                runtime=duration_clean if duration_clean != "غير متوفر" else None,
                duration_iso=iso_duration,
                labels=genres_ar if genres_ar != "غير متوفر" else None,
                year=int(year) if year and year.isdigit() else None,
                title=real_title,
                is_ready=True,
            )