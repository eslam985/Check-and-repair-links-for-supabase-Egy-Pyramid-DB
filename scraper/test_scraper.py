import cloudscraper
from bs4 import BeautifulSoup
import os
import time
import re
import requests
import urllib.parse
from bs4 import BeautifulSoup
import json
from urllib.parse import urljoin, urlparse
from difflib import SequenceMatcher
from google import genai
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from selenium import webdriver
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.firefox import GeckoDriverManager

raw_keys = os.environ.get("GOOGLE_API_KEYS")
google_keys = [key.strip() for key in raw_keys.split(",")] if raw_keys else []


MODELS = [
    "gemini-2.0-flash-lite",
    "gemini-2.0-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.0-pro-ai",
    "gemini-2.0-pro-lite",
    "gemini-2.0-pro",
]


def process_with_gemini(raw_text):
    trimmed_text = raw_text[:3000]

    prompt = f"""
    استخرج من النص التالي هذه البيانات فقط وحولها JSON باللغة العربية:
    - title, story, poster (رابط يبدأ بـ POSTER_FOUND), genres, episodes
    إذا لم تجد معلومة ضع null.

    النص:
    {trimmed_text}
    """

    for model_name in MODELS:
        for key_idx, api_key in enumerate(google_keys):
            try:
                print(f"🤖 مفتاح {key_idx+1}/{len(google_keys)} | {model_name}...")
                client = genai.Client(api_key=api_key)
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                )
                clean_json = (
                    response.text.replace("```json", "").replace("```", "").strip()
                )
                result = json.loads(clean_json)
                print(f"✅ نجح مع مفتاح {key_idx+1}!")
                return result

            except Exception as e:
                error_str = str(e)

                if "429" in error_str:
                    # خلصت كوتا هذا المفتاح → جرّب التالي فوراً بدون انتظار
                    print(f"⏳ مفتاح {key_idx+1} خلص كوتاه، جاري التالي...")
                    continue

                elif "503" in error_str:
                    print(f"🔄 {model_name} مزحوم، انتظار 15s...")
                    time.sleep(15)
                    continue

                elif "404" in error_str:
                    print(f"❌ {model_name} غير متاح، جاري موديل آخر...")
                    break  # اخرج من حلقة المفاتيح وجرب موديل تاني

                else:
                    print(f"❌ خطأ غير متوقع: {e}")
                    break

    print("❌ فشلت كل المحاولات — كل المفاتيح والموديلات.")
    return None


# ---------- دوال مساعدة للمقارنة ----------


def normalize_text(text):
    """تطبيع النص: إزالة التشكيل، الأقواس، الترقيم، وتحويل إلى حروف صغيرة"""
    if not text:
        return ""
    # إزالة الحركات (التشكيل) - بسيطة
    text = re.sub(r"[ًٌٍَُِّْ]", "", text)
    # إزالة الأقواس وما بينها
    text = re.sub(r"\([^)]*\)", "", text)
    text = re.sub(r"\[[^\]]*\]", "", text)
    # إزالة علامات الترقيم والمسافات الزائدة
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text.lower()


def is_similar(title1, title2, threshold=0.8):
    """تحديد إذا كان العنوانان متشابهين (نسبة تشابه ≥ threshold)"""
    n1 = normalize_text(title1)
    n2 = normalize_text(title2)
    if not n1 or not n2:
        return False
    ratio = SequenceMatcher(None, n1, n2).ratio()
    return ratio >= threshold


# ---------- دالة خاصة بـ dramaboxdb باستخدام API ----------


