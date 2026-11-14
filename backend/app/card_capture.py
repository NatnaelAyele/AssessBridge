"""Locate photo-card boundaries without relying on an issuer or background colour."""

import cv2
import numpy as np
from fastapi import HTTPException


def order_corners(points):
    points = np.asarray(points, dtype=np.float32).reshape(4, 2)
    center = points.mean(axis=0)
    points = points[np.argsort(np.arctan2(points[:, 1]-center[1], points[:, 0]-center[0]))]
    return np.roll(points, -np.argmin(points.sum(axis=1)), axis=0)


def dimensions(points):
    tl, tr, br, bl = points
    return (max(np.linalg.norm(tr-tl), np.linalg.norm(br-bl)),
            max(np.linalg.norm(bl-tl), np.linalg.norm(br-tr)))


def warp_card(image, points, margin=1.0):
    points = order_corners(points)
    center = points.mean(axis=0)
    points = center + (points-center)*margin
    width, height = (max(1, round(value)) for value in dimensions(points))
    target = np.float32([[0, 0], [width-1, 0], [width-1, height-1], [0, height-1]])
    return cv2.warpPerspective(image, cv2.getPerspectiveTransform(points, target), (width, height),
                               borderMode=cv2.BORDER_REPLICATE)


def _selected_pixels(image, points):
    """Preserve the document's pixels and mask everything outside its outline."""
    mask = np.zeros(image.shape[:2], np.uint8)
    cv2.fillConvexPoly(mask, np.round(points).astype(np.int32), 255)
    selected = np.full_like(image, 255)
    selected[mask > 0] = image[mask > 0]
    x, y, width, height = cv2.boundingRect(points)
    x0, y0 = max(0, x), max(0, y)
    return selected[y0:min(image.shape[0], y+height), x0:min(image.shape[1], x+width)]


def crop_from_corners(image, corners, *, preserve_pixels=False):
    """Treat user-selected corners as a crop hint; identity checks still run on the crop."""
    message = "Place the four corner markers around the full card."
    try:
        if not isinstance(corners, list) or len(corners) != 4:
            raise ValueError()
        if any(not isinstance(point, list) or len(point) != 2 or
               any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in point)
               for point in corners):
            raise ValueError()
        points = np.asarray(corners, dtype=np.float32)
        if not np.isfinite(points).all() or (points < 0).any() or (points > 1).any():
            raise ValueError()
        points *= np.float32([image.shape[1]-1, image.shape[0]-1])
        points = order_corners(points)
        if not cv2.isContourConvex(points):
            raise ValueError()
        width, height = dimensions(points)
        area = cv2.contourArea(points) / (image.shape[0]*image.shape[1])
        if min(width, height) < 160 or not .035 <= area <= 1 or not 1.15 <= max(width, height)/min(width, height) <= 2.6:
            raise ValueError()
        # Nearly collinear corners create extreme perspective distortion.
        for index in range(4):
            a, b = points[index-1]-points[index], points[(index+1)%4]-points[index]
            cosine = float(np.dot(a, b)/max(1, np.linalg.norm(a)*np.linalg.norm(b)))
            if abs(cosine) > .94:
                raise ValueError()
    except (ValueError, TypeError, OverflowError):
        raise HTTPException(422, message) from None
    if preserve_pixels:
        # A second reading must stay inside the selected document, but avoid
        # perspective interpolation joining or breaking small printed glyphs.
        return _selected_pixels(image, points)
    return warp_card(image, points)


def locate_outline(image, *, preserve_pixels=False):
    """Combine colour segmentation and edges, then rank plausible card quadrilaterals."""
    original_height, original_width = image.shape[:2]
    scale = min(1.0, 960/max(original_height, original_width))
    small = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else image
    height, width = small.shape[:2]
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    lab = cv2.cvtColor(small, cv2.COLOR_BGR2LAB)
    masks = [cv2.inRange(hsv, (0, 0, 100), (179, 85, 255))]
    # Hue regions distinguish a coloured card from a similarly bright wall.
    # Overlapping windows avoid a hard boundary between neighbouring colours.
    for hue in range(0, 180, 15):
        distance = np.abs(hsv[:, :, 0].astype(np.int16)-hue)
        distance = np.minimum(distance, 180-distance)
        masks.append(((distance <= 18) & (hsv[:, :, 1] >= 35) & (hsv[:, :, 2] >= 40)).astype(np.uint8)*255)
    for channel in (hsv[:, :, 1], lab[:, :, 1], lab[:, :, 2]):
        _, mask = cv2.threshold(channel, 0, 255, cv2.THRESH_BINARY+cv2.THRESH_OTSU)
        masks.extend((mask, cv2.bitwise_not(mask)))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    sources = [cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel) for mask in masks]
    for channel in (gray, hsv[:, :, 1], lab[:, :, 2]):
        blurred = cv2.GaussianBlur(channel, (5, 5), 0)
        for low, high in ((20, 60), (40, 120)):
            sources.append(cv2.morphologyEx(cv2.Canny(blurred, low, high), cv2.MORPH_CLOSE, kernel))
    best, best_score = None, -1
    for source in sources:
        # Keep nested boundaries: an enclosing room edge must not hide the card.
        contours, _ = cv2.findContours(source, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:16]:
            area = cv2.contourArea(contour)
            if not .035 <= area/(width*height) <= .96:
                continue
            x, y, box_width, box_height = cv2.boundingRect(contour)
            if x <= 1 or y <= 1 or x+box_width >= width-1 or y+box_height >= height-1:
                continue
            hull = cv2.convexHull(contour)
            perimeter = cv2.arcLength(hull, True)
            candidates = []
            for epsilon in (.015, .025, .04, .055):
                polygon = cv2.approxPolyDP(hull, epsilon*perimeter, True)
                if len(polygon) == 4 and cv2.isContourConvex(polygon):
                    candidates.append(polygon.reshape(4, 2))
            rectangle = cv2.minAreaRect(contour)
            rectangle_area = rectangle[1][0]*rectangle[1][1]
            # Rounded corners or small occlusions need not simplify to four points.
            if rectangle_area > 0 and area/rectangle_area >= .82:
                candidates.append(cv2.boxPoints(rectangle))
            for candidate in candidates:
                points = order_corners(candidate)
                card_width, card_height = dimensions(points)
                if min(card_width, card_height)/scale < 160 or not 1.2 <= max(card_width, card_height)/min(card_width, card_height) <= 2.5:
                    continue
                quad_area = cv2.contourArea(points)
                fill = area/max(1, quad_area)
                if not .77 <= fill <= 1.18:
                    continue
                angles = []
                for index in range(4):
                    a, b = points[index-1]-points[index], points[(index+1)%4]-points[index]
                    angles.append(abs(float(np.dot(a, b)/max(1, np.linalg.norm(a)*np.linalg.norm(b)))))
                if max(angles) > .7:
                    continue
                score = min(fill, 1/fill) + .35*(1-np.mean(angles)) + .3*area/(width*height)
                if score > best_score:
                    best, best_score = points/scale, score
    if best is None:
        return None
    return _selected_pixels(image, best) if preserve_pixels else warp_card(image, best, margin=1.08)
