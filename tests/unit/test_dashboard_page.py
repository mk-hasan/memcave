"""Streamlit page behavior. Skipped when the dashboard extra is absent."""

import json
from pathlib import Path

import pytest

pytest.importorskip("streamlit.testing.v1", reason="install memcave[dashboard] to run this test")

from streamlit.testing.v1 import AppTest


def test_dashboard_renders_uploaded_metrics(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(
            {
                "metrics": {
                    "memcave_hybrid": {
                        "rule_score": 0.5,
                        "leakage": 0.0,
                        "adversarial_compliance": 0.0,
                    }
                },
                "bootstrap": [],
            }
        ),
        encoding="utf-8",
    )
    script = Path(__file__).resolve().parents[2] / "src" / "memcave" / "dashboard" / "app.py"
    page = AppTest.from_file(script)
    page.run()
    assert page.title[0].value == "memcave benchmark"
    page.file_uploader[0].upload("report.json", report.read_bytes(), "application/json")
    page.run()
    table = str(page.dataframe[0].value)
    assert "memcave_hybrid" in table
    assert "leakage" in table
    headings = " ".join(item.value for item in page.subheader)
    assert "Isolation and adversarial" in headings