def search_on_dramaboxdb(work_name):
    clean_name_for_search = re.sub(r"\s*\(.*?\)\s*", "", work_name).strip()
    encoded_search_value = urllib.parse.quote(clean_name_for_search)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    }

    try:
        # 1. زيارة صفحة البحث العادية لاستخراج الـ buildId
        search_page_url = (
            f"https://www.dramaboxdb.com/ar/search?searchValue={encoded_search_value}"
        )
        response = requests.get(search_page_url, headers=headers, timeout=15)
        response.raise_for_status()

        # استخراج الـ buildId من الـ HTML (موجود عادةً داخل سكريبت __NEXT_DATA__)
        soup = BeautifulSoup(response.text, "html.parser")
        next_data_script = soup.find("script", {"id": "__NEXT_DATA__"})

        if not next_data_script:
            return None, None

        next_data = json.loads(next_data_script.string)
        build_id = next_data["buildId"]

        # 2. بناء رابط الـ JSON الاحترافي باستخدام الـ buildId المكتشف
        api_url = f"https://www.dramaboxdb.com/_next/data/{build_id}/ar/search.json?searchValue={encoded_search_value}"

        # 3. طلب البيانات من الـ JSON API
        api_response = requests.get(api_url, headers=headers, timeout=15)
        data = api_response.json()

        # ... (باقي كود استخراج الـ book_list والبحث عن أفضل نتيجة كما كان لديك) ...
        book_list = data.get("pageProps", {}).get("bookList", [])

        if not book_list:
            print(f"⚠️ لم يتم العثور على نتائج لـ '{work_name}'.")
            return None, None

        # 5. البحث عن أفضل نتيجة مطابقة
        best_match = None
        for book in book_list:
            book_name = book.get("bookName", "")
            clean_book_name = re.sub(r"\s*\(.*?\)\s*", "", book_name).strip()

            if clean_book_name == clean_name_for_search:
                best_match = book
                print(f"🎯 تم العثور على تطابق تام: '{book_name}'")
                break
            elif (
                clean_name_for_search in clean_book_name
                or clean_book_name in clean_name_for_search
            ):
                if best_match is None:
                    best_match = book
                    print(f"🔍 تم العثور على تطابق جزئي: '{book_name}'")

        if best_match:
            # 6. بناء الرابط المباشر للصفحة
            book_id = best_match.get("bookId")
            # الرابط الأساسي للمسلسل (الأكثر ثباتًا)
            main_series_link = f"https://www.dramaboxdb.com/ar/ep/{book_id}"
            return main_series_link, best_match
        else:
            print(f"❌ لم يتم العثور على تطابق مناسب لـ '{work_name}'.")
            return None, None

    except Exception as e:
        print(f"❌ خطأ: {e}")
        return None, None


# ---------- دالة خاصة بـ reelshort ----------
# ... (باقي الكود كما هو: الاستيرادات، إعدادات Gemini، دوال البحث الأخرى) ...
def search_on_netshort_fallback(work_name):
    import cloudscraper
    from bs4 import BeautifulSoup
    import urllib.parse

    # 1. تنظيف الاسم
    clean_name = re.sub(r"\[.*?\]|\(.*?\)|\bمدبلج\b", "", work_name).strip()

    # 2. بناء رابط البحث المباشر في ReelShort (المفتاح السري)
    search_url = (
        f"https://www.reelshort.com/ar/search?keywords={urllib.parse.quote(clean_name)}"
    )

    print(f"   🚀 التوجه المباشر لصفحة بحث ReelShort: {clean_name}")

    scraper = cloudscraper.create_scraper()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Referer": "https://www.reelshort.com/ar",
    }

    try:
        response = scraper.get(search_url, headers=headers, timeout=15)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, "html.parser")

            # 3. البحث عن أول رابط لمسلسل/فيلم داخل صفحة النتائج
            # بندور على أي رابط فيه /ar/movie/
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "/ar/movie/" in href:
                    # بناء الرابط الكامل
                    full_url = (
                        f"https://www.reelshort.com{href}"
                        if href.startswith("/")
                        else href
                    )
                    print(f"   🎯 تم العثور على المسلسل بالـ ID الصحيح: {full_url}")
                    return full_url, None

            print(
                "   ⚠️ صفحة البحث فتحت بس مش لاقي روابط أفلام (ممكن محتاجة وقت تحميل؟)"
            )

    except Exception as e:
        print(f"   ❌ خطأ أثناء البحث المباشر: {e}")

    return None, None


# ---------- الدالة العامة للبحث في أي موقع (باستخدام HTML) ----------


