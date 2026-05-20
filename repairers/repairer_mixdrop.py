import os
import time
import requests
from datetime import datetime
from urllib.parse import quote
from shared import supabase, log

# --- الإعدادات البيئية للمنظومة الجديدة ---
MIXDROP_EMAIL = os.environ.get("MIXDROP_EMAIL")
MIXDROP_KEY = os.environ.get("MIXDROP_KEY")
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "5"))

TARGET_SERVER = "mixdrop"
SOURCE_SERVERS = ["archive", "telegram_direct", "streamtape", "lulustream"]

def is_archive_url_valid(url: str) -> bool:
    """يفحص بذكاء وسرعة سلامة سورس آرشيف باستخدام طلبات جزئية دون تحميل الفيلم وبآلية التأكيد المزدوج"""
    if "archive.org" not in url:
        return True
        
    url = str(url).strip()
    if "disabled" in url.lower() or not url.startswith("http"):
        log(f"   ❌ [Source] رابط تالف أو ملغى نصياً في الـ DB.")
        return False

    # هيدر لتحديد حجم البيانات المقروءة (أول 50 كيلوبايت فقط) لمنع تحميل الفيلم كاملاً في الذاكرة
    headers = {"Range": "bytes=0-50000"}

    try:
        log(f"   🔎 [Source] جاري فحص سلامة سورس آرشيف المختار...")
        
        # 1. الفحص السريع الأول عبر HEAD للتأكد من الكود (403/404)
        resp = requests.head(url, timeout=7.0, verify=False, allow_redirects=True)
        status = resp.status_code

        # لو رجع 200، نقرأ المحتوى النصي بشكل جزئي جداً عبر GET مع Range
        if status == 200:
            resp = requests.get(url, headers=headers, timeout=7.0, verify=False, allow_redirects=True)
            status = resp.status_code

        is_dead = False
        page_content = resp.text.lower() if status == 200 else ""

        if status in [403, 404] or (status == 200 and ("item not available" in page_content or "disabled" in page_content)):
            is_dead = True

        # 2. جدار الحماية (التأكيد المزدوج) لمنع خسارة الروابط بسبب سقوط آرشيف اللحظي
        if is_dead:
            log(f"   ⚠️ [Source] اشتباه بموت رابط آرشيف، جاري إعادة التأكيد بعد 3 ثوانٍ...")
            time.sleep(3)

            retry_resp = requests.head(url, timeout=7.0, verify=False, allow_redirects=True)
            retry_status = retry_resp.status_code
            
            if retry_status == 200:
                retry_resp = requests.get(url, headers=headers, timeout=7.0, verify=False, allow_redirects=True)
                retry_status = retry_resp.status_code

            retry_content = retry_resp.text.lower() if retry_status == 200 else ""

            if retry_status in [403, 404] or (retry_status == 200 and ("item not available" in retry_content or "disabled" in retry_content)):
                log(f"   ❌ [Source] تم تأكيد موت الرابط أو حذفه نهائياً من آرشيف.")
                return False
            else:
                log(f"   🛡️ [Source] الرابط عاد للعمل في المحاولة الثانية، الرابط سليم.")
                return True

        return True

    except Exception as e:
        log(f"   ⚠️ [Source] خطأ شبكة عابر أثناء فحص الرابط: {e}")
        # في سكريبت الإنقاذ، نفضل إرجاع True عند حدوث خطأ اتصال غامض لكي نعطي المحرك فرصة لمحاولة الرفع بدلاً من تخطي الحلقة
        return True


