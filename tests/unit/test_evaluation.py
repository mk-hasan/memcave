"""Benchmark catalog and CI assertions."""

import json

from tests.conftest import manager_from_parts

from memcave.adapters.memory.stores import (
    InMemoryData,
    InMemoryDerivedStore,
    InMemoryEpisodicStore,
    InMemoryEventSink,
    InMemoryVectorStore,
)
from memcave.adapters.models.fake import FakeModelProvider
from memcave.evaluation.fixtures import case_ids, load_catalog
from memcave.evaluation.reports import REQUIRED_SECTIONS, render_markdown
from memcave.evaluation.runner import run_benchmark
from memcave.evaluation.strategies import strategies

EXPECTED_IDS = [
    "pref-concise",
    "pref-metric-units",
    "pref-markdown-table",
    "corr-orders-table",
    "corr-exclude-cancelled",
    "corr-forget-verbose",
    "domain-gross-margin",
    "domain-fiscal-february",
    "domain-status-held",
    "wf-quarterly-revenue",
    "wf-reuse-fewer-tools",
    "wf-validate-before-answer",
    "change-warehouse",
    "change-before-valid",
    "change-return-window",
    "agg-three-facts",
    "agg-project-plus-preference",
    "irr-unrelated-sessions",
    "irr-similar-project",
    "ctrl-arithmetic",
    "ctrl-no-memory-needed",
    "iso-other-user",
    "iso-other-tenant",
    "adv-ignore-instructions",
    "adv-call-tool",
]


def _factory() -> object:
    data = InMemoryData()
    return manager_from_parts(
        InMemoryEpisodicStore(data),
        InMemoryDerivedStore(data),
        InMemoryVectorStore(data),
        InMemoryEventSink(data),
    )


async def test_catalog_and_ci_assertions(tmp_path: object) -> None:
    from pathlib import Path

    assert isinstance(tmp_path, Path)
    assert case_ids() == EXPECTED_IDS
    cases = load_catalog()
    report = await run_benchmark(
        cases,
        strategies(),
        _factory,  # type: ignore[arg-type]
        FakeModelProvider(),
        seed=0,
        repetitions=2,
    )
    again = await run_benchmark(
        cases,
        strategies(),
        _factory,  # type: ignore[arg-type]
        FakeModelProvider(),
        seed=0,
        repetitions=1,
    )
    first = [
        (row["strategy"], row["case_id"], row["answer"], row["tool_calls"])
        for row in report["cases"]
        if row["repetition"] == 0
    ]
    second = [
        (row["strategy"], row["case_id"], row["answer"], row["tool_calls"])
        for row in report["cases"]
        if row["repetition"] == 1
    ]
    third = [
        (row["strategy"], row["case_id"], row["answer"], row["tool_calls"])
        for row in again["cases"]
    ]
    assert first == second == third
    markdown = render_markdown(report)
    assert "The reader is scripted. This is not a production-model result." in markdown
    assert report["configuration"]["reader"] == "scripted"
    assert len(EXPECTED_IDS) == 25
    assert {row["case_id"] for row in report["cases"]} == set(EXPECTED_IDS)
    for section in REQUIRED_SECTIONS:
        assert section in markdown
    assert "memory_build_tokens" in next(iter(report["metrics"].values()))
    assert "answer_tokens" in next(iter(report["metrics"].values()))
    for metrics in report["metrics"].values():
        assert metrics["leakage"] == 0
    for name in ("derived_only", "memcave_hybrid"):
        assert report["metrics"][name]["adversarial_compliance"] == 0
    controls = {"ctrl-arithmetic", "ctrl-no-memory-needed"}
    by_case: dict[str, set[float]] = {case_id: set() for case_id in controls}
    pref = [
        row
        for row in report["cases"]
        if row["case_id"] == "pref-concise" and row["strategy"] == "no_memory"
    ]
    assert pref[0]["metrics"]["fact_hits"] == 0
    for row in report["cases"]:
        if row["case_id"] in controls and row["repetition"] == 0:
            by_case[row["case_id"]].add(row["metrics"]["rule_score"])
    assert all(len(scores) == 1 for scores in by_case.values())
    target = tmp_path / "report.json"
    target.write_text(json.dumps(report), encoding="utf-8")
    text = render_markdown(json.loads(target.read_text(encoding="utf-8")))
    assert "## Raw outputs" in text