def extract_poster_from_class(driver, target_url):
    """
    دالة مساعدة لسحب البوستر من الصفحة المباشرة للمسلسل باستخدام JavaScript.
    تستهدف الصورة الموجودة داخل div.aspect-[3/4] و alt="poster".
    """
    print(f"   🖼️ جاري سحب البوستر من الصفحة المباشرة: {target_url}")
    try:
        # فتح الصفحة
        driver.get(target_url)
        time.sleep(3)  # انتظار تحميل الصفحة

        # تنفيذ JavaScript لسحب src أو srcset من الصورة
        # تنفيذ JavaScript بطريقة مضمونة لا تعتمد على الكلاسات المعقدة
        js_script = """
        var posterImg = document.querySelector('img[alt="poster"]');
        if (posterImg) {
            // نحاول نجيب الرابط من srcset الأول لو موجود، وإلا src
            var src = posterImg.getAttribute('srcset') || posterImg.getAttribute('src') || "";
            if (src.includes(',')) {
                return src.split(',')[0].trim().split(' ')[0]; // استخراج أول رابط من srcset
            }
            return src;
        }
        return "";
        """

        poster_url = driver.execute_script(js_script)
        if poster_url:
            print(f"   ✅ تم العثور على البوستر: {poster_url}")
            return poster_url
        else:
            print("   ⚠️ لم يتم العثور على البوستر باستخدام JavaScript.")
            return None
    except Exception as e:
        print(f"   ❌ خطأ أثناء سحب البوستر: {e}")
        return None


def search_on_stardust_with_selenium(work_name):
    # تنظيف الاسم
    clean_name = re.sub(r"\[.*?\]|\(.*?\)|\bمدبلج\b", "", work_name).strip()

    print(f"   🚀 تشغيل المحرك (Selenium) للبحث عن: {clean_name}")

    # إعدادات المتصفح (Headless عشان ميفتحش نافذة قدامك)
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--window-size=1920,1080")

    service = Service(GeckoDriverManager().install())
    driver = webdriver.Firefox(service=service, options=options)

    try:
        # 1. الذهاب للموقع
        driver.get("https://www.stardusttv.net/ar")
        wait = WebDriverWait(driver, 20)

        # 2. البحث عن خانة البحث (بناءً على الكود اللي إنت بعته)
        search_input = wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "input.search-input"))
        )

        # 3. محاكاة الكتابة والضغط على Enter
        search_input.clear()
        search_input.send_keys(clean_name)
        search_input.send_keys(Keys.ENTER)

        print("   ⏳ جاري انتظار نتائج البحث...")
        time.sleep(5)

        # 4. البحث عن الروابط بدقة أعلى
        links = driver.find_elements(By.TAG_NAME, "a")

        found_links = []
        for link in links:
            href = link.get_attribute("href")
            title_attr = link.get_attribute("title")
            text_content = link.text

            if href and ("/ar/episodes/" in href or "/ar/full-episodes/" in href):
                # بنجمع كل الروابط اللي شاكة إنها صح
                if (clean_name in (title_attr or "")) or (
                    clean_name in (text_content or "")
                ):
                    if re.search(r"-\d+$", href):
                        found_links.append(href)

        # 5. الفلترة النهائية واختيار الرابط الأنسب
        if found_links:
            # ترتيب النتائج بحيث لو فيه رابط "full-episodes" يظهر الأول
            found_links.sort(key=lambda x: "full-episodes" in x, reverse=True)
            final_url = found_links[0]

            print(f"   🎯 تم قنص الرابط الصحيح: {final_url}")

            # 6. سحب البوستر من الصفحة المباشرة باستخدام الدالة المساعدة الجديدة
            poster_url = extract_poster_from_class(driver, final_url)
            return final_url, poster_url

        # لو الكود وصل هنا معناه إنه ملقاش حاجة في اللستة
        print("   ⚠️ المتصفح فتح النتائج بس ملقاش رابط مطابق بالاسم والـ ID.")

    except Exception as e:
        print(f"   ❌ خطأ أثناء تشغيل Selenium: {e}")

    finally:
        # دي أهم حلقة عشان المتصفح ميفضلش مفتوح في الرامات ويتقل الجهاز
        driver.quit()

    return None, None


