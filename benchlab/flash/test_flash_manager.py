"""Fault injection for the flash orchestration layer; no hardware needed."""
from unittest.mock import MagicMock

import pytest

from benchlab_pycore.core import (
    BENCHLAB_BL2_PRODUCT_ID,
    BENCHLAB_ORIGINAL_PRODUCT_ID,
)

from benchlab.flash import flash_manager as fm_module
from benchlab.flash.flash_manager import (
    FlashManager,
    FlashResult,
    UnsupportedFirmwareError,
)


def _device_info(product_id=BENCHLAB_ORIGINAL_PRODUCT_ID, fw=0x06, uid="U1"):
    return {
        "VendorId": 0xEE, "ProductId": product_id, "FwVersion": fw,
        "uid": uid,
    }


# -- verify_image: DfuSe abort-before-upload sequencing --------------------

def test_verify_image_aborts_before_uploading(manager, monkeypatch):
    """set_address_pointer leaves the device in dfuDNLOAD-IDLE; DFU_UPLOAD
    only works from dfuIDLE. Confirmed against real hardware: uploading
    directly after set_address_pointer fails with a USB pipe error without
    an ABORT in between."""
    data = b"\x01\x02\x03\x04"
    fake_dfu_device = MagicMock()
    fake_dfu_device.transfer_size = 2048
    fake_dfu_device.upload.side_effect = [data, b""]
    monkeypatch.setattr(
        fm_module.dfu, "DfuDevice", lambda usb_dev: fake_dfu_device)

    ok = manager.verify_image(
        MagicMock(), data, BENCHLAB_ORIGINAL_PRODUCT_ID)

    assert ok
    fake_dfu_device.set_address_pointer.assert_called_once()
    fake_dfu_device.abort.assert_called_once()
    # abort() must happen after set_address_pointer, before any upload().
    call_order = [c[0] for c in fake_dfu_device.method_calls]
    assert call_order.index("set_address_pointer") < \
        call_order.index("abort") < call_order.index("upload")


@pytest.fixture
def manager():
    return FlashManager()


# -- identify_device / assert_cdc_mode -----------------------------------

def test_identify_device_returns_info_for_known_device(
        manager, monkeypatch):
    monkeypatch.setattr(fm_module, "open_serial_connection",
                        lambda port: MagicMock())
    monkeypatch.setattr(fm_module, "read_device",
                        lambda ser: _device_info())
    info = manager.identify_device("COM5")
    assert info["ProductId"] == BENCHLAB_ORIGINAL_PRODUCT_ID


def test_identify_device_none_when_port_unopenable(manager, monkeypatch):
    monkeypatch.setattr(fm_module, "open_serial_connection",
                        lambda port: None)
    assert manager.identify_device("COM5") is None


def test_identify_device_none_for_unknown_product(manager, monkeypatch):
    monkeypatch.setattr(fm_module, "open_serial_connection",
                        lambda port: MagicMock())
    monkeypatch.setattr(
        fm_module, "read_device",
        lambda ser: {"VendorId": 0xEE, "ProductId": 0x99, "FwVersion": 1})
    assert manager.identify_device("COM5") is None


def test_assert_cdc_mode_raises_when_port_already_in_dfu(
        manager, monkeypatch):
    monkeypatch.setattr(fm_module, "open_serial_connection",
                        lambda port: None)
    with pytest.raises(ConnectionError, match="already be in DFU mode"):
        manager.assert_cdc_mode("COM5")


# -- check_bootloader_supported -------------------------------------------

@pytest.mark.parametrize("fw,should_raise", [
    (0x03, True),
    (0x04, False),
    (0x06, False),
])
def test_check_bootloader_supported_bl1_version_gate(
        manager, fw, should_raise):
    info = _device_info(product_id=BENCHLAB_ORIGINAL_PRODUCT_ID, fw=fw)
    if should_raise:
        with pytest.raises(UnsupportedFirmwareError):
            manager.check_bootloader_supported(
                info, BENCHLAB_ORIGINAL_PRODUCT_ID)
    else:
        manager.check_bootloader_supported(
            info, BENCHLAB_ORIGINAL_PRODUCT_ID)


def test_check_bootloader_supported_bl2_not_version_gated(manager):
    # BL2's CMD_BOOTLOADER gating isn't modeled here (all shipped BL2
    # firmware postdates it) -- should never raise regardless of fw value.
    info = _device_info(product_id=BENCHLAB_BL2_PRODUCT_ID, fw=0x01)
    manager.check_bootloader_supported(info, BENCHLAB_BL2_PRODUCT_ID)


