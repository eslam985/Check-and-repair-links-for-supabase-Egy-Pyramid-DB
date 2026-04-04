import os
import asyncio
import re
from telethon import TelegramClient  # شيلنا StringSession
from dotenv import load_dotenv

load_dotenv()
from shared import supabase, log

# --- الإعدادات ---
API_ID = 30815937
API_HASH = "9f681be16051e6c93f217ab336509fe3"
# --- الإعدادات المحدثة ---
# أضفنا القناة الجديدة للقائمة
# --- الإعدادات المحدثة ---
# ضفنا القناة الجديدة وأي قناة تانية أنت مشترك فيها والملفات بتترفع عليها
# --- الإعدادات المحدثة ---
SOURCE_CHANNELS = [
    'me',                        # ⬅️ ده بيبحث في "الرسائل المحفوظة" (Saved Messages)
    "@EgyPyramid_Uploader_bot",  # البحث في شات البوت
    -1003418621080,              # القناة القديمة
    -1003519403558,              # قناة Egy Pyramid - Database
    -1001389743126               # قناة Movies (لو عايز تضيفها تاني)
]
LINK_BOT_USER = "@EgyPyramid_stream_bot"

# التعديل هنا: هنستخدم اسم ملف 'egy_session' بدل الـ String
# ده هيعمل ملف اسمه egy_session.session في مجلد المشروع
client = TelegramClient("egy_session", API_ID, API_HASH)


def normalize_title(title): 
    if not title:
        return ""
    t = str(title).lower()
    # تنظيف الرموز مع الإبقاء على الحروف والأرقام
    t = re.sub(r"[^a-zA-Z0-9\u0600-\u06FF\s]", " ", t)

    stop_words = [
        "مسلسل",
        "الموسم",
        "السادس",
        "الحلقة",
        "كامل",
        "2025",
        "2026",
        "مترجم",
        "مدبلج",
        "حصري",
        "حصرياً",
        "حصريا",
        "فيلم",
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
    ]  # أضف ما شئت
    for w in stop_words:
        t = re.sub(rf"\b{w}\b", " ", t)

    return " ".join(t.split()).strip()


async def start_telegram_rescue():
    # أول مرة بس هيطلب منك الموبايل والكود في الترمنال
    await client.start()

    if not await client.is_user_authorized():
        log("❌ فشل تسجيل الدخول!")
        return

    log("✅ تم الاتصال بنجاح! جاري فحص النواقص...")

    # جلب النواقص (نفس الكود اللي فات)
    all_episodes = (
        supabase.table("episodes").select("id, episode_number, media_id").execute().data
    )

    for ep in all_episodes:
        # فحص وجود الرابط
        exists = (
            supabase.table("links")
            .select("id")
            .eq("episode_id", ep["id"])
            .eq("server_name", "telegram_direct")
            .execute()
        )
        if exists.data:
            continue

        media = (
            supabase.table("medias")
            .select("title")
            .eq("id", ep["media_id"])
            .single()
            .execute()
            .data
        )
        if not media:
            continue

        # تنظيف الاسم للبحث (أول كلمتين)
        # تنظيف الاسم للبحث
        media_title = normalize_title(media["title"])
        clean_title = re.sub(r"[^a-zA-Z0-9\u0600-\u06FF\s]", " ", str(media_title))
        words = clean_title.split()
        short_name = " ".join(words[:3]) if words else clean_title

        # ذكاء البحث: لو حلقة 1 (فيلم أو بداية مسلسل) ابحث بالاسم بس
        # لو حلقة أعلى من 1 ابحث بالاسم + الرقم
        if ep["episode_number"] == 1:
            search_query = short_name
        else:
            search_query = f"{short_name} {ep['episode_number']}"

        log(f"🔍 البحث عن: {search_query} (حلقة {ep['episode_number']})")

        found_msg = None
        # قائمة احتمالات البحث الذكية
        # قائمة احتمالات البحث الذكية
        search_options = []

        # تنظيف الاسم مع الاحتفاظ بالـ Underscore كخيار بحث
        underscore_name = short_name.replace(" ", "_")

        if ep["episode_number"] == 1:
            search_options = [
                short_name,
                underscore_name,
                f"🎬 {short_name}",
                f"🎬 {underscore_name}",
            ]
        else:
            search_options = [
                f"{short_name} {ep['episode_number']}",
                f"{underscore_name} {ep['episode_number']}",  # هيصطاد: علي_كلاي 25
                f"{underscore_name}_{ep['episode_number']}",  # هيصطاد: علي_كلاي_25
                f"EP{ep['episode_number']}",
                f"الحلقة {ep['episode_number']}",
            ]

        # البحث في كل المصادر (قنوات وبوتات)
        for channel in SOURCE_CHANNELS:
            if found_msg:
                break

            for query in search_options:
                if found_msg:
                    break
                log(f"📡 فحص المصدر {channel} بكلمة: {query}")

                async for message in client.iter_messages(
                    channel, search=query, limit=10
                ):
                    # الشرط الجوهري: لازم يكون فيديو أو ملف فيديو
                    if not (message.video or message.document):
                        continue

                    # لو هو ملف (Document) نأكد إنه فيديو مش صورة أو نص
                    if message.document and not any(
                        ext in (message.file.name or "").lower()
                        for ext in [".mp4", ".mkv", ".avi"]
                    ):
                        if not message.video:
                            continue

                    msg_text = (message.text or message.caption or "").lower()
                    target_num = str(ep["episode_number"])

                    # --- المنطق المطور للتعامل مع "النواقص بدون اسم" ---

                    # 1. التأكد من وجود رقم الحلقة (سواء 20 أو EP20)
                    has_number = any(
                        x in msg_text
                        for x in [
                            target_num,
                            f"ep{target_num}",
                            f"ح {target_num}",
                            f"حلقة {target_num}",
                        ]
                    )

                    # 2. التأكد من الاسم (لو موجود)
                    has_name = words[0].lower() in msg_text if words else False

                    # 3. "الاستثناء الذهبي":
                    # لو المسلسل هو Winter Sonata (أو أي مسلسل إنت عارف إن ملوش اسم في القناة)
                    # بنقبل الرسالة لو فيها رقم الحلقة فقط وجاية من مصدر موثوق
                    is_special_case = (
                        "winter" in short_name.lower() or "sonata" in short_name.lower()
                    )

                    if has_number:
                        if has_name or is_special_case:
                            found_msg = message
                            log(
                                f"🎯 لقطت الفيديو (استثناء خاص لـ {short_name}) من {channel}!"
                            )
                            break

                await asyncio.sleep(0.5)  # سرعة أكبر مع حماية

        if found_msg:
            log(f"🎯 وجدت الرسالة.. توجيه للبوت...")
            await found_msg.forward_to(LINK_BOT_USER)

            await asyncio.sleep(3)  # انتظار رد البوت

            async for reply in client.iter_messages(LINK_BOT_USER, limit=1):
                urls = re.findall(r"(https?://\S+)", reply.text)
                if urls:
                    direct_url = urls[0]
                    supabase.table("links").insert(
                        {
                            "episode_id": ep["id"],
                            "server_name": "telegram_direct",
                            "url": direct_url,
                            "quality": "720p",
                            "link_type": "watch",
                            "last_check_status": "valid",
                        }
                    ).execute()
                    log(
                        f"✅ تم حقن الرابط لـ {media['title']} ح {ep['episode_number']}"
                    )

        await asyncio.sleep(5)  # حماية من الحظر


if __name__ == "__main__":
    client.loop.run_until_complete(start_telegram_rescue())
