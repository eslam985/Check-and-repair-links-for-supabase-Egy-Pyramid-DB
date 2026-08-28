import os
import re
import json
import unicodedata
from collections import defaultdict
from dotenv import load_dotenv
from supabase import create_client, Client

# تحميل متغيرات البيئة من ملف .env الخاص بالمشروع
ENV_PATH = "/media/es/DDrive/projects/apps-python/Check-and-repair-links-for-supabase-Egy-Pyramid-DB/.env"
load_dotenv(dotenv_path=ENV_PATH)

# --- إعداد الاتصال بالداتا بيز ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError(f"❌ لم يتم العثور على SUPABASE_URL أو SUPABASE_KEY في الملف: {ENV_PATH}")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

INPUT_FILE = "/media/es/DDrive/projects/apps-python/Check-and-repair-links-for-supabase-Egy-Pyramid-DB/vk_new_candidates.json"

ARABIC_NUM_MAP = {
    "الأول": 1, "الاول": 1, "الأولى": 1, "الاولى": 1,
    "الثاني": 2, "الثانية": 2, "الثانيه": 2,
    "الثالث": 3, "الثالثة": 3, "الثالثه": 3,
    "الرابع": 4, "الرابعة": 4, "الرابعه": 4,
    "الخامس": 5, "الخامسة": 5, "الخامسه": 5,
    "السادس": 6, "السادسة": 6, "السادسه": 6,
    "السابع": 7, "السابعة": 7, "السابعه": 7,
    "الثامن": 8, "الثامنة": 8, "الثامنه": 8,
    "التاسع": 9, "التاسعة": 9, "التاسعه": 9,
    "العاشر": 10, "العاشرة": 10, "العاشره": 10
}
def normalize_text(text: str) -> str:
    if not text: return ""
    text = text.lower()
    text = ''.join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')
    text = re.sub(r'[أإآ]', 'ا', text)
    text = re.sub(r'ى', 'ي', text)
    text = re.sub(r'ة', 'ه', text)
    text = re.sub(r'\b(مسلسل|فيلم|وثائقي|برنامج)\b', '', text)
    text = re.sub(r'[^\w\s]', ' ', text)
    return ' '.join(text.split())


