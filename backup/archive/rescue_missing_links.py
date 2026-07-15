import httpx
import re
from dotenv import load_dotenv

load_dotenv()
from shared import supabase, log

ID_PREFIX = "egy_pyr_"


def normalize_title(title):
    if not title:
        return ""
    t = str(title).lower()
    # تنظيف الرموز مع الإبقاء على الحروف والأرقام
    t = re.sub(r"[^a-zA-Z0-9\u0600-\u06FF\s]", " ", t)

    stop_words = [
        "مسلسل",
        "الموسم",
        "السادس",
        "الحلقة",
        "كامل",
        "2025",
        "2026",
        "-",
        "_",
        "مترجم",
        "مدبلج",
        "حصري",
        "حصرياً",
        "حصريا",
        "فيلم",
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
    ]  # أضف ما شئت
    for w in stop_words:
        t = re.sub(rf"\b{w}\b", " ", t)

    return " ".join(t.split()).strip()


def extract_ep_num(text):
    if not text:
        return None
    # 1. البحث عن الصيغ الشهيرة (الحلقة 5, e5, ep5, _e5)
    # أضفنا [_ \s]* ليدعم الأندرسكور أو المسافات
    match = re.search(r"(?:الحلقة|e|ep)[_ \s]*(\d+)", text, re.IGNORECASE)
    if match:
        return int(match.group(1))

    # 2. محاولة أخيرة: لو الـ ID بينتهي برقم بعد حرف e زي _e1
    match_end = re.search(r"e(\d+)$", text, re.IGNORECASE)
    if match_end:
        return int(match_end.group(1))

    return None


def start_rescue_mission():
    log("🚀 انطلاق مهمة الإنقاذ للروابط الضائعة...")

    # 1. جلب الحلقات التي ليس لها رابط archive
    # سنقوم بعمل استعلام ذكي للحلقات التي تفتقد لسيرفر archive
    query = """
        select e.id, e.episode_number, m.title, m.id as media_id
        from episodes e
        join medias m on e.media_id = m.id
        where not exists (
            select 1 from links l 
            where l.episode_id = e.id and l.server_name = 'archive'
        )
    """
    # تنفيذ الاستعلام عبر سوبابيز (بافتراض وجود صلاحية تنفيذ sql أو جلب الحلقات وفلترتها برمجياً)
    # للتبسيط والأمان سنستخدم الفلترة البرمجية:
    all_episodes = (
        supabase.table("episodes").select("id, episode_number, media_id").execute().data
    )

    log(f"📦 فحص {len(all_episodes)} حلقة للبحث عن نواقص...")

    rescued_count = 0

    for ep in all_episodes:
        # فحص هل لها أرشيف؟
        exists = (
            supabase.table("links")
            .select("id")
            .eq("episode_id", ep["id"])
            .eq("server_name", "archive")
            .execute()
        )
        if exists.data:
            continue  # تخطي لأن الرابط موجود بالفعل

        # جلب اسم الميديا
        media = (
            supabase.table("medias")
            .select("title")
            .eq("id", ep["media_id"])
            .single()
            .execute()
            .data
        )
        if not media:
            continue

        target_title = media["title"]
        clean_name = normalize_title(target_title)
        ep_num = ep["episode_number"]

        log(f"🔍 محاولة إيجاد سورس لـ: {target_title} - حلقة {ep_num}")

        # 2. البحث في أرشيف أورج عن هذا العمل تحديداً
        search_url = "https://archive.org/advancedsearch.php"

        # --- بداية التعديل الجديد (البحث التراكمي) ---
        words = clean_name.split()
        results = []

        # المحاولة الأولى: البحث بكلمتين (أكثر دقة)
        if len(words) >= 2:
            short_name = " ".join(words[:2])
            final_query = f'"{short_name}" AND (identifier:(egy_pyr_*) OR identifier:(egy_pyramid_*))'
            params = {
                "q": final_query,
                "fl[]": "identifier,title",
                "rows": "50",
                "output": "json",
            }
            try:
                resp = httpx.get(search_url, params=params)
                results = resp.json().get("response", {}).get("docs", [])
            except Exception:
                results = []

        # المحاولة الثانية: لو مفيش نتائج، جرب بكلمة واحدة (أكثر مرونة)
        if not results and len(words) >= 1:
            short_name = words[0]
            final_query = f'"{short_name}" AND (identifier:(egy_pyr_*) OR identifier:(egy_pyramid_*))'
            params = {
                "q": final_query,
                "fl[]": "identifier,title",
                "rows": "50",
                "output": "json",
            }
            try:
                resp = httpx.get(search_url, params=params)
                results = resp.json().get("response", {}).get("docs", [])
            except Exception:
                results = []
        # --- نهاية التعديل ---

        try:
            # اللوب دلوقتي بتبدأ من نتايج البحث اللي جمعناها
            for item in results:
                arc_id = item["identifier"]
                arc_title = item["title"]

                # تحسين المطابقة: بنجرب نجيب الرقم من العنوان أو من الـ ID نفسه
                found_ep = extract_ep_num(arc_title)
                if found_ep is None:
                    found_ep = extract_ep_num(arc_id)

                # مقارنة رقم الحلقة
                if found_ep == ep_num:
                    new_url = f"https://archive.org/details/{arc_id}"

                    # 3. الـ "حقن" في المكان الفاضي
                    supabase.table("links").insert(
                        {
                            "episode_id": ep["id"],
                            "server_name": "archive",
                            "url": new_url,
                            "quality": "720p",
                            "link_type": "watch",
                            "last_check_status": "valid",
                        }
                    ).execute()

                    log(f"✅ تم الإنقاذ! حقن رابط أرشيف لـ {target_title}")
                    rescued_count += 1
                    break  # الخروج من اللوب بعد أول نتيجة مطابقة

        except Exception as e:
            log(f"⚠️ فشل أثناء معالجة نتايج {target_title}: {e}")

    log(f"🏁 انتهت المهمة. تم إنقاذ وحقن {rescued_count} رابط جديد بنجاح.")


if __name__ == "__main__":
    start_rescue_mission()
