"""Unit tests for benchlab.csv_log — no hardware required.

Covers the column-mismatch bug (issue #11): a device's telemetry keys can
vary between polls, and the CSV writer must tolerate that instead of
crash-looping or corrupting output.
"""

import csv
import time

import pytest

from benchlab.csv_log.message_batcher import (
    BatchConfig,
    CSVBatchWriter,
    MessageBatcher,
)
from benchlab.csv_log.smart_retry import (
    RetryConfig,
    SmartRetryManager,
    SERIAL_RETRY_CONFIG,
)


# ---------------------------------------------------------------------------
# CSVBatchWriter: varying keys across messages
# ---------------------------------------------------------------------------

def test_writer_handles_extra_key_in_later_message(tmp_path):
    """A later message with an extra key must not raise or wedge the writer."""
    writer = CSVBatchWriter(str(tmp_path), batch_size=10)

    writer.write_batch([{"Timestamp": "t1", "uid": "dev1", "SYS_Power": 10}])
    # Second message carries a key not present in the header.
    writer.write_batch(
        [{"Timestamp": "t2", "uid": "dev1", "SYS_Power": 11, "GPU_Power": 5}])
    writer.flush_all()

    files = list(tmp_path.glob("*.csv"))
    assert len(files) == 1
    with open(files[0], newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["SYS_Power"] == "10"
    assert rows[1]["SYS_Power"] == "11"
    assert "GPU_Power" not in rows[1]  # dropped, not a crash


def test_writer_handles_missing_key_in_later_message(tmp_path):
    """A later message missing a header key must leave that column blank."""
    writer = CSVBatchWriter(str(tmp_path), batch_size=10)

    writer.write_batch(
        [{"Timestamp": "t1", "uid": "dev1", "SYS_Power": 10, "GPU_Power": 5}])
    writer.write_batch([{"Timestamp": "t2", "uid": "dev1", "SYS_Power": 11}])
    writer.flush_all()

    files = list(tmp_path.glob("*.csv"))
    with open(files[0], newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[1]["SYS_Power"] == "11"
    assert rows[1]["GPU_Power"] == ""


def test_writer_does_not_wedge_buffer_on_schema_drift(tmp_path):
    """Regression: extra/missing keys used to raise inside _flush_device_buffer
    and push the batch back into the buffer forever."""
    writer = CSVBatchWriter(str(tmp_path), batch_size=1)

    writer.write_batch([{"Timestamp": "t1", "uid": "dev1", "A": 1}])
    writer.write_batch([{"Timestamp": "t2", "uid": "dev1", "A": 2, "B": 3}])

    stats = writer.get_stats()
    assert stats["total_buffered"] == 0  # nothing stuck after a schema change


# ---------------------------------------------------------------------------
# MessageBatcher: basic buffering/flush behavior
# ---------------------------------------------------------------------------

def test_batcher_flushes_at_batch_size():
    flushed = []
    batcher = MessageBatcher(BatchConfig(batch_size=3, flush_interval=999))
    batcher.set_flush_callback(flushed.append)
    try:
        for i in range(3):
            batcher.add_message({"i": i})
        time.sleep(0.05)
        assert flushed and len(flushed[0]) == 3
    finally:
        batcher.shutdown()


def test_batcher_drops_messages_when_buffer_full():
    batcher = MessageBatcher(
        BatchConfig(
            batch_size=1000,
            max_buffer_size=2,
            flush_interval=999))
    try:
        assert batcher.add_message({"i": 0}) is True
        assert batcher.add_message({"i": 1}) is True
        assert batcher.add_message({"i": 2}) is False  # buffer full, dropped
    finally:
        batcher.shutdown()


# ---------------------------------------------------------------------------
# smart_retry: importable and functional without pyserial assumptions
# ---------------------------------------------------------------------------

def test_serial_retry_config_has_retryable_exceptions():
    assert OSError in SERIAL_RETRY_CONFIG.retryable_exceptions


def test_retry_manager_retries_then_succeeds():
    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise OSError("transient")
        return "ok"

    manager = SmartRetryManager(
        RetryConfig(
            max_retries=3,
            base_delay=0.01,
            circuit_breaker_enabled=False))
    assert manager.execute(flaky) == "ok"
    assert attempts["n"] == 2


def test_retry_manager_raises_after_max_retries():
    def always_fails():
        raise OSError("nope")

    manager = SmartRetryManager(
        RetryConfig(
            max_retries=2,
            base_delay=0.01,
            circuit_breaker_enabled=False))
    with pytest.raises(OSError):
        manager.execute(always_fails)
