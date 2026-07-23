from deep_translator import GoogleTranslator
import re
from logger import console

# قاموس التصنيفات (نفس القائمة الأصلية)
GENRE_MAP = {
    "Action": "أكشن",
    "Adventure": "مغامرة",
    "Animation": "رسوم متحركة",
    "Comedy": "كوميديا",
    "Crime": "جريمة",
    "Documentary": "وثائقي",
    "Drama": "دراما",
    "Family": "عائلي",
    "Fantasy": "فانتازيا",
    "History": "تاريخ",
    "Horror": "رعب",
    "Music": "موسيقى",
    "Mystery": "غموض",
    "Romance": "رومانسي",
    "Science Fiction": "خيال علمي",
    "TV Movie": "فيلم تلفزيوني",
    "Thriller": "إثارة",
    "War": "حرب",
    "Western": "غرب أمريكي",
    "Sport": "رياضة",
    "Short": "قصير",
    "Sci-Fi": "خيال علمي",
    "Biography": "سيرة شخصية",
    "German": "ألماني",
    "French": "فرنسي",
    "Japanese": "ياباني",
    "Whodunnit": "من فعلها",
    "Superhero": "سوبرهيرو",
    "Cyberpunk": "سايبربانك",
    "Korean": "كوري",
    "Psychological Thriller": "إثارة نفسية",
    "Portuguese": "برتغالي",
    "Gun Fu": "كونغ فو",
    "Martial Arts": "فنون قتالية",
    "Supernatural Horror": "رعب خارق للطبيعة",
    "Spy": "جاسوس",
    "Period Drama": "دراما تاريخية",
    "Folk Horror": "رعب شعبي",
    "Vampire Horror": "رعب مصاصي الدماء",
}

def is_mostly_english(text: str) -> bool:
    """تحديد إذا كان النص إنجليزياً غالباً"""
    if not text:
        return True
    clean_text = re.sub(r"[^a-zA-Z\u0600-\u06FF]", "", str(text))
    if not clean_text:
        return True
    english_chars = len(re.findall(r"[a-zA-Z]", clean_text))
    arabic_chars = len(re.findall(r"[\u0600-\u06FF]", clean_text))
    return english_chars >= arabic_chars

def translate_text(text: str, source: str = "en", target: str = "ar") -> str:
    """ترجمة نص باستخدام Google Translator، مع fallback"""
    if not text:
        return text
    try:
        return GoogleTranslator(source=source, target=target).translate(text)
    except Exception as e:
        console.print(f"[yellow]⚠️ فشل الترجمة: {e}[/yellow]")
        return text

def translate_genres(genres_list: list) -> str:
    """ترجمة قائمة تصنيفات إنجليزية إلى نص عربي مفصول بفواصل"""
    if not genres_list:
        return "غير متوفر"
    # أخذ أول 5 تصنيفات فقط
    clean_genres = [g.strip() for g in genres_list if len(g) > 1 and "back to top" not in g.lower()][:5]
    if not clean_genres:
        return "غير متوفر"

    # محاولة الترجمة دفعة واحدة
    try:
        english_str = ", ".join(clean_genres)
        translated = GoogleTranslator(source="en", target="ar").translate(english_str)
        # توحيد الفواصل
        return translated.replace("،", ",").replace(",,", ",").strip()
    except:
        # ترجمة فردية باستخدام القاموس
        translated = [GENRE_MAP.get(g, g) for g in clean_genres]
        return ", ".join(translated)