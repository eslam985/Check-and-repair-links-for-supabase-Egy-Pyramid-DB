from dotenv import load_dotenv

load_dotenv()
from shared import supabase, log


def find_missing_archive():
    log("🔍 جاري فحص الحلقات والميديا ومطابقتها مع Archive و Telegram...")

    # 1. جلب كل الحلقات
    episodes_res = (
        supabase.table("episodes").select("id, episode_number, media_id").execute()
    )
    all_episodes = episodes_res.data

    if not all_episodes:
        log("❌ لم يتم العثور على أي حلقات.")
        return

    log(f"📦 إجمالي الحلقات للفحص: {len(all_episodes)}")

    report_list = []

    for ep in all_episodes:
        # 2. فحص الروابط (Archive و Telegram) في خبطة واحدة
        links_res = (
            supabase.table("links")
            .select("server_name")
            .eq("episode_id", ep["id"])
            .in_("server_name", ["archive", "telegram_direct"])
            .execute()
        )

        servers = [str(l["server_name"]).strip().lower() for l in links_res.data]
        has_archive = "archive" in servers
        has_tele = "telegram_direct" in servers

        # 3. لو ناقصه حاجة منهم، سجلها في التقرير
        if not (has_archive and has_tele):
            media_info = (
                supabase.table("medias")
                .select("title, category")
                .eq("id", ep["media_id"])
                .single()
                .execute()
            )
            title = media_info.data["title"] if media_info.data else "Unknown"

            # تحديد الحالة (Status)
            if not has_archive and not has_tele:
                status = "🔴 ضايع (لا أرشيف ولا تليجرام)"
            elif not has_archive:
                status = "🟡 ناقص أرشيف (موجود تليجرام ✅)"
            else:
                status = "🔵 ناقص تليجرام (موجود أرشيف ✅)"

            report_list.append(
                {"name": title, "ep": ep["episode_number"], "status": status}
            )

    # --- عرض النتائج في جدول منظم ---
    if not report_list:
        log("🎉 كلو تمام! كل الأعمل لها روابط Archive و Telegram.")
        return

    print("\n" + "=" * 100)
    print(f"{'الاسم':<40} | {'حلقة':<6} | {'الحالة'}")
    print("-" * 100)

    # ترتيب حسب الحالة (الضايع أولاً) ثم الاسم
    for item in sorted(report_list, key=lambda x: x["status"]):
        print(f"{item['name'][:38]:<40} | {item['ep']:<6} | {item['status']}")

    print("=" * 100)
    log(f"📊 الخلاصة: عندك {len(report_list)} حلقة محتاجة تدخل منك.")


if __name__ == "__main__":
    find_missing_archive()
