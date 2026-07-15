import requests, os
from urllib.parse import quote, unquote
from supabase import create_client

# --- الإعدادات ---
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


def fix_archive_links():
    print("🔍 جاري جلب روابط الأرشيف...")

    response = (
        supabase.table("links")
        .select("id, url")
        .ilike("url", "%archive.org/%")
        .execute()
    )

    links_to_fix = response.data
    if not links_to_fix:
        return print("✅ لا توجد روابط.")

    for item in links_to_fix:
        link_id, old_url = item["id"], item["url"]

        # --- 🛠️ استخراج الـ Identifier بذكاء ---
        parts = old_url.split("/")
        try:
            if "details" in parts:
                # لو الرابط: archive.org/details/IDENTIFIER
                identifier = parts[parts.index("details") + 1]
            elif "download" in parts:
                # لو الرابط: archive.org/download/IDENTIFIER/file.mp4
                identifier = parts[parts.index("download") + 1]
            else:
                continue

            # تنظيف الـ identifier من أي زيادات
            identifier = identifier.split("?")[0].split("#")[0]

            # --- 2. فحص الـ Metadata ---
            api_url = f"https://archive.org/metadata/{identifier}"
            data = requests.get(api_url, timeout=20).json()

            video_file = next(
                (
                    f["name"]
                    for f in data.get("files", [])
                    if f["name"].lower().endswith(".mp4")
                ),
                None,
            )

            if video_file:
                # 1. التشفير الإجباري (Force Encoding) لكل الرموز والمسافات
                safe_video_file = quote(video_file, safe="")
                new_url = f"https://archive.org/download/{identifier}/{safe_video_file}"

                # 2. طباعة للتأكد في الكونسول قبل الحفظ
                print(f"📡 الرابط الجديد المجهز: {new_url}")

                # 3. تحديث قاعدة البيانات
                if old_url != new_url:
                    result = (
                        supabase.table("links")
                        .update({"url": new_url})
                        .eq("id", link_id)
                        .execute()
                    )

                    # تأكيد الحفظ من رد سوبابيز نفسه
                    if result.data:
                        print(f"✅ تم الحفظ في سوبابيز بنجاح لـ: {identifier}")
                    else:
                        print(f"❌ فشل الحفظ في سوبابيز!")
                else:
                    print(f"🆗 الرابط مطابق تماماً في القاعدة.")

        except Exception as e:
            print(f"❌ خطأ في {old_url}: {e}")


if __name__ == "__main__":
    fix_archive_links()
