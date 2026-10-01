"""Protocol-level tests for the pure-Python DFU/DfuSe client; no hardware
or real pyusb device needed -- DfuDevice._ctrl_transfer is monkeypatched."""
import struct

import pytest

from benchlab.flash import dfu


class FakeUsbDevice:
    """Stand-in for usb.core.Device: provides just enough surface for
    DfuDevice.__init__ to claim a DFU interface. ctrl_transfer defaults to
    a benign idle GETSTATUS reply so __init__'s stale-error-state check
    doesn't interfere with tests that install their own _ctrl_transfer
    patch after construction."""

    def __init__(self):
        self.configured = False
        self.claimed_interfaces = []
        self.alt_setting = None

    def set_configuration(self):
        self.configured = True

    def get_active_configuration(self):
        return self

    # usb.util.find_descriptor(cfg, ...) iterates `cfg` looking for a
    # matching interface -- a FakeInterface with the right class/subclass
    # satisfies it without touching real USB descriptors.
    def __iter__(self):
        return iter([FakeInterface()])

    def set_interface_altsetting(self, interface, alternate_setting):
        self.alt_setting = (interface, alternate_setting)

    def ctrl_transfer(self, *args, **kwargs):
        # Only reached during __init__'s stale-error-state GETSTATUS check
        # (test bodies monkeypatch DfuDevice._ctrl_transfer directly for
        # everything after construction). Report dfuIDLE so __init__ never
        # calls clear_status.
        return _status_bytes()


class FakeInterface:
    bInterfaceClass = 0xFE
    bInterfaceSubClass = 1
    bInterfaceNumber = 0
    extra_descriptors = b""


@pytest.fixture
def dev(monkeypatch):
    # usb.util.claim_interface() reaches into device._ctx, which only a
    # real usb.core.Device has -- stub it out for the fake device.
    monkeypatch.setattr(dfu.usb.util, "claim_interface", lambda *a, **k: None)
    return dfu.DfuDevice(FakeUsbDevice())


def _status_bytes(bstatus=0, poll_ms=10, bstate=dfu.STATE_DFU_IDLE,
                  istring=0):
    return bytes([bstatus]) + struct.pack("<I", poll_ms)[:3] + \
        bytes([bstate, istring])


def test_get_status_parses_fields(dev, monkeypatch):
    monkeypatch.setattr(
        dev, "_ctrl_transfer",
        lambda *a, **k: _status_bytes(bstatus=1, poll_ms=5, bstate=2))
    status = dev.get_status()
    assert status.bStatus == 1
    assert status.bwPollTimeout == 5
    assert status.bState == 2


def test_wait_while_state_returns_once_state_changes(dev, monkeypatch):
    responses = [
        _status_bytes(bstate=dfu.STATE_DFU_DOWNLOAD_BUSY, poll_ms=1),
        _status_bytes(bstate=dfu.STATE_DFU_DOWNLOAD_IDLE, poll_ms=1),
    ]
    monkeypatch.setattr(dev, "_ctrl_transfer",
                        lambda *a, **k: responses.pop(0))
    status = dev.wait_while_state(dfu.STATE_DFU_DOWNLOAD_BUSY, timeout=2.0)
    assert status.bState == dfu.STATE_DFU_DOWNLOAD_IDLE


def test_wait_while_state_raises_dfu_error_on_error_state(dev, monkeypatch):
    monkeypatch.setattr(
        dev, "_ctrl_transfer",
        lambda *a, **k: _status_bytes(bstatus=5, bstate=dfu.STATE_DFU_ERROR))
    with pytest.raises(dfu.DfuError):
        dev.wait_while_state(dfu.STATE_DFU_DOWNLOAD_BUSY, timeout=2.0)


def test_wait_while_state_times_out(dev, monkeypatch):
    monkeypatch.setattr(
        dev, "_ctrl_transfer",
        lambda *a, **k: _status_bytes(
            bstate=dfu.STATE_DFU_DOWNLOAD_BUSY, poll_ms=1))
    with pytest.raises(TimeoutError):
        dev.wait_while_state(dfu.STATE_DFU_DOWNLOAD_BUSY, timeout=0.05)


def test_set_address_pointer_sends_dfuse_command(dev, monkeypatch):
    calls = []

    def fake_ctrl(request, value, data_or_length):
        calls.append((request, value, data_or_length))
        if request == dfu._DFU_DNLOAD:
            return None
        return _status_bytes(bstate=dfu.STATE_DFU_DOWNLOAD_IDLE)

    monkeypatch.setattr(dev, "_ctrl_transfer", fake_ctrl)
    dev.set_address_pointer(0x08000000)

    dnload_calls = [c for c in calls if c[0] == dfu._DFU_DNLOAD]
    assert len(dnload_calls) == 1
    _, block_num, payload = dnload_calls[0]
    assert block_num == 0
    assert payload[0] == 0x21  # DfuSe SET ADDRESS POINTER command byte
    assert struct.unpack("<I", payload[1:5])[0] == 0x08000000


def test_erase_page_sends_dfuse_command(dev, monkeypatch):
    calls = []

    def fake_ctrl(request, value, data_or_length):
        calls.append((request, value, data_or_length))
        if request == dfu._DFU_DNLOAD:
            return None
        return _status_bytes(bstate=dfu.STATE_DFU_DOWNLOAD_IDLE)

    monkeypatch.setattr(dev, "_ctrl_transfer", fake_ctrl)
    dev.erase_page(0x0801E000)

    dnload_calls = [c for c in calls if c[0] == dfu._DFU_DNLOAD]
    assert len(dnload_calls) == 1
    _, block_num, payload = dnload_calls[0]
    assert block_num == 0
    assert payload[0] == 0x41  # DfuSe ERASE PAGE command byte
    assert struct.unpack("<I", payload[1:5])[0] == 0x0801E000


def test_leave_dfu_tolerates_device_disappearing(dev, monkeypatch):
    import usb.core

    def fake_ctrl(request, value, data_or_length):
        if request == dfu._DFU_DNLOAD:
            return None
        raise usb.core.USBError("device disappeared")

    monkeypatch.setattr(dev, "_ctrl_transfer", fake_ctrl)
    dev.leave_dfu()  # must not raise


def test_find_dfu_devices_filters_by_vid_pid(monkeypatch):
    seen_kwargs = {}

    def fake_find(**kwargs):
        seen_kwargs.update(kwargs)
        return []

    monkeypatch.setattr(dfu.usb.core, "find", fake_find)
    dfu.find_dfu_devices(vid=0x1234, pid=0x5678)
    assert seen_kwargs["idVendor"] == 0x1234
    assert seen_kwargs["idProduct"] == 0x5678
    assert seen_kwargs["find_all"] is True
