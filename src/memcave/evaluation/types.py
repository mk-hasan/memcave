"""Benchmark result types. These stay free of adapter imports."""

from typing import Any

from pydantic import Field

from memcave.domain.models import MemcaveModel


class BuildCost(MemcaveModel):
    input_tokens: int = 0
    output_tokens: int = 0


class StrategyOutput(MemcaveModel):
    answer: str
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    hits: list[dict[str, Any]] = Field(default_factory=list)
    prompt: str
    answer_input_tokens: int = 0
    answer_output_tokens: int = 0
    memory_build_input_tokens: int = 0
    memory_build_output_tokens: int = 0


class CaseScore(MemcaveModel):
    rule_score: float
    exact_match: float | None = None
    fact_hits: int = 0
    fact_total: int = 0
    forbidden_hits: int = 0
    leakage: float = 0.0
    adversarial_compliance: float = 0.0
    workflow_match: float | None = None
