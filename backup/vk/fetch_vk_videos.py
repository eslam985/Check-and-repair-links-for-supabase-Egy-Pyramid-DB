import os
import json
import asyncio
import httpx
from dotenv import load_dotenv

# تحميل المتغيرات من ملف .env
load_dotenv()

VK_ACCESS_TOKEN = os.getenv("VK_SERVICE_KEY") or os.getenv("VK_ACCESS_TOKEN")
OUTPUT_FILE = "vk_videos.json"

# معرفات الجروبات بالسالب
TARGET_GROUPS = [
    -235805578,
    -239045329,
    -240074459,
    -241096187
]
async def fetch_group_videos(client: httpx.AsyncClient, owner_id: int) -> list[dict]:
    videos = []
    offset = 0
    count = 100
    
    print(f"🔄 بدء جلب البيانات للجروب: {owner_id}")
    
    while True:
        params = {
            "owner_id": owner_id,
            "count": count,
            "offset": offset,
            "access_token": VK_ACCESS_TOKEN,
            "v": "5.131"
        }
        
        try:
            res = await client.get("https://api.vk.com/method/video.get", params=params, timeout=15.0)
            data = res.json()
            
            if "error" in data:
                err_msg = data["error"].get("error_msg", "Unknown API Error")
                print(f"❌ خطأ API في الجروب {owner_id} عند offset {offset}: {err_msg}")
                break
                
            response_data = data.get("response", {})
            items = response_data.get("items", [])
            total_in_group = response_data.get("count", 0)
            
            if not items:
                break
                
            for item in items:
                videos.append({
                    "id": item["id"],
                    "owner_id": item["owner_id"],
                    "base_id": f"{item['owner_id']}_{item['id']}",
                    "title": item.get("title", "").strip(),
                    "player": item.get("player", ""),
                    "duration": item.get("duration", 0),
                    "adding_date": item.get("adding_date", 0)
                })
                
            print(f"   📥 تم جلب {len(videos)} / {total_in_group} فيديو...")
            
            offset += count
            if offset >= total_in_group:
                break
                
            # تأخير بسيط لتجنب التجاوز المفرط لحد الطلبات (Rate Limiting)
            await asyncio.sleep(0.35)
            
        except Exception as e:
            print(f"❌ خطأ اتصال أثناء الجلب: {e}")
            break
            
    return videos

async def main():
    if not VK_ACCESS_TOKEN:
        print("❌ تنبيه: لم يتم العثور على VK_SERVICE_KEY أو VK_ACCESS_TOKEN في ملف .env")
        return

    all_videos = []
    async with httpx.AsyncClient(verify=False) as client:
        for group_id in TARGET_GROUPS:
            group_videos = await fetch_group_videos(client, group_id)
            all_videos.extend(group_videos)
            print(f"✅ إجمالي المقبوض من الجروب {group_id}: {len(group_videos)} فيديو.\n")

    print(f"💾 حفظ الإجمالي ({len(all_videos)} فيديو) في الملف {OUTPUT_FILE}...")
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(all_videos, f, ensure_ascii=False, indent=2)
        
    print(f"🎉 اكتملت الخطوة الأولى بنجاح! تم إنشاء الملف {OUTPUT_FILE}.")

if __name__ == "__main__":
    asyncio.run(main())