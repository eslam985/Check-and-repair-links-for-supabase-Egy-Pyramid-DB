"""
services/imdb.py — كشط بيانات الأعمال من IMDB عبر Playwright
يُستخدم عندما لا يوجد رابط TMDB مباشر
"""
import re
import json
import asyncio
from playwright.async_api import async_playwright, Page, Browser
from rich.console import Console

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from config import (
    IMDB_SEARCH_URL,
    IMDB_TITLE_URL,
    BROWSER_USER_AGENT,
    BROWSER_VIEWPORT,
)
from constants import UNAVAILABLE, SKIP_WORDS
from utils import (
    translate_to_arabic,
    translate_genres,
    is_mostly_english,
    parse_duration_to_iso,
    format_duration_arabic,
)
from services.cloudinary import upload_poster

console = Console()


# ─── نقطة الدخول ─────────────────────────────────────────────────────────────

async def fetch_by_imdb_id(imdb_id: str) -> dict:
    """يجلب بيانات عمل بمعرف IMDB مباشر (tt...)."""
    async with async_playwright() as p:
        browser, page = await _launch_browser(p)
        try:
            url = IMDB_TITLE_URL.format(imdb_id=imdb_id)
            console.print(f"[cyan]🔗 انتقال مباشر للـ ID: {imdb_id}[/cyan]")
            await page.goto(url, wait_until="domcontentloaded")
            await page.wait_for_selector("h1", timeout=8000)

            meta = await _extract_metadata_from_title_page(page)
            details = await _extract_title_page_details(page, meta["duration"])
            return _build_result(imdb_id, meta, details)
        finally:
            await browser.close()


async def search_and_fetch(search_query: str) -> dict:
    """يبحث عن عمل بالاسم ويجلب بياناته من IMDB."""
    async with async_playwright() as p:
        browser, page = await _launch_browser(p)
        try:
            console.print(f"\n[bold yellow]🔍 بدء البحث الذكي عن: '{search_query}'...[/bold yellow]")

            query_info = _parse_query(search_query)
            chosen, year, duration, rating = await _run_search_cascade(page, query_info)

            if not chosen:
                console.print("[bold red]❌ استنفذت كل محاولات البحث دون نتيجة.[/bold red]")
                return {}

            href = await chosen.get_attribute("href")
            work_id = href.split("/title/")[1].split("/")[0] if "/title/" in href else None
            if not work_id:
                return {}

            url = IMDB_TITLE_URL.format(imdb_id=work_id)
            await page.goto(url, wait_until="domcontentloaded")
            await page.wait_for_selector("h1", timeout=8000)

            meta = {"year": year, "duration": duration, "rating": rating}
            details = await _extract_title_page_details(page, duration)
            return _build_result(work_id, meta, details)
        finally:
            await browser.close()


# ─── الكشط الداخلي ───────────────────────────────────────────────────────────

async def _launch_browser(playwright) -> tuple:
    browser = await playwright.chromium.launch(headless=True)
    context = await browser.new_context(
        user_agent=BROWSER_USER_AGENT,
        viewport=BROWSER_VIEWPORT,
    )
    page = await context.new_page()
        
    # حقن سكريبتات التخفي برمجياً لتجاوز حماية IMDB
    await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    await page.add_init_script("Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]})")
    await page.add_init_script("Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']})")
    await page.add_init_script("window.chrome = { runtime: {} };")
    return browser, page


