import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from benchlab.fastapi.telemetry_api import app

client = TestClient(app)

# Sample mocked telemetry
mocked_telemetry = {
    "temperature": 42.5,
    "voltage": 3.3,
    "timestamp": "2025-10-19T12:00:00"
}

# Sample mocked device info
mocked_device_info = {
    "VendorId": 1,
    "ProductId": 2,
    "FwVersion": "v1.0"
}

# Sample mocked device list
mocked_devices = [
    {"port": "COM_TEST", "uid": "TESTUID123", "fw": "v1.0"}
]

@pytest.fixture(autouse=True)
def mock_serial_io():
    # Patch serial_io functions for the duration of the test
    with patch("benchlab.fastapi.telemetry_api.serial_io.find_benchlab_devices", return_value=mocked_devices), \
         patch("benchlab.fastapi.telemetry_api.serial_io.read_sensors", return_value=MagicMock(**mocked_telemetry)), \
         patch("benchlab.fastapi.telemetry_api.serial_io.read_device", return_value=mocked_device_info), \
         patch("benchlab.fastapi.telemetry_api.serial_io.open_serial_connection", return_value=MagicMock()):
        yield

def test_device_info():
    response = client.get("/device/TESTUID123/info")
    assert response.status_code == 200
    data = response.json()
    assert data["UID"] == "TESTUID123"
    assert data["FwVersion"] == "v1.0" or data["fw"] == "v1.0"

def test_telemetry_endpoint():
    # Populate a fake telemetry entry manually
    from benchlab.fastapi import telemetry_api
    telemetry_api.devices_data["TESTUID123"] = {
        "port": "COM_TEST",
        "latest": mocked_telemetry,
        "history": [],
        "info": mocked_device_info
    }

    response = client.get("/device/TESTUID123/telemetry")
    assert response.status_code == 200
    data = response.json()
    assert data["temperature"] == 42.5
    assert data["voltage"] == 3.3

def test_list_devices():
    response = client.get("/devices")
    assert response.status_code == 200
    assert isinstance(response.json(), list)

def test_favicon():
    response = client.get("/favicon.ico")
    assert response.status_code in (200, 304)
