import os
import httpx
import time
import re
import random, string
import subprocess
from tqdm import tqdm
from supabase import create_client
from internetarchive import upload, get_session

# backup/archive/README_sync_to_archive.md
# --- 1. إعدادات الاتصال (Credentials) ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
IA_ACCESS_KEY = os.environ.get("IA_ACCESS_KEY")
IA_SECRET_KEY = os.environ.get("IA_SECRET_KEY")
# روابط البراند والتمويه
BRAND_VIDEO_URL = "https://archive.org/download/logo_egypyramid/vid_Brand.mp4"
BRAND_FILE = "brand_intro.mp4"

# الرابط المعدل (شفافية 60% وحجم 70)
LOGO_URL = "https://res.cloudinary.com/dbahqgo8j/image/upload/q_auto,f_auto,w_70,h_70,c_fill,r_max/blogger/logo.webp"
LOGO_FILE = "watermark.webp"
# إنشاء جلسة أرشيف وسوبابيز
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
ia_session = get_session(
    config={"s3": {"access": IA_ACCESS_KEY, "secret": IA_SECRET_KEY}}
)

# 2. إنشاء معرف الرابط الذكي (إضافة بادئة عشوائية لكسر الحظر)
rand_pref = "".join(random.choices(string.ascii_lowercase + string.digits, k=3))


def normalize_title(title):
    if not title:
        return ""
    # 1. تحويل للأحرف الصغيرة
    t = str(title).lower()
    # 2. إزالة الرموز والكلمات الزائدة الشائعة
    t = re.sub(r"[^a-zA-Z0-9\u0600-\u06FF\s]", " ", t)
    # 3. إزالة الكلمات التي لا تعبر عن جوهر العمل
    stop_words = [
        "مسلسل",
        "فيلم",
        "مترجم",
        "مدبلج",
        "كامل",
        "حصريا",
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
    ]
    for w in stop_words:
        # إزالة الكلمة فقط لو كانت مستقلة
        t = re.sub(rf"\b{w}\b", " ", t)

    # 4. توحيد المسافات
    t = " ".join(t.split())
    return t


def get_missing_archive_links():
    # بنعمل Join بسيط أو نسحب البيانات بالترتيب
    res = (
        supabase.table("links")
        .select("id, url, episode_id")
        .like("url", "%hf.space%")
        .execute()
    )
    links = res.data or []

    missing = []
    for l in links:
        check = (
            supabase.table("links")
            .select("id")
            .eq("episode_id", l["episode_id"])
            .like("url", "%archive.org%")
            .execute()
        )
        if not check.data:
            # نسحب اسم الميديا ورقم الحلقة هنا
            # تعديل السطر ده عشان يسحب الـ media_id كمان
            ep_res = (
                supabase.table("episodes")
                .select("episode_number, media_id, medias(title)")
                .eq("id", l["episode_id"])
                .single()
                .execute()
            )
            if ep_res.data:
                l["title"] = ep_res.data["medias"]["title"]
                l["ep_num"] = ep_res.data["episode_number"]
                l["media_id"] = ep_res.data["media_id"]  # <--- إضافة ده
                missing.append(l)
    return missing


def download_file(url, filename, max_retries=5):  # عدل الرقم لـ 5
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"
    }
    for attempt in range(1, max_retries + 1):
        try:
            print(f"📥 جاري التحميل (محاولة {attempt} من {max_retries}): {url}")

            # --- السطر الجديد للصحصحة (أضفه هنا) ---
            httpx.get(url, headers=headers, timeout=5)
            time.sleep(2)

            # عدل هذا السطر لإضافة headers
            with httpx.stream(
                "GET", url, headers=headers, follow_redirects=True, timeout=None
            ) as r:
                # التأكد من أن الرابط شغال (Status 200)
                r.raise_for_status()

                total = int(r.headers.get("Content-Length", 0))
                with open(filename, "wb") as f, tqdm(
                    total=total, unit="B", unit_scale=True, desc=filename, leave=False
                ) as bar:
                    for data in r.iter_bytes():
                        f.write(data)
                        bar.update(len(data))

            print(f"✅ اكتمل التحميل بنجاح في المحاولة رقم {attempt}")
            return True  # الخروج من الدالة بنجاح

        except (httpx.HTTPError, IOError) as e:
            print(f"⚠️ فشلت المحاولة {attempt}: {str(e)}")
            if attempt < max_retries:
                wait_time = attempt * 5  # انتظر 5 ثواني ثم 10 ثم 15
                print(f"⏳ سأحاول مرة أخرى بعد {wait_time} ثوانٍ...")
                time.sleep(wait_time)
            else:
                print(f"❌ استنفدت جميع المحاولات ({max_retries}) لهذا الرابط.")
                raise e  # نرفع الخطأ للمستوى الأعلى (run_sync) عشان يتخطى الحلقة


