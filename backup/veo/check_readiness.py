import os
from dotenv import load_dotenv
load_dotenv() # <--- السطر ده السحري اللي بيقرأ ملف الـ .env

from shared import supabase, log

def get_readiness_report():
    log("📡 [Radar] جاري فحص جاهزية الروابط المكسورة وتحديد المصادر...")
    
    # 1. جلب الروابط المكسورة مع بيانات الحلقة والميديا (JOIN)
    # ملاحظة: بنستخدم SELECT مركب عشان نجيب اسم المسلسل ورقم الحلقة في خبطة واحدة
    res = (
        supabase.table("links")
        .select("""
            id, 
            url, 
            episode_id,
            episodes (
                episode_number,
                medias (title)
            )
        """)
        .ilike("server_name", "%voe%")
        .eq("last_check_status", "broken")
        .eq("is_fixed", False)
        .execute()
    )
    
    broken_links = res.data or []
    if not broken_links:
        log("✅ مفيش أي روابط VOE مكسورة حالياً. كلو تمام!")
        return

    log(f"🔍 تم العثور على {len(broken_links)} رابط مكسور. جاري البحث عن مصادر رفع (Archive/Telegram)...")
    log(f"{'='*100}")
    log(f"{'ID':<6} | {'المسلسل/الفيلم':<25} | {'حلقة':<6} | {'حالة السورس':<20} | {'الرابط المكسور'}")
    log(f"{'='*100}")

    ready_count = 0
    missing_source = 0

    for link in broken_links:
        link_id = link['id']
        old_url = link['url']
        ep_id = link['episode_id']
        
        # استخراج البيانات من الـ JOIN
        ep_data = link.get('episodes') or {}
        media_data = ep_data.get('medias') or {}
        title = media_data.get('title', 'Unknown')
        ep_num = ep_data.get('episode_number', '-')

        # 2. البحث عن سورس (Archive أو Telegram) لنفس الـ episode_id
        source_res = (
            supabase.table("links")
            .select("server_name")
            .eq("episode_id", ep_id)
            .in_("server_name", ["archive", "telegram_direct"])
            .limit(1)
            .execute()
        )
        
        sources = source_res.data
        if sources:
            source_type = sources[0]['server_name']
            status_msg = f"✅ جاهز ({source_type})"
            ready_count += 1
        else:
            status_msg = "❌ ملوش سورس!"
            missing_source += 1

        log(f"{link_id:<6} | {title[:23]:<25} | {ep_num:<6} | {status_msg:<20} | {old_url}")

    log(f"{'='*100}")
    log(f"📊 الخلاصة:")
    log(f"   ✅ روابط جاهزة للإصلاح فوراً: {ready_count}")
    log(f"   ⚠️ روابط محتاجة سورس يدوي:    {missing_source}")
    log(f"{'='*100}")

if __name__ == "__main__":
    get_readiness_report()