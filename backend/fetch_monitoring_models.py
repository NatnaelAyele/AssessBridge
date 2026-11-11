"""Fetch approved, pinned monitoring models and a Windows FFmpeg binary.

No PyTorch, imageio library, frontend framework, or cloud inference is installed.
The Windows wheel is used only as a checksum-verified FFmpeg binary archive.
On Linux/macOS install FFmpeg through the OS and set FFMPEG_CMD if necessary.
"""

import hashlib
import os
from pathlib import Path
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MODELS = {
    "yolox_s.onnx": (
        "https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_s.onnx",
        "c5c2d13e59ae883e6af3b45daea64af4833a4951c92d116ec270d9ddbe998063"),
    "silero_vad.onnx": (
        "https://raw.githubusercontent.com/snakers4/silero-vad/066409f3d9db45a30d21dfcbf9147826ffdd7052/src/silero_vad/data/silero_vad.onnx",
        "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"),
}
FFMPEG_ARCHIVE = (
    "https://files.pythonhosted.org/packages/e5/55/1d50cdd78d862a30c2ce44f01cea387f77b364b21fa2fecf1590258b215b/imageio_ffmpeg-0.7.0-py3-none-win_amd64.whl",
    "7a22b45f60321d4172bdf2bc61f716bce4c81b85727bd59c7c7e371055a6a440",
)


def fetch(path, url, digest):
    if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".download")
    try:
        with urllib.request.urlopen(url, timeout=60) as response, temporary.open("wb") as output:
            while block := response.read(1024 * 1024):
                output.write(block)
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != digest:
            raise RuntimeError(f"Checksum mismatch: {path.name}")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    for name, (url, digest) in MODELS.items():
        fetch(ROOT / "backend" / "models" / name, url, digest)
        print(f"Verified {name}")
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
