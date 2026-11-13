"""Owner-authorized, reasoned human reviews with append-only history."""

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .db import database
from .connectivity import evidence_pending, ended_at
from .exam_sessions import finalize_if_expired
from .identity import _identity_key
from .connectivity_review import load_incidents


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(value):
    return value.isoformat() + "Z" if value else None


def unpack(value):
    return json.loads(value) if isinstance(value, (str, bytes)) else value


class Grade(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_index: int = Field(ge=0, le=199)
    marks: Decimal = Field(ge=0, le=1, max_digits=3, decimal_places=2)
    reason: str = Field(min_length=5, max_length=2000)

    @field_validator("reason")
    @classmethod
    def clean(cls, value):
        if len(value.strip()) < 5:
            raise ValueError("Explain the grade using at least 5 characters")
        return value.strip()


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    request_key: UUID
    action: Literal["keep_score", "change_score", "disqualify"]
    reason: str = Field(min_length=5, max_length=5000)
    score: Decimal | None = Field(default=None, ge=0, le=200, max_digits=5, decimal_places=2)
    grades: list[Grade] = Field(default_factory=list, max_length=200)
    event_keys: list[str] = Field(default_factory=list, max_length=200)

    @field_validator("reason")
    @classmethod
    def clean(cls, value):
        if len(value.strip()) < 5:
            raise ValueError("Explain the decision using at least 5 characters")
        return value.strip()


class FindingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    request_key: UUID
    assessment: Literal["supports_violation", "dismissed", "inconclusive"]
    reason: str = Field(min_length=5, max_length=5000)

    @field_validator("reason")
    @classmethod
    def clean(cls, value):
        if len(value.strip()) < 5:
            raise ValueError("Explain the finding using at least 5 characters")
        return value.strip()


class ConnectivityFindingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    request_key: UUID
    finding: Literal["explained", "unresolved", "needs_attention"]
    reason: str = Field(min_length=5, max_length=5000)

    @field_validator("reason")
    @classmethod
    def clean(cls, value):
        if len(value.strip()) < 5:
            raise ValueError("Explain the finding using at least 5 characters")
        return value.strip()


def _load(cursor, assessment_id, invitation_id, employer_id):
    # Share the exam lock with publication before taking session locks.
    cursor.execute("SELECT id FROM assessments WHERE id=%s AND employer_user_id=%s FOR UPDATE", (assessment_id, employer_id))
    if not cursor.fetchone():
        raise HTTPException(404, "Candidate session not found")
    cursor.execute(
        """SELECT s.*,i.candidate_name,i.candidate_email,a.title,a.questions_json,
                  a.duration_minutes,a.starts_at,a.ends_at,a.rules_text
           FROM assessments a JOIN assessment_invitations i ON i.assessment_id=a.id
           JOIN assessment_exam_sessions s ON s.invitation_id=i.id
           WHERE a.id=%s AND i.id=%s AND a.employer_user_id=%s FOR UPDATE""",
        (assessment_id, invitation_id, employer_id),
    )
    session = cursor.fetchone()
    if not session:
        raise HTTPException(404, "Candidate session not found")
    finalize_if_expired(cursor, session, now())
    cursor.execute("SELECT id FROM assessment_result_publications WHERE assessment_id=%s AND is_legacy=FALSE LIMIT 1", (assessment_id,))
    session["published"] = bool(cursor.fetchone())
    return session


def _latest(cursor, session_id):
    cursor.execute("SELECT * FROM assessment_review_decisions WHERE session_id=%s ORDER BY revision DESC LIMIT 1", (session_id,))
    return cursor.fetchone()


def _version(cursor, session_id):
    cursor.execute("SELECT revision FROM assessment_review_state WHERE session_id=%s", (session_id,))
    row = cursor.fetchone()
    return row["revision"] if row else 0


def _advance(cursor, session_id, expected):
    revision = _version(cursor, session_id)
    if revision != expected:
        raise HTTPException(409, "This review changed in another tab. Reload the report before saving.")
    revision += 1
    cursor.execute("INSERT INTO assessment_review_state (session_id,revision) VALUES (%s,%s) ON DUPLICATE KEY UPDATE revision=VALUES(revision)", (session_id, revision))
    return revision


def _answers(cursor, session_id):
    cursor.execute("SELECT question_index,answer_json FROM assessment_exam_answers WHERE session_id=%s", (session_id,))
    return {row["question_index"]: unpack(row["answer_json"]) for row in cursor.fetchall()}


def score_details(questions, answers, grades=None):
    grades = grades or {}
    result, objective, pending, written_earned = [], 0, 0, Decimal(0)
    for index, question in enumerate(questions):
        answer = answers.get(index)
        answered = answer is not None and answer != ""
        item = {"index": index, "type": question["type"], "prompt": question["prompt"],
                "answer": answer, "possible": 1, "options": question.get("options") or [],
                "key": question.get("key"), "correct_index": question.get("correctIndex")}
        if question["type"] == "multiple_choice":
            correct = isinstance(answer, int) and not isinstance(answer, bool) and answer == question.get("correctIndex")
            objective += int(correct)
            item.update(earned=int(correct), grading="automatic", grade_reason=None)
        elif not answered:
            item.update(earned=0, grading="unanswered", grade_reason=None)
        elif str(index) in grades:
            grade = grades[str(index)]
            written_earned += Decimal(str(grade["marks"]))
            item.update(earned=grade["marks"], grading="manual", grade_reason=grade["reason"])
        else:
            pending += 1
            item.update(earned=None, grading="pending", grade_reason=None)
        result.append(item)
    mcq_possible = sum(q["type"] == "multiple_choice" for q in questions)
    return {"questions": result, "possible": len(questions), "objective_earned": objective,
            "objective_possible": mcq_possible, "written_pending": pending,
            "calculated_score": None if pending else float(Decimal(objective) + written_earned),
            "original_score": objective if mcq_possible == len(questions) else None}


def _decision(row):
    if not row:
        return None
    return {"id": row["id"], "revision": row["revision"], "action": row["action"],
            "reason": row["reason"], "score": float(row["final_score"]) if row["final_score"] is not None else None,
            "previous": unpack(row["previous_json"]), "grades": unpack(row["grades_json"]),
            "event_keys": unpack(row["event_keys_json"]), "reviewer_id": row["reviewer_user_id"],
            "reviewer": row["reviewer_username"], "created_at": iso(row["created_at"])}


def _audit(cursor, session_id, employer_id, resource, source_ip):
    cursor.execute("INSERT INTO assessment_review_access_audit (session_id,actor_user_id,resource,accessed_at,source_ip) VALUES (%s,%s,%s,%s,%s)",
                   (session_id, employer_id, resource, now(), source_ip))


def session_report(assessment_id, invitation_id, employer_id, source_ip=None, actor_id=None):
    with database() as connection:
        cursor = connection.cursor(dictionary=True)
        session = _load(cursor, assessment_id, invitation_id, employer_id)
        latest = _latest(cursor, session["id"])
        grades = unpack(latest["grades_json"]) if latest else {}
        scores = score_details(unpack(session["questions_json"]), _answers(cursor, session["id"]), grades)
        revision = _version(cursor, session["id"])
        cursor.execute("SELECT * FROM assessment_review_decisions WHERE session_id=%s ORDER BY revision DESC", (session["id"],))
        history = [_decision(row) for row in cursor.fetchall()]
        cursor.execute("SELECT * FROM assessment_review_findings WHERE session_id=%s ORDER BY revision DESC", (session["id"],))
        findings = []
        for row in cursor.fetchall():
            findings.append({"id": row["id"], "revision": row["revision"], "event_key": row["event_key"],
                             "assessment": row["finding"], "reason": row["reason"],
                             "event_snapshot": unpack(row["event_snapshot_json"]),
                             "reviewer": row["reviewer_username"], "reviewer_id": row["reviewer_user_id"],
                             "created_at": iso(row["created_at"])})
        cursor.execute("SELECT status,stage,failure_reason,checked_at,attempts FROM assessment_identity_checks WHERE invitation_id=%s", (invitation_id,))
        identity = cursor.fetchone()
        if identity:
            identity["checked_at"] = iso(identity["checked_at"])
        cursor.execute("SELECT reference_ciphertext IS NOT NULL AND reference_expires_at>UTC_TIMESTAMP() AS available FROM assessment_monitoring_runs WHERE session_id=%s", (session["id"],))
        reference = cursor.fetchone()
        pending_evidence = evidence_pending(cursor, session)
        cursor.execute("SELECT id,event_type,occurred_at,details_json FROM assessment_connectivity_events WHERE session_id=%s ORDER BY occurred_at,id", (session["id"],))
        connectivity = [{"id": r["id"], "type": r["event_type"], "at": iso(r["occurred_at"]), "details": unpack(r["details_json"])} for r in cursor.fetchall()]
        incident_report = load_incidents(cursor, session, coverage_ready=not pending_evidence)
        cursor.execute("SELECT * FROM assessment_connectivity_findings WHERE session_id=%s ORDER BY revision DESC", (session["id"],))
        connectivity_findings = [{"id": row["id"], "incident_id": row["incident_event_id"], "revision": row["revision"],
                                  "finding": row["finding"], "reason": row["reason"], "reviewer": row["reviewer_username"],
                                  "reviewer_id": row["reviewer_user_id"], "created_at": iso(row["created_at"]),
                                  "incident_snapshot": unpack(row["incident_snapshot_json"])} for row in cursor.fetchall()]
        _audit(cursor, session["id"], actor_id or employer_id, "report", source_ip)
        connection.commit()
    system_locked = bool(session["disqualified_at"])
    ended = bool(ended_at(session))
    latest_json = _decision(latest)
    return {"assessment_id": assessment_id, "invitation_id": invitation_id, "session_id": session["id"],
            "title": session["title"], "name": session["candidate_name"], "email": session["candidate_email"],
            "attempt_state": "disqualified" if system_locked else "interrupted" if session.get("interrupted_at") else "submitted" if ended else "in_progress",
            "interruption_reason": session.get("interruption_reason"), "connectivity_events": connectivity,
            "connectivity_report": incident_report, "connectivity_findings": connectivity_findings,
            "decision_blocked_reason": pending_evidence if ended else "Exam still in progress",
            "system_disqualification_locked": system_locked, "disqualification_reason": session["disqualification_reason"],
            "submission_reason": session["submission_reason"], "duration_minutes": session["duration_minutes"],
            "starts_at": iso(session["starts_at"]), "ends_at": iso(session["ends_at"]),
            "started_at": iso(session["started_at"]), "ended_at": iso(session["submitted_at"] or session["disqualified_at"] or session.get("interrupted_at")),
            "revision": revision, "can_decide": ended and not session["published"] and not pending_evidence, "published": session["published"], "scores": scores, "decision": latest_json,
            "history": history, "findings": findings, "identity": identity,
            "reference_available": bool(reference and reference["available"]),
            "outcome": "disqualified" if system_locked or (latest and latest["action"] == "disqualify") else "eligible" if latest else "awaiting_review"}


def save_decision(assessment_id, invitation_id, employer_id, payload):
    digest = hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
    with database() as connection:
        cursor = connection.cursor(dictionary=True)
        session = _load(cursor, assessment_id, invitation_id, employer_id)
        if session["published"]:
            raise HTTPException(409, "Results are published. This assessment is read only.")
        cursor.execute("SELECT * FROM assessment_review_decisions WHERE session_id=%s AND request_key=%s", (session["id"], str(payload.request_key)))
        duplicate = cursor.fetchone()
        if duplicate:
            if duplicate["payload_hash"] != digest or duplicate["reviewer_user_id"] != employer_id:
                raise HTTPException(409, "The request key was already used for a different decision")
            return {"decision": _decision(duplicate), "revision": _version(cursor, session["id"]), "duplicate": True}
        if not (session["submitted_at"] or session["disqualified_at"] or session.get("interrupted_at")):
            raise HTTPException(409, "Wait until the exam attempt ends before recording a disposition")
        pending_evidence = evidence_pending(cursor, session)
        if pending_evidence:
            raise HTTPException(409, pending_evidence)
        if session["disqualified_at"] and payload.action != "disqualify":
            connection.commit()
            raise HTTPException(409, "This system disqualification cannot be changed")
        if payload.action != "change_score" and payload.score is not None:
            raise HTTPException(422, "Enter a replacement score only for Change score")
        latest = _latest(cursor, session["id"])
        grades = dict(unpack(latest["grades_json"])) if latest else {}
        questions = unpack(session["questions_json"])
        seen = set()
        for grade in payload.grades:
            index = grade.question_index
            if index in seen or index >= len(questions) or questions[index]["type"] != "written":
                raise HTTPException(422, "Grades must refer to unique written questions in this assessment")
            seen.add(index)
            grades[str(index)] = {"marks": float(grade.marks), "reason": grade.reason}
        scores = score_details(questions, _answers(cursor, session["id"]), grades)
        if payload.action != "disqualify" and scores["written_pending"]:
            raise HTTPException(422, "Grade every answered written question before confirming the score")
        if payload.action == "change_score":
            if payload.score is None or payload.score > scores["possible"]:
                raise HTTPException(422, f"Enter a score from 0 to {scores['possible']}")
            final_score = float(payload.score)
        elif latest and ({key: value["marks"] for key, value in grades.items()} ==
                         {key: value["marks"] for key, value in unpack(latest["grades_json"]).items()}) and latest["final_score"] is not None:
            final_score = float(latest["final_score"])
        else:
            final_score = scores["calculated_score"]
        if len(set(payload.event_keys)) != len(payload.event_keys):
            raise HTTPException(422, "Observation references must be unique")
        for key in payload.event_keys:
            cursor.execute("SELECT event_key FROM assessment_monitoring_events WHERE session_id=%s AND event_key=%s", (session["id"], key))
            if not cursor.fetchone():
                raise HTTPException(422, "An observation does not belong to this session")
        revision = _advance(cursor, session["id"], payload.expected_revision)
        cursor.execute("SELECT username FROM users WHERE id=%s", (employer_id,))
        reviewer = cursor.fetchone()["username"]
        previous = {"action": latest["action"] if latest else None,
                    "score": float(latest["final_score"]) if latest and latest["final_score"] is not None else scores["calculated_score"],
                    "original_objective_score": scores["objective_earned"], "objective_possible": scores["objective_possible"],
                    "system_disqualification": session["disqualification_reason"]}
        cursor.execute(
            """INSERT INTO assessment_review_decisions
               (session_id,revision,request_key,payload_hash,action,reason,final_score,previous_json,
                grades_json,event_keys_json,reviewer_user_id,reviewer_username,created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (session["id"], revision, str(payload.request_key), digest, payload.action, payload.reason,
             final_score, json.dumps(previous), json.dumps(grades), json.dumps(payload.event_keys), employer_id, reviewer, now()),
        )
        connection.commit()
    return {"revision": revision, "duplicate": False}


def save_finding(assessment_id, invitation_id, employer_id, event_key, payload):
    digest = hashlib.sha256((event_key + payload.model_dump_json()).encode()).hexdigest()
    with database() as connection:
        cursor = connection.cursor(dictionary=True)
        session = _load(cursor, assessment_id, invitation_id, employer_id)
        if session["published"]:
            raise HTTPException(409, "Results are published. Findings cannot be changed.")
        cursor.execute("SELECT payload_hash FROM assessment_review_findings WHERE session_id=%s AND request_key=%s", (session["id"], str(payload.request_key)))
        duplicate = cursor.fetchone()
        if duplicate:
            if duplicate["payload_hash"] != digest:
                raise HTTPException(409, "The request key was already used for a different finding")
            return {"revision": _version(cursor, session["id"]), "duplicate": True}
        cursor.execute("SELECT event_key,event_type,category,part_id,start_ms,end_ms,closed,details_json FROM assessment_monitoring_events WHERE session_id=%s AND event_key=%s", (session["id"], event_key))
        event = cursor.fetchone()
        if not event:
            raise HTTPException(404, "Observation not found")
        event["details_json"] = unpack(event["details_json"])
        revision = _advance(cursor, session["id"], payload.expected_revision)
        cursor.execute("SELECT username FROM users WHERE id=%s", (employer_id,))
        reviewer = cursor.fetchone()["username"]
        cursor.execute(
            """INSERT INTO assessment_review_findings
               (session_id,event_key,revision,request_key,payload_hash,finding,reason,event_snapshot_json,
                reviewer_user_id,reviewer_username,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (session["id"], event_key, revision, str(payload.request_key), digest, payload.assessment,
             payload.reason, json.dumps(event), employer_id, reviewer, now()),
        )
        connection.commit()
    return {"revision": revision, "duplicate": False}


def save_connectivity_finding(assessment_id, invitation_id, employer_id, incident_id, payload):
    digest = hashlib.sha256((str(incident_id) + payload.model_dump_json()).encode()).hexdigest()
    with database() as connection:
        cursor = connection.cursor(dictionary=True)
        session = _load(cursor, assessment_id, invitation_id, employer_id)
        if session["published"]:
            raise HTTPException(409, "Results are published. Findings cannot be changed.")
        cursor.execute("SELECT payload_hash,reviewer_user_id FROM assessment_connectivity_findings WHERE session_id=%s AND request_key=%s",
                       (session["id"], str(payload.request_key)))
        duplicate = cursor.fetchone()
        if duplicate:
            if duplicate["payload_hash"] != digest or duplicate["reviewer_user_id"] != employer_id:
                raise HTTPException(409, "The request key was already used for a different finding")
            return {"revision": _version(cursor, session["id"]), "duplicate": True}
        if not ended_at(session):
            raise HTTPException(409, "Wait until the exam attempt ends before recording a finding")
        pending = evidence_pending(cursor, session)
        if pending:
            raise HTTPException(409, pending)
        incident_report = load_incidents(cursor, session, coverage_ready=True)
        incident = next((item for item in incident_report["incidents"] if item["id"] == incident_id), None)
        if not incident:
            raise HTTPException(404, "Interruption not found")
        revision = _advance(cursor, session["id"], payload.expected_revision)
        cursor.execute("SELECT username FROM users WHERE id=%s", (employer_id,))
        reviewer = cursor.fetchone()["username"]
        cursor.execute(
            """INSERT INTO assessment_connectivity_findings
               (session_id,incident_event_id,revision,request_key,payload_hash,finding,reason,incident_snapshot_json,
                reviewer_user_id,reviewer_username,created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (session["id"], incident_id, revision, str(payload.request_key), digest, payload.finding,
             payload.reason, json.dumps(incident), employer_id, reviewer, now()),
        )
        connection.commit()
    return {"revision": revision, "duplicate": False}


def reference_photo(assessment_id, invitation_id, employer_id, source_ip):
    with database() as connection:
        cursor = connection.cursor(dictionary=True)
        session = _load(cursor, assessment_id, invitation_id, employer_id)
        cursor.execute("SELECT reference_ciphertext FROM assessment_monitoring_runs WHERE session_id=%s AND reference_expires_at>UTC_TIMESTAMP()", (session["id"],))
        row = cursor.fetchone()
        if not row or not row["reference_ciphertext"]:
            raise HTTPException(404, "The verified reference photo is unavailable or expired")
        blob = bytes(row["reference_ciphertext"])
        raw = AESGCM(_identity_key()).decrypt(blob[:12], blob[12:], f"identity:{invitation_id}:selfie".encode())
        _audit(cursor, session["id"], employer_id, "reference_photo", source_ip)
        connection.commit()
    return raw


def candidate_cards(assessment_id, employer_id):
    with database() as connection:
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT id,title,questions_json FROM assessments WHERE id=%s AND employer_user_id=%s", (assessment_id, employer_id))
        assessment = cursor.fetchone()
        if not assessment:
            raise HTTPException(404, "Assessment not found")
        questions = unpack(assessment["questions_json"])
        cursor.execute(
            """SELECT s.*,i.id AS invitation_id,i.candidate_name,i.candidate_email,i.delivery_status
               FROM assessment_invitations i LEFT JOIN assessment_exam_sessions s ON s.invitation_id=i.id
               WHERE i.assessment_id=%s ORDER BY i.id FOR UPDATE""", (assessment_id,))
        rows = cursor.fetchall()
        cards = []
        for row in rows:
            latest, count, quality, coverage_missing = None, 0, 0, False
            if row["id"]:
                finalize_if_expired(cursor, row, now())
                latest = _latest(cursor, row["id"])
                cursor.execute("SELECT COUNT(*) AS total,COALESCE(SUM(category='monitoring_quality'),0) AS quality FROM assessment_monitoring_events WHERE session_id=%s", (row["id"],))
                counts = cursor.fetchone()
                count, quality = counts["total"], int(counts["quality"])
                scores = score_details(questions, _answers(cursor, row["id"]), unpack(latest["grades_json"]) if latest else {})
                cursor.execute("SELECT COUNT(*) AS total FROM assessment_monitoring_samples x JOIN assessment_monitoring_jobs j ON j.part_id=x.part_id WHERE j.session_id=%s", (row["id"],))
                coverage_missing = cursor.fetchone()["total"] == 0 or quality > 0
            else:
                scores = {"calculated_score": None, "possible": len(questions), "written_pending": 0}
            state = "disqualified" if row["disqualified_at"] else "interrupted" if row.get("interrupted_at") else "submitted" if row["submitted_at"] else "in_progress" if row["id"] else "not_started"
            cards.append({"invitation_id": row["invitation_id"], "name": row["candidate_name"], "email": row["candidate_email"],
                          "attempt_state": state, "has_session": bool(row["id"]), "invitation_state": row["delivery_status"],
                          "review_state": "reviewed" if latest else "awaiting_review" if state in {"submitted", "disqualified", "interrupted"} else "not_ready",
                          "outcome": "disqualified" if row["disqualified_at"] or (latest and latest["action"] == "disqualify") else "eligible" if latest else "awaiting_review",
                          "score": float(latest["final_score"]) if latest and latest["final_score"] is not None else scores["calculated_score"],
                          "possible": len(questions), "written_pending": scores["written_pending"],
                          "objective_earned": scores.get("objective_earned"), "objective_possible": scores.get("objective_possible"),
                          "decision": _decision(latest), "event_count": count, "quality_count": quality, "coverage_attention": coverage_missing})
        connection.commit()
    return {"assessment_id": assessment_id, "title": assessment["title"], "candidates": cards}
