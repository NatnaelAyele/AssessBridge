"""Bounded dependency checks and opt-in admission limits for small deployments."""
import os
import shutil
import tempfile
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone

import mysql.connector
from fastapi import HTTPException

from .config import DB_CONFIG, RECORDINGS_DIR

_lock = threading.Lock()
_cached = (0, {})


def limits():
    return {
        'sessions': max(0, int(os.getenv('PILOT_MAX_SESSIONS', '0'))),
        'duration_minutes': max(1, int(os.getenv('PILOT_MAX_DURATION_MINUTES', '30'))),
        'reserve_bytes': max(0, int(os.getenv('PILOT_STORAGE_RESERVE_BYTES', str(4 * 1024**3)))),
        'bytes_per_minute': max(1, int(os.getenv('PILOT_BYTES_PER_MINUTE', str(16 * 1024**2)))),
    }


def dependency_status():
    """Private diagnostics; never return component details through a public route."""
    global _cached
    with _lock:
        if time.monotonic() - _cached[0] < 10:
            return dict(_cached[1])
        checks = {'database': False, 'worker': False, 'storage': False, 'identity': False}
        detector = os.getenv('MONITORING_EXTENDED_URL', '').strip()
        if detector:
            checks['detector'] = False
            try:
                url = detector.rsplit('/', 1)[0] + '/health'
                if url.startswith('https://'):
                    with urllib.request.urlopen(url, timeout=2) as response:
                        checks['detector'] = response.status == 200
                        response.read(128)
            except Exception:
                pass
        try:
            opts = dict(DB_CONFIG, connection_timeout=2, read_timeout=2, write_timeout=2)
            db = mysql.connector.connect(**opts, time_zone='+00:00')
            try:
                c = db.cursor()
                c.execute('SELECT heartbeat_at FROM monitoring_worker_health WHERE id=1')
                row = c.fetchone()
                checks['database'] = True
                checks['worker'] = bool(row and row[0] > datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=20))
            finally:
                db.close()
        except Exception:
            pass
        try:
            with tempfile.TemporaryFile(dir=RECORDINGS_DIR) as stream:
                stream.write(b'readiness'); stream.flush()
            checks['storage'] = shutil.disk_usage(RECORDINGS_DIR).free > limits()['reserve_bytes']
        except OSError:
            pass
        try:
            issuer = os.getenv('ESIGNET_ISSUER', '').rstrip('/')
            if issuer.startswith('https://'):
                with urllib.request.urlopen(issuer+'/.well-known/openid-configuration', timeout=2) as response:
                    checks['identity'] = response.status == 200
                    response.read(128)
        except Exception:
            pass
        _cached = (time.monotonic(), checks)
        return dict(checks)


def acquire_admission(cursor):
    if not limits()['sessions']:
        return
    # The lock is held until the start transaction commits and its connection
    # closes. Concurrent starts cannot both consume the final slot.
    cursor.execute("SELECT GET_LOCK('assessbridge_exam_admission', 0) AS acquired")
    if cursor.fetchone()['acquired'] != 1:
        raise HTTPException(503, 'Exam start is busy. Please try again shortly.')


def require_supported_duration(duration_minutes):
    policy = limits()
    if policy['sessions'] and duration_minutes > policy['duration_minutes']:
        raise HTTPException(422, f"Set an exam duration of {policy['duration_minutes']} minutes or less.")


def require_capacity(cursor, duration_minutes):
    policy = limits()
    if not policy['sessions']:
        return
    if duration_minutes > policy['duration_minutes']:
        raise HTTPException(503, 'This exam exceeds the current exam duration limit. Contact the organizer.')
    if not all(dependency_status().values()):
        raise HTTPException(503, 'Exams cannot start right now. Please try again shortly.')
    # Ended attempts still reserve a slot while uploads or model analysis remain
    # unfinished. Existing answering, recovery and upload routes are unaffected.
    cursor.execute("""SELECT COUNT(*) AS occupied FROM assessment_exam_sessions s
        WHERE (s.submitted_at IS NULL AND s.disqualified_at IS NULL AND s.interrupted_at IS NULL)
        OR EXISTS (SELECT 1 FROM assessment_recordings r WHERE r.session_id=s.id AND r.status='recording')
        OR EXISTS (SELECT 1 FROM assessment_monitoring_jobs j WHERE j.session_id=s.id AND
                   (j.status IN ('pending','processing') OR (j.status='error' AND j.attempts<3)))""")
    occupied = cursor.fetchone()['occupied']
    if occupied >= policy['sessions']:
        raise HTTPException(503, 'All exam spaces are in use. Please try again shortly.')
    needed = policy['reserve_bytes'] + (occupied + 1) * (policy['duration_minutes'] + 1) * policy['bytes_per_minute']
    if shutil.disk_usage(RECORDINGS_DIR).free < needed:
        raise HTTPException(503, 'Exams cannot start right now. Please try again shortly.')


def require_upload_budget(cursor, recording_id, incoming_bytes):
    policy = limits()
    if not policy['sessions']:
        return
    # Called with the recording row already locked; the sum covers all recovery
    # parts. Duplicate retries are checked before this guard and cost no quota.
    cursor.execute("""SELECT a.duration_minutes,
        COALESCE((SELECT SUM(p.total_bytes) FROM assessment_recording_parts p WHERE p.recording_id=r.id),0) AS bytes
        FROM assessment_recordings r JOIN assessment_exam_sessions s ON s.id=r.session_id
        JOIN assessment_invitations i ON i.id=s.invitation_id JOIN assessments a ON a.id=i.assessment_id
        WHERE r.id=%s""", (recording_id,))
    row = cursor.fetchone()
    if row and int(row['bytes']) + incoming_bytes > (row['duration_minutes'] + 1) * policy['bytes_per_minute']:
        raise HTTPException(413, 'The recording upload limit has been reached. Contact the exam organizer.')


def private_snapshot():
    """For the root-owned host monitor, not an HTTP diagnostics endpoint."""
    from .db import database
    result = {'dependencies': dependency_status(), 'limits': limits()}
    with database() as db:
        c = db.cursor(dictionary=True)
        for label, query in {
            'jobs': 'SELECT status,COUNT(*) count,MAX(TIMESTAMPDIFF(SECOND,updated_at,UTC_TIMESTAMP())) oldest_seconds FROM assessment_monitoring_jobs GROUP BY status',
            'recordings': 'SELECT status,COUNT(*) count FROM assessment_recordings GROUP BY status',
            'recording_bytes': 'SELECT COALESCE(SUM(total_bytes),0) bytes FROM assessment_recording_parts',
            'failed_invitations': "SELECT COUNT(*) count FROM assessment_invitations WHERE delivery_status='failed'",
        }.items():
            c.execute(query); result[label] = c.fetchall()
    return result
