from app.agent.conversations import ConversationStore
from app.domain.models import ChatMessage

TTL = 60.0
HISTORY = (ChatMessage(role="user", content="hola"), ChatMessage(role="assistant", content="Hola"))


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_unknown_conversation_has_empty_history() -> None:
    assert ConversationStore(ttl_seconds=TTL).history("missing") == ()


def test_saved_history_is_returned() -> None:
    store = ConversationStore(ttl_seconds=TTL)

    store.save("c1", HISTORY)

    assert store.history("c1") == HISTORY


def test_history_expires_after_ttl_without_activity() -> None:
    clock = FakeClock()
    store = ConversationStore(ttl_seconds=TTL, clock=clock)
    store.save("c1", HISTORY)

    clock.now = TTL - 1
    assert store.history("c1") == HISTORY
    clock.now = TTL + 1
    assert store.history("c1") == ()


def test_saving_renews_the_ttl() -> None:
    clock = FakeClock()
    store = ConversationStore(ttl_seconds=TTL, clock=clock)
    store.save("c1", HISTORY)

    clock.now = TTL - 1
    store.save("c1", HISTORY)
    clock.now = TTL + 1

    assert store.history("c1") == HISTORY


def test_new_ids_are_unique() -> None:
    store = ConversationStore(ttl_seconds=TTL)

    assert store.new_id() != store.new_id()
