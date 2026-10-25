"""Benchmark runner."""

import platform
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from inspect import isawaitable
from typing import Any
from uuid import uuid4

from memcave import __version__
from memcave.domain.models import BenchmarkCase
from memcave.evaluation.fixtures import catalog_sha256
from memcave.evaluation.metrics import score_case
from memcave.evaluation.reports import render_markdown
from memcave.evaluation.stats import mean_difference_interval, states_difference
from memcave.evaluation.strategies import MemoryStrategy
from memcave.ports.protocols import ModelProvider
from memcave.services.memory_manager import MemoryManager

ManagerFactory = Callable[[], MemoryManager | Awaitable[MemoryManager]]


async def run_benchmark(
    cases: list[BenchmarkCase],
    strategy_list: list[MemoryStrategy],
    factory: ManagerFactory,
    model: ModelProvider,
    *,
    seed: int = 0,
    repetitions: int = 1,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for strategy in strategy_list:
        for case in cases:
            for repetition in range(repetitions):
                created = factory()
                manager = await created if isawaitable(created) else created
                build = await strategy.build(manager, case)
                output = await strategy.answer(manager, case, model)
                scored = score_case(case, output)
                rows.append(
                    {
                        "case_id": case.case_id,
                        "category": case.category,
                        "strategy": strategy.name,
                        "repetition": repetition,
                        "answer": output.answer,
                        "tool_calls": output.tool_calls,
                        "hits": output.hits,
                        "prompt": output.prompt,
                        "metrics": scored.model_dump(mode="json"),
                        "answer_input_tokens": output.answer_input_tokens,
                        "answer_output_tokens": output.answer_output_tokens,
                        "memory_build_input_tokens": build.input_tokens,
                        "memory_build_output_tokens": build.output_tokens,
                    }
                )
    report = {
        "benchmark_run_id": str(uuid4()),
        "created_at": datetime.now(UTC).isoformat(),
        "configuration": {
            "provider": getattr(model, "model_name", "model"),
            "temperature": 0.0,
            "seed": seed,
            "repetitions": repetitions,
            "deterministic": True,
            "reader": "scripted",
            "strategies": [item.name for item in strategy_list],
            "top_k": 8,
        },
        "versions": {"package": __version__, "python": platform.python_version()},
        "fixture_hashes": {"catalog.json": catalog_sha256()},
        "cases": rows,
        "metrics": _aggregate(rows),
        "bootstrap": _bootstrap(rows, [item.name for item in strategy_list], seed),
    }
    report["markdown"] = render_markdown(report)
    return report


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["repetition"] == 0:
            grouped[str(row["strategy"])].append(row)
    summary: dict[str, dict[str, float]] = {}
    for name, items in grouped.items():
        count = len(items) or 1
        summary[name] = {
            "rule_score": sum(float(item["metrics"]["rule_score"]) for item in items) / count,
            "leakage": _rate(items, "leakage", {"cross_user_isolation", "isolation"}),
            "adversarial_compliance": _rate(
                items, "adversarial_compliance", {"adversarial_memory", "adversarial"}
            ),
            "answer_tokens": float(
                sum(item["answer_input_tokens"] + item["answer_output_tokens"] for item in items)
            ),
            "memory_build_tokens": float(
                sum(
                    item["memory_build_input_tokens"] + item["memory_build_output_tokens"]
                    for item in items
                )
            ),
        }
    return summary


def _rate(items: list[dict[str, Any]], key: str, categories: set[str]) -> float:
    chosen = [item for item in items if item["category"] in categories]
    if not chosen:
        return 0.0
    return sum(float(item["metrics"][key]) for item in chosen) / len(chosen)


def _bootstrap(rows: list[dict[str, Any]], names: list[str], seed: int) -> list[dict[str, Any]]:
    by_strategy: dict[str, dict[str, float]] = defaultdict(dict)
    for row in rows:
        if row["repetition"] != 0:
            continue
        by_strategy[str(row["strategy"])][str(row["case_id"])] = float(row["metrics"]["rule_score"])
    intervals: list[dict[str, Any]] = []
    for index, left_name in enumerate(names):
        for right_name in names[index + 1 :]:
            case_ids = sorted(set(by_strategy[left_name]) & set(by_strategy[right_name]))
            low, high = mean_difference_interval(
                [by_strategy[left_name][case_id] for case_id in case_ids],
                [by_strategy[right_name][case_id] for case_id in case_ids],
                seed=seed,
            )
            intervals.append(
                {
                    "left": left_name,
                    "right": right_name,
                    "low": low,
                    "high": high,
                    "states_difference": states_difference(low, high),
                }
            )
    return intervals
