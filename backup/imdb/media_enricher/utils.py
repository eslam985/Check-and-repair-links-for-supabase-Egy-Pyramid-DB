"""
utils.py — دوال مساعدة مشتركة (لا تعتمد على خدمات خارجية)
"""
import re
from deep_translator import GoogleTranslator
from rich.console import Console

from constants import GENRE_MAP, UNAVAILABLE
from config import DEFAULT_ISO_DURATION

console = Console()


# ─── اكتشاف مصدر الرابط ──────────────────────────────────────────────────────

def extract_tmdb_id_from_url(query: str) -> str | None:
    """
    يكتشف إذا كان الاستعلام رابط TMDB مباشر ويستخرج الـ ID.
    مثال: https://www.themoviedb.org/movie/797109  → "797109"
    """
    match = re.search(r"themoviedb\.org/(?:movie|tv)/(\d+)", query)
    return match.group(1) if match else None


def extract_imdb_id_from_query(query: str) -> str | None:
    """يستخرج معرف IMDB من النص إذا وُجد. مثال: tt1234567"""
    match = re.search(r"tt\d+", query)
    return match.group(0) if match else None


# ─── اللغة ───────────────────────────────────────────────────────────────────

def is_mostly_english(text: str) -> bool:
    """يُعيد True إذا كان النص يغلب عليه الإنجليزية."""
    if not text:
        return True
    clean = re.sub(r"[^a-zA-Z\u0600-\u06FF]", "", str(text))
    if not clean:
        return True
    en = len(re.findall(r"[a-zA-Z]", clean))
    ar = len(re.findall(r"[\u0600-\u06FF]", clean))
    return en >= ar


def translate_to_arabic(text: str) -> str:
    """يترجم النص إلى العربية عبر Google Translate مع إمكانية الفشل الصامت."""
    try:
        return GoogleTranslator(source="en", target="ar").translate(text)
    except Exception as e:
        console.print(f"[yellow]⚠️ فشل الترجمة: {e}[/yellow]")
        return text


# ─── التصنيفات ───────────────────────────────────────────────────────────────

def translate_genres(genres_list: list[str]) -> str:
    """
    يترجم قائمة التصنيفات إلى العربية.
    يُرسل كل التصنيفات في طلب واحد لتوفير الوقت.
    يستخدم قاموس GENRE_MAP كخط دفاع احتياطي.
    """
    if not genres_list:
        return UNAVAILABLE

    english_str = ", ".join(genres_list)
    try:
        translated = GoogleTranslator(source="en", target="ar").translate(english_str)
        return translated.replace("،", ",").replace(",,", ",").strip()
    except Exception as e:
        console.print(f"[yellow]⚠️ فشل ترجمة التصنيفات، استخدام القاموس: {e}[/yellow]")
        return ", ".join(GENRE_MAP.get(g, g) for g in genres_list)


# ─── المدة الزمنية ───────────────────────────────────────────────────────────

def parse_duration_to_iso(duration_str: str) -> str:
    """يحوّل صيغة المدة مثل '1h 33m' إلى ISO 8601 مثل 'PT01H33M'."""
    if not duration_str or duration_str == UNAVAILABLE:
        return DEFAULT_ISO_DURATION

    hours, minutes = 0, 0
    hr_match = re.search(r"(\d+)\s*h", duration_str)
    min_match = re.search(r"(\d+)\s*m", duration_str)

    if hr_match:
        hours = int(hr_match.group(1))
    if min_match:
        minutes = int(min_match.group(1))

    if not hr_match and not min_match:
        digits = re.findall(r"\d+", duration_str)
        if digits:
            minutes = int(digits[0])

    total = (hours * 60) + minutes
    if total == 0:
        return DEFAULT_ISO_DURATION

    return f"PT{total // 60:02d}H{total % 60:02d}M"


def format_duration_arabic(duration_str: str) -> str:
    """يحوّل '1h 33m' إلى '1 ساعة 33 دقيقة'."""
    if not duration_str or duration_str == UNAVAILABLE:
        return UNAVAILABLE

    h_match = re.search(r"(\d+)\s*h", duration_str.lower())
    m_match = re.search(r"(\d+)\s*m\b", duration_str.lower())

    parts = []
    if h_match:
        parts.append(f"{h_match.group(1)} ساعة")
    if m_match:
        parts.append(f"{m_match.group(1)} دقيقة")

    return " ".join(parts) if parts else UNAVAILABLE


# ─── الـ Slug ─────────────────────────────────────────────────────────────────

def build_slug(row_id: int | str, title: str) -> str:
    """يبني slug نظيف من الـ ID والعنوان (بدون سنة)."""
    # إزالة السنة (4 أرقام) من العنوان
    slug_title = re.sub(r"\b\d{4}\b", "", title).strip()
    slug_title = slug_title.lower()
    slug_title = re.sub(r"[^\w\s-]", "", slug_title)
    slug_title = re.sub(r"[-\s]+", "-", slug_title).strip("-")
    return f"{row_id}-{slug_title}"


def build_final_title(base_title: str, year: int | str | None) -> str:
    """يُلحق السنة بالعنوان إذا لم تكن موجودة فيه."""
    if year and str(year) not in base_title:
        return f"{base_title} {year}"
    return base_title