def upload_to_ia(filename, identifier, title):
    """نسخة محسنة للرفع مع معالجة الأخطاء والـ Cleanup"""
    print(f"📤 جاري الرفع للأرشيف: {identifier}")

    md = {
        "title": title,
        "mediatype": "movies",
        "collection": "opensource_movies",
        "description": f"Data Archive {rand_pref}",
    }

    try:
        # المكتبة هنا بتتكفل بالـ Retries والـ Resuming داخلياً
        r = upload(
            identifier,
            files=[filename],
            metadata=md,
            access_key=IA_ACCESS_KEY,
            secret_key=IA_SECRET_KEY,
            verbose=True,
            retries=5,  # زودنا المحاولات لـ 5 لزيادة الأمان
            verify=True,  # التأكد من سلامة الملف بعد الرفع (Checksum)
        )

        # التأكد من أن الملف الأول في القائمة تم رفعه بنجاح
        if r and r[0].status_code == 200:
            print(f"✅ تم الرفع والتحقق من سلامة الملف بنجاح.")
            return f"https://archive.org/details/{identifier}"
        else:
            print(
                f"⚠️ السيرفر رد بكود غير متوقع: {r[0].status_code if r else 'No Response'}"
            )

    except Exception as e:
        print(f"❌ خطأ فادح أثناء الرفع للأرشيف: {str(e)}")

    return None


