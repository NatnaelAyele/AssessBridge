import unittest
from copy import deepcopy
from unittest.mock import MagicMock, patch

import numpy as np

from app.monitoring.engine import Vision
from app.monitoring.rules import Events, POLICY


class FramingEvidenceTests(unittest.TestCase):
    def replay(self, states, policy=None):
        aggregator = Events(1, 'video', policy or deepcopy(POLICY))
        events = []
        for index, state in enumerate(states):
            signals = {'framing_changed': 1.0} if state == 'C' else {}
            metrics = {'framing_state': {'C': 'changed', 'N': 'normal', 'U': 'unavailable'}[state]}
            events.extend(aggregator.push(index * 500, (index + 1) * 500, signals, metrics))
        events.extend(aggregator.finish())
        return [event for event in events if event['event_type'] == 'framing_changed']

    def test_five_seconds_of_evidence_survive_one_second_unknown_gap(self):
        events = self.replay('CCCCUUCCCCCC')
        self.assertTrue(events)
        self.assertTrue(events[-1]['closed'])
        self.assertEqual((events[-1]['start_ms'], events[-1]['end_ms']), (0, 6000))
        self.assertEqual(events[-1]['details']['observations'], 10)
        self.assertEqual(events[-1]['category'], 'monitoring_quality')
        self.assertEqual(len({event['event_key'] for event in events}), 1)

    def test_unknown_time_does_not_count_as_evidence(self):
        self.assertEqual(self.replay('CCCCCCCCCUU'), [])

    def test_more_than_one_second_unknown_resets(self):
        self.assertEqual(self.replay('CCCCUUUCCCCCC'), [])

    def test_clear_return_to_normal_resets_immediately(self):
        self.assertEqual(self.replay('CCCCCCCCNCCCCCCCC'), [])

    def test_normal_closes_at_last_changed_frame(self):
        events = self.replay('CCCCCCCCCCN')
        self.assertTrue(events[-1]['closed'])
        self.assertEqual(events[-1]['end_ms'], 5000)

    def test_sparse_evidence_cannot_accumulate_beyond_seven_seconds(self):
        self.assertEqual(self.replay('CCCUU' * 8), [])

    def test_continuous_evidence_qualifies_at_five_seconds(self):
        self.assertEqual(self.replay('C' * 9), [])
        events = self.replay('C' * 10)
        self.assertEqual(events[-1]['end_ms'], 5000)
        self.assertTrue(events[-1]['closed'])

    def test_old_saved_policy_retains_consecutive_rule(self):
        policy = deepcopy(POLICY)
        del policy['framing_evidence']
        self.assertEqual(self.replay('CCCCUUCCCCCC', policy), [])

    def test_missing_media_breaks_sequence(self):
        aggregator = Events(1, 'video')
        for index in range(8):
            self.assertEqual(aggregator.push(index * 500, (index + 1) * 500,
                             {'framing_changed': 1}), [])
        self.assertEqual(aggregator.push(5500, 6000, {'framing_changed': 1}), [])
        self.assertEqual(aggregator.finish(), [])


class FramingMeasurementTests(unittest.TestCase):
    def test_measurement_distinguishes_normal_changed_and_unavailable(self):
        image = np.random.default_rng(1).integers(30, 220, (540, 960, 3), dtype=np.uint8)
        def face(x):
            return np.array([[x, 150, 100, 100, x+20, 180, x+75, 180,
                              x+50, 200, x+25, 225, x+70, 225, .95]], dtype=np.float32)
        detector = MagicMock()
        detector.detect.side_effect = [(None, face(100)), (None, face(400)),
                                       (None, None), (None, face(400)), (None, face(100))]
        with patch('app.monitoring.engine.objects', return_value=[]), \
                patch('app.monitoring.engine._face_models', return_value=(detector, None)):
            vision = Vision(None, {**deepcopy(POLICY), 'prohibited_items': []})
            results = [vision.analyse(image) for _ in range(5)]
        self.assertEqual([r['framing_state'] for r in results],
                         ['normal', 'changed', 'unavailable', 'changed', 'normal'])
        self.assertIn('framing_changed', results[1]['signals'])
        self.assertNotIn('framing_changed', results[-1]['signals'])
        # The baseline stays at the first usable face, even after unreadable frames.
        np.testing.assert_allclose(vision.previous_center, [(100+50)/960, (150+50)/540])


if __name__ == '__main__':
    unittest.main()
