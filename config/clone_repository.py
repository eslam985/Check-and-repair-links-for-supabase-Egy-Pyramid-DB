import os
import shutil
import subprocess

# =====================================================================
# ⚙️ منطقة الإعدادات (CONFIGURATION SETTINGS)
# =====================================================================
# اسم المستخدم الخاص بك على جيت هاب
GITHUB_USERNAME = "your_github_username"

# اسم المستودع (Repository) الذي تريد سحبه
REPO_NAME = "your_repo_name"

# الـ Personal Access Token (اتركه فارغاً "" إذا كان المشروع عاماً Public)
ACCESS_TOKEN = "" 

# المسار الذي سيتم حفظ المشروع فيه داخل كولاب
TARGET_DIR = f"/content/{REPO_NAME}"
# =====================================================================


def clear_existing_directory(directory_path: str, repo_name: str) -> None:
    """تنظيف المجلد إذا كان موجوداً مسبقاً لتجنب تداخل الملفات أو فشل الـ Clone"""
    if os.path.exists(directory_path):
        print(f"🔄 المجلد '{repo_name}' موجود بالفعل. جاري حذفه لبدء سحب نظيف...")
        shutil.rmtree(directory_path)


def build_github_url(username: str, repo: str, token: str) -> str:
    """بناء رابط السحب بناءً على صلاحية الوصول (عام أو خاص)"""
    if token.strip():
        return f"https://{token.strip()}@github.com/{username}/{repo}.git"
    return f"https://github.com/{username}/{repo}.git"


def clone_repository() -> None:
    """الدالة الأساسية لإدارة عملية السحب والتحقق من الأخطاء بشكل آمن"""
    clear_existing_directory(TARGET_DIR, REPO_NAME)
    repo_url = build_github_url(GITHUB_USERNAME, REPO_NAME, ACCESS_TOKEN)
    
    print(f"🚀 جاري سحب المشروع '{REPO_NAME}' من جيت هاب...")
    
    try:
        # تشغيل أمر git clone وعزل المخرجات لتجنب طباعة الرابط الذي يحتوي على التوكن
        result = subprocess.run(
            ["git", "clone", repo_url, TARGET_DIR],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        print(f"✅ تم سحب المشروع بنجاح في المسار: {TARGET_DIR}")
        
    except subprocess.CalledProcessError as error:
        print("❌ فشل سحب المشروع. تأكد من صحة البيانات المخدلة (الاسم، الريبو، أو التوكن).")
        
        # حماية أمنية: إخفاء التوكن من رسالة الخطأ إذا ظهرت في سجلات كولاب
        error_message = error.stderr
        if ACCESS_TOKEN and ACCESS_TOKEN in error_message:
            error_message = error_message.replace(ACCESS_TOKEN, "********")
            
        print(f"تفاصيل الخطأ التقني:\n{error_message}")


# بدء التنفيذ
if __name__ == "__main__":
    clone_repository()