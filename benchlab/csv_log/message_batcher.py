"""
Message Batching System for BENCHLAB CSV Logger
Provides efficient buffering and batch processing for improved performance
"""

import time
import json
import csv
import threading
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime
import logging


@dataclass
class BatchConfig:
    """Configuration for message batching"""
    batch_size: int = 100
    flush_interval: float = 30.0      # seconds between automatic flushes
    # maximum messages in buffer before dropping
    max_buffer_size: int = 10000
    flush_on_shutdown: bool = True
    enable_metrics: bool = True


@dataclass
class BatchMetrics:
    """Metrics for batch processing performance"""
    total_batches: int = 0
    total_messages: int = 0
    total_flushes: int = 0
    avg_batch_size: float = 0.0
    avg_flush_time: float = 0.0
    last_flush_time: Optional[float] = None
    buffer_utilization: float = 0.0


class MessageBatcher:
    """Efficient message batching system with configurable strategies."""

    def __init__(self, config: BatchConfig = None):
        self.config = config or BatchConfig()
        self.buffer: List[Dict[str, Any]] = []
        self.buffer_lock = threading.Lock()
        # Single lock — no separate flush_lock to avoid deadlocks between
        # the background flush thread and manual flush() calls.
        self.metrics = BatchMetrics()
        self.logger = logging.getLogger(__name__)

        self.flush_callback: Optional[Callable] = None
        self.metrics_callback: Optional[Callable] = None

        self.shutdown_event = threading.Event()
        self.flush_thread = threading.Thread(
            target=self._flush_worker, daemon=True, name="BatcherFlush"
        )
        self.flush_thread.start()

    def add_message(self, message: Dict[str, Any]) -> bool:
        """Add a message to the batch buffer."""
        with self.buffer_lock:
            if len(self.buffer) >= self.config.max_buffer_size:
                self.logger.warning("Buffer full, dropping message")
                return False
            self.buffer.append(message)
            should_flush = len(self.buffer) >= self.config.batch_size

        if should_flush:
            self.flush()
        return True

    def flush(self) -> bool:
        """Flush the current buffer to the callback."""
        with self.buffer_lock:
            if not self.buffer:
                return True
            messages_to_flush = self.buffer.copy()
            self.buffer.clear()

        start_time = time.time()
        try:
            if self.flush_callback:
                self.flush_callback(messages_to_flush)

            elapsed = time.time() - start_time
            self.metrics.total_batches += 1
            self.metrics.total_messages += len(messages_to_flush)
            self.metrics.total_flushes += 1
            self.metrics.last_flush_time = elapsed
            if self.metrics.total_flushes > 0:
                n = self.metrics.total_flushes
                self.metrics.avg_flush_time = (
                    (self.metrics.avg_flush_time * (n - 1) + elapsed) / n
                )
            self.logger.debug(
                f"Flushed {
                    len(messages_to_flush)} messages in {
                    elapsed:.3f}s")
            return True

        except Exception as e:
            self.logger.error(f"Failed to flush batch: {e}")
            # Restore messages to buffer on failure
            with self.buffer_lock:
                self.buffer.extend(messages_to_flush)
            return False

    def set_flush_callback(
            self, callback: Callable[[List[Dict[str, Any]]], None]):
        self.flush_callback = callback

    def set_metrics_callback(self, callback: Callable[[BatchMetrics], None]):
        self.metrics_callback = callback

    def get_metrics(self) -> BatchMetrics:
        with self.buffer_lock:
            self.metrics.buffer_utilization = len(
                self.buffer) / self.config.max_buffer_size
        return self.metrics

    def _flush_worker(self):
        """Background worker for periodic flushing."""
        while not self.shutdown_event.wait(self.config.flush_interval):
            with self.buffer_lock:
                has_data = bool(self.buffer)
            if has_data:
                self.flush()

    def shutdown(self):
        """Shutdown the batcher and flush remaining messages."""
        self.shutdown_event.set()
        if self.config.flush_on_shutdown:
            self.flush()
        if self.flush_thread.is_alive():
            self.flush_thread.join(timeout=5.0)
        self.logger.info("Message batcher shutdown complete")


