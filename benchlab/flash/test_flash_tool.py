"""Tests for the flash tool CLI logic (device selection parsing, the
interactive guided flow, and the bare-DFU variant prompt); no hardware
needed -- FlashManager is monkeypatched."""
from unittest.mock import MagicMock

import pytest

from benchlab_pycore.core import (
    BENCHLAB_BL2_PRODUCT_ID,
    BENCHLAB_ORIGINAL_PRODUCT_ID,
)

from benchlab.flash import flash_tool
from benchlab.flash.flash_manager import FlashResult


# -- _make_manager / per-source device helpers ------------------------------

def _args(source='direct', service_url=None, service_token=None):
    return type('Args', (), {
        'source': source, 'service_url': service_url,
        'service_token': service_token,
    })()


def test_make_manager_passes_source_and_http_kwargs(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        flash_tool, 'FlashManager',
        lambda **kw: captured.update(kw) or MagicMock())

    flash_tool._make_manager(_args(
        source='service_http', service_url='http://host:1234',
        service_token='tok'))

    assert captured == {
        'source': 'service_http', 'base_url': 'http://host:1234',
        'token': 'tok',
    }


@pytest.mark.parametrize("source,device,expected", [
    ('direct', {'port': 'COM5'}, 'COM5'),
    ('named_pipe', {'pipe': 'BenchlabSensorPipe_X'}, 'BenchlabSensorPipe_X'),
    ('service_http', {'uid': 'abc123'}, 'abc123'),
])
def test_device_identifier_per_source(source, device, expected):
    assert flash_tool._device_identifier(_args(source=source), device) == \
        expected


@pytest.mark.parametrize("device,expected", [
    ({'fw': 6}, '0x06'),
    ({'firmwareVersion': 7}, '0x07'),
    ({'fw': None, 'firmwareVersion': None}, 'None'),
])
def test_device_fw_display(device, expected):
    assert flash_tool._device_fw(device) == expected


@pytest.mark.parametrize("device,expected", [
    ({'variant': 'ORIGINAL'}, BENCHLAB_ORIGINAL_PRODUCT_ID),
    ({'variant': 'BL2'}, BENCHLAB_BL2_PRODUCT_ID),
    ({'productId': BENCHLAB_BL2_PRODUCT_ID}, BENCHLAB_BL2_PRODUCT_ID),
])
def test_device_product_id(device, expected):
    assert flash_tool._device_product_id(device) == expected


@pytest.mark.parametrize("device,expected", [
    ({'variant': 'BL2'}, 'BL2'),
    ({'productId': BENCHLAB_ORIGINAL_PRODUCT_ID}, 'ORIGINAL'),
    ({'productId': BENCHLAB_BL2_PRODUCT_ID}, 'BL2'),
    ({}, 'N/A'),
])
def test_device_variant_label(device, expected):
    assert flash_tool._device_variant_label(device) == expected


# -- _parse_device_selection ------------------------------------------------

@pytest.mark.parametrize("choice,count,expected", [
    ("1", 3, [0]),
    ("1,3", 3, [0, 2]),
    (" 2 , 1 ", 3, [1, 0]),
    ("all", 3, [0, 1, 2]),
    ("ALL", 3, [0, 1, 2]),
    ("5", 3, None),
    ("0", 3, None),
    ("abc", 3, None),
    ("", 3, None),
])
def test_parse_device_selection(choice, count, expected):
    assert flash_tool._parse_device_selection(choice, count) == expected


# -- interactive_mode: guided CDC flow --------------------------------------

def _devices():
    return [
        {"port": "COM5", "uid": "U1", "fw": 6, "variant": "ORIGINAL"},
        {"port": "COM6", "uid": "U2", "fw": 7, "variant": "BL2"},
    ]


def test_interactive_mode_no_devices_returns_error(monkeypatch):
    monkeypatch.setattr(flash_tool, "FlashManager", lambda **kw: MagicMock(
        discover_devices=lambda: []))
    monkeypatch.setattr("builtins.input", lambda *a: "")
    assert flash_tool.interactive_mode(object()) == 1


def test_interactive_mode_invalid_selection_returns_error(
        monkeypatch):
    manager = MagicMock(discover_devices=lambda: _devices())
    monkeypatch.setattr(flash_tool, "FlashManager", lambda **kw: manager)
    monkeypatch.setattr("builtins.input", lambda *a: "99")
    assert flash_tool.interactive_mode(object()) == 1


