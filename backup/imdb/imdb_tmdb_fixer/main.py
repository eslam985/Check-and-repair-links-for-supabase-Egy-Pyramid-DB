import asyncio
from services.media_updater import MediaUpdater

async def main():
    updater = MediaUpdater()
    await updater.run(limit=1)  # يمكنك تغيير العدد من هنا

if __name__ == "__main__":
    asyncio.run(main())