def run_sync():
    # تحميل فيديو البراند مرة واحدة فقط لو مش موجود
    if not os.path.exists(BRAND_FILE):
        print("🎬 جاري تجهيز فيديو البراند للتمويه...")
        download_file(BRAND_VIDEO_URL, BRAND_FILE)
    if not os.path.exists(LOGO_FILE):
        print("🖼️ جاري تجهيز اللوجو الثابت...")
        download_file(LOGO_URL, LOGO_FILE)

    tasks = get_missing_archive_links()
    total_tasks = len(tasks)  # إجمالي الـ 39 حلقة
    print(f"🚀 تم العثور على {total_tasks} حلقة للمزامنة.")

    # enumerate بيدينا رقم العملية الحالية (index) بيبدأ من 0
    for index, task in enumerate(tasks, start=1):
        link_id = task["id"]
        tg_url = task["url"]
        ep_id = task["episode_id"]

        remaining = total_tasks - index  # حساب المتبقي

        # --- الجزء المستبدل بالمعرف الهيكلي الذكي (Structured Identifier) ---
        raw_title = task.get("title", "Unknown")
        ep_num = str(task.get("ep_num", "0"))  # تحويل لنص لضمان المقارنة
        media_id = task.get("media_id", "0")
        link_id = task.get("id", "0")

        # 1. تنظيف الاسم للعرض
        clean_name = normalize_title(raw_title)

        # --- منطق التمييز الاحترافي ---
        # الأفلام غالباً رقم حلقتها 0 أو 1 أو 100 (في بعض أنظمة الأرشفة)
        # المسلسلات بتبدأ من 1 لكن لو الاسم "Tron" ورقم 1 يبقى ده فيلم
        if ep_num in ["0", "1"]:
            display_title = clean_name
        else:
            display_title = f"{clean_name} - حلقة {ep_num}"

        # 2. إنشاء معرف الرابط الثابت
        ia_identifier = f"ep{rand_pref}_{media_id}_{ep_id}_{link_id}"

        temp_file = f"video_{ep_id}.mp4"

        print(f"\n" + "=" * 60)
        print(f"📦 العمل {index}/{total_tasks}: {display_title}")
        print(f"🔗 الروابط: {ia_identifier} | ⏳ المتبقي: {remaining}")
        print("=" * 60)
        # --- نهاية الجزء المستبدل ---

        try:
            # 1. تحميل الفيلم الأصلي
            download_file(tg_url, temp_file)

            # --- [ مطحنة التمويه العبقرية ] ---
            disguised_file = f"{rand_pref}_{temp_file}"
            print(f"🕵️ جاري تطبيق التمويه (دمج البراند + كسر البصمة)...")
            bidi_text = "To see more, please search on Google for EGY PYRAMID"

            def get_duration(file):
                cmd = f'ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "{file}"'
                return float(subprocess.check_output(cmd, shell=True))

            duration = get_duration(temp_file)
            mid_time = duration / 2
            ffmpeg_cmd = (
                f'ffmpeg -y -i "{temp_file}" -i "{LOGO_FILE}" -filter_complex '
                f'"[0:v]scale=iw*1.05:-1,crop=iw/1.05:ih/1.05,eq=gamma=1.05:contrast=1.03[v_final]; '
                # اللوجو النصي (أول 10 ثواني)
                f"[v_final]drawtext=text='EGY PYRAMID':fontcolor=0xFFD700:fontsize=80:x=(w-text_w)/2:y=(h-text_h)/2:enable='between(t,0,10)'[txt1]; "
                # النص العربي (منتصف الفيلم)
                f"[txt1]drawtext=text='{bidi_text}':fontfile=/content/arial.ttf:fontcolor=0xFFD700:fontsize=w/35:x=(w-text_w)/2:y=h-th-40:"
                f"enable='between(t,{mid_time},{mid_time+10})'[txt2]; "
                # سطر التحكم في شفافية اللوجو الصوري
                f"[1:v]format=rgba,colorchannelmixer=aa=1.0[logo_bright]; "
                f"[txt2][logo_bright]overlay=W-w-20:20[outv]"
                f'" '  # قفلنا الفلتر كومبلكس هنا
                f'-map "[outv]" -map 0:a '  # سحبنا الصوت الأصلي (0:a) كما هو لضمان التزامن 100%
                f"-c:v libx264 -preset superfast -crf 24 -maxrate 2.1M -bufsize 4.2M -pix_fmt yuv420p "
                f'-c:a aac -b:a 128k -ar 44100 "{disguised_file}"'
            )

            os.system(ffmpeg_cmd)

            # تبديل الملف القديم بالملف المموه للرفع
            if os.path.exists(disguised_file):
                os.remove(temp_file)  # مسح الأصلي
                temp_file = disguised_file  # اعتماد المموه
                print("✅ تم التمويه بنجاح. الفيلم الآن جاهز للاختراق!")
            # --- [ نهاية التمويه ] ---

            # 2. رفع (الملف المموه الآن)
            ia_url = upload_to_ia(temp_file, ia_identifier, display_title)

            if ia_url:
                # 3. تحديث سوبابيز
                direct_download_url = (
                    f"https://archive.org/download/{ia_identifier}/{temp_file}"
                )
                supabase.table("links").insert(
                    {
                        "episode_id": ep_id,
                        "url": direct_download_url,
                        "server_name": "archive",
                        "last_check_status": "valid",
                    }
                ).execute()
                print(f"✅ تم بنجاح: {ia_url}")

            # 4. مسح الملف المؤقت
            if os.path.exists(temp_file):
                os.remove(temp_file)

        except Exception as e:
            print(f"❌ خطأ في الحلقة {ep_id}: {str(e)}")
            if os.path.exists(temp_file):
                os.remove(temp_file)


if __name__ == "__main__":
    run_sync()
