"""Hybrid score fusion."""

import math
from datetime import datetime

from memcave.domain.enums import EventType
from memcave.domain.models import ScoreParts


def episodic_importance(event_type: EventType) -> float:
    if event_type in {EventType.FINAL_ANSWER, EventType.EXPLICIT_MEMORY_INSTRUCTION}:
        return 0.6
    if event_type in {EventType.USER_MESSAGE, EventType.ASSISTANT_MESSAGE}:
        return 0.3
    return 0.5


def recency_score(moment: datetime, as_of: datetime, half_life_days: float) -> float:
    age_days = max(0.0, (as_of - moment).total_seconds() / 86400)
    if half_life_days <= 0:
        return 0.0
    return float(0.5 ** (age_days / half_life_days))


def cosine_component(cosine: float | None) -> tuple[float, bool]:
    if cosine is None:
        return 0.0, False
    return (cosine + 1.0) / 2.0, True


def fuse(components: dict[str, float], weights: dict[str, float], unavailable: list[str]) -> float:
    total = 0.0
    for name, weight in weights.items():
        total += weight * components.get(name, 0.0)
    del unavailable
    return total


def score_parts(
    *,
    vector: float | None,
    lexical: float,
    recency: float,
    importance: float,
    confidence: float | None,
    weights: dict[str, float],
) -> ScoreParts:
    vector_value, vector_ok = cosine_component(vector)
    components: dict[str, float] = {
        "vector": vector_value,
        "lexical": lexical,
        "recency": recency,
        "importance": importance,
    }
    unavailable: list[str] = []
    if not vector_ok:
        unavailable.append("vector")
    if confidence is not None and "confidence" in weights:
        components["confidence"] = confidence
    elif "confidence" in weights:
        components["confidence"] = 0.0
        unavailable.append("confidence")
    fused = fuse(components, weights, unavailable)
    return ScoreParts(
        components=components, weights=dict(weights), fused=fused, unavailable=unavailable
    )


def min_max(parts: list[ScoreParts]) -> None:
    if not parts:
        return
    values = [part.fused for part in parts]
    low = min(values)
    high = max(values)
    for part in parts:
        if math.isclose(low, high):
            part.normalized = 1.0 if high > 0 or len(parts) == 1 else 0.0
        else:
            part.normalized = (part.fused - low) / (high - low)


def window_contains(valid_from: datetime, valid_until: datetime | None, as_of: datetime) -> bool:
    if valid_from > as_of:
        return False
    return valid_until is None or valid_until > as_of
