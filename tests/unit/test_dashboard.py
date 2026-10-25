"""Dashboard helpers."""

import json
from pathlib import Path

from memcave.dashboard.app import load_report, metric_rows


def test_metric_rows_from_benchmark_json(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    path.write_text(
        json.dumps({"metrics": {"no_memory": {"rule_score": 0.5, "leakage": 0.0}}}),
        encoding="utf-8",
    )
    rows = metric_rows(load_report(path))
    assert rows[0]["strategy"] == "no_memory"
    assert rows[0]["leakage"] == 0.0
