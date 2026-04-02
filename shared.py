"""
shared.py — مشترك بين جميع سكريبتات الفحص والإصلاح
- دالة log() مع flush فوري
- Supabase client
- find_source_url() — يدور على archive/telegram_direct
- update_link_in_db() — يكتب الرابط الجديد ويحفظ القديم
- mark_link_failed()
"""

import os
import sys
import traceback
from datetime import datetime
from supabase import create_client

# ── Supabase ──────────────────────────────────
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("❌ SUPABASE_URL أو SUPABASE_KEY ناقص!", flush=True)
    sys.exit(1)

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ── السيرفرات المصدرية للرفع ──────────────────
SOURCE_SERVERS = ["archive", "telegram_direct"]


# ── Logging فوري ─────────────────────────────
def log(msg: str):
    print(msg, flush=True)
    sys.stdout.flush()


# ── البحث عن مصدر الرفع ──────────────────────
def find_source_url(episode_id: int) -> str | None:
    if not episode_id:
        log("   ⚠️ [Source] episode_id فاضي")
        return None

    log(f"   🔎 [Source] بيدور على archive/telegram لـ episode_id={episode_id}")
    try:
        res = (
            supabase.table("links")
            .select("url, server_name")
            .eq("episode_id", episode_id)
            .in_("server_name", SOURCE_SERVERS)
            .limit(5)
            .execute()
        )
        candidates = res.data or []
        log(f"   🔎 [Source] النتائج: {candidates}")
    except Exception as e:
        log(f"   ⚠️ [Source] خطأ: {type(e).__name__}: {e}")
        return None

    if not candidates:
        log(f"   ⚠️ [Source] مفيش مصدر لـ episode_id={episode_id}")
        return None

    for preferred in SOURCE_SERVERS:
        for row in candidates:
            if row.get("server_name", "").lower() == preferred:
                url = row.get("url", "").strip()
                if url:
                    log(f"   ✅ [Source] اختار: {preferred} → {url}")
                    return url

    fallback = candidates[0].get("url")
    log(f"   ✅ [Source] Fallback → {fallback}")
    return fallback


# ── تحديث الرابط في DB ────────────────────────
def update_link_in_db(link_id: int, old_url: str, new_url: str) -> bool:
    now_iso = datetime.now().isoformat()
    payload = {
        "url":               new_url,
        "old_url":           old_url,
        "last_check_status": "valid",
        "last_check_at":     now_iso,
        "last_success_at":   now_iso,
        "is_fixed":          True,
        "error_message":     None,
    }

    log(f"   🔄 [DB] تحديث link_id={link_id}")
    log(f"   🔄 [DB] الرابط الجديد: {new_url}")

    try:
        resp = supabase.table("links").update(payload).eq("id", link_id).execute()
        log(f"   🔄 [DB] response.data = {resp.data}")

        if resp.data:
            log(f"   ✅ [DB] نجح التحديث!")
            return True
        else:
            log(f"   ⚠️ [DB] response.data فاضي — تحقق من RLS أو link_id")
            return False
    except Exception as e:
        log(f"   ❌ [DB] Exception: {type(e).__name__}: {e}")
        traceback.print_exc(file=sys.stdout)
        sys.stdout.flush()
        return False


# ── تسجيل الفشل ──────────────────────────────
def mark_link_failed(link_id: int, reason: str):
    log(f"   📝 [DB] تسجيل فشل link_id={link_id}: {reason}")
    try:
        resp = (
            supabase.table("links")
            .update({
                "error_message": f"[Repairer] {reason}",
                "last_check_at": datetime.now().isoformat(),
            })
            .eq("id", link_id)
            .execute()
        )
        if not resp.data:
            log(f"   ⚠️ [DB] mark_failed: response.data فاضي | id={link_id}")
    except Exception as e:
        log(f"   ❌ [DB] فشل تسجيل الخطأ: {type(e).__name__}: {e}")
        sys.stdout.flush()