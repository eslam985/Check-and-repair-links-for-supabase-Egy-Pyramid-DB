import time
from supabase import create_client, Client
import os

# --- الإعدادات (تأكد من صحتها) ---
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
TARGET_SERVER = "lulustream"
SOURCE_SERVERS = ["archive", "telegram_direct"]
OUTPUT_FILE = "lulu_missing_tasks.txt"

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def generate_lulu_tasks():
    print(f"🔍 فحص الحلقات الناقصة لسيرفر: {TARGET_SERVER.upper()}...")

    # 1. جلب البيانات من سوبابيز
    response = (
        supabase.table("episodes")
        .select("id, episode_number, medias(title), links(server_name, url)")
        .execute()
    )

    if not response.data:
        print("❌ لم يتم العثور على بيانات!")
        return

    missing_tasks = []

    for ep in response.data:
        existing_links = ep.get("links", [])

        # التأكد إن لولو مش موجود في الحلقة دي
        if any(l["server_name"].lower() == TARGET_SERVER for l in existing_links):
            continue

        # استخراج المصادر المتاحة
        sources = {
            l["server_name"].lower(): l["url"]
            for l in existing_links
            if l["server_name"].lower() in SOURCE_SERVERS
        }

        if not sources:
            continue

        media_title = ep.get("medias", {}).get("title", "Unknown")
        ep_num = ep.get("episode_number", 0)

        # إضافة المهمة للقائمة
        missing_tasks.append(
            {
                "title": media_title,
                "episode": ep_num,
                "archive": sources.get("archive", "N/A"),
                "telegram": sources.get("telegram_direct", "N/A"),
            }
        )

    # 2. كتابة النتائج في ملف تيكست بشكل منظم
    if missing_tasks:
        with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
            f.write(
                f"=== قائمة الحلقات الناقصة لـ LULU ({len(missing_tasks)} حلقة) ===\n\n"
            )

            for task in missing_tasks:
                f.write(f"🎬 المادة: {task['title']}\n")
                f.write(f"🔢 الحلقة: {task['episode']}\n")

                # إضافة Timestamp للروابط لضمان جلب داتا جديدة من لولو
                t_stamp = int(time.time())

                if task["archive"] != "N/A":
                    sep = "&" if "?" in task["archive"] else "?"
                    f.write(f"🔗 [ARCHIVE]: {task['archive']}{sep}vid={t_stamp}\n")

                if task["telegram"] != "N/A":
                    sep = "&" if "?" in task["telegram"] else "?"
                    f.write(f"🔗 [TELEGRAM]: {task['telegram']}{sep}vid={t_stamp}\n")

                f.write("-" * 50 + "\n")

        print(f"✅ تم الانتهاء! الملف جاهز: {OUTPUT_FILE}")
        print(f"📊 الإجمالي الناقص: {len(missing_tasks)} حلقة.")
    else:
        print("🎉 لا توجد حلقات ناقصة! كل شيء مكتمل.")


if __name__ == "__main__":
    generate_lulu_tasks()
