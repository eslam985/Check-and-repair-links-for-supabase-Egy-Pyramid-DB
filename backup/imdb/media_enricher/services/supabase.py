"""
services/supabase.py — كل العمليات مع قاعدة بيانات Supabase
"""
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
    """يحدد إذا كان العمل يحتاج معالجة."""
    missing_data = not item.get("story") or not item.get("poster_url")
    only_movies_label = item.get("labels", "") == "أفلام"
    short_runtime = _has_short_runtime(item.get("runtime"))
    return missing_data or only_movies_label or short_runtime


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