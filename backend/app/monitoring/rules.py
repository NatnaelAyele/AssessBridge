"""Versioned event policy and deterministic duration-based event aggregation."""

from collections import deque
import hashlib

POLICY_VERSION = "monitoring-2"
POLICY = {
    "version": POLICY_VERSION,
    "video_fps": 2,
    "identity_threshold": 0.40,
    "threshold_seconds": {
        "identity_mismatch": 5, "face_absent": 5, "face_unusable": 8,
        "additional_person": 2, "mobile_phone": 1.5, "book": 1.5,
        "tablet": 1.5, "calculator": 1.5, "head_turned": 8,
        "head_down": 8, "camera_obscured": 5, "suspected_frozen_video": 15,
        "framing_changed": 5, "speech": 3, "audio_clipping": 3,
        "digital_audio_silence": 10,
    },
    "repeated_window_seconds": 60,
    "repeated_min_episodes": 3,
    "automatic_disqualification": False,
}

TECHNICAL = {"face_unusable", "camera_obscured", "suspected_frozen_video",
             "framing_changed", "audio_clipping", "digital_audio_silence"}


class Events:
    """Merge adjacent observations, break on gaps, replay without duplicate IDs."""

    def __init__(self, part_id, stream, policy=None):
        self.part_id, self.stream = part_id, stream
        self.policy = policy or POLICY
        self.active = {}
        self.episodes = {"speech": deque(), "look_away": deque()}
        self.previous_end = None

    def _event(self, kind, state, closed):
        return {"event_key": hashlib.sha256(f"{self.part_id}:{self.stream}:{kind}:{state['start_ms']}".encode()).hexdigest(),
                "event_type": kind, "category": "monitoring_quality" if kind in TECHNICAL else "observation",
                "start_ms": state["start_ms"], "end_ms": state["end_ms"],
                "confidence": state["confidence"], "closed": closed,
                "details": {"observations": state["count"], "decision": "requires_human_review"}}

    def push(self, start_ms, end_ms, signals):
        emitted = []
        gap = self.previous_end is not None and start_ms - self.previous_end > 750
        for kind in list(self.active):
            if kind not in signals or gap:
                state = self.active.pop(kind)
                threshold = self.policy["threshold_seconds"][kind] * 1000
                if state["end_ms"] - state["start_ms"] >= threshold:
                    emitted.append(self._event(kind, state, True))
                # Repeated short turns/speech are useful even below the long-event threshold.
                group = "look_away" if kind in {"head_turned", "head_down"} else kind
                if group in self.episodes and state["end_ms"] - state["start_ms"] >= 2000:
                    episodes = self.episodes[group]
                    if episodes and state["start_ms"] <= episodes[-1][1]:
                        previous = episodes.pop()
                        episodes.append((min(previous[0], state["start_ms"]), max(previous[1], state["end_ms"])))
                    else:
                        episodes.append((state["start_ms"], state["end_ms"]))
        for kind, confidence in signals.items():
            if kind not in self.policy["threshold_seconds"]:
                continue
            state = self.active.setdefault(kind, {"start_ms": start_ms, "end_ms": end_ms, "count": 0, "confidence": 0})
            state.update(end_ms=end_ms, count=state["count"] + 1,
                         confidence=max(state["confidence"], float(confidence)))
            if end_ms - state["start_ms"] >= self.policy["threshold_seconds"][kind] * 1000:
                emitted.append(self._event(kind, state, False))
        for group, episodes in self.episodes.items():
            while episodes and episodes[0][1] < end_ms - self.policy["repeated_window_seconds"] * 1000:
                episodes.popleft()
            if len(episodes) >= self.policy["repeated_min_episodes"]:
                state = {"start_ms": episodes[0][0], "end_ms": episodes[-1][1], "count": len(episodes), "confidence": 1.0}
                emitted.append(self._event("repeated_" + group, state, True))
        self.previous_end = end_ms
        return emitted

    def finish(self):
        return self.push(self.previous_end or 0, self.previous_end or 0, {})
