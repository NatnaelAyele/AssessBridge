"""Private RT-DETR inference service; never exposes exam or database access."""
from contextlib import asynccontextmanager
import asyncio
import hmac
import logging
import os

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .engine import local_extended_objects, session

TOKEN = os.environ.get('MONITORING_DETECTOR_TOKEN', '')
if len(TOKEN) < 32:
    raise RuntimeError('A detector authentication secret is required')


@asynccontextmanager
async def lifespan(_app):
    session('rtdetr_r18vd_objects365.onnx')
    yield


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
INFERENCE = asyncio.Lock()


@app.get('/health')
def health():
    return {'ready': True}


@app.post('/detect')
async def detect(request: Request):
    if not hmac.compare_digest(request.headers.get('X-AssessBridge-Detector-Token', ''), TOKEN):
        raise HTTPException(401, 'Access denied')
    pieces = []
    size = 0
    async for piece in request.stream():
        size += len(piece)
        if size > 2 * 1024 * 1024:
            raise HTTPException(413, 'Image is too large')
        pieces.append(piece)
    raw = b''.join(pieces)
    if not raw.startswith(b'\x89PNG\r\n\x1a\n'):
        raise HTTPException(422, 'Invalid image')
    # Reject decompression bombs before handing the image to the decoder.
    if len(raw) < 24 or int.from_bytes(raw[16:20], 'big') != 960 or int.from_bytes(raw[20:24], 'big') != 540:
        raise HTTPException(422, 'Invalid image dimensions')
    image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if image is None or image.shape != (540,960,3):
        raise HTTPException(422, 'Invalid image')
    try:
        # One request at a time on this small inference host. The API/worker
        # heartbeat stays independent on web-01 while native inference runs.
        async with INFERENCE:
            found = await asyncio.to_thread(local_extended_objects, image)
    except Exception:
        logging.getLogger(__name__).exception('Extended object inference failed')
        raise HTTPException(503, 'Detection is temporarily unavailable')
    return JSONResponse({'model': 'rtdetr-r18-o365v2', 'detections': found}, headers={'Cache-Control':'no-store'})
