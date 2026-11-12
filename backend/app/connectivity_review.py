"""Reviewable interruption incidents assembled from server-side session events."""

import json
from datetime import datetime, timezone


def _unpack(value):
    return json.loads(value) if isinstance(value, (str, bytes)) else value or {}


def _iso(value):
    return value.isoformat() + "Z" if value else None


def load_incidents(cursor, session, coverage_ready=False):
    cursor.execute(
        "SELECT id,event_type,occurred_at,details_json FROM assessment_connectivity_events "
        "WHERE session_id=%s ORDER BY occurred_at,id", (session["id"],)
    )
    events = [{"id": row["id"], "type": row["event_type"], "at": row["occurred_at"],
               "details": _unpack(row["details_json"])} for row in cursor.fetchall()]
    incidents = []
    active = None
    linked_exits = set()
    for item in events:
        kind = item["type"]
        if kind == "interruption_started":
            # Legacy or overlapping events may lack a matching completion.
            active = {"id": item["id"], "started_at": item["at"], "ended_at": None,
                      "trigger": item["details"].get("reason") or "unknown",
                      "outcome": "awaiting_recovery", "face_checks": [], "platform_restored": False}
            incidents.append(active)
        elif active and kind == "recovery_identity_check":
            active["face_checks"].append({"at": _iso(item["at"]),
                                           "passed": bool(item["details"].get("passed")),
                                           "status": item["details"].get("status") or ("passed" if item["details"].get("passed") else "not_matched"),
                                           "similarity": item["details"].get("similarity")})
        elif active and kind == "platform_restored":
            active["platform_restored"] = True
        elif active and kind in {"connection_restored", "attempt_interrupted", "deadline_reached", "confirmed_exit"}:
            active["ended_at"] = item["at"]
            active["outcome"] = {"connection_restored": "resumed", "attempt_interrupted": "limit_reached",
                                 "deadline_reached": "time_expired", "confirmed_exit": "confirmed_exit"}[kind]
            if kind == "confirmed_exit":
                linked_exits.add(item["id"])
            active = None
    # A confirmed exit is still visible when no recovery interval was open.
    exits = [{"at": _iso(item["at"])} for item in events if item["type"] == "confirmed_exit" and item["id"] not in linked_exits]
    for incident in incidents:
        start, end = incident["started_at"], incident["ended_at"]
        incident["duration_seconds"] = max(0, round((end-start).total_seconds())) if end else None
        incident["gaps"] = []
    cursor.execute(
        "SELECT event_type,start_ms,end_ms FROM assessment_monitoring_events "
        "WHERE session_id=%s AND event_type IN ('video_coverage_gap','audio_coverage_gap') ORDER BY start_ms,id",
        (session["id"],)
    )
    gaps = cursor.fetchall()
    if coverage_ready and not gaps and session.get("started_at"):
        # The worker persists coverage events separately. Calculate missing
        # intervals here as well so a reviewer never sees a false "no gap"
        # between finished analysis and that periodic reconciliation.
        cursor.execute(
            """SELECT p.started_at,x.stream,MIN(x.start_ms) AS first_ms,MAX(x.end_ms) AS last_ms
               FROM assessment_monitoring_samples x JOIN assessment_recording_parts p ON p.id=x.part_id
               JOIN assessment_recordings r ON r.id=p.recording_id
               WHERE r.session_id=%s GROUP BY p.id,p.started_at,x.stream ORDER BY p.started_at""",
            (session["id"],)
        )
        intervals = cursor.fetchall()
        finish = session.get("submitted_at") or session.get("disqualified_at") or session.get("interrupted_at")
        duration = max(0, int((finish-session["started_at"]).total_seconds()*1000)) if finish else 0
        for stream in ("video", "audio"):
            position = 0
            for interval in intervals:
                if interval["stream"] != stream:
                    continue
                offset = int((interval["started_at"]-session["started_at"]).total_seconds()*1000)
                start = max(0, offset + interval["first_ms"])
                end = min(duration, offset + interval["last_ms"])
                if start-position > 2500:
                    gaps.append({"event_type": stream+"_coverage_gap", "start_ms": position, "end_ms": min(start,duration)})
                position = max(position, end)
            if duration-position > 2500:
                gaps.append({"event_type": stream+"_coverage_gap", "start_ms": position, "end_ms": duration})
    for incident in incidents:
        # Heartbeat detection follows the last successful contact. Include the
        # nearby capture interval without treating a server timestamp as video time.
        start = (incident["started_at"] - session["started_at"]).total_seconds()*1000 - 15000
        end = ((incident["ended_at"] - session["started_at"]).total_seconds()*1000
               if incident["ended_at"] else None)
        if end is not None:
            end += 5000
        if end is None:
            continue
        for gap in gaps:
            overlap = min(end, gap["end_ms"]) - max(start, gap["start_ms"])
            if overlap > 0:
                incident["gaps"].append({"stream": "video" if gap["event_type"].startswith("video") else "audio",
                                          "estimated_seconds": max(1, round(overlap / 1000))})
    for incident in incidents:
        incident["started_at"], incident["ended_at"] = _iso(incident["started_at"]), _iso(incident["ended_at"])
    cursor.execute("SELECT status FROM assessment_recordings WHERE session_id=%s", (session["id"],))
    recording = cursor.fetchone()
    cursor.execute(
        """SELECT p.started_at,p.total_bytes,MAX(c.received_at) AS last_upload
           FROM assessment_recording_parts p JOIN assessment_recordings r ON r.id=p.recording_id
           LEFT JOIN assessment_recording_chunks c ON c.part_id=p.id
           WHERE r.session_id=%s GROUP BY p.id,p.started_at,p.total_bytes""", (session["id"],)
    )
    parts = cursor.fetchall()
    for incident in incidents:
        start = datetime.fromisoformat(incident["started_at"].removesuffix("Z"))
        end = datetime.fromisoformat(incident["ended_at"].removesuffix("Z")) if incident["ended_at"] else None
        incident["earlier_recording_available"] = any(p["total_bytes"] > 0 and p["started_at"] <= start for p in parts)
        incident["later_recording_part_available"] = any(p["total_bytes"] > 0 and p["started_at"] >= start for p in parts)
        incident["delayed_upload_received"] = bool(end and any(p["total_bytes"] > 0 and p["started_at"] <= start
                                                               and p["last_upload"] and p["last_upload"] > end for p in parts))
    recovery_used = float(session.get("recovery_seconds") or 0)
    if session.get("recovery_started_at"):
        recovery_end = session.get("submitted_at") or session.get("disqualified_at") or session.get("interrupted_at") or datetime.now(timezone.utc).replace(tzinfo=None)
        recovery_used += max(0, (recovery_end - session["recovery_started_at"]).total_seconds())
    return {"incidents": incidents, "confirmed_exits": exits,
            "recovery_seconds": min(600, recovery_used),
            "recording_status": recording["status"] if recording else "unavailable",
            "coverage_ready": bool(coverage_ready and recording and recording["status"] in {"complete", "incomplete"})}
