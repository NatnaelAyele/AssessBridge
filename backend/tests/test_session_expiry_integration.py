"""Expired account sessions redirect documents and clear authentication cookies."""
import unittest
from fastapi.testclient import TestClient

from app.config import COOKIE_NAME, EXAM_COOKIE_NAME
from app.db import database
from app.main import app
import test_exam_session_integration as fixtures


class SessionExpiryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ExamSessionIntegrationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)

    def expire(self, client):
        with database() as db:
            c = db.cursor()
            c.execute('UPDATE auth_sessions SET expires_at=UTC_TIMESTAMP()-INTERVAL 1 SECOND WHERE user_id=%s',
                      (self.fixture.candidate_id,))
            db.commit()

    def test_expired_documents_redirect_to_sign_in_and_clear_both_cookies(self):
        with self.fixture.client() as client:
            self.expire(client)
            response = client.get('/candidate', headers={'Accept': 'text/html'}, follow_redirects=False)
            self.assertEqual(response.status_code, 303)
            self.assertEqual(response.headers['location'], '/?session=expired')
            self.assertEqual(response.headers['cache-control'], 'no-store')
            cookies = response.headers.get_list('set-cookie')
            for name in (COOKIE_NAME, EXAM_COOKIE_NAME):
                self.assertTrue(any(value.startswith(name+'=') and 'Max-Age=0' in value for value in cookies))
            self.assertEqual(client.get('/api/auth/me').status_code, 401)

    def test_expired_api_retains_json_contract_and_has_expiry_marker(self):
        with self.fixture.client() as client:
            self.expire(client)
            response = client.get('/api/candidate/assessments')
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json(), {'detail': 'Please sign in'})
            self.assertEqual(response.headers['X-AssessBridge-Session-Ended'], '1')

    def test_bad_password_does_not_log_out_a_valid_account(self):
        with self.fixture.client() as client:
            response = client.post('/api/auth/login', json={'username': self.fixture.candidate_username, 'password': 'wrong'})
            self.assertEqual(response.status_code, 401)
            self.assertNotIn('X-AssessBridge-Session-Ended', response.headers)
            self.assertNotIn('set-cookie', response.headers)
            self.assertEqual(client.get('/api/auth/me').status_code, 200)

    def test_deadline_header_tracks_the_extended_exam_session(self):
        with self.fixture.client() as client:
            me = client.get('/api/auth/me')
            self.assertTrue(me.headers.get('X-AssessBridge-Session-Expires-At'))
            started = client.post('/api/candidate/exam/start')
            self.assertEqual(started.status_code, 200, started.text)
            self.assertTrue(started.headers.get('X-AssessBridge-Session-Expires-At'))
            self.assertEqual(client.get('/api/auth/me').status_code, 200)

    def test_sign_in_page_is_public_without_an_expiry_redirect_loop(self):
        with TestClient(app) as client:
            response = client.get('/?session=expired', headers={'Accept': 'text/html'})
            self.assertEqual(response.status_code, 200)
            self.assertIn('/js/session.js', response.text)
