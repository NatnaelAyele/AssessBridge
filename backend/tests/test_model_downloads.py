"""Model downloads must preserve installed files when transfer validation fails."""
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fetch_monitoring_models as downloads


class ModelDownloadTests(unittest.TestCase):
    def test_verified_download_is_reused_without_network(self):
        payload = b"verified model fixture"
        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "models" / "sample.onnx"
            with patch.object(downloads.urllib.request, "urlopen", return_value=io.BytesIO(payload)):
                downloads.fetch(path, "https://example.invalid/model", digest)
            self.assertEqual(path.read_bytes(), payload)
            with patch.object(downloads.urllib.request, "urlopen") as network:
                downloads.fetch(path, "https://example.invalid/model", digest)
                network.assert_not_called()

    def test_checksum_failure_preserves_previous_file_and_removes_partial(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.onnx"
            path.write_bytes(b"previous version")
            with patch.object(downloads.urllib.request, "urlopen", return_value=io.BytesIO(b"wrong download")):
                with self.assertRaisesRegex(RuntimeError, "Checksum mismatch"):
                    downloads.fetch(path, "https://example.invalid/model", "0" * 64)
            self.assertEqual(path.read_bytes(), b"previous version")
            self.assertFalse(path.with_suffix(".onnx.download").exists())

    def test_interrupted_transfer_removes_partial_file(self):
        class Interrupted(io.BytesIO):
            def read(self, size):
                if self.tell():
                    raise OSError("Connection interrupted")
                return super().read(size)

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "sample.onnx"
            with patch.object(downloads.urllib.request, "urlopen", return_value=Interrupted(b"partial")):
                with self.assertRaises(OSError):
                    downloads.fetch(path, "https://example.invalid/model", "0" * 64)
            self.assertFalse(path.exists())
            self.assertFalse(path.with_suffix(".onnx.download").exists())


if __name__ == "__main__":
    unittest.main()