# -- enter_dfu_mode / wait_for_dfu_device ----------------------------------

def test_enter_dfu_mode_times_out_if_port_never_disappears(
        manager, monkeypatch):
    monkeypatch.setattr(fm_module, "open_serial_connection",
                        lambda port: MagicMock())
    monkeypatch.setattr(fm_module.config_io, "enter_bootloader",
                        lambda ser: True)
    monkeypatch.setattr(fm_module, "get_benchlab_ports",
                        lambda: [{"port": "COM5"}])
    monkeypatch.setattr(fm_module.time, "sleep", lambda s: None)
    with pytest.raises(TimeoutError):
        manager.enter_dfu_mode("COM5", timeout=0.01)


def test_enter_dfu_mode_returns_once_port_disappears(manager, monkeypatch):
    calls = {"n": 0}

    def fake_ports():
        calls["n"] += 1
        return [] if calls["n"] > 1 else [{"port": "COM5"}]

    monkeypatch.setattr(fm_module, "open_serial_connection",
                        lambda port: MagicMock())
    monkeypatch.setattr(fm_module.config_io, "enter_bootloader",
                        lambda ser: True)
    monkeypatch.setattr(fm_module, "get_benchlab_ports", fake_ports)
    monkeypatch.setattr(fm_module.time, "sleep", lambda s: None)
    manager.enter_dfu_mode("COM5", timeout=5.0)  # must not raise


def test_wait_for_dfu_device_times_out(manager, monkeypatch):
    monkeypatch.setattr(fm_module.dfu, "find_dfu_devices", lambda: [])
    monkeypatch.setattr(fm_module.time, "sleep", lambda s: None)
    with pytest.raises(TimeoutError, match="WinUSB"):
        manager.wait_for_dfu_device(expected_count=1, timeout=0.01)


def test_wait_for_dfu_device_returns_once_count_matches(
        manager, monkeypatch):
    calls = {"n": 0}

    def fake_find():
        calls["n"] += 1
        return [MagicMock()] if calls["n"] > 1 else []

    monkeypatch.setattr(fm_module.dfu, "find_dfu_devices", fake_find)
    monkeypatch.setattr(fm_module.time, "sleep", lambda s: None)
    devices = manager.wait_for_dfu_device(expected_count=1, timeout=5.0)
    assert len(devices) == 1


# -- flash_many: continues past per-device failures ------------------------

@pytest.mark.parametrize("failing_port", ["COM5", "COM6", "COM7"])
def test_flash_many_continues_past_one_device_failure(
        manager, monkeypatch, tmp_path, failing_port):
    image_path = tmp_path / "fw.bin"
    image_path.write_bytes(b"\x00" * 16)

    ports = ["COM5", "COM6", "COM7"]
    infos = {
        p: _device_info(uid=f"U-{p}") for p in ports
    }

    monkeypatch.setattr(
        manager, "identify_device", lambda port: infos[port])

    def fake_flash_one(port, data, product_id, verify_only=False,
                       progress_cb=None):
        if port == failing_port:
            return FlashResult(
                port=port, uid=infos[port]["uid"], ok=False,
                message="simulated failure")
        return FlashResult(
            port=port, uid=infos[port]["uid"], ok=True, message="ok")

    monkeypatch.setattr(manager, "flash_one", fake_flash_one)

    results = manager.flash_many(ports, image_path)

    assert len(results) == 3
    by_port = {r.port: r for r in results}
    assert not by_port[failing_port].ok
    for port in ports:
        if port != failing_port:
            assert by_port[port].ok


def test_flash_many_reports_unidentifiable_device_without_aborting_batch(
        manager, monkeypatch, tmp_path):
    image_path = tmp_path / "fw.bin"
    image_path.write_bytes(b"\x00" * 16)

    def fake_identify(port):
        return None if port == "COM5" else _device_info(uid=port)

    monkeypatch.setattr(manager, "identify_device", fake_identify)
    monkeypatch.setattr(
        manager, "flash_one",
        lambda port, data, product_id, verify_only=False, progress_cb=None:
            FlashResult(port=port, uid=port, ok=True, message="ok"))

    results = manager.flash_many(["COM5", "COM6"], image_path)

    assert len(results) == 2
    by_port = {r.port: r for r in results}
    assert not by_port["COM5"].ok
    assert by_port["COM6"].ok


# -- flash_one: exceptions become failed FlashResults, never raise --------

