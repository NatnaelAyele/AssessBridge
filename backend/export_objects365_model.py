"""Build the approved Objects365-v2 detector in an isolated Python 3.10 environment.

Install requirements-model-export.txt there, then run this script. The app itself
only needs the resulting ONNX file and its existing ONNX Runtime installation.
Upstream sources and weights are checksum verified before they are loaded.
"""
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import numpy as np

from fetch_monitoring_models import fetch

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "var" / "model-export"
COMMIT = "df2a96c8c3a098567a1149bf0248da6efe6d495d"
WEIGHT_SHA = "127b6e675ad7a44c29220d659182d462426e9591ccde8aed5ecce7e3c56b2d9c"
NAME = "rtdetr_r18vd_objects365"


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    archive = WORK / "rtdetr-source.zip"
    fetch(archive, f"https://codeload.github.com/lyuwenyu/RT-DETR/zip/{COMMIT}",
          "e6014ae32d2d384799e353a7a6a511aaef1dad77763492738dfef70e714d6564")
    source_dir = (WORK / "source").resolve()
    with zipfile.ZipFile(archive) as package:
        if not all((source_dir / name).resolve().is_relative_to(source_dir) for name in package.namelist()):
            raise RuntimeError("Unsafe source archive")
        package.extractall(source_dir)
    upstream = source_dir / f"RT-DETR-{COMMIT}" / "rtdetr_paddle"
    weights = WORK / "rtdetr_r18vd_1x_objects365.pdparams"
    fetch(weights, "https://bj.bcebos.com/v1/paddledet/models/rtdetr_r18vd_1x_objects365.pdparams", WEIGHT_SHA)
    labels_source = WORK / "objects365_classes_source.py"
    fetch(labels_source, "https://raw.githubusercontent.com/open-mmlab/mmdetection/v3.3.0/mmdet/datasets/objects365.py",
          "5566586de7bc3af60dc7ce6d023015f84e399eadb7cbbf7a97a06667d1d2adf6")
    # Read the upstream class tuple as data; do not import MMDetection.
    definition = next(n for n in ast.parse(labels_source.read_text(encoding="utf-8")).body
                      if isinstance(n, ast.ClassDef) and n.name == "Objects365V2Dataset")
    meta = next(n.value for n in definition.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "METAINFO" for t in n.targets))
    labels = ast.literal_eval(meta)["classes"]
    assert len(labels) == 365 and labels[243] == "Tablet" and labels[316] == "Calculator"
    label_file = WORK / "objects365-v2-labels.txt"
    label_file.write_text("\n".join(labels), encoding="utf-8")

    sys.path.insert(0, str(upstream))
    import paddle
    from ppdet.core.workspace import load_config, merge_config
    from ppdet.engine import Trainer
    paddle.set_device("cpu")
    cfg = load_config(str(upstream / "configs/rtdetr/rtdetr_r18vd_6x_coco.yml"))
    # Export raw boxes and class logits. Paddle2ONNX 1.0.5 produces a Tile node
    # in Paddle's postprocessor that fails at runtime with dynamic batch shapes.
    # Decode the small raw outputs in the FastAPI worker instead.
    merge_config({"num_classes": 365, "use_gpu": False, "use_ema": False,
                  "pretrain_weights": None, "exclude_post_process": True,
                  "trt": True, "filename": NAME})
    trainer = Trainer(cfg, mode="test")
    state = paddle.load(str(weights))
    expected = trainer.model.state_dict()
    missing = [key for key in expected if key not in state]
    mismatched = [key for key in expected if key in state and list(expected[key].shape) != list(state[key].shape)]
    if missing or mismatched:
        raise RuntimeError(f"Model/weight mismatch: missing={missing}, shape={mismatched}")
    trainer.model.set_state_dict(state)
    trainer.dataset.anno_path = str(label_file)
    trainer.dataset.dataset_dir = ""
    trainer.export(str(WORK / "inference"))
    temporary = WORK / (NAME + ".onnx")
    subprocess.run([sys.executable, "-m", "paddle2onnx.command",
                    "--model_dir", str(WORK / "inference" / NAME),
                    "--model_filename", "model.pdmodel", "--params_filename", "model.pdiparams",
                    "--opset_version", "16", "--save_file", str(temporary)], check=True)
    import onnx
    model = onnx.load(str(temporary))
    onnx.checker.check_model(model)
    metadata = {"assessbridge.dataset": "Objects365-v2", "assessbridge.classes": json.dumps(dict(enumerate(labels))),
                "assessbridge.upstream_commit": COMMIT, "assessbridge.weights_sha256": WEIGHT_SHA}
    onnx.helper.set_model_props(model, metadata)
    import onnxruntime as ort
    probe = ort.InferenceSession(model.SerializeToString(), providers=["CPUExecutionProvider"])
    outputs = probe.run(None, {"image": np.zeros((1, 3, 640, 640), np.float32)})
    if len(outputs) != 2 or outputs[0].shape != (1, 300, 4) or outputs[1].shape != (1, 300, 365):
        raise RuntimeError("Exported model failed the inference smoke check")
    destination = ROOT / "backend" / "models" / (NAME + ".onnx")
    staged = destination.with_suffix(".onnx.tmp")
    onnx.save(model, str(staged))
    staged.replace(destination)
    manifest = {"file": destination.name, "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
                "upstream_commit": COMMIT, "weights_sha256": WEIGHT_SHA, "dataset": "Objects365-v2",
                "classes": {"243": "tablet", "316": "calculator"}}
    destination.with_suffix(".json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
