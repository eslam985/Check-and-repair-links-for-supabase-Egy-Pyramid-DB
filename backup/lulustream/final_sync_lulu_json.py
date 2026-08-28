import requests
import urllib.parse
import os
from supabase import create_client, Client

# --- إعدادات سوبابيز ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# 📋 الصق هنا ملف الـ JSON اللي طلع لك من الكونسول (الـ 27 حلقة)
LULU_DATA = [

]
def final_injection():
    success_count = 0
    already_exists = 0
    not_found_in_db = 0

    print(f"📡 بدء حقن {len(LULU_DATA)} رابط مستخرج من الكونسول...")

    for item in LULU_DATA:
        # 2. السطر ده عدلناه عشان يفك تشفير الرابط (unquote) ويشيل أي زيادات
        raw_source = item['source_url'].split('?')[0]
        clean_source = urllib.parse.unquote(raw_source) 
        
        lulu_link = item['lulu_embed']

        # استخراج اسم الملف فقط من الرابط (عشان نبحث بيه بمرونة)
        filename_only = clean_source.split('/')[-1]

        # 3. البحث باستخدام اسم الملف العربي الصريح
        query = supabase.table("links").select("episode_id").ilike("url", f"%{filename_only}%").execute()

        if query.data:
            ep_id = query.data[0]['episode_id']

            check = supabase.table("links").select("id").eq("episode_id", ep_id).eq("server_name", "lulustream").execute()

            if not check.data:
                new_entry = {
                    "episode_id": ep_id,
                    "server_name": "lulustream",
                    "url": lulu_link,
                    "link_type": "watch",
                    "quality": "720p"
                }
                supabase.table("links").insert(new_entry).execute()
                print(f"✅ تم الحقن: {item['video_title']} -> ID: {ep_id}")
                success_count += 1
            else:
                print(f"⏭️ موجود مسبقاً: {item['video_title']}")
                already_exists += 1
        else:
            # لو ملقاش، جرب يبحث بالرابط المشفر الأصلي كخطة بديلة (Fallback)
            query_fallback = supabase.table("links").select("episode_id").ilike("url", f"%{raw_source}%").execute()
            if query_fallback.data:
                # ... نفس كود الحقن لو تحب تكرره هنا ...
                pass
            
            print(f"❌ لم نجد أصل في سوبابيز للرابط: {filename_only}")
            not_found_in_db += 1

    print("\n" + "="*30)
    print(f"📊 التقرير النهائي:")
    print(f"✅ تم الحقن بنجاح: {success_count}")
    print(f"⏭️ تم تخطيه (موجود): {already_exists}")
    print(f"⚠️ لم يتم العثور عليه: {not_found_in_db}")
    print("="*30)

if __name__ == "__main__":
    final_injection()