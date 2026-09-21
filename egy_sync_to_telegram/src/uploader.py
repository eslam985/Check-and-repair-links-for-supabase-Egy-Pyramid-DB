"""
Telegram uploader - handles sending files via Telethon.
"""

import asyncio
import re
import math
import os
import random
from typing import Optional

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.upload import SaveBigFilePartRequest
from telethon.types import InputFileBig

from src.config import settings
from src.logger import setup_logger

logger = setup_logger(__name__)


class UploadProgressTracker:
    def __init__(self, step: int = 5):
        self.step = step
        self.last_percent = -step

    def __call__(self, current: int, total: int) -> None:
        if not total:
            return
        percent = int((current / total) * 100)
        if percent - self.last_percent >= self.step or percent == 100:
            curr_mb = current // (1024 * 1024)
            tot_mb = total // (1024 * 1024)
            logger.info(f"📤 Uploading: {percent}% ({curr_mb} MB / {tot_mb} MB)")
            self.last_percent = percent



async def fast_upload_file(
    client: TelegramClient,
    file_path: str,
    progress_callback=None,
    connections: int = 4,
) -> InputFileBig:
    file_size = os.path.getsize(file_path)
    part_size = 512 * 1024  # 512 KB per chunk
    part_count = math.ceil(file_size / part_size)
    file_id = random.getrandbits(63)

    sem = asyncio.Semaphore(connections)
    uploaded_bytes = 0
    lock = asyncio.Lock()

    async def upload_part(part_index: int):
        nonlocal uploaded_bytes
        async with sem:
            with open(file_path, "rb") as f:
                f.seek(part_index * part_size)
                chunk = f.read(part_size)

            for attempt in range(3):
                try:
                    await client(SaveBigFilePartRequest(
                        file_id=file_id,
                        file_part=part_index,
                        file_total_parts=part_count,
                        bytes=chunk,
                    ))
                    break
                except Exception as e:
                    if attempt == 2:
                        raise e
                    await asyncio.sleep(2)

            async with lock:
                uploaded_bytes += len(chunk)
                if progress_callback:
                    progress_callback(uploaded_bytes, file_size)

    tasks = [upload_part(i) for i in range(part_count)]
    await asyncio.gather(*tasks)

    return InputFileBig(
        id=file_id,
        parts=part_count,
        name=os.path.basename(file_path),
    )


class TelegramUploader:
    def __init__(self, client: TelegramClient):
        self._client = client

    async def upload_and_get_link(
        self,
        file_path: str,
        caption: str,
        episode_id: int,
    ) -> Optional[str]:
        """
        Upload file to Saved Messages, forward to the Bot,
        then extract the HF stream URL from the Bot's reply using polling.
        """
        bot_target = settings.TELEGRAM_BOT_USERNAME

        logger.info(f"📤 Uploading '{file_path}' to Telegram...")

        # جلب الـ entity الخاصة بالبوت بدلاً من القناة
        bot_entity = await self._client.get_input_entity(bot_target)

        async with self._client.action(bot_entity, "document"):
            progress_tracker = UploadProgressTracker(step=5)
            uploaded_file = await fast_upload_file(
                self._client,
                file_path,
                progress_callback=progress_tracker,
                connections=4,
            )
            sent = await self._client.send_file(
                "me",
                uploaded_file,
                caption=caption,
            )

            # توجيه الملف للبوت لكي يقوم بمعالجته ونشره في القناة تلقائياً
            await sent.forward_to(bot_entity)
            logger.info("⏳ File forwarded to bot. Waiting for bot response...")

# البحث عن رابط HF في محادثة البوت والتأكد من مطابقته للـ ID
        return await self._extract_hf_link_with_polling(bot_entity, episode_id=episode_id, timeout=120)
      
    async def _extract_hf_link_with_polling(self, chat, episode_id: int, timeout: int = 120) -> Optional[str]:
        """
        البحث التكراري عن رابط HF لفترة زمنية محددة مع تتبع الردود (Replies)
        للتحقق من أن الرابط يعود للرسالة التي تحمل الـ ID الصحيح.
        """
        elapsed = 0
        interval = 6  # فحص كل 6 ثوانٍ
        id_str = str(episode_id)

        while elapsed < timeout:
            # زيادة limit إلى 10 لضمان التقاط الرسالتين (الأصلية والرد) حتى لو كان هناك زحام في المحادثة
            async for message in self._client.iter_messages(chat, limit=10):
                # 1. البحث عن رسالة تحتوي على الرابط
                if message.text and "hf.space" in message.text:
                    
                    # 2. التأكد من أن هذه الرسالة هي "رد" (Reply) على رسالة أخرى
                    if message.reply_to_msg_id:
                        
                        # 3. جلب الرسالة الأصلية التي تم الرد عليها
                        replied_msg = await self._client.get_messages(chat, ids=message.reply_to_msg_id)
                        
                        # 4. التحقق من وجود الـ ID في الرسالة الأصلية
                        if replied_msg and replied_msg.text and id_str in replied_msg.text:
                            
                            # 5. استخراج الرابط إذا تطابق الـ ID
                            match = re.search(r"(https?://[^\s`]+hf\.space[^\s`]+)", message.text)
                            if match:
                                url = match.group(1).strip().rstrip("`")
                                logger.info(f"🔗 HF link captured for ID {episode_id}: {url[:70]}")
                                return url
            
            logger.info(f"⏳ Still waiting for bot reply for ID {episode_id}... ({elapsed}s / {timeout}s)")
            await asyncio.sleep(interval)
            elapsed += interval

        logger.warning(f"⚠️ Timeout: No HF link found from bot for ID {episode_id} after waiting.")
        return None
