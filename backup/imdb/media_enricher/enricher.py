"""
enricher.py — معالج البيانات الرئيسي (المنطق الوسيط بين قاعدة البيانات والكواشف)
"""
import asyncio
from rich.console import Console

from utils import (
    extract_tmdb_id_from_url,
    extract_imdb_id_from_query,
    build_slug,
    build_final_title,
)
from services.imdb import fetch_by_imdb_id, search_and_fetch
from services.tmdb import fetch_by_tmdb_id  # افترضنا أن ملف tmdb موجود بالهيكلة
from services.supabase import fetch_incomplete_medias, update_media_data
from config import FETCH_LIMIT

console = Console()


async def enrich_single_media(media: dict) -> bool:
    """يعالج عملاً واحداً: يجلب بياناته المحدثة ويحفظها في Supabase."""
    row_id = media["id"]
    title = media.get("title", "")
    year = media.get("year", "")
    
    # بناء استعلام البحث
    query = f"{title} {year}".strip() if year and str(year) not in str(title) else title
    if not query:
        query = str(title)

    console.print(f"\n[cyan]🔄 معالجة العمل (ID: {row_id}): '{query}'[/cyan]")

    scraped_data = {}

    try:
        # 1. التحقق هل هو رابط TMDB مباشر
        tmdb_id = extract_tmdb_id_from_url(query)
        if tmdb_id:
            console.print(f"[cyan]🔗 تم اكتشاف رابط TMDB مباشر ({tmdb_id})[/cyan]")
            scraped_data = fetch_by_tmdb_id(tmdb_id)
        else:
            # 2. التحقق هل يحتوي على معرف IMDb مباشر
            imdb_id = extract_imdb_id_from_query(query)
            if imdb_id:
                scraped_data = await fetch_by_imdb_id(imdb_id)
            else:
                # 3. البحث الذكي عبر الاسم
                scraped_data = await search_and_fetch(query)

        if not scraped_data or not scraped_data.get("story") and not scraped_data.get("poster_url"):
            console.print(f"[red]⚠️ بيانات الكشط فارغة تماماً للـ ID: {row_id}، تم إلغاء التحديث لتجنب تلف البيانات.[/red]")
            return False

        # تجهيز البيانات النهائية للتحديث
        final_year = scraped_data.get("year") or year

        scraped_title = scraped_data.get("title")
        raw_db_title = str(title)
        base_title = scraped_title if scraped_title and scraped_title != "غير متوفر" else (raw_db_title if "imdb.com" not in raw_db_title else "unknown title")

        final_title = build_final_title(base_title, final_year)
        slug = build_slug(row_id, base_title)

        update_payload = {
            "title": final_title,
            "slug": slug,
            "story": scraped_data.get("story"),
            "poster_url": scraped_data.get("poster_url"),
            "rating": scraped_data.get("rating"),
            "runtime": scraped_data.get("runtime"),
            "duration_iso": scraped_data.get("duration_iso"),
            "labels": scraped_data.get("labels") if "labels" in scraped_data else scraped_data.get("genres"),
            "year": final_year if isinstance(final_year, int) else (int(final_year) if str(final_year).isdigit() else None),
            "is_ready": True,
        }

        # تنظيف المفاتيح الفارغة إن وجدت
        update_payload = {k: v for k, v in update_payload.items() if v is not None}

        # طباعة البيانات المستخرجة للتصحيح (Debugging)
        console.print(f"[bold cyan]🔍 تفاصيل البيانات المستخرجة للـ ID ({row_id}):[/bold cyan]")
        console.print(f"  - القصة (Story): {scraped_data.get('story')}")
        console.print(f"  - البوستر (Poster): {scraped_data.print(scraped_data.get('poster_url')) if hasattr(scraped_data, 'print') else scraped_data.get('poster_url')}")
        console.print(f"  - التقييم (Rating): {scraped_data.get('rating')}")
        console.print(f"  - المدة (Runtime): {scraped_data.get('runtime')}")
        console.print(f"  - التصنيفات (Labels): {scraped_data.get('labels') or scraped_data.get('genres')}")

        # حفظ البيانات في Supabase
        success = update_media_data(row_id, update_payload)
        return success

    except Exception as e:
        console.print(f"[bold red]💥 خطأ غير متوقع أثناء معالجة ID {row_id}: {e}[/bold red]")
        return False


async def run_enrichment_process(limit: int = None):
    """الدالة الرئيسية لتشغيل عملية السحب والصيانة الدورية."""
    target_limit = limit if limit is not None else FETCH_LIMIT
    console.print(f"[bold green]🚀 بدء عملية سيانة وتحديث الأعمال (الحد الأقصى: {target_limit})[/bold green]")

    medias = fetch_incomplete_medias(target_limit)
    if not medias:
        console.print("[green]✨ لا توجد أعمال ناقصة تحتاج إلى معالجة حالياً.[/green]")
        return

    console.print(f"[yellow]📦 تم العثور على {len(medias)} عمل بحاجة للتحديث.[/yellow]")

    for media in medias:
        await enrich_single_media(media)
        # انتظار بسيط بين الطلبات لتجنب الحظر
        await asyncio.sleep(3)

    console.print("[bold green]✨ انتهت عملية الصيانة بنجاح.[/bold green]")