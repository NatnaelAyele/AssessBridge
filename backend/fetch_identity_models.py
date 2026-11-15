"""Download the approved OpenCV Zoo face models with pinned SHA-256 hashes."""

import hashlib
import os
import urllib.request
from pathlib import Path


MODELS = {
    "face_detection_yunet_2023mar.onnx": (
        "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    ),
    "face_recognition_sface_2021dec.onnx": (
        "https://huggingface.co/opencv/opencv_zoo/resolve/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
        "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79",
    ),
}


def digest(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def main() -> None:
    directory = Path(__file__).parent / "models"
    directory.mkdir(exist_ok=True)
    for name, (url, expected) in MODELS.items():
        target = directory / name
        if target.is_file() and digest(target) == expected:
            print(f"Verified {name}")
            continue
        temporary = directory / (name + ".download")
        try:
            with urllib.request.urlopen(url, timeout=120) as source, temporary.open("wb") as output:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    output.write(chunk)
            if digest(temporary) != expected:
                raise RuntimeError(f"Downloaded {name} has an unexpected SHA-256 checksum")
            os.replace(temporary, target)
            print(f"Installed {name}")
        finally:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
