import os
import asyncio
import httpx
from datetime import datetime
from supabase import create_client


# سحب البيانات من بيئة التشغيل (GitHub Secrets)
URL = os.getenv("SUPABASE_URL")
KEY = os.getenv("SUPABASE_KEY")

# أضف هذا التحقق فوراً قبل إنشاء الـ client
if not URL or not KEY:
    print(
        "❌ Error: SUPABASE_URL or SUPABASE_KEY is missing from environment variables!"
    )
    # لا تكمل التشغيل إذا كانت البيانات ناقصة
    exit(1)

supabase = create_client(URL, KEY)

VOE_API_KEY = os.getenv("VOE_API_KEY")
LULUSTREAM_API_KEY = os.getenv("LULUSTREAM_API_KEY")
VK_ACCESS_TOKEN = os.getenv("VK_ACCESS_TOKEN")
DOOD_API_KEY = os.getenv("DOOD_API_KEY")
STREAMTAPE_API_KEY = os.getenv("STREAMTAPE_API_KEY")
MIXDROP_API_KEY = os.getenv("MIXDROP_API_KEY")
MIXDROP_EMAIL = os.getenv("MIXDROP_EMAIL")
# تحديد عدد الطلبات المتزامنة (5 كفاية عشان الـ Rate Limit)
sem = asyncio.Semaphore(5)


async def check_voe(client, url, link_id, server_name):
    async with sem:  # الانتظار في الطابور
        try:
            # استخراج الكود (12 حرف بالظبط)
            clean_url = url.strip().rstrip("/")
            if clean_url.endswith("/download"):
                clean_url = clean_url[:-9]
            # الأصح - ياخد كل اللي بعد / من غير قص
            file_code = clean_url.split("/")[-1].split("?")[0]

            # تأخير بسيط جداً بين كل طلب والتاني لضمان الـ Rate Limit
            await asyncio.sleep(0.4)

            api_url = (
                f"https://voe.sx/api/file/info?key={VOE_API_KEY}&file_code={file_code}"
            )
            res = await client.get(api_url, timeout=12.0)
            data = res.json()

            if data.get("success"):
                result = data.get("result", [{}])
                item = result[0] if isinstance(result, list) else result
                status = str(item.get("status"))
                if status == "200":
                    return link_id, "valid", None, server_name, url
                if status == "404":
                    return link_id, "broken", "API: Deleted", server_name, url

            return (
                link_id,
                "broken",
                f"VOE Unknown ({data.get('msg')})",
                server_name,
                url,
            )
        except Exception as e:
            return link_id, "broken", f"VOE Error: {str(e)}", server_name, url


async def check_streamtape(client, url, link_id, server_name):
    """فحص Streamtape الصارم"""
    try:
        file_code = url.split("/e/")[-1].split("/v/")[-1].split("?")[0].split("/")[0]
        login = MIXDROP_EMAIL.split("@")[0]
        api_url = f"https://api.streamtape.com/file/info?key={STREAMTAPE_API_KEY}&login={login}&file={file_code}"

        res = await client.get(api_url, timeout=10.0)
        data = res.json()
        if data.get("status") == 200:
            result_info = data.get("result", {}).get(file_code, {})
            if result_info.get("status") == 200:
                return link_id, "valid", None, server_name, url
        return link_id, "broken", "Streamtape: Deleted", server_name, url
    except Exception as e:
        return link_id, "broken", f"Streamtape Error: {str(e)}", server_name, url


async def check_dood(client, url, link_id, server_name):
    """فحص Doodstream"""
    try:
        file_code = url.split("/e/")[-1].split("/d/")[-1].split("?")[0].split("/")[0]
        api_url = (
            f"https://doodapi.co/api/file/info?key={DOOD_API_KEY}&file_code={file_code}"
        )
        res = await client.get(api_url, timeout=10.0)
        data = res.json()
        if data.get("status") == 200 and data.get("result"):
            return link_id, "valid", None, server_name, url
        return link_id, "broken", "Dood: Deleted", server_name, url
    except Exception as e:
        return link_id, "broken", f"Dood Error: {str(e)}", server_name, url


