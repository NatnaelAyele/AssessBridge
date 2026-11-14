"""Read names from TD3 passport MRZ glyphs using the existing OCR engine.

Format reference: https://www.icao.int/sites/default/files/publications/DocSeries/9303_p4_cons_en.pdf
This reads a printed name; it does not authenticate a passport or verify its chip.
Document numbers and dates are neither OCRed nor persisted here.
"""

import re

import cv2
import numpy as np

from .card_capture import order_corners, warp_card
from .name_matching import match_parts, parts


def _glyph_boxes(ink):
    characters, _ = cv2.findContours(ink, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = sorted(cv2.boundingRect(char) for char in characters
                   if cv2.boundingRect(char)[3] > .25*ink.shape[0] and cv2.contourArea(char) > 4)
    if len(boxes) == 44:
        return boxes
    # Faint strokes can split one letter into several connected components.
    # Group ink by column before rejecting the row; still require 44 glyphs.
    occupied = (ink > 0).sum(axis=0) >= max(2, ink.shape[0]*.08)
    changes = np.diff(np.r_[False, occupied, False].astype(np.int8))
    boxes = []
    for start, end in zip(np.flatnonzero(changes == 1), np.flatnonzero(changes == -1)):
        ys, _ = np.nonzero(ink[:, start:end])
        if len(ys) > 4 and np.ptp(ys)+1 > .25*ink.shape[0]:
            boxes.append((int(start), int(ys.min()), int(end-start), int(np.ptp(ys)+1)))
    return boxes


def _bands(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    width = gray.shape[1]
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(19, width//50), max(7, width//140)))
    black = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, kernel)
    _, binary = cv2.threshold(black, 0, 255, cv2.THRESH_BINARY+cv2.THRESH_OTSU)
    connected = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (max(21, width//50), 3)))
    contours, _ = cv2.findContours(connected, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    bands = []
    for contour in contours:
        rectangle = cv2.minAreaRect(contour)
        long, short = max(rectangle[1]), min(rectangle[1])
        if long < .35*width or short < 8 or long/short < 12 or rectangle[0][1] < .4*gray.shape[0]:
            continue
        points = order_corners(cv2.boxPoints(rectangle))
        # Expand perpendicular to the text baseline so punctuation tips and
        # ascenders survive the deskew, without widening the fixed-width row.
        center = points.mean(axis=0)
        horizontal = (points[1]-points[0])
        horizontal /= max(1, np.linalg.norm(horizontal))
        vertical = np.float32([-horizontal[1], horizontal[0]])
        delta = points-center
        expanded = center + np.outer(delta @ horizontal, horizontal)*1.01 + np.outer(delta @ vertical, vertical)*1.4
        band = warp_card(image, expanded)
        if band.shape[1] < band.shape[0]:
            continue
        g = cv2.cvtColor(band, cv2.COLOR_BGR2GRAY)
        flat = cv2.divide(g, np.maximum(cv2.GaussianBlur(g, (0, 0), 9), 1), scale=255)
        _, ink = cv2.threshold(flat, 0, 255, cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU)
        boxes = _glyph_boxes(ink)
        if len(boxes) != 44:
            continue
        centers = np.array([x+w/2 for x, _, w, _ in boxes])
        spacing = np.diff(centers)
        if spacing.mean() <= 0 or spacing.std()/spacing.mean() > .20:
            continue
        bands.append({"flat": flat, "ink": ink, "boxes": boxes, "center": rectangle[0], "width": long, "height": short})
    return sorted(bands, key=lambda band: band["center"][1])[:6]


def _filler_positions(band):
    ink, boxes = band["ink"], band["boxes"]
    glyphs = [cv2.resize(ink[y:y+h, x:x+w], (20, 28), interpolation=cv2.INTER_NEAREST) > 0 for x, y, w, h in boxes]
    template = np.mean(glyphs[-6:], axis=0) > .5
    density = template.mean()
    positions = np.arange(20)
    row_centers = (template*positions).sum(axis=1)/np.maximum(1, template.sum(axis=1))
    # A filler is a left-pointing chevron, not simply a repeated letter.
    if not .1 < density < .7 or min(row_centers[:8].mean(), row_centers[-8:].mean()) <= row_centers[11:17].mean()+2:
        return None
    scores = [2*np.sum(glyph & template)/max(1, np.sum(glyph)+np.sum(template)) for glyph in glyphs]
    def points_left(glyph):
        centers = (glyph*positions).sum(axis=1)/np.maximum(1, glyph.sum(axis=1))
        return (min(centers[:8].mean(), centers[-8:].mean()) > centers[11:17].mean()+2
                and glyph[:, :4].any(axis=1).mean() < .65)

    # Blurred A/K strokes can overlap the template. Every separator must also
    # have left-chevron geometry, not just resemble the trailing fillers.
    filler = [score >= .70 and points_left(glyph) for score, glyph in zip(scores, glyphs)]
    if not all(filler[-6:]) or any(filler[index] for index in (0, 2, 3, 4, 5)):
        return None
    return filler


def _read_piece(band, first, end, read_layout, *, passport_prefix=False):
    boxes, flat = band["boxes"], band["flat"]
    left, right = boxes[first][0], boxes[end-1][0]+boxes[end-1][2]
    source = flat[:, max(0, left-2):min(flat.shape[1], right+2)]
    readings = {}
    for scale in (4, 3):
        image = cv2.resize(source, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        image = cv2.copyMakeBorder(image, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
        _, binary = cv2.threshold(image, 0, 255, cv2.THRESH_BINARY+cv2.THRESH_OTSU)
        for view in (image, binary):
            for psm in (7, 13):
                words = [word for row in read_layout(view, psm=psm) for word in row["words"] if word["text"].strip()]
                text = "".join(word["text"] for word in words).upper()
                if not words or not re.fullmatch(r"[A-Z<]+", text) or len(text) != end-first:
                    continue
                if passport_prefix:
                    if not re.fullmatch(r"P[A-Z<][A-Z]{3}", text):
                        continue
                    # Only the document type is needed for name extraction.
                    # OCR disagreement over the unused issuer code must not
                    # block a readable name. Still require repeated P readings.
                    text = 'P'
                confidence = min(word["confidence"] for word in words)
                if confidence >= .35:
                    readings.setdefault(text, []).append(confidence)
            if len(readings) == 1 and len(next(iter(readings.values()))) >= 2:
                text = next(iter(readings))
                return text, min(readings[text])
    ranked = sorted(readings.items(), key=lambda item: len(item[1]), reverse=True)
    if not ranked or len(ranked[0][1]) < 2 or (len(ranked) > 1 and len(ranked[0][1]) == len(ranked[1][1])):
        return None
    return ranked[0][0], min(ranked[0][1])


def read_passport_name(image, expected, read_layout):
    """Require two aligned 44-character rows and complete, repeatable name readings."""
    bands = _bands(image)
    for upper, lower in zip(bands, bands[1:]):
        dx = abs(upper["center"][0]-lower["center"][0])
        dy = lower["center"][1]-upper["center"][1]
        if dx > .07*upper["width"] or abs(upper["width"]-lower["width"]) > .08*upper["width"] or not 1.1 <= dy/max(upper["height"], lower["height"]) <= 4.5:
            continue
        filler = _filler_positions(upper)
        if filler is None:
            continue
        prefix = _read_piece(upper, 0, 5, read_layout, passport_prefix=True)
        if prefix is None:
            continue
        trailing = 44
        while trailing > 5 and filler[trailing-1]:
            trailing -= 1
        separators = [index for index in range(5, trailing-1) if filler[index] and filler[index+1]]
        if len(separators) != 1:
            continue
        split = separators[0]
        if split <= 5 or split+2 >= trailing:
            continue
        groups, start = [], 5
        for index in range(5, trailing):
            if filler[index]:
                if start < index:
                    groups.append((start, index))
                start = index+1
        if start < trailing:
            groups.append((start, trailing))
        if not 2 <= len(groups) <= 10:
            continue
        readings = [_read_piece(upper, start, end, read_layout) for start, end in groups]
        if any(reading is None or '<' in reading[0] for reading in readings):
            continue
        observed = [reading[0].lower() for reading in readings]
        # OCR extraction never uses the invitation to invent or repair text.
        return match_parts(parts(expected), observed), min(reading[1] for reading in readings)
    return 0.0, 0.0
