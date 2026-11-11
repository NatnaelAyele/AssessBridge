"""Durable, session-bound monitoring evidence; no exam state mutations."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import time

import mysql.connector

from fastapi import HTTPException

from ..config import RECORDING_RETENTION_DAYS
from ..db import database
from .rules import POLICY

CAPABILITIES = {
    "identity_continuity": "supported", "additional_person": "always_enabled",
    "mobile_phone": "assessment_policy", "book": "assessment_policy",
    "tablet": "assessment_policy", "calculator": "assessment_policy", "head_orientation": "coarse_estimate",
    "speech_activity": "supported", "recording_quality": "supported",
    "sunglasses": "unsupported", "speaker_identification": "unsupported",
    "precise_gaze": "unsupported", "liveness_or_replay_authentication": "unsupported",
}


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def unpack(value):
    return json.loads(value) if isinstance(value, (str, bytes)) else value


def create_run(cursor, session, exam):
    policy = deepcopy(POLICY)
    policy["prohibited_items"] = [item.strip() for item in (exam.get("rules_text") or "").split(",") if item.strip()]
    cursor.execute(
        """INSERT IGNORE INTO assessment_monitoring_runs
           (session_id, policy_json, capabilities_json, reference_ciphertext, reference_expires_at, created_at)
           SELECT %s, %s, %s, selfie_ciphertext, %s, %s FROM assessment_identity_checks
           WHERE invitation_id = %s AND candidate_user_id = %s AND status = 'passed'""",
        (session["id"], json.dumps(policy), json.dumps(CAPABILITIES),
         now() + timedelta(days=RECORDING_RETENTION_DAYS), now(),
         session["invitation_id"], session["candidate_user_id"]),
    )


def create_job(cursor, session_id, part_id):
    cursor.execute(
        """INSERT IGNORE INTO assessment_monitoring_jobs (part_id, session_id, updated_at)
           SELECT %s, session_id, %s FROM assessment_monitoring_runs WHERE session_id = %s""",
        (part_id, now(), session_id),
    )


def save_events(cursor, session_id, part_id, events):
    for event in events:
        cursor.execute(
            """INSERT INTO assessment_monitoring_events
               (event_key, session_id, part_id, event_type, category, start_ms, end_ms,
                confidence, closed, details_json, created_at, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON DUPLICATE KEY UPDATE end_ms=VALUES(end_ms), confidence=VALUES(confidence),
                 closed=VALUES(closed), details_json=VALUES(details_json), updated_at=VALUES(updated_at)""",
            (event["event_key"], session_id, part_id, event["event_type"], event["category"],
             event["start_ms"], event["end_ms"], event["confidence"], event["closed"],
             json.dumps(event["details"], allow_nan=False), now(), now()),
        )


def record_sample(session_id, part_id, stream, start_ms, end_ms, metrics, events):
    for attempt in range(3):
        try:
            _record_sample(session_id, part_id, stream, start_ms, end_ms, metrics, events)
            return
        except mysql.connector.Error as exc:
            if exc.errno not in {1205, 1213} or attempt == 2:
                raise
            time.sleep(.05 * (attempt + 1))


def _record_sample(session_id, part_id, stream, start_ms, end_ms, metrics, events):
    with database() as connection:
        cursor = connection.cursor()
        # Both audio and video insert child rows. Lock their common parent
        # before either FK check takes a shared lock, avoiding lock upgrades.
        cursor.execute("UPDATE assessment_monitoring_jobs SET updated_at=%s WHERE part_id=%s", (now(), part_id))
        cursor.execute(
            """INSERT INTO assessment_monitoring_samples (part_id,stream,start_ms,end_ms,metrics_json)
               VALUES (%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE
                 end_ms=VALUES(end_ms),metrics_json=VALUES(metrics_json)""",
            (part_id, stream, start_ms, end_ms, json.dumps(metrics, allow_nan=False)),
        )
        save_events(cursor, session_id, part_id, events)
        connection.commit()


def diagnostic(session_id, part_id, kind, details, start_ms=0, end_ms=0):
    event = {"event_key": hashlib.sha256(f"{session_id}:{part_id}:{kind}".encode()).hexdigest(),
             "event_type": kind, "category": "monitoring_quality", "start_ms": start_ms,
             "end_ms": end_ms, "confidence": None, "closed": True, "details": details}
    with database() as connection:
        cursor = connection.cursor()
        save_events(cursor, session_id, part_id, [event])
        connection.commit()


def purge_reference_images():
    with database() as connection:
        cursor = connection.cursor()
        cursor.execute("UPDATE assessment_monitoring_runs SET reference_ciphertext=NULL WHERE reference_expires_at<=%s", (now(),))
        connection.commit()


def reconcile_gaps():
    """Record missing recording intervals, including attempts with no uploads."""
    with database() as connection:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """SELECT s.id,s.started_at,COALESCE(s.submitted_at,s.disqualified_at,s.interrupted_at) AS ended_at
               FROM assessment_monitoring_runs m JOIN assessment_exam_sessions s ON s.id=m.session_id
               JOIN assessment_recordings r ON r.session_id=s.id
               WHERE r.status IN ('complete','incomplete') AND NOT EXISTS (SELECT 1 FROM assessment_monitoring_jobs j WHERE j.session_id=s.id AND j.status IN ('pending','processing')) AND COALESCE(s.submitted_at,s.disqualified_at,s.interrupted_at) BETWEEN %s AND %s""",
            (now()-timedelta(days=RECORDING_RETENTION_DAYS), now()),
        )
        sessions = cursor.fetchall()
        for session in sessions:
            duration = max(0, int((session["ended_at"]-session["started_at"]).total_seconds()*1000))
            cursor.execute(
                """SELECT p.started_at,x.stream,MIN(x.start_ms) AS first_ms,MAX(x.end_ms) AS last_ms
                   FROM assessment_monitoring_samples x JOIN assessment_recording_parts p ON p.id=x.part_id
                   JOIN assessment_recordings r ON r.id=p.recording_id
                   WHERE r.session_id=%s GROUP BY p.id,p.started_at,x.stream ORDER BY p.started_at""", (session["id"],))
            intervals = cursor.fetchall()
            for stream in ("video", "audio"):
                position = 0
                gaps = []
                for interval in intervals:
                    if interval["stream"] != stream:
                        continue
                    offset = int((interval["started_at"]-session["started_at"]).total_seconds()*1000)
                    start, end = max(0, offset+interval["first_ms"]), min(duration, offset+interval["last_ms"])
                    if start-position > 2500:
                        gaps.append((position, min(start, duration)))
                    position = max(position, end)
                if duration-position > 2500:
                    gaps.append((position, duration))
                # Recompute after retries; a former processing gap may now be filled.
                kind = stream + "_coverage_gap"
                keys = [hashlib.sha256(f"{session['id']}:{kind}:{start}".encode()).hexdigest() for start, end in gaps if end > start]
                obsolete = " AND event_key NOT IN (" + ",".join(["%s"] * len(keys)) + ")" if keys else ""
                cursor.execute("DELETE FROM assessment_monitoring_events WHERE session_id=%s AND event_type=%s AND part_id IS NULL" + obsolete, (session["id"], kind, *keys))
                for start, end in gaps:
                    if end <= start:
                        continue
                    event = {"event_key": hashlib.sha256(f"{session['id']}:{kind}:{start}".encode()).hexdigest(),
                             "event_type": kind, "category": "monitoring_quality", "start_ms": start,
                             "end_ms": end, "confidence": None, "closed": True,
                             "details": {"offset_origin": "exam_session", "reason": "No analysed media covers this interval",
                                         "decision": "requires_human_review"}}
                    save_events(cursor, session["id"], None, [event])
        connection.commit()


def report(assessment_id, invitation_id, employer_id, after_id=0, limit=200):
    with database() as connection:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """SELECT s.id, s.started_at, s.submitted_at, s.disqualified_at, s.interrupted_at, s.deadline_at,
                      m.policy_json, m.capabilities_json
               FROM assessments a JOIN assessment_invitations i ON i.assessment_id=a.id
               JOIN assessment_exam_sessions s ON s.invitation_id=i.id
               LEFT JOIN assessment_monitoring_runs m ON m.session_id=s.id
               WHERE a.id=%s AND i.id=%s AND a.employer_user_id=%s""",
            (assessment_id, invitation_id, employer_id),
        )
        session = cursor.fetchone()
        if not session:
            raise HTTPException(status_code=404, detail="Exam monitoring not found")
        if not session["policy_json"]:
            return {"status": "not_monitored", "events": [], "automatic_disqualification": False}
        cursor.execute("SELECT part_id,status,updated_at,error_code,model_version FROM assessment_monitoring_jobs WHERE session_id=%s ORDER BY part_id", (session["id"],))
        jobs = cursor.fetchall()
        cursor.execute(
            """SELECT x.stream,COUNT(*) AS sample_count,SUM(x.end_ms-x.start_ms) AS analysed_ms,
                      MAX(x.end_ms) AS last_media_ms,
                      SUM(CASE WHEN JSON_EXTRACT(x.metrics_json,'$.identity_checked')=true THEN x.end_ms-x.start_ms ELSE 0 END) AS identity_checked_ms,
                      SUM(CASE WHEN JSON_EXTRACT(x.metrics_json,'$.face_usable')=true THEN x.end_ms-x.start_ms ELSE 0 END) AS usable_face_ms
               FROM assessment_monitoring_samples x JOIN assessment_monitoring_jobs j ON j.part_id=x.part_id
               WHERE j.session_id=%s GROUP BY x.stream""", (session["id"],))
        coverage = cursor.fetchall()
        cursor.execute(
            """SELECT id,event_key,part_id,event_type,category,start_ms,end_ms,confidence,closed,details_json
               FROM assessment_monitoring_events WHERE session_id=%s AND id>%s ORDER BY id LIMIT %s""",
            (session["id"], after_id, min(500, max(1, limit))),
        )
        events = cursor.fetchall()
        cursor.execute(
            """SELECT p.id,p.started_at,p.finalized_at,p.status FROM assessment_recording_parts p
               JOIN assessment_recordings r ON r.id=p.recording_id WHERE r.session_id=%s ORDER BY p.id""",
            (session["id"],),
        )
        parts = cursor.fetchall()
        cursor.execute("SELECT heartbeat_at,model_version FROM monitoring_worker_health WHERE id=1")
        worker = cursor.fetchone()
    for event in events:
        event["details"] = unpack(event.pop("details_json"))
        event["evidence"] = ({"part_id": event["part_id"], "from_ms": max(0, event["start_ms"] - 5000), "to_ms": event["end_ms"] + 5000}
                             if event["part_id"] else None)
    for part in parts:
        for key in ("started_at", "finalized_at"):
            part[key] = part[key].isoformat() + "Z" if part[key] else None
    for job in jobs:
        job["updated_at"] = job["updated_at"].isoformat() + "Z"
    ended = session["submitted_at"] or session["disqualified_at"] or session.get("interrupted_at")
    duration_ms = max(0, int(((ended or min(now(), session["deadline_at"])) - session["started_at"]).total_seconds() * 1000))
    status = "processing" if any(j["status"] in {"pending", "processing"} for j in jobs) else "analysed" if jobs and all(j["status"] == "complete" for j in jobs) else "incomplete"
    return {"status": status, "session_id": session["id"], "automatic_disqualification": False,
            "worker_online": bool(worker and worker["heartbeat_at"] > now()-timedelta(seconds=15)),
            "policy": unpack(session["policy_json"]), "capabilities": unpack(session["capabilities_json"]),
            "expected_duration_ms": duration_ms, "coverage": coverage, "jobs": jobs, "parts": parts,
            "events": events, "next_after_id": events[-1]["id"] if events else after_id,
            "timestamps": "Event offsets are relative to recording part start, except coverage gaps which use exam session start. Wall-clock alignment is approximate; part timestamps are UTC."}
