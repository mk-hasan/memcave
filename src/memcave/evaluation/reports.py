"""JSON and Markdown benchmark reports."""

from typing import Any


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Benchmark report",
        "",
        "The reader is scripted. This is not a production-model result.",
        "",
        "## Run configuration",
        "",
        f"- package: {report['versions']['package']}",
        f"- python: {report['versions']['python']}",
        f"- reader: {report['configuration']['reader']}",
        f"- provider: {report['configuration']['provider']}",
        f"- temperature: {report['configuration']['temperature']}",
        f"- seed: {report['configuration']['seed']}",
        f"- deterministic: {report['configuration']['deterministic']}",
        f"- fixture hash: {report['fixture_hashes']['catalog.json']}",
        "",
        "## Strategy metrics",
        "",
        "| strategy | rule score | leakage | adversarial | answer tokens | build tokens |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for name, metrics in report["metrics"].items():
        lines.append(
            "| {name} | {rule:.3f} | {leak:.3f} | {adv:.3f} | {answer} | {build} |".format(
                name=name,
                rule=metrics["rule_score"],
                leak=metrics["leakage"],
                adv=metrics["adversarial_compliance"],
                answer=metrics["answer_tokens"],
                build=metrics["memory_build_tokens"],
            )
        )
    lines.extend(["", "## Bootstrap intervals", ""])
    for item in report["bootstrap"]:
        claim = (
            "interval excludes 0"
            if item["states_difference"]
            else "interval includes no difference"
        )
        lines.append(
            f"- {item['left']} minus {item['right']}: "
            f"[{item['low']:.3f}, {item['high']:.3f}] ({claim})"
        )
    lines.extend(
        [
            "",
            "These intervals describe 25 synthetic cases read by a deterministic script. "
            "They are not a general ranking and they do not use an LLM judge.",
            "",
            "## Build cost versus answer cost",
            "",
            "Memory-build tokens count dreamflow and embedding work. "
            "Answer tokens count only the question-answering call. "
            "The two are not added together.",
            "",
        ]
    )
    for name, metrics in report["metrics"].items():
        lines.append(
            f"- {name}: build {metrics['memory_build_tokens']} tokens, "
            f"answer {metrics['answer_tokens']} tokens"
        )
    lines.extend(["", "## Isolation and adversarial", ""])
    for name, metrics in report["metrics"].items():
        lines.append(
            f"- {name}: leakage {metrics['leakage']:.3f}, "
            f"adversarial compliance {metrics['adversarial_compliance']:.3f}"
        )
    lines.extend(
        [
            "",
            "## Judge scores",
            "",
            "Judge scores were unused. An optional judge would not be sufficient "
            "to rank strategies.",
            "",
            "## Raw outputs",
            "",
        ]
    )
    for row in report["cases"]:
        lines.append(f"### {row['strategy']} / {row['case_id']}")
        lines.append("")
        lines.append(row["answer"] or "(empty)")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


REQUIRED_SECTIONS = (
    "## Run configuration",
    "## Strategy metrics",
    "## Bootstrap intervals",
    "## Build cost versus answer cost",
    "## Isolation and adversarial",
    "## Judge scores",
    "## Raw outputs",
)
