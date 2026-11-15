"""Candidate account registration and email ownership checks."""

import hashlib
import secrets
import smtplib
from datetime import datetime, timedelta, timezone

import mysql.connector
from fastapi import HTTPException

from .db import database
from .mail import MailNotConfigured, MailSettings, send_verification_email
from .security import hash_password, verify_password


VERIFY_MINUTES = 60
RESEND_SECONDS = 60


def _verification_token() -> tuple[str, str, datetime]:
    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    expires = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=VERIFY_MINUTES)
    return token, digest, expires


def _mail_settings() -> MailSettings:
    try:
        return MailSettings.from_environment()
    except MailNotConfigured as exc:
        raise HTTPException(status_code=503, detail="Email delivery is not configured") from exc


def register_candidate(username: str | None, email: str, password: str) -> dict:
    settings = _mail_settings()
    account_name = username or "candidate_" + secrets.token_hex(12)
    token, digest, expires = _verification_token()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        with database() as connection:
            cursor = connection.cursor()
            cursor.execute(
                "INSERT INTO users (username, email, password_hash, role, is_active) VALUES (%s, %s, %s, 'candidate', FALSE)",
                (account_name, email, hash_password(password)),
            )
            user_id = cursor.lastrowid
            cursor.execute(
                "INSERT INTO candidate_email_verifications (user_id, token_hash, expires_at, sent_at) VALUES (%s, %s, %s, %s)",
                (user_id, digest, expires, now),
            )
            send_verification_email(settings=settings, email=email, username=email, token=token)
            connection.commit()
            cursor.close()
    except mysql.connector.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="This email is already registered") from exc
    except (mysql.connector.Error, OSError, smtplib.SMTPException) as exc:
        raise HTTPException(status_code=503, detail="Registration is temporarily unavailable; no account was created") from exc
    return {"message": "Check your email for a verification link before signing in"}


def verify_candidate_email(token: str) -> dict:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    try:
        with database() as connection:
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                """SELECT user_id FROM candidate_email_verifications
                   WHERE token_hash = %s AND expires_at > UTC_TIMESTAMP() FOR UPDATE""",
                (digest,),
            )
            row = cursor.fetchone()
            if not row:
                raise HTTPException(status_code=400, detail="Verification link is invalid or expired")
            cursor.execute("UPDATE users SET is_active = TRUE WHERE id = %s AND role = 'candidate'", (row["user_id"],))
            cursor.execute("DELETE FROM candidate_email_verifications WHERE user_id = %s", (row["user_id"],))
            connection.commit()
            cursor.close()
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=503, detail="Verification is temporarily unavailable") from exc
    return {"verified": True}


def resend_verification(username: str, password: str) -> dict:
    settings = _mail_settings()
    try:
        with database() as connection:
            cursor = connection.cursor(dictionary=True)
            field = "u.email" if "@" in username else "u.username"
            cursor.execute(
                f"""SELECT u.id, u.username, u.email, u.password_hash, v.sent_at
                    FROM users u JOIN candidate_email_verifications v ON v.user_id = u.id
                    WHERE {field} = %s AND u.role = 'candidate' AND u.is_active = FALSE FOR UPDATE""",
                (username.lower() if "@" in username else username,),
            )
            user = cursor.fetchone()
            if not user or not verify_password(password, user["password_hash"]):
                raise HTTPException(status_code=401, detail="Invalid username or password")
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            if (now - user["sent_at"]).total_seconds() < RESEND_SECONDS:
                raise HTTPException(status_code=429, detail="Wait one minute before requesting another link")
            token, digest, expires = _verification_token()
            cursor.execute(
                "UPDATE candidate_email_verifications SET token_hash = %s, expires_at = %s, sent_at = %s WHERE user_id = %s",
                (digest, expires, now, user["id"]),
            )
            send_verification_email(settings=settings, email=user["email"], username=user["email"], token=token)
            connection.commit()
            cursor.close()
    except (mysql.connector.Error, OSError, smtplib.SMTPException) as exc:
        raise HTTPException(status_code=503, detail="Could not send verification email") from exc
    return {"message": "A new verification link was sent"}
