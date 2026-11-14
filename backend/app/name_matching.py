"""Match invitation name parts within the holder's OCR name fields."""

import re
import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache


def parts(text):
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"['’`]", "", text)
    return re.findall(r"[a-z]+", text)


def _distance(left, right):
    previous = list(range(len(right)+1))
    for index, char in enumerate(left, 1):
        current = [index]
        for other, second in enumerate(right, 1):
            current.append(min(current[-1]+1, previous[other]+1, previous[other-1]+(char != second)))
        previous = current
    return previous[-1]


def match_parts(expected, observed):
    """Allow a shorter invitation name; require every supplied part on the ID.

    At least two distinct, fully written parts must be supplied. Extra ID name
    parts may be omitted from the invitation, but missing invitation parts,
    initials and reusing one observed word twice cannot pass.
    """
    if (len(set(expected)) < 2 or any(len(part) < 2 for part in expected)
            or not 2 <= len(expected) <= len(observed) <= 16):
        return 0.0
    options = []
    for target in expected:
        choices = []
        for index, actual in enumerate(observed):
            score = 1.0 if target == actual else SequenceMatcher(None, target, actual).ratio()
            allowance = 0 if min(len(target), len(actual)) < 5 else 1 if max(len(target), len(actual)) < 8 else 2
            if score >= .8 and _distance(target, actual) <= allowance:
                choices.append((index, score))
        if not choices:
            return 0.0
        options.append(choices)
    options.sort(key=len)

    @lru_cache(maxsize=None)
    def assign(index, used):
        if index == len(options):
            return 0.0
        best = -100.0
        for position, score in options[index]:
            if not used & (1 << position):
                best = max(best, score+assign(index+1, used | (1 << position)))
        return best

    return max(0.0, assign(0, 0)/len(expected))


_LABEL = re.compile(r"^\s*(?:\d+[.)]?\s*)?(?:(?:full|given|first|last|family|middle)\s+names?|surnames?|forenames?|names?(?:\s+of\s+(?:the\s+)?holder)?)\b[\s:./-]*", re.I)
_NON_NAME = set("birth born dob date expiry expires issue issued nationality national sex gender place address number document passport identity identification card authority signature country republic university college school company department employee student staff occupation father mother spouse parent guardian contact emergency witness issuer employer residence personal code male female".split())
_OTHER_LABEL = re.compile(r"^(?:(?:date|place) of (?:birth|issue|expiry)|dob|sex|gender|nationality|address|occupation|(?:fathers?|mothers?|spouses?|parents?|guardians?) names?|(?:passport|document|identity) (?:number|no)|issuing authority|emergency contact)$")


def split_columns(lines):
    """Undo OCR joining neighbouring columns into a single text line."""
    result = []
    for line in lines:
        groups, current = [], []
        words = line['words']
        for index, word in enumerate(words):
            gap = word['left']-(current[-1]['left']+current[-1]['width']) if current else 0
            # A new explicit field caption also starts a cell when OCR lost
            # the visual gap. Do not split the words within the caption.
            suffix = ' '.join(item['text'] for item in words[index:])
            label = _LABEL.match(suffix)
            new_label = label and parts(word['text']) != ['name'] and parts(word['text']) != ['names']
            if current and (gap > 4*max(word['height'], current[-1]['height']) or new_label):
                groups.append(current)
                current = []
            current.append(word)
        if current:
            groups.append(current)
        for group in groups:
            result.append({'text': ' '.join(word['text'] for word in group), 'words': group, 'box': _box(group)})
    return result


def _box(words):
    left = min(word["left"] for word in words)
    top = min(word["top"] for word in words)
    return (left, top, max(word["left"]+word["width"] for word in words)-left,
            max(word["top"]+word["height"] for word in words)-top)


