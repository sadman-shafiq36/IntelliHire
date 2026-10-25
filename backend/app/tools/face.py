# the face model: opencv's yunet finds faces and sface turns one face into a template of
# 128 numbers. images are decoded in memory and dropped as soon as the template exists;
# nothing here writes a picture anywhere

import threading
from pathlib import Path
from typing import NamedTuple

import numpy as np

from app.config import settings

MODEL_NAME = "opencv-sface-2021dec"
MIN_FACE_SCORE = 0.8
# a smaller face gives an unreliable template
MIN_FACE_PX = 60
# a second face at least this big next to the largest means we can't tell whose it is
SECOND_FACE_SHARE = 0.25
MAX_SIDE = 960

_lock = threading.Lock()
_models: tuple | None = None


class Face(NamedTuple):
    vector: list[float] | None
    # no_face | several_faces | too_small | unreadable, when there is no vector
    reason: str | None


def _load() -> tuple:
    global _models
    if _models is None:
        import cv2

        folder = Path(settings.face_model_dir)
        detector = cv2.FaceDetectorYN.create(
            str(folder / "yunet.onnx"), "", (320, 320), MIN_FACE_SCORE, 0.3, 5000
        )
        recognizer = cv2.FaceRecognizerSF.create(str(folder / "sface.onnx"), "")
        _models = (detector, recognizer)
    return _models


def _normalise(v: np.ndarray) -> list[float]:
    v = v.astype(np.float32).ravel()
    norm = float(np.linalg.norm(v))
    return (v / norm).tolist() if norm else v.tolist()


def embed_bgr(image: np.ndarray) -> Face:
    import cv2

    h, w = image.shape[:2]
    if max(h, w) > MAX_SIDE:
        scale = MAX_SIDE / max(h, w)
        image = cv2.resize(image, (int(w * scale), int(h * scale)))
        h, w = image.shape[:2]
    # the detector keeps its input size between calls, so one image at a time
    with _lock:
        detector, recognizer = _load()
        detector.setInputSize((w, h))
        _, faces = detector.detect(image)
        if faces is None or len(faces) == 0:
            return Face(None, "no_face")
        faces = sorted(faces, key=lambda f: f[2] * f[3], reverse=True)
        largest = faces[0][2] * faces[0][3]
        if len(faces) > 1 and faces[1][2] * faces[1][3] >= SECOND_FACE_SHARE * largest:
            return Face(None, "several_faces")
        if min(faces[0][2], faces[0][3]) < MIN_FACE_PX:
            return Face(None, "too_small")
        aligned = recognizer.alignCrop(image, faces[0])
        return Face(_normalise(recognizer.feature(aligned)), None)


def embed_image(data: bytes) -> Face:
    import cv2

    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        return Face(None, "unreadable")
    return embed_bgr(image)


def embed_rgb(pixels: np.ndarray) -> Face:
    import cv2

    return embed_bgr(cv2.cvtColor(pixels, cv2.COLOR_RGB2BGR))


def similarity(a: list[float], b: list[float]) -> float:
    # both are unit length, so this is the cosine
    return float(np.dot(np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)))


def average(vectors: list[list[float]]) -> list[float]:
    return _normalise(np.mean(np.asarray(vectors, dtype=np.float32), axis=0))
