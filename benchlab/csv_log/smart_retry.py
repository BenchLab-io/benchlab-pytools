"""
Smart Retry Logic for BENCHLAB CSV Logger
Provides intelligent retry strategies with exponential backoff, jitter,
and circuit breaker patterns
"""

import time
import random
import logging
import threading
from typing import Callable, Any, Optional, Dict, List, Union
from enum import Enum
from dataclasses import dataclass
from functools import wraps

try:
    import serial
    _SERIAL_EXCEPTIONS: List[type] = [
        OSError, serial.SerialException, TimeoutError]
except ImportError:
    serial = None
    _SERIAL_EXCEPTIONS = [OSError, TimeoutError]


class RetryStrategy(Enum):
    """Retry strategy types"""
    EXPONENTIAL_BACKOFF = "exponential_backoff"
    LINEAR_BACKOFF = "linear_backoff"
    FIXED_DELAY = "fixed_delay"
    JITTERED_EXPONENTIAL = "jittered_exponential"


class CircuitState(Enum):
    """Circuit breaker states"""
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Circuit is open, failing fast
    HALF_OPEN = "half_open"  # Testing if circuit can be closed


@dataclass
class RetryConfig:
    """Configuration for retry logic"""
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    strategy: RetryStrategy = RetryStrategy.EXPONENTIAL_BACKOFF
    jitter: bool = True
    jitter_factor: float = 0.1
    timeout: Optional[float] = None
    retryable_exceptions: List[type] = None
    circuit_breaker_enabled: bool = True
    circuit_failure_threshold: int = 5
    circuit_recovery_timeout: float = 60.0


class CircuitBreaker:
    """Circuit breaker implementation for preventing cascading failures"""

    def __init__(self, config: RetryConfig):
        self.config = config
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = 0.0
        self.lock = threading.Lock()
        self.logger = logging.getLogger(__name__)

    def call(self, func: Callable, *args, **kwargs) -> Any:
        """Execute function with circuit breaker protection"""
        with self.lock:
            if self.state == CircuitState.OPEN:
                since_failure = time.time() - self.last_failure_time
                if since_failure > self.config.circuit_recovery_timeout:
                    self.logger.info(
                        "Circuit breaker: transitioning to half-open")
                    self.state = CircuitState.HALF_OPEN
                    self.success_count = 0
                else:
                    raise CircuitBreakerOpenError("Circuit breaker is open")

            try:
                result = func(*args, **kwargs)
                self._on_success()
                return result
            except Exception as e:
                self._on_failure()
                raise e

    def _on_success(self):
        """Handle successful operation"""
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= 3:  # Require 3 successes to close
                self.logger.info("Circuit breaker: transitioning to closed")
                self.state = CircuitState.CLOSED
                self.failure_count = 0
                self.success_count = 0

    def _on_failure(self):
        """Handle failed operation"""
        self.failure_count += 1
        self.last_failure_time = time.time()

        if (self.config.circuit_breaker_enabled and
                self.failure_count >= self.config.circuit_failure_threshold):
            self.logger.warning(
                f"Circuit breaker: opening after {
                    self.failure_count} failures")
            self.state = CircuitState.OPEN


class CircuitBreakerOpenError(Exception):
    """Exception raised when circuit breaker is open"""
    pass


class SmartRetryManager:
    """Smart retry manager with multiple retry strategies"""

    def __init__(self, config: RetryConfig = None):
        self.config = config or RetryConfig()
        self.circuit_breaker = CircuitBreaker(
            self.config) if self.config.circuit_breaker_enabled else None
        self.logger = logging.getLogger(__name__)

    def execute(self, func: Callable, *args, **kwargs) -> Any:
        """Execute function with smart retry logic"""
        last_exception = None

        for attempt in range(self.config.max_retries + 1):
            try:
                if self.circuit_breaker:
                    return self.circuit_breaker.call(func, *args, **kwargs)
                else:
                    return func(*args, **kwargs)

            except Exception as e:
                last_exception = e

                # Check if exception is retryable
                if not self._is_retryable_exception(e):
                    self.logger.error(f"Non-retryable exception: {e}")
                    raise e

                if attempt == self.config.max_retries:
                    self.logger.error(
                        f"Max retries ({
                            self.config.max_retries}) exceeded for {
                            func.__name__}")
                    raise e

                # Calculate delay
                delay = self._calculate_delay(attempt)

                self.logger.warning(
                    f"Attempt {attempt + 1}/{self.config.max_retries + 1} "
                    f"failed for {func.__name__}: {e}. "
                    f"Retrying in {delay:.2f}s"
                )

                time.sleep(delay)

        # This should never be reached, but just in case
        raise last_exception

    def _is_retryable_exception(self, exception: Exception) -> bool:
        """Check if exception is retryable"""
        if not self.config.retryable_exceptions:
            # If no specific exceptions defined, assume all are retryable
            # except for KeyboardInterrupt and SystemExit
            return not isinstance(exception, (KeyboardInterrupt, SystemExit))

        return any(isinstance(exception, exc_type)
                   for exc_type in self.config.retryable_exceptions)

    def _calculate_delay(self, attempt: int) -> float:
        """Calculate delay based on retry strategy"""
        if self.config.strategy == RetryStrategy.FIXED_DELAY:
            delay = self.config.base_delay

        elif self.config.strategy == RetryStrategy.LINEAR_BACKOFF:
            delay = self.config.base_delay * (attempt + 1)

        elif self.config.strategy in [
                RetryStrategy.EXPONENTIAL_BACKOFF,
                RetryStrategy.JITTERED_EXPONENTIAL]:
            delay = self.config.base_delay * (2 ** attempt)

            if (self.config.jitter and self.config.strategy ==
                    RetryStrategy.JITTERED_EXPONENTIAL):
                # Add jitter
                jitter_amount = delay * self.config.jitter_factor
                delay += random.uniform(-jitter_amount, jitter_amount)
                delay = max(0, delay)  # Ensure delay is not negative

        # Cap delay at max_delay
        return min(delay, self.config.max_delay)


