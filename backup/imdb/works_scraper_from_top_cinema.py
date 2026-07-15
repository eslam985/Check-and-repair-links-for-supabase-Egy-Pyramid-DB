"""
╔══════════════════════════════════════════════════════════════════╗
║          TopCinema Smart Crawler - by Islam                      ║
║          يجلب روابط LuluStream ويحقنها في Supabase               ║
╚══════════════════════════════════════════════════════════════════╝

المتطلبات (شغّل في Colab):
    !pip install playwright supabase-py
    !playwright install chromium

الإعدادات: عدّل القسم CONFIG أدناه فقط.
"""

import time
import random
import logging
from typing import Optional
import re
import os
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from supabase import create_client, Client
import nest_asyncio

# هذا السطر هو السحر الذي يحل المشكلة في كولاب
nest_asyncio.apply()

# ──────────────────────────────────────────────
# ⚙️  CONFIG — عدّل هنا فقط
# ──────────────────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TABLE_NAME = "download_tasks"


START_PAGE = 10  # ابدأ من الصفحة
END_PAGE = 20  # انتهِ عند الصفحة (غيّرها حسب احتياجك)
# TARGET_SERVER = "6"  # data-server="6" = GoodStream

DELAY_MIN = 3.0  # أقل تأخير (ثانية) بين الأفلام
DELAY_MAX = 7.0  # أعلى تأخير

HEADLESS = True  # False لو عايز تشوف المتصفح

# ──────────────────────────────────────────────
# 🪵  Logging
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("TopCrawler")

# ──────────────────────────────────────────────
# 🎭  User-Agents عشوائية
# ──────────────────────────────────────────────
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
    "Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
]


# ──────────────────────────────────────────────
# 🗄️  Supabase helpers
# ──────────────────────────────────────────────
def get_supabase() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def already_exists(sb: Client, movie_name: str, download_url: str) -> bool:
    """
    فحص ذكي للتكرار يعالج تلوث العناوين بالسنين واختلاف حالة الأحرف.
    """
    # 1. تنظيف أولي لاسم الفيلم القادم من البوت (إزالة السنة لو ملتصقة بالاسم)
    # نستخدم نفس منطق normalize_title لضمان أننا نبحث عن "الاسم الصافي"
    import re

    # فصل السنة عن الاسم إذا كان movie_name يحتوي عليها في آخره
    match = re.search(r"^(.*)\s(\d{4})$", movie_name)
    if match:
        incoming_pure_title = match.group(1).strip()
        incoming_year = match.group(2).strip()
    else:
        incoming_pure_title = movie_name.strip()
        incoming_year = None

    # إنشاء نمط بحث مرن (Smart Wildcard) لتجاوز اختلافات علامات الترقيم والمسافات
    # يحول "Barron's Cove" أو "Barron s Cove" إلى "Barron%s%Cove"
    smart_pattern = re.sub(r"[^a-zA-Z0-9\u0600-\u06FF]+", "%", incoming_pure_title)
    smart_pattern = f"%{smart_pattern}%"

    # 2. الفحص في جدول المهام باستخدام النمط المرن
    in_tasks = (
        sb.table("download_tasks")
        .select("id")
        .or_(f"source_url.eq.{download_url},task_name.ilike.{smart_pattern}")
        .execute()
    )

    if in_tasks.data:
        return True

    # 3. الفحص الشامل في جدول الميديا باستخدام النمط المرن
    query = sb.table("medias").select("id").ilike("title", smart_pattern)

    if incoming_year:
        query = query.eq("year", incoming_year)

    in_medias = query.execute()

    if in_medias.data:
        log.info(f"  ♻️  تم العثور على العمل مسبقاً (تطابق مرن): {incoming_pure_title}")
        return True

    return False


def insert_task(sb: Client, movie_name: str, download_url: str) -> bool:
    """يُدرج مهمة جديدة في download_tasks."""
    try:
        sb.table(TABLE_NAME).insert(
            {
                "task_name": movie_name,
                "source_url": download_url,
                "status": "idle",
                "progress_percent": 0,
                "download_speed": "0 MB/s",
                "status_message": "Waiting for Beast...",
            }
        ).execute()
        return True
    except Exception as exc:
        log.error(f"  ❌ خطأ في الإدراج: {exc}")
        return False


