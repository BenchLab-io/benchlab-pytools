"""
Message Batching System for BENCHLAB CSV Logger
Provides efficient buffering and batch processing for improved performance
"""

import time
import json
import csv
import threading
from typing import List, Dict, Any, Optional, Callable, Union
from dataclasses import dataclass, asdict
from pathlib import Path
from datetime import datetime
import logging


@dataclass
class BatchConfig:
    """Configuration for message batching"""
    batch_size: int = 100
    flush_interval: float = 30.0  # seconds
    max_buffer_size: int = 10000  # maximum messages in buffer
    compression_enabled: bool = False
    compression_threshold: int = 1000  # minimum messages before compression
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
    """Efficient message batching system with configurable strategies"""
    
    def __init__(self, config: BatchConfig = None):
        self.config = config or BatchConfig()
        self.buffer: List[Dict[str, Any]] = []
        self.buffer_lock = threading.Lock()
        self.flush_lock = threading.Lock()
        self.metrics = BatchMetrics()
        self.logger = logging.getLogger(__name__)
        
        # Threading
        self.flush_thread: Optional[threading.Thread] = None
        self.shutdown_event = threading.Event()
        
        # Callbacks
        self.flush_callback: Optional[Callable] = None
        self.metrics_callback: Optional[Callable] = None
        
        # Start background flush thread
        self._start_flush_thread()
    
    def add_message(self, message: Dict[str, Any]) -> bool:
        """Add a message to the batch buffer"""
        with self.buffer_lock:
            if len(self.buffer) >= self.config.max_buffer_size:
                self.logger.warning("Buffer full, dropping message")
                return False
            
            self.buffer.append(message)
            self._update_metrics()
            
            # Check if we should trigger immediate flush
            if len(self.buffer) >= self.config.batch_size:
                self.flush()
        
        return True
    
    def flush(self) -> bool:
        """Flush the current buffer to the callback"""
        with self.flush_lock:
            with self.buffer_lock:
                if not self.buffer:
                    return True
                
                # Take a copy of the buffer and clear it
                messages_to_flush = self.buffer.copy()
                self.buffer.clear()
            
            # Process the batch
            start_time = time.time()
            try:
                if self.flush_callback:
                    self.flush_callback(messages_to_flush)
                
                # Update metrics
                self.metrics.total_batches += 1
                self.metrics.total_messages += len(messages_to_flush)
                self.metrics.total_flushes += 1
                
                flush_time = time.time() - start_time
                self.metrics.last_flush_time = flush_time
                
                # Update average flush time
                if self.metrics.total_flushes > 0:
                    self.metrics.avg_flush_time = (
                        (self.metrics.avg_flush_time * (self.metrics.total_flushes - 1) + flush_time) 
                        / self.metrics.total_flushes
                    )
                
                self.logger.debug(f"Flushed batch of {len(messages_to_flush)} messages in {flush_time:.3f}s")
                return True
                
            except Exception as e:
                self.logger.error(f"Failed to flush batch: {e}")
                # Put messages back in buffer on failure
                with self.buffer_lock:
                    self.buffer.extend(messages_to_flush)
                return False
    
    def set_flush_callback(self, callback: Callable[[List[Dict[str, Any]]], None]):
        """Set the callback function to handle flushed batches"""
        self.flush_callback = callback
    
    def set_metrics_callback(self, callback: Callable[[BatchMetrics], None]):
        """Set the callback function to receive metrics updates"""
        self.metrics_callback = callback
    
    def get_metrics(self) -> BatchMetrics:
        """Get current batch processing metrics"""
        with self.buffer_lock:
            self.metrics.buffer_utilization = len(self.buffer) / self.config.max_buffer_size
        
        return self.metrics
    
    def _update_metrics(self):
        """Update buffer utilization metrics"""
        if self.config.enable_metrics:
            with self.buffer_lock:
                self.metrics.buffer_utilization = len(self.buffer) / self.config.max_buffer_size
            
            if self.metrics_callback:
                self.metrics_callback(self.get_metrics())
    
    def _start_flush_thread(self):
        """Start the background flush thread"""
        self.flush_thread = threading.Thread(target=self._flush_worker, daemon=True)
        self.flush_thread.start()
    
    def _flush_worker(self):
        """Background worker for periodic flushing"""
        while not self.shutdown_event.is_set():
            # Wait for flush interval or shutdown
            if self.shutdown_event.wait(self.config.flush_interval):
                break
            
            # Check if we have messages to flush
            with self.buffer_lock:
                if len(self.buffer) > 0:
                    self.flush()
    
    def shutdown(self):
        """Shutdown the batcher and flush remaining messages"""
        self.shutdown_event.set()
        
        if self.config.flush_on_shutdown:
            self.flush()
        
        # Wait for flush thread to finish
        if self.flush_thread and self.flush_thread.is_alive():
            self.flush_thread.join(timeout=5.0)
        
        self.logger.info("Message batcher shutdown complete")


