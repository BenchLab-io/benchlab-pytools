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


# ---------------------------------------------------------------------------
# save_config / load_config / reset_config -- issue #68
#
# benchlab-pycore 0.6.0 removed config_io.save_config/load_config/reset_config
# (they sent UART_CMD_NVM_CONFIG and friends, opcodes no released or
# in-development firmware implements -- see benchlab-pycore#11). These three
# methods now route through send_action(), which mirrors UART_CMD_ACTION
# (opcode 2), the command firmware actually implements for save/load/reset.
# ---------------------------------------------------------------------------

def test_save_config_sends_the_save_action(monkeypatch):
    client = _make_client(BENCHLAB_ORIGINAL_PRODUCT_ID)
    captured = {}

    def fake_send_action(ser, action):
        captured["action"] = action
        return True
    monkeypatch.setattr("benchlab_pycore.core.send_action", fake_send_action)

    assert client.save_config() is True
    assert captured["action"] == 0  # CONFIG_ACTION_SAVE


def test_load_config_sends_the_load_action(monkeypatch):
    client = _make_client(BENCHLAB_ORIGINAL_PRODUCT_ID)
    captured = {}

    def fake_send_action(ser, action):
        captured["action"] = action
        return True
    monkeypatch.setattr("benchlab_pycore.core.send_action", fake_send_action)

    assert client.load_config() is True
    assert captured["action"] == 1  # CONFIG_ACTION_LOAD


def test_reset_config_sends_the_reset_action(monkeypatch):
    client = _make_client(BENCHLAB_ORIGINAL_PRODUCT_ID)
    captured = {}

    def fake_send_action(ser, action):
        captured["action"] = action
        return True
    monkeypatch.setattr("benchlab_pycore.core.send_action", fake_send_action)

    assert client.reset_config() is True
    assert captured["action"] == 2  # CONFIG_ACTION_RESET


def test_save_config_propagates_a_failed_send(monkeypatch):
    """send_action returning False (e.g. another action already pending on
    the device) must surface as a failed save, not be swallowed."""
    client = _make_client(BENCHLAB_ORIGINAL_PRODUCT_ID)
    monkeypatch.setattr(
        "benchlab_pycore.core.send_action", lambda ser, action: False)

    assert client.save_config() is False


# ---------------------------------------------------------------------------
# factory_cal_unlock -- benchlab-pycore 0.8.0 / BENCHLAB_Service 2.6.0
#
# New BL2 fw07+ only command that temporarily lifts write-protection on the
# factory calibration slot. Disruptive (resets the device on success), but
# from this client's perspective it's a thin pass-through: DirectConfigClient
# calls pycore's factory_cal_unlock(ser); NamedPipeConfigClient sends the
# SendFactoryCalUnlock pipe command with the fixed passphrase payload.
# ---------------------------------------------------------------------------

def test_direct_factory_cal_unlock_calls_pycore(monkeypatch):
    client = _make_client(BENCHLAB_BL2_PRODUCT_ID)
    captured = {}

    def fake_factory_cal_unlock(ser):
        captured["called"] = True
        return True
    monkeypatch.setattr(
        "benchlab_pycore.core.factory_cal_unlock", fake_factory_cal_unlock)

    assert client.factory_cal_unlock() is True
    assert captured["called"] is True


def test_direct_factory_cal_unlock_refuses_non_bl2_device(monkeypatch):
    """pycore's factory_cal_unlock() only reports whether bytes were
    written, not whether the firmware accepted them -- confirmed against
    real BL1 hardware, which returns True even though the opcode is
    unknown on ORIGINAL firmware and nothing happens. Gate on product_id
    ourselves so callers get an honest False."""
    client = _make_client(BENCHLAB_ORIGINAL_PRODUCT_ID)
    captured = {}

    def fake_factory_cal_unlock(ser):
        captured["called"] = True
        return True
    monkeypatch.setattr(
        "benchlab_pycore.core.factory_cal_unlock", fake_factory_cal_unlock)

    assert client.factory_cal_unlock() is False
    assert "called" not in captured


def test_direct_factory_cal_unlock_propagates_a_failed_call(monkeypatch):
    client = _make_client(BENCHLAB_BL2_PRODUCT_ID)
    monkeypatch.setattr(
        "benchlab_pycore.core.factory_cal_unlock", lambda ser: False)

    assert client.factory_cal_unlock() is False


def test_named_pipe_factory_cal_unlock_sends_command_and_passphrase(
        monkeypatch):
    from benchlab.config.config_client import NamedPipeConfigClient

    client = NamedPipeConfigClient.__new__(NamedPipeConfigClient)
    client.pipe_name = "BenchlabSensorPipe_11_TEST"
    client.handle = None

    captured = {}

    def fake_send_command(command, payload=None):
        captured["command"] = command
        captured["payload"] = payload
        return {"success": True}
    monkeypatch.setattr(client, "_send_command", fake_send_command)

    assert client.factory_cal_unlock() is True
    assert captured["command"] == "SendFactoryCalUnlock"
    assert captured["payload"] == "benchlab"


def test_named_pipe_factory_cal_unlock_propagates_a_failed_call(monkeypatch):
    from benchlab.config.config_client import NamedPipeConfigClient

    client = NamedPipeConfigClient.__new__(NamedPipeConfigClient)
    client.pipe_name = "BenchlabSensorPipe_11_TEST"
    client.handle = None

    monkeypatch.setattr(
        client, "_send_command",
        lambda command, payload=None: {"success": False})

    assert client.factory_cal_unlock() is False
