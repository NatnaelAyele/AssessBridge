"""Local inference. Observations are evidence, never a misconduct decision."""

from functools import lru_cache
import threading
import json

import cv2
import numpy as np
import onnxruntime as ort

from ..identity import MODEL_DIR, _face_models, _model_lock

MODEL_VERSION = "yunet-2023mar+sface-2021dec+yolox-s-0.1.1+rtdetr-r18-o365v2+silero-066409f+policy2"
PROHIBITED_OBJECTS = {"Mobile phone": "mobile_phone", "Books": "book", "Tablet": "tablet", "Calculator": "calculator"}
OBJECTS365_CLASSES = {243: "tablet", 316: "calculator"}


@lru_cache(maxsize=3)
def session(name):
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    model = ort.InferenceSession(str(MODEL_DIR / name), sess_options=options, providers=["CPUExecutionProvider"])
    if name == "rtdetr_r18vd_objects365.onnx":
        meta = model.get_modelmeta().custom_metadata_map
        classes = json.loads(meta.get("assessbridge.classes", "{}"))
        if meta.get("assessbridge.dataset") != "Objects365-v2" or classes.get("243") != "Tablet" or classes.get("316") != "Calculator":
            raise RuntimeError("Objects365 model class mapping is missing or incorrect; rebuild the approved model")
    return model