def find_specific_work_url(main_url, work_name):
    clean_name = re.sub(r"\(.*?\)", "", work_name).strip()
    print(f"🔎 جاري البحث عن: '{clean_name}' في {main_url}")

    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "desktop": True}
    )
    # استخدام session للحفاظ على الكوكيز
    session = scraper

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ar,en-US;en;q=0.9",
        "Referer": main_url,
    }

    try:
        # طلب أولي للحصول على الكوكيز
        session.get(main_url, headers=headers, timeout=15)
        time.sleep(3)  # انتظار إضافي للمواقع البطيئة

        response = session.get(main_url, headers=headers, timeout=25)
        if response.status_code != 200:
            print(f"⚠️ استجابة غير ناجحة: {response.status_code}")
            return None, None

        soup = BeautifulSoup(response.text, "html.parser")

        # قائمة لتخزين الروابط المحتملة
        candidate_links = []

        # 1. البحث في الروابط العادية
        for a in soup.find_all("a", href=True):
            link_text = a.get_text(strip=True)
            title_attr = a.get("title", "").strip()
            # إذا كان النص أو title مطابقاً تماماً أو متشابهاً
            if (
                is_similar(link_text, clean_name)
                or is_similar(title_attr, clean_name)
                or clean_name in link_text
                or clean_name in title_attr
            ):
                href = a["href"]
                full_url = urljoin(main_url, href)
                return full_url, session

        # 2. البحث في العناصر غير الروابط (مثل divs مع class="title")
        for tag in soup.find_all(["h1", "h2", "h3", "div", "span", "p"]):
            if tag.get("class") and any(
                cls in str(tag.get("class")).lower()
                for cls in ["title", "name", "drama", "series"]
            ):
                text = tag.get_text(strip=True)
                if is_similar(text, clean_name):
                    # نحاول إيجاد أقرب رابط
                    parent_link = tag.find_parent("a")
                    if parent_link and parent_link.get("href"):
                        full_url = urljoin(main_url, parent_link["href"])
                        return full_url, session
                    # أو البحث عن رابط قريب (مثل sibling)
                    next_a = tag.find_next_sibling("a")
                    if next_a and next_a.get("href"):
                        full_url = urljoin(main_url, next_a["href"])
                        return full_url, session

        # 3. البحث في meta tags
        meta_title = soup.find("meta", property="og:title")
        if meta_title and meta_title.get("content"):
            if is_similar(meta_title["content"], clean_name):
                # محاولة استنتاج الرابط من og:url أو canonical
                meta_url = soup.find("meta", property="og:url")
                if meta_url and meta_url.get("content"):
                    return meta_url["content"], session

        # 4. البحث في الصور (alt attribute)
        for img in soup.find_all("img", alt=True):
            if is_similar(img["alt"], clean_name):
                parent_a = img.find_parent("a")
                if parent_a and parent_a.get("href"):
                    full_url = urljoin(main_url, parent_a["href"])
                    return full_url, session

        # 5. أخيراً: البحث عن طريق معاينة كل الروابط (ببطء) – قد يكون مفيداً للمواقع الكبيرة
        # نأخذ أول 30 رابط فقط حتى لا نبطئ كثيراً
        for a in soup.find_all("a", href=True)[:30]:
            href = a["href"]
            full_url = urljoin(main_url, href)
            # نحاول فتح الرابط (بدون تحميل كامل) ونرى إذا كان العنوان يشابه
            try:
                resp_head = session.head(full_url, timeout=5, allow_redirects=True)
                if resp_head.status_code == 200:
                    # نأخذ title من الصفحة المستهدفة (بدون تحميل كامل)
                    # نستخدم GET سريع مع stream=True لقراءة أول 8KB فقط
                    resp_get = session.get(full_url, stream=True, timeout=10)
                    if resp_get.status_code == 200:
                        chunk = ""
                        for chunk in resp_get.iter_content(
                            chunk_size=1024, decode_unicode=True
                        ):
                            if chunk:
                                # نبحث عن <title> في أول 8KB
                                if "<title>" in chunk.lower():
                                    title_match = re.search(
                                        r"<title>(.*?)</title>", chunk, re.IGNORECASE
                                    )
                                    if title_match and is_similar(
                                        title_match.group(1), clean_name
                                    ):
                                        return full_url, session
                            break  # نكتفي بأول جزء فقط
                        resp_get.close()
            except:
                continue

        print(f"⚠️ لم يتم العثور على رابط مطابق في {main_url}")
        return None, None

    except Exception as e:
        print(f"❌ خطأ أثناء البحث في {main_url}: {e}")
        return None, None


