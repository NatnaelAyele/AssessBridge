"""Readiness and recovery checks bound to one candidate, invitation and client."""
from datetime import timedelta
import uuid

from fastapi import HTTPException

from .db import database
from .exam_sessions import load_exam, load_session, serialize_state, utc_now
from .connectivity import advance, begin_recovery, finish_recovery, ended_at, event


def client_id(value):
    try:
        return str(uuid.UUID(value or ""))
    except ValueError:
        raise HTTPException(428, "Reload the exam room to check this device")


def check_client(invitation, candidate, client, auth_token=None):
    with database() as connection:
        cursor = connection.cursor(dictionary=True)
        session = load_session(cursor, invitation)
        if not session:
            return
        if session["candidate_user_id"] != candidate:
            raise HTTPException(403, "This attempt belongs to another account")
        if ended_at(session):
            return
        if session["client_instance_id"] != client or (auth_token and session["auth_session_token_id"] != auth_token):
            raise HTTPException(428, "Reopen the exam room and complete recovery checks")


def prepare(invitation, candidate, client, auth_token):
    with database() as connection:
        cursor = connection.cursor(dictionary=True)
        exam = load_exam(cursor, invitation)
        session = load_session(cursor, invitation)
        now = utc_now()
        if session:
            if session["candidate_user_id"] != candidate:
                raise HTTPException(403, "This attempt belongs to another account")
            advance(cursor, session, now)
            if not ended_at(session) and (session["client_instance_id"] != client or session["auth_session_token_id"] != auth_token):
                begin_recovery(cursor, session, now, "session_restart")
                cursor.execute("UPDATE assessment_exam_sessions SET client_instance_id=%s,auth_session_token_id=%s,resume_required=TRUE,resume_identity_client=NULL WHERE id=%s", (client, auth_token, session["id"]))
                session.update(client_instance_id=client, resume_required=True)
                event(cursor, session["id"], "recovery_checks_required", now)
        result = serialize_state(cursor, exam, session, now)
        result["storage_scope"] = f"{candidate}:{invitation}"
        connection.commit()
        return result


def begin_probe(invitation, client):
    with database() as connection:
        cursor = connection.cursor()
        cursor.execute("INSERT INTO assessment_network_readiness (invitation_id,client_instance_id,checked_at,upload_started_at) VALUES (%s,%s,UTC_TIMESTAMP(),UTC_TIMESTAMP(3)) ON DUPLICATE KEY UPDATE checked_at=UTC_TIMESTAMP(),upload_started_at=UTC_TIMESTAMP(3),passed=FALSE,upload_bytes=0", (invitation, client))
        connection.commit()


def complete_probe(invitation, client, byte_count):
    with database() as connection:
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT * FROM assessment_network_readiness WHERE invitation_id=%s AND client_instance_id=%s FOR UPDATE", (invitation, client))
        row = cursor.fetchone()
        elapsed = max(1, int((utc_now() - row["upload_started_at"]).total_seconds()*1000)) if row and row["upload_started_at"] else 0
        if not elapsed or byte_count != 131072 or elapsed > 10000:
            raise HTTPException(422, "Connection is too slow or unstable. Restore it and retry the check.")
        cursor.execute("UPDATE assessment_network_readiness SET passed=TRUE,checked_at=UTC_TIMESTAMP(),upload_bytes=%s,upload_ms=%s,upload_started_at=NULL WHERE invitation_id=%s AND client_instance_id=%s", (byte_count, elapsed, invitation, client))
        connection.commit()
        return {"passed": True, "upload_ms": elapsed}


def require_readiness(cursor, invitation, client):
    cursor.execute("SELECT 1 FROM assessment_network_readiness WHERE invitation_id=%s AND client_instance_id=%s AND passed=TRUE AND checked_at>=UTC_TIMESTAMP()-INTERVAL 5 MINUTE", (invitation, client))
    if not cursor.fetchone():
        raise HTTPException(428, "Complete the connection and storage check before starting")


def resume(invitation, candidate, client):
    with database() as connection:
        cursor = connection.cursor(dictionary=True)
        exam = load_exam(cursor, invitation)
        session = load_session(cursor, invitation)
        if not session or session["candidate_user_id"] != candidate or session["client_instance_id"] != client:
            raise HTTPException(403, "Reopen the exam room")
        now = utc_now()
        advance(cursor, session, now)
        if not ended_at(session):
            require_readiness(cursor, invitation, client)
            if session["resume_required"] and session["resume_identity_client"] != client:
                raise HTTPException(403, "Repeat the live identity check before resuming")
            cursor.execute("UPDATE assessment_exam_sessions SET resume_required=FALSE,last_seen_at=%s WHERE id=%s", (now, session["id"]))
            session["resume_required"] = False
            finish_recovery(cursor, session, now)
            event(cursor, session["id"], "session_resumed", now)
        result = serialize_state(cursor, exam, session, now)
        connection.commit()
        return result


def verify_live(invitation, candidate, client, selfie):
    # Compare to the original verified photo; never replace the original evidence.
    from .identity import _decode_image, _face_models, _detect_one, _identity_key, FACE_THRESHOLD, _model_lock
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    import cv2
    with database() as connection:
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT selfie_ciphertext FROM assessment_identity_checks WHERE invitation_id=%s AND candidate_user_id=%s AND status='passed' AND evidence_expires_at>UTC_TIMESTAMP()", (invitation, candidate))
        row = cursor.fetchone()
    if not row or not row["selfie_ciphertext"]:
        raise HTTPException(409, "The verified identity image is unavailable. Contact the employer.")
    encrypted = bytes(row["selfie_ciphertext"])
    reference = AESGCM(_identity_key()).decrypt(encrypted[:12], encrypted[12:], f"identity:{invitation}:selfie".encode())
    reference, live = _decode_image(reference)[0], _decode_image(selfie)[0]
    with _model_lock:
        detector, recognizer = _face_models()
        face1, failure1 = _detect_one(detector, reference, "selfie")
        face2, failure2 = _detect_one(detector, live, "selfie")
        unusable = bool(failure1 or failure2)
        score = None if unusable else float(recognizer.match(recognizer.feature(recognizer.alignCrop(reference, face1)), recognizer.feature(recognizer.alignCrop(live, face2)), cv2.FaceRecognizerSF_FR_COSINE))
    import math
    passed = score is not None and math.isfinite(score) and score >= FACE_THRESHOLD
    with database() as connection:
        cursor = connection.cursor(dictionary=True)
        session = load_session(cursor, invitation)
        now = utc_now()
        if not session or session["candidate_user_id"] != candidate or session["client_instance_id"] != client:
            raise HTTPException(403, "Reopen the exam room")
        advance(cursor, session, now)
        if not ended_at(session):
            cursor.execute("UPDATE assessment_exam_sessions SET resume_identity_client=%s WHERE id=%s", (client if passed else None, session["id"]))
            event(cursor, session["id"], "recovery_identity_check", now,
                  {"passed": passed, "status": "unusable" if unusable else "passed" if passed else "not_matched",
                   "similarity": score if score is not None and math.isfinite(score) else None})
        connection.commit()
    if unusable:
        raise HTTPException(422, "Show one clear face to the camera and retry")
    return {"passed": passed, "message": "Identity confirmed" if passed else "Face does not match your verified image"}
