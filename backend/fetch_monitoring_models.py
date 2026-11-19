"""Fetch approved, pinned monitoring models and a Windows FFmpeg binary.

No PyTorch, imageio library, frontend framework, or cloud inference is installed.
The Windows wheel is used only as a checksum-verified FFmpeg binary archive.
On Linux/macOS install FFmpeg through the OS and set FFMPEG_CMD if necessary.
"""

import hashlib
import json
import os
from pathlib import Path
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
EXTENDED_MODEL = "rtdetr_r18vd_objects365.onnx"
EXTENDED_SHA256 = "251f73171648a0e921793e3711543daad3a3c98e6b2c241b8516a35e9abd75fb"
RELEASE_URL = "https://github.com/NatnaelAyele/AssessBridge/releases/download/monitoring-models-v1"
MODELS = {
    "yolox_s.onnx": (
        "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_s.onnx",
        "c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063"),
    "silero_vad.onnx": (
        "https://raw.githubusercontent.com/snakers4/silero-vad/066409f3d9db45a30d21dfcbf9147826ffdd7052/src/silero_vad/data/silero_vad.onnx",
        "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"),
    EXTENDED_MODEL: (RELEASE_URL + "/" + EXTENDED_MODEL, EXTENDED_SHA256),
    "rtdetr_r18vd_objects365.json": (
        RELEASE_URL + "/rtdetr_r18vd_objects365.json",
        "b8a3d58443a211bbddb1536daef6187adbc2fb22404f256a6be6231d8650dc97"),
}
FFMPEG_ARCHIVE = (
    "https://files.pythonhosted.org/packages/e5/55/1d50cdd78d862a30c2ce44f01cea387f77b364b21fa2fecf1590258b215b/imageio_ffmpeg-0.7.0-py3-none-win_amd64.whl",
    "7a22b45f60321d4172bdf2bc61f716bce4c81b85727bd59c7c7e371055a6a440",
)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def fetch(path, url, digest):
    if path.is_file() and sha256(path) == digest:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".download")
    try:
        with urllib.request.urlopen(url, timeout=60) as response, temporary.open("wb") as output:
            while block := response.read(1024 * 1024):
                output.write(block)
        if sha256(temporary) != digest:
            raise RuntimeError(f"Checksum mismatch: {path.name}")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def validate_extended_model(directory):
    """Check provenance, class mapping and inference before reporting success."""
    import numpy as np
    import onnxruntime as ort

    manifest = json.loads((directory / "rtdetr_r18vd_objects365.json").read_text(encoding="utf-8"))
    if (manifest.get("file") != EXTENDED_MODEL or manifest.get("sha256") != EXTENDED_SHA256
            or manifest.get("dataset") != "Objects365-v2"
            or manifest.get("classes") != {"243": "tablet", "316": "calculator"}
            or sha256(directory / EXTENDED_MODEL) != EXTENDED_SHA256):
        raise RuntimeError("Tablet/calculator model manifest is invalid")
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    model = ort.InferenceSession(str(directory / EXTENDED_MODEL), sess_options=options,
                                 providers=["CPUExecutionProvider"])
    meta = model.get_modelmeta().custom_metadata_map
    classes = json.loads(meta.get("assessbridge.classes", "{}"))
    if (meta.get("assessbridge.dataset") != manifest["dataset"]
            or classes.get("243") != "Tablet" or classes.get("316") != "Calculator"
            or meta.get("assessbridge.upstream_commit") != manifest.get("upstream_commit")
            or meta.get("assessbridge.weights_sha256") != manifest.get("weights_sha256")):
        raise RuntimeError("Tablet/calculator model metadata is invalid")
    output = model.run(None, {"image": np.zeros((1, 3, 640, 640), np.float32)})
    if (len(output) != 2 or output[0].shape != (1, 300, 4)
            or output[1].shape != (1, 300, 365)
            or not all(np.isfinite(value).all() for value in output)):
        raise RuntimeError("Tablet/calculator model failed its inference check")


def main():
    for name, (url, digest) in MODELS.items():
        fetch(ROOT / "backend" / "models" / name, url, digest)
        print(f"Verified {name}")
    validate_extended_model(ROOT / "backend" / "models")
    print("Verified tablet/calculator model inference and class mapping")
    if os.name == "nt":
        archive = ROOT / "var" / "ffmpeg-binary.whl"
        fetch(archive, *FFMPEG_ARCHIVE)
        destination = ROOT / "var" / "ffmpeg"
        destination.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as package:
            binaries = [name for name in package.namelist() if name.endswith(".exe") and "/binaries/" in name]
            if len(binaries) != 1:
                raise RuntimeError("Unexpected FFmpeg archive layout")
            data = package.read(binaries[0])
            executable = destination / "ffmpeg.exe"
            if not executable.exists() or hashlib.sha256(executable.read_bytes()).digest() != hashlib.sha256(data).digest():
                executable.write_bytes(data)
            for name in package.namelist():
                if "license" in name.lower() and not name.endswith("/"):
                    (destination / Path(name).name).write_bytes(package.read(name))
        print("Verified FFmpeg executable (binary only)")


if __name__ == "__main__":
    main()
