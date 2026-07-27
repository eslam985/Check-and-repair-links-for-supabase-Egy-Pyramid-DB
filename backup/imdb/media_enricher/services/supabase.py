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
    MIN_RUNTIME_MINUTES,
)

console = Console()

# ─── الاستعلام ────────────────────────────────────────────────────────────────
def fetch_incomplete_medias(limit: int = 50) -> list[dict]:
    """
    يجلب الأعمال من Supabase بنظام الصفحات (Pagination) لضمان مسح القاعدة بالكامل
    ويترك لـ Python مهمة فحصها وتصفيتها بدقة حتى يصل للحد الأقصى (limit).
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        console.print("[bold red]❌ SUPABASE_URL أو SUPABASE_KEY غير مضبوط![/bold red]")
        return []

    endpoint = f"{SUPABASE_URL}/rest/v1/medias"
    
    incomplete_items = []
    offset = 0
    chunk_size = 1000  # جلب 1000 سجل في كل طلب

    console.print("[cyan]⏳ جاري مسح قاعدة البيانات للبحث عن الأعمال الناقصة...[/cyan]")

    while len(incomplete_items) < limit:
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            # تحديد النطاق ديناميكياً لجلب الصفحات التالية
            "Range": f"{offset}-{offset + chunk_size - 1}",
        }
        
        params = {
            "order": "created_at.desc",
        }

        try:
            response = requests.get(endpoint, headers=headers, params=params, timeout=15)
            
            if response.status_code != 200:
                console.print(f"[red]❌ Supabase أعاد: {response.status_code}[/red]")
                break

            data = response.json()
            
            # إذا كانت الدفعة فارغة، يعني وصلنا لنهاية قاعدة البيانات
            if not data:
                break

            # فحص الدفعة الحالية
            for item in data:
                if _is_incomplete(item):
                    incomplete_items.append(item)
                    # التوقف فوراً إذا وصلنا للعدد المطلوب (مثلاً 50)
                    if len(incomplete_items) >= limit:
                        break
            
            # زيادة الأوفست للطلب التالي (الانتقال للصفحة التالية)
            offset += chunk_size

        except Exception as e:
            console.print(f"[bold red]❌ خطأ في جلب البيانات من Supabase: {e}[/bold red]")
            break

    return incomplete_items

# ملاحظة: تم دمج دالة _filter_medias داخل الـ Loop في الدالة السابقة 
# لذلك يمكنك حذف دالة _filter_medias القديمة تماماً لترتيب الكود.


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