"""Encrypted session photos and face models for eSignet monitoring and recovery."""
import os
os.environ.setdefault("OPENCV_IO_MAX_IMAGE_PIXELS", "10000000")
import threading
from functools import lru_cache
from datetime import datetime, timezone
import cv2
import psycopg
import numpy as np
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from fastapi import HTTPException
from .config import ROOT_DIR
from .db import database
from .recordings import encryption_key

MODEL_DIR = ROOT_DIR / "backend" / "models"
DETECTOR_MODEL = MODEL_DIR / "face_detection_yunet_2023mar.onnx"
RECOGNIZER_MODEL = MODEL_DIR / "face_recognition_sface_2021dec.onnx"
MAX_IMAGE_BYTES = 3 * 1024 * 1024
MAX_IMAGE_SIDE = 3000
MIN_IMAGE_SIDE = 320
FACE_THRESHOLD = 0.40
_model_lock = threading.Lock()
_REASONS = {
    "selfie_face_missing": "Your face is not clear enough. Face the camera in good light and try again.",
    "multiple_faces": "More than one face is visible. Keep only your face in the camera view and try again.",
}


def _identity_key() -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                info=b"AssessBridge identity evidence v1").derive(encryption_key())


def _encrypt(data: bytes, invitation_id: int, kind: str) -> bytes:
    nonce = os.urandom(12)
    aad = f"identity:{invitation_id}:{kind}".encode()
    return nonce + AESGCM(_identity_key()).encrypt(nonce, data, aad)


def _decode_image(payload: bytes) -> tuple[np.ndarray, bytes]:
    if not payload or len(payload) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Each image must be 3 MB or smaller")
    if not (payload.startswith(b"\xff\xd8\xff") or payload.startswith(b"\x89PNG\r\n\x1a\n")):
        raise HTTPException(status_code=415, detail="Use a JPEG or PNG image")
    try:
        image = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
    except cv2.error as exc:
        raise HTTPException(status_code=422, detail="Could not read the image") from exc
    if image is None:
        raise HTTPException(status_code=422, detail="Could not read the image")
    height, width = image.shape[:2]
    if min(height, width) < MIN_IMAGE_SIDE or max(height, width) > MAX_IMAGE_SIDE:
        raise HTTPException(status_code=422, detail="Image resolution must be between 320 and 3000 pixels per side")
    success, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 88])
    if not success:
        raise HTTPException(status_code=422, detail="Could not prepare the image")
    return image, encoded


@lru_cache(maxsize=1)
def _face_models():
    if not DETECTOR_MODEL.is_file() or not RECOGNIZER_MODEL.is_file():
        raise HTTPException(status_code=503, detail="Face matching models are not installed on the server")
    try:
        detector = cv2.FaceDetectorYN.create(str(DETECTOR_MODEL), "", (320, 320), 0.72, 0.3, 100)
        recognizer = cv2.FaceRecognizerSF.create(str(RECOGNIZER_MODEL), "")
    except cv2.error as exc:
        raise HTTPException(status_code=503, detail="Face matching is unavailable") from exc
    return detector, recognizer


def _detect_one(detector, image: np.ndarray, kind: str = "selfie"):
    height, width = image.shape[:2]
    detector.setInputSize((width, height))
    try:
        _, faces = detector.detect(image)
    except cv2.error as exc:
        raise HTTPException(status_code=503, detail="Face matching is unavailable") from exc
    if faces is None or len(faces) == 0:
        return None, "selfie_face_missing"
    if len(faces) != 1:
        return None, "multiple_faces"
    if min(faces[0][2], faces[0][3]) < 110:
        return None, "selfie_face_missing"
    return faces[0], None


def identity_state(invitation_id: int, candidate_id: int, access_fingerprint: str) -> dict:
    try:
        with database() as connection:
            cursor = connection.cursor(dictionary=True)
            cursor.execute("""SELECT a.title, a.identity_policy, ic.status, ic.stage, ic.failure_reason, ic.attempts,
                                     ic.checked_at, ic.access_fingerprint, ic.evidence_expires_at, ic.verification_method,
                                     EXISTS(SELECT 1 FROM assessment_exam_sessions s WHERE s.invitation_id=i.id) AS started
                              FROM assessment_invitations i JOIN assessments a ON a.id = i.assessment_id
                              LEFT JOIN assessment_identity_checks ic ON ic.invitation_id = i.id AND ic.candidate_user_id = %s
                              WHERE i.id = %s AND i.candidate_user_id = %s""",
                           (candidate_id, invitation_id, candidate_id))
            row = cursor.fetchone()
            cursor.close()
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="Identity check unavailable") from exc
    from . import esignet
    current = row and row["access_fingerprint"] == access_fingerprint and row['verification_method'] == esignet.METHOD and esignet.enabled()
    if current and row["status"]=="passed" and not row["started"] and (not row["evidence_expires_at"] or row["evidence_expires_at"]<=datetime.now(timezone.utc).replace(tzinfo=None)):
        current=False
    return {"status": row["status"] if current else "required",
            "identity_policy": esignet.POLICY,
            "stage": (row["stage"] or "complete") if current else None,
            "message": _REASONS.get(row["failure_reason"]) if current and row["status"] == "failed" else None,
            "checked_at": row["checked_at"].isoformat() + "Z" if current and row["checked_at"] else None,
            "assessment_title": row["title"] if row else None,
            "attempts": row["attempts"] if row and row["attempts"] else 0,
            "verification_method": row['verification_method'] if current else None,
            "demo": esignet.state(invitation_id,candidate_id,access_fingerprint)}


def purge_expired_identity_evidence() -> int:
    try:
        with database() as connection:
            cursor = connection.cursor()
            cursor.execute("""UPDATE assessment_identity_checks
                              SET card_ciphertext = NULL, selfie_ciphertext = NULL
                              WHERE evidence_expires_at <= (statement_timestamp() AT TIME ZONE 'UTC')
                                AND (card_ciphertext IS NOT NULL OR selfie_ciphertext IS NOT NULL)""")
            count = cursor.rowcount
            connection.commit()
            cursor.close()
            return count
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="Identity evidence cleanup unavailable") from exc


def clear_prestart_identity_access(invitation_id: int, candidate_id: int, access_fingerprint: str) -> None:
    """A voluntary SEB exit ends this prestart verification without deleting its audit evidence."""
    try:
        with database() as connection:
            cursor = connection.cursor()
            cursor.execute("""UPDATE assessment_identity_checks SET access_fingerprint = NULL
                              WHERE invitation_id = %s AND candidate_user_id = %s AND access_fingerprint = %s""",
                           (invitation_id, candidate_id, access_fingerprint))
            connection.commit()
            cursor.close()
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="Identity check unavailable") from exc
