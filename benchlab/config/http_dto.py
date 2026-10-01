"""Translation between the C# BenchLab service's HTTP DTO JSON shapes and
this repo's flat PascalCase raw-struct-dict convention (the shape
DirectConfigClient/NamedPipeConfigClient already return/accept).

The service's DTOs (FanConfigDto, RgbConfigDto, CalibrationDto,
CalibrationValueDto) use camelCase property names, plain integers for
enum-like fields, a real JSON bool for FanStop, and FanConfigDto.Temp in
real Celsius (the service itself applies FAN_TEMP_SCALE against the
firmware's raw int16 0.1degC wire units). These functions are pure and
have no HTTP/session dependency so they can be unit tested in isolation.
"""

from typing import Any, Dict

FAN_TEMP_SCALE = 10.0


def fan_dto_to_dict(dto: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "FanMode": dto["fanMode"],
        "TempSource": dto["tempSource"],
        "Temp": [round(t * FAN_TEMP_SCALE) for t in dto["temp"]],
        "Duty": list(dto["duty"]),
        "RampStep": dto["rampStep"],
        "FixedDuty": dto["fixedDuty"],
        "MinDuty": dto["minDuty"],
        "MaxDuty": dto["maxDuty"],
        "FanStop": int(bool(dto["fanStop"])),
    }


def fan_dict_to_dto(d: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "fanMode": d["FanMode"],
        "tempSource": d["TempSource"],
        "temp": [t / FAN_TEMP_SCALE for t in d["Temp"]],
        "duty": list(d["Duty"]),
        "rampStep": d["RampStep"],
        "fixedDuty": d["FixedDuty"],
        "minDuty": d["MinDuty"],
        "maxDuty": d["MaxDuty"],
        "fanStop": bool(d["FanStop"]),
    }


def rgb_dto_to_dict(dto: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "Mode": dto["mode"],
        "Red": dto["red"],
        "Green": dto["green"],
        "Blue": dto["blue"],
        "Direction": dto["direction"],
        "Speed": dto["speed"],
    }


def rgb_dict_to_dto(d: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "mode": d["Mode"],
        "red": d["Red"],
        "green": d["Green"],
        "blue": d["Blue"],
        "direction": d["Direction"],
        "speed": d["Speed"],
    }


def _cal_value_dto_to_dict(v: Dict[str, Any]) -> Dict[str, Any]:
    return {"Offset": v["offset"], "GainOffset": v["gainOffset"]}


def _cal_value_dict_to_dto(v: Dict[str, Any]) -> Dict[str, Any]:
    return {"offset": v["Offset"], "gainOffset": v["GainOffset"]}


def calibration_dto_to_dict(dto: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "Crc": dto["crc"],
        "Vin": [_cal_value_dto_to_dict(v) for v in dto["vin"]],
        "Vdd": _cal_value_dto_to_dict(dto["vdd"]),
        "Vref": _cal_value_dto_to_dict(dto["vref"]),
        "Ts": [_cal_value_dto_to_dict(v) for v in dto["ts"]],
        "TsB": list(dto["tsB"]),
        "Tamb": _cal_value_dto_to_dict(dto["tamb"]),
        "Hum": _cal_value_dto_to_dict(dto["hum"]),
        "PowerReadingVoltage": [
            _cal_value_dto_to_dict(v) for v in dto["powerReadingVoltage"]],
        "PowerReadingCurrent": [
            _cal_value_dto_to_dict(v) for v in dto["powerReadingCurrent"]],
    }


def calibration_dict_to_dto(d: Dict[str, Any]) -> Dict[str, Any]:
    # Crc defaults to 0 when absent: the server recomputes/overwrites it
    # on write regardless of what the client sends.
    return {
        "crc": d.get("Crc", 0),
        "vin": [_cal_value_dict_to_dto(v) for v in d["Vin"]],
        "vdd": _cal_value_dict_to_dto(d["Vdd"]),
        "vref": _cal_value_dict_to_dto(d["Vref"]),
        "ts": [_cal_value_dict_to_dto(v) for v in d["Ts"]],
        "tsB": list(d["TsB"]),
        "tamb": _cal_value_dict_to_dto(d["Tamb"]),
        "hum": _cal_value_dict_to_dto(d["Hum"]),
        "powerReadingVoltage": [
            _cal_value_dict_to_dto(v) for v in d["PowerReadingVoltage"]],
        "powerReadingCurrent": [
            _cal_value_dict_to_dto(v) for v in d["PowerReadingCurrent"]],
    }