def test_interactive_mode_single_device_happy_path(
        monkeypatch, tmp_path):
    fw_path = tmp_path / "generic_fw.bin"
    fw_path.write_bytes(b"\x00" * 16)

    manager = MagicMock(discover_devices=lambda: _devices())
    manager.flash_many.return_value = [
        FlashResult(port="COM5", uid="U1", ok=True, message="ok"),
    ]
    monkeypatch.setattr(flash_tool, "FlashManager", lambda **kw: manager)

    inputs = iter(["1", str(fw_path), "y", "FLASH", ""])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))

    rc = flash_tool.interactive_mode(object())

    assert rc == 0
    manager.flash_many.assert_called_once()
    called_ports = manager.flash_many.call_args[0][0]
    assert called_ports == ["COM5"]


def test_interactive_mode_all_devices_mixed_results(
        monkeypatch, tmp_path):
    fw_path = tmp_path / "generic_fw.bin"
    fw_path.write_bytes(b"\x00" * 16)

    manager = MagicMock(discover_devices=lambda: _devices())
    manager.flash_many.return_value = [
        FlashResult(port="COM5", uid="U1", ok=True, message="ok"),
        FlashResult(port="COM6", uid="U2", ok=False, message="failed"),
    ]
    monkeypatch.setattr(flash_tool, "FlashManager", lambda **kw: manager)

    inputs = iter(["all", str(fw_path), "y", "FLASH", ""])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))

    rc = flash_tool.interactive_mode(object())

    assert rc == 1  # one device failed
    called_ports = manager.flash_many.call_args[0][0]
    assert called_ports == ["COM5", "COM6"]


def test_interactive_mode_blocks_on_confirmed_variant_mismatch(
        monkeypatch, tmp_path):
    """A benchlab2-named image against an ORIGINAL-only selection must be
    refused, same as the flag-driven cmd_flash path."""
    fw_path = tmp_path / "benchlab2-fw07-v0.7.1-abc.bin"
    fw_path.write_bytes(b"\x00" * 16)

    manager = MagicMock(discover_devices=lambda: _devices())
    monkeypatch.setattr(flash_tool, "FlashManager", lambda **kw: manager)

    inputs = iter(["1", str(fw_path)])  # device 1 is ORIGINAL
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))

    rc = flash_tool.interactive_mode(object())

    assert rc == 1
    manager.flash_many.assert_not_called()


def test_interactive_mode_cancel_at_first_confirmation(
        monkeypatch, tmp_path):
    fw_path = tmp_path / "generic_fw.bin"
    fw_path.write_bytes(b"\x00" * 16)

    manager = MagicMock(discover_devices=lambda: _devices())
    monkeypatch.setattr(flash_tool, "FlashManager", lambda **kw: manager)

    inputs = iter(["1", str(fw_path), "n"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))

    rc = flash_tool.interactive_mode(object())

    assert rc == 0
    manager.flash_many.assert_not_called()


def test_interactive_mode_cancel_at_second_confirmation(
        monkeypatch, tmp_path):
    fw_path = tmp_path / "generic_fw.bin"
    fw_path.write_bytes(b"\x00" * 16)

    manager = MagicMock(discover_devices=lambda: _devices())
    monkeypatch.setattr(flash_tool, "FlashManager", lambda **kw: manager)

    inputs = iter(["1", str(fw_path), "y", "not flash"])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))

    rc = flash_tool.interactive_mode(object())

    assert rc == 0
    manager.flash_many.assert_not_called()


# -- cmd_flash_bare_dfu: variant resolution ---------------------------------

def test_cmd_flash_bare_dfu_uses_explicit_variant(monkeypatch, tmp_path):
    fw_path = tmp_path / "mystery.bin"
    fw_path.write_bytes(b"\x00" * 16)

    manager = MagicMock()
    manager.flash_bare_dfu.return_value = FlashResult(
        port="<bare DFU>", uid=None, ok=True, message="ok")
    monkeypatch.setattr(flash_tool, "FlashManager", lambda **kw: manager)

    args = type("Args", (), {
        "file": str(fw_path), "variant": "benchlab2",
        "verify_only": False, "yes": True,
    })()

    rc = flash_tool.cmd_flash_bare_dfu(args)

    assert rc == 0
    product_id = manager.flash_bare_dfu.call_args[0][0]
    from benchlab_pycore.core import BENCHLAB_BL2_PRODUCT_ID
    assert product_id == BENCHLAB_BL2_PRODUCT_ID