def get_idle_tasks_count(sb) -> int:
    """استعلام سريع لجلب عدد المهام المنتظرة حالياً في الطابور"""
    try:
        response = (
            sb.table(TABLE_NAME)
            .select("id", count="exact")
            .eq("status", "idle")
            .execute()
        )
        # إذا نجح الاستعلام نُرجع العدد الفعلي، وإلا نُرجع 0 كأمان
        return response.count if response.count is not None else 0
    except Exception as e:
        log.error(f"❌ فشل فحص عدد المهام الـ idle من قاعدة البيانات: {e}")
        # لو حصل خطأ في الاتصال نرجع رقم كبير كأمان عشان الاسكربت ما يغرقش الطابور بالخطأ
        return 999


# ──────────────────────────────────────────────
# 🕷️  Crawler Logic
# ──────────────────────────────────────────────
def build_page_url(page_num: int) -> str:
    if page_num == 1:
        return "https://topcinemaa.com/movies/"
    return f"https://topcinemaa.com/movies/page/{page_num}/"


def get_movie_links(page) -> list[str]:
    """يسحب روابط الأفلام من صفحة القائمة."""
    anchors = page.query_selector_all("a.recent--block")
    links = []
    for a in anchors:
        href = a.get_attribute("href")
        if href:
            links.append(href)
    return links


def get_watch_url(page) -> Optional[str]:
    """يسحب رابط صفحة المشاهدة (/watch/) من صفحة الفيلم."""
    watch_anchor = page.query_selector("a.watch")
    if not watch_anchor:
        return None
    return watch_anchor.get_attribute("href")


# --- دالة مساعدة عامة ---
def get_iframe_src(page):
    """انتظار الحصول على الـ iframe واستخراج الرابط منه."""
    page.wait_for_selector(".player--iframe iframe", timeout=15_000)
    iframe = page.query_selector(".player--iframe iframe")
    return (iframe.get_attribute("src")) if iframe else None


# --- معالج سيرفر VidTube (الأولوية الأولى) ---
def extract_vidtube(page):
    servers = page.query_selector_all(".watch--servers--list ul li.server--item")
    for srv in servers:
        name = (srv.inner_text()).strip()
        if "متعدد الجودات" in name:
            log.info(f"  🎯 محاولة سحب VidTube/متعدد الجودات: {name}")
            srv.click()
            page.wait_for_timeout(2000)
            src = get_iframe_src(page)
            if src:
                return src, "vidtube_live"
    return None, None


# --- معالج سيرفر MixDrop (الأولوية الثانية) ---
def extract_mixdrop(page):
    servers = page.query_selector_all(".watch--servers--list ul li.server--item")
    for srv in servers:
        name = (srv.inner_text()).strip()
        if "Mixdrop" in name:
            log.info(f"  🎯 محاولة سحب MixDrop: {name}")
            srv.click()
            page.wait_for_timeout(2000)

            # فحص الـ 404 الشهير في مكس دروب
            iframe = page.query_selector(".player--iframe iframe")
            if iframe:
                frame = iframe.content_frame()
                if frame:
                    content = frame.evaluate("document.body.innerHTML")
                    if "can't find the" in content and "looking for" in content:
                        log.error("  🚫 رابط Mixdrop ميت!")
                        return None, "mixdrop_dead"

            src = get_iframe_src(page)
            if src:
                return src, "mixdrop_live"
    return None, None


# --- الدالة الرئيسية (المنسق) ---
def get_embed_url(page) -> tuple[Optional[str], str]:
    """
    الدالة الرئيسية التي تتحكم في ترتيب الأولويات.
    """

    # 2. إذا فشل MixDrop، حاول مع VidTube
    log.warning("⚠️ فشل MixDrop، جارٍ تجربة VidTube...")
    src, status = extract_vidtube(page)
    if src:
        log.info(f"✅ تم سحب الرابط بنجاح عبر VidTube: {src}")
        return src, status

    # 1. حاول أولاً مع MixDrop
    src, status = extract_mixdrop(page)
    if src:
        log.info(f"✅ تم سحب الرابط بنجاح عبر MixDrop: {src}")
        return src, status

    # 3. إذا فشل الجميع
    log.error("❌ لم يتم العثور على أي سيرفر صالح.")
    return None, "none"


