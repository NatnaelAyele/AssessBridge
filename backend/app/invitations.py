import hashlib
import json
import re
import secrets
import smtplib
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

import mysql.connector
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .db import database
from .config import ROOT_DIR, SEB_BASE_URL, SEB_CONFIG_KEY, SEB_CONFIG_PASSWORD, SEB_QUIT_PASSWORD, SEB_REQUIRED
from .mail import EXAM_ENTRY_URL, MailNotConfigured, MailSettings, send_calendar_invitation
from .seb_config import build_encrypted_configuration, config_password_for, quit_password_for
from .security import hash_password


EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
PROHIBITED_ITEMS = ("Mobile phone", "Additional person", "Shade glass", "Books", "Tablet", "Calculator")
EXAM_USERNAME_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def new_exam_password() -> str:
    return secrets.token_urlsafe(6)  # Six random bytes encode to exactly eight characters.


def new_exam_username() -> str:
    return "".join(secrets.choice(EXAM_USERNAME_ALPHABET) for _ in range(5))


def validate_choices(value: str, allowed: tuple[str, ...]) -> str:
    if not value:
        return ""
    choices = value.split(", ")
    if len(set(choices)) != len(choices) or any(choice not in allowed for choice in choices):
        raise ValueError("Choose only the available options, without duplicates")
    return ", ".join(choice for choice in allowed if choice in choices)


class QuestionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["written", "multiple_choice"]
    prompt: str = Field(min_length=1, max_length=5000)
    key: str | None = Field(default=None, max_length=5000)
    options: list[str] | None = None
    correctIndex: int | None = None

    @model_validator(mode="after")
    def validate_answer(self):
        if self.type == "written":
            if not self.key or not self.key.strip():
                raise ValueError("Written questions require an answer key")
        else:
            if not self.options or len(self.options) != 4:
                raise ValueError("Multiple choice questions require four option slots")
            choices = [option.strip() for option in self.options]
            if not choices[0] or not choices[1]:
                raise ValueError("Multiple choice questions require at least options A and B")
            filled = [choice.casefold() for choice in choices if choice]
            if len(set(filled)) != len(filled):
                raise ValueError("Multiple choice options must be distinct")
            if self.correctIndex is None or self.correctIndex not in range(4) or not choices[self.correctIndex]:
                raise ValueError("Select a filled correct answer")
        return self


class CandidateDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not EMAIL_PATTERN.fullmatch(value):
            raise ValueError("Enter a valid candidate email")
        return value


class SendInvitationsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_key: UUID
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=10000)
    internal_reference: str = Field(default="", max_length=60)
    duration_minutes: int = Field(ge=1, le=480)
    rules_text: str = Field(default="", max_length=10000)
    identity_policy: str = Field(default="Fayda ID", max_length=10000)
    questions: list[QuestionDraft] = Field(min_length=1, max_length=200)
    candidates: list[CandidateDraft] = Field(min_length=1, max_length=200)
    starts_at: datetime
    ends_at: datetime

    @field_validator("rules_text")
    @classmethod
    def valid_prohibited_items(cls, value: str) -> str:
        return validate_choices(value, PROHIBITED_ITEMS)

    @field_validator("identity_policy")
    @classmethod
    def valid_identity_documents(cls, value: str) -> str:
        if value not in {"Fayda ID", "Other ID cards"}:
            raise ValueError("Choose Fayda ID card or Other ID cards")
        return value

    @model_validator(mode="after")
    def validate_schedule_and_roster(self):
        if not self.title.strip():
            raise ValueError("Assessment title is required")
        if any(character in self.title for character in "\r\n"):
            raise ValueError("Assessment title must be one line")
        if self.starts_at.tzinfo is None or self.ends_at.tzinfo is None:
            raise ValueError("Schedule must include a timezone")
        if self.starts_at <= datetime.now(timezone.utc):
            raise ValueError("Assessment start must be in the future")
        if self.ends_at <= self.starts_at:
            raise ValueError("Assessment end must be after its start")
        if (self.ends_at - self.starts_at).total_seconds() < self.duration_minutes * 60:
            raise ValueError("The access window must allow the full assessment duration")
        emails = [candidate.email for candidate in self.candidates]
        if len(set(emails)) != len(emails):
            raise ValueError("Each candidate email must be unique")
        return self


def utc_naive(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc).replace(tzinfo=None)