class CSVBatchWriter:
    """Batch writer for CSV files with optimized performance"""
    
    def __init__(self, output_dir: str, batch_size: int = 100, format: str = "csv"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.batch_size = batch_size
        self.format = format
        self.logger = logging.getLogger(__name__)
        
        # File management
        self.active_files: Dict[str, Dict] = {}  # uid -> file info
        self.file_locks: Dict[str, threading.Lock] = {}
        
        # Buffer management
        self.buffers: Dict[str, List[Dict[str, Any]]] = {}
        self.buffer_locks: Dict[str, threading.Lock] = {}
    
    def write_batch(self, messages: List[Dict[str, Any]]):
        """Write a batch of messages to appropriate files"""
        # Group messages by device UID
        device_messages = {}
        for message in messages:
            uid = message.get('uid', 'unknown')
            if uid not in device_messages:
                device_messages[uid] = []
            device_messages[uid].append(message)
        
        # Write each device's messages
        for uid, device_msgs in device_messages.items():
            self._write_device_batch(uid, device_msgs)
    
    def _write_device_batch(self, uid: str, messages: List[Dict[str, Any]]):
        """Write a batch of messages for a specific device"""
        # Ensure we have file and buffer setup for this device
        if uid not in self.active_files:
            self._setup_device_file(uid)
        
        file_info = self.active_files[uid]
        buffer_lock = self.buffer_locks[uid]
        
        with buffer_lock:
            # Add messages to buffer
            if uid not in self.buffers:
                self.buffers[uid] = []
            self.buffers[uid].extend(messages)
            
            # Write if buffer is full
            if len(self.buffers[uid]) >= self.batch_size:
                self._flush_device_buffer(uid)
    
    def _setup_device_file(self, uid: str):
        """Setup file and locks for a device"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"log_{timestamp}_{uid}.{self.format}"
        filepath = self.output_dir / filename
        
        # Create file with headers
        if self.format == "csv":
            with open(filepath, 'w', newline='', encoding='utf-8') as f:
                if messages:  # Use first message to determine headers
                    headers = list(messages[0].keys())
                    writer = csv.DictWriter(f, fieldnames=headers)
                    writer.writeheader()
        
        # Setup file info and locks
        self.active_files[uid] = {
            'filepath': filepath,
            'file_handle': None,
            'headers_written': False
        }
        self.file_locks[uid] = threading.Lock()
        self.buffer_locks[uid] = threading.Lock()
    
    def _flush_device_buffer(self, uid: str):
        """Flush buffer for a specific device"""
        if uid not in self.buffers or not self.buffers[uid]:
            return
        
        buffer_lock = self.buffer_locks[uid]
        file_lock = self.file_locks[uid]
        
        with buffer_lock:
            messages_to_write = self.buffers[uid].copy()
            self.buffers[uid].clear()
        
        with file_lock:
            file_info = self.active_files[uid]
            
            try:
                if self.format == "csv":
                    self._write_csv_batch(file_info, messages_to_write)
                elif self.format == "json":
                    self._write_json_batch(file_info, messages_to_write)
                
                self.logger.debug(f"Wrote {len(messages_to_write)} messages for device {uid}")
                
            except Exception as e:
                self.logger.error(f"Failed to write batch for device {uid}: {e}")
                # Put messages back in buffer on failure
                with buffer_lock:
                    self.buffers[uid].extend(messages_to_write)
    
    def _write_csv_batch(self, file_info: Dict, messages: List[Dict[str, Any]]):
        """Write CSV batch to file"""
        filepath = file_info['filepath']
        
        # Get headers from first message
        headers = list(messages[0].keys())
        
        # Write to file
        with open(filepath, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            
            # Write headers if not already written
            if not file_info['headers_written']:
                writer.writeheader()
                file_info['headers_written'] = True
            
            # Write messages
            for message in messages:
                writer.writerow(message)
    
    def _write_json_batch(self, file_info: Dict, messages: List[Dict[str, Any]]):
        """Write JSON batch to file"""
        filepath = file_info['filepath']
        
        with open(filepath, 'a', encoding='utf-8') as f:
            for message in messages:
                f.write(json.dumps(message) + '\n')
    
    def flush_all(self):
        """Flush all device buffers"""
        for uid in list(self.buffers.keys()):
            self._flush_device_buffer(uid)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get writer statistics"""
        stats = {
            'active_devices': len(self.active_files),
            'total_buffers': sum(len(buffer) for buffer in self.buffers.values()),
            'buffer_sizes': {uid: len(buffer) for uid, buffer in self.buffers.items()}
        }
        return stats


class BatchingLogger:
    """High-level batching logger that combines all batching features"""
    
    def __init__(self, output_dir: str, config: BatchConfig = None, format: str = "csv"):
        self.config = config or BatchConfig()
        self.format = format
        self.logger = logging.getLogger(__name__)
        
        # Setup components
        self.batcher = MessageBatcher(self.config)
        self.writer = CSVBatchWriter(output_dir, self.config.batch_size, self.format)
        
        # Connect components
        self.batcher.set_flush_callback(self.writer.write_batch)
        
        # Metrics tracking
        self.batcher.set_metrics_callback(self._on_metrics_update)
        self.metrics_history: List[BatchMetrics] = []
    
    def add_message(self, message: Dict[str, Any]) -> bool:
        """Add a message to the batching system"""
        return self.batcher.add_message(message)
    
    def add_messages(self, messages: List[Dict[str, Any]]) -> int:
        """Add multiple messages to the batching system"""
        success_count = 0
        for message in messages:
            if self.add_message(message):
                success_count += 1
        return success_count
    
    def flush(self) -> bool:
        """Flush all buffered messages"""
        self.writer.flush_all()
        return self.batcher.flush()
    
    def get_metrics(self) -> BatchMetrics:
        """Get current batching metrics"""
        return self.batcher.get_metrics()
    
    def _on_metrics_update(self, metrics: BatchMetrics):
        """Handle metrics updates"""
        self.metrics_history.append(metrics)
        
        # Keep only last 1000 metrics
        if len(self.metrics_history) > 1000:
            self.metrics_history.pop(0)
    
    def get_performance_summary(self) -> Dict[str, Any]:
        """Get performance summary"""
        metrics = self.get_metrics()
        
        summary = {
            'buffer_utilization': f"{metrics.buffer_utilization:.1%}",
            'total_messages': metrics.total_messages,
            'total_batches': metrics.total_batches,
            'avg_batch_size': metrics.avg_batch_size,
            'avg_flush_time_ms': metrics.avg_flush_time * 1000,
            'last_flush_time_ms': (metrics.last_flush_time or 0) * 1000,
            'writer_stats': self.writer.get_stats()
        }
        
        return summary
    
    def shutdown(self):
        """Shutdown the batching logger"""
        self.batcher.shutdown()
        self.writer.flush_all()
        self.logger.info("Batching logger shutdown complete")


# Convenience functions for common use cases
def create_csv_batcher(output_dir: str, batch_size: int = 100, flush_interval: float = 30.0) -> BatchingLogger:
    """Create a CSV batcher with default settings"""
    config = BatchConfig(
        batch_size=batch_size,
        flush_interval=flush_interval,
        max_buffer_size=10000
    )
    return BatchingLogger(output_dir, config, format="csv")


def create_json_batcher(output_dir: str, batch_size: int = 500, flush_interval: float = 60.0) -> BatchingLogger:
    """Create a JSON batcher with settings optimized for JSON"""
    config = BatchConfig(
        batch_size=batch_size,
        flush_interval=flush_interval,
        max_buffer_size=50000
    )
    return BatchingLogger(output_dir, config, format="json")


def create_high_frequency_batcher(output_dir: str, batch_size: int = 10, flush_interval: float = 5.0) -> BatchingLogger:
    """Create a batcher optimized for high-frequency logging"""
    config = BatchConfig(
        batch_size=batch_size,
        flush_interval=flush_interval,
        max_buffer_size=1000,
        flush_on_shutdown=True
    )
    return BatchingLogger(output_dir, config, format="csv")


if __name__ == '__main__':
    # Example usage
    import tempfile
    import time
    
    # Create a temporary directory for testing
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create a CSV batcher
        batcher = create_csv_batcher(temp_dir, batch_size=5, flush_interval=2.0)
        
        # Add some test messages
        test_messages = [
            {'timestamp': time.time(), 'uid': 'device1', 'SYS_Power': 100, 'CPU_Power': 50},
            {'timestamp': time.time(), 'uid': 'device1', 'SYS_Power': 101, 'CPU_Power': 51},
            {'timestamp': time.time(), 'uid': 'device2', 'SYS_Power': 200, 'CPU_Power': 100},
            {'timestamp': time.time(), 'uid': 'device2', 'SYS_Power': 201, 'CPU_Power': 101},
            {'timestamp': time.time(), 'uid': 'device1', 'SYS_Power': 102, 'CPU_Power': 52},
        ]
        
        print("Adding test messages...")
        for i, message in enumerate(test_messages):
            success = batcher.add_message(message)
            print(f"Message {i+1}: {'Success' if success else 'Failed'}")
        
        # Wait for flush
        print("Waiting for flush...")
        time.sleep(3.0)
        
        # Get performance summary
        summary = batcher.get_performance_summary()
        print("\nPerformance Summary:")
        for key, value in summary.items():
            print(f"  {key}: {value}")
        
        # Shutdown
        batcher.shutdown()
        
        # Check created files
        import os
        files = os.listdir(temp_dir)
        print(f"\nCreated files: {files}")
        
    print("Batching system test complete!")