def test_flash_one_catches_exceptions_and_returns_failed_result(
        manager, monkeypatch):
    monkeypatch.setattr(
        manager, "assert_cdc_mode",
        lambda port: (_ for _ in ()).throw(ConnectionError("boom")))

    result = manager.flash_one("COM5", b"\x00" * 16,
                               BENCHLAB_ORIGINAL_PRODUCT_ID)
    assert isinstance(result, FlashResult)
    assert not result.ok
    assert "boom" in result.message


# -- flash_bare_dfu: manually-jumpered device, no CDC port -----------------

def test_flash_bare_dfu_flashes_without_cdc_round_trip(
        manager, monkeypatch):
    """A device manually jumpered into DFU (old BL1 firmware below
    MIN_BOOTLOADER_FW_VERSION) has no CDC port at all -- flash_bare_dfu
    must never call assert_cdc_mode/enter_dfu_mode, only wait for the
    already-present USB DFU device and flash it directly."""
    data = b"\x00" * 16
    fake_usb_dev = MagicMock()
    monkeypatch.setattr(
        manager, "wait_for_dfu_device", lambda expected_count=1,
        timeout=10.0: [fake_usb_dev])
    monkeypatch.setattr(
        manager, "verify_image", lambda usb_dev, d, pid: True)
    monkeypatch.setattr(
        manager, "erase_and_flash",
        lambda usb_dev, d, pid, progress_cb=None: None)
    monkeypatch.setattr(fm_module.dfu, "DfuDevice", lambda dev: MagicMock())

    def fail_if_called(*a, **k):
        raise AssertionError("must not touch CDC for a bare-DFU device")

    monkeypatch.setattr(manager, "assert_cdc_mode", fail_if_called)
    monkeypatch.setattr(manager, "enter_dfu_mode", fail_if_called)

    result = manager.flash_bare_dfu(BENCHLAB_ORIGINAL_PRODUCT_ID, data)

    assert result.ok
    assert "Flashed and verified successfully" in result.message


def test_flash_bare_dfu_catches_exceptions(manager, monkeypatch):
    monkeypatch.setattr(
        manager, "wait_for_dfu_device",
        lambda expected_count=1, timeout=10.0: (_ for _ in ()).throw(
            TimeoutError("no DFU device found")))

    result = manager.flash_bare_dfu(
        BENCHLAB_ORIGINAL_PRODUCT_ID, b"\x00" * 16)

    assert not result.ok
    assert "no DFU device found" in result.message


# -- named_pipe / service_http sources: the C# service owns the port -------

def _fake_client_info(product_id=BENCHLAB_ORIGINAL_PRODUCT_ID, fw=6,
                      guid="U1", port="COM9", vendor_id=0xEE):
    """Shape returned by ConfigClient.get_device_info() for named_pipe/
    service_http -- camelCase keys, unlike DirectConfigClient's PascalCase."""
    return {
        "vendorId": vendor_id, "productId": product_id,
        "firmwareVersion": fw, "guid": guid, "port": port,
    }


@pytest.fixture
def named_pipe_manager():
    return FlashManager(source="named_pipe")


@pytest.fixture
def http_manager():
    return FlashManager(
        source="service_http", base_url="http://localhost:8585",
        token="secret")


def test_identify_device_normalizes_named_pipe_shape(
        named_pipe_manager, monkeypatch):
    fake_client = MagicMock()
    fake_client.get_device_info.return_value = _fake_client_info()
    monkeypatch.setattr(
        fm_module, "create_config_client", lambda *a, **k: fake_client)

    info = named_pipe_manager.identify_device("BenchlabSensorPipe_X")

    assert info == {
        "VendorId": 0xEE, "ProductId": BENCHLAB_ORIGINAL_PRODUCT_ID,
        "FwVersion": 6, "uid": "U1", "port": "COM9",
    }
    fake_client.close.assert_called_once()


def test_identify_device_passes_base_url_and_token_for_http(
        http_manager, monkeypatch):
    captured = {}

    def fake_create(source, identifier, base_url=None, token=None):
        captured["source"] = source
        captured["identifier"] = identifier
        captured["base_url"] = base_url
        captured["token"] = token
        client = MagicMock()
        client.get_device_info.return_value = _fake_client_info()
        return client

    monkeypatch.setattr(fm_module, "create_config_client", fake_create)
    http_manager.identify_device("some-uid")

    assert captured == {
        "source": "service_http", "identifier": "some-uid",
        "base_url": "http://localhost:8585", "token": "secret",
    }


def test_identify_device_none_for_unreachable_named_pipe_device(
        named_pipe_manager, monkeypatch):
    def fake_create(*a, **k):
        raise ConnectionError("pipe not available")

    monkeypatch.setattr(fm_module, "create_config_client", fake_create)
    assert named_pipe_manager.identify_device("BenchlabSensorPipe_X") is None