def list_assessments(employer_id: int, page: int, search: str = "", status: str = "all",
                     candidates_min: int | None = None, candidates_max: int | None = None,
                     questions_min: int | None = None, questions_max: int | None = None) -> dict:
    page_size = 20
    if ((candidates_min is not None and candidates_max is not None and candidates_min > candidates_max)
            or (questions_min is not None and questions_max is not None and questions_min > questions_max)):
        raise HTTPException(status_code=422, detail="Minimum filter value cannot exceed maximum")
    where = ["a.employer_user_id = %s"]
    parameters: list = [employer_id]
    if search:
        where.append("(LOCATE(LOWER(%s), LOWER(a.title)) > 0 OR LOCATE(LOWER(%s), LOWER(a.internal_reference)) > 0)")
        parameters.extend((search, search))
    if status == "scheduled":
        where.append("a.starts_at > UTC_TIMESTAMP()")
    elif status == "open":
        where.append("a.starts_at <= UTC_TIMESTAMP() AND a.ends_at > UTC_TIMESTAMP()")
    elif status == "closed":
        where.append("a.ends_at <= UTC_TIMESTAMP()")
    having = []
    for field, minimum, maximum in (("sent_count", candidates_min, candidates_max),
                                    ("question_count", questions_min, questions_max)):
        if minimum is not None:
            having.append(f"{field} >= %s")
            parameters.append(minimum)
        if maximum is not None:
            having.append(f"{field} <= %s")
            parameters.append(maximum)
    query = (
        "SELECT a.id, a.title, a.internal_reference, a.starts_at, a.ends_at, "
        "JSON_LENGTH(a.questions_json) AS question_count, COUNT(i.id) AS candidate_count, "
        "COALESCE(SUM(i.delivery_status = 'sent'), 0) AS sent_count, "
        "COALESCE(SUM(i.delivery_status = 'failed'), 0) AS failed_count "
        "FROM assessments a LEFT JOIN assessment_invitations i ON i.assessment_id = a.id "
        f"WHERE {' AND '.join(where)} GROUP BY a.id"
        + (f" HAVING {' AND '.join(having)}" if having else "")
    )
    try:
        with database() as connection:
            cursor = connection.cursor(dictionary=True)
            cursor.execute(f"SELECT COUNT(*) AS total FROM ({query}) AS filtered", tuple(parameters))
            total = cursor.fetchone()["total"]
            cursor.execute(
                f"{query} ORDER BY a.created_at DESC, a.id DESC LIMIT %s OFFSET %s",
                (*parameters, page_size, (page - 1) * page_size),
            )
            rows = cursor.fetchall()
            cursor.close()
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=503, detail="Service temporarily unavailable") from exc
    return {
        "page": page, "page_size": page_size, "total": total,
        "items": [
            {
                "id": row["id"], "title": row["title"], "internal_reference": row["internal_reference"],
                "starts_at": row["starts_at"].isoformat() + "Z", "ends_at": row["ends_at"].isoformat() + "Z",
                "candidate_count": row["candidate_count"], "sent_count": int(row["sent_count"]),
                "question_count": int(row["question_count"]),
                "failed_count": int(row["failed_count"]),
            }
            for row in rows
        ],
    }


