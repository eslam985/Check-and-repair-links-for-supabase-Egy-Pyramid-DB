from telethon import TelegramClient, types
from dotenv import load_dotenv
import re
import os
import asyncio
import nest_asyncio
import math

# شحن متغيرات البيئة
load_dotenv()

# --- الإعدادات المستدعاة من الـ .env ---
API_ID = int(os.environ.get("TG_API_ID", 0))
API_HASH = os.environ.get("TG_API_HASH")

# تحويل المعرفات إلى أرقام لأن تليجرام يتوقع معرفات القنوات كـ Int
SOURCE_CHANNEL = int(os.environ.get("TG_SOURCE_CHANNEL", 0))
TARGET_CHANNEL = int(os.environ.get("TG_TARGET_CHANNEL", 0))
SERIES_NAME="إمبراطور البحر"
FINAL_FILE_NAME="مسلسل الكوري إمبراطور البحر مترجم.mp4"
# تشغيل العميل باستخدام اسم جلسة مناسب للسكربت
client = TelegramClient("egy_series_session", API_ID, API_HASH)

async def main():
    async with TelegramClient("beast_session", API_ID, API_HASH) as client:
        print(f"🔍 جاري محاولة الوصول للمصدر: {SOURCE_CHANNEL}...")

        # تعريف القاموس مبدئياً لتجنب أخطاء التنظيف في النهاية
        links_dict = {}

        if os.path.exists("temp_final.mp4"):
            print("✅ الملف موجود! جاري الانتقال لمرحلة الرفع مباشرة...")
        else:
            try:
                source_entity = await client.get_entity(SOURCE_CHANNEL)
                if hasattr(source_entity, "title"):
                    print(f"✅ تم العثور على القناة/المجموعة: {source_entity.title}")
                elif hasattr(source_entity, "first_name"):
                    print(f"✅ تم العثور على المستخدم: {source_entity.first_name}")
                else:
                    print(f"✅ تم العثور على كيان غير معروف: {source_entity}")
            except Exception as e:
                print(f"❌ فشل الوصول للمصدر: {e}")
                return

            print(f"🔍 جاري فحص الرسائل وتحميل الحلقات...")

            async for message in client.iter_messages(source_entity, limit=500):
                if message.file:
                    file_name = message.file.name or ""
                    msg_text = message.text or ""
                    full_content = f"{file_name} {msg_text}"

                    if SERIES_NAME in full_content:
                        pattern = r"الحلقة\s*(\d+)"
                        match = re.search(pattern, full_content)
                        if match:
                            episode_num = int(match.group(1))
                            if episode_num not in links_dict:
                                print(f"✅ تم العثور على: حلقة {episode_num}")
                                path = await client.download_media(
                                    message, file=f"ep_{episode_num}.mp4"
                                )
                                links_dict[episode_num] = path

            if not links_dict:
                print("❌ لم يتم العثور على أي حلقات بالنمط المطلوب!")
                return

            # ترتيب الحلقات وكتابة ملف القائمة
            sorted_episodes = sorted(links_dict.keys())
            with open("list.txt", "w") as f:
                for i in sorted_episodes:
                    f.write(f"file '{links_dict[i]}'\n")

            print(f"🎬 جاري دمج {len(links_dict)} حلقة...")
            os.system('ffmpeg -f concat -safe 0 -i list.txt -c copy "temp_final.mp4"')

        # --- منطقة الرفع الموحدة (تعمل في حالة الـ Skip أو بعد الدمج) ---
        if os.path.exists("temp_final.mp4"):
            file_size_gb = os.path.getsize("temp_final.mp4") / (1024**3)
            print(f"📊 حجم الملف النهائي: {file_size_gb:.2f} GB")

            MAX_SIZE_GB = 1.8
            if file_size_gb > MAX_SIZE_GB:
                num_parts = math.ceil(file_size_gb / 1.7)
                print(f"⚠️ الملف كبير! سيتم تقسيمه إلى {num_parts} أجزاء...")

                import subprocess

                cmd = f"ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 temp_final.mp4"
                total_duration = float(subprocess.check_output(cmd, shell=True))
                part_duration = total_duration / num_parts

                os.system(
                    f"ffmpeg -i temp_final.mp4 -c copy -map 0 -f segment -segment_time {part_duration} -reset_timestamps 1 part_%03d.mp4"
                )

                parts = sorted(
                    [
                        f
                        for f in os.listdir(".")
                        if f.startswith("part_") and f.endswith(".mp4")
                    ]
                )
                for i, part in enumerate(parts, 1):
                    print(f"🚀 رفع الجزء {i}/{len(parts)}...")
                    await client.send_file(
                        TARGET_CHANNEL,
                        part,
                        caption=f"✅ {FINAL_FILE_NAME}\n📦 الجزء ({i}/{len(parts)})",
                        attributes=[],
                    )
                    os.remove(part)
                print(f"✨ تم رفع جميع الأجزاء بنجاح!")
            else:
                print(f"🚀 جاري رفع الملف كاملاً...")
                await client.send_file(
                    TARGET_CHANNEL,
                    "temp_final.mp4",
                    caption=f"✅ تم دمج المسلسل بنجاح\n🎬 {FINAL_FILE_NAME}",
                    attributes=[],
                )
                print(f"✨ تم الإرسال بنجاح!")

            # --- التنظيف النهائي ---
            print("🧹 جاري التنظيف...")
            if os.path.exists("list.txt"):
                os.remove("list.txt")
            if os.path.exists("temp_final.mp4"):
                os.remove("temp_final.mp4")
            for path in links_dict.values():
                if os.path.exists(path):
                    os.remove(path)
        else:
            print("❌ تعذر العثور على الملف النهائي.")


# التشغيل لبيئة Colab
nest_asyncio.apply()
asyncio.run(main())
