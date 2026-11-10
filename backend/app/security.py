import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt

from .config import JWT_SECRET, SESSION_MINUTES
import hashlib


ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    encoded = password.encode("utf-8")
    if not encoded or len(encoded) > 72:
        raise ValueError("Password must be 1 to 72 UTF-8 bytes")
    return bcrypt.hashpw(encoded, bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    encoded = password.encode("utf-8")
    if not encoded or len(encoded) > 72:
        return False
    try:
        return bcrypt.checkpw(encoded, password_hash.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        return False


def new_session(user_id: int, minutes: int = SESSION_MINUTES) -> tuple[str, str, datetime]:
    token_id = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    token = jwt.encode(
        {"sub": str(user_id), "jti": token_id, "exp": expires_at},
        JWT_SECRET,
        algorithm=ALGORITHM,
    )
    return token, hashlib.sha256(token_id.encode()).hexdigest(), expires_at


def read_session(token: str) -> tuple[int, str] | None:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
        user_id = int(payload["sub"])
        token_id = payload["jti"]
        if user_id <= 0 or not isinstance(token_id, str):
            return None
        return user_id, hashlib.sha256(token_id.encode()).hexdigest()
    except (JWTError, KeyError, TypeError, ValueError):
        return None


def new_exam_access(user_id: int, invitation_id: int, expires_at: datetime, seb_verified: bool = False) -> str:
    return jwt.encode(
        {"sub": str(user_id), "invitation_id": invitation_id, "scope": "exam_room", "seb_verified": seb_verified, "exp": expires_at},
        JWT_SECRET,
        algorithm=ALGORITHM,
    )


def exam_entry_fingerprint(auth_session_token_id: str, exam_access_token: str) -> str:
    """Bind a pre-exam identity check to one sign-in and exam entry."""
    return hashlib.sha256(f"{auth_session_token_id}:{exam_access_token}".encode("utf-8")).hexdigest()


def read_exam_access(token: str) -> tuple[int, int, bool] | None:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[ALGORITHM])
        if payload.get("scope") != "exam_room":
            return None
        user_id = int(payload["sub"])
        invitation_id = int(payload["invitation_id"])
        return (user_id, invitation_id, payload.get("seb_verified") is True) if user_id > 0 and invitation_id > 0 else None
    except (JWTError, KeyError, TypeError, ValueError):
        return None