def normalize_title(title, for_search=False):
    if not title:
        return ""

    t = str(title)

    # --- الخطوة الناقصة والضرورية ---
    # حذف أي سنة (19xx أو 20xx) قبل أي عملية تنظيف تانية
    # t = re.sub(r"\b(19|20)\d{2}\b", " ", t)
    # --------------------------------

    # تنظيف الرموز - لو للبحث بنسيب النقطتين والشرطة والأبوستروف عشان TMDB/IMDB
    # تنظيف الرموز - تم إضافة ' و : للقائمة المسموح بها
    # if for_search:
    #     t = re.sub(r"[^a-zA-Z0-9\u0600-\u06FF\s:\-\']", " ", t)
    # else:
    #     t = re.sub(r"[^a-zA-Z0-9\u0600-\u06FF\s\':]", " ", t)
    stop_words = [
        "مسلسل",
        "فيلم",
        "مترجم",
        "مدبلج",
        "كامل",
        "حصريا",
        "اونلاين",
        "مشاهدة",
        "تحميل",
        "بجودة",
        "عالية",
        "hd",
        "sd",
        "4k",
        "web-dl",
        "bluray",
        "season",
        "episode",
        "سيزون",
        "حلقة",
        "موسم",
        "اون",
        "لاين",
    ]
    for w in stop_words:
        t = re.sub(rf"\b{w}\b", " ", t)

    t = " ".join(t.split())
    return t


def get_movie_title(page) -> str:
    """يسحب عنوان الفيلم من الصفحة ويقوم بتنظيفه."""
    title_element = page.query_selector("h1.post-title")
    if title_element:
        raw_title = title_element.inner_text().strip()
    else:
        # حل احتياطي لو الأول فشل
        raw_title = (
            page.title().replace("توب سينما", "").replace("TopCinema", "").strip()
        )

    # استدعاء دالة التنظيف هنا قبل إرجاع النتيجة
    return normalize_title(raw_title)


def random_delay():
    t = random.uniform(DELAY_MIN, DELAY_MAX)
    log.info(f"  💤 انتظار {t:.1f} ثانية...")
    time.sleep(t)


