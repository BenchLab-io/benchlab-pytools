"""
Cross-platform serial port handling for BENCHLAB devices
Optimized for Windows, Linux, and embedded systems
"""

import serial
import serial.tools.list_ports
import platform
import time
import logging
from typing import List, Dict, Optional, Tuple
import re

class CrossPlatformSerialManager:
    """Enhanced serial port manager with cross-platform optimizations"""
    
    def __init__(self):
        self.system = platform.system().lower()
        self.logger = logging.getLogger(__name__)
        
        # Platform-specific configurations
        self.platform_configs = {
            'windows': {
                'baudrate': 115200,
                'timeout': 1.0,
                'write_timeout': 1.0,
                'inter_byte_timeout': 0.1,
                'rtscts': False,
                'dsrdtr': False,
                'exclusive': True
            },
            'linux': {
                'baudrate': 115200,
                'timeout': 1.0,
                'write_timeout': 1.0,
                'inter_byte_timeout': 0.1,
                'rtscts': False,
                'dsrdtr': False,
                'exclusive': True
            },
            'darwin': {
                'baudrate': 115200,
                'timeout': 1.0,
                'write_timeout': 1.0,
                'inter_byte_timeout': 0.1,
                'rtscts': False,
                'dsrdtr': False,
                'exclusive': True
            }
        }
    
    def get_platform_config(self) -> Dict:
        """Get platform-specific serial configuration"""
        return self.platform_configs.get(self.system, self.platform_configs['linux'])
    
    def list_ports(self) -> List[Dict]:
        """List all available serial ports with platform-specific optimizations"""
        ports = []
        
        try:
            for port_info in serial.tools.list_ports.comports():
                port_dict = {
                    'port': port_info.device,
                    'description': port_info.description,
                    'hwid': port_info.hwid,
                    'vid': port_info.vid,
                    'pid': port_info.pid,
                    'serial_number': port_info.serial_number,
                    'location': port_info.location,
                    'manufacturer': port_info.manufacturer,
                    'product': port_info.product
                }
                ports.append(port_dict)
        except Exception as e:
            self.logger.error(f"Error listing ports: {e}")
        
        return ports
    
    def is_benchlab_port(self, port_info: Dict) -> bool:
        """Check if a port is likely to be a BENCHLAB device"""
        # Check by VID/PID if available
        if port_info.get('vid') and port_info.get('pid'):
            # Common BENCHLAB VID/PID combinations
            benchlab_vids = [0x1A86, 0x0403, 0x10C4]  # Common USB-to-Serial chips
            if port_info['vid'] in benchlab_vids:
                return True
        
        # Check by description patterns
        description = port_info.get('description', '').lower()
        hwid = port_info.get('hwid', '').lower()
        
        benchlab_patterns = [
            'ch340', 'ch341', 'ft232', 'cp210', 'cp2102',
            'usb serial', 'usb to serial', 'uart', 'ttyusb'
        ]
        
        for pattern in benchlab_patterns:
            if pattern in description or pattern in hwid:
                return True
        
        return False
    
    def get_benchlab_ports(self) -> List[Dict]:
        """Get list of BENCHLAB-compatible ports"""
        all_ports = self.list_ports()
        benchlab_ports = []
        
        for port in all_ports:
            if self.is_benchlab_port(port):
                benchlab_ports.append(port)
        
        return benchlab_ports
    
    def open_port(self, port: str, **kwargs) -> Optional[serial.Serial]:
        """Open a serial port with platform-specific optimizations"""
        config = self.get_platform_config()
        config.update(kwargs)  # Allow overriding defaults
        
        try:
            # Platform-specific optimizations
            if self.system == 'windows':
                # Windows-specific settings
                config['timeout'] = 2.0  # Slightly longer timeout
                config['write_timeout'] = 2.0
                
            elif self.system == 'linux':
                # Linux-specific settings
                config['timeout'] = 1.0
                config['write_timeout'] = 1.0
                
                # For embedded Linux systems, reduce timeouts
                if self._is_embedded_linux():
                    config['timeout'] = 0.5
                    config['write_timeout'] = 0.5
            
            # Open the port
            ser = serial.Serial(port, **config)
            
            # Additional platform-specific setup
            if self.system == 'windows':
                # Windows-specific setup
                ser.rts = False
                ser.dtr = False
                time.sleep(0.1)  # Wait for port to stabilize
                
            elif self.system == 'linux':
                # Linux-specific setup
                try:
                    ser.reset_input_buffer()
                    ser.reset_output_buffer()
                except Exception:
                    pass  # Ignore buffer reset errors
            
            self.logger.info(f"Opened port {port} with config: {config}")
            return ser
            
        except serial.SerialException as e:
            self.logger.error(f"Failed to open port {port}: {e}")
            return None
        except Exception as e:
            self.logger.error(f"Unexpected error opening port {port}: {e}")
            return None
    
    def _is_embedded_linux(self) -> bool:
        """Check if running on an embedded Linux system"""
        try:
            # Check for common embedded Linux indicators
            with open('/proc/cpuinfo', 'r') as f:
                cpuinfo = f.read().lower()
                
            embedded_indicators = [
                'armv', 'aarch64', 'raspberry', 'beagle', 'rockchip',
                'imx', 'sunxi', 'mt76', 'bcm'
            ]
            
            for indicator in embedded_indicators:
                if indicator in cpuinfo:
                    return True
                    
        except Exception:
            pass
        
        return False
    
    def close_port(self, ser: serial.Serial):
        """Safely close a serial port"""
        try:
            if ser and ser.is_open:
                ser.close()
                self.logger.info(f"Closed port {ser.port}")
        except Exception as e:
            self.logger.error(f"Error closing port {ser.port if ser else 'unknown'}: {e}")
    
    def read_with_timeout(self, ser: serial.Serial, size: int = 1, timeout: float = None) -> bytes:
        """Read from serial port with configurable timeout"""
        if not ser or not ser.is_open:
            return b''
        
        original_timeout = ser.timeout
        try:
            if timeout is not None:
                ser.timeout = timeout
            
            return ser.read(size)
        finally:
            ser.timeout = original_timeout
    
    def write_with_retry(self, ser: serial.Serial, data: bytes, max_retries: int = 3) -> bool:
        """Write to serial port with retry logic"""
        if not ser or not ser.is_open:
            return False
        
        for attempt in range(max_retries):
            try:
                ser.write(data)
                ser.flush()
                return True
            except serial.SerialTimeoutException:
                self.logger.warning(f"Write timeout on {ser.port} (attempt {attempt + 1})")
                if attempt < max_retries - 1:
                    time.sleep(0.1 * (2 ** attempt))  # Exponential backoff
            except Exception as e:
                self.logger.error(f"Write error on {ser.port}: {e}")
                if attempt < max_retries - 1:
                    time.sleep(0.1 * (2 ** attempt))
        
        return False
    
    def get_port_status(self, ser: serial.Serial) -> Dict:
        """Get detailed status of a serial port"""
        if not ser:
            return {'status': 'closed', 'port': None}
        
        status = {
            'port': ser.port,
            'is_open': ser.is_open,
            'baudrate': ser.baudrate,
            'bytes_in_buffer': ser.in_waiting if ser.is_open else 0,
            'bytes_out_buffer': ser.out_waiting if ser.is_open else 0,
            'timeout': ser.timeout,
            'write_timeout': ser.write_timeout,
            'rts': ser.rts if ser.is_open else None,
            'dtr': ser.dtr if ser.is_open else None,
            'cts': ser.cts if ser.is_open else None,
            'dsr': ser.dsr if ser.is_open else None,
            'ri': ser.ri if ser.is_open else None,
            'cd': ser.cd if ser.is_open else None
        }
        
        return status
    
    def scan_for_devices(self, timeout: float = 5.0) -> List[Dict]:
        """Scan all ports for active BENCHLAB devices"""
        benchlab_ports = self.get_benchlab_ports()
        active_devices = []
        
        for port_info in benchlab_ports:
            port = port_info['port']
            ser = self.open_port(port, timeout=1.0)
            
            if ser:
                try:
                    # Try to read a response (this would need to be adapted for BENCHLAB protocol)
                    # For now, just check if we can open the port successfully
                    device_info = {
                        'port': port,
                        'description': port_info.get('description', ''),
                        'manufacturer': port_info.get('manufacturer', ''),
                        'serial_number': port_info.get('serial_number', ''),
                        'status': 'active'
                    }
                    active_devices.append(device_info)
                    
                except Exception as e:
                    self.logger.debug(f"No response from {port}: {e}")
                finally:
                    self.close_port(ser)
        
        return active_devices
    
    def get_system_info(self) -> Dict:
        """Get system information for debugging"""
        return {
            'platform': platform.platform(),
            'system': platform.system(),
            'release': platform.release(),
            'version': platform.version(),
            'machine': platform.machine(),
            'processor': platform.processor(),
            'python_version': platform.python_version(),
            'serial_version': serial.VERSION
        }


