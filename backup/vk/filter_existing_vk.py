import os
import sys
import json
import re

# تحديد مسار المجلد الرئيسي للمشروع ومسار ملف .env
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(project_root)

from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(project_root, ".env"))

from shared import supabase

INPUT_REPORT_FILE = "vk_duplicates_report.json"

OUTPUT_NEW_CANDIDATES = "vk_new_candidates.json"
OUTPUT_ALREADY_IN_DB = "vk_already_in_db.json"


def parse_special_title(title: str) -> dict:
    """استخراج المعرفات الرقمية أو رقم الحلقة من العنوان"""
    title_str = title.strip()
    
    # 1. فحص نمط IMDb ID (مثال: Tt3756806 أو tt1234567)
    imdb_match = re.search(r"\b(tt\d+)\b", title_str, re.IGNORECASE)
    if imdb_match:
        return {"type": "imdb", "value": imdb_match.group(1).lower()}
        
    # 2. فحص نمط الحلقة الصريح (مثال: Episode 902 أو E902)
    ep_match = re.search(r"(?:Episode|Ep|الحلقة)\s*(\d+)", title_str, re.IGNORECASE)
    if ep_match:
        return {"type": "episode_num", "value": int(ep_match.group(1))}
        
    return {"type": "raw_title", "value": title_str}


def extract_vk_base_id(url: str) -> str | None:
    """استخراج المعرف الأساسي owner_id_id من الرابط"""
    if not url:
        return None
    oid_m = re.search(r"[?&]oid=(-?\d+)", url)
    id_m = re.search(r"[?&]id=(\d+)", url)
    if oid_m and id_m:
        return f"{oid_m.group(1)}_{id_m.group(1)}"
    
    vid_m = re.search(r"video(-?\d+)_(\d+)", url)
    if vid_m:
        return f"{vid_m.group(1)}_{vid_m.group(2)}"
    return None

