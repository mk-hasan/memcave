"""Hybrid score fusion."""

import math
from datetime import datetime

from memcave.domain.enums import EventType, MemoryCategory, MemorySource
from memcave.domain.models import Memory, RetrievalHit, ScoreParts, SessionEvent
from memcave.services.text import same_subject

_EXPLICIT_SOURCES = {MemorySource.EXPLICIT_INSTRUCTION, MemorySource.API}
_BOOST_STEP = 2.0


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


def _add_subject_boost(hit: RetrievalHit, amount: float) -> None:
    if amount <= 0:
        return
    current = float(hit.score_components.get("subject_boost", 0.0))
    hit.score_components["subject_boost"] = current + amount
    hit.score += amount


def apply_subject_boosts(
    derived: list[tuple[Memory, RetrievalHit]],
    episodic: list[tuple[SessionEvent, RetrievalHit]],
) -> None:
    """Boost later facts and explicit corrections without changing top_k."""
    groups: dict[str, list[int]] = {}
    for index, (memory, _hit) in enumerate(derived):
        if not memory.subject_key:
            continue
        groups.setdefault(memory.subject_key, []).append(index)
    for indexes in groups.values():
        if len(indexes) < 2:
            continue
        ordered = sorted(
            indexes,
            key=lambda item: (derived[item][0].valid_from, derived[item][0].version_number),
        )
        for rank, index in enumerate(ordered):
            _add_subject_boost(derived[index][1], rank * _BOOST_STEP)
    for memory, hit in derived:
        if memory.category not in {MemoryCategory.CORRECTION, MemoryCategory.PREFERENCE}:
            continue
        if memory.source not in _EXPLICIT_SOURCES:
            continue
        if not memory.subject_key:
            continue
        older = False
        for event, episode in episodic:
            if event.occurred_at.tzinfo is None or memory.valid_from.tzinfo is None:
                continue
            if event.occurred_at >= memory.valid_from:
                continue
            if same_subject(memory.subject_key, episode.content):
                older = True
                break
        if older:
            _add_subject_boost(hit, _BOOST_STEP)
