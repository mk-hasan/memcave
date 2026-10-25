"""Rule parser for explicit memory instructions."""

import re
from datetime import datetime

from memcave.domain.enums import InstructionType, MemoryCategory, MemoryStatus
from memcave.domain.models import InstructionPayload, ParsedInstruction

_REMEMBER = re.compile(r"^remember that\s+\S", re.IGNORECASE)
_FORGET = re.compile(r"^forget\s+\S", re.IGNORECASE)
_USE_INSTEAD = re.compile(r"^use\s+\S.+\binstead of\s+\S", re.IGNORECASE)
_AS_OF = re.compile(r"\bas of\s+\d{4}-\d{2}-\d{2}\b", re.IGNORECASE)
_ISO_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


def immediate_instruction(text: str) -> bool:
    """True only for the four patterns that write a memory when the event is appended."""
    compact = " ".join(text.split())
    return bool(
        _REMEMBER.match(compact)
        or _FORGET.match(compact)
        or _USE_INSTEAD.match(compact)
        or _AS_OF.search(compact)
    )


def _title_from(text: str, limit: int = 80) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def parse_instruction(payload: InstructionPayload) -> ParsedInstruction:
    """Parse an explicit instruction. Structured fields win over raw-text rules."""
    if payload.title and payload.content:
        return ParsedInstruction(
            instruction_type=payload.instruction_type,
            category=payload.category or _category_for(payload.instruction_type, payload.raw_text),
            title=payload.title,
            content=payload.content,
            confidence=1.0,
            importance=0.8,
            status=MemoryStatus.ACTIVE,
            valid_from=payload.valid_from,
            valid_until=payload.valid_until,
            target_memory_id=payload.target_memory_id,
            structured_content=payload.structured_content,
        )
    text = payload.raw_text.strip()
    lowered = text.casefold()
    if lowered.startswith("remember that") and "prefer" in lowered:
        return _remember(text, MemoryCategory.PREFERENCE)
    if lowered.startswith("remember that"):
        return _remember(text, MemoryCategory.DOMAIN_KNOWLEDGE)
    if lowered.startswith("forget"):
        needle = text[len("forget") :].strip(" :.-")
        return ParsedInstruction(
            instruction_type=InstructionType.FORGET,
            category=payload.category,
            title=_title_from(text),
            content=text,
            confidence=1.0,
            importance=0.8,
            status=MemoryStatus.ACTIVE,
            target_memory_id=payload.target_memory_id,
            forget_needle=needle,
        )
    if "instead of" in lowered and _USE_INSTEAD.match(text.strip()):
        return ParsedInstruction(
            instruction_type=InstructionType.CORRECT,
            category=payload.category or MemoryCategory.CORRECTION,
            title=_title_from(text),
            content=text,
            confidence=1.0,
            importance=0.9,
            status=MemoryStatus.ACTIVE,
            target_memory_id=payload.target_memory_id,
        )
    match = _ISO_DATE.search(text)
    if "as of" in lowered and match is not None:
        valid_from = datetime.fromisoformat(f"{match.group(1)}T00:00:00+00:00")
        return ParsedInstruction(
            instruction_type=InstructionType.TEMPORAL_CHANGE,
            category=payload.category or MemoryCategory.DOMAIN_KNOWLEDGE,
            title=_title_from(text),
            content=text,
            confidence=1.0,
            importance=0.8,
            status=MemoryStatus.ACTIVE,
            valid_from=valid_from,
            target_memory_id=payload.target_memory_id,
        )
    return ParsedInstruction(
        instruction_type=payload.instruction_type,
        category=payload.category or MemoryCategory.DOMAIN_KNOWLEDGE,
        title=_title_from(text) or "ambiguous instruction",
        content=text or payload.raw_text,
        confidence=0.4,
        importance=0.3,
        status=MemoryStatus.CANDIDATE,
        ambiguous=True,
    )


def _remember(text: str, category: MemoryCategory) -> ParsedInstruction:
    return ParsedInstruction(
        instruction_type=InstructionType.REMEMBER,
        category=category,
        title=_title_from(text),
        content=text,
        confidence=1.0,
        importance=0.7,
        status=MemoryStatus.ACTIVE,
    )


def _category_for(instruction_type: InstructionType, raw_text: str) -> MemoryCategory:
    if instruction_type is InstructionType.CORRECT:
        return MemoryCategory.CORRECTION
    if "prefer" in raw_text.casefold():
        return MemoryCategory.PREFERENCE
    return MemoryCategory.DOMAIN_KNOWLEDGE


def classify_text(text: str, structured: dict[str, object] | None = None) -> MemoryCategory:
    """First matching classification rule from the memory model."""
    lowered = text.casefold()
    if any(token in lowered for token in ("prefer", "i like", "please always")):
        return MemoryCategory.PREFERENCE
    if any(token in lowered for token in ("instead of", "do not use", "that was wrong")):
        return MemoryCategory.CORRECTION
    if any(token in lowered for token in ("must", "never", "do not")):
        return MemoryCategory.CONSTRAINT
    blob = lowered
    if structured:
        blob = f"{lowered} {' '.join(structured.keys())}"
    if any(token in blob for token in ("steps", "tables", "joins", "tool")):
        return MemoryCategory.WORKFLOW
    if any(token in lowered for token in ("project", "repository")):
        return MemoryCategory.PROJECT
    if any(token in lowered for token in ("means", "defined as", "definition", "glossary")):
        return MemoryCategory.DOMAIN_KNOWLEDGE
    if any(token in lowered for token in ("my role", "my name", "i am a", "locale")):
        return MemoryCategory.PROFILE
    return MemoryCategory.DOMAIN_KNOWLEDGE
