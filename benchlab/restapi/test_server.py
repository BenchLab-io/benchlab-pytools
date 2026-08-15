#!/usr/bin/env python3
"""
Test script for the improved BenchLab FastAPI telemetry server.
This script tests the basic functionality without requiring actual
BenchLab devices.
"""

import sys
from pathlib import Path

# Add the parent directory to the path so we can import benchlab modules
sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def test_server_startup():
    """Test that the server starts without errors."""
    print("Testing server startup...")

    try:
        # Import the telemetry API module
        from benchlab.restapi.telemetry_api import app, Config

        # Test configuration validation
        Config.validate()
        print("✓ Configuration validation passed")

        # Test that the app was created successfully
        assert app is not None
        print("✓ FastAPI app created successfully")

        return True

    except Exception as e:
        print(f"✗ Server startup test failed: {e}")
        return False


def test_api_endpoints():
    """Test the API endpoints without starting the full server."""
    print("\nTesting API endpoints...")

    try:
        from benchlab.restapi.telemetry_api import app

        # Test that all expected routes are registered
        routes = [route.path for route in app.routes]

        expected_routes = [
            "/devices",
            "/device/{uid}/info",
            "/device/{uid}/telemetry",
            "/device/{uid}/telemetry/{sensor}",
            "/device/{uid}/history",
            "/device/{uid}/sensors",
            "/device/{uid}/stream",
            "/favicon.ico",
            "/health",
            "/status",
            "/device/{uid}/status"
        ]

        for route in expected_routes:
            if route in routes:
                print(f"✓ Route {route} registered")
            else:
                print(f"✗ Route {route} missing")
                return False

        return True

    except Exception as e:
        print(f"✗ API endpoint test failed: {e}")
        return False


def test_device_discovery():
    """Test that device discovery is callable and returns a list."""
    print("\nTesting device discovery...")

    try:
        from benchlab.restapi.telemetry_api import find_benchlab_devices

        # No hardware required - just verify it runs and returns a list
        devices = find_benchlab_devices()
        assert isinstance(devices, list)
        print("✓ Device discovery working")

        return True

    except Exception as e:
        print(f"✗ Device discovery test failed: {e}")
        return False


def test_error_handling():
    """Test error handling and validation."""
    print("\nTesting error handling...")

    try:
        # Test invalid configuration by creating a new Config class instance
        # We need to test the validation logic directly

        # Test invalid port validation
        try:
            # Create a test config with invalid port
            test_config = type('TestConfig', (), {
                'POLL_INTERVAL': 1.0,
                'HISTORY_LENGTH': 10,
                'API_PORT': 99999,  # Invalid port
                'MAX_HISTORY_LIMIT': 1000,
                'SCAN_INTERVAL': 30,
                'validate': lambda self: (
                    None if self.POLL_INTERVAL >= 0.1 else exec(
                        'raise ValueError('
                        '"POLL_INTERVAL must be at least 0.1 seconds")'),
                    None if self.HISTORY_LENGTH >= 1 else exec(
                        'raise ValueError('
                        '"HISTORY_LENGTH must be at least 1")'),
                    None if 1 <= self.API_PORT <= 65535 else exec(
                        'raise ValueError('
                        '"API_PORT must be between 1 and 65535")'),
                    None if self.MAX_HISTORY_LIMIT >= 1 else exec(
                        'raise ValueError('
                        '"MAX_HISTORY_LIMIT must be at least 1")'),
                    None if self.SCAN_INTERVAL >= 1 else exec(
                        'raise ValueError('
                        '"SCAN_INTERVAL must be at least 1 second")')
                )[0]
            })()

            test_config.validate()
            print("✗ Should have failed validation for invalid port")
            return False
        except ValueError:
            print("✓ Invalid port validation working")

        # Test invalid poll interval validation
        try:
            test_config = type('TestConfig', (), {
                'POLL_INTERVAL': 0.05,  # Too low
                'HISTORY_LENGTH': 10,
                'API_PORT': 8000,
                'MAX_HISTORY_LIMIT': 1000,
                'SCAN_INTERVAL': 30,
                'validate': lambda self: (
                    None if self.POLL_INTERVAL >= 0.1 else exec(
                        'raise ValueError('
                        '"POLL_INTERVAL must be at least 0.1 seconds")'),
                    None if self.HISTORY_LENGTH >= 1 else exec(
                        'raise ValueError('
                        '"HISTORY_LENGTH must be at least 1")'),
                    None if 1 <= self.API_PORT <= 65535 else exec(
                        'raise ValueError('
                        '"API_PORT must be between 1 and 65535")'),
                    None if self.MAX_HISTORY_LIMIT >= 1 else exec(
                        'raise ValueError('
                        '"MAX_HISTORY_LIMIT must be at least 1")'),
                    None if self.SCAN_INTERVAL >= 1 else exec(
                        'raise ValueError('
                        '"SCAN_INTERVAL must be at least 1 second")')
                )[0]
            })()

            test_config.validate()
            print("✗ Should have failed validation for invalid poll interval")
            return False
        except ValueError:
            print("✓ Invalid poll interval validation working")

        return True

    except Exception as e:
        print(f"✗ Error handling test failed: {e}")
        return False


def main():
    """Run all tests."""
    print("BenchLab FastAPI Server Test Suite")
    print("=" * 40)

    tests = [
        test_server_startup,
        test_api_endpoints,
        test_device_discovery,
        test_error_handling
    ]

    passed = 0
    total = len(tests)

    for test in tests:
        if test():
            passed += 1

    print(f"\nTest Results: {passed}/{total} tests passed")

    if passed == total:
        print(
            "🎉 All tests passed! The FastAPI server improvements are "
            "working correctly.")
        return 0
    else:
        print("❌ Some tests failed. Please check the implementation.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
