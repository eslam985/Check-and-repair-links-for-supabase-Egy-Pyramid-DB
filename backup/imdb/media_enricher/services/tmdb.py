"""
services/tmdb.py — جلب بيانات الأعمال عبر TMDB API
يُستخدم عندما يحتوي الاستعلام على رابط themoviedb.org مباشر
"""
import requests
from rich.console import Console

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from config import TMDB_API_KEY, TMDB_BASE_URL, TMDB_IMAGE_BASE
from constants import UNAVAILABLE
from utils import (
    translate_to_arabic,
    translate_genres,
    is_mostly_english,
    parse_duration_to_iso,
    format_duration_arabic,
)
from services.cloudinary import upload_poster

console = Console()


def fetch_by_tmdb_id(tmdb_id: str) -> dict:
    """
    يجلب بيانات عمل كامل من TMDB API باستخدام الـ ID.
    يُجرب movie أولاً ثم tv إذا فشل.
    """
    if not TMDB_API_KEY:
        console.print("[bold red]❌ TMDB_API_KEY غير مضبوط![/bold red]")
        return {}

    console.print(f"[bold cyan]🎬 جلب بيانات TMDB للـ ID: {tmdb_id}...[/bold cyan]")

    # نجرب movie أولاً، ثم tv
    for media_type in ("movie", "tv"):
        data = _get_tmdb_details(tmdb_id, media_type)
        if data:
            console.print(f"[green]✅ تم العثور على العمل كـ '{media_type}' في TMDB.[/green]")
            return _parse_tmdb_response(data, media_type)

    console.print(f"[bold red]❌ لم يُعثر على ID {tmdb_id} في TMDB (movie أو tv).[/bold red]")
    return {}


# ─── طلبات API ───────────────────────────────────────────────────────────────

def _get_tmdb_details(tmdb_id: str, media_type: str) -> dict | None:
    """يُرسل طلب GET لـ TMDB ويُعيد البيانات الخام أو None عند الفشل."""
    url = f"{TMDB_BASE_URL}/{media_type}/{tmdb_id}"
    params = {
        "api_key": TMDB_API_KEY,
        "language": "en-US",
        "append_to_response": "credits",
    }
    try:
        res = requests.get(url, params=params, timeout=15)
        if res.status_code == 200:
            return res.json()
        return None
    except Exception as e:
        console.print(f"[yellow]⚠️ خطأ في طلب TMDB ({media_type}): {e}[/yellow]")
        return None


# ─── تحليل الاستجابة ─────────────────────────────────────────────────────────

def _parse_tmdb_response(data: dict, media_type: str) -> dict:
    """يحوّل الاستجابة الخام من TMDB إلى الشكل الموحد للمشروع."""

    # ─ العنوان
    title = data.get("title") or data.get("name") or UNAVAILABLE

    # ─ السنة
    date_str = data.get("release_date") or data.get("first_air_date") or ""
    year = int(date_str[:4]) if date_str and date_str[:4].isdigit() else None

    # ─ القصة
    overview = data.get("overview", "").strip()
    if overview and is_mostly_english(overview):
        overview = translate_to_arabic(overview)
    story = overview or UNAVAILABLE

    # ─ التقييم
    rating_val = data.get("vote_average")
    rating = str(round(rating_val, 1)) if rating_val else None

    # ─ المدة
    raw_minutes = data.get("runtime") or (
        data.get("episode_run_time") or [None]
    )[0]
    duration_str, duration_arabic, duration_iso = _process_runtime(raw_minutes)

    # ─ التصنيفات
    genres_list = [g["name"] for g in data.get("genres", [])][:5]
    labels = translate_genres(genres_list) if genres_list else None

    # ─ الصورة
    poster_path = data.get("poster_path", "")
    image_url = UNAVAILABLE
    if poster_path:
        raw_url = f"{TMDB_IMAGE_BASE}{poster_path}"
        image_url = upload_poster(raw_url)

    return {
        "tmdb_id": str(data.get("id", "")),
        "title": title,
        "year": year,
        "story": story if story != UNAVAILABLE else None,
        "poster_url": image_url if image_url != UNAVAILABLE else None,
        "rating": rating,
        "runtime": duration_arabic if duration_arabic != UNAVAILABLE else None,
        "duration_iso": duration_iso,
        "labels": labels,
        "is_ready": True,
    }


def _process_runtime(minutes: int | None) -> tuple[str, str, str]:
    """يُعيد (raw_str, arabic_str, iso_str) للمدة الزمنية."""
    if not minutes:
        return UNAVAILABLE, UNAVAILABLE, parse_duration_to_iso(UNAVAILABLE)

    h, m = divmod(int(minutes), 60)
    raw = f"{h}h {m}m" if h else f"{m}m"
    arabic = format_duration_arabic(raw)
    iso = parse_duration_to_iso(raw)
    return raw, arabic, iso