def _near(first, second, image_width, image_height, *, fields=False):
    x, y, w, h = first
    xx, yy, ww, hh = second
    horizontal = max(0, max(x, xx)-min(x+w, xx+ww))
    vertical = max(0, max(y, yy)-min(y+h, yy+hh))
    if fields:
        return vertical <= max(6*max(h, hh), .20*image_height) and horizontal <= .45*image_width
    similar_size = max(h,hh)/max(1,min(h,hh)) <= 3
    same_row = similar_size and abs((y+h/2)-(yy+hh/2)) <= .65*max(h, hh)
    aligned = horizontal <= min(max(2*max(h, hh), .04*image_width), .08*image_width)
    return (same_row and horizontal <= min(5*max(h, hh), .18*image_width)) or (similar_size and aligned and vertical <= 1.6*max(h, hh))


def _value(words):
    # A name value may include punctuation but not dates, numbers or MRZ text.
    text = " ".join(word["text"] for word in words)
    tokens = parts(text)
    if not tokens or any(len(token) < 2 for token in tokens) or any(char.isdigit() for char in text) or "<" in text or set(tokens) & _NON_NAME:
        return None
    confidence = min(word["confidence"] for word in words if parts(word["text"]))
    return {"parts": tokens, "confidence": confidence, "box": _box(words)}


def _value_near_label(label, other, image_width, image_height):
    lx, ly, lw, lh = label
    ox, oy, ow, oh = other
    same_row = (max(lh,oh)/max(1,min(lh,oh)) <= 3 and abs((ly+lh/2)-(oy+oh/2)) <= .65*max(lh, oh)
                and ox >= lx+lw and ox-(lx+lw) <= max(3*lh, .18*image_width))
    below = oy >= ly+lh*.5 and oy-(ly+lh) <= max(3*max(lh, oh), .055*image_height) and abs(ox-lx) <= max(lw, .12*image_width)
    return (0 if same_row else 1, max(0, oy-ly), abs(ox-lx)) if same_row or below else None


def _barrier_between(first, second, barriers):
    ax, ay, aw, ah = first
    bx, by, bw, bh = second
    for x, y, w, h in barriers:
        vertical = min(ay+ah/2, by+bh/2) < y+h/2 < max(ay+ah/2, by+bh/2)
        horizontal = min(ax+aw/2, bx+bw/2) < x+w/2 < max(ax+aw/2, bx+bw/2)
        if vertical and x < min(ax+aw, bx+bw) and x+w > max(ax, bx):
            return True
        if horizontal and y < min(ay+ah, by+bh) and y+h > max(ay, by):
            return True
    return False