def main():
    # 1. تحميل قائمة الفيديوهات الفريدة من ملف التقرير
    try:
        with open(INPUT_REPORT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            clean_videos = data.get("clean_videos", [])
    except FileNotFoundError:
        print(f"❌ لم يتم العثور على الملف {INPUT_REPORT_FILE}")
        return

    print(f"📦 إجمالي الفيديوهات الفريدة المحملة: {len(clean_videos)}")

    # 2. سحب جميع روابط VK الموجودة حالياً في قاعدة البيانات مع Pagination
    print("🔍 جلب روابط VK الحالية من قاعدة البيانات...")
    db_links = []
    start = 0
    step = 1000
    try:
        while True:
            res = (
                supabase.table("links")
                .select("id, url, episode_id")
                .eq("server_name", "vk")
                .range(start, start + step - 1)
                .execute()
            )
            data = res.data or []
            if not data:
                break
            db_links.extend(data)
            if len(data) < step:
                break
            start += step
        print(f"✅ تم جلب {len(db_links)} رابط VK من قاعدة البيانات.")
        
        # جلب معرفات IMDb المتاحة من جدول medias
        print("🔍 جلب بيانات الأعمال (medias) للتحقق من IMDb IDs...")
        res_medias = supabase.table("medias").select("id, title, tmdb_id, category").execute()
        db_medias = res_medias.data or []
        tmdb_to_media_id = {str(m["tmdb_id"]).lower(): m["id"] for m in db_medias if m.get("tmdb_id")}
        
        # جلب الحلقات الحالية للربط برقم الحلقة
        print("🔍 جلب الحلقات (episodes) للربط المباشر...")
        
    except Exception as e:
        print(f"❌ خطأ أثناء جلب البيانات من Supabase: {e}")
        return

    # 3. استخراج المعرفات الأساسية لروابط الداتا بيز
    db_base_ids = set()
    for item in db_links:
        b_id = extract_vk_base_id(item.get("url", ""))
        if b_id:
            db_base_ids.add(b_id)

    print(f"🔹 عدد المعرفات المستخرجة بنجاح من الداتا بيز: {len(db_base_ids)}")

    # 4. التصفية المتقدمة ومطابقة الأنماط الخاصّة
    already_in_db = []
    new_candidates = []
    matched_db_base_ids = set()
    matched_by_imdb = 0
    matched_by_ep_num = 0

    for video in clean_videos:
        # أ) المطابقة المباشرة عبر base_id
        if video["base_id"] in db_base_ids:
            already_in_db.append(video)
            matched_db_base_ids.add(video["base_id"])
            continue
            
        # ب) التحقق من الأنماط الخاصة للعنوان
        parsed_info = parse_special_title(video.get("title", ""))
        
        if parsed_info["type"] == "imdb":
            imdb_val = parsed_info["value"]
            if imdb_val in tmdb_to_media_id:
                video["matched_media_id"] = tmdb_to_media_id[imdb_val]
                video["match_reason"] = "tmdb_id"
                matched_by_imdb += 1
                
        elif parsed_info["type"] == "episode_num":
            video["parsed_episode_number"] = parsed_info["value"]
            video["match_reason"] = "episode_number"
            matched_by_ep_num += 1

        new_candidates.append(video)

    print(f"🔹 تم تمييز {matched_by_imdb} فيديو عبر imdb id.")
    print(f"🔹 تم تمييز {matched_by_ep_num} فيديو عبر رقم الحلقة (episode num).")

    print("\n📊 نتائج الفحص والمطابقة:")
    print(f"├─ 🟢 موجودة بالفعل في الداتا بيز وسيتم استبعادها: {len(already_in_db)}")
    print(f"└─ 🎯 فيديوهات جديدة غير مسجلة (المرشحة للإضافة): {len(new_candidates)}")

    # 5. حفظ النتائج في ملفات منفصلة
    with open(OUTPUT_NEW_CANDIDATES, "w", encoding="utf-8") as f:
        json.dump(new_candidates, f, ensure_ascii=False, indent=2)

    with open(OUTPUT_ALREADY_IN_DB, "w", encoding="utf-8") as f:
        json.dump(already_in_db, f, ensure_ascii=False, indent=2)
    # حصر الروابط غير المطابقة مع جلب أسماء الميديا وتفاصيل الحلقات
    unmatched_raw = [
        link for link in db_links 
        if not extract_vk_base_id(link.get("url", "")) or extract_vk_base_id(link.get("url", "")) not in matched_db_base_ids
    ]
    
    unmatched_ep_ids = list(set(l["episode_id"] for l in unmatched_raw if l.get("episode_id")))
    
    episodes_map = {}
    medias_map = {}
    
    if unmatched_ep_ids:
        res_eps = supabase.table("episodes").select("id, media_id, episode_number").in_("id", unmatched_ep_ids).execute()
        episodes_map = {ep["id"]: ep for ep in (res_eps.data or [])}
        
        media_ids = list(set(ep["media_id"] for ep in episodes_map.values() if ep.get("media_id")))
        if media_ids:
            res_meds = supabase.table("medias").select("id, title").in_("id", media_ids).execute()
            medias_map = {m["id"]: m.get("title") for m in (res_meds.data or [])}

    unmatched_db_links = []
    unique_titles = set()

    for link in unmatched_raw:
        b_id = extract_vk_base_id(link.get("url", ""))
        ep_id = link.get("episode_id")
        ep_data = episodes_map.get(ep_id, {})
        m_id = ep_data.get("media_id")
        m_title = medias_map.get(m_id, "غير معروف")
        ep_num = ep_data.get("episode_number", "?")

        if m_title != "غير معروف":
            unique_titles.add(m_title)

        unmatched_db_links.append({
            "id": link["id"],
            "url": link.get("url"),
            "extracted_base_id": b_id,
            "episode_id": ep_id,
            "media_title": m_title,
            "episode_number": ep_num
        })

    print(f"\n⚠️ تم حصر {len(unmatched_db_links)} رابط غير مطبق من الداتا بيز.")
    print(f"🎬 إجمالي الأعمال (Medias) التابعة لها: {len(unique_titles)}")
    print("📋 قائمة بعض الأعمال غير المطابقة:")
    for title in list(unique_titles)[:15]:
        print(f"  - {title}")
    if len(unique_titles) > 15:
        print(f"  ... وغيرها ({len(unique_titles) - 15} عمل آخر)")

    with open("unmatched_db_links.json", "w", encoding="utf-8") as f:
        json.dump(unmatched_db_links, f, ensure_ascii=False, indent=2)

    print("💾 تم حفظ التفاصيل الكاملة بالأسماء في: unmatched_db_links.json")
    # فحص الـ 79 رابطاً في ملف الجلب الخام لمعرفة سبب غيابها
    try:
        with open("vk_videos.json", "r", encoding="utf-8") as f:
            raw_videos = json.load(f)
            raw_base_ids = {v.get("base_id") for v in raw_videos if v.get("base_id")}
            
            dropped_by_duplicates = 0
            missing_from_vk = 0
            
            for item in unmatched_db_links:
                b_id = item.get("extracted_base_id")
                if b_id in raw_base_ids:
                    dropped_by_duplicates += 1
                else:
                    missing_from_vk += 1
                    
            print(f"\n🔍 تحليل سبب عدم مطابقة الـ {len(unmatched_db_links)} رابط:")
            print(f"  ├─ ❌ محذوفة/غير موجودة في جلب VK الأصلي: {missing_from_vk}")
            print(f"  └─ ⚠️ تم استبعادها بسبب التكرار في check_duplicates: {dropped_by_duplicates}")
    except Exception as e:
        print(f"⚠️ تعذر فحص الملف الخام: {e}")
    print(f"\n💾 تم حفظ الفيديوهات الجديدة في: {OUTPUT_NEW_CANDIDATES}")
    print(f"💾 تم حفظ الفيديوهات المستبعدة في: {OUTPUT_ALREADY_IN_DB}")

if __name__ == "__main__":
    main()