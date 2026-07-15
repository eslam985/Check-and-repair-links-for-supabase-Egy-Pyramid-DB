import requests
import os
from supabase import create_client, Client

# --- الإعدادات ---
LULU_API_KEY = os.environ.get("LULU_API_KEY")
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def harvest_links_smartly():
    print("🎯 بدء الحصاد الذكي بناءً على الدوكيومنت...")
    
    # --- العدادات ---
    stats = {
        "found_and_ready": 0,    # ملفات تم إيجادها وجاهزة للحقن
        "already_exists": 0,     # ملفات موجودة بالفعل في سوبابيز
        "not_found_on_lulu": 0,  # ملفات لم يعثر عليها في لولو
        "errors": 0              # أخطاء تقنية أثناء الفحص
    }

    # 1. جلب الحلقات الناقصة
    print("🔍 جلب الحلقات الناقصة من سوبابيز...")
    try:
        response = supabase.table("episodes").select(
            "id, episode_number, links(server_name, url)"
        ).execute()
    except Exception as e:
        print(f"❌ خطأ في الاتصال بسوبابيز: {e}")
        return

    for ep in response.data:
        existing_links = ep.get("links", [])
        
        # تخطي لو لولو موجود فعلاً
        if any(l["server_name"].lower() == "lulustream" for l in existing_links):
            stats["already_exists"] += 1
            continue

        source_link = next((l["url"] for l in existing_links if l["server_name"] in ["archive", "telegram_direct"]), None)
        
        if not source_link:
            continue

        filename = source_link.split('/')[-1].split('?')[0]
        clean_name = filename.replace(".mp4", "").replace(".mkv", "")

        # 2. البحث في لولو
        lulu_search_url = f"https://lulustream.com/api/file/list?key={LULU_API_KEY}&title={clean_name}"
        
        try:
            lulu_res = requests.get(lulu_search_url).json()
            lulu_files = lulu_res.get("result", {}).get("files", [])

            if lulu_files:
                match = lulu_files[0]
                f_code = match.get("file_code")
                lulu_embed_url = f"https://lulustream.com/e/{f_code}"

                # --- التنفيذ الفعلي (الحقن) ---
                print(f"\n🚀 [جارِ الحقن الآن...] حلقة رقم: {ep['episode_number']}")
                
                new_link = {
                    "episode_id": ep['id'],
                    "server_name": "lulustream",
                    "url": lulu_embed_url,
                    "link_type": "watch",
                    "quality": "720p"
                }
                
                try:
                    # السطر ده هو اللي بينفذ العملية في سوبابيز
                    supabase.table("links").insert(new_link).execute() 
                    
                    # بنزود العداد مرة واحدة فقط بعد نجاح العملية
                    stats["found_and_ready"] += 1
                    
                    print(f"✨ [تطابق وحقن ناجح #{stats['found_and_ready']}]")
                    print(f"🎬 حلقة: {ep['episode_number']} (ID: {ep['id']})")
                    print(f"📦 الملف: {filename}")
                    print(f"🔗 لولو: {lulu_embed_url}")
                    print("-" * 30)
                except Exception as e:
                    print(f"❌ فشل حقن الحلقة {ep['episode_number']}: {e}")
                    stats["errors"] += 1
                
            else:
                stats["not_found_on_lulu"] += 1
                print(f"⚠️ لم يتم العثور على [{clean_name}] في لولو.")

        except Exception as e:
            stats["errors"] += 1
            print(f"❌ خطأ أثناء البحث عن {clean_name}: {e}")

    # --- الطباعة النهائية للملخص ---
    print("\n" + "="*40)
    print("📊 ملخص العملية النهائي:")
    print("="*40)
    print(f"✅ ملفات جاهزة للحقن:     {stats['found_and_ready']}")
    print(f"⏭️ ملفات موجودة سابقاً:   {stats['already_exists']}")
    print(f"🔎 ملفات لم يعثر عليها:   {stats['not_found_on_lulu']}")
    print(f"⚠️ أخطاء تقنية:          {stats['errors']}")
    print("="*40)
    print("🏁 انتهت المهمة.")

if __name__ == "__main__":
    harvest_links_smartly()