import io
import json
import os
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

import numpy as np
from app.monitoring.engine import extended_objects


class RemoteDetectorTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'MONITORING_EXTENDED_URL':'https://detector.invalid/detect', 'MONITORING_DETECTOR_TOKEN':'x'*48})
        self.env.start(); self.addCleanup(self.env.stop)
        self.image = np.zeros((540,960,3),np.uint8)

    def response(self, result):
        return patch('app.monitoring.engine.urllib.request.urlopen', return_value=io.BytesIO(json.dumps(result).encode()))

    def test_authenticated_lossless_image_transport_preserves_detection(self):
        item={'label':'tablet','confidence':.8,'box':[1,2,30,40]}
        with self.response({'model':'rtdetr-r18-o365v2','detections':[item]}) as transport:
            self.assertEqual(extended_objects(self.image),[item])
            request=transport.call_args.args[0]
            self.assertTrue(request.data.startswith(b'\x89PNG\r\n\x1a\n'))
            self.assertIn('x'*48, request.headers.values())

    def test_invalid_or_unexpected_model_output_fails_instead_of_returning_no_flags(self):
        for payload in [{'model':'other','detections':[]}, {'model':'rtdetr-r18-o365v2','detections':[{'label':'book','confidence':.8,'box':[1,2,3,4]}]}, {'model':'rtdetr-r18-o365v2','detections':[{'label':'tablet','confidence':float('nan'),'box':[1,2,3,4]}]}]:
            with self.subTest(payload=payload), self.response(payload), self.assertRaises(RuntimeError):
                extended_objects(self.image)

    def test_backend_failure_propagates_to_worker_error_handling(self):
        with patch('app.monitoring.engine.urllib.request.urlopen',side_effect=HTTPError('https://detector.invalid',503,'Unavailable',{},None)), self.assertRaises(HTTPError):
            extended_objects(self.image)

    def test_plaintext_transport_is_rejected(self):
        with patch.dict(os.environ,{'MONITORING_EXTENDED_URL':'http://detector.invalid/detect'}), self.assertRaises(RuntimeError):
            extended_objects(self.image)


if __name__=='__main__':
    unittest.main()
