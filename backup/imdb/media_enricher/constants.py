"""
constants.py — الثوابت الساكنة للمشروع (لا تتغير بالبيئة)
"""

# قاموس ترجمة التصنيفات (خط دفاع احتياطي إذا فشلت Google Translate)
GENRE_MAP: dict[str, str] = {
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

# أدوات التعريف التي تُتجاهل عند البحث في IMDB
SKIP_WORDS: set[str] = {"the", "a", "an", "to"}

# القيمة الافتراضية عند غياب البيانات
UNAVAILABLE: str = "غير متوفر"