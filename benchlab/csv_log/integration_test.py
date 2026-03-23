"""
Integration test for the enhanced CSV logger system
Tests cross-platform compatibility and end-to-end functionality
"""

import unittest
import tempfile
import os
import time
import json
import threading
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import serial

# Import the enhanced components
from benchlab.csv_log.csv_logger_enhanced import EnhancedCSVLogger, LoggerConfig, DeviceConfig
from benchlab.csv_log.cross_platform_serial import CrossPlatformSerialManager
from benchlab.csv_log.smart_retry import SmartRetryManager, SERIAL_RETRY_CONFIG
from benchlab.csv_log.message_batcher import BatchingLogger, create_csv_batcher


class TestCrossPlatformCompatibility(unittest.TestCase):
    """Test cross-platform compatibility features"""
    
    def setUp(self):
        """Set up test environment"""
        self.temp_dir = tempfile.mkdtemp()
        self.config = LoggerConfig(
            interval=0.1,
            output_dir=self.temp_dir,
            buffer_size=2,
            format="csv",
            silent_mode=True
        )
    
    def tearDown(self):
        """Clean up test environment"""
        import shutil
        shutil.rmtree(self.temp_dir)
    
    @patch('platform.system')
    def test_windows_serial_config(self, mock_platform):
        """Test Windows-specific serial configuration"""
        mock_platform.return_value = 'Windows'
        
        manager = CrossPlatformSerialManager()
        config = manager.get_platform_config()
        
        self.assertEqual(config['timeout'], 2.0)  # Windows uses longer timeout
        self.assertEqual(config['write_timeout'], 2.0)
        self.assertTrue(config['exclusive'])
    
    @patch('platform.system')
    def test_linux_serial_config(self, mock_platform):
        """Test Linux-specific serial configuration"""
        mock_platform.return_value = 'Linux'
        
        manager = CrossPlatformSerialManager()
        config = manager.get_platform_config()
        
        self.assertEqual(config['timeout'], 1.0)
        self.assertEqual(config['write_timeout'], 1.0)
        self.assertTrue(config['exclusive'])
    
    @patch('platform.system')
    @patch('builtins.open')
    def test_embedded_linux_detection(self, mock_open, mock_platform):
        """Test embedded Linux system detection"""
        mock_platform.return_value = 'Linux'
        mock_open.return_value.__enter__.return_value.read.return_value = '''
        processor       : 0
        model name      : ARMv7 Processor rev 4 (v7l)
        BogoMIPS        : 38.40
        '''
        
        manager = CrossPlatformSerialManager()
        is_embedded = manager._is_embedded_linux()
        
        self.assertTrue(is_embedded)
    
    def test_serial_retry_config(self):
        """Test serial-specific retry configuration"""
        config = SERIAL_RETRY_CONFIG
        
        self.assertEqual(config.max_retries, 5)
        self.assertEqual(config.base_delay, 0.5)
        self.assertEqual(config.max_delay, 10.0)
        self.assertEqual(config.strategy.value, "jittered_exponential")
        self.assertTrue(config.jitter)
        self.assertEqual(config.jitter_factor, 0.2)
        
        # Check retryable exceptions
        self.assertIn(serial.SerialException, config.retryable_exceptions)
        self.assertIn(OSError, config.retryable_exceptions)
        self.assertIn(TimeoutError, config.retryable_exceptions)


