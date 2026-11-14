# Monitoring model provenance

The approved monitoring models run locally. Downloads and SHA-256 checks are in
`backend/fetch_monitoring_models.py` and `backend/export_objects365_model.py`.
Models are ignored by version control.

* YOLOX-S, upstream release `0.1.1rc0`: https://github.com/Megvii-BaseDetection/YOLOX
  (Apache-2.0). Uses its COCO person, cell-phone, and book classes. The preprocessing and
  grid decoding follow the upstream ONNX inference specification. Other classes
  are not reported. Sunglasses are not one of its classes.
* RT-DETR-R18, Objects365-only weights `rtdetr_r18vd_1x_objects365.pdparams`:
  https://github.com/lyuwenyu/RT-DETR (Apache-2.0), commit
  `df2a96c8c3a098567a1149bf0248da6efe6d495d`. Exported locally by
  `backend/export_objects365_model.py`, using checksum-verified upstream weights,
  source, and class definitions. Uses Objects365 **v2** class 243 (Tablet) and
  class 316 (Calculator), not the differently ordered v1 classes. Input is RGB,
  resized directly to 640x640 and scaled to 0..1. The export returns normalized
  center/size boxes and raw class logits. The runtime applies sigmoid scores and
  converts boxes to original image coordinates. The current 0.30 score cutoff
  detected a tablet and calculator in two sample photos; it is not yet calibrated
  on exam webcam footage. The runtime rejects an ONNX model without
  the expected dataset/class metadata. The adjacent JSON manifest records the
  exported model hash. Copy both artifacts when deploying.
  This model runs only when Tablet or Calculator is prohibited; books/phones keep
  using the existing YOLOX model. No observation automatically disqualifies anyone.
* Silero VAD, upstream commit `066409f3d9db45a30d21dfcbf9147826ffdd7052`:
  https://github.com/snakers4/silero-vad (MIT). Streaming ONNX input is 512 samples
  at 16 kHz, with 64 samples of context and recurrent state per recording part.
  Detects speech activity, not a speaker's identity or spoken words.
* YuNet/SFace retain the pinned downloads in `fetch_identity_models.py`.
* FFmpeg on Windows is extracted from the checksum-verified `imageio-ffmpeg`
  0.7.0 binary distribution. No imageio Python package is installed or imported.
  FFmpeg's build configuration and license are available with `ffmpeg -version`
  and `ffmpeg -L`; do not assume the binary has the wrapper's license.
  Distribution source: https://github.com/imageio/imageio-ffmpeg .

No model scores are probabilities that a candidate cheated. Thresholds and
camera sampling need validation with real volunteer exams before production.

## Rebuilding the tablet/calculator detector

Use an isolated Python 3.10 environment (the app environment stays unchanged):

```powershell
python -m pip install -r backend/requirements-model-export.txt
python backend/export_objects365_model.py
```

Run those commands with the conversion environment's Python. The script verifies
downloads, checks every checkpoint tensor against the 365-class architecture,
exports through Paddle2ONNX, validates and runs the ONNX graph, and writes
`backend/models/rtdetr_r18vd_objects365.onnx` and its JSON manifest. The regular
FastAPI worker uses ONNX Runtime; it does not import PaddlePaddle or Paddle2ONNX.
