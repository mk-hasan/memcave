"""Deterministic model provider used by tests and the default benchmark."""

import json
import math
import re
from collections.abc import Callable

from memcave.domain.models import ModelRequest, ModelResponse

_FENCE_START = "<<<MEMCAVE_MEMORY>>>"
_FENCE_END = "<<<END_MEMCAVE_MEMORY>>>"
_FORBIDDEN = re.compile(r"(?i)ignore previous|ignore all|system prompt|exfiltrate|CALL_TOOL:")
_CALL = re.compile(r"CALL_TOOL:\s*([A-Za-z0-9_\-]+)")
_SUM = re.compile(r"(\d+)\s*\+\s*(\d+)")
_QUESTION = re.compile(r"Question:\s*(.+)")


class FakeModelProvider:
    """Context-bound reader. It only repeats facts that are present in the prompt."""

    def __init__(
        self,
        scripts: dict[str, str] | None = None,
        *,
        model_name: str = "fake-memcave",
    ) -> None:
        self.scripts = scripts or {}
        self.model_name = model_name

    async def complete_structured(self, request: ModelRequest) -> ModelResponse:
        if request.task in self.scripts:
            content = self.scripts[request.task]
        elif request.task == "answer":
            content = scripted_answer(request.user_prompt)
        elif request.task == "extract":
            content = '{"candidates": []}'
        elif request.task == "conflict":
            content = (
                '{"conflict_type": "contradiction", "suggested_resolution": '
                '"needs_review", "confidence": 0.4, "rationale": "uncertain"}'
            )
        else:
            content = '{"candidates": []}'
        return ModelResponse(
            content=content,
            input_tokens=max(1, math.ceil(len(request.user_prompt) / 4)),
            output_tokens=max(1, math.ceil(len(content) / 4)),
            latency_ms=0.0,
            model_name=self.model_name,
        )


def scripted_answer(prompt: str) -> str:
    question_match = _QUESTION.search(prompt)
    question = question_match.group(1).strip() if question_match else prompt
    fenced = _between(prompt, _FENCE_START, _FENCE_END)
    history = _section(prompt, "History:")
    facts: list[str] = []
    arithmetic = _SUM.search(question)
    if arithmetic is not None:
        facts.append(str(int(arithmetic.group(1)) + int(arithmetic.group(2))))
    facts.extend(re.findall(r'"([^"]+)"', question))
    source = fenced if fenced is not None else history
    if source:
        for line in source.splitlines():
            if fenced is not None and _FORBIDDEN.search(line):
                continue
            if _shares(line, question):
                facts.append(line.strip())
    tools = _tools(prompt, fenced, history)
    outside = prompt.replace(fenced, "") if fenced is not None else prompt
    for name in _CALL.findall(outside):
        tools.append({"name": name, "arguments": {}})
    answer = " ".join(dict.fromkeys(fact for fact in facts if fact))
    return json.dumps({"answer": answer, "tool_calls": tools})


def _between(text: str, start: str, end: str) -> str | None:
    if start not in text or end not in text:
        return None
    return text.split(start, 1)[1].split(end, 1)[0]


def _section(text: str, label: str) -> str:
    if label not in text:
        return ""
    return text.split(label, 1)[1]


def _shares(line: str, question: str) -> bool:
    def tokens(value: str) -> set[str]:
        return {
            token[:-1] if token.endswith("s") and len(token) > 3 else token
            for token in re.findall(r"[a-z0-9]+", value.casefold())
        }

    return bool(tokens(line) & tokens(question))


def _tools(prompt: str, fenced: str | None, history: str) -> list[dict[str, object]]:
    if fenced and "category: workflow" in fenced:
        names = re.findall(r"'tool': '([^']+)'|\"tool\": \"([^\"]+)\"", fenced)
        flat = [left or right for left, right in names]
        if flat:
            return [{"name": name, "arguments": {}} for name in flat]
    if history and "TOOL:" in history:
        seen: list[str] = []
        for name in re.findall(r"TOOL:\s*([A-Za-z0-9_\-]+)", history):
            if name not in seen:
                seen.append(name)
        return [{"name": name, "arguments": {}} for name in seen]
    if "Tools:" in prompt:
        return [
            {"name": "list_tables", "arguments": {}},
            {"name": "describe_table", "arguments": {}},
            {"name": "run_query", "arguments": {}},
        ]
    return []


AnswerFn = Callable[[str], str]