class TestEnhancedLoggerIntegration(unittest.TestCase):
    """Test enhanced logger integration with new components"""
    
    def setUp(self):
        """Set up test environment"""
        self.temp_dir = tempfile.mkdtemp()
        self.config = LoggerConfig(
            interval=0.1,
            output_dir=self.temp_dir,
            buffer_size=2,
            format="csv",
            silent_mode=True,
            auto_select=True
        )
    
    def tearDown(self):
        """Clean up test environment"""
        import shutil
        shutil.rmtree(self.temp_dir)
    
    @patch('benchlab.csv_log.csv_logger_enhanced.get_benchlab_ports')
    @patch('benchlab.csv_log.csv_logger_enhanced.serial.Serial')
    @patch('benchlab.csv_log.csv_logger_enhanced.read_uid')
    @patch('benchlab.csv_log.csv_logger_enhanced.read_device')
    @patch('benchlab.csv_log.csv_logger_enhanced.read_sensors')
    @patch('benchlab.csv_log.csv_logger_enhanced.translate_sensor_struct')
    def test_enhanced_logger_with_batching(self, mock_translate, mock_read_sensors, 
                                          mock_read_device, mock_read_uid, 
                                          mock_serial, mock_get_ports):
        """Test enhanced logger with batching integration"""
        # Mock port discovery
        mock_get_ports.return_value = [{"port": "COM1"}]
        
        # Mock serial connection
        mock_serial_instance = Mock()
        mock_serial.return_value = mock_serial_instance
        
        # Mock device responses
        mock_read_uid.return_value = "test_device"
        mock_read_device.return_value = {"FwVersion": "1.0"}
        
        # Mock sensor data
        mock_read_sensors.return_value = Mock()
        mock_translate.return_value = {
            "SYS_Power": 100,
            "CPU_Power": 50,
            "GPU_Power": 30
        }
        
        # Create logger with batching
        logger = EnhancedCSVLogger(self.config)
        
        # Test that logger can be created and configured
        self.assertIsNotNone(logger)
        self.assertEqual(logger.config.interval, 0.1)
        self.assertEqual(logger.config.output_dir, self.temp_dir)
        self.assertEqual(logger.config.buffer_size, 2)
        
        # Test device discovery
        devices = logger.discover_devices()
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].uid, "test_device")
        
        # Test connection opening
        connections = logger.open_connections(devices)
        self.assertEqual(len(connections), 1)
        self.assertIn("test_device", connections)
    
    def test_batching_logger_integration(self):
        """Test batching logger integration"""
        batcher = create_csv_batcher(self.temp_dir, batch_size=3, flush_interval=1.0)
        
        # Add test messages
        test_messages = [
            {'timestamp': time.time(), 'uid': 'device1', 'SYS_Power': 100},
            {'timestamp': time.time(), 'uid': 'device1', 'SYS_Power': 101},
            {'timestamp': time.time(), 'uid': 'device2', 'SYS_Power': 200},
        ]
        
        # Add messages
        for message in test_messages:
            success = batcher.add_message(message)
            self.assertTrue(success)
        
        # Check metrics
        metrics = batcher.get_metrics()
        self.assertGreater(metrics.total_messages, 0)
        
        # Check performance summary
        summary = batcher.get_performance_summary()
        self.assertIn('buffer_utilization', summary)
        self.assertIn('total_messages', summary)
        
        # Shutdown
        batcher.shutdown()
        
        # Check that files were created
        csv_files = list(Path(self.temp_dir).glob("*.csv"))
        self.assertGreater(len(csv_files), 0)
    
    @patch('benchlab.csv_log.csv_logger_enhanced.get_benchlab_ports')
    @patch('benchlab.csv_log.csv_logger_enhanced.serial.Serial')
    @patch('benchlab.csv_log.csv_logger_enhanced.read_uid')
    @patch('benchlab.csv_log.csv_logger_enhanced.read_device')
    @patch('benchlab.csv_log.csv_logger_enhanced.read_sensors')
    @patch('benchlab.csv_log.csv_logger_enhanced.translate_sensor_struct')
    def test_full_integration_cycle(self, mock_translate, mock_read_sensors,
                                   mock_read_device, mock_read_uid,
                                   mock_serial, mock_get_ports):
        """Test full integration cycle with all enhanced features"""
        # Mock port discovery
        mock_get_ports.return_value = [{"port": "COM1"}, {"port": "COM2"}]
        
        # Mock serial connections
        mock_serial.side_effect = [Mock(), Mock()]
        
        # Mock device responses
        mock_read_uid.side_effect = ["device1", "device2"]
        mock_read_device.side_effect = [
            {"FwVersion": "1.0"},
            {"FwVersion": "2.0"}
        ]
        
        # Mock sensor data
        mock_read_sensors.side_effect = [Mock(), Mock()]
        mock_translate.side_effect = [
            {"SYS_Power": 100, "CPU_Power": 50},
            {"SYS_Power": 200, "CPU_Power": 100}
        ]
        
        # Create enhanced logger
        logger = EnhancedCSVLogger(self.config)
        
        # Test full cycle
        devices = logger.discover_devices()
        self.assertEqual(len(devices), 2)
        
        selected_devices = logger.select_devices(devices)
        self.assertEqual(len(selected_devices), 2)
        
        connections = logger.open_connections(selected_devices)
        self.assertEqual(len(connections), 2)
        
        # Initialize writers
        logger.initialize_writers(connections)
        
        # Check that files were created
        csv_files = list(Path(self.temp_dir).glob("*.csv"))
        self.assertEqual(len(csv_files), 2)
        
        # Test data logging
        for uid, ser in connections.items():
            success = logger.log_device_data(uid, ser)
            self.assertTrue(success)
        
        # Test buffer flushing
        for uid in connections.keys():
            logger.write_buffered_data(uid)
        
        # Cleanup
        logger.stop_logging(connections)
        
        # Verify files contain data
        for csv_file in csv_files:
            with open(csv_file, 'r') as f:
                content = f.read()
                self.assertIn("Timestamp", content)
                self.assertIn("SYS_Power", content)


