import unittest
from unittest.mock import MagicMock, patch
from fastapi import HTTPException
from app import operations


class AdmissionTests(unittest.TestCase):
    def setUp(self):
        self.policy = dict(sessions=1, duration_minutes=30, reserve_bytes=4096, bytes_per_minute=1024)
        self.cursor = MagicMock()

    def test_disabled_preserves_local_behavior(self):
        with patch.object(operations, 'limits', return_value=dict(self.policy, sessions=0)):
            operations.acquire_admission(self.cursor)
            operations.require_capacity(self.cursor, 120)
        self.cursor.execute.assert_not_called()

    def test_busy_lock_rejects_before_start(self):
        self.cursor.fetchone.return_value = {'acquired': 0}
        with patch.object(operations, 'limits', return_value=self.policy):
            with self.assertRaises(HTTPException) as result:
                operations.acquire_admission(self.cursor)
        self.assertEqual(result.exception.status_code, 503)

    def check(self, occupied=0, free=1000000, healthy=True, duration=30):
        self.cursor.fetchone.return_value = {'occupied': occupied}
        with patch.object(operations, 'limits', return_value=self.policy), \
             patch.object(operations, 'dependency_status', return_value={'worker': healthy}), \
             patch.object(operations.shutil, 'disk_usage', return_value=MagicMock(free=free)):
            operations.require_capacity(self.cursor, duration)

    def test_available_slot(self): self.check()
    def test_active_or_uploading_slot_blocks_new_start(self):
        with self.assertRaises(HTTPException): self.check(occupied=1)
    def test_worker_failure_blocks_start(self):
        with self.assertRaises(HTTPException): self.check(healthy=False)
    def test_reserves_full_attempt_space_above_disk_floor(self):
        with self.assertRaises(HTTPException): self.check(free=4096+31*1024-1)
    def test_overlong_exam_blocks_start(self):
        with self.assertRaises(HTTPException): self.check(duration=31)

    def test_overlong_invitation_rejected_before_mail_or_persistence(self):
        with patch.object(operations, 'limits', return_value=self.policy):
            with self.assertRaises(HTTPException) as error:
                operations.require_supported_duration(31)
        self.assertEqual(error.exception.status_code, 422)
        self.assertIn('30 minutes', error.exception.detail)

    def test_upload_budget_counts_all_recording_parts(self):
        self.cursor.fetchone.return_value = {'bytes': 31*1024, 'duration_minutes': 30}
        with patch.object(operations, 'limits', return_value=self.policy):
            with self.assertRaises(HTTPException) as result:
                operations.require_upload_budget(self.cursor, 1, 1)
        self.assertEqual(result.exception.status_code, 413)

    def test_upload_budget_allows_reserved_recording(self):
        self.cursor.fetchone.return_value = {'bytes': 0, 'duration_minutes': 30}
        with patch.object(operations, 'limits', return_value=self.policy):
            operations.require_upload_budget(self.cursor, 1, 31*1024)


if __name__ == '__main__': unittest.main()