def test_identify_device_none_when_get_device_info_returns_none(
        named_pipe_manager, monkeypatch):
    fake_client = MagicMock()
    fake_client.get_device_info.return_value = None
    monkeypatch.setattr(
        fm_module, "create_config_client", lambda *a, **k: fake_client)

    assert named_pipe_manager.identify_device("BenchlabSensorPipe_X") is None
    fake_client.close.assert_called_once()


def test_discover_devices_uses_config_manager_for_named_pipe(
        named_pipe_manager, monkeypatch):
    fake_cm = MagicMock()
    fake_cm.discover_devices.return_value = [{"pipe": "p1"}]
    monkeypatch.setattr(
        fm_module, "ConfigManager", lambda **kwargs: fake_cm)

    result = named_pipe_manager.discover_devices()

    assert result == [{"pipe": "p1"}]


def test_enter_dfu_mode_uses_config_client_for_named_pipe(
        named_pipe_manager, monkeypatch):
    fake_client = MagicMock()
    fake_client.enter_bootloader.return_value = True
    monkeypatch.setattr(
        fm_module, "create_config_client", lambda *a, **k: fake_client)
    monkeypatch.setattr(fm_module, "get_benchlab_ports", lambda: [])
    monkeypatch.setattr(fm_module.time, "sleep", lambda s: None)

    named_pipe_manager.enter_dfu_mode(
        "BenchlabSensorPipe_X", port="COM9", timeout=5.0)

    fake_client.enter_bootloader.assert_called_once()
    fake_client.close.assert_called_once()


def test_enter_dfu_mode_raises_when_service_rejects_bootloader(
        named_pipe_manager, monkeypatch):
    fake_client = MagicMock()
    fake_client.enter_bootloader.return_value = False
    monkeypatch.setattr(
        fm_module, "create_config_client", lambda *a, **k: fake_client)

    with pytest.raises(ConnectionError, match="rejected the bootloader"):
        named_pipe_manager.enter_dfu_mode(
            "BenchlabSensorPipe_X", port="COM9")


def test_enter_dfu_mode_requires_port_for_named_pipe(named_pipe_manager):
    with pytest.raises(ValueError, match="port is required"):
        named_pipe_manager.enter_dfu_mode("BenchlabSensorPipe_X")


def test_leave_and_reboot_skips_reidentify_for_named_pipe(
        named_pipe_manager, monkeypatch):
    """For named_pipe/service_http, re-identifying by the physical COM
    port after leaving DFU would be wrong (identify_device expects a pipe
    name/UID, not a port) -- confirm it's skipped, not misused."""
    monkeypatch.setattr(
        fm_module, "get_benchlab_ports", lambda: [{"port": "COM9"}])
    monkeypatch.setattr(fm_module.dfu, "DfuDevice", lambda dev: MagicMock())

    def fail_if_called(identifier):
        raise AssertionError(
            "must not call identify_device with a raw COM port for "
            "named_pipe")

    monkeypatch.setattr(
        named_pipe_manager, "identify_device", fail_if_called)

    result = named_pipe_manager.leave_and_reboot(MagicMock(), "COM9")

    assert result is None


def test_flash_one_resolves_physical_port_for_named_pipe(
        named_pipe_manager, monkeypatch):
    """flash_one must pass the device's reported physical port (not the
    pipe-name identifier) to enter_dfu_mode/leave_and_reboot."""
    monkeypatch.setattr(
        named_pipe_manager, "assert_cdc_mode",
        lambda identifier: _device_info(uid="U1") | {"port": "COM9"})
    monkeypatch.setattr(
        named_pipe_manager, "check_bootloader_supported", lambda *a: None)

    captured = {}

    def fake_enter_dfu_mode(identifier, port=None, timeout=5.0):
        captured["identifier"] = identifier
        captured["port"] = port

    monkeypatch.setattr(
        named_pipe_manager, "enter_dfu_mode", fake_enter_dfu_mode)
    monkeypatch.setattr(
        named_pipe_manager, "wait_for_dfu_device",
        lambda expected_count=1: [MagicMock()])
    monkeypatch.setattr(
        named_pipe_manager, "_erase_verify_leave",
        lambda *a, **k: (True, "ok"))

    result = named_pipe_manager.flash_one(
        "BenchlabSensorPipe_X", b"\x00" * 16, BENCHLAB_ORIGINAL_PRODUCT_ID)

    assert captured == {"identifier": "BenchlabSensorPipe_X", "port": "COM9"}
    assert result.ok
    assert result.port == "BenchlabSensorPipe_X"
