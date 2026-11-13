"""Exam-wide approval and immutable candidate result snapshots."""

import hashlib
import json
from decimal import Decimal
from datetime import datetime, time, timedelta, timezone

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .auditing import _status, assessment_for_auditor
from .db import database
from .connectivity import evidence_pending
from .reviews import _answers, _latest, iso, now, score_details, unpack


class PublishResults(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmed: bool
    remark: str = Field(min_length=1, max_length=5000)

    @field_validator("remark")
    @classmethod
    def clean_remark(cls, value):
        if not value.strip():
            raise ValueError("Enter a publication remark")
        return value.strip()


def _latest_publication(cursor, assessment_id):
    cursor.execute("""SELECT id,source_version,publisher_user_id,publisher_username,remark,candidate_count,created_at
                      FROM assessment_result_publications WHERE assessment_id=%s AND is_legacy=FALSE LIMIT 1""", (assessment_id,))
    row = cursor.fetchone()
    if row:
        row["published_at"] = iso(row.pop("created_at"))
    return row


def _state(cursor, assessment_id, lock=False):
    assessment = assessment_for_auditor(cursor, assessment_id, lock=lock)
    # Publication locks invitations, then sessions in a stable order. Review writes
    # lock the same sessions, preventing a decision change during approval.
    cursor.execute("SELECT id,candidate_user_id FROM assessment_invitations WHERE assessment_id=%s ORDER BY id" + (" FOR UPDATE" if lock else ""), (assessment_id,))
    invitations = cursor.fetchall()
    rows, versions, audited = [], [], 0
    for invitation in invitations:
        cursor.execute("SELECT * FROM assessment_exam_sessions WHERE invitation_id=%s" + (" FOR UPDATE" if lock else ""), (invitation["id"],))
        session = cursor.fetchone()
        audit, decision = None, None
        if session:
            audit, _ = _status(cursor, session["id"])
            decision = _latest(cursor, session["id"])
        ready = bool(session and (session["submitted_at"] or session["disqualified_at"] or session.get("interrupted_at")) and decision and audit["current"] and not evidence_pending(cursor, session))
        audited += int(ready)
        versions.append({"invitation": invitation["id"], "account": invitation["candidate_user_id"],
                         "session": session["id"] if session else None,
                         "version": audit["version"] if audit else None,
                         "audit": audit["current"]["id"] if audit and audit["current"] else None,
                         "decision": decision["id"] if decision else None})
        rows.append((invitation, session, decision))
    version = hashlib.sha256(json.dumps(versions, sort_keys=True).encode()).hexdigest()
    latest = _latest_publication(cursor, assessment_id)
    current = bool(latest)
    can_publish = bool(assessment["closed"] and invitations and audited == len(invitations) and not current)
    pending = len(invitations) - audited
    blocked = "No candidates assigned." if not invitations else f"{pending} {'candidate still needs' if pending == 1 else 'candidates still need'} auditing." if pending else None
    return {"status": "published" if current else "unpublished",
            "latest": latest, "version": version, "candidate_count": len(invitations), "audited_count": audited,
            "can_publish": can_publish, "blocked_reason": blocked}, rows, unpack(assessment["questions_json"])


def publication_state(assessment_id):
    with database() as connection:
        cursor = connection.cursor(dictionary=True)
        state, _, _ = _state(cursor, assessment_id)
    return state


def _history_filters(search, period, date_from, date_to):
    where = ["p.is_legacy=FALSE", "a.ends_at<=UTC_TIMESTAMP()"]
    parameters = []
    if search.strip():
        where.append("(LOCATE(LOWER(%s),LOWER(a.title))>0 OR LOCATE(LOWER(%s),LOWER(COALESCE(a.internal_reference,'')))>0)")
        parameters.extend([search.strip(), search.strip()])
    eat = timezone(timedelta(hours=3))
    today = now().replace(tzinfo=timezone.utc).astimezone(eat).date()
    if period == "custom":
        if date_from is None or date_to is None or date_from > date_to:
            raise HTTPException(422, "Choose a valid From and To date range.")
        start, end = date_from, date_to
    elif period in {"today", "last7", "last30"}:
        start = today - timedelta(days={"today":0,"last7":6,"last30":29}[period])
        end = today
    else:
        return where, parameters
    try:
        lower = datetime.combine(start, time.min, eat).astimezone(timezone.utc).replace(tzinfo=None)
        upper = datetime.combine(end + timedelta(days=1), time.min, eat).astimezone(timezone.utc).replace(tzinfo=None)
    except OverflowError:
        raise HTTPException(422, "Choose dates within the supported date range.")
    where.extend(["p.created_at>=%s", "p.created_at<%s"])
    parameters.extend([lower, upper])
    return where, parameters


def publication_history(page=1, search="", period="all", date_from=None, date_to=None):
    where, parameters = _history_filters(search, period, date_from, date_to)
    source = "FROM assessment_result_publications p JOIN assessments a ON a.id=p.assessment_id WHERE " + " AND ".join(where)
    with database() as connection:
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT COUNT(*) AS total " + source, tuple(parameters))
        total = cursor.fetchone()["total"]
        cursor.execute("""SELECT p.id,p.assessment_id,a.title,a.internal_reference,p.publisher_username,
                          p.candidate_count,p.remark,p.created_at """ + source +
                          " ORDER BY p.created_at DESC,p.id DESC LIMIT 20 OFFSET %s", (*parameters, (page-1)*20))
        items = cursor.fetchall()
    for item in items:
        item["published_at"] = iso(item.pop("created_at"))
    return {"items": items, "total": total, "page": page, "page_size": 20}


def published_results(publication_id):
    with database() as connection:
        cursor = connection.cursor(dictionary=True)
        cursor.execute("""SELECT p.id,p.assessment_id,a.title,a.internal_reference,p.publisher_username,
                          p.candidate_count,p.remark,p.created_at FROM assessment_result_publications p
                          JOIN assessments a ON a.id=p.assessment_id
                          WHERE p.id=%s AND p.is_legacy=FALSE AND a.ends_at<=UTC_TIMESTAMP()""", (publication_id,))
        publication = cursor.fetchone()
        if not publication:
            raise HTTPException(404, "Published results not found")
        cursor.execute("""SELECT r.invitation_id,i.candidate_name AS name,i.candidate_email AS email,r.result_json
                          FROM assessment_publication_results r JOIN assessment_invitations i ON i.id=r.invitation_id
                          WHERE r.publication_id=%s ORDER BY i.candidate_name,i.id""", (publication_id,))
        candidates = [{"invitation_id":row["invitation_id"], "name":row["name"], "email":row["email"],
                       "result":unpack(row["result_json"])} for row in cursor.fetchall()]
    publication["published_at"] = iso(publication.pop("created_at"))
    return {"publication":publication, "candidates":candidates}


def publish(assessment_id, publisher_id, payload):
    if not payload.confirmed:
        raise HTTPException(422, "Confirm approval before publishing results.")
    with database() as connection:
        cursor = connection.cursor(dictionary=True)
        state, rows, questions = _state(cursor, assessment_id, lock=True)
        if state["status"] == "published":
            return {"publication": state, "duplicate": True}
        if state["version"] != payload.expected_version:
            raise HTTPException(409, "The assessment or audits changed. Refresh the candidate list before publishing.")
        if not state["can_publish"]:
            raise HTTPException(409, state["blocked_reason"] or "Every candidate must be audited before publication.")
        cursor.execute("SELECT username FROM users WHERE id=%s AND role='auditor' AND is_active=TRUE", (publisher_id,))
        publisher = cursor.fetchone()
        if not publisher:
            raise HTTPException(403, "Access denied")
        # The final score is frozen alongside the objective and manual grades.
        snapshots = []
        for invitation, session, decision in rows:
            scores = score_details(questions, _answers(cursor, session["id"]), unpack(decision["grades_json"]))
            written = [q for q in scores["questions"] if q["type"] == "written"]
            written_earned = float(sum((Decimal(str(q["earned"] or 0)) for q in written), Decimal(0)))
            objective = {"earned": scores["objective_earned"], "possible": scores["objective_possible"],
                         "percent": round(100 * scores["objective_earned"] / scores["objective_possible"])} if scores["objective_possible"] else None
            snapshot = {"multiple_choice_score": objective,
                        "written_score": {"earned": written_earned, "possible": len(written), "pending": scores["written_pending"]} if written else None,
                        "written_grading_pending": bool(scores["written_pending"]),
                        "submitted_at": iso(session["submitted_at"]),
                        "review": {"action": decision["action"], "score": float(decision["final_score"]) if decision["final_score"] is not None else None,
                                   "possible": scores["possible"], "reviewed_at": iso(decision["created_at"]),
                                   "outcome": "disqualified" if session["disqualified_at"] or decision["action"] == "disqualify" else "eligible"}}
            snapshots.append((invitation["id"], session["candidate_user_id"], json.dumps(snapshot)))
        cursor.execute("""INSERT INTO assessment_result_publications
                          (assessment_id,source_version,publisher_user_id,publisher_username,remark,candidate_count,created_at)
                          VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                       (assessment_id, state["version"], publisher_id, publisher["username"], payload.remark, len(rows), now()))
        publication_id = cursor.lastrowid
        cursor.executemany("INSERT INTO assessment_publication_results (publication_id,invitation_id,candidate_user_id,result_json) VALUES (%s,%s,%s,%s)",
                           [(publication_id, *snapshot) for snapshot in snapshots])
        state, _, _ = _state(cursor, assessment_id)
        connection.commit()
    return {"publication": state, "duplicate": False}