def objects(image):
    """YOLOX official BGR/0..255 preprocessing and raw-grid decoding."""
    height, width = image.shape[:2]
    ratio = min(640 / height, 640 / width)
    resized = cv2.resize(image, (int(width * ratio), int(height * ratio)))
    canvas = np.full((640, 640, 3), 114, np.uint8)
    canvas[:resized.shape[0], :resized.shape[1]] = resized
    model = session("yolox_s.onnx")
    output = model.run(None, {model.get_inputs()[0].name: canvas.transpose(2, 0, 1)[None].astype(np.float32)})[0][0].copy()
    grids, strides = [], []
    for stride in (8, 16, 32):
        y, x = np.mgrid[:640 // stride, :640 // stride]
        grids.append(np.stack((x, y), axis=-1).reshape(-1, 2))
        strides.append(np.full((x.size, 1), stride))
    stride = np.concatenate(strides)
    output[:, :2] = (output[:, :2] + np.concatenate(grids)) * stride
    output[:, 2:4] = np.exp(np.clip(output[:, 2:4], -10, 10)) * stride
    boxes = output[:, :4] / ratio
    boxes[:, :2] -= boxes[:, 2:4] / 2
    found = []
    for class_id, label in ((0, "person"), (67, "mobile_phone"), (73, "book")):
        scores = output[:, 4] * output[:, 5 + class_id]
        selected = np.where(scores >= .45)[0]
        indices = cv2.dnn.NMSBoxes(boxes[selected].tolist(), scores[selected].tolist(), .45, .45)
        for index in np.asarray(indices).reshape(-1):
            i = selected[index]
            found.append({"label": label, "confidence": float(scores[i]),
                          "box": [round(float(v), 2) for v in boxes[i]]})
    return found


def extended_objects(image):
    """Objects365 v2 RT-DETR raw boxes/logits, decoded with focal-loss sigmoid."""
    height, width = image.shape[:2]
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    resized = cv2.resize(rgb, (640, 640), interpolation=cv2.INTER_CUBIC)
    inputs = {"image": (resized.astype(np.float32) / 255).transpose(2, 0, 1)[None]}
    model = session("rtdetr_r18vd_objects365.onnx")
    boxes, logits = model.run(None, inputs)
    if boxes.shape != (1, 300, 4) or logits.shape != (1, 300, 365):
        raise RuntimeError("Unexpected Objects365 detector output")
    boxes, logits = boxes[0], logits[0]
    xywh = np.empty_like(boxes)
    xywh[:, 0] = (boxes[:, 0] - boxes[:, 2] / 2) * width
    xywh[:, 1] = (boxes[:, 1] - boxes[:, 3] / 2) * height
    xywh[:, 2] = boxes[:, 2] * width
    xywh[:, 3] = boxes[:, 3] * height
    found = []
    for class_id, label in OBJECTS365_CLASSES.items():
        scores = 1 / (1 + np.exp(-np.clip(logits[:, class_id], -30, 30)))
        selected = np.where((scores >= .30) & np.isfinite(boxes).all(axis=1))[0]
        if not len(selected):
            continue
        candidates = xywh[selected].copy()
        candidates[:, 0] = np.clip(candidates[:, 0], 0, width)
        candidates[:, 1] = np.clip(candidates[:, 1], 0, height)
        candidates[:, 2] = np.clip(candidates[:, 2], 0, width - candidates[:, 0])
        candidates[:, 3] = np.clip(candidates[:, 3], 0, height - candidates[:, 1])
        valid = (candidates[:, 2] > 0) & (candidates[:, 3] > 0)
        candidates, confidence = candidates[valid], scores[selected][valid]
        indices = cv2.dnn.NMSBoxes(candidates.tolist(), confidence.tolist(), .30, .45)
        for index in np.asarray(indices).reshape(-1):
            found.append({"label": label, "confidence": float(confidence[index]),
                          "box": [round(float(v), 2) for v in candidates[index]]})
    return found


def pose_ratios(face):
    # Coarse orientation relative to verified selfie, not gaze or eye tracking.
    eyes = (face[4:6] + face[6:8]) / 2
    mouth = (face[10:12] + face[12:14]) / 2
    eye_span = max(1, float(np.linalg.norm(face[4:6] - face[6:8])))
    vertical = max(1, float(np.linalg.norm(mouth - eyes)))
    return np.array([(face[8] - eyes[0]) / eye_span, (face[9] - eyes[1]) / vertical])


class Vision:
    def __init__(self, reference, policy):
        self.policy = policy
        self.reference_feature = self.reference_pose = None
        self.previous = None
        self.previous_center = None
        if reference is not None:
            with _model_lock:
                detector, recognizer = _face_models()
                detector.setInputSize((reference.shape[1], reference.shape[0]))
                _, faces = detector.detect(reference)
                if faces is not None and len(faces):
                    face = max(faces, key=lambda f: f[2] * f[3])
                    self.reference_feature = recognizer.feature(recognizer.alignCrop(reference, face)).copy()
                    self.reference_pose = pose_ratios(face)

    def analyse(self, image):
        detections = objects(image)
        if {"Tablet", "Calculator"}.intersection(self.policy["prohibited_items"]):
            detections += extended_objects(image)
        signals = {}
        metrics = {"objects": detections, "signals": signals, "identity_checked": False,
                   "reference_available": self.reference_feature is not None}
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        tiny = cv2.resize(gray, (64, 36)).astype(np.float32)
        brightness, contrast = float(np.mean(gray)), float(np.std(gray))
        metrics.update(brightness=round(brightness, 2), contrast=round(contrast, 2))
        if brightness < 15 or brightness > 248 or contrast < 4:
            signals["camera_obscured"] = 1.0
        if self.previous is not None and float(np.mean(np.abs(tiny - self.previous))) < .20:
            signals["suspected_frozen_video"] = 1.0
        self.previous = tiny
        with _model_lock:
            detector, recognizer = _face_models()
            detector.setInputSize((image.shape[1], image.shape[0]))
            _, faces = detector.detect(image)
            faces = [] if faces is None else sorted(faces, key=lambda f: f[2] * f[3], reverse=True)
            # Small background faces remain evidence; identity uses the dominant face only.
            faces = [face for face in faces if min(face[2:4]) >= 25]
            metrics["face_count"] = len(faces)
            people = [obj for obj in detections if obj["label"] == "person"]
            if len(faces) > 1 or len(people) > 1:
                signals["additional_person"] = max(float(faces[1][-1]) if len(faces) > 1 else 0,
                                                    people[1]["confidence"] if len(people) > 1 else 0)
            for item, label in PROHIBITED_OBJECTS.items():
                if item in self.policy["prohibited_items"]:
                    scores = [obj["confidence"] for obj in detections if obj["label"] == label]
                    if scores:
                        signals[label] = max(scores)
            if not faces:
                signals["face_absent"] = 1.0
                return metrics
            face = faces[0]
            x, y, w, h = [int(v) for v in face[:4]]
            crop = gray[max(0, y):min(gray.shape[0], y+h), max(0, x):min(gray.shape[1], x+w)]
            sharpness = float(cv2.Laplacian(crop, cv2.CV_64F).var()) if crop.size else 0
            usable = min(w, h) >= 75 and sharpness >= 12 and "camera_obscured" not in signals
            metrics["face_usable"] = usable
            if not usable:
                signals["face_unusable"] = 1.0
                return metrics
            pose = pose_ratios(face)
            if self.reference_pose is not None:
                delta = pose - self.reference_pose
                metrics["orientation_delta"] = [round(float(v), 3) for v in delta]
                if abs(delta[0]) > .22:
                    signals["head_turned"] = float(face[-1])
                if delta[1] > .20:
                    signals["head_down"] = float(face[-1])
            center = np.array([(x+w/2)/image.shape[1], (y+h/2)/image.shape[0]])
            if self.previous_center is None:
                self.previous_center = center
            if np.linalg.norm(center-self.previous_center) > .25:
                signals["framing_changed"] = 1.0
            # Angled, blurred, or obscured faces are not identity-mismatch evidence.
            if self.reference_feature is not None and abs(pose[0]) < .35 and .10 < pose[1] < .90:
                feature = recognizer.feature(recognizer.alignCrop(image, face))
                similarity = float(recognizer.match(self.reference_feature, feature, cv2.FaceRecognizerSF_FR_COSINE))
                if np.isfinite(similarity):
                    metrics.update(identity_checked=True, identity_similarity=round(similarity, 5))
                    if similarity < self.policy["identity_threshold"]:
                        signals["identity_mismatch"] = float(face[-1])
        return metrics


class Speech:
    def __init__(self):
        self.model = session("silero_vad.onnx")
        self.state = np.zeros((2, 1, 128), np.float32)
        self.context = np.zeros((1, 64), np.float32)

    def analyse(self, pcm):
        probabilities = []
        for start in range(0, len(pcm), 512):
            window = pcm[start:start + 512]
            if len(window) < 512:
                window = np.pad(window, (0, 512-len(window)))
            data = np.concatenate((self.context, window[None]), axis=1).astype(np.float32)
            probability, self.state = self.model.run(None, {"input": data, "state": self.state, "sr": np.array(16000, np.int64)})
            self.context = data[:, -64:]
            probabilities.append(float(probability.ravel()[0]))
        speech_fraction = float(np.mean(np.asarray(probabilities) >= .5))
        signals = {}
        if speech_fraction >= .30:
            signals["speech"] = max(probabilities)
        clipped = float(np.mean(np.abs(pcm) >= .995))
        if clipped >= .02:
            signals["audio_clipping"] = 1.0
        if np.max(np.abs(pcm), initial=0) < 1e-7:
            signals["digital_audio_silence"] = 1.0
        return {"signals": signals, "speech_fraction": speech_fraction,
                "speech_probability": max(probabilities, default=0), "clipped_fraction": clipped}