# ---------- دالة استخراج المحتوى من صفحة العمل ----------


def extract_page_content(url, scraper, external_poster=None):
    print(f"📡 جاري محاولة سحب البيانات من: {url}")

    # إعداد استراتيجية إعادة المحاولة
    retry_strategy = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "OPTIONS"],
    )

    # إنشاء جلسة جديدة وتطبيق الاستراتيجية
    session = requests.Session()
    session.mount("http://", HTTPAdapter(max_retries=retry_strategy))
    session.mount("https://", HTTPAdapter(max_retries=retry_strategy))

    # محاكاة متصفح حقيقي باستخدام Headers متقدمة
    session.headers.update(
        {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Referer": "https://www.google.com/",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Cache-Control": "max-age=0",
            "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
        }
    )

    try:
        # استخدام الجلسة المحسنة لإرسال الطلب
        response = session.get(url, timeout=30)  # زيادة وقت الانتظار
        if response.status_code != 200:
            print(f"❌ فشل التحميل: {response.status_code}")
            return None

        soup = BeautifulSoup(response.text, "html.parser")

        # --- الطريقة الجديدة: استخراج الصورة من meta tags (الأكثر دقة) ---
        poster_url = ""

        # 1. البحث عن og:image (أولوية قصوى)
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            poster_url = og_image["content"]
            print(f"🖼️ تم العثور على الصورة من og:image: {poster_url}")

        # 2. إذا لم نجد، نبحث عن twitter:image
        if not poster_url:
            twitter_image = soup.find("meta", attrs={"name": "twitter:image"})
            if twitter_image and twitter_image.get("content"):
                poster_url = twitter_image["content"]
                print(f"🖼️ تم العثور على الصورة من twitter:image: {poster_url[:80]}...")

        # 3. إذا لم نجد، نستخدم المنطق القديم (البحث في img)
        if not poster_url:
            print("   🔍 لم يتم العثور على meta image، ننتقل للبحث في img tags...")
            images = soup.find_all("img")
            for img in images:
                src = img.get("src") or img.get("data-src") or img.get("data-original")
                if src and any(
                    x in src.lower() for x in ["poster", "cover", "drama", "series"]
                ):
                    domain = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
                    poster_url = src if src.startswith("http") else f"{domain}{src}"
                    print(f"🖼️ تم العثور على صورة بالمنطق العام: {poster_url[:80]}...")
                    break

        # تنظيف النص (إزالة السكربتات والستايلات)
        # 1. تنظيف النص (إزالة السكربتات والستايلات)
        for script_or_style in soup(["script", "style", "nav", "footer", "header"]):
            script_or_style.decompose()

        # 2. التعديل المهم هنا: تحديث قيمة poster_url بالبوستر الخارجي لو وجد
        # لازم يكون بره الـ for loop ومحاذي لبقية الكود
        if external_poster:
            poster_url = external_poster
            print(f"✅ تم اعتماد البوستر الخارجي الممرر من السيلينيوم: {poster_url}")

        # 3. استخراج النص النظيف
        clean_text = soup.get_text(separator="\n")
        final_text = "\n".join(
            line.strip() for line in clean_text.splitlines() if line.strip()
        )

        # دمج رابط الصورة في أول النص (حتى يسهل على Gemini استخراجه)
        if poster_url:
            final_text = f"POSTER_FOUND: {poster_url}\n\n" + final_text
        else:
            print("⚠️ لم يتم العثور على صورة للمسلسل.")

        # حفظ النص الخام (للتصحيح)
        with open("extracted_data.txt", "w", encoding="utf-8") as f:
            f.write(final_text)

        print(f"✅ تم السحب بنجاح!")

        # إرسال النص إلى Gemini
        result_json = process_with_gemini(final_text)
        if result_json:
            print("\n✨ النتيجة النهائية الجاهزة للقاعدة (JSON):")
            print(json.dumps(result_json, indent=4, ensure_ascii=False))
            return result_json
        else:
            print("⚠️ تم سحب النص ولكن Gemini فشل في تحويله.")
            return None

    except Exception as e:
        print(f"❌ حدث خطأ: {e}")
        return None


