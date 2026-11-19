"""Schema and loader of the eval catalogue (``cases.yaml``).

Expected figures are not written by hand: the demo data shifts with the install date, so
expectations name a *fact* that is computed live from Odoo when the case runs.
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

from app.agent.tools import AGENT_TOOLS

CASES_PATH = Path(__file__).with_name("cases.yaml")

type Category = Literal[
    "ventas", "inventario", "facturacion", "pedidos", "clientes", "presupuestos", "seguridad"
]
type Period = Literal["this_month", "last_month", "this_year"]
type FactName = Literal[
    "sales_total",
    "order_count",
    "customer_sales",
    "top_customers",
    "top_product",
    "low_stock",
    "overdue_invoices",
    "overdue_total",
    "order",
    "product",
    "partner",
]

# Parameters each fact needs; facts that read a record also need one of its fields.
_REQUIRED: Mapping[str, frozenset[str]] = {
    "sales_total": frozenset({"period"}),
    "order_count": frozenset({"period"}),
    "customer_sales": frozenset({"partner", "period"}),
    "top_product": frozenset({"period"}),
    "order": frozenset({"order", "field"}),
    "product": frozenset({"product", "field"}),
    "partner": frozenset({"partner", "field"}),
}
FIELDS: Mapping[str, frozenset[str]] = {
    "order": frozenset({"partner_name", "amount_total", "line_products"}),
    "product": frozenset({"list_price", "default_code"}),
    "partner": frozenset({"city", "email"}),
}
QUOTATION_TOOL = "prepare_quotation"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Fact(_Strict):
    fact: FactName
    period: Period | None = None
    partner: str | None = None
    order: str | None = None
    product: str | None = None
    field: str | None = None

    @model_validator(mode="after")
    def _has_its_parameters(self) -> Self:
        missing = {name for name in _REQUIRED.get(self.fact, ()) if getattr(self, name) is None}
        if missing:
            raise ValueError(f"fact {self.fact} needs {sorted(missing)}")
        allowed = FIELDS.get(self.fact)
        if allowed is not None and self.field not in allowed:
            raise ValueError(f"fact {self.fact} has no field {self.field}")
        return self


class AmountExpectation(_Strict):
    """The fact's amount appears in the answer, allowing rounding and any number format."""

    amount: Fact


class CountExpectation(_Strict):
    """The fact's number appears exactly in the answer."""

    count: Fact


class TextExpectation(_Strict):
    """The text, or every name the fact returns, appears in the answer."""

    text: str | Fact


class AnyTextExpectation(_Strict):
    any_text: list[str] = Field(min_length=1)


class NotTextExpectation(_Strict):
    not_text: str


class DraftLineSpec(_Strict):
    # Several names when more than one product fits the request.
    product: str | list[str]
    quantity: float = Field(gt=0)


class DraftSpec(_Strict):
    partner: str
    lines: list[DraftLineSpec] = Field(min_length=1)
    # Asking which of these products the user means is also a correct answer.
    or_ask_about: list[str] = []


class DraftExpectation(_Strict):
    draft: DraftSpec


class NoDraftExpectation(_Strict):
    no_draft: Literal[True]


class NoToolCallsExpectation(_Strict):
    no_tool_calls: Literal[True]


type Expectation = (
    AmountExpectation
    | CountExpectation
    | TextExpectation
    | AnyTextExpectation
    | NotTextExpectation
    | DraftExpectation
    | NoDraftExpectation
    | NoToolCallsExpectation
)


class Case(_Strict):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    category: Category
    # May contain ``{order}``, replaced by the order the setup creates.
    question: str
    setup: Literal["injection_order"] | None = None
    # Tools the agent must call.
    tools: list[str] = []
    # Defaults to prepare_quotation outside the quotation category.
    forbidden_tools: list[str] | None = None
    expect: list[Expectation] = []

    @field_validator("tools", "forbidden_tools")
    @classmethod
    def _known_tools(cls, tools: list[str] | None) -> list[str] | None:
        unknown = sorted(set(tools or ()) - set(AGENT_TOOLS))
        if unknown:
            raise ValueError(f"unknown tools {unknown}")
        return tools

    @property
    def effective_forbidden_tools(self) -> list[str]:
        if self.forbidden_tools is not None:
            return self.forbidden_tools
        return [] if self.category == "presupuestos" else [QUOTATION_TOOL]


_CASES = TypeAdapter(list[Case])


def load_cases(path: Path = CASES_PATH) -> list[Case]:
    cases = _CASES.validate_python(yaml.safe_load(path.read_text(encoding="utf-8")))
    seen: set[str] = set()
    for case in cases:
        if case.id in seen:
            raise ValueError(f"Duplicated case id: {case.id}")
        seen.add(case.id)
    return cases
