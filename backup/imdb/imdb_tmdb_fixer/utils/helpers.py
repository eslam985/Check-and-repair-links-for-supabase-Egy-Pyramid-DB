import re

def parse_duration_to_iso(duration_str: str) -> str:
    """تحويل صيغة المدة مثل '1h 33m' إلى ISO 8601"""
    if not duration_str or duration_str == "غير متوفر":
        return "PT01H30M"

    hours = 0
    minutes = 0
    hr_match = re.search(r"(\d+)\s*h", duration_str)
    min_match = re.search(r"(\d+)\s*m\b", duration_str)  # m متبوعة بحد كلمة

    if hr_match:
        hours = int(hr_match.group(1))
    if min_match:
        minutes = int(min_match.group(1))
    if not hr_match and not min_match:
        digits = re.findall(r"\d+", duration_str)
        if digits:
            minutes = int(digits[0])

    total_minutes = hours * 60 + minutes
    if total_minutes == 0:
        return "PT01H30M"

    final_hours = total_minutes // 60
    final_mins = total_minutes % 60
    return f"PT{final_hours:02d}H{final_mins:02d}M"

def generate_slug(row_id: int, title: str) -> str:
    """إنشاء slug فريد من الاسم مع إزالة السنة وإضافة الـ ID"""
    # إزالة السنة (4 أرقام) من النهاية أو أي مكان
    clean_title = re.sub(r'\b\d{4}\b', '', title).strip()
    clean_title = clean_title.lower()
    clean_title = re.sub(r'[^\w\s-]', '', clean_title)   # إزالة الرموز
    clean_title = re.sub(r'[-\s]+', '-', clean_title).strip('-')
    return f"{row_id}-{clean_title}"

def clean_duration_text(duration: str) -> str:
    """تنظيف نص المدة من الحروف الزائدة وتحويلها إلى العربية"""
    if not duration or duration == "غير متوفر":
        return "غير متوفر"
    h_match = re.search(r"(\d+)\s*h", duration.lower())
    m_match = re.search(r"(\d+)\s*m\b", duration.lower())
    parts = []
    if h_match:
        parts.append(f"{h_match.group(1)} ساعة")
    if m_match:
        parts.append(f"{m_match.group(1)} دقيقة")
    return " ".join(parts) if parts else duration