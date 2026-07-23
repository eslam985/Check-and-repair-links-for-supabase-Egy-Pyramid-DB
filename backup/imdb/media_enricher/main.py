"""
main.py — نقطة البداية لتشغيل نظام صيانة وتحديث قاعدة البيانات
"""
import asyncio
import sys
import os

# إضافة المسار الحالي للمشروع
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from enricher import run_enrichment_process
from rich.console import Console

console = Console()

if __name__ == "__main__":
    try:
        console.print("[bold blue]🎬 تشغيل نظام تحديث وإصلاح بيانات Egy Pyramid...[/bold blue]")
        asyncio.run(run_enrichment_process())
    except KeyboardInterrupt:
        console.print("\n[yellow]⚠️ تم إيقاف العملية بواسطة المستخدم.[/yellow]")
    except Exception as e:
        console.print(f"\n[bold red]❌ حدث خطأ جسيم في النظام: {e}[/bold red]")