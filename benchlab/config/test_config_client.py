"""Non-hardware regression tests for benchlab.config.config_client.

Covers the BL2 vs Original calibration struct selection bug fixed in the
config bug sweep (issue #32): write_calibration used to always reconstruct
a plain CalibrationStruct (Original, 4 temp sensors) regardless of the
connected device's actual product_id, even though benchlab-pycore has a
distinct CalibrationStructBL2 (8 temp sensors) for BL2 devices and
read_calibration already selected the right one. Uses the real
benchlab-pycore struct types (a hard dependency of this module) but no
real serial device -- write_calibration is invoked directly on an instance
built without going through __init__'s serial connection.
"""

from benchlab.config.config_client import DirectConfigClient
from benchlab_pycore.core import (
    CalibrationStruct, CalibrationStructBL2,
    BENCHLAB_BL2_PRODUCT_ID, BENCHLAB_ORIGINAL_PRODUCT_ID,
)
import pytest

pytest.importorskip("benchlab_pycore")


def _make_client(product_id):
    """Construct a DirectConfigClient without running __init__ (which opens
    a real serial connection) -- just set the attributes write_calibration
    actually needs."""
    client = DirectConfigClient.__new__(DirectConfigClient)
    client.product_id = product_id
    client.ser = None
    return client


def test_write_calibration_selects_bl2_struct_for_bl2_device(monkeypatch):
    """Regression test: BL2-shaped calibration data (8 temp sensors) used
    to be forced into the Original 4-sensor struct, raising IndexError."""
    client = _make_client(BENCHLAB_BL2_PRODUCT_ID)

    captured = {}

    def fake_write_calibration(ser, calibration, product_id=None):
        captured["struct_type"] = type(calibration)
        return True
    monkeypatch.setattr(
        "benchlab_pycore.core.write_calibration",
        fake_write_calibration)

    cal_dict = {
        "Crc": 0,
        # BL2 has 8 sensors
        "Ts": [{"Offset": i, "GainOffset": 0} for i in range(8)],
    }

    result = client.write_calibration(cal_dict)

    assert result is True
    assert captured["struct_type"] is CalibrationStructBL2


def test_write_calibration_selects_original_struct_for_original_device(
        monkeypatch):
    client = _make_client(BENCHLAB_ORIGINAL_PRODUCT_ID)

    captured = {}

    def fake_write_calibration(ser, calibration, product_id=None):
        captured["struct_type"] = type(calibration)
        return True
    monkeypatch.setattr(
        "benchlab_pycore.core.write_calibration",
        fake_write_calibration)

    cal_dict = {
        "Crc": 0,
        # Original has 4 sensors
        "Ts": [{"Offset": i, "GainOffset": 0} for i in range(4)],
    }

    result = client.write_calibration(cal_dict)

    assert result is True
    assert captured["struct_type"] is CalibrationStruct


def test_write_calibration_bl2_data_no_longer_raises_indexerror():
    """Directly reproduces the original bug scenario: reconstructing
    8-sensor calibration data now succeeds when the client knows it's
    talking to a BL2 device, instead of raising IndexError against the
    4-sensor Original struct."""
    client = _make_client(BENCHLAB_BL2_PRODUCT_ID)

    cal_dict = {
        "Crc": 0,
        "Ts": [{"Offset": i, "GainOffset": 0} for i in range(8)],
    }

    # _dict_to_struct is the piece that previously raised IndexError when
    # forced into the wrong (4-sensor) struct type; call it directly with
    # the now-correct struct type to confirm reconstruction succeeds.
    struct_type = (
        CalibrationStructBL2
        if client.product_id == BENCHLAB_BL2_PRODUCT_ID
        else CalibrationStruct
    )
    result = client._dict_to_struct(cal_dict, struct_type)

    assert len(result.Ts) == 8
