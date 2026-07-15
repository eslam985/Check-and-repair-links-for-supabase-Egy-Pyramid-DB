import requests
import time
import os
from datetime import datetime
from urllib.parse import quote
from supabase import create_client

# --- الإعدادات ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

VK_ACCESS_TOKEN = os.getenv("VK_ACCESS_TOKEN")

VK_GROUP_ID = os.getenv("VK_GROUP_ID")
TARGET_SERVER = "vk"  # السيرفر المستهدف للإنقاذ
SOURCE_SERVERS = ["archive", "telegram_direct"]  # تليجرام له الأولوية

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def is_archive_url_valid(url: str) -> bool:
    """يفحص إذا كان رابط آرشيف يحتوي على جملة تفيد بحذفه أو إغلاقه باستخدام requests"""
    if "archive.org" not in url:
        return True
    try:
        print(f"   🔎 [Source] جاري فحص سلامة سورس آرشيف المختار...")
        resp = requests.get(url, timeout=15.0, verify=False)
        if resp.status_code == 404:
            return False
        if resp.status_code == 200 and "Item not available" in resp.text:
            return False
        return True
    except Exception as e:
        print(f"   ⚠️ [Source] خطأ أثناء فحص الرابط: {e}")
        return False    


def rescue_vk_mission():
    now = datetime.now().strftime("%H:%M:%S")
    print(f"🚀 [{now}] بدء مهمة الإنقاذ الذكية لسيرفر: {TARGET_SERVER.upper()}")

    # 1. جلب الحلقات الناقصة في ميكس دروب
    response = (
        supabase.table("episodes")
        .select("id, episode_number, medias(title), links(server_name, url)")
        .execute()
    )

    if not response.data:
        print("❌ لم يتم العثور على بيانات!")
        return

    count_success = 0

    for ep in response.data:
        ep_id = ep["id"]
        existing_links = ep.get("links", [])

        # التأكد إن ميكس دروب مش موجود
        if any(l["server_name"].lower() == TARGET_SERVER for l in existing_links):
            continue

        # تنظيم المصادر المتاحة
        available_sources = {
            l["server_name"].lower(): l["url"]
            for l in existing_links
            if l["server_name"].lower() in SOURCE_SERVERS
        }

        # ترتيب المصادر (الأرشيف أولاً ثم التليجرام)
        sorted_sources = []

        # 1. الأولوية للأرشيف
        if "archive" in available_sources:
            sorted_sources.append("archive")

        # 2. ثم التليجرام كخيار ثاني
        t_links = [
            l for l in existing_links if "telegram_direct" in l["server_name"].lower()
        ]
        if t_links:
            sorted_sources.append("telegram_direct")

        if not sorted_sources:
            continue

        # === التعديل الجديد: التحقق من السورس المختار والالتفاف التلقائي ===
        now = datetime.now().strftime("%H:%M:%S")
        print(f"\n[{now}] 🔍 فحص حلقة ID: {ep_id} | المصادر المتاحة: {sorted_sources}")
        is_rescued = False

        # جلب المصدر الأول المختار مبدئياً بناءً على الترتيب (آرشيف غالباً)
        primary_source_key = sorted_sources[0]
        source_url = (
            available_sources.get(primary_source_key)
            if primary_source_key != "telegram_direct"
            else t_links[0]["url"]
        )
        print(f"   ✅ [Source] السورس الأولي المختار: [{primary_source_key}] → {source_url}")

        # إذا كان الاختيار الأول هو آرشيف، نقوم بفحصه مسبقاً قبل بدء عملية الرفع والضخ لـ VK
        if primary_source_key == "archive":
            if not is_archive_url_valid(source_url):
                print(f"   ❌ [Source] رابط Archive تالف ومحذوف! جاري البحث عن البديل التالي...")
                
                # البحث عن بديل تليجرام في القائمة المرتبة
                fallback_key = next((k for k in sorted_sources if k != "archive"), None)
                if fallback_key:
                    primary_source_key = fallback_key
                    source_url = (
                        available_sources.get(fallback_key)
                        if fallback_key != "telegram_direct"
                        else t_links[0]["url"]
                    )
                    print(f"   ✅ [Source] تم التحويل تلقائياً للسورس البديل: [{primary_source_key}] → {source_url}")
                else:
                    print(f"   ❌ [Source] رابط Archive ميت ولا يوجد أي سورس بديل آخر لهذه الحلقة.")
                    continue

        # حصر التكرار على السورس المستقر والنظيف النهائي
        active_sources = [primary_source_key]

        for source_key in active_sources:
