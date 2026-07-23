import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY") or os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    CLOUDINARY_CLOUD_NAME = os.getenv("CLOUDINARY_CLOUD_NAME")
    CLOUDINARY_UPLOAD_PRESET = os.getenv("CLOUDINARY_UPLOAD_PRESET")
    TMDB_API_KEY = os.getenv("TMDB_API_KEY")
    DEFAULT_RUNTIME_ISO = "PT01H30M"
    INCOMPLETE_LIMIT = 20  # يمكن تغييره

    @classmethod
    def validate(cls):
        if not cls.SUPABASE_URL or not cls.SUPABASE_KEY:
            raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set")
        if not cls.TMDB_API_KEY:
            raise ValueError("TMDB_API_KEY must be set (required for TMDB feature)")

config = Config()