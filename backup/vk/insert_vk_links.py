import os
import json
from dotenv import load_dotenv
from supabase import create_client, Client

ENV_PATH = "/media/es/DDrive/projects/apps-python/Check-and-repair-links-for-supabase-Egy-Pyramid-DB/.env"
REPORT_FILE = "dry_run_validation_report.json"
BATCH_SIZE = 100

load_dotenv(dotenv_path=ENV_PATH)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError(f"❌ لم يتم العثور على بيانات الاتصال في: {ENV_PATH}")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def run_insert():
    if not os.path.exists(REPORT_FILE):
        print(f"❌ ملف التقرير غير موجود: {REPORT_FILE}")
        return

    with open(REPORT_FILE, "r", encoding="utf-8") as f:
        report = json.load(f)

    ready_items = report.get("🟢_READY_FOR_INSERT", [])
    if not ready_items:
        print("⚠️ لا توجد عناصر جاهزة للإدخال (🟢_READY_FOR_INSERT خالية).")
        return

    print(f"📦 تم قراءة {len(ready_items)} عنصر جاهز للإدخال...")

    # خط دفاع أخير: جلب جميع روابط VK الحالية من الداتا بيز لحظة التنفيذ
    existing_vk_episodes = set()
    start = 0
    while True:
        res = supabase.table("links").select("episode_id").eq("server_name", "vk").range(start, start + 999).execute()
        data = res.data or []
        if not data: break
        for row in data:
            existing_vk_episodes.add(row["episode_id"])
        if len(data) < 1000: break
        start += 1000

    payload = []
    skipped_existing = 0

    for record in ready_items:
        ep_id = record["episode_id"]
        player_url = record["item"]["player"]

        if ep_id in existing_vk_episodes:
            skipped_existing += 1
            continue

        payload.append({
            "episode_id": ep_id,
            "server_name": "vk",
            "url": player_url
        })
        # إضفاء المعرف للذاكرة المحلية لمنع التكرار داخل نفس الملف
        existing_vk_episodes.add(ep_id)

    if skipped_existing > 0:
        print(f"⚠️ تم تجاوز {skipped_existing} عنصر لوجود رابط VK مسبق لهم في الداتا بيز.")

    if not payload:
        print("🛑 لا توجد روابط جديدة للحقن بعد الفحص الأخير.")
        return

    print(f"🚀 بدء عملية الحقن لعدد {len(payload)} رابط على دفعات ({BATCH_SIZE} عنصر لكل دفعة)...")

    inserted_count = 0
    error_count = 0

    for i in range(0, len(payload), BATCH_SIZE):
        batch = payload[i:i + BATCH_SIZE]
        try:
            res = supabase.table("links").insert(batch).execute()
            inserted_count += len(res.data or [])
            print(f"  ├─ تم إدخال الدفعة {i // BATCH_SIZE + 1} بنجاح ({len(batch)} رابط)")
        except Exception as e:
            error_count += len(batch)
            print(f"  ❌ خطأ في الدفعة {i // BATCH_SIZE + 1}: {e}")

    print("=" * 60)
    print("📊 النتيجة النهائية لعملية الحقن:")
    print(f"├─ 🟢 روابط تم إدخالها بنجاح: {inserted_count}")
    print(f"├─ ⚠️ روابط تم تجنب تكرارها: {skipped_existing}")
    print(f"└─ 🔴 روابط فشل إدخالها: {error_count}")

if __name__ == "__main__":
    run_insert()