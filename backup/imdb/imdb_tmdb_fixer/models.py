from dataclasses import dataclass
from typing import Optional

@dataclass
class ScrapedData:
    tmdb_id: Optional[str]  # سنستخدمه للمعرف (سواء IMDb أو TMDB)
    story: Optional[str]
    poster_url: Optional[str]
    rating: Optional[float]
    runtime: Optional[str]  # نص مترجم (مثل "1 ساعة 30 دقيقة")
    duration_iso: Optional[str]
    labels: Optional[str]   # التصنيفات مترجمة مفصولة بفواصل
    year: Optional[int]
    title: Optional[str]    # الاسم الحقيقي (قد يُستخدم)
    is_ready: bool = True
    slug: Optional[str] = None   # سيُضاف لاحقاً

@dataclass
class Media:
    id: int
    title: str
    year: Optional[int] = None
    story: Optional[str] = None
    poster_url: Optional[str] = None
    labels: Optional[str] = None
    runtime: Optional[str] = None
    # ... يمكن إضافة حقول أخرى حسب الحاجة