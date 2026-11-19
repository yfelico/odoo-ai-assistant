"""Eval report: accuracy, latency, tokens and estimated cost per model, as Markdown and JSON."""

import json
import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from app.domain.models import Usage

ANSWER_EXCERPT = 300


@dataclass(frozen=True, slots=True)
class EvalResult:
    model: str
    case_id: str
    category: str
    passed: bool
    failures: tuple[str, ...]
    # Agent time without the waits imposed by the rate limiter.
    latency_seconds: float
    usage: Usage
    tools: tuple[str, ...]
    answer: str


@dataclass(frozen=True, slots=True)
class ModelSummary:
    model: str
    cases: int
    passed: int
    accuracy: float
    by_category: dict[str, tuple[int, int]]
    mean_latency_seconds: float
    p95_latency_seconds: float
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    cost_per_case_usd: float | None


def summarise(results: Iterable[EvalResult]) -> list[ModelSummary]:
    by_model: dict[str, list[EvalResult]] = defaultdict(list)
    for result in results:
        by_model[result.model].append(result)
    return [_summary(model, rows) for model, rows in by_model.items()]


def _summary(model: str, rows: Sequence[EvalResult]) -> ModelSummary:
    passed = sum(row.passed for row in rows)
    categories: dict[str, list[bool]] = defaultdict(list)
    for row in rows:
        categories[row.category].append(row.passed)
    usage = sum((row.usage for row in rows), Usage())
    latencies = sorted(row.latency_seconds for row in rows)
    return ModelSummary(
        model=model,
        cases=len(rows),
        passed=passed,
        accuracy=passed / len(rows),
        by_category={name: (sum(ok), len(ok)) for name, ok in sorted(categories.items())},
        mean_latency_seconds=sum(latencies) / len(latencies),
        # Nearest-rank percentile: a value that was actually observed.
        p95_latency_seconds=latencies[math.ceil(0.95 * len(latencies)) - 1],
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cost_usd=usage.cost_usd,
        cost_per_case_usd=None if usage.cost_usd is None else usage.cost_usd / len(rows),
    )


def render_markdown(results: Sequence[EvalResult], generated_at: datetime) -> str:
    summaries = summarise(results)
    lines = [
        "# Eval report",
        "",
        f"Generated {generated_at:%Y-%m-%d %H:%M} UTC. Cost is estimated from public "
        "list prices (LiteLLM); n/d when the model has no known price.",
        "",
        "| Model | Accuracy | Mean latency | p95 latency | Tokens in / out "
        "| Cost (total) | Cost / case |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    lines += [
        f"| {s.model} | {s.passed}/{s.cases} ({s.accuracy:.0%}) "
        f"| {s.mean_latency_seconds:.1f} s | {s.p95_latency_seconds:.1f} s "
        f"| {s.input_tokens:,} / {s.output_tokens:,} "
        f"| {_usd(s.cost_usd)} | {_usd(s.cost_per_case_usd)} |"
        for s in summaries
    ]
    categories = sorted({c for s in summaries for c in s.by_category})
    lines += ["", "## Accuracy by category", ""]
    lines += ["| Model | " + " | ".join(categories) + " |"]
    lines += ["| --- |" + " --- |" * len(categories)]
    lines += [
        f"| {s.model} | "
        + " | ".join(_ratio(s.by_category.get(category)) for category in categories)
        + " |"
        for s in summaries
    ]
    lines += ["", "## Failures", ""]
    failed = [result for result in results if not result.passed]
    if not failed:
        lines.append("None.")
    for result in failed:
        excerpt = " ".join(result.answer.split())[:ANSWER_EXCERPT]
        lines += [
            f"- **{result.model} / {result.case_id}**: {'; '.join(result.failures)}",
            f"  - Tools: {', '.join(result.tools) or 'none'}",
            f"  - Answer: {excerpt}",
        ]
    return "\n".join(lines) + "\n"


def write_report(results: Sequence[EvalResult], directory: Path, now: datetime) -> Path:
    """Writes ``evals-<timestamp>.md`` and ``.json`` into ``directory``; returns the Markdown."""
    directory.mkdir(parents=True, exist_ok=True)
    markdown = directory / f"evals-{now:%Y%m%d-%H%M%S}.md"
    markdown.write_text(render_markdown(results, now), encoding="utf-8")
    data = {
        "generated_at": now.isoformat(),
        "summary": [asdict(summary) for summary in summarise(results)],
        "results": [asdict(result) for result in results],
    }
    markdown.with_suffix(".json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return markdown


def _usd(value: float | None) -> str:
    return "n/d" if value is None else f"${value:.4f}"


def _ratio(counts: tuple[int, int] | None) -> str:
    return "-" if counts is None else f"{counts[0]}/{counts[1]}"
