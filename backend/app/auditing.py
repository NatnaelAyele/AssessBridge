"""Read-only system-wide auditor access and versioned audit completions."""

import hashlib
import json
from typing import Literal

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .db import database
from .connectivity import evidence_pending
from .identity import _identity_key
from .monitoring.repository import report as monitoring_report
from .recordings import employer_recording
from .reviews import candidate_cards, iso, now, session_report, _audit, _version


class AuditCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: str = Field(pattern=r"^[0-9a-f]{64}$")


def assessment_for_auditor(cursor, assessment_id, lock=False):
    """Every auditor operation requires the assessment window to have closed."""
    cursor.execute("""SELECT a.id,a.employer_user_id,a.questions_json,a.ends_at,
                      a.ends_at<=UTC_TIMESTAMP() AS closed,
                      EXISTS(SELECT 1 FROM assessment_result_publications p
                             WHERE p.assessment_id=a.id AND p.is_legacy=FALSE) AS published
                      FROM assessments a WHERE a.id=%s""" + (" FOR UPDATE" if lock else ""), (assessment_id,))
    row = cursor.fetchone()
    if not row or not row["closed"]:
        raise HTTPException(404, "Closed assessment not found")
    return row


def owner(assessment_id):
    with database() as connection:
        cursor = connection.cursor(dictionary=True)
        row = assessment_for_auditor(cursor, assessment_id)
    return row["employer_user_id"]


def _session(cursor, assessment_id, invitation_id, lock=False):
    assessment_for_auditor(cursor, assessment_id, lock=lock)
    cursor.execute("""SELECT s.id,s.submitted_at,s.disqualified_at,s.interrupted_at FROM assessment_exam_sessions s
                      JOIN assessment_invitations i ON i.id=s.invitation_id
                      WHERE i.assessment_id=%s AND i.id=%s""" + (" FOR UPDATE" if lock else ""),
                   (assessment_id, invitation_id))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(404, "Candidate session not found")
    return row


def _evidence_version(cursor, session_id):
    revision = _version(cursor, session_id)
    snapshot = {"review_revision": revision}
    queries = {
        "connectivity": "SELECT event_type,occurred_at,details_json FROM assessment_connectivity_events WHERE session_id=%s ORDER BY occurred_at,id",
        "connectivity_findings": "SELECT incident_event_id,revision,finding,reason,reviewer_user_id,created_at FROM assessment_connectivity_findings WHERE session_id=%s ORDER BY revision",
        "session": "SELECT submitted_at,disqualified_at,interrupted_at,interruption_reason,disqualification_reason,current_index FROM assessment_exam_sessions WHERE id=%s",
        "events": "SELECT event_key,event_type,part_id,start_ms,end_ms,confidence,closed,updated_at FROM assessment_monitoring_events WHERE session_id=%s ORDER BY id",
        "jobs": "SELECT part_id,status,updated_at,error_code,model_version FROM assessment_monitoring_jobs WHERE session_id=%s ORDER BY part_id",
        "coverage": "SELECT x.stream,COUNT(*) AS total,SUM(x.end_ms-x.start_ms) AS duration FROM assessment_monitoring_samples x JOIN assessment_monitoring_jobs j ON j.part_id=x.part_id WHERE j.session_id=%s GROUP BY x.stream ORDER BY x.stream",
        "media": "SELECT r.status,r.expires_at>UTC_TIMESTAMP() AS retained,p.id,p.status AS part_status,p.total_bytes,p.started_at,p.finalized_at FROM assessment_recordings r LEFT JOIN assessment_recording_parts p ON p.recording_id=r.id WHERE r.session_id=%s ORDER BY p.id",
        "identity": "SELECT ic.status,ic.name_similarity,ic.face_similarity,ic.checked_at,ic.evidence_expires_at>UTC_TIMESTAMP() AS retained,ic.card_ciphertext IS NOT NULL AS card,ic.selfie_ciphertext IS NOT NULL AS selfie FROM assessment_identity_checks ic JOIN assessment_exam_sessions s ON s.invitation_id=ic.invitation_id WHERE s.id=%s",
        "reference": "SELECT reference_ciphertext IS NOT NULL AND reference_expires_at>UTC_TIMESTAMP() AS available FROM assessment_monitoring_runs WHERE session_id=%s",
    }
    for name, query in queries.items():
        cursor.execute(query, (session_id,))
        snapshot[name] = cursor.fetchall()
    version = hashlib.sha256(json.dumps(snapshot, sort_keys=True, default=str).encode()).hexdigest()
    return version, snapshot