def assessment_detail(assessment_id: int, employer_id: int) -> dict:
    try:
        with database() as connection:
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                """SELECT id, title, description, internal_reference, duration_minutes,
                          starts_at, ends_at, identity_policy, questions_json, JSON_LENGTH(questions_json) AS question_count
                   FROM assessments WHERE id = %s AND employer_user_id = %s""",
                (assessment_id, employer_id),
            )
            assessment = cursor.fetchone()
            if assessment is None:
                cursor.close()
                raise HTTPException(status_code=404, detail="Assessment not found")
            cursor.execute(
                """SELECT i.id AS invitation_id, i.candidate_name, i.candidate_email, i.delivery_status, i.sent_at,
                          s.current_index, s.started_at, s.submitted_at, s.submission_reason,
                          s.disqualified_at, s.interrupted_at, s.disqualification_reason,
                          ic.status AS identity_status, ic.stage AS identity_stage,
                          ic.failure_reason AS identity_failure_reason,
                          ic.checked_at AS identity_checked_at, ic.attempts AS identity_attempts,
                          r.status AS recording_status,
                          COALESCE(media.total_bytes, 0) AS recording_bytes,
                          COALESCE(a.answered_count, 0) AS answered_count
                   FROM assessment_invitations i
                   LEFT JOIN assessment_exam_sessions s ON s.invitation_id = i.id
                   LEFT JOIN assessment_identity_checks ic ON ic.invitation_id = i.id
                   LEFT JOIN assessment_recordings r ON r.session_id = s.id
                     AND r.status <> 'deleted' AND (r.expires_at IS NULL OR r.expires_at > UTC_TIMESTAMP())
                   LEFT JOIN (
                       SELECT recording_id, SUM(total_bytes) AS total_bytes
                       FROM assessment_recording_parts GROUP BY recording_id
                   ) media ON media.recording_id = r.id
                   LEFT JOIN (
                       SELECT session_id, SUM(JSON_TYPE(answer_json) <> 'NULL') AS answered_count
                       FROM assessment_exam_answers GROUP BY session_id
                   ) a ON a.session_id = s.id
                   WHERE i.assessment_id = %s ORDER BY i.id""",
                (assessment_id,),
            )
            candidates = cursor.fetchall()
            cursor.close()
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=503, detail="Service temporarily unavailable") from exc
    return {
        "id": assessment["id"], "title": assessment["title"],
        "description": assessment["description"], "internal_reference": assessment["internal_reference"],
        "identity_policy": assessment["identity_policy"] or "Fayda ID",
        "duration_minutes": assessment["duration_minutes"], "question_count": assessment["question_count"],
        "questions": json.loads(assessment["questions_json"]) if isinstance(assessment["questions_json"], str) else assessment["questions_json"],
        "starts_at": assessment["starts_at"].isoformat() + "Z",
        "ends_at": assessment["ends_at"].isoformat() + "Z",
        "candidates": [
            {"invitation_id": candidate["invitation_id"],
             "name": candidate["candidate_name"], "email": candidate["candidate_email"],
             "invitation": candidate["delivery_status"],
             "progress": "disqualified" if candidate["disqualified_at"] else "interrupted" if candidate["interrupted_at"] else "submitted" if candidate["submitted_at"] else "in_progress" if candidate["started_at"] else "not_started",
             "answered_count": int(candidate["answered_count"]),
             "current_question": candidate["current_index"] + 1 if candidate["started_at"] else None,
             "submission_reason": candidate["submission_reason"],
             "disqualification_reason": candidate["disqualification_reason"],
             "identity_status": candidate["identity_status"] or "required",
             "identity_stage": candidate["identity_stage"],
             "identity_failure_reason": candidate["identity_failure_reason"],
             "identity_checked_at": candidate["identity_checked_at"].isoformat() + "Z" if candidate["identity_checked_at"] else None,
             "identity_attempts": candidate["identity_attempts"] or 0,
             "recording_status": candidate["recording_status"],
             "recording_bytes": int(candidate["recording_bytes"])}
            for candidate in candidates
        ],
    }


def invitation_status(assessment_id: int, employer_id: int) -> dict:
    with database() as connection:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, title FROM assessments WHERE id = %s AND employer_user_id = %s",
            (assessment_id, employer_id),
        )
        assessment = cursor.fetchone()
        if assessment is None:
            cursor.close()
            raise HTTPException(status_code=404, detail="Assessment not found")
        cursor.execute(
            """SELECT candidate_name, candidate_email, delivery_status, sent_at
               FROM assessment_invitations WHERE assessment_id = %s ORDER BY id""",
            (assessment_id,),
        )
        candidates = cursor.fetchall()
        cursor.close()
    return {
        "assessment_id": assessment_id,
        "title": assessment["title"],
        "sent": sum(item["delivery_status"] == "sent" for item in candidates),
        "failed": sum(item["delivery_status"] == "failed" for item in candidates),
        "pending": sum(item["delivery_status"] == "pending" for item in candidates),
        "candidates": [
            {"name": item["candidate_name"], "email": item["candidate_email"], "invitation": item["delivery_status"]}
            for item in candidates
        ],
    }


