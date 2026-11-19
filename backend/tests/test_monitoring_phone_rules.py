import unittest
from copy import deepcopy

from app.monitoring.rules import Events, POLICY


class PhoneEvidenceTests(unittest.TestCase):
    kind = "mobile_phone"
    evidence_key = "phone_evidence"

    def replay(self, detections, policy=None):
        aggregator = Events(1, "video", policy or deepcopy(POLICY))
        events = []
        for index, detected in enumerate(detections):
            events.extend(aggregator.push(index * 500, (index + 1) * 500,
                                         {self.kind: .6} if detected else {}))
        events.extend(aggregator.finish())
        return [event for event in events if event["event_type"] == self.kind]

    def test_blur_test_pattern_survives_single_missed_checks(self):
        events = self.replay([True, False, True, False, True, False, False])
        self.assertTrue(events)
        self.assertTrue(events[-1]["closed"])
        self.assertEqual((events[-1]["start_ms"], events[-1]["end_ms"]), (0, 2500))
        self.assertEqual(events[-1]["details"]["observations"], 3)
        self.assertEqual(len({event["event_key"] for event in events}), 1)

    def test_isolated_detections_do_not_accumulate_across_long_gaps(self):
        self.assertEqual(self.replay([True, False, False] * 6), [])

    def test_two_detections_are_not_enough(self):
        self.assertEqual(self.replay([True, False, True]), [])

    def test_consecutive_detections_still_work_and_close_at_end(self):
        events = self.replay([True, True, True])
        self.assertTrue(events[-1]["closed"])
        self.assertEqual(events[-1]["end_ms"], 1500)

    def test_saved_old_policy_keeps_original_rules(self):
        old_policy = deepcopy(POLICY)
        del old_policy[self.evidence_key]
        self.assertEqual(self.replay([True, False, True, False, True], old_policy), [])

    def test_missing_media_breaks_sequence(self):
        aggregator = Events(1, "video")
        self.assertEqual(aggregator.push(0, 500, {self.kind: .6}), [])
        self.assertEqual(aggregator.push(500, 1000, {self.kind: .6}), [])
        self.assertEqual(aggregator.push(2500, 3000, {self.kind: .6}), [])
        self.assertEqual(aggregator.finish(), [])

    def test_other_items_still_require_consecutive_detections(self):
        aggregator = Events(1, "video")
        events = []
        for index in range(5):
            events.extend(aggregator.push(index * 500, (index + 1) * 500,
                                         {"tablet": .8} if index % 2 == 0 else {}))
        events.extend(aggregator.finish())
        self.assertEqual(events, [])


class BookEvidenceTests(PhoneEvidenceTests):
    kind = "book"
    evidence_key = "book_evidence"

    def test_phone_and_book_evidence_cannot_be_combined(self):
        aggregator = Events(1, "video")
        events = []
        for index, kind in enumerate(("book", "mobile_phone", "book")):
            events.extend(aggregator.push(index * 500, (index + 1) * 500, {kind: .6}))
        events.extend(aggregator.finish())
        self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
