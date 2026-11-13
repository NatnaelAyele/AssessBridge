"""Bounded recovery. A missing heartbeat is never evidence of a voluntary exit."""
from datetime import timedelta
import json

from fastapi import HTTPException

RECOVERY_SECONDS = 180
TOTAL_RECOVERY_SECONDS = 600
HEARTBEAT_SECONDS = 10
DETECTION_SECONDS = 25
FINAL_UPLOAD_SECONDS = 900


def ended_at(session):
    return session.get("submitted_at") or session.get("disqualified_at") or session.get("interrupted_at")


def event(cursor, session_id, kind, at, details=None):
    cursor.execute("INSERT INTO assessment_connectivity_events (session_id,event_type,occurred_at,details_json) VALUES (%s,%s,%s,%s)",
                   (session_id, kind, at, json.dumps(details or {})))


def begin_recovery(cursor, session, at, reason):
    if ended_at(session) or session.get("recovery_started_at"):
        return
    cursor.execute("UPDATE assessment_exam_sessions SET recovery_started_at=%s,recovery_reason=%s WHERE id=%s", (at, reason, session["id"]))
    session.update(recovery_started_at=at, recovery_reason=reason)
    event(cursor, session["id"], "interruption_started", at, {"reason": reason})


def finish_recovery(cursor, session, at):
    start = session.get("recovery_started_at")
    if not start or ended_at(session):
        return
    seconds = max(0, (at - start).total_seconds())
    total = float(session.get("recovery_seconds") or 0) + seconds
    event(cursor, session["id"], "connection_restored", at, {"reason": session["recovery_reason"], "duration_seconds": round(seconds, 3)})
    cursor.execute("UPDATE assessment_exam_sessions SET recovery_started_at=NULL,recovery_reason=NULL,recovery_seconds=%s WHERE id=%s", (total, session["id"]))
    session.update(recovery_started_at=None, recovery_reason=None, recovery_seconds=total)


def advance(cursor, session, now):
    if ended_at(session):
        return
    seen = session["last_seen_at"] or session["started_at"]
    if not session.get("recovery_started_at") and now >= seen + timedelta(seconds=DETECTION_SECONDS):
        begin_recovery(cursor, session, seen + timedelta(seconds=HEARTBEAT_SECONDS), "connection_lost")
    start = session.get("recovery_started_at")
    limit = None
    if start:
        remaining = min(RECOVERY_SECONDS, max(0, TOTAL_RECOVERY_SECONDS - float(session.get("recovery_seconds") or 0)))
        limit = start + timedelta(seconds=remaining)
    if limit and limit < session["deadline_at"] and now >= limit:
        reason = session.get("recovery_reason") or "connection_lost"
        cursor.execute("UPDATE assessment_exam_sessions SET interrupted_at=%s,interruption_reason=%s WHERE id=%s", (limit, reason, session["id"]))
        session.update(interrupted_at=limit, interruption_reason=reason)
        event(cursor, session["id"], "attempt_interrupted", limit, {"reason": reason, "limit": "recovery_limit"})
    elif now >= session["deadline_at"]:
        cursor.execute("UPDATE assessment_exam_sessions SET submitted_at=deadline_at,submission_reason='timeout' WHERE id=%s", (session["id"],))
        session.update(submitted_at=session["deadline_at"], submission_reason="timeout")
        event(cursor, session["id"], "deadline_reached", session["deadline_at"])


def require_answering(session):
    if session.get("interrupted_at"):
        raise HTTPException(409, "This attempt was interrupted and cannot be reopened")
    if session.get("resume_required") or session.get("recovery_started_at"):
        raise HTTPException(409, "Complete the recovery checks before continuing")


def evidence_pending(cursor, session):
    cursor.execute("SELECT status FROM assessment_recordings WHERE session_id=%s", (session["id"],))
    recording = cursor.fetchone()
    if recording and recording["status"] == "recording":
        return "Recording upload pending"
    cursor.execute("SELECT 1 FROM assessment_monitoring_jobs WHERE session_id=%s AND status IN ('pending','processing') LIMIT 1", (session["id"],))
    if cursor.fetchone():
        return "Recording analysis pending"
    return None
