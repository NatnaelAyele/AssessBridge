"""Encrypted exam media parts, ordered uploads, retention, and protected playback."""

import base64
import hashlib
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

import mysql.connector
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from .config import RECORDING_KEY, RECORDING_RETENTION_DAYS, RECORDINGS_DIR
from .db import database


MAX_CHUNK_BYTES = 8 * 1024 * 1024
from .connectivity import FINAL_UPLOAD_SECONDS, ended_at, advance
ALLOWED_MIME_TYPES = {"video/webm", "video/mp4"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def encryption_key() -> bytes:
    try:
        raw = base64.urlsafe_b64decode(RECORDING_KEY + "=" * (-len(RECORDING_KEY) % 4))
    except (ValueError, base64.binascii.Error) as exc:
        raise HTTPException(status_code=503, detail="Recording storage is not configured") from exc
    if len(raw) != 32:
        raise HTTPException(status_code=503, detail="Recording storage is not configured")
    return raw


def validate_mime(value: str) -> str:
    clean = value.strip().lower()
    if len(clean) > 120 or clean.split(";", 1)[0] not in ALLOWED_MIME_TYPES:
        raise HTTPException(status_code=415, detail="This video format is not supported")
    return clean


def create_for_session(cursor, session_id: int, started_at: datetime) -> None:
    cursor.execute(
        """INSERT INTO assessment_recordings (session_id, storage_token, started_at)
           VALUES (%s, %s, %s) ON DUPLICATE KEY UPDATE id = id""",
        (session_id, secrets.token_hex(16), started_at),
    )


def _recording(cursor, invitation_id: int, candidate_id: int) -> dict:
    cursor.execute(
        """SELECT r.id AS recording_id, r.storage_token, r.status AS recording_status,
                  s.id AS session_id, s.started_at, s.deadline_at, s.submitted_at, s.disqualified_at, s.interrupted_at
           FROM assessment_exam_sessions s
           JOIN assessment_recordings r ON r.session_id = s.id
           WHERE s.invitation_id = %s AND s.candidate_user_id = %s FOR UPDATE""",
        (invitation_id, candidate_id),
    )
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=403, detail="Start the exam before recording")
    return row


def _can_upload(row: dict, now: datetime) -> None:
    ended = ended_at(row)
    if ended and now > ended + timedelta(seconds=FINAL_UPLOAD_SECONDS):
        raise HTTPException(status_code=409, detail="Recording upload window has closed")
    if row["recording_status"] != "recording":
        raise HTTPException(status_code=409, detail="Recording collection has finished")
    if not ended and now >= row["deadline_at"]:
        raise HTTPException(status_code=409, detail="The exam deadline has passed")


def open_part(invitation_id: int, candidate_id: int, mime_type: str) -> dict:
    encryption_key()
    mime_type = validate_mime(mime_type)
    try:
        with database() as connection:
            cursor = connection.cursor(dictionary=True)
            recording = _recording(cursor, invitation_id, candidate_id)
            now = utc_now()
            if ended_at(recording) or now >= recording["deadline_at"]:
                raise HTTPException(status_code=409, detail="The exam is no longer active")
            cursor.execute(
                """INSERT INTO assessment_recording_parts (recording_id, mime_type, started_at)
                   VALUES (%s, %s, %s)""",
                (recording["recording_id"], mime_type, now),
            )
            part_id = cursor.lastrowid
            from .monitoring.repository import create_job
            create_job(cursor, recording["session_id"], part_id)
            connection.commit()
            cursor.close()
            return {"part_id": part_id, "next_sequence": 0}
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=503, detail="Recording service unavailable") from exc


def _part(cursor, invitation_id: int, candidate_id: int, part_id: int) -> tuple[dict, dict]:
    recording = _recording(cursor, invitation_id, candidate_id)
    cursor.execute(
        "SELECT id, recording_id, status, mime_type, total_bytes FROM assessment_recording_parts WHERE id = %s AND recording_id = %s FOR UPDATE",
        (part_id, recording["recording_id"]),
    )
    part = cursor.fetchone()
    if not part:
        raise HTTPException(status_code=404, detail="Recording part not found")
    return recording, part


def _chunk_path(token: str, part_id: int, sequence: int) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", token):
        raise RuntimeError("Invalid recording storage token")
    return RECORDINGS_DIR / token / str(part_id) / f"{sequence:08d}.enc"


def _aad(part_id: int, sequence: int) -> bytes:
    return f"AssessBridge-recording:{part_id}:{sequence}".encode()


