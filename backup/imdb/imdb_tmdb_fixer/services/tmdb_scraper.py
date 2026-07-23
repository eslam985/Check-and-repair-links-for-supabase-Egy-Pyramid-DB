import requests
from config import config
from logger import console
from models import ScrapedData
from utils.validators import is_tmdb_url, extract_tmdb_id
from services.translator import translate_text, is_mostly_english, translate_genres
from services.cloudinary_client import CloudinaryClient
from utils.helpers import parse_duration_to_iso

class TMDBScraper:
    def __init__(self):
        self.api_key = config.TMDB_API_KEY
        self.base_url = "https://api.themoviedb.org/3"
        self.cloudinary = CloudinaryClient()

    def fetch_media_data(self, search_query: str) -> ScrapedData:
        """جلب البيانات من TMDB API. يدعم الرابط المباشر أو المعرف."""
        # إذا كان المدخل رابطاً، استخرج المعرف
        if is_tmdb_url(search_query):
            tmdb_id = extract_tmdb_id(search_query)
        elif search_query.strip().isdigit():
            tmdb_id = search_query.strip()
        else:
            # يمكننا أيضاً البحث بالعنوان، لكننا سنكتفي بالرابط المباشر كما طلبت
            console.print("[yellow]⚠️ المدخل ليس رابطاً صالحاً لـ TMDB، سيتم تجاهله[/yellow]")
            return None

        if not tmdb_id:
            return None

        console.print(f"[cyan]🎬 جلب بيانات الفيلم من TMDB (ID: {tmdb_id})...[/cyan]")
        # جلب التفاصيل
        details_url = f"{self.base_url}/movie/{tmdb_id}?api_key={self.api_key}&language=en-US"
        try:
            resp = requests.get(details_url)
            if resp.status_code != 200:
                console.print(f"[red]❌ فشل جلب بيانات TMDB: {resp.status_code}[/red]")
                return None
            data = resp.json()
        except Exception as e:
            console.print(f"[red]❌ خطأ في طلب TMDB: {e}[/red]")
            return None

        # استخراج البيانات
        title = data.get("title")
        year = data.get("release_date", "")[:4] if data.get("release_date") else None
        if year:
            year = int(year)
        overview = data.get("overview")
        if not overview:
            overview = "غير متوفر"
        elif is_mostly_english(overview):
            overview = translate_text(overview)

        # التصنيفات
        genres = [g["name"] for g in data.get("genres", [])]
        genres_ar = translate_genres(genres)

        # المدة
        runtime_minutes = data.get("runtime")
        duration_iso = parse_duration_to_iso(f"{runtime_minutes}m") if runtime_minutes else config.DEFAULT_RUNTIME_ISO
        # ترجمة المدة
        hours = runtime_minutes // 60 if runtime_minutes else 0
        minutes = runtime_minutes % 60 if runtime_minutes else 0
        runtime_text = f"{hours} ساعة" if hours else ""
        if minutes:
            runtime_text += f" {minutes} دقيقة" if runtime_text else f"{minutes} دقيقة"
        if not runtime_text:
            runtime_text = "غير متوفر"

        # التقييم
        rating = data.get("vote_average")

        # البوستر
        poster_path = data.get("poster_path")
        if poster_path:
            full_poster_url = f"https://image.tmdb.org/t/p/original{poster_path}"
            poster = self.cloudinary.upload_poster(full_poster_url)
        else:
            poster = "غير متوفر"

        # المعرف (نستخدم tmdb_id نفسه)
        return ScrapedData(
            tmdb_id=f"tmdb_{tmdb_id}",  # لتمييزه عن imdb
            story=overview,
            poster_url=poster,
            rating=rating,
            runtime=runtime_text,
            duration_iso=duration_iso,
            labels=genres_ar,
            year=year,
            title=title,
            is_ready=True,
        )