def rescue_mixdrop_mission():
    now = datetime.now().strftime("%H:%M:%S")
    log(f"🚀 [{now}] بدء مهمة الإنقاذ الذكية لسيرفر: {TARGET_SERVER.upper()}")

    # 1. الاستهداف الدقيق: جلب الروابط المعطوبة حتماً من الفاحص
    response = (
        supabase.table("links")
        .select("id, episode_id, url, server_name")
        .ilike("server_name", f"%{TARGET_SERVER}%")
        .eq("last_check_status", "broken")
        .eq("error_message", "404_DELETED")
        .eq("is_fixed", False)
        .limit(BATCH_SIZE)
        .execute()
    )

    links_to_repair = response.data or []
    log(f"   📥 تم العثور على {len(links_to_repair)} رابط مكسور وجاهز للإصلاح.")

    if not links_to_repair:
        return

    count_success = 0

    for link in links_to_repair:
        link_id = link["id"]
        ep_id = link["episode_id"]

        # 2. جلب السيرفرات البديلة السليمة والـ valid لنفس الحلقة
        sources_res = (
            supabase.table("links")
            .select("server_name, url")
            .eq("episode_id", ep_id)
            .eq("last_check_status", "valid")
            .execute()
        )
        existing_links = sources_res.data or []

        # تنظيم المصادر المتاحة للرفع
        available_sources = {
            l["server_name"].lower(): l["url"]
            for l in existing_links
            if l["server_name"].lower() in SOURCE_SERVERS
        }

        # ترتيب المصادر حسب الأولوية: أرشيف > تليجرام > ستريم تاب > لولو
        sorted_sources = []
        if "archive" in available_sources:
            sorted_sources.append("archive")

        t_links = [
            l for l in existing_links if "telegram_direct" in l["server_name"].lower()
        ]
        if t_links:
            sorted_sources.append("telegram_direct")

        if "streamtape" in available_sources:
            sorted_sources.append("streamtape")

        if "lulustream" in available_sources:
            sorted_sources.append("lulustream")

        if not sorted_sources:
            log(
                f"⚠️ حلقة ID {ep_id}: لا يوجد أي مصدر شغال ومتاح لإعادة الرفع منه. تخطي."
            )
            continue

        # === التعديل الجديد: التحقق من السورس المختار والالتفاف التلقائي ===
        log(f"🔍 فحص حلقة ID: {ep_id} | المصادر المرتبة المتاحة: {sorted_sources}")
        is_rescued = False

        # جلب المصدر الأول المختار مبدئياً بناءً على الترتيب
        primary_source_key = sorted_sources[0]
        source_url = (
            available_sources.get(primary_source_key)
            if primary_source_key != "telegram_direct"
            else t_links[0]["url"]
        )
        log(f"   ✅ [Source] السورس الأولي المختار: [{primary_source_key}] → {source_url}")

        # إذا كان الاختيار الأول هو آرشيف، نقوم بفحصه مسبقاً
        if primary_source_key == "archive":
            if not is_archive_url_valid(source_url):
                log(f"   ❌ [Source] رابط Archive تالف ومحذوف! جاري البحث عن البديل التالي...")
                
                # البحث عن بديل تليجرام أو السيرفر التالي في القائمة المرتبة
                fallback_key = next((k for k in sorted_sources if k != "archive"), None)
                if fallback_key:
                    primary_source_key = fallback_key
                    source_url = (
                        available_sources.get(fallback_key)
                        if fallback_key != "telegram_direct"
                        else t_links[0]["url"]
                    )
                    log(f"   ✅ [Source] تم التحويل تلقائياً للسورس البديل: [{primary_source_key}] → {source_url}")
                else:
                    log(f"   ❌ [Source] رابط Archive ميت ولا يوجد أي سورس بديل آخر في الداتابيز.")
                    continue

        # تحويل حلقة الـ loops لتعتمد على السورس النظيف النهائي المستقر عليه
        active_sources = [primary_source_key]

        for source_key in active_sources:
# ===================================================================

            for attempt in range(1, 4):
                log(
                    f"   📡 محاولة [{attempt}/3] للرفع باستخدام مصدر: [{source_key}]..."
                )

                try:
                    # إرسال طلب الـ Remote Upload إلى الـ API
                    api_url = f"https://api.mixdrop.ag/remoteupload?email={MIXDROP_EMAIL}&key={MIXDROP_KEY}&url={quote(source_url)}"
                    upload_res = requests.get(api_url, timeout=30).json()

                    if upload_res.get("success"):
                        remote_id = upload_res["result"]["id"]

                        # 3. تعديل الـ Polling لحماية مهلة الأكشن (15 محاولة × 20 ثانية)
                        # 3. نظام فحص مرن (60 محاولة × 15 ثانية = 15 دقيقة كحد أقصى للحلقات الكبيرة)
                        is_verified = False
                        for check_attempt in range(1, 61):
                            time.sleep(15)

                            status_url = f"https://api.mixdrop.ag/remotestatus?email={MIXDROP_EMAIL}&key={MIXDROP_KEY}&id={remote_id}"
                            try:
                                status_res = requests.get(status_url, timeout=20).json()
                            except Exception as check_err:
                                log(
                                    f"   ⚠️ خطأ مؤقت في فحص الحالة: {str(check_err)}.. سأحاول مجدداً"
                                )
                                continue

                            if status_res.get("success"):
                                status_info = status_res["result"]
                                result_status = status_info.get(
                                    "status"
                                )  # القيم: Complete, Downloading, Queued, Error

                                if result_status == "Complete":
                                    file_code = status_info.get("fileref")
                                    final_url = f"https://mixdrop.ag/e/{file_code}"
                                    log(
                                        f"   🎉 تم اكتمال الرفع والمعالجة بنجاح! الرابط: {final_url}"
                                    )

                                    # 4. التحديث الحتمي للرابط القديم التالف
                                    supabase.table("links").update(
                                        {
                                            "url": final_url,
                                            "last_check_status": "valid",
                                            "error_message": None,
                                            "is_fixed": True,
                                            "last_check_at": datetime.now().isoformat(),
                                        }
                                    ).eq("id", link_id).execute()

                                    is_verified = True
                                    break
                                elif result_status == "Error":
                                    log(
                                        "   ❌ ميكس دروب أبلغ عن فشل نهائي في سحب هذا الرابط."
                                    )
                                    break
                                else:
                                    # يطبع الحالة الحالية سواء كانت Queued أو Downloading مع النسبة إن وجدت
                                    log(
                                        f"   ⏳ الحالة الحالية: {result_status} ({check_attempt}/60)..."
                                    )

                        if is_verified:
                            is_rescued = True
                            count_success += 1
                            break
                    else:
                        log(f"   ⚠️ ميكس دروب رفض الطلب: {upload_res.get('msg')}")

                except Exception as e:
                    log(f"   ❌ خطأ تقني في الاتصال: {str(e)}")

                if not is_rescued:
                    time.sleep(5)

            if is_rescued:
                break

        # 5. تقليص وقت التهدئة لحماية وقت الـ Workflow
        time.sleep(5)

    log(
        f"✨ المهمة انتهت! تم إنقاذ وتحديث {count_success} روابط تالفة لـ MixDrop بنجاح."
    )


if __name__ == "__main__":
    rescue_mixdrop_mission()