def store_chunk(invitation_id: int, candidate_id: int, part_id: int, sequence: int, payload: bytes) -> dict:
    if not payload or len(payload) > MAX_CHUNK_BYTES:
        raise HTTPException(status_code=413, detail="Recording chunk must be between 1 byte and 8 MB")
    key = encryption_key()
    digest = hashlib.sha256(payload).hexdigest()
    created_path = None
    temporary = None
    try:
        with database() as connection:
            cursor = connection.cursor(dictionary=True)
            recording, part = _part(cursor, invitation_id, candidate_id, part_id)
            now = utc_now()
            _can_upload(recording, now)
            cursor.execute(
                "SELECT size_bytes, sha256 FROM assessment_recording_chunks WHERE part_id = %s AND sequence_number = %s",
                (part_id, sequence),
            )
            existing = cursor.fetchone()
            if existing:
                if existing["size_bytes"] != len(payload) or existing["sha256"] != digest:
                    raise HTTPException(status_code=409, detail="Recording chunk conflicts with an earlier upload")
                return {"received": True, "sequence": sequence, "duplicate": True}
            if part["status"] != "recording":
                raise HTTPException(status_code=409, detail="This recording part has ended")
            cursor.execute(
                "SELECT MAX(sequence_number) AS last_sequence FROM assessment_recording_chunks WHERE part_id = %s",
                (part_id,),
            )
            last = cursor.fetchone()["last_sequence"]
            if sequence != (last + 1 if last is not None else 0):
                raise HTTPException(status_code=409, detail="Recording chunks must arrive in order")
            path = _chunk_path(recording["storage_token"], part_id, sequence)
            path.parent.mkdir(parents=True, exist_ok=True)
            nonce = secrets.token_bytes(12)
            encrypted = nonce + AESGCM(key).encrypt(nonce, payload, _aad(part_id, sequence))
            temporary = path.with_name(path.name + "." + secrets.token_hex(6) + ".tmp")
            with temporary.open("xb") as file:
                file.write(encrypted)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
            created_path = path
            cursor.execute(
                """INSERT INTO assessment_recording_chunks
                   (part_id, sequence_number, size_bytes, sha256, received_at)
                   VALUES (%s, %s, %s, %s, %s)""",
                (part_id, sequence, len(payload), digest, now),
            )
            cursor.execute(
                "UPDATE assessment_recording_parts SET total_bytes = total_bytes + %s WHERE id = %s",
                (len(payload), part_id),
            )
            connection.commit()
            cursor.close()
            return {"received": True, "sequence": sequence, "duplicate": False}
    except (mysql.connector.Error, OSError) as exc:
        if temporary:
            temporary.unlink(missing_ok=True)
        if created_path:
            created_path.unlink(missing_ok=True)
        raise HTTPException(status_code=503, detail="Could not store recording chunk") from exc


def finish_part(invitation_id: int, candidate_id: int, part_id: int, final_sequence: int, interrupted: bool = False) -> dict:
    try:
        with database() as connection:
            cursor = connection.cursor(dictionary=True)
            recording, part = _part(cursor, invitation_id, candidate_id, part_id)
            now = utc_now()
            _can_upload(recording, now)
            if part["status"] != "recording":
                return {"status": part["status"]}
            cursor.execute(
                "SELECT COUNT(*) AS count, MAX(sequence_number) AS last_sequence FROM assessment_recording_chunks WHERE part_id = %s",
                (part_id,),
            )
            chunks = cursor.fetchone()
            if (not interrupted and (chunks["count"] != final_sequence + 1 or (final_sequence >= 0 and chunks["last_sequence"] != final_sequence))) or chunks["count"] > final_sequence + 1:
                raise HTTPException(status_code=409, detail="Some recording chunks are still missing")
            status = "complete" if chunks["count"] and not interrupted else "incomplete"
            cursor.execute(
                "UPDATE assessment_recording_parts SET status = %s, finalized_at = %s WHERE id = %s",
                (status, now, part_id),
            )
            connection.commit()
            cursor.close()
            return {"status": status}
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=503, detail="Recording service unavailable") from exc


def finish_recording(invitation_id: int, candidate_id: int) -> dict:
    try:
        with database() as connection:
            cursor = connection.cursor(dictionary=True)
            recording = _recording(cursor, invitation_id, candidate_id)
            now = utc_now()
            ended = ended_at(recording)
            if not ended:
                raise HTTPException(status_code=409, detail="The exam is still active")
            if recording["recording_status"] != "recording":
                return {"status": recording["recording_status"]}
            _can_upload(recording, now)
            cursor.execute(
                "SELECT status, total_bytes FROM assessment_recording_parts WHERE recording_id = %s",
                (recording["recording_id"],),
            )
            parts = cursor.fetchall()
            cursor.execute("UPDATE assessment_recording_parts SET status='incomplete',finalized_at=%s WHERE recording_id=%s AND status='recording'", (now, recording["recording_id"]))
            status = "complete" if parts and all(part["status"] == "complete" for part in parts) else "incomplete"
            cursor.execute(
                """UPDATE assessment_recordings SET status = %s, finalized_at = %s, expires_at = %s
                   WHERE id = %s""",
                (status, now, ended + timedelta(days=RECORDING_RETENTION_DAYS), recording["recording_id"]),
            )
            connection.commit()
            cursor.close()
            return {"status": status, "parts": len(parts), "upload_deadline_at": (ended_at(recording) + timedelta(seconds=FINAL_UPLOAD_SECONDS)).isoformat()+"Z" if ended_at(recording) else None,
                    "total_bytes": sum(part["total_bytes"] for part in parts)}
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=503, detail="Recording service unavailable") from exc


