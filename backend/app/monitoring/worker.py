"""Durable local worker: decrypt ordered chunks into streaming FFmpeg decoders.

No plaintext media files are created. Each part is decoded from its beginning
once per run; after a crash it is replayed with idempotent sample/event keys.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import timedelta
import hashlib
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

import cv2
import numpy as np
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..config import ROOT_DIR
from ..db import database
from ..identity import _identity_key
from ..recordings import _aad, _chunk_path, encryption_key, FINAL_UPLOAD_SECONDS
from .engine import MODEL_VERSION, Speech, Vision, session
from .repository import diagnostic, now, record_sample, reconcile_gaps, save_events, unpack
from .rules import Events

LOG = logging.getLogger(__name__)


@contextmanager
def scheduler_pool(stop, concurrency):
    pool = ThreadPoolExecutor(max_workers=concurrency)
    try:
        yield pool
    finally:
        # A scheduler/DB failure must stop its decoders before joining them;
        # otherwise an active recording could hold shutdown until the exam ends.
        stop.set()
        pool.shutdown(wait=True, cancel_futures=True)


def ffmpeg():
    configured = os.getenv("FFMPEG_CMD", "")
    executable = configured or shutil.which("ffmpeg") or str(ROOT_DIR / "var" / "ffmpeg" / "ffmpeg.exe")
    if not Path(executable).is_file():
        raise RuntimeError("FFmpeg is unavailable; set FFMPEG_CMD")
    return executable


def load_job(part_id):
    with database() as connection:
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """SELECT j.*,p.mime_type,p.status AS part_status,p.started_at AS part_started_at,
                      r.storage_token,r.status AS recording_status,s.invitation_id,s.started_at,
                      s.submitted_at,s.disqualified_at,s.interrupted_at,s.deadline_at,m.policy_json,m.reference_ciphertext,
                      m.reference_expires_at
               FROM assessment_monitoring_jobs j JOIN assessment_monitoring_runs m ON m.session_id=j.session_id
               JOIN assessment_recording_parts p ON p.id=j.part_id
               JOIN assessment_recordings r ON r.id=p.recording_id
               JOIN assessment_exam_sessions s ON s.id=j.session_id WHERE j.part_id=%s""", (part_id,))
        return cursor.fetchone()


def feed(proc, job, stop, errors, source_done):
    sequence = 0
    cipher = AESGCM(encryption_key())
    try:
        while not stop.is_set():
            with database() as connection:
                cursor = connection.cursor(dictionary=True)
                cursor.execute("SELECT * FROM assessment_recording_chunks WHERE part_id=%s AND sequence_number=%s", (job["part_id"], sequence))
                chunk = cursor.fetchone()
            if chunk:
                encrypted = _chunk_path(job["storage_token"], job["part_id"], sequence).read_bytes()
                payload = cipher.decrypt(encrypted[:12], encrypted[12:], _aad(job["part_id"], sequence))
                if len(payload) != chunk["size_bytes"] or hashlib.sha256(payload).hexdigest() != chunk["sha256"]:
                    raise RuntimeError("media_integrity_failure")
                proc.stdin.write(payload)
                proc.stdin.flush()
                sequence += 1
                continue
            current = load_job(job["part_id"])
            if not current or current["recording_status"] == "deleted":
                raise RuntimeError("evidence_unavailable")
            ended = current["submitted_at"] or current["disqualified_at"] or current.get("interrupted_at") or current["deadline_at"]
            if current["part_status"] != "recording" or now() > ended + timedelta(seconds=FINAL_UPLOAD_SECONDS):
                break
            stop.wait(.5)
    except Exception as exc:
        errors.append(exc)
    finally:
        source_done.set()
        try:
            proc.stdin.close()
        except OSError:
            pass


def read_exact(file, count):
    chunks, remaining = [], count
    while remaining:
        chunk = file.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def analyse_stream(job, stream, stop, reference):
    policy = unpack(job["policy_json"])
    analyser = Vision(reference, policy) if stream == "video" else Speech()
    if stream == "video" and analyser.reference_feature is None:
        diagnostic(job["session_id"], job["part_id"], "identity_reference_unavailable",
                   {"identity_continuity": "not_checked", "reason": "No usable verified reference"})
    decoder_format = "matroska" if job["mime_type"].startswith("video/webm") else "mov"
    command = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-nostdin", "-threads", "1",
               "-protocol_whitelist", "pipe", "-probesize", "1000000", "-analyzeduration", "1000000",
               "-f", decoder_format, "-i", "pipe:0"]
    if stream == "video":
        command += ["-map", "0:v:0", "-an", "-vf", "fps=2,scale=960:540:force_original_aspect_ratio=decrease,pad=960:540:(ow-iw)/2:(oh-ih)/2",
                    "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1"]
        unit_bytes = 960 * 540 * 3
    else:
        command += ["-map", "0:a:0", "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "pipe:1"]
        unit_bytes = 16384 * 4
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    events = Events(job["part_id"], stream, policy)
    errors, stderr = [], bytearray()
    done, source_done = threading.Event(), threading.Event()
    proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags)

    def watch():
        drained_at = None
        while not done.wait(.5):
            if source_done.is_set() and drained_at is None:
                drained_at = time.monotonic()
            if stop.is_set() or (drained_at and time.monotonic() - drained_at > 90):
                proc.kill()
                return

    def drain_errors():
        while True:
            data = proc.stderr.read(1024)
            if not data:
                return
            if len(stderr) < 8192:
                stderr.extend(data)

    feeder = threading.Thread(target=feed, args=(proc, job, stop, errors, source_done), daemon=True)
    watcher = threading.Thread(target=watch, daemon=True)
    error_reader = threading.Thread(target=drain_errors, daemon=True)
    count = 0
    position_ms = 0
    try:
        feeder.start(); watcher.start(); error_reader.start()
        while not stop.is_set():
            data = read_exact(proc.stdout, unit_bytes)
            if not data:
                break
            if stream == "video":
                if len(data) != unit_bytes:
                    raise RuntimeError("incomplete_decoded_video_frame")
                metrics = analyser.analyse(np.frombuffer(data, np.uint8).reshape(540, 960, 3))
                duration_ms = 500
            else:
                pcm = np.frombuffer(data[:len(data)//4*4], dtype="<f4")
                if not len(pcm):
                    break
                metrics = analyser.analyse(pcm)
                duration_ms = round(len(pcm) / 16)
            end_ms = position_ms + duration_ms
            record_sample(job["session_id"], job["part_id"], stream, position_ms, end_ms, metrics,
                          events.push(position_ms, end_ms, metrics["signals"]))
            position_ms = end_ms
            count += 1
        if stop.is_set():
            raise InterruptedError("worker_stopping")
        proc.wait(timeout=10)
        feeder.join(timeout=3)
        if errors:
            raise errors[0]
        if proc.returncode or not count:
            raise RuntimeError(stream + "_decode_unavailable")
        with database() as connection:
            cursor = connection.cursor()
            save_events(cursor, job["session_id"], job["part_id"], events.finish())
            connection.commit()
        return count
    finally:
        done.set()
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)
        feeder.join(timeout=3)
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            try:
                pipe.close()
            except OSError:
                pass


def process_job(part_id, stop):
    # Also protect individual parts from operator-triggered replay while a
    # scheduler is processing them. Connection loss releases the lock.
    with database() as lock_connection:
        cursor = lock_connection.cursor()
        cursor.execute("SELECT GET_LOCK(%s,0)", (f"ab_monitor_part:{part_id}",))
        if cursor.fetchone()[0] != 1:
            return
        _process_job(part_id, stop)


def _process_job(part_id, stop):
    job = load_job(part_id)
    if not job:
        return
    with database() as connection:
        cursor = connection.cursor()
        cursor.execute("UPDATE assessment_monitoring_jobs SET status='processing',attempts=attempts+1,updated_at=%s,error_code=NULL,model_version=%s WHERE part_id=%s", (now(), MODEL_VERSION, part_id))
        connection.commit()
    reference = None
    if job["reference_ciphertext"] and job["reference_expires_at"] > now():
        try:
            blob = bytes(job["reference_ciphertext"])
            raw = AESGCM(_identity_key()).decrypt(blob[:12], blob[12:], f"identity:{job['invitation_id']}:selfie".encode())
            reference = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
        except Exception:
            LOG.warning("Monitoring reference unavailable for session %s", job["session_id"])
    failures = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {kind: pool.submit(analyse_stream, job, kind, stop, reference) for kind in ("video", "audio")}
        for kind, future in futures.items():
            try:
                future.result()
            except InterruptedError:
                failures.append("worker_stopping")
            except Exception as exc:
                LOG.exception("Monitoring %s failed for part %s", kind, part_id)
                failures.append(kind + "_analysis_failed")
                diagnostic(job["session_id"], part_id, kind + "_analysis_unavailable",
                           {"reason": type(exc).__name__, "coverage": "incomplete"})
    state = "pending" if stop.is_set() else "error" if failures else "complete"
    with database() as connection:
        cursor = connection.cursor()
        cursor.execute("UPDATE assessment_monitoring_jobs SET status=%s,error_code=%s,updated_at=%s WHERE part_id=%s",
                       (state, failures[0] if failures else None, now(), part_id))
        connection.commit()


def run(stop):
    # One scheduler per database, even with multiple API workers/reload processes.
    with database() as lock_connection:
        cursor = lock_connection.cursor()
        cursor.execute("SELECT GET_LOCK('assessbridge_monitoring_worker',0)")
        if cursor.fetchone()[0] != 1:
            return
        ffmpeg()
        session("yolox_s.onnx"); session("silero_vad.onnx")
        LOG.info("Monitoring worker ready: %s", MODEL_VERSION)
        concurrency = max(1, min(8, int(os.getenv("MONITORING_CONCURRENCY", "2"))))
        with scheduler_pool(stop, concurrency) as pool:
            active = {}
            last_reconcile = 0
            while not stop.is_set():
                cursor.execute("SELECT 1")
                cursor.fetchone()
                for part_id in list(active):
                    if active[part_id].done():
                        try:
                            active.pop(part_id).result()
                        except Exception:
                            LOG.exception("Monitoring job failed")
                with database() as connection:
                    cur = connection.cursor(dictionary=True)
                    cur.execute("INSERT INTO monitoring_worker_health (id,heartbeat_at,model_version) VALUES (1,%s,%s) ON DUPLICATE KEY UPDATE heartbeat_at=VALUES(heartbeat_at),model_version=VALUES(model_version)", (now(), MODEL_VERSION))
                    connection.commit()
                    cur.execute(
                        """SELECT j.part_id FROM assessment_monitoring_jobs j
                           JOIN assessment_recording_parts p ON p.id=j.part_id
                           JOIN assessment_recordings r ON r.id=p.recording_id
                           WHERE p.total_bytes>0 AND r.status<>'deleted'
                             AND (j.status IN ('pending','processing') OR
                                  (j.status='error' AND j.attempts<3 AND j.updated_at<%s))
                           ORDER BY j.part_id LIMIT 100""", (now()-timedelta(seconds=30),))
                    pending = cur.fetchall()
                for item in pending:
                    part_id = item["part_id"]
                    if part_id not in active and len(active) < concurrency:
                        active[part_id] = pool.submit(process_job, part_id, stop)
                if time.monotonic()-last_reconcile > 30:
                    reconcile_gaps()
                    last_reconcile = time.monotonic()
                stop.wait(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--managed", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    stop = threading.Event()
    if args.managed:
        def read_shutdown():
            sys.stdin.readline()
            stop.set()
        threading.Thread(target=read_shutdown, daemon=True).start()
    try:
        run(stop)
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    main()
