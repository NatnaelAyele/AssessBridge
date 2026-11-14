import json
import secrets
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import COOKIE_NAME, EXAM_COOKIE_NAME, SEB_BASE_URL  # noqa: E402
from app.db import database  # noqa: E402
from app.exam_sessions import submit_expired_attempts  # noqa: E402
from app.invitations import assessment_detail  # noqa: E402
from app.main import POLICY_VERSION, app  # noqa: E402
from app.security import exam_entry_fingerprint, hash_password, new_exam_access, read_session  # noqa: E402
from app.seb import config_hash  # noqa: E402


class ExamSessionIntegrationTests(unittest.TestCase):
    def setUp(self):
        suffix = secrets.token_hex(5)
        self.candidate_username = f"exam_candidate_{suffix}"
        self.employer_username = f"exam_employer_{suffix}"
        self.password = "Exam-test-password-123!"
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        questions = [
            {"type": "multiple_choice", "prompt": "Choose B", "options": ["A", "B", "", ""], "correctIndex": 1, "key": None},
            {"type": "written", "prompt": "Explain your answer", "key": "Reference answer", "options": None, "correctIndex": None},
            {"type": "multiple_choice", "prompt": "Choose A", "options": ["A", "B", "", ""], "correctIndex": 0, "key": None},
        ]
        with database() as connection:
            cursor = connection.cursor()
            cursor.execute("INSERT INTO companies(name,is_active) VALUES('Exam integration test',TRUE)")
            self.company_id=cursor.lastrowid
            cursor.execute("INSERT INTO users (username, password_hash, role) VALUES (%s, %s, 'employer_personnel')", (self.employer_username, hash_password(self.password)))
            self.employer_id = cursor.lastrowid
            cursor.execute("UPDATE users SET company_id=%s WHERE id=%s",(self.company_id,self.employer_id))
            cursor.execute("INSERT INTO users (username, email, password_hash, role) VALUES (%s, %s, %s, 'candidate')", (self.candidate_username, f"{suffix}@example.com", hash_password(self.password)))
            self.candidate_id = cursor.lastrowid
            cursor.execute(
                """INSERT INTO assessments
                   (employer_user_id, request_key, payload_hash, title, duration_minutes, questions_json, starts_at, ends_at)
                   VALUES (%s, %s, %s, 'Exam flow test', 30, %s, %s, %s)""",
                (self.employer_id, str(uuid4()), secrets.token_hex(32), json.dumps(questions), now - timedelta(minutes=1), now + timedelta(hours=2)),
            )
            self.assessment_id = cursor.lastrowid
            cursor.execute("UPDATE assessments SET company_id=%s WHERE id=%s",(self.company_id,self.assessment_id))
            self.exam_username = f"abx_{suffix}"
            cursor.execute(
                """INSERT INTO assessment_invitations
                   (assessment_id, candidate_name, candidate_email, candidate_user_id, exam_username, password_hash, delivery_status)
                   VALUES (%s, 'Test Candidate', %s, %s, %s, %s, 'sent')""",
                (self.assessment_id, f"{suffix}@example.com", self.candidate_id, self.exam_username, hash_password("Exam-entry-password-123!")),
            )
            self.invitation_id = cursor.lastrowid
            cursor.execute(
                "INSERT INTO assessment_consents (invitation_id, candidate_user_id, policy_version) VALUES (%s, %s, %s)",
                (self.invitation_id, self.candidate_id, POLICY_VERSION),
            )
            cursor.execute(
                """INSERT INTO assessment_identity_checks
                   (invitation_id, candidate_user_id, status, checked_at, evidence_expires_at)
                   VALUES (%s, %s, 'passed', UTC_TIMESTAMP(), UTC_TIMESTAMP() + INTERVAL 30 DAY)""",
                (self.invitation_id, self.candidate_id),
            )
            connection.commit()
            cursor.close()

    def tearDown(self):
        with database() as connection:
            cursor = connection.cursor()
            cursor.execute("DELETE FROM assessments WHERE id = %s", (self.assessment_id,))
            for user_id in (self.candidate_id, self.employer_id):
                cursor.execute("DELETE FROM auth_sessions WHERE user_id = %s", (user_id,))
                cursor.execute("DELETE FROM auth_audit WHERE user_id = %s", (user_id,))
                cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
            cursor.execute("DELETE FROM companies WHERE id=%s",(self.company_id,))
            connection.commit()
            cursor.close()

    def client(self):
        client = TestClient(app)
        login = client.post("/api/auth/login", json={"username": self.candidate_username, "password": self.password})
        self.assertEqual(login.status_code, 200, login.text)
        entry_token = new_exam_access(self.candidate_id, self.invitation_id, datetime.now(timezone.utc) + timedelta(hours=2), seb_verified=True)
        client.cookies.set(EXAM_COOKIE_NAME, entry_token, domain="testserver.local", path="/")
        fingerprint = exam_entry_fingerprint(read_session(client.cookies.get(COOKIE_NAME))[1], entry_token)
        with database() as connection:
            cursor = connection.cursor()
            cursor.execute("UPDATE assessment_identity_checks SET access_fingerprint = %s WHERE invitation_id = %s AND candidate_user_id = %s AND status = 'passed'",
                           (fingerprint, self.invitation_id, self.candidate_id))
            connection.commit()
            cursor.close()
        client.headers.update({"X-AssessBridge-SEB-ConfigKeyHash": config_hash(SEB_BASE_URL + "/exam-room")})
        client.headers["X-Exam-Client"] = str(uuid4())
        self.assertEqual(client.post("/api/candidate/exam/prepare").status_code, 200)
        self.assertEqual(client.post("/api/candidate/exam/network-check").status_code, 200)
        self.assertEqual(client.put("/api/candidate/exam/network-check", content=bytes(131072)).status_code, 200)
        self.confirm_readiness(client)
        return client

    def confirm_readiness(self,client):
        requirement=client.get('/api/candidate/exam/readiness').json()
        confirmation={'instructions_version':requirement['instructions_version'],'platform':'Windows','client_version':'SEB_Windows_test','checks':{key:True for key in ('secure_context','media_recorder','encrypted_storage','camera','microphone','web_audio')},'acknowledgements':{key:True for key in ('instructions','room','interruptions')}}
        self.assertEqual(client.post('/api/candidate/exam/readiness',json=confirmation).status_code,200)

    def test_sequential_navigation_autosave_and_manual_submission(self):
        with self.client() as client:
            self.assertEqual(client.get("/api/candidate/exam/session").json()["state"], "not_started")
            started = client.post("/api/candidate/exam/start")
            self.assertEqual(started.status_code, 200, started.text)
            first = started.json()
            self.assertEqual(first["state"], "active")
            self.assertEqual(first["question"]["prompt"], "Choose B")
            self.assertNotIn("correctIndex", first["question"])
            self.assertNotIn("key", first["question"])
            self.assertEqual(client.post("/api/candidate/exam/start").json()["started_at"], first["started_at"])
            self.assertEqual(client.post("/api/candidate/exam/answer", json={"question_index": 1, "answer": "too soon"}).status_code, 409)
            saved = client.post("/api/candidate/exam/answer", json={"question_index": 0, "answer": 1})
            self.assertEqual(saved.json()["answered_count"], 1)
            cleared = client.post("/api/candidate/exam/answer", json={"question_index": 0, "answer": None})
            self.assertEqual(cleared.json()["answered_count"], 0)
            self.assertEqual(client.post("/api/candidate/exam/answer", json={"question_index": 0, "answer": 1}).json()["answered_count"], 1)
            second = client.post("/api/candidate/exam/navigate", json={"direction": "next"}).json()
            self.assertEqual(second["current_index"], 1)
            self.assertEqual(second["question"]["type"], "written")
            back = client.post("/api/candidate/exam/navigate", json={"direction": "previous"}).json()
            self.assertEqual(back["current_index"], 0)
            self.assertEqual(back["answer"], 1)
            client.post("/api/candidate/exam/navigate", json={"direction": "next"})
            self.assertEqual(client.post("/api/candidate/exam/submit").status_code, 409)
            self.assertEqual(client.post("/api/candidate/exam/answer", json={"question_index": 1, "answer": "My explanation"}).json()["answered_count"], 2)
            final_question = client.post("/api/candidate/exam/navigate", json={"direction": "next"}).json()
            self.assertEqual(final_question["current_index"], 2)
            self.assertIsNone(final_question["answer"])
            submitted = client.post("/api/candidate/exam/submit")
            self.assertEqual(submitted.status_code, 200, submitted.text)
            self.assertEqual(submitted.json()["submission_reason"], "manual")
            self.assertEqual(submitted.json()["answered_count"], 2)
            self.assertEqual(client.get("/seb/exit").status_code, 200)
            history = client.get("/api/candidate/assessments").json()["assessments"]
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["status"], "submitted")
            self.assertIsNone(history[0]["multiple_choice_score"])
            self.assertIsNone(history[0]["written_grading_pending"])
            self.assertIsNone(history[0]["publication"])
            progress = assessment_detail(self.assessment_id, self.employer_id)["candidates"][0]
            self.assertEqual(progress["progress"], "submitted")
            self.assertEqual(progress["answered_count"], 2)
            self.assertEqual(client.post("/api/candidate/exam/answer", json={"question_index": 2, "answer": 0}).status_code, 409)

    def test_timeout_submits_saved_progress_without_browser(self):
        with self.client() as client:
            client.post("/api/candidate/exam/start")
            client.post("/api/candidate/exam/answer", json={"question_index": 0, "answer": 1})
            with database() as connection:
                cursor = connection.cursor()
                cursor.execute(
                    "UPDATE assessment_exam_sessions SET deadline_at = %s WHERE invitation_id = %s",
                    (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1), self.invitation_id),
                )
                connection.commit()
                cursor.close()
            self.assertEqual(submit_expired_attempts(), 1)
            state = client.get("/api/candidate/exam/session").json()
            self.assertEqual(state["state"], "submitted")
            self.assertEqual(state["submission_reason"], "timeout")
            self.assertEqual(state["answered_count"], 1)
            self.assertEqual(state["current_index"], 0)
            self.assertEqual(client.get("/seb/exit").status_code, 200)
            progress = assessment_detail(self.assessment_id, self.employer_id)["candidates"][0]
            self.assertEqual(progress["submission_reason"], "timeout")
            self.assertEqual(progress["answered_count"], 1)
            self.assertEqual(client.post("/api/candidate/exam/navigate", json={"direction": "next"}).status_code, 409)
            with database() as connection:
                cursor = connection.cursor()
                cursor.execute("UPDATE assessments SET ends_at = %s WHERE id = %s", (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=1), self.assessment_id))
                connection.commit()
                cursor.close()
            entry_proof = {"X-AssessBridge-SEB-ConfigKeyHash": config_hash(SEB_BASE_URL + "/exam-entry")}
            reentry = client.post("/api/candidate/exam-entry", json={"username": self.exam_username, "password": "Exam-entry-password-123!"}, headers=entry_proof)
            self.assertEqual(reentry.json()["window"], "submitted")
            self.assertEqual(client.get("/exam-room").status_code, 200)

    def test_every_exam_api_requires_config_proof(self):
        with self.client() as client:
            bad = {"X-AssessBridge-SEB-ConfigKeyHash": "0" * 64}
            self.assertEqual(client.get("/seb/exit").status_code, 200)
            requests = [
                client.get("/api/candidate/exam-room", headers=bad),
                client.post("/api/candidate/exam-room/consent", json={"agreed": True}, headers=bad),
                client.get("/api/candidate/exam/session", headers=bad),
                client.post("/api/candidate/exam/start", headers=bad),
                client.post("/api/candidate/exam/answer", json={"question_index": 0, "answer": 1}, headers=bad),
                client.post("/api/candidate/exam/navigate", json={"direction": "next"}, headers=bad),
                client.post("/api/candidate/exam/submit", headers=bad),
                client.post("/api/candidate/exam/leave", headers=bad),
            ]
            self.assertTrue(all(response.status_code == 403 for response in requests))
            native = {"X-AssessBridge-SEB-ConfigKeyHash": "", "X-SafeExamBrowser-ConfigKeyHash": config_hash(SEB_BASE_URL + "/api/candidate/exam/session")}
            self.assertEqual(client.get("/api/candidate/exam/session", headers=native).status_code, 200)

    def test_early_exit_disqualifies_and_blocks_reentry(self):
        with self.client() as client:
            self.assertEqual(client.get("/api/candidate/exam/session").json()["state"], "not_started")
            self.assertEqual(client.post("/api/candidate/exam/start").status_code, 200)
            self.assertEqual(client.post("/api/candidate/exam/heartbeat", json={"ready": True}).json()["state"], "active")
            self.assertEqual(client.post("/api/candidate/exam/answer", json={"question_index": 0, "answer": 1}).status_code, 200)
            report = client.post("/api/candidate/exam/leave")
            self.assertEqual(report.status_code, 200, report.text)
            self.assertEqual(report.json()["exit_url"], "/seb/exit")
            self.assertEqual(report.json()["state"], "disqualified")
            self.assertEqual(client.get("/seb/exit").status_code, 200)
            self.assertEqual(client.get("/api/candidate/exam/session").json()["state"], "disqualified")
            self.assertEqual(client.post("/api/candidate/exam/start").json()["state"], "disqualified")
            self.assertEqual(client.post("/api/candidate/exam/answer", json={"question_index": 0, "answer": 0}).status_code, 409)
            self.assertEqual(client.post("/api/candidate/exam/navigate", json={"direction": "next"}).status_code, 409)
            self.assertEqual(client.post("/api/candidate/exam/submit").status_code, 409)
            history = client.get("/api/candidate/assessments").json()["assessments"][0]
            self.assertEqual(history["status"], "disqualified")
            self.assertIsNone(history["multiple_choice_score"])
            self.assertEqual(assessment_detail(self.assessment_id, self.employer_id)["candidates"][0]["progress"], "disqualified")
            proof = {"X-AssessBridge-SEB-ConfigKeyHash": config_hash(SEB_BASE_URL + "/exam-entry")}
            self.assertEqual(client.post("/api/candidate/exam-entry", json={"username": self.exam_username, "password": "Exam-entry-password-123!"}, headers=proof).json()["window"], "disqualified")

    def test_missing_heartbeat_recovers_before_deadline(self):
        with self.client() as client:
            self.assertEqual(client.post("/api/candidate/exam/start").status_code, 200)
            with database() as connection:
                cursor = connection.cursor()
                cursor.execute("UPDATE assessment_exam_sessions SET last_seen_at = UTC_TIMESTAMP() - INTERVAL 60 SECOND WHERE invitation_id = %s", (self.invitation_id,))
                connection.commit()
                cursor.close()
            self.assertEqual(client.post("/api/candidate/exam/heartbeat", json={"ready": True}).status_code, 200)
            state = client.get("/api/candidate/exam/session").json()
            self.assertEqual(state["state"], "active")
            self.assertIsNone(state["disqualification_reason"])
            self.assertGreaterEqual(state["recovery_seconds"], 49)
            self.assertEqual(submit_expired_attempts(), 0)

    def test_deadline_during_short_outage_submits_server_saved_answers(self):
        with self.client() as client:
            self.assertEqual(client.post("/api/candidate/exam/start").status_code, 200)
            with database() as connection:
                cursor = connection.cursor()
                cursor.execute(
                    """UPDATE assessment_exam_sessions
                       SET last_seen_at = UTC_TIMESTAMP() - INTERVAL 25 SECOND,
                           deadline_at = UTC_TIMESTAMP() - INTERVAL 1 SECOND
                       WHERE invitation_id = %s""",
                    (self.invitation_id,),
                )
                connection.commit()
                cursor.close()
            self.assertEqual(submit_expired_attempts(), 1)
            self.assertEqual(client.get("/api/candidate/exam/session").json()["state"], "submitted")

    def test_candidate_can_exit_seb_before_signing_in(self):
        with TestClient(app) as anonymous:
            self.assertEqual(anonymous.get("/seb/exit").status_code, 200)

    def test_direct_exit_after_start_disqualifies_without_submission(self):
        with self.client() as client:
            self.assertEqual(client.post("/api/candidate/exam/start").json()["state"], "active")
            self.assertEqual(client.get("/seb/exit").status_code, 200)
            state = client.get("/api/candidate/exam/session").json()
            self.assertEqual(state["state"], "disqualified")
            self.assertIsNone(state["submitted_at"])

    def test_new_sign_in_requires_recovery_without_disqualification(self):
        with self.client() as first:
            self.assertEqual(first.post("/api/candidate/exam/start").json()["state"], "active")
            with TestClient(app) as reopened:
                login = reopened.post("/api/auth/login", json={"username": self.candidate_username, "password": self.password})
                self.assertEqual(login.status_code, 200)
                proof = {"X-AssessBridge-SEB-ConfigKeyHash": config_hash(SEB_BASE_URL + "/exam-entry")}
                entry = reopened.post("/api/candidate/exam-entry", json={"username": self.exam_username, "password": "Exam-entry-password-123!"}, headers=proof)
                self.assertEqual(entry.status_code, 200, entry.text)
                self.assertEqual(entry.json()["window"], "open")
                self.assertEqual(reopened.get("/api/candidate/assessments").json()["assessments"][0]["status"], "in_progress")
            self.assertEqual(first.get("/api/candidate/exam/session").json()["state"], "active")

    def test_fresh_exam_entry_binds_invitation_to_one_candidate_account(self):
        other_username = f"other_{secrets.token_hex(5)}"
        other_id = None
        exam_username = self.exam_username
        exam_password = "Exam-entry-password-123!"
        with database() as connection:
            cursor = connection.cursor()
            cursor.execute(
                "INSERT INTO users (username, email, password_hash, role) VALUES (%s, %s, %s, 'candidate')",
                (other_username, f"{other_username}@example.com", hash_password(self.password)),
            )
            other_id = cursor.lastrowid
            cursor.execute("UPDATE assessment_invitations SET candidate_user_id = NULL WHERE id = %s", (self.invitation_id,))
            invitation_id = self.invitation_id
            connection.commit()
            cursor.close()
        try:
            with TestClient(app) as first:
                first.headers.update({"X-AssessBridge-SEB-ConfigKeyHash": config_hash(SEB_BASE_URL + "/exam-entry")})
                self.assertEqual(first.get("/exam-entry").status_code, 200)
                self.assertIn("Step 1 of 2", first.get("/exam-entry").text)
                self.assertEqual(first.get("/exam-room").status_code, 401)
                self.assertEqual(first.post("/api/auth/login", json={"username": self.candidate_username, "password": self.password}).status_code, 200)
                self.assertEqual(first.post("/api/candidate/exam-entry", json={"username": exam_username, "password": exam_password}, headers={"X-AssessBridge-SEB-ConfigKeyHash": ""}).status_code, 403)
                first.cookies.set(EXAM_COOKIE_NAME, new_exam_access(self.candidate_id, invitation_id, datetime.now(timezone.utc) + timedelta(hours=1)))
                self.assertEqual(first.get("/exam-room").status_code, 403)
                first.cookies.delete(EXAM_COOKIE_NAME)
                self.assertEqual(first.post("/api/candidate/exam-entry", json={"username": exam_username, "password": "wrong"}).status_code, 401)
                with TestClient(app) as wrong_account:
                    wrong_account.headers.update({"X-AssessBridge-SEB-ConfigKeyHash": config_hash(SEB_BASE_URL + "/exam-entry")})
                    self.assertEqual(wrong_account.post("/api/auth/login", json={"username": other_username, "password": self.password}).status_code, 200)
                    self.assertEqual(wrong_account.get("/api/candidate/assessments").json()["assessments"], [])
                    rejected = wrong_account.post("/api/candidate/exam-entry", json={"username": exam_username, "password": exam_password})
                    self.assertEqual(rejected.status_code, 403)
                    self.assertEqual(rejected.json()["detail"], "This invitation is addressed to a different candidate email")
                with database() as connection:
                    cursor = connection.cursor()
                    cursor.execute("SELECT candidate_user_id FROM assessment_invitations WHERE id = %s", (invitation_id,))
                    self.assertIsNone(cursor.fetchone()[0])
                    cursor.close()
                accepted = first.post("/api/candidate/exam-entry", json={"username": exam_username, "password": exam_password})
                self.assertEqual(accepted.status_code, 200, accepted.text)
                self.assertEqual(accepted.json()["window"], "open")
                self.assertEqual(first.get("/exam-room").status_code, 200)
                with TestClient(app) as second:
                    second.headers.update({"X-AssessBridge-SEB-ConfigKeyHash": config_hash(SEB_BASE_URL + "/exam-entry")})
                    self.assertEqual(second.post("/api/auth/login", json={"username": other_username, "password": self.password}).status_code, 200)
                    self.assertEqual(second.post("/api/candidate/exam-entry", json={"username": exam_username, "password": exam_password}).status_code, 403)
                    second.cookies.set(EXAM_COOKIE_NAME, new_exam_access(other_id, invitation_id, datetime.now(timezone.utc) + timedelta(hours=1)))
                    self.assertEqual(second.get("/exam-room").status_code, 403)
                self.assertEqual(first.post("/api/candidate/exam-entry", json={"username": exam_username, "password": exam_password}).status_code, 200)
        finally:
            with database() as connection:
                cursor = connection.cursor()
                cursor.execute("UPDATE assessment_invitations SET candidate_user_id = %s WHERE id = %s", (self.candidate_id, invitation_id))
                cursor.execute("DELETE FROM auth_sessions WHERE user_id = %s", (other_id,))
                cursor.execute("DELETE FROM auth_audit WHERE user_id = %s", (other_id,))
                cursor.execute("DELETE FROM users WHERE id = %s", (other_id,))
                connection.commit()
                cursor.close()