def recording_exit_state(invitation_id: int, candidate_id: int) -> dict:
    """Confirm collection has ended before offering the normal SEB quit link."""
    with database() as connection:
        cursor = connection.cursor(dictionary=True)
        row = _recording(cursor, invitation_id, candidate_id)
        ended = ended_at(row)
        if not ended:
            raise HTTPException(409, "Submit the exam before exiting")
        now = utc_now()
        status = row['recording_status']
        if status == 'recording':
            if now < ended + timedelta(seconds=FINAL_UPLOAD_SECONDS):
                raise HTTPException(409, "Recording upload is still pending. Keep SEB open.")
            cursor.execute("UPDATE assessment_recording_parts SET status='incomplete',finalized_at=%s WHERE recording_id=%s AND status='recording'", (now, row['recording_id']))
            cursor.execute("UPDATE assessment_recordings SET status='incomplete',finalized_at=%s,expires_at=%s WHERE id=%s", (now, ended+timedelta(days=RECORDING_RETENTION_DAYS), row['recording_id']))
            status = 'incomplete'
        connection.commit()
        return {'ready': True, 'recording_status': status, 'exit_url': '/seb/exit'}


def recording_state(invitation_id: int, candidate_id: int) -> dict:
    try:
        with database() as connection:
            cursor = connection.cursor(dictionary=True)
            recording = _recording(cursor, invitation_id, candidate_id)
            cursor.execute(
                "SELECT id, status, total_bytes FROM assessment_recording_parts WHERE recording_id = %s ORDER BY id",
                (recording["recording_id"],),
            )
            parts = cursor.fetchall()
            cursor.close()
            return {"status": recording["recording_status"], "parts": parts,
                    "upload_deadline_at": (ended_at(recording) + timedelta(seconds=FINAL_UPLOAD_SECONDS)).isoformat()+"Z" if ended_at(recording) else None,
                    "total_bytes": sum(part["total_bytes"] for part in parts)}
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=503, detail="Recording service unavailable") from exc


def finalize_stale_recordings() -> None:
    now = utc_now()
    with database() as connection:
        cursor = connection.cursor()
        cursor.execute(
            """UPDATE assessment_recording_parts p
               JOIN assessment_recordings r ON r.id = p.recording_id
               JOIN assessment_exam_sessions s ON s.id = r.session_id
               SET p.status = 'incomplete', p.finalized_at = %s
               WHERE p.status = 'recording' AND COALESCE(s.submitted_at, s.disqualified_at, s.interrupted_at) < %s""",
            (now, now - timedelta(seconds=FINAL_UPLOAD_SECONDS)),
        )
        cursor.execute(
            """UPDATE assessment_recordings r
               JOIN assessment_exam_sessions s ON s.id = r.session_id
               LEFT JOIN (
                   SELECT recording_id, COUNT(*) AS part_count,
                          SUM(status <> 'complete') AS incomplete_count
                   FROM assessment_recording_parts GROUP BY recording_id
               ) p ON p.recording_id = r.id
               SET r.status = CASE WHEN p.part_count > 0 AND p.incomplete_count = 0
                                   THEN 'complete' ELSE 'incomplete' END,
                   r.finalized_at = %s,
                   r.expires_at = DATE_ADD(COALESCE(s.submitted_at, s.disqualified_at, s.interrupted_at), INTERVAL %s DAY)
               WHERE r.status = 'recording' AND COALESCE(s.submitted_at, s.disqualified_at, s.interrupted_at) < %s""",
            (now, RECORDING_RETENTION_DAYS, now - timedelta(seconds=FINAL_UPLOAD_SECONDS)),
        )
        connection.commit()
        cursor.close()