def match_layout(expected, lines, image_shape, threshold=.35):
    target = parts(expected)
    if not target:
        return 0.0, 0.0
    height, width = image_shape[:2]
    lines = sorted(split_columns(lines), key=lambda line: (line["box"][1], line["box"][0]))
    unrelated = set()
    for index, line in enumerate(lines):
        if not _OTHER_LABEL.fullmatch(" ".join(parts(line["text"]))):
            continue
        nearby = []
        for other_index, other in enumerate(lines):
            if index == other_index or _LABEL.match(other["text"]):
                continue
            position = _value_near_label(line["box"], other["box"], width, height)
            if position is not None:
                nearby.append((*position, other_index))
        if nearby:
            unrelated.add(min(nearby)[-1])
    fields, claimed, has_label = [], set(), False
    unreadable_field = False
    for index, line in enumerate(lines):
        label = _LABEL.match(line["text"])
        if not label:
            continue
        has_label = True
        claimed.add(index)
        # Preserve word confidence and position after removing just the label.
        value_start = label.end()
        # Bilingual captions commonly put a translation after a slash. A
        # colon separates an inline value; otherwise read the value below
        # rather than treating the translated caption as somebody's name.
        slash = re.search(r"[/|]", line["text"])
        colon = line["text"].find(":")
        if slash and (colon < 0 or slash.start() < colon):
            value_start = colon+1 if colon >= 0 else len(line["text"])
        offset, values = 0, []
        for word in line["words"]:
            end = offset+len(word["text"])
            if end > value_start:
                remainder = word["text"][max(0, value_start-offset):]
                if parts(remainder):
                    values.append({**word, "text": remainder})
            offset = end+1
        value = _value(values) if values else None
        if value is None and not values:
            # Read a value beside or directly below its label, stopping at
            # another field rather than jumping over dates or other details.
            candidates = []
            lx, ly, lw, lh = line["box"]
            for other_index, other in enumerate(lines):
                if other_index == index or other_index in claimed or other_index in unrelated or _LABEL.match(other["text"]):
                    continue
                position = _value_near_label(line["box"], other["box"], width, height)
                if position is not None:
                    candidates.append((*position, other_index, other))
            if candidates:
                _, _, _, other_index, other = min(candidates, key=lambda item: item[:4])
                # A competing label between these lines owns the lower value.
                blocked = any(_LABEL.match(middle["text"]) and ly < middle["box"][1] < other["box"][1]
                              and abs(middle["box"][0]-lx) < .15*width for middle in lines)
                if not blocked:
                    value = _value(other["words"])
                    claimed.add(other_index)
        if value:
            # Wrapped name values may occupy several nearby lines before the
            # next field. Keep them in the same field rather than dropping a
            # middle name or the second part of a surname.
            for other_index, other in enumerate(lines):
                if other_index in claimed or other_index in unrelated or _LABEL.match(other["text"]):
                    continue
                vx, vy, vw, vh = value["box"]
                ox, oy, ow, oh = other["box"]
                if oy < vy+vh*.7 or not _near(value["box"], other["box"], width, height):
                    continue
                addition = _value(other["words"])
                if not addition:
                    continue
                if any(middle["box"][1] > vy+vh*.5 and middle["box"][1] < oy
                       and abs(middle["box"][0]-vx) < .15*width
                       and (_LABEL.match(middle["text"]) or _value(middle["words"]) is None) for middle in lines):
                    continue
                value = {"parts": value["parts"]+addition["parts"],
                         "confidence": min(value["confidence"], addition["confidence"]),
                         "box": (min(vx,ox),vy,max(vx+vw,ox+ow)-min(vx,ox),oy+oh-vy)}
                claimed.add(other_index)
            fields.append(value)
        else:
            unreadable_field = True

    if has_label:
        # Keep holder-name fields together so every invitation part is checked
        # against the same name, without borrowing words from unrelated fields.
        candidates = fields
    else:
        candidates = [value for index, line in enumerate(lines) if index not in unrelated and (value := _value(line["words"]))]
    barriers = [line["box"] for line in lines if _value(line["words"]) is None]
    # Explicit holder-name fields can be on opposite sides of a document.
    # All fields remain mandatory, including a field whose value OCR missed.
    if has_label and unreadable_field:
        return 0.0, 0.0
    groups = [fields] if has_label and fields else []
    remaining = set() if has_label else set(range(len(candidates)))
    while remaining:
        group = {remaining.pop()}
        expanded = True
        while expanded:
            expanded = False
            for index in list(remaining):
                if any(_near(candidates[index]["box"], candidates[member]["box"], width, height, fields=has_label)
                       and (has_label or not _barrier_between(candidates[index]["box"], candidates[member]["box"], barriers))
                       for member in group):
                    remaining.remove(index); group.add(index); expanded = True
        groups.append([candidates[index] for index in sorted(group)])
    if not has_label:
        # A complete unlabelled name on one line is also a valid group. Split
        # names can combine only inside the connected spatial group above.
        groups.extend([[candidate] for candidate in candidates])
    best, readability = 0.0, 0.0
    for group in groups:
        confidence = min(item["confidence"] for item in group)
        observed = [token for item in group for token in item['parts']]
        # Unrelated readable text is not evidence that the holder name was read.
        if has_label or 2 <= len(target) <= len(observed) <= 16:
            readability = max(readability, confidence)
        if confidence >= threshold:
            best = max(best, match_parts(target, observed))
            if best >= .85:
                return best, confidence
    return best, readability
