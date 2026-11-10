import os
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[2]
FRONTEND_DIR = ROOT_DIR / "frontend"
load_dotenv(ROOT_DIR / ".env")

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "database": os.getenv("DB_NAME", "assessbridge"),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "connection_timeout": 5,
}

JWT_SECRET = os.getenv("JWT_SECRET", "")
if len(JWT_SECRET) < 32:
    raise RuntimeError("Set JWT_SECRET to at least 32 random characters in .env")

COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
COOKIE_NAME = "ab_session"
EXAM_COOKIE_NAME = "ab_exam_access"
SESSION_MINUTES = 60

SEB_BASE_URL = os.getenv("SEB_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
SEB_CONFIG_KEY = os.getenv("SEB_CONFIG_KEY", "").strip().lower()
SEB_CONFIG_PASSWORD = os.getenv("SEB_CONFIG_PASSWORD", "")
SEB_QUIT_PASSWORD = os.getenv("SEB_QUIT_PASSWORD", "")
SEB_REQUIRED = os.getenv("SEB_REQUIRED", "true").lower() == "true"

_recordings_path = Path(os.getenv("RECORDINGS_DIR", "var/recordings"))
RECORDINGS_DIR = (_recordings_path if _recordings_path.is_absolute() else ROOT_DIR / _recordings_path).resolve()
RECORDING_KEY = os.getenv("RECORDING_KEY", "").strip()
RECORDING_RETENTION_DAYS = int(os.getenv("RECORDING_RETENTION_DAYS", "30"))
if not 1 <= RECORDING_RETENTION_DAYS <= 3650:
    raise RuntimeError("RECORDING_RETENTION_DAYS must be between 1 and 3650")