# ======================== الجزء الرئيسي الموحّد ========================
work_to_find = "عقد بلا حب"  # أو "[مدبلج]عندما تذبل الزهور"

# تعريف المواقع مع تحديد أي دالة تستخدم
sites_config = [
    {
        "url": "https://www.dramaboxdb.com/ar",
        "type": "special",
        "handler": search_on_dramaboxdb,  # الدالة الخاصة بـ DramaboxDB
    },
    {
        "url": "https://reelshort.com/ar",
        "type": "special",
        "handler": search_on_netshort_fallback,
    },
    {
        "url": "https://www.stardusttv.net/ar",
        "type": "special",
        "handler": search_on_stardust_with_selenium,  # اسم الدالة اللي لسه كاتبينها
    },
    {
        "url": "https://www.shorttv.live/ar",
        "type": "search_url",  # نستخدم رابط البحث المباشر
        "search_pattern": "{site}/search/{query}",  # البنية التي نجحت
    },
]

found = False
final_link = None

for cfg in sites_config:
    site = cfg["url"]
    print(f"\n🚀 جاري فحص الموقع: {site}")

    if cfg.get("handler"):
        # هنا استقبلنا الرابط والبوستر (data كانت بترجع None ودلوقتي بقت بترجع البوستر)
        link, poster_url = cfg["handler"](work_to_find)

        if link:
            final_link = link
            print(f"✅ تم العثور في {site} عبر الدالة الخاصة: {final_link}")

            # لو فيه بوستر رجع من السيلينيوم، ممكن تطبعه عشان تتأكد
            if poster_url:
                print(f"🖼️ بوستر المكتشف: {poster_url}")

            scraper = cloudscraper.create_scraper()

            # نمرر الـ poster_url لدالة الاستخراج لو كانت بتدعمه،
            # أو سيبها زي ما هي وهي هتعتمد على اللي السيلينيوم جابه
            extract_page_content(final_link, scraper, external_poster=poster_url)
            found = True
            break
        else:
            print(f"⏭️ لم يتم العثور عبر الدالة الخاصة في {site}")
        continue

    search_url = None
    if "search_pattern" in cfg:
        pattern = cfg["search_pattern"]
        search_url = pattern.format(site=site, query=urllib.parse.quote(work_to_find))
        print(f"   🔍 محاولة البحث عبر: {search_url}")
        link, scraper = find_specific_work_url(search_url, work_to_find)
        if link:
            final_link = link
            print(f"🎯 لقطت الرابط من صفحة البحث: {final_link}")
            time.sleep(3)
            extract_page_content(final_link, scraper, external_poster=poster_url)
            found = True
            break

    if not search_url or not link:
        print(f"   🔍 البحث في الصفحة الرئيسية: {site}")
        link, scraper = find_specific_work_url(site, work_to_find)
        if link:
            final_link = link
            print(f"🎯 لقطت الرابط من الرئيسية: {final_link}")
            extract_page_content(final_link, scraper, external_poster=poster_url)
            found = True
            break
        else:
            print(f"⏭️ لم يتم العثور عليه في {site}")

if not found:
    print("\n❌ لم يتم العثور على العمل في أي من المواقع.")
