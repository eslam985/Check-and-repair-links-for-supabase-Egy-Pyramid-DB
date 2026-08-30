import os
import sys
from huggingface_hub import HfApi

# المسار الخاص بملف .env و اسم الـ Space
ENV_FILE_PATH = os.environ.get('ENV_FILE_PATH') or '.env'

REPO_ID = "egystreamer/egy_sync_to_telegram"

# قائمة المتغيرات السرية المطلوبة للمشروع
REQUIRED_SECRETS = [
    "DOOD_API_KEY",
    "LULUSTREAM_API_KEY",
    "MIXDROP_EMAIL",
    "MIXDROP_KEY",
    "STREAMTAPE_API_KEY",
    "STREAMTAPE_LOGIN",
    "SUPABASE_KEY",
    "SUPABASE_URL",
    "TELEGRAM_API_ID",
    "TELEGRAM_API_ID",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_SESSION",
    "TELEGRAM_TARGET_CHAT",
    "VK_ACCESS_TOKEN",
    "VK_SERVICE_KEY",
    "VOE_API_KEY",
]


def parse_env_file(filepath):
    env_vars = {}
    if not os.path.exists(filepath):
        print(f"❌ لم يتم العثور على الملف في المسار:\n{filepath}")
        sys.exit(1)

    with open(filepath, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip().strip("'\"")
            env_vars[key] = val
    return env_vars


def main():
    print(f"📂 قراءة ملف .env من: {ENV_FILE_PATH}")
    env_vars = parse_env_file(ENV_FILE_PATH)

    # التحقق من وجود المتغيرات المطلوبة وبأن لها قيمة
    missing_secrets = [
        key for key in REQUIRED_SECRETS if key not in env_vars or not env_vars[key]
    ]

    if missing_secrets:
        print(
            "\n❌ تم إيقاف التشغيل! المتغيرات التالية غير موجودة أو تختلف تسميتها في ملف .env:\n"
        )
        for key in missing_secrets:
            print(f" ✖ {key}")
        print(
            "\nيرجى تعديل التسمية داخل ملف .env لتطابق الأسماء المذكورة أعلاه ثم إعادة التشغيل."
        )
        sys.exit(1)

    print("\n✅ تم التحقق: جميع المتغيرات المطلوبة موجودة بنجاح.")

    # جلب HF_TOKEN من النظام أو الملف أو إدخاله يدوياً
    hf_token = os.getenv("HF_TOKEN") or env_vars.get("HF_TOKEN")
    if not hf_token:
        hf_token = input(
            "\n🔑 أدخل Hugging Face Access Token (بصلاحية Write): "
        ).strip()

    if not hf_token:
        print("❌ لم يتم إدخال HF_TOKEN.")
        sys.exit(1)

    api = HfApi(token=hf_token)
    print(f"\n🚀 جاري رفع {len(REQUIRED_SECRETS)} Secret إلى Space ({REPO_ID})...\n")

    for key in REQUIRED_SECRETS:
        value = env_vars[key]
        try:
            api.add_space_secret(repo_id=REPO_ID, key=key, value=value)
            print(f" 🟢 تم رفع: {key}")
        except Exception as e:
            print(f" 🔴 فشل رفع {key}: {e}")

    print(
        "\n🎉 تم رفع جميع الـ Secrets بنجاح! سيعيد Hugging Face بناء وتشغيل الـ Space تلقائياً الان."
    )


if __name__ == "__main__":
    main()
