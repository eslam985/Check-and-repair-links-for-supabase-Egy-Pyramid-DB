"""
config.py — مركز التحكم الموحد للمشروع
كل الإعدادات والمفاتيح تُقرأ من هنا فقط
"""
import os

# ─── Supabase ────────────────────────────────────────────────────────────────
SUPABASE_URL: str = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY: str = (
    os.getenv("SUPABASE_KEY")
    or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    or os.getenv("SUPABASE_ANON_KEY")
    or ""
)   

# ─── Cloudinary ──────────────────────────────────────────────────────────────
CLOUDINARY_CLOUD_NAME: str = os.getenv("CLOUDINARY_CLOUD_NAME", "")
CLOUDINARY_UPLOAD_PRESET: str = os.getenv("CLOUDINARY_UPLOAD_PRESET", "")
CLOUDINARY_FOLDER: str = "blogger"
CLOUDINARY_TRANSFORM: str = "c_fill,g_auto,w_300,h_450,q_auto:good,f_avif"

# ─── TMDB ─────────────────────────────────────────────────────────────────────
TMDB_API_KEY: str = os.getenv("TMDB_API_KEY", "")
TMDB_BASE_URL: str = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE: str = "https://image.tmdb.org/t/p/w780"

# ─── IMDB / Playwright ───────────────────────────────────────────────────────
IMDB_SEARCH_URL: str = "https://www.imdb.com/find/?q={query}&s=tt&ref_=fn_mov"
IMDB_TITLE_URL: str = "https://www.imdb.com/title/{imdb_id}/"
BROWSER_USER_AGENT: str = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
BROWSER_VIEWPORT: dict = {"width": 1280, "height": 720}

# ─── Logic Settings ───────────────────────────────────────────────────────────
FETCH_LIMIT: int = 50                     # عدد الأعمال الناقصة في كل دورة
SLEEP_BETWEEN_ITEMS: int = 5             # فترة الانتظار بين كل عمل (ثوانٍ)
SUPABASE_FETCH_RANGE: str = "0-1000"    # نطاق الاستعلام من Supabase
MIN_RUNTIME_MINUTES: int = 30           # الحد الأدنى لمدة العمل (دقيقة)
DEFAULT_ISO_DURATION: str = "PT01H30M"  # مدة افتراضية عند الفشل

# ─── Validation ───────────────────────────────────────────────────────────────
def validate_config() -> list[str]:
    """يُعيد قائمة بأسماء المتغيرات الناقصة"""
    missing = []
    if not SUPABASE_URL:
        missing.append("SUPABASE_URL")
    if not SUPABASE_KEY:
        missing.append("SUPABASE_KEY / SUPABASE_SERVICE_ROLE_KEY / SUPABASE_ANON_KEY")
    return missing