import os
import requests
from config import config
from logger import console
from typing import List, Dict, Any

class SupabaseClient:
    def __init__(self):
        self.url = config.SUPABASE_URL
        self.key = config.SUPABASE_KEY
        if not self.url or not self.key:
            raise ValueError("Supabase credentials missing")

    def _headers(self) -> Dict:
        return {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        }

    def fetch_incomplete_medias(self, limit: int = 20) -> List[Dict]:
        """جلب الأعمال الناقصة وفقاً لشروط محددة"""
        endpoint = f"{self.url}/rest/v1/medias"
        params = {
            "or": "(story.is.null,story.eq.,story.eq.غير متوفر,poster_url.is.null,poster_url.eq.,labels.is.null)",
            "order": "created_at.desc",
            "limit": 1000,  # نأخذ كمية أكبر ثم نفلتر
        }
        headers = self._headers()
        headers["Range"] = "0-1000"  # لتجاوز حد الصفحة

        try:
            response = requests.get(endpoint, headers=headers, params=params)
            if response.status_code != 200:
                console.print(f"[red]❌ فشل جلب النواقص: {response.status_code}[/red]")
                return []

            raw = response.json()
            filtered = []
            for item in raw:
                is_missing = not item.get("story") or not item.get("poster_url")
                is_just_movies_label = item.get("labels", "") == "أفلام"
                is_short_runtime = False
                runtime = item.get("runtime")
                if runtime and "ساعة" not in runtime and "دقيقة" in runtime:
                    try:
                        minutes = int("".join(filter(str.isdigit, runtime)))
                        if minutes < 30:
                            is_short_runtime = True
                    except:
                        pass
                if is_missing or is_just_movies_label or is_short_runtime:
                    filtered.append(item)
                    if len(filtered) >= limit:
                        break
            return filtered
        except Exception as e:
            console.print(f"[red]❌ خطأ في جلب النواقص: {e}[/red]")
            return []

    def update_media(self, row_id: int, data: Dict) -> bool:
        """تحديث سطر معين في جدول medias"""
        endpoint = f"{self.url}/rest/v1/medias"
        headers = self._headers()
        params = {"id": f"eq.{row_id}"}
        try:
            res = requests.patch(endpoint, headers=headers, params=params, json=data)
            if res.status_code in [200, 204]:
                console.print(f"[green]✅ تم تحديث العمل ID={row_id}[/green]")
                return True
            else:
                console.print(f"[red]❌ فشل تحديث {row_id}: {res.text}[/red]")
                return False
        except Exception as e:
            console.print(f"[red]❌ خطأ أثناء التحديث: {e}[/red]")
            return False