async def _extract_metadata_from_title_page(page: Page) -> dict:
    """يستخرج السنة والمدة والتقييم من صفحة العمل بثلاثة مسارات بديلة."""
    year, duration, rating = UNAVAILABLE, UNAVAILABLE, UNAVAILABLE

    # المسار 1: قائمة ul.ipc-inline-list
    try:
        items = page.locator("ul.ipc-inline-list li")
        count = await items.count()
        for i in range(count):
            text = (await items.nth(i).inner_text()).strip()
            if re.match(r"^\d{4}$", text):
                year = text
            elif ("h" in text.lower() or "m" in text.lower()) and any(c.isdigit() for c in text):
                duration = text
    except Exception:
        pass

    # المسار 2: meta tags (og:title, og:description)
    try:
        if year == UNAVAILABLE:
            meta_title = await page.locator("meta[property='og:title']").get_attribute("content")
            yr = re.search(r"\((\d{4})\)", meta_title or "")
            if yr:
                year = yr.group(1)
        if duration == UNAVAILABLE:
            meta_desc = await page.locator("meta[property='og:description']").get_attribute("content")
            if meta_desc:
                duration = meta_desc.split("|")[0].strip()
    except Exception:
        pass

    # المسار 3: JSON-LD
    try:
        json_el = page.locator("script[type='application/ld+json']").first
        if await json_el.is_visible():
            jd = json.loads(await json_el.inner_text())
            if year == UNAVAILABLE and "datePublished" in jd:
                year = jd["datePublished"].split("-")[0]
            if duration == UNAVAILABLE and "duration" in jd:
                iso = jd["duration"]
                h = re.search(r"(\d+)H", iso)
                m = re.search(r"(\d+)M", iso)
                duration = f"{h.group(1)}h {m.group(1)}m" if h and m else (
                    f"{h.group(1)}h" if h else f"{m.group(1)}m" if m else UNAVAILABLE
                )
            if rating == UNAVAILABLE:
                rating = str(jd.get("aggregateRating", {}).get("ratingValue", UNAVAILABLE))
    except Exception:
        pass

    # المسار 4: عنصر التقييم في الواجهة
    try:
        if rating == UNAVAILABLE:
            rating_el = page.locator(
                "[data-testid='hero-rating-bar__aggregate-rating__score'] > span"
            ).first
            if await rating_el.is_visible(timeout=2000):
                rating = await rating_el.inner_text()
    except Exception:
        pass

    return {"year": year, "duration": duration, "rating": rating}


async def _extract_title_page_details(page: Page, duration: str) -> dict:
    """يستخرج الصورة والقصة والتصنيفات من صفحة العمل."""

    # الصورة
    try:
        img = page.locator("img.ipc-image").first
        raw_url = await img.get_attribute("src")
        high_res = (
            raw_url.split("._V1_")[0] + "._V1_FMjpg_UX1200_.jpg"
            if "._V1_" in raw_url
            else raw_url
        )
        image_url = upload_poster(high_res)
    except Exception:
        image_url = UNAVAILABLE

    # القصة
    story = UNAVAILABLE
    try:
        el = page.locator("[data-testid='plot'] [data-testid='plot-xl']").first
        story = await el.inner_text()
    except Exception:
        try:
            el = page.locator("[data-testid='plot-xs_to_m'] span").first
            story = await el.inner_text()
        except Exception:
            pass

    # التصنيفات
    genres = UNAVAILABLE
    try:
        els = page.locator("span.ipc-chip__text")
        count = await els.count()
        raw = [await els.nth(i).inner_text() for i in range(count)]
        clean = [g.strip() for g in raw if len(g) > 1 and "back to top" not in g.lower()][:5]
        genres = translate_genres(clean) if clean else UNAVAILABLE
    except Exception as e:
        console.print(f"[red]❌ خطأ في معالجة التصنيفات: {e}[/red]")

    # ترجمة القصة إذا كانت إنجليزية
    if story != UNAVAILABLE and is_mostly_english(story):
        story = translate_to_arabic(story)

    return {"image_url": image_url, "story": story, "genres": genres}


# ─── البحث بالاسم ─────────────────────────────────────────────────────────────

def _parse_query(query: str) -> dict:
    """يُحلل نص البحث ويستخرج الأجزاء الأساسية منه."""
    words = query.split()
    year_str = words[-1] if words and words[-1].isdigit() else ""
    year = int(year_str) if year_str else None
    title = query.split(year_str)[0].strip() if year_str else query.strip()
    title_words = title.split()

    if title_words and title_words[0].lower() in SKIP_WORDS and len(title_words) > 1:
        first = title_words[1]
        first_two = " ".join(title_words[1:3])
    else:
        first = title_words[0] if title_words else title
        first_two = " ".join(title_words[:2]) if title_words else title

    target_years = [year, year + 1, year - 1] if year else [None]

    return {
        "original": query,
        "title": title,
        "year": year,
        "year_str": year_str,
        "first_word": first,
        "first_two": first_two,
        "target_years": target_years,
    }