class TestPerformanceOptimizations(unittest.TestCase):
    """Test performance optimizations"""
    
    def setUp(self):
        """Set up test environment"""
        self.temp_dir = tempfile.mkdtemp()
    
    def tearDown(self):
        """Clean up test environment"""
        import shutil
        shutil.rmtree(self.temp_dir)
    
    def test_buffer_performance(self):
        """Test buffer performance improvements"""
        batcher = create_csv_batcher(self.temp_dir, batch_size=100, flush_interval=60.0)
        
        # Generate test data
        test_messages = []
        for i in range(1000):
            test_messages.append({
                'timestamp': time.time(),
                'uid': f'device{i % 10}',
                'SYS_Power': 100 + i,
                'CPU_Power': 50 + i,
                'GPU_Power': 30 + i
            })
        
        # Time the batched writing
        start_time = time.time()
        success_count = batcher.add_messages(test_messages)
        end_time = time.time()
        
        batch_time = end_time - start_time
        
        # Shutdown to flush remaining data
        batcher.shutdown()
        
        # Check performance
        self.assertEqual(success_count, 1000)
        self.assertLess(batch_time, 5.0)  # Should be fast
        
        # Check that files were created
        csv_files = list(Path(self.temp_dir).glob("*.csv"))
        self.assertGreater(len(csv_files), 0)
    
    def test_retry_performance(self):
        """Test retry mechanism performance"""
        config = SERIAL_RETRY_CONFIG
        manager = SmartRetryManager(config)
        
        # Mock a function that fails then succeeds
        call_count = 0
        def mock_function():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise serial.SerialException("Simulated failure")
            return "success"
        
        # Time the retry operation
        start_time = time.time()
        result = manager.execute(mock_function)
        end_time = time.time()
        
        retry_time = end_time - start_time
        
        # Should succeed after retries
        self.assertEqual(result, "success")
        self.assertEqual(call_count, 3)  # Should have retried twice
        self.assertLess(retry_time, 2.0)  # Should be reasonably fast


class TestErrorHandling(unittest.TestCase):
    """Test error handling and recovery"""
    
    def setUp(self):
        """Set up test environment"""
        self.temp_dir = tempfile.mkdtemp()
    
    def tearDown(self):
        """Clean up test environment"""
        import shutil
        shutil.rmtree(self.temp_dir)
    
    def test_buffer_overflow_handling(self):
        """Test buffer overflow handling"""
        batcher = create_csv_batcher(self.temp_dir, batch_size=1000, flush_interval=1.0)
        
        # Create a very large number of messages
        test_messages = []
        for i in range(15000):  # Exceed max buffer size
            test_messages.append({
                'timestamp': time.time(),
                'uid': 'device1',
                'SYS_Power': 100 + i
            })
        
        # Add messages - some should be dropped
        success_count = batcher.add_messages(test_messages)
        
        # Should have dropped some messages due to buffer overflow
        self.assertLess(success_count, 15000)
        self.assertGreater(success_count, 0)
        
        batcher.shutdown()
    
    def test_file_write_error_recovery(self):
        """Test recovery from file write errors"""
        batcher = create_csv_batcher(self.temp_dir, batch_size=2, flush_interval=1.0)
        
        # Mock a write error
        with patch('builtins.open', side_effect=PermissionError("Permission denied")):
            test_message = {
                'timestamp': time.time(),
                'uid': 'device1',
                'SYS_Power': 100
            }
            
            # Should handle the error gracefully
            success = batcher.add_message(test_message)
            self.assertTrue(success)  # Should still accept the message
        
        batcher.shutdown()


