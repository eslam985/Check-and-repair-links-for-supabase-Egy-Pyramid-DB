import os
import sys
import json
import re
from collections import defaultdict

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(project_root)

from dotenv import load_dotenv
load_dotenv(dotenv_path=os.path.join(project_root, ".env"))

from shared import supabase

INPUT_FILE = "vk_videos.json"

REPORT_FILE = "vk_duplicates_report.json"

def check_duplicates():
    try:
        with open(INPUT_FILE, "r", encoding="utf-8") as f:
            videos = json.load(f)
    except FileNotFoundError:
        print(f"❌ الملف {INPUT_FILE} غير موجود!")
        return

    # 1. جلب base_ids الموجودة في الداتا بيز لضمان عدم استبعادها
    def extract_vk_base_id(url: str) -> str | None:
        if not url: return None
        oid_m = re.search(r"[?&]oid=(-?\d+)", url)
        id_m = re.search(r"[?&]id=(\d+)", url)
        if oid_m and id_m: return f"{oid_m.group(1)}_{id_m.group(1)}"
        vid_m = re.search(r"video(-?\d+)_(\d+)", url)
        if vid_m: return f"{vid_m.group(1)}_{vid_m.group(2)}"
        return None

    db_base_ids = set()
    start = 0
    step = 1000
    while True:
        res = supabase.table("links").select("url").eq("server_name", "vk").range(start, start + step - 1).execute()
        data = res.data or []
        if not data: break
        for item in data:
            b_id = extract_vk_base_id(item.get("url", ""))
            if b_id: db_base_ids.add(b_id)
        if len(data) < step: break
        start += step

    print(f"🔍 تم حصر {len(db_base_ids)} معرف فريد من قاعدة البيانات لحمايتها من الحذف.")

    title_map = defaultdict(list)

    for item in videos:
        # تنظيف بسيط للمسافات الزائدة حول العنوان
        clean_title = item.get("title", "").strip()
        title_map[clean_title].append(item)

    duplicates = {}
    clean_videos = []

    for title, items in title_map.items():
        if len(items) > 1:
            duplicates[title] = items
        
        # إذا كان أي فيديو في التكرار موجوداً بالداتا بيز نحتفظ به، وإلا نأخذ الأول فقط
        db_matched = [v for v in items if v.get("base_id") in db_base_ids]
        if db_matched:
            clean_videos.extend(db_matched)
        else:
            clean_videos.append(items[0])

    total_count = len(videos)
    dup_titles_count = len(duplicates)
    dup_videos_count = sum(len(v) for v in duplicates.values())

    print(f"📊 إجمالي الفيديوهات في الملف: {total_count}")
    print(f"🔹 عدد العناوين الفريدة: {len(title_map)}")

    if not duplicates:
        print("✅ مبروك! لا يوجد أي تكرار (Duplicates) في العناوين، جميع الأعمال فريدة تماماً.")
    else:
        print(f"⚠️ تم العثور على {dup_titles_count} عنوان مكرر (بإجمالي {dup_videos_count} فيديو).")

        report_data = {
            "summary": {
                "total_videos": total_count,
                "unique_titles": len(title_map),
                "duplicate_titles_count": dup_titles_count,
                "total_duplicate_videos": dup_videos_count
            },
            "duplicates": duplicates,
            "clean_videos": clean_videos
        }

        with open(REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(report_data, f, ensure_ascii=False, indent=2)

        print(f"💾 تم إنشاء ملف التقرير المفصل: {REPORT_FILE}")

if __name__ == "__main__":
    check_duplicates()