def _status(cursor, session_id):
    version, snapshot = _evidence_version(cursor, session_id)
    cursor.execute("""SELECT id,review_revision,evidence_version,auditor_user_id,auditor_username,created_at
                      FROM assessment_audit_completions WHERE session_id=%s ORDER BY id DESC""", (session_id,))
    history = cursor.fetchall()
    for row in history:
        row["created_at"] = iso(row["created_at"])
    matching = next((row for row in history if row["evidence_version"] == version), None)
    cursor.execute("""SELECT p.id FROM assessment_result_publications p
                      JOIN assessment_invitations i ON i.assessment_id=p.assessment_id
                      JOIN assessment_exam_sessions s ON s.invitation_id=i.id
                      WHERE s.id=%s AND p.is_legacy=FALSE LIMIT 1""", (session_id,))
    if cursor.fetchone() and history:
        matching = history[0]
    return {"version": version, "current": matching, "history": history,
            "status": "audited" if matching else "changed" if history else "pending"}, snapshot


def assessments(page=1, search="", audit="all"):
    where, params = ["a.ends_at<=UTC_TIMESTAMP()"], []
    if search:
        where.append("(LOCATE(LOWER(%s),LOWER(a.title))>0 OR LOCATE(LOWER(%s),LOWER(COALESCE(a.internal_reference,'')))>0)")
        params.extend([search, search])
    with database() as connection:
        cursor = connection.cursor(dictionary=True)
        cursor.execute("""SELECT a.id,a.title,a.internal_reference,a.starts_at,a.ends_at,COUNT(i.id) AS candidate_count
                          FROM assessments a LEFT JOIN assessment_invitations i ON i.assessment_id=a.id"""
                       + (" WHERE " + " AND ".join(where) if where else "")
                       + " GROUP BY a.id ORDER BY a.created_at DESC,a.id DESC", tuple(params))
        items = cursor.fetchall()
        matching = []
        for item in items:
            cursor.execute("""SELECT s.id, EXISTS(SELECT 1 FROM assessment_review_decisions d
                                  WHERE d.session_id=s.id) AS reviewed
                              FROM assessment_exam_sessions s JOIN assessment_invitations i ON i.id=s.invitation_id
                              WHERE i.assessment_id=%s""", (item["id"],))
            sessions = cursor.fetchall()
            item["reviewed_count"] = sum(bool(row["reviewed"]) for row in sessions)
            item["audited_count"] = sum(_status(cursor, row["id"])[0]["status"] == "audited" for row in sessions)
            fully = item["candidate_count"] > 0 and item["audited_count"] == item["candidate_count"]
            if audit == "audited" and not fully or audit == "pending" and fully:
                continue
            item["starts_at"], item["ends_at"] = iso(item["starts_at"]), iso(item["ends_at"])
            matching.append(item)
    return {"items": matching[(page-1)*20:page*20], "total": len(matching), "page": page, "page_size": 20}


def candidates(assessment_id):
    result = candidate_cards(assessment_id, owner(assessment_id))
    with database() as connection:
        cursor = connection.cursor(dictionary=True)
        for item in result["candidates"]:
            if item["has_session"]:
                session = _session(cursor, assessment_id, item["invitation_id"])
                item["audit"], _ = _status(cursor, session["id"])
            else:
                item["audit"] = {"status": "not_ready", "current": None, "history": []}
    from .publications import publication_state
    result["publication"] = publication_state(assessment_id)
    return result


def case_report(assessment_id, invitation_id, auditor_id, source_ip):
    owner_id = owner(assessment_id)
    with database() as connection:
        cursor = connection.cursor(dictionary=True)
        session = _session(cursor, assessment_id, invitation_id)
        initial, _ = _status(cursor, session["id"])
    detail = session_report(assessment_id, invitation_id, owner_id, source_ip, actor_id=auditor_id)
    monitoring = monitoring_report(assessment_id, invitation_id, owner_id, limit=500)
    while len(monitoring["events"]) and len(monitoring["events"]) % 500 == 0:
        more = monitoring_report(assessment_id, invitation_id, owner_id, monitoring["events"][-1]["id"], 500)
        monitoring["events"].extend(more["events"])
        if len(more["events"]) < 500:
            break
    try:
        media = employer_recording(assessment_id, invitation_id, owner_id, source_ip, actor_id=auditor_id)
        media = {key: media[key] for key in ("status", "parts")}
    except HTTPException as error:
        if error.status_code != 404:
            raise
        media = {"status": "unavailable", "parts": []}
    with database() as connection:
        cursor = connection.cursor(dictionary=True)
        cursor.execute("""SELECT status,name_similarity,face_similarity,
                          (card_ciphertext IS NOT NULL AND evidence_expires_at>UTC_TIMESTAMP()) AS card_available,
                          (selfie_ciphertext IS NOT NULL AND evidence_expires_at>UTC_TIMESTAMP()) AS selfie_available
                          FROM assessment_identity_checks WHERE invitation_id=%s""", (invitation_id,))
        identity = cursor.fetchone()
        reviewer = None
        if detail["decision"]:
            cursor.execute("SELECT id,username,email,role FROM users WHERE id=%s", (detail["decision"]["reviewer_id"],))
            reviewer = cursor.fetchone()
        audit, _ = _status(cursor, session["id"])
    if initial["version"] != audit["version"]:
        raise HTTPException(409, "The report changed while loading. Reload the report.")
    processing = any(job["status"] in {"pending", "processing"} for job in monitoring.get("jobs", []))
    audit["can_mark"] = bool(not detail["published"] and detail["can_decide"] and detail["decision"] and not processing)
    audit["blocked_reason"] = ("Results published" if detail["published"] else "Awaiting reviewer decision" if not detail["decision"] else (detail.get("decision_blocked_reason") or "Exam still in progress") if not detail["can_decide"] else "Monitoring still processing" if processing else None)
    return {"review": detail, "reviewer": reviewer, "identity": identity, "monitoring": monitoring, "recording": media, "audit": audit}


