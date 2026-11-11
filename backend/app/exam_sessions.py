"""Candidate exam attempts, saved answers, and deadline enforcement."""

import json
import secrets
from datetime import datetime, timedelta, timezone

import mysql.connector
from fastapi import HTTPException

from .db import database
from .connectivity import advance, begin_recovery, finish_recovery, ended_at, event, require_answering, RECOVERY_SECONDS, TOTAL_RECOVERY_SECONDS, FINAL_UPLOAD_SECONDS



def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso_utc(moment: datetime | None) -> str | None:
    return moment.isoformat() + "Z" if moment else None


def questions_for(exam: dict) -> list[dict]:
    raw = exam["questions_json"]
    return json.loads(raw) if isinstance(raw, (str, bytes)) else raw


def load_exam(cursor, invitation_id: int) -> dict:
    cursor.execute(
        """SELECT i.id AS invitation_id, a.title, a.description, a.rules_text,
                  a.identity_policy, a.duration_minutes, a.starts_at, a.ends_at,
                  a.questions_json
           FROM assessment_invitations i
           JOIN assessments a ON a.id = i.assessment_id
           WHERE i.id = %s AND i.delivery_status = 'sent' FOR UPDATE""",
        (invitation_id,),
    )
    exam = cursor.fetchone()
    if not exam:
        raise HTTPException(status_code=403, detail="Exam invitation is unavailable")
    return exam


def load_session(cursor, invitation_id: int) -> dict | None:
    cursor.execute(
        "SELECT * FROM assessment_exam_sessions WHERE invitation_id = %s FOR UPDATE",
        (invitation_id,),
    )
    return cursor.fetchone()


def require_current_client(session, client_id=None):
    # Recheck under the session lock: prepare may replace the client after the
    # endpoint's initial authorization but before an answer transaction begins.
    if session and client_id and not ended_at(session) and session.get("client_instance_id") != client_id:
        raise HTTPException(428, "Reopen the exam room and complete recovery checks")


def finalize_if_expired(cursor, session: dict, now: datetime) -> None:
    advance(cursor, session, now)


def serialize_state(cursor, exam: dict, session: dict | None, now: datetime) -> dict:
    questions = questions_for(exam)
    base = {
        "title": exam["title"],
        "description": exam["description"] or "",
        "rules_text": exam["rules_text"] or "",
        "identity_policy": exam["identity_policy"] or "",
        "duration_minutes": exam["duration_minutes"],
        "question_count": len(questions),
        "server_now": iso_utc(now),
    }
    if session is None:
        return {**base, "state": "not_started"}

    cursor.execute(
        "SELECT question_index, answer_json FROM assessment_exam_answers WHERE session_id = %s",
        (session["id"],),
    )
    answers = {row["question_index"]: json.loads(row["answer_json"]) for row in cursor.fetchall()}
    base.update({
        "state": "disqualified" if session["disqualified_at"] else "interrupted" if session.get("interrupted_at") else "submitted" if session["submitted_at"] else "active",
        "session_id": session["id"],
        "interrupted_at": iso_utc(session.get("interrupted_at")),
        "interruption_reason": session.get("interruption_reason"),
        "recovery_started_at": iso_utc(session.get("recovery_started_at")),
        "recovery_reason": session.get("recovery_reason"),
        "recovery_seconds": float(session.get("recovery_seconds") or 0),
        "resume_required": bool(session.get("resume_required")),
        "recovery_limit_seconds": RECOVERY_SECONDS,
        "total_recovery_limit_seconds": TOTAL_RECOVERY_SECONDS,
        "upload_deadline_at": iso_utc(ended_at(session) + timedelta(seconds=FINAL_UPLOAD_SECONDS)) if ended_at(session) else None,
        "started_at": iso_utc(session["started_at"]),
        "deadline_at": iso_utc(session["deadline_at"]),
        "submitted_at": iso_utc(session["submitted_at"]),
        "submission_reason": session["submission_reason"],
        "disqualification_reason": session["disqualification_reason"],
        "current_index": session["current_index"],
        "answered_count": sum(answer is not None for answer in answers.values()),
    })
    if not ended_at(session) and not session.get("resume_required"):
        question = questions[session["current_index"]]
        base["question"] = {
            "type": question["type"], "prompt": question["prompt"],
            "options": question.get("options") if question["type"] == "multiple_choice" else None,
        }
        base["answer"] = answers.get(session["current_index"])
    return base


