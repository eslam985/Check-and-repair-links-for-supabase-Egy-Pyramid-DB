import asyncio
from logger import console
from config import config
from models import ScrapedData
from services.supabase_client import SupabaseClient
from services.imdb_scraper import IMDbScraper
from services.tmdb_scraper import TMDBScraper
from utils.helpers import generate_slug
from utils.validators import is_tmdb_url

class MediaUpdater:
    def __init__(self):
        self.supabase = SupabaseClient()
        self.imdb_scraper = IMDbScraper()
        self.tmdb_scraper = TMDBScraper()

    async def run(self, limit: int = None):
        if limit is None:
            limit = config.INCOMPLETE_LIMIT
        console.print(f"[bold cyan]🚀 بدء عملية الصيانة التلقائية (الحد: {limit})[/bold cyan]")
        medias = self.supabase.fetch_incomplete_medias(limit)
        if not medias:
            console.print("[green]✨ لا توجد أعمال ناقصة حالياً.[/green]")
            return

        console.print(f"[yellow]تم رصد {len(medias)} عمل ناقص. البدء...[/yellow]")
        for media in medias:
            row_id = media["id"]
            title = media.get("title", "")
            year = media.get("year", "")
            search_query = f"{title} {year}".strip() if year and str(year) not in str(title) else title

            # تحديد الكاشط المناسب
            scraper = None
            if is_tmdb_url(search_query):
                scraper = self.tmdb_scraper
                console.print(f"[cyan]🔗 تم اكتشاف رابط TMDB، استخدام TMDB API[/cyan]")
            else:
                scraper = self.imdb_scraper
                console.print(f"[cyan]🔍 استخدام IMDb Scraper[/cyan]")

            try:
                # استدعاء الكاشط (متزامن أو غير متزامن حسب الحاجة)
                if hasattr(scraper, 'fetch_media_data') and asyncio.iscoroutinefunction(scraper.fetch_media_data):
                    scraped = await scraper.fetch_media_data(search_query)
                else:
                    scraped = scraper.fetch_media_data(search_query)

                if scraped and isinstance(scraped, ScrapedData):
                    # تحقق من نجاح الجلب (وجود القصة ومعرف)
                    if scraped.story and scraped.story != "غير متوفر" and scraped.tmdb_id:
                        # تحضير البيانات للتحديث
                        update_data = {
                            "story": scraped.story,
                            "poster_url": scraped.poster_url,
                            "rating": scraped.rating,
                            "runtime": scraped.runtime,
                            "duration_iso": scraped.duration_iso,
                            "labels": scraped.labels,
                            "year": scraped.year,
                            "is_ready": True,
                            "tmdb_id": scraped.tmdb_id,
                        }
                        # تحديد الاسم النهائي (مع السنة)
                        base_title = scraped.title or title
                        final_year = scraped.year or year
                        if final_year and str(final_year) not in base_title:
                            final_title = f"{base_title} {final_year}"
                        else:
                            final_title = base_title
                        update_data["title"] = final_title

                        # إنشاء slug
                        update_data["slug"] = generate_slug(row_id, base_title)

                        # تحديث
                        self.supabase.update_media(row_id, update_data)
                    else:
                        console.print(f"[red]⚠️ البيانات المجلوبة للعمل '{title}' غير مكتملة، تم التخطي.[/red]")
                else:
                    console.print(f"[red]⚠️ فشل جلب البيانات للعمل '{title}'[/red]")
            except Exception as e:
                console.print(f"[red]💥 خطأ أثناء معالجة العمل {title}: {e}[/red]")

            # انتظار 5 ثوانٍ بين العمليات
            await asyncio.sleep(5)