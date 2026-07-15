import json
import os
import re
from dotenv import load_dotenv
load_dotenv()
from shared import supabase, log

# مسار ملف البيك آب الخاص بك
BACKUP_PATH = ""

def fix_archive_url(url):
    """تحويل أي رابط أرشيف إلى صيغة details الموحدة"""
    if "archive.org" not in url:
        return url
    
    # استخراج الـ identifier من الرابط مهما كان شكله (embed, download, details)
    match = re.search(r"archive\.org/(?:details|embed|download)/([^/?#]+)", url)
    if match:
        identifier = match.group(1)
        return f"https://archive.org/details/{identifier}"
    return url

def start_json_recovery():
    if not os.path.exists(BACKUP_PATH):
        log(f"❌ الملف غير موجود في المسار: {BACKUP_PATH}")
        return

    log("📂 جاري قراءة ملف الـ JSON...")
    with open(BACKUP_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # الوصول لقائمة الروابط داخل الملف
    # الوصول للروابط داخل مفتاح data
    all_links = data.get("data", {}).get("links", [])
    log(f"🔍 تم العثور على {len(all_links)} رابط إجمالي في البيك آب.")

    # فلترة روابط أرشيف فقط
    archive_links = [l for l in all_links if "archive.org" in (l.get("url") or "")]
    log(f"🎯 تم تحديد {len(archive_links)} رابط يخص أرشيف أورج للاستعادة.")

    success_count = 0
    already_exists = 0

    for link in archive_links:
        ep_id = link.get("episode_id")
        old_url = link.get("url")
        
        if not ep_id or not old_url:
            continue

        # تصحيح الرابط للصيغة الموحدة
        new_url = fix_archive_url(old_url)

        try:
            # التأكد إن الرابط مش موجود عشان ميكررش
            check = supabase.table("links").select("id").eq("episode_id", ep_id).eq("url", new_url).execute()
            
            if not check.data:
                supabase.table("links").insert({
                    "episode_id": ep_id,
                    "server_name": "archive", # توحيد الاسم لـ archive
                    "url": new_url,
                    "link_type": "watch",
                    "quality": "720p",
                    "last_check_status": "valid"
                }).execute()
                success_count += 1
                print(f"✅ تم استعادة: {new_url} للحلقة {ep_id}")
            else:
                already_exists += 1
        except Exception as e:
            log(f"⚠️ خطأ أثناء إضافة الرابط {new_url}: {e}")

    log("="*50)
    log(f"🏁 اكتملت المهمة!")
    log(f"✅ روابط تم استعادتها بنجاح: {success_count}")
    log(f"🔄 روابط كانت موجودة بالفعل: {already_exists}")
    log("="*50)

if __name__ == "__main__":
    start_recovery_json = start_json_recovery # مجرد مرجع للتسمية
    start_json_recovery()