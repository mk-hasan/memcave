"""Read a benchmark JSON report. Streamlit is imported only when the page runs."""

import json
from pathlib import Path
from typing import Any


def load_report(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("benchmark report must be a JSON object")
    return payload


def metric_rows(report: dict[str, Any]) -> list[dict[str, object]]:
    metrics = report.get("metrics", {})
    if not isinstance(metrics, dict):
        return []
    rows: list[dict[str, object]] = []
    for name, values in metrics.items():
        if isinstance(values, dict):
            rows.append({"strategy": name, **values})
    return rows


def main() -> None:
    import streamlit as st

    st.title("memcave benchmark")
    st.caption("Judge scores are unused. This view does not rank a winner.")
    uploaded = st.file_uploader("Benchmark JSON", type="json")
    if uploaded is None:
        st.info("Upload a report produced by memcave benchmark run.")
        return
    payload = json.loads(uploaded.getvalue().decode("utf-8"))
    if not isinstance(payload, dict):
        st.error("The file is not a benchmark object.")
        return
    st.subheader("Strategy metrics")
    st.dataframe(metric_rows(payload))
    st.subheader("Bootstrap intervals")
    st.json(payload.get("bootstrap", []))
    st.subheader("Isolation and adversarial")
    for row in metric_rows(payload):
        st.write(
            f"{row.get('strategy')}: leakage {row.get('leakage')}, "
            f"adversarial compliance {row.get('adversarial_compliance')}"
        )


if __name__ == "__main__":
    main()
