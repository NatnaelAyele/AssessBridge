import unittest

from app.monitoring.worker import decoder_stalled


class DecoderWatchdogTests(unittest.TestCase):
    def test_long_cpu_analysis_keeps_decoder_alive_while_frames_progress(self):
        # Input finished at t=10, but six frames each require 40 seconds.
        for current in (50, 90, 130, 170, 210, 250):
            self.assertFalse(decoder_stalled(10, current - 40, current))

    def test_stalled_output_after_input_eof_is_bounded(self):
        self.assertFalse(decoder_stalled(10, 50, 140))
        self.assertTrue(decoder_stalled(10, 50, 141))

    def test_live_input_is_not_treated_as_a_drained_decoder(self):
        self.assertFalse(decoder_stalled(None, 1, 1000))


if __name__ == '__main__':
    unittest.main()
