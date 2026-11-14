"""Photo ID text and portrait comparison before an exam begins.

This is a card-to-camera comparison. It does not authenticate a Fayda number
against the National ID Program or prove that the camera image is live.
"""

import os
os.environ.setdefault("OPENCV_IO_MAX_IMAGE_PIXELS", "10000000")

import math
import csv
import io
import re
import shutil
import subprocess
import threading
import unicodedata
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

import cv2
import mysql.connector
import numpy as np
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from fastapi import HTTPException

from .config import ROOT_DIR
from .retention import for_invitation
from .db import database
from .recordings import encryption_key
from .card_capture import locate_outline, crop_from_corners
from .name_matching import match_layout, split_columns
from .passport_names import read_passport_name


MODEL_DIR = ROOT_DIR / "backend" / "models"
DETECTOR_MODEL = MODEL_DIR / "face_detection_yunet_2023mar.onnx"
RECOGNIZER_MODEL = MODEL_DIR / "face_recognition_sface_2021dec.onnx"
MAX_IMAGE_BYTES = 3 * 1024 * 1024
MAX_IMAGE_SIDE = 3000
MIN_IMAGE_SIDE = 320
FACE_THRESHOLD = 0.40  # Conservative starting point; must be calibrated on real Fayda cards.
NAME_READABILITY_THRESHOLD = 0.35
_model_lock = threading.Lock()
_REASONS = {
    "card_not_found": "Could not detect the card outline. Select Adjust card outline, or capture the card again.",
    "card_unreadable": "The card text is too blurred to read. Hold the card still, avoid glare, and capture it again.",
    "not_fayda": "The card title or Ethiopian flag could not be recognized. Hold the full card still in the camera view and try again.",
    "name_unreadable": "The printed name is too unclear to read. Hold the card still and capture it again in brighter, even light.",
    "name_mismatch": "Could not match the printed name to the invitation. Retake a clearer photo and check the invitation name.",
    "card_face_missing": "The portrait on the card is not clear enough. Move the card closer and try again.",
    "selfie_face_missing": "Your face is not clear enough. Face the camera in good light and try again.",
    "multiple_faces": "More than one face is visible. Keep other people and photos showing faces out of the camera view, then try again.",
    "face_mismatch": "The card portrait could not be matched to the camera image. Try again in better light.",
}
_STAGES = {
    "card_not_found": "card_capture", "card_unreadable": "card_quality", "not_fayda": "card_type",
    "name_unreadable": "name", "name_mismatch": "name", "card_face_missing": "card_portrait",
    "selfie_face_missing": "camera_face", "multiple_faces": "camera_face", "face_mismatch": "face_comparison",
}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _identity_key() -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                info=b"AssessBridge identity evidence v1").derive(encryption_key())


def _encrypt(data: bytes, invitation_id: int, kind: str) -> bytes:
    nonce = os.urandom(12)
    aad = f"identity:{invitation_id}:{kind}".encode()
    return nonce + AESGCM(_identity_key()).encrypt(nonce, data, aad)


def _tesseract() -> str:
    configured = os.getenv("TESSERACT_CMD", "").strip()
    if configured:
        if Path(configured).is_file():
            return configured
        raise HTTPException(status_code=503, detail="Configured card reader was not found")
    command = shutil.which("tesseract")
    if command:
        return command
    for candidate in (ROOT_DIR / "var" / "tesseract" / "tesseract.exe",
                      Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
                      Path.home() / "AppData" / "Local" / "Programs" / "Tesseract-OCR" / "tesseract.exe"):
        if candidate.is_file():
            return str(candidate)
    raise HTTPException(status_code=503, detail="Card reading is not installed on the server")


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