# Convenience functions for direct use
def get_benchlab_ports() -> List[Dict]:
    """Get list of BENCHLAB-compatible ports (convenience function)"""
    manager = CrossPlatformSerialManager()
    return manager.get_benchlab_ports()


def open_serial_connection(port: str, **kwargs) -> Optional[serial.Serial]:
    """Open a serial connection with cross-platform optimizations"""
    manager = CrossPlatformSerialManager()
    return manager.open_port(port, **kwargs)


def close_serial_connection(ser: serial.Serial):
    """Close a serial connection safely"""
    manager = CrossPlatformSerialManager()
    manager.close_port(ser)


def scan_for_benchlab_devices(timeout: float = 5.0) -> List[Dict]:
    """Scan for active BENCHLAB devices"""
    manager = CrossPlatformSerialManager()
    return manager.scan_for_devices(timeout)


# Performance monitoring
class SerialPerformanceMonitor:
    """Monitor serial port performance for optimization"""
    
    def __init__(self):
        self.read_times = []
        self.write_times = []
        self.error_count = 0
    
    def record_read(self, duration: float):
        """Record a read operation duration"""
        self.read_times.append(duration)
        if len(self.read_times) > 1000:  # Keep last 1000 measurements
            self.read_times.pop(0)
    
    def record_write(self, duration: float):
        """Record a write operation duration"""
        self.write_times.append(duration)
        if len(self.write_times) > 1000:  # Keep last 1000 measurements
            self.write_times.pop(0)
    
    def record_error(self):
        """Record an error occurrence"""
        self.error_count += 1
    
    def get_stats(self) -> Dict:
        """Get performance statistics"""
        if not self.read_times and not self.write_times:
            return {'status': 'no_data'}
        
        stats = {
            'read_operations': len(self.read_times),
            'write_operations': len(self.write_times),
            'error_count': self.error_count
        }
        
        if self.read_times:
            stats['read_avg_ms'] = sum(self.read_times) / len(self.read_times) * 1000
            stats['read_min_ms'] = min(self.read_times) * 1000
            stats['read_max_ms'] = max(self.read_times) * 1000
        
        if self.write_times:
            stats['write_avg_ms'] = sum(self.write_times) / len(self.write_times) * 1000
            stats['write_min_ms'] = min(self.write_times) * 1000
            stats['write_max_ms'] = max(self.write_times) * 1000
        
        return stats


# Global performance monitor instance
performance_monitor = SerialPerformanceMonitor()


if __name__ == '__main__':
    # Example usage and testing
    manager = CrossPlatformSerialManager()
    
    print("=== System Information ===")
    system_info = manager.get_system_info()
    for key, value in system_info.items():
        print(f"{key}: {value}")
    
    print("\n=== Available Ports ===")
    ports = manager.list_ports()
    for port in ports:
        is_benchlab = manager.is_benchlab_port(port)
        print(f"Port: {port['port']}")
        print(f"  Description: {port['description']}")
        print(f"  BENCHLAB: {is_benchlab}")
        print(f"  VID: {port.get('vid')}, PID: {port.get('pid')}")
        print()
    
    print("=== BENCHLAB Ports ===")
    benchlab_ports = manager.get_benchlab_ports()
    for port in benchlab_ports:
        print(f"BENCHLAB Port: {port['port']}")
    
    print(f"\nFound {len(benchlab_ports)} BENCHLAB-compatible ports")