async def _run_search_cascade(page: Page, q: dict):
    """
    خوارزمية البحث التعاقبي:
    6 محاولات بمعايير متساهلة تدريجياً حتى العثور على العمل.
    """
    title = q["title"]
    year = q["year"]
    target = q["target_years"]

    steps = [
        (q["original"],   [year],   True,  f"الاسم الكامل + السنة ({year})"),
        (title,           target,   True,  "الاسم الكامل + السنوات الثلاث"),
        (f"{title} {year - 1}" if year else title, [year - 1] if year else [None], True,  f"الاسم + سنة (-1)"),
        (f"{title} {year + 1}" if year else title, [year + 1] if year else [None], True,  f"الاسم + سنة (+1)"),
        (q["first_two"],  target,   False, f"أول كلمتين '{q['first_two']}'"),
        (q["first_word"], target,   False, f"الكلمة الأولى '{q['first_word']}'"),
    ]

    for query_text, years, strict, label in steps:
        console.print(f"[cyan]🔄 [{label}]...[/cyan]")
        result = await _execute_search(page, query_text, years, q["title"], strict)
        if result[0]:
            return result

    return None, UNAVAILABLE, UNAVAILABLE, UNAVAILABLE


async def _execute_search(
    page: Page,
    query_text: str,
    allowed_years: list,
    target_title: str,
    strict: bool,
):
    """يُنفّذ بحثاً واحداً في IMDB ويُعيد أول نتيجة مطابقة."""
    url = IMDB_SEARCH_URL.format(query=query_text.replace(" ", "%20"))
    try:
        await page.goto(url, wait_until="commit", timeout=8000)
        await page.wait_for_selector("a.ipc-title-link-wrapper", timeout=4000)
    except Exception:
        return None, UNAVAILABLE, UNAVAILABLE, UNAVAILABLE

    links = page.locator("a.ipc-title-link-wrapper")
    count = await links.count()

    clean_target = _normalize_title(target_title)

    for i in range(min(count, 15)):
        try:
            link = links.nth(i)
            if not await link.is_visible():
                continue

            raw_text = await link.inner_text()
            link_text = _normalize_title(raw_text.split(".", 1)[-1].strip() if "." in raw_text else raw_text.strip())

            name_ok = (
                (link_text == clean_target or clean_target in link_text)
                if strict
                else True
            )
            if not name_ok:
                continue

            parent = link.locator("xpath=ancestor::div[contains(@class, 'cli-children')]")
            meta = parent.locator(".cli-title-metadata ul li")
            meta_count = await meta.count()

            result_year = await meta.nth(0).inner_text(timeout=1000) if meta_count > 0 else UNAVAILABLE
            year_ok = (
                any(str(y) in result_year for y in allowed_years if y)
                if allowed_years != [None]
                else True
            )
            if not year_ok:
                continue

            duration = await meta.nth(1).inner_text(timeout=1000) if meta_count > 1 else UNAVAILABLE

            rating = UNAVAILABLE
            try:
                rating_el = parent.locator(".ipc-rating-star--rating").first
                if await rating_el.is_visible():
                    rating = await rating_el.inner_text(timeout=1000)
            except Exception:
                pass

            return link, result_year, duration, rating
        except Exception:
            continue

    return None, UNAVAILABLE, UNAVAILABLE, UNAVAILABLE


def _normalize_title(text: str) -> str:
    """يُنظّف العنوان من علامات الترقيم والمسافات الزائدة."""
    text = text.strip().lower()
    for ch in [".", ":", "-", ",", "'", "'"]:
        text = text.replace(ch, " ")
    return " ".join(text.split())


# ─── بناء النتيجة ─────────────────────────────────────────────────────────────

def _build_result(work_id: str, meta: dict, details: dict) -> dict:
    """يجمع بيانات الميتاداتا وتفاصيل الصفحة في dict موحد."""
    duration = meta.get("duration", UNAVAILABLE)
    year_val = meta.get("year", UNAVAILABLE)
    rating = meta.get("rating", UNAVAILABLE)

    iso = parse_duration_to_iso(duration)
    duration_ar = format_duration_arabic(duration)

    story = details.get("story", UNAVAILABLE)
    image = details.get("image_url", UNAVAILABLE)
    genres = details.get("genres", UNAVAILABLE)

    return {
        "tmdb_id": work_id,
        "story": story if story != UNAVAILABLE else None,
        "poster_url": image if image != UNAVAILABLE else None,
        "rating": rating if rating != UNAVAILABLE else None,
        "runtime": duration_ar if duration_ar != UNAVAILABLE else None,
        "duration_iso": iso,
        "labels": genres if genres != UNAVAILABLE else None,
        "year": (
            int(year_val)
            if year_val and year_val != UNAVAILABLE and str(year_val).isdigit()
            else None
        ),
        "is_ready": True,
    }