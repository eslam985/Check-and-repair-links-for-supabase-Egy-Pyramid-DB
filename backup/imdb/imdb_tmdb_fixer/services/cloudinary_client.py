import requests
from config import config
from logger import console

class CloudinaryClient:
    def __init__(self):
        self.cloud_name = config.CLOUDINARY_CLOUD_NAME
        self.upload_preset = config.CLOUDINARY_UPLOAD_PRESET

    def upload_poster(self, image_url: str) -> str:
        """رفع الصورة إلى Cloudinary وإرجاع رابط محوّل"""
        if not self.cloud_name or not self.upload_preset:
            return image_url
        try:
            api_url = f"https://api.cloudinary.com/v1_1/{self.cloud_name}/image/upload"
            payload = {
                "file": image_url,
                "upload_preset": self.upload_preset,
                "folder": "blogger",
            }
            res = requests.post(api_url, data=payload).json()
            public_id = res.get("public_id")
            if public_id:
                transform = "c_fill,g_auto,w_300,h_450,q_auto:good,f_avif"
                return f"https://res.cloudinary.com/{self.cloud_name}/image/upload/{transform}/v1/{public_id}.avif"
            return image_url
        except Exception as e:
            console.print(f"[red]⚠️ خطأ في رفع الصورة: {e}[/red]")
            return image_url