async def check_generic(client, url, link_id, server_name):
    # كشف روابط تليجرام المبتورة
    if "hf.space" in url and "hash=" not in url:
        return link_id, "broken", "Missing Hash Param", server_name, url

    async with sem:
        # إضافة Referer و User-Agent قوي لمحاكاة المتصفح
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://egypyramid.vercel.app/",
        }
        try:
            # زيادة التايم أوت لـ 20 ثانية عشان Hugging Face
            response = await client.get(
                url, headers=headers, follow_redirects=True, timeout=20.0
            )

            if response.status_code in [200, 206]:
                return link_id, "valid", None, server_name, url

            # محاولة أخيرة لو فشل (Retry)
            await asyncio.sleep(2)
            retry = await client.get(
                url, headers=headers, follow_redirects=True, timeout=15.0
            )
            if retry.status_code in [200, 206]:
                return link_id, "valid", None, server_name, url

            return link_id, "broken", f"HTTP {response.status_code}", server_name, url
        except Exception as e:
            return link_id, "broken", str(e), server_name, url


async def check_link(client, link_id, url, server_name):
    """المايسترو: يوجه كل رابط لدالته الخاصة"""
    server = server_name.lower() if server_name else "unknown"

    # توجيه VOE وروابط التحميل الخاصة به
    if "voe" in server or "down" in server:
        return await check_voe(client, url, link_id, server_name)

    # توجيه Streamtape
    elif "streamtape" in server:
        return await check_streamtape(client, url, link_id, server_name)

    # توجيه Doodstream
    elif "dood" in server:
        return await check_dood(client, url, link_id, server_name)

    # أي شيء آخر (VK, OK, Archive, Telegram)
    else:
        return await check_generic(client, url, link_id, server_name)


async def run_watcher(limit=40):
    print(f"🔍 بدء فحص أقدم {limit} روابط من جدول links...")
    try:
        # سحب الروابط
        # السطر ده بيجبر سوبابيز تجيب اللي ملمسناهوش خالص الأول (NULLS FIRST)
        res = (
            supabase.table("links")
            .select("id, url, server_name")
            .eq("is_fixed", False)
            .order("last_check_at", nulls_first=True)
            .limit(limit)
            .execute()
        )
        links_to_check = res.data

        if not links_to_check:
            print("✅ لا توجد روابط للفحص حالياً.")
            return

        async with httpx.AsyncClient(verify=False) as client:
            tasks = [
                check_link(client, l["id"], l["url"], l.get("server_name", "Unknown"))
                for l in links_to_check
            ]
            results = await asyncio.gather(*tasks)

        # تحديث سوبابيز بالنتائج
        # تحديث سوبابيز بالنتائج
        for link_id, status, error, server_name, url in results:
            try:
                # تحديث الأعمدة الجديدة (تأكد انك شغلت كويري الـ SQL الأول)
                # السطر ده بيزود الـ check_count بواحد في كل مرة بيفحص فيها
                supabase.rpc("increment_check_count", {"row_id": link_id}).execute()

                supabase.table("links").update(
                    {
                        "last_check_status": status,
                        "error_message": error,
                        "last_check_at": datetime.now().isoformat(),
                    }
                ).eq("id", link_id).execute()
            except Exception as update_err:
                # لو فشل التحديث عشان العمود مش موجود، هنطبع النتيجة بس
                pass

            icon = "✅" if status == "valid" else "❌"
            # هنا السحر: طبع الرابط كاملاً بدون قص
            print(
                f"{icon} ID {link_id:<5} | {server_name:<10} | {status:<10} | URL: {url}"
            )

    except Exception as e:
        print(f"⚠️ خطأ أثناء التشغيل: {e}")


# امسح nest_asyncio.apply() مش محتاجينها في جيتهاب
# واستبدل الـ asyncio.run بالمنطق ده:

if __name__ == "__main__":
    import asyncio

    try:
        # غيرنا الـ 40 لـ 200 عشان نخلص الـ 1624 رابط في أسبوع
        asyncio.run(run_watcher(200))
    except Exception as e:
        print(f"❌ فشل تشغيل الـ Watcher: {e}")
