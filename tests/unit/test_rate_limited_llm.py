import pytest

from app.adapters.fake_llm import FakeLLM
from app.adapters.rate_limited_llm import RateLimitedLLM
from app.domain.models import ChatMessage

USER = [ChatMessage(role="user", content="hola")]


class FakeTime:
    """A clock whose sleep advances it, so waits are instant and observable."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _replies(count: int) -> FakeLLM:
    return FakeLLM([ChatMessage(role="assistant", content=str(n)) for n in range(count)])


def _limited(llm: FakeLLM, time: FakeTime, per_minute: int = 2) -> RateLimitedLLM:
    return RateLimitedLLM(llm, requests_per_minute=per_minute, clock=time.clock, sleep=time.sleep)


def test_passes_requests_and_replies_through() -> None:
    llm = _replies(1)

    reply = _limited(llm, FakeTime()).complete(USER, [], 12.0)

    assert reply.content == "0"
    assert llm.requests[0].messages == USER
    assert llm.requests[0].timeout == 12.0


def test_does_not_wait_within_the_limit() -> None:
    time = FakeTime()
    limited = _limited(_replies(2), time)

    limited.complete(USER, [], 5.0)
    time.now = 1.0
    limited.complete(USER, [], 5.0)

    assert time.sleeps == []
    assert limited.waited_seconds == 0.0


def test_waits_until_the_oldest_request_leaves_the_minute_window() -> None:
    time = FakeTime()
    limited = _limited(_replies(3), time)

    limited.complete(USER, [], 5.0)
    time.now = 10.0
    limited.complete(USER, [], 5.0)
    time.now = 15.0
    limited.complete(USER, [], 5.0)

    assert time.sleeps == [45.0]
    assert limited.waited_seconds == 45.0


def test_requests_after_a_quiet_minute_do_not_wait() -> None:
    time = FakeTime()
    limited = _limited(_replies(3), time)

    limited.complete(USER, [], 5.0)
    limited.complete(USER, [], 5.0)
    time.now = 61.0
    limited.complete(USER, [], 5.0)

    assert time.sleeps == []


def test_requires_a_positive_limit() -> None:
    with pytest.raises(ValueError, match="requests_per_minute"):
        RateLimitedLLM(_replies(0), requests_per_minute=0)