# ──────────────────────────────────────────────
# 🚀  Main Runner
# ──────────────────────────────────────────────
def run():
    sb = get_supabase()
    log.info("✅ تم الاتصال بـ Supabase")

    # 🛡️ صمام الأمان الذكي لمنع تراكم وموت الروابط
    idle_count = get_idle_tasks_count(sb)
    log.info(
        f"🔍 فحص الطابور: يوجد حالياً ({idle_count}) فيلم في حالة idle تنتظر التحميل..."
    )

    if idle_count >= 20:
        log.warning(
            f"🛑 الطابور ممتلئ! (الحد الأقصى المسموح 19 وأنت عندك {idle_count}). تم إيقاف الإسكربر تلقائياً لحماية الروابط من الموت."
        )
        return

    log.info("🚀 الطابور جاهز ومستقر، جاري بدء عملية الصيد والتغذية...")

    total_inserted = 0
    total_skipped = 0
    total_failed = 0

    mixdrop_dead_count = 0
    mixdrop_live_count = 0
    vidtube_saved_count = 0

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=HEADLESS)

        for page_num in range(START_PAGE, END_PAGE + 1):
            list_url = build_page_url(page_num)
            log.info(f"\n{'═'*55}")
            log.info(f"📄 صفحة القائمة {page_num}: {list_url}")

            # ── افتح صفحة القائمة ──────────────────────────────
            list_page = browser.new_page(user_agent=random.choice(USER_AGENTS))
            try:
                list_page.goto(list_url, wait_until="domcontentloaded", timeout=30_000)
                movie_links = get_movie_links(list_page)
            except Exception as exc:
                log.error(f"❌ فشل تحميل صفحة القائمة: {exc}")
                list_page.close()
                continue
            finally:
                list_page.close()

            log.info(f"🎬 وجدت {len(movie_links)} فيلم في الصفحة")

            # ── تصفّح كل فيلم ───────────────────────────────────
            for idx, movie_url in enumerate(movie_links, 1):
                log.info(f"\n  [{idx}/{len(movie_links)}] 🎥 {movie_url}")

                try:
                    movie_page = browser.new_page(user_agent=random.choice(USER_AGENTS))

                    # صفحة الفيلم
                    movie_page.goto(
                        movie_url, wait_until="domcontentloaded", timeout=30_000
                    )
                    # ضيف السطر ده هنا عشان يسحب الاسم من الصفحة الأولى
                    movie_title = get_movie_title(movie_page)
                    watch_url = get_watch_url(movie_page)
                    movie_page.close()

                    if not watch_url:
                        log.warning("  ⚠️  لم أجد رابط المشاهدة، تخطي...")
                        total_failed += 1
                        continue

                    log.info(f"  🔗 صفحة المشاهدة: {watch_url}")

                    # صفحة المشاهدة (تحتاج JavaScript)
                    watch_page = browser.new_page(user_agent=random.choice(USER_AGENTS))
                    watch_page.goto(watch_url, wait_until="networkidle", timeout=40_000)
                    embed_url, server_status = get_embed_url(watch_page)
                    watch_page.close()

                    if server_status in [
                        "vidtube_fallback",
                        "mixdrop_dead_no_fallback",
                    ]:
                        mixdrop_dead_count += 1

                    if not embed_url:
                        log.warning("  ⚠️  لم أجد رابط الإيمباد، تخطي...")
                        total_failed += 1
                    elif embed_url == "404_DELETED":
                        log.error(
                            "  🚫 تم تخطي الفيلم لأن الرابط ميت (404 من المصدر والسيرفر البديل فشل)"
                        )
                        total_failed += 1
                    else:
                        log.info(f"  🎯 الإيمباد: {embed_url}")
                        log.info(f"  📝 الاسم:    {movie_title}")

                        # تحقق من التكرار
                        if already_exists(sb, movie_title, embed_url):
                            log.info("  ♻️  موجود مسبقاً، تخطي...")
                            total_skipped += 1
                        else:
                            ok = insert_task(sb, movie_title, embed_url)
                            if ok:
                                log.info("  ✅ تم الإدراج بنجاح!")
                                total_inserted += 1
                                if server_status == "mixdrop_live":
                                    mixdrop_live_count += 1
                                elif server_status == "vidtube_fallback":
                                    vidtube_saved_count += 1
                            else:
                                total_failed += 1

                except PlaywrightTimeout:
                    log.error("  ⏱️  انتهت المهلة، تخطي هذا الفيلم...")
                    total_failed += 1
                except Exception as exc:
                    log.error(f"  ❌ خطأ غير متوقع: {exc}")
                    total_failed += 1
                finally:
                    # أغلق أي صفحة مفتوحة
                    try:
                        movie_page.close()
                    except Exception:
                        pass
                    try:
                        watch_page.close()
                    except Exception:
                        pass

                random_delay()

            random_delay()  # تأخير إضافي بين صفحات القائمة

        browser.close()

    # ── ملخص نهائي ─────────────────────────────────────────
    log.info(f"\n{'═'*55}")
    log.info("📊 ملخص العملية التفصيلي:")
    log.info(f"   ✅ إجمالي المدرج في الـ DB:      {total_inserted}")
    log.info(f"   ♻️  أعمال مكررة تم تخطيها:      {total_skipped}")
    log.info(f"   ❌ إجمالي الفشل والأخطاء:         {total_failed}")
    log.info(f"{'─'*55}")
    log.info(f"   💀 روابط Mixdrop البايظة المكتشفة: {mixdrop_dead_count}")
    log.info(f"   🍏 روابط Mixdrop السليمة المسحوبة: {mixdrop_live_count}")
    log.info(f"   📺 روابط VidTube (متعدد) المُنقذة:  {vidtube_saved_count}")
    log.info(f"{'═'*55}")


if __name__ == "__main__":
    run()
