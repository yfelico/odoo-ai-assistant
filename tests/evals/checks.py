"""Grades an agent turn against the expectations of an eval case. Deterministic: no LLM judge."""

import re
from collections.abc import Callable, Iterable, Mapping

from app.agent.loop import AgentTurn
from tests.evals.cases import (
    AmountExpectation,
    AnyTextExpectation,
    Case,
    CountExpectation,
    DraftExpectation,
    DraftSpec,
    Expectation,
    Fact,
    NoDraftExpectation,
    NoToolCallsExpectation,
    NotTextExpectation,
    TextExpectation,
)
from tests.evals.facts import FactError, FactValue, plain_name

type Resolve = Callable[[Fact, AgentTurn], FactValue]

NUMBER = re.compile(r"\d[\d.,\s]*\d|\d")
SPACES = re.compile(r"\s")
RELATIVE_TOLERANCE = 0.005


def check(case: Case, turn: AgentTurn, resolve: Resolve) -> list[str]:
    """Returns the failed expectations of the case; empty when the turn passes."""
    failures: list[str] = []
    if turn.stop_reason != "answered":
        failures.append(f"stopped by {turn.stop_reason}")
    used = [call.name for call in turn.tool_calls]
    failures += [f"missing tool {tool}" for tool in case.tools if tool not in used]
    failures += [f"forbidden tool {t}" for t in case.effective_forbidden_tools if t in used]
    for expectation in case.expect:
        try:
            failures += _CHECKS[type(expectation)](expectation, turn, resolve)
        except FactError as err:
            fact = _fact_of(expectation)
            failures.append(f"fact {fact} unavailable: {err}")
    return failures


def _check_amount(expectation: Expectation, turn: AgentTurn, resolve: Resolve) -> list[str]:
    assert isinstance(expectation, AmountExpectation)
    amount = _number(resolve(expectation.amount, turn))
    tolerance = max(1.0, abs(amount) * RELATIVE_TOLERANCE)
    if any(abs(value - amount) <= tolerance for value in numbers(turn.answer)):
        return []
    return [f"amount {amount:g} not found"]


def _check_count(expectation: Expectation, turn: AgentTurn, resolve: Resolve) -> list[str]:
    assert isinstance(expectation, CountExpectation)
    count = _number(resolve(expectation.count, turn))
    return [] if count in numbers(turn.answer) else [f"count {count:g} not found"]


def _check_text(expectation: Expectation, turn: AgentTurn, resolve: Resolve) -> list[str]:
    assert isinstance(expectation, TextExpectation)
    if isinstance(expectation.text, str):
        texts = [expectation.text]
    else:
        value = resolve(expectation.text, turn)
        if not isinstance(value, list):
            raise FactError(f"{expectation.text.fact} is not a list of names")
        texts = [plain_name(name) for name in value]
    answer = _normalise(turn.answer)
    return [f"text {text!r} not found" for text in texts if _normalise(text) not in answer]


def _check_any_text(expectation: Expectation, turn: AgentTurn, _: Resolve) -> list[str]:
    assert isinstance(expectation, AnyTextExpectation)
    answer = _normalise(turn.answer)
    if any(_normalise(text) in answer for text in expectation.any_text):
        return []
    return [f"none of {expectation.any_text} found"]


def _check_not_text(expectation: Expectation, turn: AgentTurn, _: Resolve) -> list[str]:
    assert isinstance(expectation, NotTextExpectation)
    if _normalise(expectation.not_text) in _normalise(turn.answer):
        return [f"forbidden text {expectation.not_text!r} found"]
    return []


def _check_draft(expectation: Expectation, turn: AgentTurn, _: Resolve) -> list[str]:
    assert isinstance(expectation, DraftExpectation)
    spec = expectation.draft
    draft = turn.pending_draft
    if draft is None:
        if spec.or_ask_about and _mentions_all(turn.answer, spec.or_ask_about):
            return []
        return ["no draft prepared"]
    failures = []
    if _normalise(draft.partner_name) != _normalise(spec.partner):
        failures.append(f"draft partner {draft.partner_name!r} is not {spec.partner!r}")
    lines = [(line.product_name, float(line.quantity)) for line in draft.lines]
    if not _lines_match(spec, lines):
        failures.append(f"draft lines {lines} do not match")
    return failures


def _check_no_draft(expectation: Expectation, turn: AgentTurn, _: Resolve) -> list[str]:
    assert isinstance(expectation, NoDraftExpectation)
    return [] if turn.pending_draft is None else ["unexpected draft"]


def _check_no_tool_calls(expectation: Expectation, turn: AgentTurn, _: Resolve) -> list[str]:
    assert isinstance(expectation, NoToolCallsExpectation)
    used = [call.name for call in turn.tool_calls]
    return [f"unexpected tool calls {used}"] if used else []


_CHECKS: Mapping[type, Callable[[Expectation, AgentTurn, Resolve], list[str]]] = {
    AmountExpectation: _check_amount,
    CountExpectation: _check_count,
    TextExpectation: _check_text,
    AnyTextExpectation: _check_any_text,
    NotTextExpectation: _check_not_text,
    DraftExpectation: _check_draft,
    NoDraftExpectation: _check_no_draft,
    NoToolCallsExpectation: _check_no_tool_calls,
}


def numbers(text: str) -> set[float]:
    """Every reading of the numbers in ``text`` under both decimal conventions."""
    values: set[float] = set()
    for match in NUMBER.findall(text):
        raw = SPACES.sub("", match).rstrip(".,")
        for thousands, decimal in ((",", "."), (".", ",")):
            candidate = raw.replace(thousands, "").replace(decimal, ".")
            try:
                values.add(float(candidate))
            except ValueError:
                continue
    return values


def _number(value: FactValue) -> float:
    if isinstance(value, list):
        raise FactError("expected a number, got names")
    return value


def _normalise(text: str) -> str:
    # Markdown escapes (gemini\_furniture) must not hide a match.
    return text.replace("\\", "").casefold()


def _mentions_all(answer: str, names: Iterable[str]) -> bool:
    normalised = _normalise(answer)
    return all(_normalise(plain_name(name)) in normalised for name in names)


def _lines_match(spec: DraftSpec, lines: list[tuple[str, float]]) -> bool:
    if len(lines) != len(spec.lines):
        return False
    remaining = list(lines)
    for expected in spec.lines:
        options = [expected.product] if isinstance(expected.product, str) else expected.product
        match = next(
            (line for line in remaining if line[0] in options and line[1] == expected.quantity),
            None,
        )
        if match is None:
            return False
        remaining.remove(match)
    return True


def _fact_of(expectation: Expectation) -> str:
    for value in expectation.model_dump().values():
        if isinstance(value, dict) and "fact" in value:
            return str(value["fact"])
    return "?"