# ===================================================================

            # محاولات الرفع (3 محاولات لكل مصدر)
            for attempt in range(1, 4):
                print(f"📡 محاولة [{attempt}/3] باستخدام: [{source_key}]...")

                try:
                    # 1. إرسال طلب الـ Remote Upload
                    # 1. حجز مكان للفيديو في VK
                    save_url = "https://api.vk.com/method/video.save"
                    save_params = {
                        "name": f"Episode {ep_id}",
                        "group_id": VK_GROUP_ID, # رجعنا الجروب عادي لأننا هنرفع مش هنبعت رابط
                        "access_token": VK_ACCESS_TOKEN,
                        "v": "5.131",
                    }
                    upload_res = requests.get(save_url, params=save_params).json()

                    if "response" in upload_res:
                        video_id = upload_res["response"]["video_id"]
                        owner_id = upload_res["response"]["owner_id"]
                        upload_url = upload_res['response']['upload_url']
                        
                        print(f"   📡 [VK] تم الحجز. جاري ضخ الفيديو من المصدر إلى VK مباشرة...")
                        
                        # --- مرحلة الضخ المباشر (Stream) ---
                        try:
                            with requests.get(source_url, stream=True, timeout=60) as r_source:
                                if r_source.status_code == 200:
                                    files = {'video_file': (f"ep_{ep_id}.mp4", r_source.raw, 'video/mp4')}
                                    # نرسل الملف لـ VK وننتظر انتهاء النقل
                                    requests.post(upload_url, files=files, timeout=600)
                                    print("   ✅ انتهى ضخ البايتات بنجاح. يبدأ الآن فحص المعالجة...")
                                else:
                                    print(f"   ❌ فشل السحب من المصدر (Status: {r_source.status_code})")
                                    continue
                        except Exception as e:
                            print(f"   ❌ خطأ أثناء الضخ: {str(e)}")
                            continue

                        # 2. نظام الـ Hunter (Polling) للتأكد من المعالجة
                        is_verified = False
                        final_url = None
                        video_id = upload_res["response"]["video_id"]
                        owner_id = upload_res["response"]["owner_id"]
                        # 2. نظام الـ Hunter (Polling) للتأكد من المعالجة
                        is_verified = False
                        final_url = None
                        for check_attempt in range(1, 51):  # 20 محاولة فحص
                            time.sleep(30)  # انتظر 30 ثانية بين كل فحص

                            get_url = "https://api.vk.com/method/video.get"
                            get_params = {
                                "videos": f"{owner_id}_{video_id}",
                                "access_token": VK_ACCESS_TOKEN,
                                "v": "5.131",
                            }
                            status_res = requests.get(get_url, params=get_params).json()
                            # دود بيستخدم status كود 200 لما يكون جاهز للعرض، غير كده بيكون لسه بيتحمل أو بيتحول
                            # ... (بعد تعريف status_res)
                            
                            if "response" in status_res and status_res["response"]["items"]:
                                video_data = status_res["response"]["items"][0]
                                embed_url = video_data.get("player")
                                
                                if embed_url:
                                    final_url = embed_url.replace("vk.com", "vkvideo.ru")
                                    # إضافة البارامترات المطلوبة للرابط
                                    connector = "&" if "?" in final_url else "?"
                                    final_url += f"{connector}hd=2&autoplay=0"

                                # 2. التحقق من الاكتمال: لو الـ file_code ظهر، فالفيديو جاهز فوراً
                                # 2. التحقق من الاكتمال
                                if 'final_url' in locals() and final_url:
                                    now = datetime.now().strftime("%H:%M:%S")

                                    # VK لا يوفر حجم الملف في رد الـ polling العادي
                                    print(f"[{now}] ✅ تم القنص بنجاح لـ VK! | الرابط: {final_url}")

                                    # الحفظ في سوبابيز
                                    supabase.table("links").upsert({
                                        "episode_id": ep_id,
                                        "server_name": TARGET_SERVER,
                                        "url": final_url,
                                    }, on_conflict="episode_id, server_name").execute()

                                    is_verified = True
                                    break
                                
                                # 3. حالة الانتظار (لو لسه مخلصش)
                                else:
                                    # في VK، إذا لم يظهر player يعني الفيديو لا يزال قيد السحب أو المعالجة
                                    print(f"⏳ VK يعالج الفيديو حالياً ({check_attempt}/30)...")
                                    
                        if is_verified:
                            is_rescued = True
                            count_success += 1
                            break
                    else:
                        error_msg = upload_res.get("error", {}).get("error_msg", "Unknown Error")
                        print(f"⚠️ VK رفض الطلب: {error_msg}")

                except Exception as e:
                    print(f"❌ خطأ تقني: {str(e)}")

                if not is_rescued:
                    time.sleep(10)  # انتظار بسيط قبل المحاولة التالية

            if is_rescued:
                break

        # تهدئة بين الحلقات
        time.sleep(120)  # 2 دقائق بين كل حلقة لتجنب الحظر

    now = datetime.now().strftime("%H:%M:%S")
    print(
        f"\n✨ [{now}] المهمة انتهت! تم إنقاذ {count_success} مادة لـ {TARGET_SERVER.upper()}."
    )


if __name__ == "__main__":
    rescue_vk_mission()