import re

def is_tmdb_url(text: str) -> bool:
    """تتحقق إذا كان النص يحتوي على رابط TMDB صحيح"""
    pattern = r'(?:https?://)?(?:www\.)?themoviedb\.org/movie/(\d+)'
    return bool(re.search(pattern, text))

def extract_tmdb_id(url: str) -> str:
    """استخراج معرف الفيلم من رابط TMDB"""
    match = re.search(r'themoviedb\.org/movie/(\d+)', url)
    return match.group(1) if match else None