"""Load the packaged benchmark catalog."""

import hashlib
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from memcave.domain.models import BenchmarkCase


def catalog_bytes() -> bytes:
    return Path(__file__).with_name("catalog.json").read_bytes()


def load_catalog() -> list[BenchmarkCase]:
    adapter = TypeAdapter(list[BenchmarkCase])
    return adapter.validate_json(catalog_bytes())


def catalog_sha256() -> str:
    return hashlib.sha256(catalog_bytes()).hexdigest()


def case_ids() -> list[str]:
    return [case.case_id for case in load_catalog()]


def raw_catalog() -> Any:
    return TypeAdapter(list[dict[str, Any]]).validate_json(catalog_bytes())
