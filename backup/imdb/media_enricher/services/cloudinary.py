"""
services/cloudinary.py — رفع ومعالجة الصور عبر Cloudinary
"""
import requests
from rich.console import Console

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from config import (
    CLOUDINARY_CLOUD_NAME,
    CLOUDINARY_UPLOAD_PRESET,
    CLOUDINARY_FOLDER,
    CLOUDINARY_TRANSFORM,
)

console = Console()


def upload_poster(image_url: str) -> str:
    """
    يرفع صورة البوستر إلى Cloudinary ويُعيد رابطها المحسّن.
    في حالة فشل الرفع أو عدم ضبط الإعدادات، يُعيد الرابط الأصلي.
    """
    if not CLOUDINARY_CLOUD_NAME or not CLOUDINARY_UPLOAD_PRESET:
        return image_url

    try:
        api_url = f"https://api.cloudinary.com/v1_1/{CLOUDINARY_CLOUD_NAME}/image/upload"
        payload = {
            "file": image_url,
            "upload_preset": CLOUDINARY_UPLOAD_PRESET,
            "folder": CLOUDINARY_FOLDER,
        }
        res = requests.post(api_url, data=payload, timeout=30).json()
        public_id = res.get("public_id")

        if public_id:
            return (
                f"https://res.cloudinary.com/{CLOUDINARY_CLOUD_NAME}"
                f"/image/upload/{CLOUDINARY_TRANSFORM}/v1/{public_id}.avif"
            )
        return image_url

    except Exception as e:
        console.print(f"[yellow]⚠️ خطأ في رفع الصورة لـ Cloudinary: {e}[/yellow]")
        return image_url