def send_all_invitations(payload: SendInvitationsRequest, employer_id: int) -> dict:
    if SEB_REQUIRED and (
        not re.fullmatch(r"[0-9a-f]{64}", SEB_CONFIG_KEY)
        or not SEB_CONFIG_PASSWORD
        or not SEB_QUIT_PASSWORD
        or not (ROOT_DIR / "backend" / "seb" / "AssessBridge.seb").is_file()
        or EXAM_ENTRY_URL != SEB_BASE_URL + "/exam-entry"
    ):
        raise HTTPException(status_code=503, detail="SEB invitation setup is incomplete")
    try:
        mail_settings = MailSettings.from_environment()
    except MailNotConfigured as exc:
        raise HTTPException(status_code=503, detail="Email delivery is not configured") from exc

    payload_json = payload.model_dump_json(exclude={"request_key"})
    payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    question_json = json.dumps([question.model_dump() for question in payload.questions], ensure_ascii=False)

    try:
        with database() as connection:
            cursor = connection.cursor(dictionary=True)
            cursor.execute(
                "SELECT id, employer_user_id, payload_hash FROM assessments WHERE request_key = %s FOR UPDATE",
                (str(payload.request_key),),
            )
            existing = cursor.fetchone()
            if existing:
                if existing["employer_user_id"] != employer_id or existing["payload_hash"] != payload_hash:
                    cursor.close()
                    raise HTTPException(status_code=409, detail="Invitation request does not match the saved assessment")
                assessment_id = existing["id"]
                cursor.execute("SELECT id FROM assessment_result_publications WHERE assessment_id=%s AND is_legacy=FALSE LIMIT 1", (assessment_id,))
                if cursor.fetchone():
                    raise HTTPException(409, "Results are published. Invitations cannot be changed.")
            else:
                cursor.execute(
                    """INSERT INTO assessments
                       (employer_user_id, request_key, payload_hash, title, description, internal_reference,
                        duration_minutes, rules_text, identity_policy, questions_json, starts_at, ends_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        employer_id, str(payload.request_key), payload_hash, payload.title.strip(),
                        payload.description, payload.internal_reference, payload.duration_minutes,
                        payload.rules_text, payload.identity_policy, question_json,
                        utc_naive(payload.starts_at), utc_naive(payload.ends_at),
                    ),
                )
                assessment_id = cursor.lastrowid
                for candidate in payload.candidates:
                    cursor.execute(
                        "SELECT id FROM users WHERE email = %s AND role = 'candidate' AND is_active = TRUE",
                        (candidate.email,),
                    )
                    account = cursor.fetchone()
                    for _ in range(20):
                        exam_username = new_exam_username()
                        if SEB_REQUIRED:
                            seb_key, seb_file = build_encrypted_configuration(
                                SEB_BASE_URL,
                                f"{SEB_BASE_URL}/exam-entry?exam={exam_username}",
                                config_password_for(exam_username),
                                quit_password_for(exam_username),
                            )
                        else:
                            seb_key, seb_file = None, None
                        try:
                            cursor.execute(
                                """INSERT INTO assessment_invitations
                                   (assessment_id, candidate_name, candidate_email, candidate_user_id, exam_username, password_hash, seb_config_key, seb_config_blob)
                                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                                (assessment_id, candidate.name.strip(), candidate.email, account["id"] if account else None, exam_username, hash_password(new_exam_password()), seb_key, seb_file),
                            )
                            break
                        except mysql.connector.IntegrityError as exc:
                            if exc.errno != 1062 or "exam_username" not in exc.msg:
                                raise
                    else:
                        raise HTTPException(status_code=503, detail="Could not allocate an exam username")
                cursor.execute(
                    "INSERT INTO assessment_activity (assessment_id, actor_user_id, event_type) VALUES (%s, %s, 'assessment_created')",
                    (assessment_id, employer_id),
                )
                connection.commit()
            cursor.close()
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=503, detail="Service temporarily unavailable") from exc

    # Each retry rotates an unsent candidate's password. Sent invitations are never mailed twice.
    with database() as connection:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """SELECT id, candidate_name, candidate_email, candidate_user_id, exam_username
               FROM assessment_invitations WHERE assessment_id = %s AND delivery_status <> 'sent' ORDER BY id""",
            (assessment_id,),
        )
        unsent = cursor.fetchall()
        cursor.close()

    for candidate in unsent:
        password = new_exam_password()
        with database() as connection:
            cursor = connection.cursor()
            cursor.execute(
                "UPDATE assessment_invitations SET password_hash = %s, delivery_status = 'pending' WHERE id = %s",
                (hash_password(password), candidate["id"]),
            )
            connection.commit()
            cursor.close()
        try:
            send_calendar_invitation(
                settings=mail_settings,
                assessment_id=assessment_id,
                invitation_id=candidate["id"],
                title=payload.title.strip(),
                duration_minutes=payload.duration_minutes,
                start=payload.starts_at,
                end=payload.ends_at,
                candidate_name=candidate["candidate_name"],
                candidate_email=candidate["candidate_email"],
                username=candidate["exam_username"],
                password=password,
                seb_config_password=config_password_for(candidate["exam_username"]),
                seb_launch_url=f"{SEB_BASE_URL}/seb/launch/{candidate['exam_username']}",
                seb_quit_password=quit_password_for(candidate["exam_username"]),
                registered=candidate["candidate_user_id"] is not None,
            )
            event_type = "invitation_sent"
            delivery_status = "sent"
        except (OSError, smtplib.SMTPException):
            event_type = "invitation_failed"
            delivery_status = "failed"
        with database() as connection:
            cursor = connection.cursor()
            cursor.execute(
                "UPDATE assessment_invitations SET delivery_status = %s, sent_at = %s WHERE id = %s",
                (delivery_status, datetime.now(timezone.utc).replace(tzinfo=None) if delivery_status == "sent" else None, candidate["id"]),
            )
            cursor.execute(
                """INSERT INTO assessment_activity (assessment_id, actor_user_id, event_type, invitation_id)
                   VALUES (%s, %s, %s, %s)""",
                (assessment_id, employer_id, event_type, candidate["id"]),
            )
            connection.commit()
            cursor.close()

    return invitation_status(assessment_id, employer_id)
