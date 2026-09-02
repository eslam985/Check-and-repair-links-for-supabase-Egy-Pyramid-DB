import os
import json
import requests
import time
import base64
import zipfile
from datetime import datetime
from supabase import create_client, Client
# /Check-and-repair-links-for-supabase-Egy-Pyramid-DB/backup/dbBackup/backup.py
# --- إعدادات البيئة (تيجي من GitHub Secrets) ---
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
BOT_TOKEN = os.environ["BOT_TOKEN_EGY_UPLOADER"]
TELEGRAM_DESTINATION = os.environ["TELEGRAM_DESTINATION"]
GITHUB_TOKEN = os.environ["GH_BACKUP_TOKEN"]
GITHUB_REPO = os.environ["GITHUB_REPO"] 
# --- تهيئة Supabase ---
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def fetch_all(table_name):
    all_data = []
    start = 0
    page_size = 1000
    while True:
        stop = start + page_size - 1
        response = supabase.table(table_name).select("*").range(start, stop).execute()
        batch = response.data
        if not batch:
            break
        all_data.extend(batch)
        if len(batch) < page_size:
            break
        start += page_size
    return all_data


def upload_to_github(file_path, file_name):
    """يرفع الملف إلى مستودع GitHub مع فرز آلي داخل مجلدات السنة والشهر الحاليين"""
    
    # 1) استخراج السنة والشهر من تاريخ اليوم تلقائياً
    current_year = datetime.now().strftime("%Y")       # النتيجة مثلاً: 2026
    current_month = datetime.now().strftime("%m-%B")   # النتيجة مثلاً: 05-May
    
    # 2) بناء المسار الديناميكي الموجه لجيتهاب
    folder_path = f"all_backups/{current_year}/{current_month}"
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{folder_path}/{file_name}"
    
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }

    with open(file_path, "rb") as f:
        content = base64.b64encode(f.read()).decode("utf-8")

    # فحص ما إذا كان الملف مرفوعاً مسبقاً لجلب الـ SHA (حماية إضافية)
    sha = None
    check = requests.get(url, headers=headers)
    if check.status_code == 200:
        sha = check.json().get("sha")

    payload = {
        "message": f"Backup: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "content": content,
    }
    if sha:
        payload["sha"] = sha

    response = requests.put(url, json=payload, headers=headers)
    if response.status_code in [200, 201]:
        print(f"✅ تم الرفع بنجاح داخل المجلد المنظم: {folder_path}/{file_name}")
        return True
    else:
        print(f"❌ فشل رفع GitHub: {response.json().get('message')}")
        return False
    


def send_to_telegram(zip_path, caption, retries=3):
    """يرسل الملف لتليجرام مع إعادة المحاولة عند انقطاع الشبكة"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    for attempt in range(1, retries + 1):
        try:
            with open(zip_path, "rb") as doc:
                response = requests.post(
                    url,
                    files={"document": doc},
                    data={
                        "chat_id": TELEGRAM_DESTINATION,
                        "caption": caption,
                        "parse_mode": "Markdown",
                    },
                    timeout=(15, 90)
                )
            if response.status_code == 200:
                print("✅ تم الإرسال لتليجرام بنجاح.")
                return response
            print(f"⚠️ فشلت محاولة تلجرام ({attempt}/{retries}) - كود الاستجابة: {response.status_code}")
        except Exception as e:
            print(f"⚠️ فشلت محاولة تلجرام ({attempt}/{retries}) بسبب خطأ شبكة: {e}")
            if attempt == retries:
                raise e
            time.sleep(3)


def backup_and_notify():
    tables_to_backup = [
        "medias",
        "episodes",
        "links",
        "seasons",
        "genres",
        "media_genres",
        "download_tasks",
    ]

    print(f"🔍 جاري سحب البيانات لـ {len(tables_to_backup)} جداول...")

    all_backup_data = {}
    stats_dict = {}
    summary_parts = []

    for table in tables_to_backup:
        data = fetch_all(table)
        all_backup_data[table] = data
        stats_dict[table] = len(data)
        summary_parts.append(f"{table}: {len(data)}")
        print(f"  ✅ {table}: {len(data)} سجل")

    backup_dict = {
        "backup_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "stats": stats_dict,
        "data": all_backup_data,
    }

    # --- إعداد الملفات ---
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_name = f"full_backup_{timestamp}.json"
    zip_name = f"full_backup_{timestamp}.zip"
    json_path = f"/tmp/{json_name}"
    zip_path = f"/tmp/{zip_name}"

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(backup_dict, f, ensure_ascii=False, indent=2)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        zipf.write(json_path, arcname=json_name)

    zip_size_mb = os.path.getsize(zip_path) / (1024 * 1024)
    print(f"📦 حجم الملف: {zip_size_mb:.2f} MB")

    # --- الرفع لـ GitHub ---
    upload_to_github(zip_path, zip_name)

    # --- الإرسال لتليجرام ---
    summary_text = " | ".join(summary_parts)
    total_records = sum(stats_dict.values())
    caption = (
        f"🚀 *Full Backup Success*\n"
        f"📅 Date: `{backup_dict['backup_date']}`\n"
        f"📊 Total: `{total_records} records`\n"
        f"📋 `{summary_text}`\n"
        f"💾 Size: `{zip_size_mb:.2f} MB`"
    )
    send_to_telegram(zip_path, caption)

    print("🎉 تم البيك اب بنجاح!")


if __name__ == "__main__":
    backup_and_notify()