def test_cmd_flash_bare_dfu_prompts_when_filename_unrecognized(
        monkeypatch, tmp_path):
    fw_path = tmp_path / "mystery.bin"
    fw_path.write_bytes(b"\x00" * 16)

    manager = MagicMock()
    manager.flash_bare_dfu.return_value = FlashResult(
        port="<bare DFU>", uid=None, ok=True, message="ok")
    monkeypatch.setattr(flash_tool, "FlashManager", lambda **kw: manager)

    args = type("Args", (), {
        "file": str(fw_path), "variant": None,
        "verify_only": True, "yes": False,
    })()

    monkeypatch.setattr("builtins.input", lambda *a: "1")  # BENCHLAB1

    rc = flash_tool.cmd_flash_bare_dfu(args)

    assert rc == 0
    from benchlab_pycore.core import BENCHLAB_ORIGINAL_PRODUCT_ID
    assert manager.flash_bare_dfu.call_args[0][0] == \
        BENCHLAB_ORIGINAL_PRODUCT_ID


# -- _make_boot0_fallback_prompt --------------------------------------------

def test_boot0_fallback_prompt_confirms_on_enter(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a: "")
    callback = flash_tool._make_boot0_fallback_prompt()
    assert callback("COM5", {"FwVersion": 0x03}) is True


def test_boot0_fallback_prompt_skip_declines(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a: "skip")
    callback = flash_tool._make_boot0_fallback_prompt()
    assert callback("COM5", {"FwVersion": 0x03}) is False


def test_boot0_fallback_prompt_auto_confirm_skips_input(monkeypatch):
    def fail_if_called(*a, **k):
        raise AssertionError("must not call input() when auto_confirm")

    monkeypatch.setattr("builtins.input", fail_if_called)
    callback = flash_tool._make_boot0_fallback_prompt(auto_confirm=True)
    assert callback("COM5", {"FwVersion": 0x03}) is True


def test_interactive_mode_falls_back_to_dfu_for_old_firmware(
        monkeypatch, tmp_path):
    """interactive_mode must pass an on_unsupported_firmware callback into
    flash_many so an old-firmware device isn't just reported as failed."""
    fw_path = tmp_path / "generic_fw.bin"
    fw_path.write_bytes(b"\x00" * 16)

    devices = [
        {"port": "COM5", "uid": "U1", "fw": 3, "variant": "ORIGINAL"},
    ]
    manager = MagicMock(discover_devices=lambda: devices)
    manager.flash_many.return_value = [
        FlashResult(port="COM5", uid="U1", ok=True, message="ok"),
    ]
    monkeypatch.setattr(flash_tool, "FlashManager", lambda **kw: manager)

    inputs = iter(["1", str(fw_path), "y", "FLASH", ""])
    monkeypatch.setattr("builtins.input", lambda *a: next(inputs))

    rc = flash_tool.interactive_mode(object())

    assert rc == 0
    assert manager.flash_many.call_args.kwargs[
        "on_unsupported_firmware"] is not None


def test_cmd_flash_passes_fallback_with_auto_confirm_from_yes(
        monkeypatch, tmp_path):
    fw_path = tmp_path / "generic_fw.bin"
    fw_path.write_bytes(b"\x00" * 16)

    manager = MagicMock()
    manager.discover_devices.return_value = [
        {"port": "COM5", "uid": "U1", "fw": 3, "variant": "ORIGINAL"},
    ]
    manager.identify_device.return_value = {
        "VendorId": 0xEE, "ProductId": 16, "FwVersion": 3, "uid": "U1",
    }
    manager.flash_many.return_value = [
        FlashResult(port="COM5", uid="U1", ok=True, message="ok"),
    ]
    monkeypatch.setattr(flash_tool, "FlashManager", lambda **kw: manager)

    args = type("Args", (), {
        "file": str(fw_path), "source": "direct", "service_url": None,
        "service_token": None, "port": "COM5", "all": False,
        "verify_only": False, "yes": True,
    })()

    rc = flash_tool.cmd_flash(args)

    assert rc == 0
    callback = manager.flash_many.call_args.kwargs[
        "on_unsupported_firmware"]

    # With --yes, the fallback prompt must not block on input().
    def fail_if_called(*a, **k):
        raise AssertionError("must not call input() with --yes")

    monkeypatch.setattr("builtins.input", fail_if_called)
    assert callback("COM5", {"FwVersion": 0x03}) is True