def smart_retry(config: Union[RetryConfig, Dict, None] = None):
    """Decorator for applying smart retry logic to functions"""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Create config from dict if provided
            retry_config = config
            if isinstance(config, dict):
                retry_config = RetryConfig(**config)
            elif config is None:
                retry_config = RetryConfig()

            manager = SmartRetryManager(retry_config)
            return manager.execute(func, *args, **kwargs)
        return wrapper
    return decorator


class AdaptiveRetryManager:
    """Adaptive retry manager that learns from failure patterns"""

    def __init__(self, config: RetryConfig = None):
        self.config = config or RetryConfig()
        self.failure_history = []  # List of (timestamp, delay_used, succeeded)
        self.logger = logging.getLogger(__name__)
        self.lock = threading.Lock()

    def execute(self, func: Callable, *args, **kwargs) -> Any:
        """Execute function with adaptive retry logic"""
        last_exception = None

        for attempt in range(self.config.max_retries + 1):
            try:
                result = func(*args, **kwargs)
                self._record_success(attempt)
                return result

            except Exception as e:
                last_exception = e

                if not self._is_retryable_exception(e):
                    raise e

                if attempt == self.config.max_retries:
                    self.logger.error(
                        f"Max retries ({
                            self.config.max_retries}) exceeded for {
                            func.__name__}")
                    raise e

                # Calculate adaptive delay
                delay = self._calculate_adaptive_delay(attempt)

                self.logger.warning(
                    f"Attempt {attempt + 1}/{self.config.max_retries + 1} "
                    f"failed for {func.__name__}: {e}. "
                    f"Using adaptive delay: {delay:.2f}s"
                )

                self._record_failure(attempt, delay)
                time.sleep(delay)

        raise last_exception

    def _calculate_adaptive_delay(self, attempt: int) -> float:
        """Calculate delay based on historical failure patterns"""
        with self.lock:
            if not self.failure_history:
                # No history, use base strategy
                return self._calculate_base_delay(attempt)

            # Analyze recent failures
            # Last 10 failures
            recent_failures = [
                f for f in self.failure_history[-10:] if not f[2]]

            if not recent_failures:
                return self._calculate_base_delay(attempt)

            # Calculate average delay that failed
            avg_failed_delay = sum(
                f[1] for f in recent_failures) / len(recent_failures)

            # If recent failures had short delays, try longer delays
            if avg_failed_delay < self.config.base_delay * 2:
                base_delay = self.config.base_delay * 3
            else:
                base_delay = self.config.base_delay

            # Apply exponential backoff with the adapted base
            delay = base_delay * (2 ** attempt)
            return min(delay, self.config.max_delay)

    def _calculate_base_delay(self, attempt: int) -> float:
        """Calculate base delay using standard strategies"""
        if self.config.strategy == RetryStrategy.FIXED_DELAY:
            return self.config.base_delay
        elif self.config.strategy == RetryStrategy.LINEAR_BACKOFF:
            return self.config.base_delay * (attempt + 1)
        else:  # Exponential backoff
            delay = self.config.base_delay * (2 ** attempt)
            if self.config.jitter:
                jitter_amount = delay * self.config.jitter_factor
                delay += random.uniform(-jitter_amount, jitter_amount)
                delay = max(0, delay)
            return min(delay, self.config.max_delay)

    def _record_success(self, attempt: int):
        """Record a successful operation"""
        with self.lock:
            self.failure_history.append((time.time(), 0, True))
            # Keep only last 100 entries
            if len(self.failure_history) > 100:
                self.failure_history.pop(0)

    def _record_failure(self, attempt: int, delay: float):
        """Record a failed operation"""
        with self.lock:
            self.failure_history.append((time.time(), delay, False))
            # Keep only last 100 entries
            if len(self.failure_history) > 100:
                self.failure_history.pop(0)

    def _is_retryable_exception(self, exception: Exception) -> bool:
        """Check if exception is retryable"""
        if not self.config.retryable_exceptions:
            return not isinstance(exception, (KeyboardInterrupt, SystemExit))

        return any(isinstance(exception, exc_type)
                   for exc_type in self.config.retryable_exceptions)

    def get_stats(self) -> Dict:
        """Get retry statistics"""
        with self.lock:
            total_attempts = len(self.failure_history)
            successes = sum(
                1 for _, _, success in self.failure_history if success)
            failures = total_attempts - successes

            if total_attempts == 0:
                return {
                    'total_attempts': 0,
                    'success_rate': 0.0,
                    'avg_delay': 0.0,
                    'recent_failures': 0
                }

            recent_failures = [
                f for f in self.failure_history[-10:] if not f[2]]

            return {
                'total_attempts': total_attempts,
                'successes': successes,
                'failures': failures,
                'success_rate': successes / total_attempts,
                'avg_delay': sum(
                    delay for _,
                    delay,
                    _ in self.failure_history) / total_attempts,
                'recent_failures': len(recent_failures)}


