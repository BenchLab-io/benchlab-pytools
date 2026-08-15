"""Simple retry utilities used across Benchlab data sources.

The library originally performed a single attempt to connect to a serial
port, FastAPI server or MQTT broker.  In production environments a
transient failure (e.g. the device is temporarily busy or the network
is momentarily unavailable) should not cause the whole tool to abort.

This module provides a lightweight ``RetryPolicy`` dataclass and a
``retry`` decorator that can be applied to any callable.  The decorator
retries the wrapped function according to the policy and logs each
attempt using the module‑level ``logger``.
"""

from __future__ import annotations

import time
import logging
from dataclasses import dataclass
from typing import Callable, TypeVar, Any, cast

logger = logging.getLogger("benchlab.core.retry")

T = TypeVar("T")


@dataclass
class RetryPolicy:
    """Configuration for retry behaviour.

    Attributes
    ----------
    max_retries: int
        Maximum number of attempts (the initial call counts as the first
        attempt). ``0`` means no retries.
    backoff_factor: float
        Multiplier applied to the base delay after each failure. The
        actual sleep time is ``base_delay * (backoff_factor ** attempt)``.
    base_delay: float
        Initial delay in seconds before the first retry.
    allowed_exceptions: tuple[type[BaseException], ...]
        Exceptions that trigger a retry. All other exceptions are raised
        immediately.
    """

    max_retries: int = 3
    backoff_factor: float = 2.0
    base_delay: float = 0.5
    allowed_exceptions: tuple[type[BaseException], ...] = (Exception,)


def retry(
        policy: RetryPolicy) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator that retries a function according to *policy*.

    The wrapped function is called up to ``policy.max_retries + 1``
    times.  If the function succeeds, its result is returned immediately.
    If it raises an exception listed in ``policy.allowed_exceptions`` the
    decorator sleeps for an exponentially‑increasing delay and retries.
    After exhausting all attempts the last exception is re‑raised.
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        def wrapper(*args: Any, **kwargs: Any) -> T:
            attempt = 0
            while True:
                try:
                    return cast(T, func(*args, **kwargs))
                except policy.allowed_exceptions as exc:
                    if attempt >= policy.max_retries:
                        logger.error(
                            f"{func.__name__} failed after "
                            f"{attempt + 1} attempts: {exc}"
                        )
                        raise
                    delay = policy.base_delay * \
                        (policy.backoff_factor ** attempt)
                    logger.warning(
                        f"{func.__name__} attempt {attempt + 1} failed "
                        f"({exc}); retrying in {delay:.2f}s"
                    )
                    time.sleep(delay)
                    attempt += 1
        return wrapper

    return decorator
