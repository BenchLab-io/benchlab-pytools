"""
Test suite for Enhanced CSV Logger
"""

import unittest
import tempfile
import os
import time
import json
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from benchlab.csv_log.csv_logger_enhanced import (
    EnhancedCSVLogger, 
    LoggerConfig, 
    DeviceConfig,
    load_config
)


class TestDeviceConfig(unittest.TestCase):
    """Test DeviceConfig dataclass"""
    
    def test_device_config_creation(self):
        """Test creating a device configuration"""
        config = DeviceConfig(
            port="COM1",
            uid="test123",
            firmware="1.0.0"
        )
        
        self.assertEqual(config.port, "COM1")
        self.assertEqual(config.uid, "test123")
        self.assertEqual(config.firmware, "1.0.0")
        self.assertTrue(config.enabled)
        self.assertIsNone(config.last_seen)


class TestLoggerConfig(unittest.TestCase):
    """Test LoggerConfig dataclass"""
    
    def test_default_config(self):
        """Test default configuration values"""
        config = LoggerConfig()
        
        self.assertEqual(config.interval, 1.0)
        self.assertEqual(config.output_dir, "logs")
        self.assertEqual(config.buffer_size, 100)
        self.assertEqual(config.retry_attempts, 3)
        self.assertEqual(config.retry_delay, 1.0)
        self.assertEqual(config.format, "csv")
        self.assertFalse(config.silent_mode)
        self.assertFalse(config.auto_select)
    
    def test_custom_config(self):
        """Test custom configuration values"""
        config = LoggerConfig(
            interval=0.5,
            output_dir="/custom/logs",
            buffer_size=50,
            format="json",
            silent_mode=True,
            auto_select=True
        )
        
        self.assertEqual(config.interval, 0.5)
        self.assertEqual(config.output_dir, "/custom/logs")
        self.assertEqual(config.buffer_size, 50)
        self.assertEqual(config.format, "json")
        self.assertTrue(config.silent_mode)
        self.assertTrue(config.auto_select)


class TestConfigLoading(unittest.TestCase):
    """Test configuration file loading"""
    
    def setUp(self):
        """Set up test environment"""
        self.temp_dir = tempfile.mkdtemp()
        self.config_file = os.path.join(self.temp_dir, "test_config.config")
    
    def tearDown(self):
        """Clean up test environment"""
        import shutil
        shutil.rmtree(self.temp_dir)
    
    def test_load_config_file(self):
        """Test loading configuration from file"""
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
        
        config = load_config(self.config_file)
        
        self.assertEqual(config.interval, 0.5)
        self.assertEqual(config.output_dir, "/test/logs")
        self.assertEqual(config.buffer_size, 50)
        self.assertEqual(config.format, "json")
        self.assertTrue(config.silent_mode)
        self.assertTrue(config.auto_select)
    
    def test_load_config_nonexistent_file(self):
        """Test loading configuration when file doesn't exist"""
        nonexistent_file = os.path.join(self.temp_dir, "nonexistent.config")
        config = load_config(nonexistent_file)
        
        # Should return default configuration
        self.assertEqual(config.interval, 1.0)
        self.assertEqual(config.output_dir, "logs")
        self.assertEqual(config.buffer_size, 100)
    
    def test_environment_variable_override(self):
        """Test that environment variables override config file"""
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


