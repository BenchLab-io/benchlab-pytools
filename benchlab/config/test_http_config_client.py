"""Non-hardware regression tests for HttpConfigClient's fan/RGB/calibration
methods. Constructs the client without running __init__ (which opens a
real requests.Session) and monkeypatches _get/_put directly, mirroring the
pattern already used for NamedPipeConfigClient in test_config_client.py.
"""

from benchlab.config.config_client import HttpConfigClient


def _make_client():
    client = HttpConfigClient.__new__(HttpConfigClient)
    client.uid = "test-uid"
    client.base_url = "http://localhost:8585"
    client.timeout = 5.0
    return client


_FAN_DTO = {
    "fanMode": 1, "tempSource": 0, "temp": [30.0, 50.0],
    "duty": [20, 80], "rampStep": 5, "fixedDuty": 50,
    "minDuty": 10, "maxDuty": 100, "fanStop": True,
}
_FAN_DICT = {
    "FanMode": 1, "TempSource": 0, "Temp": [300, 500],
    "Duty": [20, 80], "RampStep": 5, "FixedDuty": 50,
    "MinDuty": 10, "MaxDuty": 100, "FanStop": 1,
}

_RGB_DTO = {"mode": 1, "red": 255, "green": 0, "blue": 0,
            "direction": 0, "speed": 50}
_RGB_DICT = {"Mode": 1, "Red": 255, "Green": 0, "Blue": 0,
             "Direction": 0, "Speed": 50}

_CAL_VALUE_DTO = {"offset": 1, "gainOffset": 2}
_CAL_VALUE_DICT = {"Offset": 1, "GainOffset": 2}
_CAL_DTO = {
    "crc": 1234,
    "vin": [_CAL_VALUE_DTO],
    "vdd": _CAL_VALUE_DTO,
    "vref": _CAL_VALUE_DTO,
    "ts": [_CAL_VALUE_DTO],
    "tsB": [10],
    "tamb": _CAL_VALUE_DTO,
    "hum": _CAL_VALUE_DTO,
    "powerReadingVoltage": [_CAL_VALUE_DTO],
    "powerReadingCurrent": [_CAL_VALUE_DTO],
}
_CAL_DICT = {
    "Crc": 1234,
    "Vin": [_CAL_VALUE_DICT],
    "Vdd": _CAL_VALUE_DICT,
    "Vref": _CAL_VALUE_DICT,
    "Ts": [_CAL_VALUE_DICT],
    "TsB": [10],
    "Tamb": _CAL_VALUE_DICT,
    "Hum": _CAL_VALUE_DICT,
    "PowerReadingVoltage": [_CAL_VALUE_DICT],
    "PowerReadingCurrent": [_CAL_VALUE_DICT],
}


def test_http_read_fan_config_translates_dto(monkeypatch):
    client = _make_client()
    captured = {}

    def fake_get(path):
        captured["path"] = path
        return _FAN_DTO
    monkeypatch.setattr(client, "_get", fake_get)

    assert client.read_fan_config(0, 0) == _FAN_DICT
    assert captured["path"] == "/device/test-uid/fan/0/0"


def test_http_write_fan_config_translates_and_calls_put(monkeypatch):
    client = _make_client()
    captured = {}

    def fake_put(path, json_body=None):
        captured["path"] = path
        captured["body"] = json_body
        return True
    monkeypatch.setattr(client, "_put", fake_put)

    assert client.write_fan_config(1, 0, _FAN_DICT) is True
    assert captured["path"] == "/device/test-uid/fan/1/0"
    assert captured["body"] == _FAN_DTO


def test_http_read_fan_config_returns_none_on_get_failure(monkeypatch):
    client = _make_client()
    monkeypatch.setattr(client, "_get", lambda path: None)
    assert client.read_fan_config(0, 0) is None


def test_http_read_rgb_config_translates_dto(monkeypatch):
    client = _make_client()
    captured = {}

    def fake_get(path):
        captured["path"] = path
        return _RGB_DTO
    monkeypatch.setattr(client, "_get", fake_get)

    assert client.read_rgb_config(0) == _RGB_DICT
    assert captured["path"] == "/device/test-uid/rgb/0"


def test_http_write_rgb_config_translates_and_calls_put(monkeypatch):
    client = _make_client()
    captured = {}

    def fake_put(path, json_body=None):
        captured["path"] = path
        captured["body"] = json_body
        return True
    monkeypatch.setattr(client, "_put", fake_put)

    assert client.write_rgb_config(0, _RGB_DICT) is True
    assert captured["path"] == "/device/test-uid/rgb/0"
    assert captured["body"] == _RGB_DTO


def test_http_read_calibration_translates_dto(monkeypatch):
    client = _make_client()
    monkeypatch.setattr(client, "_get", lambda path: _CAL_DTO)
    assert client.read_calibration() == _CAL_DICT


def test_http_write_calibration_translates_and_calls_put(monkeypatch):
    client = _make_client()
    captured = {}

    def fake_put(path, json_body=None):
        captured["path"] = path
        captured["body"] = json_body
        return True
    monkeypatch.setattr(client, "_put", fake_put)

    assert client.write_calibration(_CAL_DICT) is True
    assert captured["path"] == "/device/test-uid/calibration"
    assert captured["body"] == _CAL_DTO


def test_http_write_calibration_no_read_before_write(monkeypatch):
    client = _make_client()

    def fail_if_called(path):
        raise AssertionError("write_calibration must not call _get")
    monkeypatch.setattr(client, "_get", fail_if_called)
    monkeypatch.setattr(client, "_put", lambda path, json_body=None: True)

    assert client.write_calibration(_CAL_DICT) is True
