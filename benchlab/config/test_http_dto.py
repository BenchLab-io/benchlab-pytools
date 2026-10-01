"""Unit tests for the HTTP DTO <-> raw struct-dict translation layer."""

from benchlab.config.http_dto import (
    fan_dto_to_dict, fan_dict_to_dto,
    rgb_dto_to_dict, rgb_dict_to_dto,
    calibration_dto_to_dict, calibration_dict_to_dto,
)


def test_fan_dto_to_dict_scales_temp():
    dto = {
        "fanMode": 1, "tempSource": 0, "temp": [30.0, 50.0],
        "duty": [20, 80], "rampStep": 5, "fixedDuty": 50,
        "minDuty": 10, "maxDuty": 100, "fanStop": True,
    }
    d = fan_dto_to_dict(dto)
    assert d["Temp"] == [300, 500]


def test_fan_dict_to_dto_scales_temp():
    d = {
        "FanMode": 1, "TempSource": 0, "Temp": [300, 500],
        "Duty": [20, 80], "RampStep": 5, "FixedDuty": 50,
        "MinDuty": 10, "MaxDuty": 100, "FanStop": 1,
    }
    dto = fan_dict_to_dto(d)
    assert dto["temp"] == [30.0, 50.0]


def test_fan_round_trip():
    d = {
        "FanMode": 2, "TempSource": 1, "Temp": [250, 450],
        "Duty": [10, 90], "RampStep": 3, "FixedDuty": 60,
        "MinDuty": 5, "MaxDuty": 95, "FanStop": 0,
    }
    assert fan_dto_to_dict(fan_dict_to_dto(d)) == d


def test_fan_dto_to_dict_fan_stop_bool_to_int():
    dto = {
        "fanMode": 0, "tempSource": 0, "temp": [0.0, 0.0],
        "duty": [0, 0], "rampStep": 0, "fixedDuty": 0,
        "minDuty": 0, "maxDuty": 0, "fanStop": True,
    }
    d = fan_dto_to_dict(dto)
    assert d["FanStop"] == 1
    assert type(d["FanStop"]) is int


def test_fan_dict_to_dto_fan_stop_int_to_bool():
    d = {
        "FanMode": 0, "TempSource": 0, "Temp": [0, 0],
        "Duty": [0, 0], "RampStep": 0, "FixedDuty": 0,
        "MinDuty": 0, "MaxDuty": 0, "FanStop": 0,
    }
    dto = fan_dict_to_dto(d)
    assert dto["fanStop"] is False
    assert type(dto["fanStop"]) is bool


def test_rgb_round_trip():
    dto = {"mode": 1, "red": 255, "green": 0, "blue": 0,
           "direction": 0, "speed": 50}
    assert rgb_dict_to_dto(rgb_dto_to_dict(dto)) == dto


def test_calibration_dto_to_dict_flattens_nested_names():
    dto = {
        "crc": 1234,
        "vin": [{"offset": 1, "gainOffset": 2}],
        "vdd": {"offset": 3, "gainOffset": 4},
        "vref": {"offset": 5, "gainOffset": 6},
        "ts": [{"offset": 7, "gainOffset": 8}],
        "tsB": [10],
        "tamb": {"offset": 9, "gainOffset": 10},
        "hum": {"offset": 11, "gainOffset": 12},
        "powerReadingVoltage": [{"offset": 13, "gainOffset": 14}],
        "powerReadingCurrent": [{"offset": 15, "gainOffset": 16}],
    }
    d = calibration_dto_to_dict(dto)
    assert d["Vdd"] == {"Offset": 3, "GainOffset": 4}
    assert d["Vin"] == [{"Offset": 1, "GainOffset": 2}]


def _cal_dict(ts_len, power_len, vin_len=13):
    value = {"Offset": 0, "GainOffset": 0}
    return {
        "Crc": 0,
        "Vin": [value] * vin_len,
        "Vdd": value,
        "Vref": value,
        "Ts": [value] * ts_len,
        "TsB": [0] * ts_len,
        "Tamb": value,
        "Hum": value,
        "PowerReadingVoltage": [value] * power_len,
        "PowerReadingCurrent": [value] * power_len,
    }


def test_calibration_round_trip_original_lengths():
    d = _cal_dict(ts_len=4, power_len=11)
    assert calibration_dto_to_dict(calibration_dict_to_dto(d)) == d


def test_calibration_round_trip_bl2_lengths():
    d = _cal_dict(ts_len=8, power_len=23)
    assert calibration_dto_to_dict(calibration_dict_to_dto(d)) == d


def test_calibration_dict_to_dto_defaults_crc_when_missing():
    d = _cal_dict(ts_len=4, power_len=11)
    del d["Crc"]
    dto = calibration_dict_to_dto(d)
    assert dto["crc"] == 0