def _tokens(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKD", value.casefold())
    normalized = "".join(c for c in normalized if not unicodedata.combining(c))
    return [token for token in re.split(r"[^\w]+", normalized) if token and not token.isdigit()]


def _read_layout(gray: np.ndarray, *, psm: int = 11) -> list[dict]:
    """Retain OCR word positions and confidence in memory for name grouping."""
    encoded = cv2.imencode(".png", gray)[1].tobytes()
    try:
        completed = subprocess.run(
            [_tesseract(), "stdin", "stdout", "-l", "eng", "--psm", str(psm), "tsv"],
            input=encoded, capture_output=True, timeout=12, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(status_code=503, detail="Card reading is temporarily unavailable") from exc
    if completed.returncode:
        raise HTTPException(status_code=503, detail="Card reading is temporarily unavailable")
    groups = {}
    for row in csv.DictReader(io.StringIO(completed.stdout.decode("utf-8", "replace")), delimiter="\t", quoting=csv.QUOTE_NONE):
        word = row.get("text", "").strip()
        if not word or row.get("level") != "5":
            continue
        key = tuple(row.get(field) for field in ("page_num", "block_num", "par_num", "line_num"))
        groups.setdefault(key, []).append({"text": word, "confidence": max(0.0, float(row["conf"]))/100,
                                             **{field: int(row[field]) for field in ("left", "top", "width", "height")}})
    result = []
    for words in groups.values():
        words.sort(key=lambda word: word["left"])
        left, top = min(word["left"] for word in words), min(word["top"] for word in words)
        result.append({"text": " ".join(word["text"] for word in words), "words": words,
                       "box": (left, top, max(word["left"]+word["width"] for word in words)-left,
                               max(word["top"]+word["height"] for word in words)-top)})
    return result


def _read_text(gray: np.ndarray, *, psm: int = 11) -> list[tuple[str, float]]:
    return [(line["text"], sum(word["confidence"]*len(word["text"]) for word in line["words"])/
             sum(len(word["text"]) for word in line["words"])) for line in _read_layout(gray, psm=psm)]


def _prepare_text(image: np.ndarray, mode: str, target: int) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if mode == "lighting":
        # Remove slow lighting gradients without amplifying every background mark.
        background = cv2.GaussianBlur(gray, (0, 0), max(9, gray.shape[1] / 45))
        gray = cv2.divide(gray, np.maximum(background, 1), scale=255)
    elif mode == "adaptive":
        gray = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                     cv2.THRESH_BINARY, 41, 9)
    elif mode == "clahe":
        gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    scale = min(3.0, target / max(gray.shape))
    if scale != 1:
        gray = cv2.resize(gray, None, fx=scale, fy=scale,
                          interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA)
    return cv2.copyMakeBorder(gray, 24, 24, 24, 24, cv2.BORDER_CONSTANT, value=255)


def _ocr(image: np.ndarray, *, enlarged: bool = False) -> list[str]:
    height, width = image.shape[:2]
    target = 2400 if enlarged else 1800
    if max(height, width) != target:
        scale = target / max(height, width)
        if scale < 1 or (enlarged and scale > 1):
            image = cv2.resize(image, (round(width * scale), round(height * scale)),
                               interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    encoded = cv2.imencode(".png", gray)[1].tobytes()
    try:
        completed = subprocess.run([_tesseract(), "stdin", "stdout", "-l", "eng", "--psm", "11"],
                                   input=encoded, capture_output=True, timeout=12, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(status_code=503, detail="Card reading is temporarily unavailable") from exc
    if completed.returncode:
        raise HTTPException(status_code=503, detail="Card reading is temporarily unavailable")
    return [line.strip() for line in completed.stdout.decode("utf-8", "replace").splitlines() if line.strip()]


def _has_fayda_heading(lines: list[str]) -> bool:
    """Recognize the English headings used on Fayda card and eFayda layouts."""
    tokens = _tokens(" ".join(lines))
    if any(len(token) >= 5 and SequenceMatcher(None, token, "fayda").ratio() >= 0.8 for token in tokens):
        return True
    text = " ".join(tokens)
    if "ethiopian digital id" in text or "ethiopian national id" in text:
        return True
    # The country word is often lost to glare or to the Amharic line above it.
    # Still require a card label, then check the invited name and portrait below.
    for first, second, third in zip(tokens, tokens[1:], tokens[2:]):
        if (SequenceMatcher(None, first, "digital").ratio() >= 0.75
                and second == "id" and SequenceMatcher(None, third, "card").ratio() >= 0.75):
            return True
    return "national id" in text and any(token.startswith("ethiop") for token in tokens)


def _find_fayda_flag(image: np.ndarray) -> tuple[int, int, int, int] | None:
    """Locate the flag's colored bands as a card-layout cue, not issuer proof."""
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    bands = (
        cv2.inRange(hsv, (50, 50, 50), (100, 255, 255)),
        cv2.inRange(hsv, (15, 50, 50), (40, 255, 255)),
        cv2.bitwise_or(cv2.inRange(hsv, (0, 50, 40), (12, 255, 255)),
                       cv2.inRange(hsv, (165, 50, 40), (179, 255, 255))),
    )
    sums = [cv2.integral((band > 0).astype(np.uint8), sdepth=cv2.CV_32S) for band in bands]

    def fraction(integral: np.ndarray, x: int, y: int, width: int, height: int) -> float:
        count = integral[y + height, x + width] - integral[y, x + width] - integral[y + height, x] + integral[y, x]
        return float(count) / (width * height)

    image_height, image_width = image.shape[:2]
    best = None
    best_score = 0.0
    for width in (36, 48, 60, 72, 96, 120, 150, 180, 220, 270):
        height = round(width * .6 / 3) * 3
        if width > image_width or height > image_height:
            continue
        stripe = height // 3
        for y in range(0, image_height - height + 1, max(6, height // 8)):
            for x in range(0, image_width - width + 1, max(8, width // 8)):
                green = fraction(sums[0], x, y, width, stripe)
                if green < 0.42:
                    continue
                yellow = fraction(sums[1], x, y + stripe, width, stripe)
                if yellow < 0.18:
                    continue
                red = fraction(sums[2], x, y + stripe * 2, width, stripe)
                score = green + yellow + red + 0.15 * width / 150
                if red >= 0.25 and score > best_score:
                    best, best_score = (x, y, width, height), score
    return best


def _has_fayda_flag(image: np.ndarray) -> bool:
    return _find_fayda_flag(image) is not None


def _locate_card(image: np.ndarray, *, other_id: bool = False, preserve_pixels: bool = False) -> tuple[np.ndarray | None, str | None]:
    """Locate a card outline; retain Fayda's flag fallback when appropriate."""
    region = locate_outline(image, preserve_pixels=preserve_pixels)
    if region is not None:
        return region, "outline"
    if other_id:
        return None, None
    height, width = image.shape[:2]
    flag = _find_fayda_flag(image)
    if flag:
        x, y, flag_width, _ = flag
        card_width = round(flag_width * 8.2)
        card_height = round(card_width / 1.6)
        left, top = max(0, round(x - flag_width * 0.5)), max(0, round(y - flag_width * 0.3))
        right, bottom = min(width, left + card_width), min(height, top + card_height)
        if right - left >= 300 and bottom - top >= 230:
            return image[top:bottom, left:right], "flag_crop"
    return None, None


def _read_spatial_name(image: np.ndarray, expected: str, target: int = 2400) -> tuple[float, float]:
    best_score, readability = 0.0, 0.0
    for mode, psm in (("gray", 11), ("lighting", 11), ("adaptive", 11), ("lighting", 6)):
        prepared = _prepare_text(image, mode, target)
        score, confidence = match_layout(expected, _read_layout(prepared, psm=psm), prepared.shape,
                                         NAME_READABILITY_THRESHOLD)
        best_score, readability = max(best_score, score), max(readability, confidence)
        if score >= .85 and confidence >= NAME_READABILITY_THRESHOLD:
            return score, confidence
    return best_score, readability


def _read_name_lines(image: np.ndarray, expected: str) -> tuple[float, float]:
    """Re-read individual text cells without a portrait or other columns.

    Coordinates and field captions are retained: this is not a search for
    invitation words anywhere on a document. Work is bounded per capture.
    """
    prepared = _prepare_text(image, 'lighting', 2400)
    lines = split_columns(_read_layout(prepared, psm=11))
    if len(lines) > 40:
        return 0.0, 0.0
    refined = []
    for line in lines:
        x, y, w, h = line['box']
        if h < 10 or w < 20 or '<' in line['text'] or any(c.isdigit() for c in line['text']):
            refined.append(line)
            continue
        left, top = max(0, x-5), max(0, y-5)
        crop = prepared[top:min(prepared.shape[0], y+h+5), left:min(prepared.shape[1], x+w+5)]
        readings = _read_layout(crop, psm=7)
        if len(readings) != 1:
            refined.append(line)
            continue
        reading = readings[0]
        words = [{**word, 'left': word['left']+left, 'top': word['top']+top} for word in reading['words']]
        refined.append({**reading, 'words': words, 'box': line['box']})
    return match_layout(expected, refined, prepared.shape, NAME_READABILITY_THRESHOLD)


def _read_name(card: np.ndarray, expected: str) -> tuple[float, float]:
    """Try Fayda's usual name area first, then use the same spatial reader on the card."""
    height, width = card.shape[:2]
    area = card[round(height*.12):round(height*.55), round(width*.30):round(width*.98)]
    first = _read_spatial_name(area, expected, 1600) if area.size else (0.0, 0.0)
    if first[0] >= .85:
        return first
    full = _read_spatial_name(card, expected)
    if full[0] >= .85:
        return full
    local = _read_name_lines(card, expected)
    return max((first, full, local), key=lambda reading: (reading[0], reading[1]))


def _read_other_name(card: np.ndarray, expected: str) -> tuple[float, float]:
    visual = _read_spatial_name(card, expected)
    if visual[0] >= .85:
        return visual
    passport = read_passport_name(card, expected, _read_layout)
    if passport[0] >= .85:
        return passport
    local = _read_name_lines(card, expected)
    return max((visual, passport, local), key=lambda reading: (reading[0], reading[1]))



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


def _detect_one(detector, image: np.ndarray, kind: str):
    height, width = image.shape[:2]
    detector.setInputSize((width, height))
    try:
        _, faces = detector.detect(image)
    except cv2.error as exc:
        raise HTTPException(status_code=503, detail="Face matching is unavailable") from exc
    if faces is None or len(faces) == 0:
        return None, "card_face_missing" if kind == "card" else "selfie_face_missing"
    if kind == "card":
        # Fayda cards can have a small secondary portrait or hologram. A room
        # photo can also appear behind the card. Use only a dominant portrait.
        faces = sorted(faces, key=lambda item: float(item[2] * item[3]), reverse=True)
        if len(faces) > 1 and float(faces[0][2] * faces[0][3]) < 3 * float(faces[1][2] * faces[1][3]):
            return None, "multiple_faces"
    elif len(faces) != 1:
        return None, "multiple_faces"
    face = faces[0]
    if min(face[2], face[3]) < (35 if kind == "card" else 110):
        return None, "card_face_missing" if kind == "card" else "selfie_face_missing"
    return face, None


def _face_feature(detector, recognizer, image: np.ndarray, face):
    """Refine landmarks at a consistent scale around the already selected face.

    Selection and multiple-face checks happen on the whole source first. The
    refinement must overlap that face and cannot select a different portrait.
    No comparison score is used to choose a crop or to accept a refinement.
    """
    x, y, width, height = (float(value) for value in face[:4])
    left, top = max(0, int(x-width)), max(0, int(y-height))
    right, bottom = min(image.shape[1], int(x+2*width)), min(image.shape[0], int(y+2*height))
    region = image[top:bottom, left:right]
    if not region.size:
        return recognizer.feature(recognizer.alignCrop(image, face))
    scale = 320/max(region.shape[:2])
    region = cv2.resize(region, None, fx=scale, fy=scale,
                        interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA)
    refined, failure = _detect_one(detector, region, 'card')
    if not failure:
        rx, ry, rw, rh = (float(value)/scale for value in refined[:4])
        rx, ry = rx+left, ry+top
        intersection = max(0, min(x+width, rx+rw)-max(x, rx))*max(0, min(y+height, ry+rh)-max(y, ry))
        overlap = intersection/max(1, width*height+rw*rh-intersection)
        if overlap >= .5:
            return recognizer.feature(recognizer.alignCrop(region, refined))
    return recognizer.feature(recognizer.alignCrop(image, face))


def compare_card_and_face(card_bytes: bytes, selfie_bytes: bytes, invited_name: str,
                          identity_policy: str = "Fayda ID", card_corners=None) -> dict:
    card, card_jpeg = _decode_image(card_bytes)
    selfie, selfie_jpeg = _decode_image(selfie_bytes)
    result = {"status": "failed", "stage": "card_capture", "reason": "card_not_found",
              "name_similarity": None, "face_similarity": None, "name_readability": None,
              "card_detection_method": None, "card_jpeg": card_jpeg, "selfie_jpeg": selfie_jpeg}
    other_id = identity_policy == "Other ID cards"
    if card_corners is not None:
        card_region, method = crop_from_corners(card, card_corners), "manual_crop"
    else:
        card_region, method = _locate_card(card, other_id=other_id)
    if card_region is None:
        return result
    if card_corners is not None:
        portrait_region = crop_from_corners(card, card_corners, preserve_pixels=True)
    else:
        portrait_region, _ = _locate_card(card, other_id=other_id, preserve_pixels=True)
        if portrait_region is None:
            portrait_region = card_region
    result["card_detection_method"] = method
    result["stage"] = "card_type"
    lines = [] if other_id else _ocr(card_region)
    # Other photo IDs require a card outline, readable matching name and
    # matching portrait below. No country, issuer, heading or flag is required.
    recognized = other_id or _has_fayda_heading(lines) or _has_fayda_flag(card_region)
    if not recognized:
        # Read the title independently so the portrait and bilingual body do
        # not confuse page segmentation. Keep all cues inside the card crop.
        header = card_region[:round(card_region.shape[0] * .30)]
        for mode in ("lighting", "adaptive"):
            lines += [line for line, _ in _read_text(_prepare_text(header, mode, 2000))]
            if _has_fayda_heading(lines):
                recognized = True
                break
    if not recognized:
        if not lines:
            result.update(stage="card_quality", reason="card_unreadable")
            return result
        result["reason"] = "not_fayda"
        return result
    result["stage"] = "name"
    reader = _read_other_name if other_id else _read_name
    score, confidence = reader(card_region, invited_name)
    if score < .85 and card_corners is not None:
        # Preserve the original glyph pixels as an alternate reading of the
        # same selected card. Never use room/background text as name evidence.
        alternate = reader(portrait_region, invited_name)
        score, confidence = max(((score, confidence), alternate), key=lambda reading: (reading[0], reading[1]))
    result["name_readability"] = confidence
    if result["name_readability"] < NAME_READABILITY_THRESHOLD:
        result["reason"] = "name_unreadable"
        return result
    result["name_similarity"] = score
    if result["name_similarity"] < 0.85:
        result["reason"] = "name_mismatch"
        return result
    with _model_lock:
        detector, recognizer = _face_models()
        result["stage"] = "card_portrait"
        card_face, failure = _detect_one(detector, portrait_region, "card")
        if failure:
            result["reason"] = failure
            return result
        result["stage"] = "camera_face"
        selfie_face, failure = _detect_one(detector, selfie, "selfie")
        if failure:
            result["reason"] = failure
            return result
        try:
            result["stage"] = "face_comparison"
            card_features = _face_feature(detector, recognizer, portrait_region, card_face)
            selfie_features = _face_feature(detector, recognizer, selfie, selfie_face)
            result["face_similarity"] = float(recognizer.match(card_features, selfie_features, cv2.FaceRecognizerSF_FR_COSINE))
        except cv2.error as exc:
            raise HTTPException(status_code=503, detail="Face matching is unavailable") from exc
    if not math.isfinite(result["face_similarity"]) or result["face_similarity"] < FACE_THRESHOLD:
        result["reason"] = "face_mismatch"
        return result
    result["status"] = "passed"
    result["stage"] = "complete"
    result["reason"] = None
    return result


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
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=503, detail="Identity check unavailable") from exc
    from . import esignet
    demo = row and row['identity_policy'] == esignet.POLICY
    current = row and row["access_fingerprint"] == access_fingerprint and row['verification_method'] == (esignet.METHOD if demo else 'card_face') and (not demo or esignet.enabled())
    if current and row["status"]=="passed" and not row["started"] and (not row["evidence_expires_at"] or row["evidence_expires_at"]<=datetime.now(timezone.utc).replace(tzinfo=None)):
        current=False
    return {"status": row["status"] if current else "required",
            "identity_policy": (row["identity_policy"] or "Fayda ID") if row else "Fayda ID",
            "stage": (row["stage"] or _STAGES.get(row["failure_reason"], "complete")) if current else None,
            "message": _REASONS.get(row["failure_reason"]) if current and row["status"] == "failed" else None,
            "checked_at": row["checked_at"].isoformat() + "Z" if current and row["checked_at"] else None,
            "assessment_title": row["title"] if row else None,
            "attempts": row["attempts"] if row and row["attempts"] else 0,
            "verification_method": row['verification_method'] if current else None,
            "demo": esignet.state(invitation_id,candidate_id,access_fingerprint) if demo else None}


def verify_identity(invitation_id: int, candidate_id: int, policy_version: str,
                    access_fingerprint: str, card_bytes: bytes, selfie_bytes: bytes, card_corners=None) -> dict:
    try:
        with database() as connection:
            cursor = connection.cursor(dictionary=True)
            cursor.execute("""SELECT i.candidate_name, a.title, a.starts_at, a.ends_at, a.identity_policy,
                                     s.id AS session_id, c.id AS consent_id,
                                     ic.status, ic.access_fingerprint, ic.checked_at, ic.evidence_expires_at, ic.attempts,
                                     ic.candidate_user_id AS checked_candidate_id
                              FROM assessment_invitations i
                              JOIN assessments a ON a.id = i.assessment_id
                              LEFT JOIN assessment_exam_sessions s ON s.invitation_id = i.id
                              LEFT JOIN assessment_consents c ON c.invitation_id = i.id AND c.candidate_user_id = %s AND c.policy_version = %s
                              LEFT JOIN assessment_identity_checks ic ON ic.invitation_id = i.id
                              WHERE i.id = %s AND i.candidate_user_id = %s AND i.delivery_status = 'sent'""",
                           (candidate_id, policy_version, invitation_id, candidate_id))
            row = cursor.fetchone()
            cursor.close()
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=503, detail="Identity check unavailable") from exc
    if not row:
        raise HTTPException(status_code=403, detail="Exam invitation is unavailable")
    from . import esignet
    if row['identity_policy'] == esignet.POLICY:
        raise HTTPException(403, 'This exam requires demo identity authentication')
    if row["checked_candidate_id"] is not None and row["checked_candidate_id"] != candidate_id:
        raise HTTPException(status_code=403, detail="Identity check belongs to another candidate account")
    if row["status"] == "passed" and row["access_fingerprint"] == access_fingerprint and row["evidence_expires_at"] and row["evidence_expires_at"]>datetime.now(timezone.utc).replace(tzinfo=None):
        return {"status": "passed", "stage": "complete", "message": None,
                "checked_at": row["checked_at"].isoformat() + "Z", "assessment_title": row["title"],
                "attempts": row["attempts"]}
    if row["session_id"]:
        raise HTTPException(status_code=409, detail="The exam has already started")
    if not row["consent_id"]:
        raise HTTPException(status_code=403, detail="Agreement is required before identity verification")
    now = _now()
    if not row["starts_at"] <= now < row["ends_at"]:
        raise HTTPException(status_code=403, detail="The exam access window is closed")
    result = compare_card_and_face(card_bytes, selfie_bytes, row["candidate_name"], row["identity_policy"] or "Fayda ID", card_corners)
    stage = result.get("stage") or ("complete" if result["status"] == "passed" else _STAGES.get(result["reason"], "card_capture"))
    now = _now().replace(microsecond=0)
    try:
        with database() as connection:
            cursor = connection.cursor(dictionary=True)
            cursor.execute("""SELECT i.candidate_user_id, a.starts_at, a.ends_at
                              FROM assessment_invitations i JOIN assessments a ON a.id = i.assessment_id
                              WHERE i.id = %s FOR UPDATE""", (invitation_id,))
            owner = cursor.fetchone()
            if not owner or owner["candidate_user_id"] != candidate_id:
                raise HTTPException(status_code=403, detail="Exam invitation is unavailable")
            if not owner["starts_at"] <= now < owner["ends_at"]:
                raise HTTPException(status_code=403, detail="The exam access window is closed")
            cursor.execute("SELECT id FROM assessment_exam_sessions WHERE invitation_id = %s", (invitation_id,))
            if cursor.fetchone():
                raise HTTPException(status_code=409, detail="The exam has already started")
            cursor.execute("SELECT status, candidate_user_id, access_fingerprint, attempts, checked_at, evidence_expires_at FROM assessment_identity_checks WHERE invitation_id = %s FOR UPDATE", (invitation_id,))
            previous = cursor.fetchone()
            if previous and previous["candidate_user_id"] != candidate_id:
                raise HTTPException(status_code=403, detail="Identity check belongs to another candidate account")
            if previous and previous["status"] == "passed" and previous["access_fingerprint"] == access_fingerprint and previous["evidence_expires_at"] and previous["evidence_expires_at"]>now:
                return {"status": "passed", "stage": "complete", "message": None,
                        "checked_at": previous["checked_at"].isoformat() + "Z", "assessment_title": row["title"],
                        "attempts": previous["attempts"]}
            attempt_number = int(previous["attempts"]) + 1 if previous else 1
            cursor.execute("""INSERT INTO assessment_identity_checks
                       (invitation_id, candidate_user_id, access_fingerprint, status, stage, failure_reason,
                        card_detection_method, name_readability, name_similarity, face_similarity,
                        checked_at, evidence_expires_at, card_ciphertext, selfie_ciphertext)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON DUPLICATE KEY UPDATE access_fingerprint = VALUES(access_fingerprint),
                         status = VALUES(status), stage = VALUES(stage), failure_reason = VALUES(failure_reason),
                         card_detection_method = VALUES(card_detection_method), name_readability = VALUES(name_readability),
                         name_similarity = VALUES(name_similarity), face_similarity = VALUES(face_similarity),
                         checked_at = VALUES(checked_at), evidence_expires_at = VALUES(evidence_expires_at),
                         card_ciphertext = VALUES(card_ciphertext), selfie_ciphertext = VALUES(selfie_ciphertext),
                         attempts = attempts + 1""",
                       (invitation_id, candidate_id, access_fingerprint, result["status"], stage, result["reason"],
                        result.get("card_detection_method"), result.get("name_readability"), result["name_similarity"],
                        result["face_similarity"], now, now + timedelta(days=for_invitation(cursor,invitation_id)['identity_days']),
                        _encrypt(result["card_jpeg"], invitation_id, "card"),
                        _encrypt(result["selfie_jpeg"], invitation_id, "selfie")))
            cursor.execute("""INSERT INTO assessment_identity_attempts
                              (invitation_id, candidate_user_id, attempt_number, status, stage, failure_reason,
                               card_detection_method, name_readability, name_similarity, face_similarity, checked_at)
                              VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                           (invitation_id, candidate_id, attempt_number, result["status"], stage, result["reason"],
                            result.get("card_detection_method"), result.get("name_readability"),
                            result["name_similarity"], result["face_similarity"], now))
            connection.commit()
            cursor.close()
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=503, detail="Identity check unavailable") from exc
    return {"status": result["status"], "stage": stage, "message": _REASONS.get(result["reason"]),
            "checked_at": now.isoformat() + "Z", "assessment_title": row["title"],
            "attempts": attempt_number}


def purge_expired_identity_evidence() -> int:
    try:
        with database() as connection:
            cursor = connection.cursor()
            cursor.execute("""UPDATE assessment_identity_checks
                              SET card_ciphertext = NULL, selfie_ciphertext = NULL
                              WHERE evidence_expires_at <= UTC_TIMESTAMP()
                                AND (card_ciphertext IS NOT NULL OR selfie_ciphertext IS NOT NULL)""")
            count = cursor.rowcount
            connection.commit()
            cursor.close()
            return count
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=503, detail="Identity evidence cleanup unavailable") from exc


def clear_prestart_identity_access(invitation_id: int, candidate_id: int, access_fingerprint: str) -> None:
    """A voluntary SEB exit ends this prestart match without deleting its audit evidence."""
    try:
        with database() as connection:
            cursor = connection.cursor()
            cursor.execute("""UPDATE assessment_identity_checks SET access_fingerprint = NULL
                              WHERE invitation_id = %s AND candidate_user_id = %s AND access_fingerprint = %s""",
                           (invitation_id, candidate_id, access_fingerprint))
            connection.commit()
            cursor.close()
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=503, detail="Identity check unavailable") from exc