def read_exam_state(invitation_id: int, candidate_user_id: int, client_id=None) -> dict:
    try:
        with database() as connection:
            cursor = connection.cursor(dictionary=True)
            exam = load_exam(cursor, invitation_id)
            session = load_session(cursor, invitation_id)
            require_current_client(session, client_id)
            if session and session["candidate_user_id"] != candidate_user_id:
                raise HTTPException(status_code=403, detail="This exam attempt belongs to another candidate account")
            now = utc_now()
            if session:
                finalize_if_expired(cursor, session, now)
            result = serialize_state(cursor, exam, session, now)
            connection.commit()
            cursor.close()
            return result
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=503, detail="Exam service temporarily unavailable") from exc


def start_exam_attempt(invitation_id: int, candidate_user_id: int, policy_version: str,
                       auth_session_token_id: str, identity_fingerprint: str, client_id: str = None) -> dict:
    try:
        with database() as connection:
            cursor = connection.cursor(dictionary=True)
            exam = load_exam(cursor, invitation_id)
            session = load_session(cursor, invitation_id)
            require_current_client(session, client_id)
            now = utc_now()
            if session:
                if session["candidate_user_id"] != candidate_user_id:
                    raise HTTPException(status_code=403, detail="This exam attempt belongs to another candidate account")
                finalize_if_expired(cursor, session, now)
                if not ended_at(session):
                    require_answering(session)
            else:
                if not exam["starts_at"] <= now < exam["ends_at"]:
                    raise HTTPException(status_code=403, detail="The exam access window is closed")
                cursor.execute(
                    """SELECT 1 FROM assessment_consents
                       WHERE invitation_id = %s AND candidate_user_id = %s AND policy_version = %s""",
                    (invitation_id, candidate_user_id, policy_version),
                )
                if cursor.fetchone() is None:
                    raise HTTPException(status_code=403, detail="Agreement is required before starting")
                cursor.execute(
                    """SELECT 1 FROM assessment_identity_checks
                       WHERE invitation_id = %s AND candidate_user_id = %s
                         AND access_fingerprint = %s AND status = 'passed'""",
                    (invitation_id, candidate_user_id, identity_fingerprint),
                )
                if cursor.fetchone() is None:
                    raise HTTPException(status_code=403, detail="Complete ID card and face matching before starting")
                deadline = min(now + timedelta(minutes=exam["duration_minutes"]), exam["ends_at"])
                cursor.execute(
                    """INSERT INTO assessment_exam_sessions
                       (invitation_id, candidate_user_id, auth_session_token_id, started_at, deadline_at, last_seen_at)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (invitation_id, candidate_user_id, auth_session_token_id, now, deadline, now),
                )
                cursor.execute("UPDATE assessment_exam_sessions SET client_instance_id=%s WHERE invitation_id=%s", (client_id, invitation_id))
                session = load_session(cursor, invitation_id)
                from .monitoring.repository import create_run
                create_run(cursor, session, exam)
            if not ended_at(session):
                cursor.execute(
                    """INSERT INTO assessment_recordings (session_id, storage_token, started_at)
                       VALUES (%s, %s, %s) ON DUPLICATE KEY UPDATE id = id""",
                    (session["id"], secrets.token_hex(16), session["started_at"]),
                )
            result = serialize_state(cursor, exam, session, now)
            connection.commit()
            cursor.close()
            return result
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=503, detail="Exam service temporarily unavailable") from exc


def validate_answer(question: dict, answer: str | int | None) -> str | int | None:
    if answer is None or answer == "":
        return None
    if question["type"] == "written":
        if not isinstance(answer, str) or len(answer) > 5000:
            raise HTTPException(status_code=422, detail="Enter a written answer of at most 5000 characters")
        return answer
    if isinstance(answer, bool) or not isinstance(answer, int):
        raise HTTPException(status_code=422, detail="Select a valid answer option")
    options = question.get("options") or []
    if answer < 0 or answer >= len(options) or not options[answer]:
        raise HTTPException(status_code=422, detail="Select a valid answer option")
    return answer


def save_exam_answer(invitation_id: int, candidate_user_id: int, question_index: int, answer: str | int | None, client_id=None) -> dict:
    try:
        with database() as connection:
            cursor = connection.cursor(dictionary=True)
            exam = load_exam(cursor, invitation_id)
            session = load_session(cursor, invitation_id)
            require_current_client(session, client_id)
            now = utc_now()
            if not session or session["candidate_user_id"] != candidate_user_id:
                raise HTTPException(status_code=403, detail="Start your exam first")
            finalize_if_expired(cursor, session, now)
            if session["disqualified_at"] is not None:
                connection.commit()
                raise HTTPException(status_code=409, detail="This exam was disqualified and cannot be resumed")
            if session["submitted_at"] is not None:
                connection.commit()
                raise HTTPException(status_code=409, detail="This exam has been submitted")
            try:
                require_answering(session)
            except HTTPException:
                connection.commit()
                raise
            if question_index != session["current_index"]:
                raise HTTPException(status_code=409, detail="Only the current question can be saved")
            questions = questions_for(exam)
            validated = validate_answer(questions[question_index], answer)
            cursor.execute(
                """INSERT INTO assessment_exam_answers (session_id, question_index, answer_json, saved_at)
                   VALUES (%s, %s, %s, %s)
                   ON DUPLICATE KEY UPDATE answer_json = VALUES(answer_json), saved_at = VALUES(saved_at)""",
                (session["id"], question_index, json.dumps(validated), now),
            )
            result = serialize_state(cursor, exam, session, now)
            connection.commit()
            cursor.close()
            return result
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=503, detail="Exam service temporarily unavailable") from exc


def navigate_exam(invitation_id: int, candidate_user_id: int, direction: str, client_id=None) -> dict:
    try:
        with database() as connection:
            cursor = connection.cursor(dictionary=True)
            exam = load_exam(cursor, invitation_id)
            session = load_session(cursor, invitation_id)
            require_current_client(session, client_id)
            now = utc_now()
            if not session or session["candidate_user_id"] != candidate_user_id:
                raise HTTPException(status_code=403, detail="Start your exam first")
            finalize_if_expired(cursor, session, now)
            if session["disqualified_at"] is not None:
                connection.commit()
                raise HTTPException(status_code=409, detail="This exam was disqualified and cannot be resumed")
            if session["submitted_at"] is not None:
                connection.commit()
                raise HTTPException(status_code=409, detail="This exam has been submitted")
            try:
                require_answering(session)
            except HTTPException:
                connection.commit()
                raise
            if direction == "previous":
                next_index = max(0, session["current_index"] - 1)
            elif direction == "next":
                next_index = min(len(questions_for(exam)) - 1, session["current_index"] + 1)
            else:
                raise HTTPException(status_code=422, detail="Invalid navigation direction")
            cursor.execute("UPDATE assessment_exam_sessions SET current_index = %s WHERE id = %s", (next_index, session["id"]))
            session["current_index"] = next_index
            result = serialize_state(cursor, exam, session, now)
            connection.commit()
            cursor.close()
            return result
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=503, detail="Exam service temporarily unavailable") from exc


def submit_exam_attempt(invitation_id: int, candidate_user_id: int, client_id=None) -> dict:
    try:
        with database() as connection:
            cursor = connection.cursor(dictionary=True)
            exam = load_exam(cursor, invitation_id)
            session = load_session(cursor, invitation_id)
            require_current_client(session, client_id)
            now = utc_now()
            if not session or session["candidate_user_id"] != candidate_user_id:
                raise HTTPException(status_code=403, detail="Start your exam first")
            finalize_if_expired(cursor, session, now)
            if session["disqualified_at"] is not None:
                connection.commit()
                raise HTTPException(status_code=409, detail="This exam was disqualified and cannot be submitted")
            if session.get("interrupted_at"):
                connection.commit()
            if not ended_at(session):
                require_answering(session)
            if session.get("interrupted_at"):
                raise HTTPException(409, "This attempt was interrupted and cannot be submitted")
            if session["submitted_at"] is None:
                if session["current_index"] != len(questions_for(exam)) - 1:
                    raise HTTPException(status_code=409, detail="Reach the final question before submitting")
                cursor.execute(
                    """UPDATE assessment_exam_sessions
                       SET submitted_at = %s, submission_reason = 'manual'
                       WHERE id = %s AND submitted_at IS NULL""",
                    (now, session["id"]),
                )
                session["submitted_at"] = now
                session["submission_reason"] = "manual"
            result = serialize_state(cursor, exam, session, now)
            connection.commit()
            cursor.close()
            return result
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=503, detail="Exam service temporarily unavailable") from exc


def submit_expired_attempts() -> int:
    with database() as connection:
        cursor = connection.cursor(dictionary=True)
        now = utc_now()
        cursor.execute("SELECT seen_at FROM exam_service_health WHERE id=1 FOR UPDATE")
        previous = cursor.fetchone()
        cursor.execute("INSERT INTO exam_service_health (id,seen_at) VALUES (1,%s) ON DUPLICATE KEY UPDATE seen_at=VALUES(seen_at)", (now,))
        cursor.execute("SELECT * FROM assessment_exam_sessions WHERE submitted_at IS NULL AND disqualified_at IS NULL AND interrupted_at IS NULL FOR UPDATE")
        sessions = cursor.fetchall()
        for session in sessions:
            if previous and (now - previous["seen_at"]).total_seconds() > 25 and session["started_at"] < previous["seen_at"]:
                begin_recovery(cursor, session, max(session["last_seen_at"] or session["started_at"], previous["seen_at"]), "platform_unavailable")
                event(cursor, session["id"], "platform_restored", now, {"last_available_at": previous["seen_at"].isoformat()+"Z"})
            finalize_if_expired(cursor, session, now)
        connection.commit()
        return sum(bool(ended_at(s)) for s in sessions)


def heartbeat_exam_attempt(invitation_id: int, candidate_user_id: int, ready=True, offline_seconds=0, client_id=None) -> dict:
    with database() as connection:
        cursor = connection.cursor(dictionary=True)
        exam = load_exam(cursor, invitation_id)
        session = load_session(cursor, invitation_id)
        require_current_client(session, client_id)
        if not session or session["candidate_user_id"] != candidate_user_id:
            raise HTTPException(403, "Start your exam first")
        now = utc_now()
        if offline_seconds and not ended_at(session):
            begin_recovery(cursor, session, max(session["last_seen_at"] or session["started_at"], now - timedelta(seconds=offline_seconds)), "connection_lost")
        finalize_if_expired(cursor, session, now)
        if not ended_at(session):
            if ready and not session.get("resume_required"):
                finish_recovery(cursor, session, now)
            else:
                begin_recovery(cursor, session, now, "session_restart" if session.get("resume_required") else "monitoring_unavailable")
            cursor.execute("UPDATE assessment_exam_sessions SET last_seen_at=%s WHERE id=%s", (now, session["id"]))
        result = serialize_state(cursor, exam, session, now)
        connection.commit()
        return result


def disqualify_exam_attempt(invitation_id: int, candidate_user_id: int) -> dict:
    try:
        with database() as connection:
            cursor = connection.cursor(dictionary=True)
            exam = load_exam(cursor, invitation_id)
            session = load_session(cursor, invitation_id)
            now = utc_now()
            if session:
                if session["candidate_user_id"] != candidate_user_id:
                    raise HTTPException(status_code=403, detail="This exam attempt belongs to another candidate account")
                finalize_if_expired(cursor, session, now)
                if not ended_at(session):
                    cursor.execute(
                        """UPDATE assessment_exam_sessions SET disqualified_at = %s,
                           disqualification_reason = 'early_exit' WHERE id = %s""",
                        (now, session["id"]),
                    )
                    session["disqualified_at"] = now
                    session["disqualification_reason"] = "early_exit"
                    event(cursor, session["id"], "confirmed_exit", now)
            result = serialize_state(cursor, exam, session, now)
            connection.commit()
            cursor.close()
            return result
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=503, detail="Exam service temporarily unavailable") from exc