# Pre-configured retry managers for common use cases
SERIAL_RETRY_CONFIG = RetryConfig(
    max_retries=5,
    base_delay=0.5,
    max_delay=10.0,
    strategy=RetryStrategy.JITTERED_EXPONENTIAL,
    jitter_factor=0.2,
    retryable_exceptions=_SERIAL_EXCEPTIONS,
)

NETWORK_RETRY_CONFIG = RetryConfig(
    max_retries=3,
    base_delay=1.0,
    max_delay=30.0,
    strategy=RetryStrategy.EXPONENTIAL_BACKOFF,
    circuit_breaker_enabled=True,
    circuit_failure_threshold=3,
    circuit_recovery_timeout=60.0
)

FILE_RETRY_CONFIG = RetryConfig(
    max_retries=3,
    base_delay=0.1,
    max_delay=5.0,
    strategy=RetryStrategy.LINEAR_BACKOFF,
    retryable_exceptions=[OSError, IOError, PermissionError]
)


# Convenience functions
def retry_serial_operation(func: Callable, *args, **kwargs) -> Any:
    """Execute function with serial-specific retry logic"""
    manager = SmartRetryManager(SERIAL_RETRY_CONFIG)
    return manager.execute(func, *args, **kwargs)


def retry_network_operation(func: Callable, *args, **kwargs) -> Any:
    """Execute function with network-specific retry logic"""
    manager = SmartRetryManager(NETWORK_RETRY_CONFIG)
    return manager.execute(func, *args, **kwargs)


def retry_file_operation(func: Callable, *args, **kwargs) -> Any:
    """Execute function with file-specific retry logic"""
    manager = SmartRetryManager(FILE_RETRY_CONFIG)
    return manager.execute(func, *args, **kwargs)


# Decorator shortcuts
retry_serial = smart_retry(SERIAL_RETRY_CONFIG)
retry_network = smart_retry(NETWORK_RETRY_CONFIG)
retry_file = smart_retry(FILE_RETRY_CONFIG)


if __name__ == '__main__':
    # Example usage and testing
    import serial

    # Example 1: Using the decorator
    @retry_serial
    def read_serial_port(port: str) -> str:
        """Example function that reads from a serial port"""
        ser = serial.Serial(port, timeout=1)
        try:
            return ser.readline().decode('utf-8').strip()
        finally:
            ser.close()

    # Example 2: Using the manager directly
    def write_to_file_safely(filename: str, data: str):
        """Example function that writes to a file with retry logic"""
        manager = SmartRetryManager(FILE_RETRY_CONFIG)

        def _write():
            with open(filename, 'w') as f:
                f.write(data)

        return manager.execute(_write)

    # Example 3: Using adaptive retry
    def network_request_with_adaptive_retry(url: str):
        """Example function using adaptive retry"""
        manager = AdaptiveRetryManager(NETWORK_RETRY_CONFIG)

        def _request():
            # Simulate network request
            import requests
            response = requests.get(url, timeout=5)
            return response.text

        return manager.execute(_request)

    print("Smart retry logic loaded successfully!")
    print("Available retry managers:")
    print("- SmartRetryManager: Standard smart retry")
    print("- AdaptiveRetryManager: Learning-based retry")
    print("- CircuitBreaker: Circuit breaker pattern")
    print("\nPre-configured retry configs:")
    print("- SERIAL_RETRY_CONFIG: For serial port operations")
    print("- NETWORK_RETRY_CONFIG: For network operations")
    print("- FILE_RETRY_CONFIG: For file operations")
    print("\nDecorators:")
    print("- @retry_serial: Decorate serial operations")
    print("- @retry_network: Decorate network operations")
    print("- @retry_file: Decorate file operations")