def canonical_clean(text: str) -> str:
    if not text: return ""
    text = text.lower()
    text = re.sub(r'\.(mp4|mkv|avi|mov)\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\(.*?\)', '', text)
    text = re.sub(r'\b(مترجم|مترجمة|مدبلج|مدبلجة|كامل|كاملة|قسم|جودة|hd|fhd|4k)\b', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\b(مسلسل|فيلم|وثائقي|برنامج)\b', '', text, flags=re.IGNORECASE)
    
    # توحيد الأحرف العربية كاملاً
    text = re.sub(r'[أإآا]', 'ا', text)
    text = re.sub(r'[ةه]\b', 'ه', text)
    text = re.sub(r'[يى]\b', 'ي', text)
    text = re.sub(r'[^\w\s]', ' ', text)
    return ' '.join(text.split())

def parse_candidate_title(raw_title: str):
    title = raw_title.strip()
    
    # قص الامتدادات والأقواس
    title = re.sub(r'\.(mp4|mkv|avi|mov)\b', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\(.*?\)', '', title)
    
    # أخذ النص الواقع قبل كلمة الموسم أو الحلقة لمنع تكرار العناوين
    parts = re.split(r'\b(?:الموسم|سلسلة|season|s|الحلقة|حلقة|episode|ep|e)\b', title, flags=re.IGNORECASE)
    base_raw_title = parts[0].strip() if parts else title

    # 1. Direct Episode ID
    ep_id_match = re.search(r'\bEpisode\s+(\d+)\b', title, re.IGNORECASE)
    if ep_id_match:
        return {"pattern": "DIRECT_EPISODE_ID", "episode_id": int(ep_id_match.group(1))}

    # 2. IMDb ID
    imdb_match = re.search(r'\b(tt\d+)\b', title, re.IGNORECASE)
    imdb_id = imdb_match.group(1).lower() if imdb_match else None

    # 3. السنة
    year_match = re.search(r'\(?(\b20\d{2}|\b19\d{2})\)?', title)
    year = year_match.group(1) if year_match else None

    # 4. الموسم
    season_num = None
    s_match = re.search(r'\b(?:الموسم|سلسلة|season|s)\b\s*([\d+|الأول|الاول|الثاني|الثالث|الرابع|الخامس|السادس|السابع|الثامن|التاسع|العاشر]+)', title, re.IGNORECASE)
    if s_match:
        val = s_match.group(1).strip()
        season_num = int(val) if val.isdigit() else ARABIC_NUM_MAP.get(val)

    # 5. الحلقة
    ep_num = None
    e_match = re.search(
        r'\b(?:الحلقة|حلقة|episode|ep|e)\b\s*([\d+|الأولى|الاولى|الثانية|الثانيه|الثالثة|الثالثه|الرابعة|الرابعه|الخامسة|الخامسه|السادسة|السادسه|السابعة|السابعه|الثامنة|الثامنه|التاسعة|التاسعه|العاشرة|العاشره]+)',
        title, re.IGNORECASE
    )
    if e_match:
        val = e_match.group(1).strip()
        ep_num = int(val) if val.isdigit() else ARABIC_NUM_MAP.get(val)
    elif season_num is not None:
        ep_num = 1

    return {
        "pattern": "PARSED_METADATA",
        "imdb_id": imdb_id,
        "clean_title": canonical_clean(base_raw_title),
        "year": year,
        "season_number": season_num,
        "episode_number": ep_num
    }

def validate_all_candidates():
    if not os.path.exists(INPUT_FILE):
        print(f"❌ الملف غير موجود: {INPUT_FILE}")
        return

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        candidates = json.load(f)

    print(f"📦 بدء فحص المحاكاة (Dry-Run) لعدد {len(candidates)} فيديو...\n")

    # 1. جلب جميع روابط VK الحالية مع روابطها دفعة واحدة (Bulk)
    existing_vk_links = {}
    start = 0
    while True:
        res = supabase.table("links").select("episode_id, url").or_("server_name.ilike.vk,url.ilike.%vkvideo.ru%,url.ilike.%vk.com%").range(start, start + 999).execute()
        data = res.data or []
        if not data: break
        for row in data:
            if row.get("episode_id"):
                existing_vk_links[row["episode_id"]] = row.get("url")
        if len(data) < 1000: break
        start += 1000

    # 2. جلب جميع الأعمال (medias) إلى الذاكرة دفعة واحدة للمطابقة الدقيقة في بايثون
    all_medias = []
    start_m = 0
    while True:
        res_m = supabase.table("medias").select("id, media_type, normalized_title, slug, title, tmdb_id").range(start_m, start_m + 999).execute()
        data_m = res_m.data or []
        if not data_m: break
        all_medias.extend(data_m)
        if len(data_m) < 1000: break
        start_m += 1000

    episodes_by_id_cache = {}
    medias_by_title_cache = {}
    seasons_by_media_cache = {}
    episodes_lookup_cache = {}

    # تجميع الأنماط والمعرفات المباشرة لجلبها دفعة واحدة
    direct_ep_ids = []
    parsed_items = []

    for item in candidates:
        raw_title = item.get("title", "")
        parsed = parse_candidate_title(raw_title)
        parsed_items.append((item, parsed))
        if parsed["pattern"] == "DIRECT_EPISODE_ID":
            direct_ep_ids.append(parsed["episode_id"])

    # جلب جميع الـ Episode IDs المباشرة دفعة واحدة على دفعات (Chunks)
    if direct_ep_ids:
        unique_ep_ids = list(set(direct_ep_ids))
        for i in range(0, len(unique_ep_ids), 500):
            chunk = unique_ep_ids[i:i + 500]
            res = supabase.table("episodes").select("id, media_id, season_id, episode_number, medias(media_type)").in_("id", chunk).execute()
            for row in (res.data or []):
                episodes_by_id_cache[row["id"]] = row

    def is_close_match(target: str, candidate: str) -> bool:
        if not target or not candidate:
            return False
        if target == candidate:
            return True
        # منع ربط العناوين المختلفة التي تشترك فقط في الكلمات الأولى
        if target.startswith(candidate) or candidate.startswith(target):
            len_diff = abs(len(target) - len(candidate))
            if len_diff / max(len(target), len(candidate)) <= 0.25:
                return True
        return False

    # دوال الاستعلام المخبأة (Cached Queries)
    def get_media(clean_title, imdb_id):
        if not clean_title and not imdb_id:
            return None

        cache_key = f"{imdb_id}_{clean_title}"
        if cache_key in medias_by_title_cache:
            return medias_by_title_cache[cache_key]

        media = None

        # 1. المطابقة بـ tmdb_id
        if imdb_id:
            media = next((m for m in all_medias if m.get("tmdb_id") == imdb_id), None)

        # 2. المطابقة الدقيقة في الذاكرة لتجاهل الهمزات والتاء المربوطة والزوائد
        if not media and clean_title:
            target = canonical_clean(clean_title)

            for cand in all_medias:
                db_t = canonical_clean(cand.get("title", ""))
                db_norm = canonical_clean(cand.get("normalized_title", ""))
                db_slug = canonical_clean(cand.get("slug", "").replace("-", " "))

                # مطابقة تامة بعد التنظيف
                if target in (db_t, db_norm, db_slug):
                    media = cand
                    break

                # مطابقة قريبة بشرط تقارب الطول
                if is_close_match(target, db_t) or is_close_match(target, db_norm) or is_close_match(target, db_slug):
                    media = cand
                    break

        medias_by_title_cache[cache_key] = media
        return media

    def get_seasons(media_id):
        if media_id not in seasons_by_media_cache:
            res = supabase.table("seasons").select("id, season_number").eq("media_id", media_id).execute()
            seasons_by_media_cache[media_id] = res.data or []
        return seasons_by_media_cache[media_id]

    def get_episode(media_id, season_id, ep_num):
        key = (media_id, season_id, ep_num)
        if key not in episodes_lookup_cache:
            query = supabase.table("episodes").select("id, season_id").eq("media_id", media_id).eq("episode_number", ep_num)
            if season_id is None:
                query = query.is_("season_id", "null")
            else:
                query = query.eq("season_id", season_id)
            res = query.execute()
            episodes_lookup_cache[key] = res.data[0]["id"] if res.data else None
        return episodes_lookup_cache[key]

    results = defaultdict(list)

    for item, parsed in parsed_items:
        # المسار الأول: Direct Episode ID
        if parsed["pattern"] == "DIRECT_EPISODE_ID":
            ep_id = parsed["episode_id"]
            ep = episodes_by_id_cache.get(ep_id)
            
            if not ep:
                results["🔴_EPISODE_NOT_FOUND_IN_DB"].append({"item": item, "reason": f"Episode ID {ep_id} غير موجود في الداتا بيز"})
                continue
            
            media_type = ep.get("medias", {}).get("media_type")

            if ep_id in existing_vk_links:
                results["⚠️_LINK_ALREADY_EXISTS"].append({
                    "item": item, 
                    "episode_id": ep_id,
                    "existing_vk_url": existing_vk_links[ep_id]
                })
            elif media_type in ['series', 'tv'] and ep["season_id"] is None:
                results["⚠️_INVALID_SEASON_NULL"].append({"item": item, "episode_id": ep_id, "reason": "حلقة مسلسل بدون season_id"})
            elif media_type == 'movie' and ep["season_id"] is not None:
                results["⚠️_INVALID_MOVIE_SEASON"].append({"item": item, "episode_id": ep_id, "reason": "فيلم يحتوي على season_id"})
            else:
                results["🟢_READY_FOR_INSERT"].append({
                    "item": item, 
                    "episode_id": ep_id, 
                    "media_id": ep["media_id"],
                    "season_id": ep["season_id"],
                    "media_type": media_type
                })
            continue

        # المسار الثاني: Parsed Title / IMDb ID
        clean_title = parsed["clean_title"]
        imdb_id = parsed["imdb_id"]

        media = get_media(clean_title, imdb_id)
        if not media:
            results["🔴_UNMATCHED_MEDIA"].append({"item": item, "clean_title": clean_title, "imdb_id": imdb_id})
            continue

        media_id = media["id"]
        media_type = media["media_type"]

        # معالجة الأفلام
        if media_type == 'movie':
            ep_id = get_episode(media_id, None, parsed["episode_number"] or 1)
            if ep_id:
                if ep_id in existing_vk_links:
                    results["⚠️_LINK_ALREADY_EXISTS"].append({"item": item, "episode_id": ep_id})
                else:
                    results["🟢_READY_FOR_INSERT"].append({
                        "item": item, "episode_id": ep_id, "media_id": media_id, "season_id": None, "media_type": "movie"
                    })
            else:
                results["🔴_MISSING_MOVIE_EPISODE_ENTRY"].append({"item": item, "media_id": media_id})
            continue

        # معالجة المسلسلات
        seasons = get_seasons(media_id)

        target_season_id = None
        if parsed["season_number"] is not None:
            matched_s = next((s for s in seasons if s["season_number"] == parsed["season_number"]), None)
            if matched_s:
                target_season_id = matched_s["id"]
            else:
                results["🔴_SEASON_NUMBER_NOT_FOUND"].append({"item": item, "media_id": media_id, "season_num": parsed["season_number"]})
                continue
        else:
            if len(seasons) == 1:
                target_season_id = seasons[0]["id"]
            elif len(seasons) > 1:
                results["⚠️_AMBIGUOUS_MULTIPLE_SEASONS"].append({"item": item, "media_id": media_id, "total_seasons": len(seasons)})
                continue
            else:
                results["🔴_NO_SEASONS_FOR_SERIES"].append({"item": item, "media_id": media_id})
                continue

        ep_num = parsed["episode_number"]
        if ep_num is None:
            results["🔴_MISSING_EPISODE_NUMBER_IN_TITLE"].append({"item": item, "media_id": media_id})
            continue

        ep_id = get_episode(media_id, target_season_id, ep_num)
        if ep_id:
            if ep_id in existing_vk_links:
                results["⚠️_LINK_ALREADY_EXISTS"].append({
                    "item": item, 
                    "episode_id": ep_id,
                    "existing_vk_url": existing_vk_links[ep_id]
                })
            else:
                results["🟢_READY_FOR_INSERT"].append({
                    "item": item, "episode_id": ep_id, "media_id": media_id, "season_id": target_season_id, "media_type": media_type
                })
        else:
            results["🔴_EPISODE_NOT_FOUND_FOR_SEASON"].append({"item": item, "media_id": media_id, "season_id": target_season_id, "ep_num": ep_num})

    # طباعة التقرير الشامل
    print("=" * 60)
    print("📊 تقرير مطابقة وحساب المحاكاة (Dry-Run Matching Report)")
    print("=" * 60)
    for category, items in results.items():
        print(f"├─ {category}: {len(items)}")
    print(f"└─ 📦 إجمالي الفيديوهات المفحوصة: {len(candidates)}")

    # حفظ تقرير تفصيلي بكل الفئات
    with open("dry_run_validation_report.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("\n💾 تم حفظ التقرير التفصيلي في: dry_run_validation_report.json")
    
    
if __name__ == "__main__":
    validate_all_candidates()