class TestEnhancedCSVLogger(unittest.TestCase):
    """Test EnhancedCSVLogger class"""
    
    def setUp(self):
        """Set up test environment"""
        self.temp_dir = tempfile.mkdtemp()
        self.config = LoggerConfig(
            interval=0.1,  # Fast for testing
            output_dir=self.temp_dir,
            buffer_size=2,  # Small buffer for testing
            format="csv",
            silent_mode=True
        )
        self.logger = EnhancedCSVLogger(self.config)
    
    def tearDown(self):
        """Clean up test environment"""
        import shutil
        shutil.rmtree(self.temp_dir)
    
    @patch('benchlab.csv_log.csv_logger_enhanced.get_benchlab_ports')
    @patch('benchlab.csv_log.csv_logger_enhanced.serial.Serial')
    @patch('benchlab.csv_log.csv_logger_enhanced.read_uid')
    @patch('benchlab.csv_log.csv_logger_enhanced.read_device')
    def test_discover_devices(self, mock_read_device, mock_read_uid, mock_serial, mock_get_ports):
        """Test device discovery"""
        # Mock port discovery
        mock_get_ports.return_value = [
            {"port": "COM1"},
            {"port": "COM2"}
        ]
        
        # Mock serial connections
        mock_serial.side_effect = [Mock(), Mock()]
        
        # Mock device responses
        mock_read_uid.side_effect = ["device1", "device2"]
        mock_read_device.side_effect = [
            {"FwVersion": "1.0"},
            {"FwVersion": "2.0"}
        ]
        
        devices = self.logger.discover_devices()
        
        self.assertEqual(len(devices), 2)
        self.assertEqual(devices[0].uid, "device1")
        self.assertEqual(devices[0].firmware, "1.0")
        self.assertEqual(devices[1].uid, "device2")
        self.assertEqual(devices[1].firmware, "2.0")
    
    @patch('benchlab.csv_log.csv_logger_enhanced.get_benchlab_ports')
    def test_discover_no_devices(self, mock_get_ports):
        """Test device discovery when no devices are found"""
        mock_get_ports.return_value = []
        
        devices = self.logger.discover_devices()
        
        self.assertEqual(len(devices), 0)
    
    def test_select_devices_auto(self):
        """Test automatic device selection"""
        devices = [
            DeviceConfig(port="COM1", uid="device1", firmware="1.0"),
            DeviceConfig(port="COM2", uid="device2", firmware="2.0")
        ]
        
        self.config.auto_select = True
        selected = self.logger.select_devices(devices)
        
        self.assertEqual(len(selected), 2)
        self.assertEqual(selected[0].uid, "device1")
        self.assertEqual(selected[1].uid, "device2")
    
    @patch('builtins.input')
    def test_select_devices_interactive(self, mock_input):
        """Test interactive device selection"""
        devices = [
            DeviceConfig(port="COM1", uid="device1", firmware="1.0"),
            DeviceConfig(port="COM2", uid="device2", firmware="2.0"),
            DeviceConfig(port="COM3", uid="device3", firmware="3.0")
        ]
        
        # Test selecting specific devices
        mock_input.return_value = "1,3"
        
        selected = self.logger.select_devices(devices)
        
        self.assertEqual(len(selected), 2)
        self.assertEqual(selected[0].uid, "device1")
        self.assertEqual(selected[1].uid, "device3")
    
    @patch('benchlab.csv_log.csv_logger_enhanced.serial.Serial')
    def test_open_connections(self, mock_serial):
        """Test opening serial connections"""
        devices = [
            DeviceConfig(port="COM1", uid="device1", firmware="1.0"),
            DeviceConfig(port="COM2", uid="device2", firmware="2.0")
        ]
        
        mock_serial.side_effect = [Mock(), Mock()]
        
        connections = self.logger.open_connections(devices)
        
        self.assertEqual(len(connections), 2)
        self.assertIn("device1", connections)
        self.assertIn("device2", connections)
    
    def test_initialize_writers_csv(self):
        """Test CSV writer initialization"""
        connections = {"device1": Mock()}
        
        with patch('benchlab.csv_log.csv_logger_enhanced.read_sensors') as mock_read_sensors:
            mock_read_sensors.return_value = Mock()  # Mock sensor struct
            with patch('benchlab.csv_log.csv_logger_enhanced.translate_sensor_struct') as mock_translate:
                mock_translate.return_value = {"SYS_Power": 100, "CPU_Power": 50}
                
                self.logger.initialize_writers(connections)
        
        # Check that files were created
        files = list(Path(self.temp_dir).glob("*.csv"))
        self.assertEqual(len(files), 1)
        
        # Check file content
        with open(files[0], 'r') as f:
            content = f.read()
            self.assertIn("Timestamp", content)
            self.assertIn("SYS_Power", content)
            self.assertIn("CPU_Power", content)
    
    def test_initialize_writers_json(self):
        """Test JSON writer initialization"""
        self.config.format = "json"
        logger = EnhancedCSVLogger(self.config)
        
        connections = {"device1": Mock()}
        
        with patch('benchlab.csv_log.csv_logger_enhanced.read_sensors') as mock_read_sensors:
            mock_read_sensors.return_value = Mock()  # Mock sensor struct
            with patch('benchlab.csv_log.csv_logger_enhanced.translate_sensor_struct') as mock_translate:
                mock_translate.return_value = {"SYS_Power": 100, "CPU_Power": 50}
                
                logger.initialize_writers(connections)
        
        # Check that files were created
        files = list(Path(self.temp_dir).glob("*.json"))
        self.assertEqual(len(files), 1)
    
    def test_write_buffered_data_csv(self):
        """Test writing buffered data in CSV format"""
        uid = "test_device"
        
        # Initialize writer
        with open(os.path.join(self.temp_dir, "test.csv"), "w", newline="") as f:
            writer = MagicMock()
            self.logger.files[uid] = f
            self.logger.writers[uid] = writer
        
        # Add data to buffer
        self.logger.data_buffers[uid] = [
            {"Timestamp": "2023-01-01T00:00:00", "SYS_Power": 100},
            {"Timestamp": "2023-01-01T00:00:01", "SYS_Power": 101}
        ]
        
        # Write buffered data
        self.logger.write_buffered_data(uid)
        
        # Check that writer was called
        self.assertEqual(writer.writerow.call_count, 2)
        self.assertEqual(self.logger.files[uid].flush.call_count, 1)
    
    def test_write_buffered_data_json(self):
        """Test writing buffered data in JSON format"""
        uid = "test_device"
        self.config.format = "json"
        logger = EnhancedCSVLogger(self.config)
        
        # Initialize writer
        with open(os.path.join(self.temp_dir, "test.json"), "w") as f:
            self.logger.files[uid] = f
            self.logger.writers[uid] = f
        
        # Add data to buffer
        self.logger.data_buffers[uid] = [
            {"Timestamp": "2023-01-01T00:00:00", "SYS_Power": 100},
            {"Timestamp": "2023-01-01T00:00:01", "SYS_Power": 101}
        ]
        
        # Write buffered data
        self.logger.write_buffered_data(uid)
        
        # Check file content
        with open(os.path.join(self.temp_dir, "test.json"), 'r') as f:
            content = f.read()
            data_lines = content.strip().split('\n')
            self.assertEqual(len(data_lines), 2)
            
            # Parse JSON lines
            for line in data_lines:
                data = json.loads(line)
                self.assertIn("Timestamp", data)
                self.assertIn("SYS_Power", data)


