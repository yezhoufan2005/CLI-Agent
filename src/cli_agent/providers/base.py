"""Model-provider interface and shared retry policy."""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Protocol

from cli_agent.messages import Completion, Message


@dataclass(frozen=True)
class ToolSpec:
    """A tool as advertised to the model."""

    name: str
    description: str
    input_schema: dict[str, Any]


class ProviderError(Exception):
    """A model call failed. `retryable` tells the retry loop whether to try again."""

    def __init__(self, message: str, *, retryable: bool = False, retry_after: float | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after


class Provider(Protocol):
    name: str
    model: str

    def complete(
        self,
        *,
        system: str,
        messages: list[Message],
        tools: list[ToolSpec],
        max_tokens: int,
        on_text: Callable[[str], None] | None = None,
    ) -> Completion: ...


# HTTP statuses worth retrying: rate limits, timeouts, server errors, overload (529).
RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504, 529})


@dataclass
class RetryPolicy:
    max_attempts: int = 6
    base_delay: float = 1.0
    max_delay: float = 30.0

    def delay(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return float(min(max(retry_after, 0.0), self.max_delay))
        exp = self.base_delay * 2.0 ** (attempt - 1)
        return min(exp, self.max_delay) * (0.5 + random.random() / 2)  # noqa: S311 - jitter, not crypto


def with_retries(
    call: Callable[[], Completion],
    policy: RetryPolicy,
    *,
    sleep: Callable[[float], None] = time.sleep,
    on_retry: Callable[[int, ProviderError, float], None] | None = None,
) -> Completion:
    attempt = 0
    while True:
        attempt += 1
        try:
            return call()
        except ProviderError as err:
            if not err.retryable or attempt >= policy.max_attempts:
                raise
            wait = policy.delay(attempt, err.retry_after)
            if on_retry is not None:
                on_retry(attempt, err, wait)
            sleep(wait)


STREAM_DEADLINE = 900.0


def with_deadline(lines: Iterator[str], seconds: float = STREAM_DEADLINE) -> Iterator[str]:
    """Bound a whole stream. httpx timeouts apply per read, so a server that
    keeps sending keepalives (pings, comments) would otherwise never time out."""
    end = time.monotonic() + seconds
    for line in lines:
        if time.monotonic() > end:
            raise ProviderError(f"stream did not finish within {seconds:g}s", retryable=True)
        yield line


def parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None
