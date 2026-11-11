"""Candidate invitations, attempt history, and published result snapshots."""

import json
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import mysql.connector
from fastapi import HTTPException

from .config import SEB_BASE_URL
from .db import database
from .connectivity import advance


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() + "Z" if moment else None


def _launch_link(exam_username: str, has_specific_config: bool) -> tuple[str, str]:
    path = f"/seb/launch/{exam_username}" if has_specific_config else "/seb/config"
    download = SEB_BASE_URL + path
    parsed = urlsplit(download)
    launch = urlunsplit(("sebs" if parsed.scheme == "https" else "seb", parsed.netloc, parsed.path, "", ""))
    return launch, download


def candidate_assessments(user_id: int) -> dict:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        with database() as connection:
            cursor = connection.cursor(dictionary=True)
            cursor.execute("SELECT * FROM assessment_exam_sessions WHERE candidate_user_id=%s AND submitted_at IS NULL AND disqualified_at IS NULL AND interrupted_at IS NULL FOR UPDATE", (user_id,))
            for session in cursor.fetchall():
                advance(cursor, session, now)
            cursor.execute(
                """SELECT i.id AS invitation_id, i.exam_username, i.candidate_email,
                          i.seb_config_key, a.title, a.description, a.duration_minutes,
                          a.starts_at, a.ends_at, a.questions_json,
                          s.id AS session_id, s.started_at, s.deadline_at, s.submitted_at,
                          s.submission_reason, s.current_index, s.disqualified_at, s.interrupted_at, s.disqualification_reason,
                          p.id AS publication_id,p.created_at AS published_at,pr.result_json AS published_result
                   FROM assessment_invitations i
                   JOIN assessments a ON a.id = i.assessment_id
                   JOIN users u ON u.id = %s AND u.role = 'candidate' AND u.is_active = TRUE
                   LEFT JOIN assessment_exam_sessions s ON s.invitation_id = i.id AND s.candidate_user_id = u.id
                   LEFT JOIN assessment_result_publications p ON p.assessment_id=a.id AND p.is_legacy=FALSE
                   LEFT JOIN assessment_publication_results pr ON pr.publication_id=p.id AND pr.invitation_id=i.id AND pr.candidate_user_id=u.id
                   WHERE i.delivery_status = 'sent'
                     AND (i.candidate_user_id = u.id OR (i.candidate_user_id IS NULL AND i.candidate_email COLLATE utf8mb4_unicode_ci = u.email))
                   ORDER BY a.starts_at DESC, i.id DESC""",
                (user_id,),
            )
            rows = cursor.fetchall()
            result = []
            for row in rows:
                questions = row["questions_json"]
                if isinstance(questions, (str, bytes)):
                    questions = json.loads(questions)
                answers = {}
                if row["session_id"]:
                    cursor.execute(
                        "SELECT question_index, answer_json FROM assessment_exam_answers WHERE session_id = %s",
                        (row["session_id"],),
                    )
                    answers = {item["question_index"]: json.loads(item["answer_json"]) for item in cursor.fetchall()}
                if row["disqualified_at"]:
                    state = "disqualified"
                elif row["interrupted_at"]:
                    state = "interrupted"
                elif row["submitted_at"]:
                    state = "submitted"
                elif row["started_at"]:
                    state = "in_progress"
                elif now < row["starts_at"]:
                    state = "upcoming"
                elif now < row["ends_at"]:
                    state = "ready"
                else:
                    state = "missed"
                published = row["published_result"]
                if isinstance(published, (str, bytes)):
                    published = json.loads(published)
                launch, download = _launch_link(row["exam_username"], bool(row["seb_config_key"]))
                result.append({
                    "invitation_id": row["invitation_id"], "title": row["title"],
                    "description": row["description"] or "", "email": row["candidate_email"],
                    "duration_minutes": row["duration_minutes"],
                    "starts_at": _iso(row["starts_at"]), "ends_at": _iso(row["ends_at"]),
                    "started_at": _iso(row["started_at"]), "submitted_at": _iso(row["submitted_at"]),
                    "disqualified_at": _iso(row["disqualified_at"]),
                    "disqualification_reason": row["disqualification_reason"],
                    "submission_reason": row["submission_reason"], "status": state,
                    "question_count": len(questions), "answered_count": sum(answer is not None for answer in answers.values()),
                    "current_question": row["current_index"] + 1 if state == "in_progress" else None,
                    "publication": {"id": row["publication_id"], "published_at": _iso(row["published_at"])} if published else None,
                    "multiple_choice_score": published["multiple_choice_score"] if published else None,
                    "written_score": published["written_score"] if published else None,
                    "written_grading_pending": published["written_grading_pending"] if published else None,
                    "review": published["review"] if published else None,
                    "seb_launch_url": launch, "seb_download_url": download,
                })
            connection.commit()
            cursor.close()
    except mysql.connector.Error as exc:
        raise HTTPException(status_code=503, detail="Candidate assessments are temporarily unavailable") from exc
    return {"assessments": result}