class TestConfiguration(unittest.TestCase):
    """Test configuration system"""
    
    def setUp(self):
        """Set up test environment"""
        self.temp_dir = tempfile.mkdtemp()
        self.config_file = os.path.join(self.temp_dir, "test_config.config")
    
    def tearDown(self):
        """Clean up test environment"""
        import shutil
        shutil.rmtree(self.temp_dir)
    
    def test_config_file_loading(self):
        """Test configuration file loading"""
        config_content = """
[logger]
interval = 0.5
output_dir = /test/logs
buffer_size = 50
format = json
silent_mode = true
auto_select = true

[advanced]
debug_mode = true
log_level = DEBUG
"""
        
        with open(self.config_file, 'w') as f:
            f.write(config_content)
        
        from benchlab.csv_log.csv_logger_enhanced import load_config
        config = load_config(self.config_file)
        
        self.assertEqual(config.interval, 0.5)
        self.assertEqual(config.output_dir, "/test/logs")
        self.assertEqual(config.buffer_size, 50)
        self.assertEqual(config.format, "json")
        self.assertTrue(config.silent_mode)
        self.assertTrue(config.auto_select)
    
    def test_environment_variable_override(self):
        """Test environment variable configuration override"""
        config_content = """
[logger]
interval = 2.0
output_dir = /config/logs
silent_mode = false
"""
        
        with open(self.config_file, 'w') as f:
            f.write(config_content)
        
        # Set environment variables
        os.environ['CSV_LOG_INTERVAL'] = '0.1'
        os.environ['CSV_LOG_OUTPUT_DIR'] = '/env/logs'
        os.environ['CSV_LOG_SILENT'] = 'true'
        
        try:
            from benchlab.csv_log.csv_logger_enhanced import load_config
            config = load_config(self.config_file)
            
            # Environment variables should override config file
            self.assertEqual(config.interval, 0.1)
            self.assertEqual(config.output_dir, "/env/logs")
            self.assertTrue(config.silent_mode)
            
            # Config file values should still be used where not overridden
            self.assertEqual(config.buffer_size, 100)  # Default value
            
        finally:
            # Clean up environment variables
            del os.environ['CSV_LOG_INTERVAL']
            del os.environ['CSV_LOG_OUTPUT_DIR']
            del os.environ['CSV_LOG_SILENT']


if __name__ == '__main__':
    # Run integration tests
    print("Running Enhanced CSV Logger Integration Tests")
    print("=" * 50)
    
    # Create test suite
    test_suite = unittest.TestSuite()
    
    # Add test classes
    test_classes = [
        TestCrossPlatformCompatibility,
        TestEnhancedLoggerIntegration,
        TestPerformanceOptimizations,
        TestErrorHandling,
        TestConfiguration
    ]
    
    for test_class in test_classes:
        tests = unittest.TestLoader().loadTestsFromTestCase(test_class)
        test_suite.addTests(tests)
    
    # Run tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(test_suite)
    
    # Print summary
    print("\n" + "=" * 50)
    print("Integration Test Summary")
    print("=" * 50)
    print(f"Tests run: {result.testsRun}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    
    if result.failures:
        print("\nFailures:")
        for test, traceback in result.failures:
            print(f"  - {test}: {traceback}")
    
    if result.errors:
        print("\nErrors:")
        for test, traceback in result.errors:
            print(f"  - {test}: {traceback}")
    
    if result.wasSuccessful():
        print("\nAll integration tests passed! ✓")
    else:
        print("\nSome integration tests failed! ✗")
        exit(1)