def purge_expired_recordings() -> int:
    with database() as connection:
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT id, storage_token FROM assessment_recordings WHERE expires_at <= %s AND status <> 'deleted'", (utc_now(),))
        expired = cursor.fetchall()
        count = 0
        for item in expired:
            token = item["storage_token"]
            if not re.fullmatch(r"[0-9a-f]{32}", token):
                raise RuntimeError("Invalid recording storage token")
            directory = (RECORDINGS_DIR / token).resolve()
            if directory.parent != RECORDINGS_DIR:
                raise RuntimeError("Recording storage path escaped its directory")
            if directory.is_dir():
                for part in directory.iterdir():
                    if part.is_dir():
                        for chunk in part.iterdir():
                            if chunk.is_file():
                                chunk.unlink()
                        part.rmdir()
                directory.rmdir()
            cursor.execute("DELETE c FROM assessment_recording_chunks c JOIN assessment_recording_parts p ON p.id = c.part_id WHERE p.recording_id = %s", (item["id"],))
            cursor.execute("DELETE FROM assessment_recording_parts WHERE recording_id = %s", (item["id"],))
            cursor.execute("UPDATE assessment_recordings SET status = 'deleted' WHERE id = %s", (item["id"],))
            connection.commit()
            count += 1
        cursor.close()
        return count


def employer_recording(assessment_id: int, invitation_id: int, employer_id: int, source_ip: str | None, actor_id: int | None = None) -> dict:
    try:
        with database() as connection:
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                """SELECT r.id, r.storage_token, r.status, r.expires_at
                   FROM assessments a
                   JOIN assessment_invitations i ON i.assessment_id = a.id
                   JOIN assessment_exam_sessions s ON s.invitation_id = i.id
                   JOIN assessment_recordings r ON r.session_id = s.id
                   WHERE a.id = %s AND i.id = %s AND a.employer_user_id = %s""",
                (assessment_id, invitation_id, employer_id),
            )
            recording = cursor.fetchone()
            if not recording or recording["status"] == "deleted" or (recording["expires_at"] and recording["expires_at"] <= utc_now()):
                raise HTTPException(status_code=404, detail="Recording not found")
            cursor.execute(
                """SELECT id, status, mime_type, total_bytes, started_at, finalized_at
                   FROM assessment_recording_parts WHERE recording_id = %s ORDER BY id""",
                (recording["id"],),
            )
            parts = cursor.fetchall()
            cursor.execute(
                """INSERT INTO assessment_recording_access_audit
                   (recording_id, actor_user_id, accessed_at, source_ip) VALUES (%s, %s, %s, %s)""",
                (recording["id"], actor_id or employer_id, utc_now(), source_ip),
            )
            connection.commit()
            cursor.close()
            return {**recording, "parts": parts}
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=503, detail="Recording service unavailable") from exc


def stream_part(recording: dict, part_id: int, range_header: str | None) -> StreamingResponse:
    part = next((item for item in recording["parts"] if item["id"] == part_id), None)
    if not part or not part["total_bytes"]:
        raise HTTPException(status_code=404, detail="Recording part not found")
    try:
        with database() as connection:
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                """SELECT sequence_number, size_bytes, sha256 FROM assessment_recording_chunks
                   WHERE part_id = %s ORDER BY sequence_number""",
                (part_id,),
            )
            chunks = cursor.fetchall()
            cursor.close()
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=503, detail="Recording service unavailable") from exc
    total = sum(chunk["size_bytes"] for chunk in chunks)
    if total != part["total_bytes"] or not chunks:
        raise HTTPException(status_code=503, detail="Recording is incomplete")
    start, end = 0, total - 1
    if range_header:
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
        if not match or not any(match.groups()):
            raise HTTPException(status_code=416, detail="Invalid recording byte range")
        if match.group(1):
            start = int(match.group(1))
            end = min(int(match.group(2)), total - 1) if match.group(2) else total - 1
        else:
            start = max(0, total - int(match.group(2)))
        if start > end or start >= total:
            raise HTTPException(status_code=416, detail="Recording byte range is unavailable")

    def contents():
        offset = 0
        aes = AESGCM(encryption_key())
        for chunk in chunks:
            next_offset = offset + chunk["size_bytes"]
            if next_offset <= start:
                offset = next_offset
                continue
            if offset > end:
                break
            encrypted = _chunk_path(recording["storage_token"], part_id, chunk["sequence_number"]).read_bytes()
            plain = aes.decrypt(encrypted[:12], encrypted[12:], _aad(part_id, chunk["sequence_number"]))
            if len(plain) != chunk["size_bytes"] or hashlib.sha256(plain).hexdigest() != chunk["sha256"]:
                raise RuntimeError("Recording integrity check failed")
            yield plain[max(0, start - offset):min(len(plain), end - offset + 1)]
            offset = next_offset

    headers = {"Accept-Ranges": "bytes", "Content-Length": str(end - start + 1),
               "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
    if range_header:
        headers["Content-Range"] = f"bytes {start}-{end}/{total}"
    return StreamingResponse(contents(), status_code=206 if range_header else 200,
                             media_type=part["mime_type"].split(";", 1)[0], headers=headers)
