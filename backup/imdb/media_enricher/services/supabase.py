"""
services/supabase.py — كل العمليات مع قاعدة بيانات Supabase
"""
import re
import requests
from rich.console import Console

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from config import (
    SUPABASE_URL,
    SUPABASE_KEY,
    SUPABASE_FETCH_RANGE,
    MIN_RUNTIME_MINUTES,
)

console = Console()

# ─── الاستعلام ────────────────────────────────────────────────────────────────

def fetch_incomplete_medias(limit: int = 20) -> list[dict]:
    """
    يجلب الأعمال الناقصة أو التالفة من Supabase.
    معيار النقص: القصة أو الصورة فارغة/null، أو التصنيفات مفقودة،
    أو مدة العمل أقل من الحد الأدنى المقبول.
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        console.print("[bold red]❌ SUPABASE_URL أو SUPABASE_KEY غير مضبوط![/bold red]")
        return []

    endpoint = f"{SUPABASE_URL}/rest/v1/medias"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Range": SUPABASE_FETCH_RANGE,
    }
    params = {
        "or": "(story.is.null,story.eq.,story.eq.غير متوفر,poster_url.is.null,poster_url.eq.,labels.is.null)",
        "order": "created_at.desc",
    }

    try:
        response = requests.get(endpoint, headers=headers, params=params, timeout=15)
        if response.status_code != 200:
            console.print(f"[red]❌ Supabase أعاد: {response.status_code}[/red]")
            return []

        return _filter_medias(response.json(), limit)

    except Exception as e:
        console.print(f"[bold red]❌ خطأ في جلب البيانات من Supabase: {e}[/bold red]")
        return []


def _filter_medias(raw_medias: list[dict], limit: int) -> list[dict]:
    """يُصفي القائمة الخام ويُعيد الأعمال المؤهلة للمعالجة فقط."""
    result = []
    for item in raw_medias:
        if len(result) >= limit:
            break
        if _is_incomplete(item):
            result.append(item)
    return result


def _is_incomplete(item: dict) -> bool:
    """يحدد إذا كان العمل يحتاج معالجة بناءً على القواعد المحددة."""
    story = item.get("story")
    poster_url = item.get("poster_url")
    
    # 1. القصة الأساسية: لو ناقصة أو غير متوفرة
    if not story or str(story).strip() in ["", "غير متوفر", "None"]:
        return True
        
    # 2. البوستر: التحقق أنه موجود ويماتش كـ رابط صحيح يبدأ بـ https
    poster_str = str(poster_url).strip() if poster_url else ""
    is_valid_poster = bool(re.match(r"^https://\S+\.\S+", poster_str))
    if not is_valid_poster:
        return True
        
    # 3. إذا وُجدت القصة والبوستر الصحيح، نفحص الحقول الثلاثة الباقية (التقييم، التصنيفات، المدة)
    missing_count = 0
    
    # فحص التقييم (Rating)
    rating = item.get("rating")
    if not rating or str(rating).strip() in ["", "None", "غير متوفر", "NA"]:
        missing_count += 1
        
    # فحص التصنيفات (Labels) مع التعامل مع القيمة الافتراضية
    labels = item.get("labels")
    clean_labels = str(labels).strip() if labels else ""
    default_labels = ["أفلام", "افلام", "الافلام", "الأفلام"]
    if not clean_labels or clean_labels in default_labels or clean_labels == "غير متوفر":
        missing_count += 1
        
    # فحص المدة (Runtime)
    runtime = item.get("runtime")
    if not runtime or str(runtime).strip() in ["", "None", "غير متوفر"] or _has_short_runtime(runtime):
        missing_count += 1
        
    # لو 2 أو أكثر من الـ 3 ناقصين، يُعتبر العمل ناقصاً
    return missing_count >= 2


def _has_short_runtime(runtime_str: str | None) -> bool:
    """يتحقق إذا كانت المدة أقل من الحد الأدنى المقبول."""
    if not runtime_str:
        return False
    if "ساعة" in runtime_str or "دقيقة" not in runtime_str:
        return False
    try:
        minutes = int("".join(filter(str.isdigit, runtime_str)))
        return minutes < MIN_RUNTIME_MINUTES
    except ValueError:
        return False


# ─── التحديث ──────────────────────────────────────────────────────────────────

def update_media_data(row_id: int | str, updated_fields: dict) -> bool:
    """يُحدّث سجل معين في Supabase بناءً على الـ ID."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        console.print("[bold red]❌ مفاتيح Supabase غير مضبوطة![/bold red]")
        return False

    endpoint = f"{SUPABASE_URL}/rest/v1/medias"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }

    try:
        res = requests.patch(
            endpoint,
            headers=headers,
            params={"id": f"eq.{row_id}"},
            json=updated_fields,
            timeout=15,
        )
        if res.status_code in (200, 204):
            console.print(f"[bold green]✅ تم تحديث العمل (ID: {row_id}) بنجاح.[/bold green]")
            return True
        else:
            console.print(f"[bold red]❌ فشل تحديث السطر {row_id}: {res.text}[/bold red]")
            return False

    except Exception as e:
        console.print(f"[bold red]❌ خطأ أثناء تحديث Supabase: {e}[/bold red]")
        return False