def complete(assessment_id, invitation_id, auditor_id, expected_version, source_ip):
    with database() as connection:
        cursor = connection.cursor(dictionary=True)
        session = _session(cursor, assessment_id, invitation_id, lock=True)
        status, snapshot = _status(cursor, session["id"])
        cursor.execute("SELECT id FROM assessment_result_publications WHERE assessment_id=%s AND is_legacy=FALSE LIMIT 1", (assessment_id,))
        if cursor.fetchone():
            if status["current"]:
                return {"audit": status, "duplicate": True}
            raise HTTPException(409, "Results are published. Audits cannot be changed.")
        if status["version"] != expected_version:
            raise HTTPException(409, "The report changed. Reload it before marking it audited.")
        if status["current"]:
            return {"audit": status, "duplicate": True}
        cursor.execute("SELECT id FROM assessment_review_decisions WHERE session_id=%s LIMIT 1", (session["id"],))
        decision = cursor.fetchone()
        if not (session["submitted_at"] or session["disqualified_at"] or session.get("interrupted_at")) or not decision:
            raise HTTPException(409, "A completed exam and reviewer decision are required before auditing.")
        pending_evidence = evidence_pending(cursor, session)
        if pending_evidence:
            raise HTTPException(409, pending_evidence)
        if any(job["status"] in {"pending", "processing"} for job in snapshot["jobs"]):
            raise HTTPException(409, "Wait for monitoring to finish before marking the report audited.")
        cursor.execute("SELECT username FROM users WHERE id=%s AND role='auditor' AND is_active=TRUE", (auditor_id,))
        auditor = cursor.fetchone()
        if not auditor:
            raise HTTPException(403, "Access denied")
        cursor.execute("""INSERT INTO assessment_audit_completions
                          (session_id,review_revision,evidence_version,auditor_user_id,auditor_username,snapshot_json,created_at)
                          VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                       (session["id"], snapshot["review_revision"], expected_version, auditor_id, auditor["username"],
                        json.dumps(snapshot, default=str), now()))
        _audit(cursor, session["id"], auditor_id, "mark_audited", source_ip)
        status, _ = _status(cursor, session["id"])
        connection.commit()
    return {"audit": status, "duplicate": False}


def identity_image(assessment_id, invitation_id, kind: Literal["card", "selfie"], auditor_id, source_ip):
    with database() as connection:
        cursor = connection.cursor(dictionary=True)
        session = _session(cursor, assessment_id, invitation_id)
        cursor.execute("""SELECT card_ciphertext,selfie_ciphertext FROM assessment_identity_checks
                          WHERE invitation_id=%s AND status='passed' AND evidence_expires_at>UTC_TIMESTAMP()""", (invitation_id,))
        row = cursor.fetchone()
        blob = row[f"{kind}_ciphertext"] if row else None
        if kind == "selfie" and not blob:
            cursor.execute("SELECT reference_ciphertext FROM assessment_monitoring_runs WHERE session_id=%s AND reference_expires_at>UTC_TIMESTAMP()", (session["id"],))
            reference = cursor.fetchone()
            blob = reference["reference_ciphertext"] if reference else None
        if not blob:
            raise HTTPException(404, "Verification image unavailable or expired")
        blob = bytes(blob)
        raw = AESGCM(_identity_key()).decrypt(blob[:12], blob[12:], f"identity:{invitation_id}:{kind}".encode())
        _audit(cursor, session["id"], auditor_id, f"audit_identity_{kind}", source_ip)
        connection.commit()
    return raw
