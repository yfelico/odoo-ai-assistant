import pytest

from app.agent.drafts import DraftNotFoundError, DraftStore
from app.domain.models import QuotationLine

TTL = 900.0
CHAIRS = QuotationLine(
    product_id=5, product_name="Office Chair", quantity=10.0, price_unit=70.0, subtotal=700.0
)
LAMP = QuotationLine(
    product_id=6, product_name="Office Lamp", quantity=2.0, price_unit=40.5, subtotal=81.0
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_create_assigns_an_id_and_estimates_the_total() -> None:
    store = DraftStore(ttl_seconds=TTL)

    draft = store.create(partner_id=15, partner_name="Azure Interior", lines=(CHAIRS, LAMP))

    assert draft.id
    assert draft.partner_id == 15
    assert draft.lines == (CHAIRS, LAMP)
    assert draft.estimated_total_untaxed == pytest.approx(781.0)
    assert store.get(draft.id) == draft


def test_draft_ids_are_unique() -> None:
    store = DraftStore(ttl_seconds=TTL)

    first = store.create(partner_id=15, partner_name="Azure Interior", lines=(CHAIRS,))
    second = store.create(partner_id=15, partner_name="Azure Interior", lines=(CHAIRS,))

    assert first.id != second.id


def test_unknown_draft_raises() -> None:
    with pytest.raises(DraftNotFoundError):
        DraftStore(ttl_seconds=TTL).get("missing")


def test_draft_expires_after_ttl() -> None:
    clock = FakeClock()
    store = DraftStore(ttl_seconds=TTL, clock=clock)
    draft = store.create(partner_id=15, partner_name="Azure Interior", lines=(CHAIRS,))

    clock.now = TTL + 1

    with pytest.raises(DraftNotFoundError):
        store.take(draft.id)


def test_take_can_only_succeed_once() -> None:
    store = DraftStore(ttl_seconds=TTL)
    draft = store.create(partner_id=15, partner_name="Azure Interior", lines=(CHAIRS,))

    assert store.take(draft.id) == draft
    with pytest.raises(DraftNotFoundError):
        store.take(draft.id)


def test_restore_makes_a_taken_draft_available_again() -> None:
    store = DraftStore(ttl_seconds=TTL)
    draft = store.create(partner_id=15, partner_name="Azure Interior", lines=(CHAIRS,))
    store.take(draft.id)

    store.restore(draft)

    assert store.take(draft.id) == draft