class TestIntegration(unittest.TestCase):
    """Integration tests for the enhanced logger"""
    
    def setUp(self):
        """Set up test environment"""
        self.temp_dir = tempfile.mkdtemp()
    
    def tearDown(self):
        """Clean up test environment"""
        import shutil
        shutil.rmtree(self.temp_dir)
    
    def test_full_logging_cycle(self):
        """Test a complete logging cycle"""
        config = LoggerConfig(
            interval=0.1,
            output_dir=self.temp_dir,
            buffer_size=1,  # Write immediately
            format="csv",
            silent_mode=True,
            auto_select=True
        )
        
        logger = EnhancedCSVLogger(config)
        
        # Mock the discovery and connection process
        with patch.object(logger, 'discover_devices') as mock_discover, \
             patch.object(logger, 'open_connections') as mock_open, \
             patch.object(logger, 'initialize_writers') as mock_init, \
             patch.object(logger, '_logging_loop') as mock_loop:
            
            # Mock devices
            mock_discover.return_value = [
                DeviceConfig(port="COM1", uid="test_device", firmware="1.0")
            ]
            
            # Mock connections
            mock_open.return_value = {"test_device": Mock()}
            
            # Mock the logging loop to run once and stop
            def mock_logging_loop(connections):
                logger.logging_active = False  # Stop after one iteration
            
            mock_loop.side_effect = mock_logging_loop
            
            # Run the logger
            logger.start_logging()
        
        # Check that files were created
        csv_files = list(Path(self.temp_dir).glob("*.csv"))
        self.assertGreater(len(csv_files), 0)


if __name__ == '__main__':
    unittest.main()