class CSVBatchWriter:
    """Batch writer for CSV files."""

    def __init__(
            self,
            output_dir: str,
            batch_size: int = 100,
            format: str = "csv"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.batch_size = batch_size
        self.format = format
        self.logger = logging.getLogger(__name__)

        self.active_files: Dict[str, Dict] = {}
        self.file_locks: Dict[str, threading.Lock] = {}
        self.buffers: Dict[str, List[Dict[str, Any]]] = {}
        self.buffer_locks: Dict[str, threading.Lock] = {}
        # Fieldnames fixed at file creation (from the first message's keys).
        # Later messages with different keys are still written safely: extra
        # keys are dropped, missing keys are left blank — this prevents a
        # transient schema change (e.g. a sensor briefly absent) from
        # crash-looping the writer or corrupting the CSV.
        self.fieldnames: Dict[str, List[str]] = {}

    def write_batch(self, messages: List[Dict[str, Any]]):
        """Write a batch of messages, grouped by device UID."""
        device_messages: Dict[str, List] = {}
        for message in messages:
            uid = message.get("uid", "unknown")
            device_messages.setdefault(uid, []).append(message)

        for uid, msgs in device_messages.items():
            self._write_device_batch(uid, msgs)

    def _write_device_batch(self, uid: str, messages: List[Dict[str, Any]]):
        if uid not in self.active_files:
            self._setup_device_file(uid, messages)

        lock = self.buffer_locks.setdefault(uid, threading.Lock())
        with lock:
            self.buffers.setdefault(uid, []).extend(messages)
            if len(self.buffers[uid]) >= self.batch_size:
                self._flush_device_buffer(uid)

    def _setup_device_file(self, uid: str, messages: List[Dict[str, Any]]):
        """Create the CSV file and write headers using the first message."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"log_{timestamp}_{uid}.{self.format}"
        filepath = self.output_dir / filename

        self.active_files[uid] = {
            "filepath": filepath,
            "headers_written": False,
        }
        self.file_locks[uid] = threading.Lock()
        self.buffer_locks[uid] = threading.Lock()

        # Write headers immediately using the first message so the file
        # exists on disk and is non-empty even before the first flush.
        if messages and self.format == "csv":
            headers = list(messages[0].keys())
            self.fieldnames[uid] = headers
            with open(filepath, "w", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=headers).writeheader()
            self.active_files[uid]["headers_written"] = True

    def _flush_device_buffer(self, uid: str):
        """Flush the in-memory buffer for a device to disk."""
        buf = self.buffers.get(uid)
        if not buf:
            return

        messages_to_write = buf.copy()
        buf.clear()

        file_info = self.active_files[uid]
        with self.file_locks[uid]:
            try:
                if self.format == "csv":
                    self._write_csv_batch(uid, file_info, messages_to_write)
                elif self.format == "json":
                    self._write_json_batch(file_info, messages_to_write)
                self.logger.debug(
                    f"Wrote {
                        len(messages_to_write)} rows for {uid}")
            except Exception as e:
                self.logger.error(f"Failed to write batch for {uid}: {e}")
                buf.extend(messages_to_write)  # restore on failure

    def _write_csv_batch(self, uid: str, file_info: Dict,
                         messages: List[Dict[str, Any]]):
        filepath = file_info["filepath"]
        headers = self.fieldnames.get(uid) or list(messages[0].keys())
        with open(filepath, "a", newline="", encoding="utf-8") as f:
            # extrasaction="ignore": drop keys not in the file's header (e.g. a
            # sensor that reappeared with a new field) instead of raising.
            # restval="": leave missing keys blank instead of raising.
            writer = csv.DictWriter(
                f,
                fieldnames=headers,
                extrasaction="ignore",
                restval="")
            if not file_info["headers_written"]:
                writer.writeheader()
                file_info["headers_written"] = True
            writer.writerows(messages)

    def _write_json_batch(self, file_info: Dict,
                          messages: List[Dict[str, Any]]):
        filepath = file_info["filepath"]
        with open(filepath, "a", encoding="utf-8") as f:
            for message in messages:
                f.write(json.dumps(message) + "\n")

    def flush_all(self):
        """Flush all device buffers to disk."""
        for uid in list(self.buffers.keys()):
            lock = self.buffer_locks.get(uid)
            if lock:
                with lock:
                    self._flush_device_buffer(uid)
            else:
                self._flush_device_buffer(uid)

    def get_stats(self) -> Dict[str, Any]:
        return {
            "active_devices": len(self.active_files),
            "total_buffered": sum(len(b) for b in self.buffers.values()),
            "buffer_sizes": {uid: len(b) for uid, b in self.buffers.items()},
        }


class BatchingLogger:
    """High-level batching logger combining MessageBatcher and
    CSVBatchWriter."""

    def __init__(
            self,
            output_dir: str,
            config: BatchConfig = None,
            format: str = "csv"):
        self.config = config or BatchConfig()
        self.format = format
        self.logger = logging.getLogger(__name__)

        self.writer = CSVBatchWriter(
            output_dir, self.config.batch_size, self.format)
        self.batcher = MessageBatcher(self.config)
        self.batcher.set_flush_callback(self.writer.write_batch)
        self.batcher.set_metrics_callback(self._on_metrics_update)
        self.metrics_history: List[BatchMetrics] = []

    def add_message(self, message: Dict[str, Any]) -> bool:
        return self.batcher.add_message(message)

    def add_messages(self, messages: List[Dict[str, Any]]) -> int:
        return sum(1 for m in messages if self.add_message(m))

    def flush(self) -> bool:
        """Flush all buffered messages to disk."""
        # flushes batcher buffer -> writer.write_batch()
        self.batcher.flush()
        self.writer.flush_all()     # flushes writer device buffers -> disk
        return True

    def set_flush_callback(self, callback: Callable):
        """Retained for API compatibility — callback is no longer needed."""
        pass

    def get_metrics(self) -> BatchMetrics:
        return self.batcher.get_metrics()

    def _on_metrics_update(self, metrics: BatchMetrics):
        self.metrics_history.append(metrics)
        if len(self.metrics_history) > 1000:
            self.metrics_history.pop(0)

    def get_performance_summary(self) -> Dict[str, Any]:
        metrics = self.get_metrics()
        return {
            "buffer_utilization": f"{metrics.buffer_utilization:.1%}",
            "total_messages": metrics.total_messages,
            "total_batches": metrics.total_batches,
            "avg_flush_time_ms": metrics.avg_flush_time * 1000,
            "writer_stats": self.writer.get_stats(),
        }

    def shutdown(self):
        """Shutdown the batching logger, flushing all remaining data."""
        self.batcher.shutdown()
        self.writer.flush_all()
        self.logger.info("Batching logger shutdown complete")


# ---------------------------------------------------------------------------
# Convenience factories
# ---------------------------------------------------------------------------

def create_csv_batcher(
    output_dir: str, batch_size: int = 100, flush_interval: float = 30.0
) -> BatchingLogger:
    config = BatchConfig(batch_size=batch_size, flush_interval=flush_interval)
    return BatchingLogger(output_dir, config, format="csv")


def create_json_batcher(
    output_dir: str, batch_size: int = 500, flush_interval: float = 60.0
) -> BatchingLogger:
    config = BatchConfig(batch_size=batch_size, flush_interval=flush_interval)
    return BatchingLogger(output_dir, config, format="json")


def create_high_frequency_batcher(
    output_dir: str, batch_size: int = 10, flush_interval: float = 5.0
) -> BatchingLogger:
    config = BatchConfig(
        batch_size=batch_size,
        flush_interval=flush_interval,
        max_buffer_size=1000)
    return BatchingLogger(output_dir, config, format="csv")
