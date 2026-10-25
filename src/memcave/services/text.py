"""Text, path, hash, and redaction helpers used by the core."""

import base64
import hashlib
import json
import math
import re
from datetime import datetime
from typing import Any
from uuid import UUID

from memcave.domain.enums import MemoryCategory
from memcave.domain.errors import ValidationError

_TOKEN = re.compile(r"[a-z0-9]+")
_SLUG_UNSAFE = re.compile(r"[^a-z0-9]+")
_PATH_OK = re.compile(r"[a-z0-9/_-]+")
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("openai_project", re.compile(r"sk-proj-[A-Za-z0-9_\-]{20,}")),
    ("openai", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("github_pat", re.compile(r"github_pat_[A-Za-z0-9_]{20,}")),
    ("github", re.compile(r"ghp_[A-Za-z0-9]{20,}")),
    ("slack", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("bearer", re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*")),
    (
        "assignment",
        re.compile(r"(?i)\b(api_key|apikey|token|secret)\b\s*[=:]\s*\S+"),
    ),
)
NIL_USER = "00000000-0000-0000-0000-000000000000"


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.casefold())


def lexical_score(query: str, document: str) -> float:
    query_tokens = set(tokenize(query))
    if not query_tokens:
        return 0.0
    document_tokens = set(tokenize(document))
    return len(query_tokens & document_tokens) / len(query_tokens)


def normalize_content(content: str) -> str:
    return " ".join(content.casefold().split())


def content_hash(
    tenant_id: UUID,
    user_id: UUID | None,
    category: MemoryCategory,
    content: str,
) -> str:
    """Suppression identity: tenant, user, category, and casefolded content."""
    normalized = normalize_content(content)
    user = NIL_USER if user_id is None else str(user_id)
    raw = f"{tenant_id}\n{user}\n{category.value}\n{normalized}"
    return hashlib.sha256(raw.encode()).hexdigest()


_SUBJECT_STOP = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "be",
        "for",
        "i",
        "in",
        "is",
        "of",
        "on",
        "or",
        "prefer",
        "remember",
        "that",
        "the",
        "this",
        "to",
        "use",
    }
)


def subject_key(text: str) -> str:
    """First content-bearing token. Fallback when a memory has no explicit key."""
    for token in tokenize(text):
        if token not in _SUBJECT_STOP:
            return token
    return ""


def _explicit_subject_key(source: object) -> str | None:
    if not isinstance(source, dict) or "subject_key" not in source:
        return None
    raw = source.get("subject_key")
    if isinstance(raw, str):
        return raw.strip()
    return ""


def resolve_subject_key(
    *,
    category: MemoryCategory,
    title: str,
    content: str,
    structured_content: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Stable subject. Explicit keys win; otherwise category plus the title slug."""
    explicit = _explicit_subject_key(structured_content)
    if explicit is None:
        explicit = _explicit_subject_key(metadata)
    if explicit is not None:
        return explicit
    try:
        return f"{category.value}/{slugify(title)}"
    except ValidationError:
        return subject_key(content)


def same_subject(memory_key: str, episode_text: str) -> bool:
    """True when an episode is about the derived memory's subject key."""
    if not memory_key:
        return False
    if memory_key == subject_key(episode_text):
        return True
    tail = memory_key.rsplit("/", 1)[-1]
    if tail and tail == subject_key(episode_text):
        return True
    return memory_key in set(tokenize(episode_text))


def subject_in_question(memory_key: str, question: str) -> bool:
    """True when the subject key itself is a token in the question."""
    if not memory_key:
        return False
    tokens = set(tokenize(question))
    if memory_key in tokens:
        return True
    tail = memory_key.rsplit("/", 1)[-1]
    return bool(tail) and tail in tokens


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = 0.0
    left_norm = 0.0
    right_norm = 0.0
    for x_value, y_value in zip(left, right, strict=True):
        dot += x_value * y_value
        left_norm += x_value * x_value
        right_norm += y_value * y_value
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (math.sqrt(left_norm) * math.sqrt(right_norm))


def slugify(title: str) -> str:
    slug = _SLUG_UNSAFE.sub("-", title.casefold()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    if not slug:
        raise ValidationError("title does not produce a safe path slug")
    return slug


def validate_logical_path(path: str, category: MemoryCategory | None = None) -> str:
    if "\\" in path or path.startswith("/") or path.startswith("~"):
        raise ValidationError("logical path must be relative")
    if not _PATH_OK.fullmatch(path):
        raise ValidationError("logical path contains unsupported characters")
    segments = path.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise ValidationError("logical path contains an empty or parent segment")
    if len(segments) > 4:
        raise ValidationError("logical path is deeper than four segments")
    if category is not None and segments[0] != category.value:
        raise ValidationError("logical path must stay under its category directory")
    return path


def allocate_logical_path(
    category: MemoryCategory,
    title: str,
    existing: set[str],
    requested: str | None = None,
) -> str:
    if requested is not None:
        path = validate_logical_path(requested, category)
    else:
        path = validate_logical_path(f"{category.value}/{slugify(title)}", category)
    if path not in existing:
        return path
    suffix = 2
    while f"{path}-{suffix}" in existing:
        suffix += 1
    return validate_logical_path(f"{path}-{suffix}", category)


def redact_text(text: str) -> tuple[str, bool, list[str]]:
    reasons: list[str] = []
    redacted = text
    changed = False
    for name, pattern in _SECRET_PATTERNS:
        updated, count = pattern.subn("[REDACTED]", redacted)
        if count:
            changed = True
            reasons.append(name)
            redacted = updated
    return redacted, changed, reasons


def redact_value(value: object) -> tuple[object, bool, list[str]]:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        changed = False
        reasons: list[str] = []
        items: list[object] = []
        for item in value:
            new_item, item_changed, item_reasons = redact_value(item)
            items.append(new_item)
            changed = changed or item_changed
            reasons.extend(item_reasons)
        return items, changed, reasons
    if isinstance(value, dict):
        changed = False
        reasons = []
        output: dict[object, object] = {}
        for key, item in value.items():
            new_key, key_changed, key_reasons = redact_value(key)
            new_item, item_changed, item_reasons = redact_value(item)
            output[new_key] = new_item
            changed = changed or key_changed or item_changed
            reasons.extend(key_reasons)
            reasons.extend(item_reasons)
        return output, changed, reasons
    return value, False, []


def encode_cursor(moment: datetime, identifier: UUID) -> str:
    payload = json.dumps({"t": moment.isoformat(), "id": str(identifier)}).encode()
    return base64.urlsafe_b64encode(payload).decode()


def decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
        moment = datetime.fromisoformat(str(payload["t"]))
        identifier = UUID(str(payload["id"]))
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        raise ValidationError("invalid pagination cursor") from exc
    if moment.tzinfo is None:
        raise ValidationError("cursor timestamp must be timezone-aware")
    return moment, identifier


def query_fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def event_search_text(payload: dict[str, object]) -> str:
    parts: list[str] = []
    for key in ("content", "raw_text", "comment", "tool_name", "outcome_type"):
        value = payload.get(key)
        if isinstance(value, str):
            parts.append(value)
    for key in ("arguments", "data", "structured_content"):
        value = payload.get(key)
        if value is not None:
            parts.append(json.dumps(value, sort_keys=True, default=str))
    return "\n".join(parts)
