"""Tests for the flash tool CLI logic (device selection parsing, the
interactive guided flow, and the bare-DFU variant prompt); no hardware
needed -- FlashManager is monkeypatched."""
from unittest.mock import MagicMock

import pytest

from benchlab.flash import flash_tool
from benchlab.flash.flash_manager import FlashResult


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
    monkeypatch.setattr(flash_tool, "FlashManager", lambda: MagicMock(
        discover_devices=lambda: []))
    monkeypatch.setattr("builtins.input", lambda *a: "")
    assert flash_tool.interactive_mode(object()) == 1


def test_interactive_mode_invalid_selection_returns_error(
        monkeypatch):
    manager = MagicMock(discover_devices=lambda: _devices())
    monkeypatch.setattr(flash_tool, "FlashManager", lambda: manager)
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
    monkeypatch.setattr(flash_tool, "FlashManager", lambda: manager)

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
    monkeypatch.setattr(flash_tool, "FlashManager", lambda: manager)

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
    monkeypatch.setattr(flash_tool, "FlashManager", lambda: manager)

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
    monkeypatch.setattr(flash_tool, "FlashManager", lambda: manager)

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
    monkeypatch.setattr(flash_tool, "FlashManager", lambda: manager)

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
    monkeypatch.setattr(flash_tool, "FlashManager", lambda: manager)

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
    monkeypatch.setattr(flash_tool, "FlashManager", lambda: manager)

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
