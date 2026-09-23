import os
import json
import requests
import base64
import zipfile
import subprocess
from datetime import datetime
import asyncio
from telethon import TelegramClient

# --- إعدادات البيئة ---
# أضف DATABASE_URL في GitHub Secrets أو متغيرات البيئة
DATABASE_URL = os.environ["DATABASE_URL"] 
BOT_TOKEN = os.environ["BOT_TOKEN_EGY_UPLOADER"]
TELEGRAM_DESTINATION = os.environ["TELEGRAM_DESTINATION"]
GITHUB_TOKEN = os.environ["GH_BACKUP_TOKEN"]
GITHUB_REPO = os.environ["GITHUB_REPO"] 

TELEGRAM_API_ID = int(os.environ.get("TELEGRAM_API_ID"))
TELEGRAM_API_HASH = os.environ.get("TELEGRAM_API_HASH")

def run_pg_dump(sql_file_path):
    """تنفيذ أداة pg_dump لإنشاء نسخة احتياطية شاملة بصيغة SQL"""
    print("🔍 جاري سحب النسخة الاحتياطية الشاملة (Database Dump)...")
    try:
        # يمكنك إضافة '--clean' و '--if-exists' إذا أردت أوامر Drop قبل الـ Create
        command = [
            "pg_dump",
            DATABASE_URL,
            "-f", sql_file_path
        ]
        # تشغيل الأمر والانتظار حتى ينتهي
        subprocess.run(command, check=True, text=True, capture_output=True)
        print("✅ تم إنشاء ملف الـ SQL بنجاح.")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ فشل pg_dump: {e.stderr}")
        return False

def upload_to_github(file_path, file_name):
    # نفس الكود الخاص بك بدون تعديل
    current_year = datetime.now().strftime("%Y")
    current_month = datetime.now().strftime("%m-%B")
    folder_path = f"all_backups/full_sql_backups/{current_year}/{current_month}"
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{folder_path}/{file_name}"
    
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }

    with open(file_path, "rb") as f:
        content = base64.b64encode(f.read()).decode("utf-8")

    sha = None
    check = requests.get(url, headers=headers)
    if check.status_code == 200:
        sha = check.json().get("sha")

    payload = {
        "message": f"Full SQL Backup: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "content": content,
    }
    if sha:
        payload["sha"] = sha

    response = requests.put(url, json=payload, headers=headers)
    if response.status_code in [200, 201]:
        print(f"✅ تم الرفع بنجاح داخل المجلد: {folder_path}/{file_name}")
        return True
    else:
        print(f"❌ فشل رفع GitHub: {response.json().get('message')}")
        return False

async def _async_send_to_telegram(zip_path, caption):
    # نفس الكود الخاص بك بدون تعديل
    client = TelegramClient('backup_bot_session', TELEGRAM_API_ID, TELEGRAM_API_HASH)
    try:
        await client.start(bot_token=BOT_TOKEN)
        target_chat = int(TELEGRAM_DESTINATION)
        await client.send_file(
            target_chat,
            zip_path,
            caption=caption,
            parse_mode='md'
        )
        print("✅ تم إرسال النسخة الاحتياطية لتليجرام بنجاح.")
    except Exception as e:
        print(f"❌ فشل الإرسال عبر Telethon: {e}")
    finally:
        await client.disconnect()

def send_to_telegram(zip_path, caption):
    asyncio.run(_async_send_to_telegram(zip_path, caption))

def backup_and_notify():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    sql_name = f"egy_pyramid_full_{timestamp}.sql"
    zip_name = f"egy_pyramid_full_{timestamp}.zip"
    
    # يفضل وضع الملفات في مسار العمل الحالي أو مسار /tmp
    sql_path = f"/tmp/{sql_name}"
    zip_path = f"/tmp/{zip_name}"

    # 1. إنشاء الـ SQL Dump
    if not run_pg_dump(sql_path):
        print("توقف السكريبت بسبب خطأ في سحب قاعدة البيانات.")
        return

    # 2. ضغط الملف لتقليل حجمه (حجم ملف الـ SQL سيكون كبيراً)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        zipf.write(sql_path, arcname=sql_name)

    zip_size_mb = os.path.getsize(zip_path) / (1024 * 1024)
    sql_size_mb = os.path.getsize(sql_path) / (1024 * 1024)
    print(f"📦 حجم ملف SQL الأصلي: {sql_size_mb:.2f} MB")
    print(f"🗜️ حجم الملف بعد الضغط: {zip_size_mb:.2f} MB")

    # 3. الرفع لـ GitHub
    upload_to_github(zip_path, zip_name)

    # 4. الإرسال لتليجرام
    caption = (
        f"🚀 *Full Database SQL Backup Success*\n"
        f"📅 Date: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`\n"
        f"💾 SQL Size: `{sql_size_mb:.2f} MB`\n"
        f"🗜️ Zip Size: `{zip_size_mb:.2f} MB`\n"
        f"🛠️ Contains: Schema, Data, Functions, and Relations"
    )
    send_to_telegram(zip_path, caption)

    # تنظيف الملفات المؤقتة
    if os.path.exists(sql_path): os.remove(sql_path)
    if os.path.exists(zip_path): os.remove(zip_path)

    print("🎉 تم البيك اب بنجاح!")

if __name__ == "__main__":
    backup_and_notify()