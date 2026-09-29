"""Fault injection for the collector's serial recovery; no hardware needed."""
from collections import deque
from unittest.mock import MagicMock, call

import pytest

from benchlab.restapi import telemetry_api as api


class StopEvent:
    def __init__(self):
        self.stopped = False
        self.waits = []
        self.on_wait = lambda _: None

    def is_set(self):
        return self.stopped

    def wait(self, delay):
        self.waits.append(delay)
        self.on_wait(delay)
        return self.stopped


@pytest.fixture
def reader(monkeypatch):
    stop = StopEvent()
    device = {"connected": False, "latest": {}, "history": deque()}
    connections = []

    def open_port(_):
        ser = MagicMock()
        connections.append(ser)
        if len(connections) > 30:
            stop.stopped = True  # Bound regressions that never reach backoff.
        return ser

    monkeypatch.setattr(api, "shutdown_event", stop)
    monkeypatch.setattr(api, "devices_data", {"test": device})
    monkeypatch.setattr(api, "open_serial_connection", open_port)
    monkeypatch.setattr(api, "read_device", lambda _: {"ProductId": 16})
    monkeypatch.setattr(
        api, "translate_sensor_struct", lambda _: {"TS_2": 24.0})
    trace = []
    snapshots = []

    def device_event(uid, event):
        # Record rather than assert here: the reader catches exceptions.
        unlocked = api.data_lock.acquire(blocking=False)
        if unlocked:
            api.data_lock.release()
        wire = api._device_to_wire_shape(uid) if unlocked else {}
        snapshots.append((event, device["connected"],
                          device["latest"].copy(), unlocked, wire))
        trace.append(event)

    events = MagicMock(side_effect=device_event)
    publish = MagicMock(side_effect=lambda *_: trace.append("telemetry"))
    monkeypatch.setattr(api, "schedule_update", publish)
    monkeypatch.setattr(api, "schedule_device_event", events)
    yield stop, device, connections, publish, events, trace

    for event, connected, latest, unlocked, wire in snapshots:
        assert unlocked, "Device event scheduled while holding data_lock"
        assert connected == (event == "connected")
        assert wire["status"] == event.upper()
        if connected:
            assert latest["TS_2"] == 24.0
        else:
            assert latest["connected"] is False
            assert latest["TS_2"] == 0


@pytest.mark.parametrize("failure", [None, OSError("USB disconnected")])
def test_failed_read_discards_stale_data_and_reconnects(
        monkeypatch, reader, failure):
    stop, device, connections, publish, events, trace = reader
    reads = 0

    def read(*args, **kwargs):
        nonlocal reads
        reads += 1
        if reads >= 3:
            stop.stopped = True
        if reads == 2:
            assert device["connected"]
            if isinstance(failure, Exception):
                raise failure
            return failure
        if reads == 3:
            assert not device["connected"]
            assert device["latest"]["connected"] is False
            assert len(connections) == 2
            stop.stopped = True
        return object()

    monkeypatch.setattr(api, "read_sensors", read)
    api.read_device_loop("/fake", "test")
    assert publish.call_count == 2
    assert trace == ["connected", "telemetry", "disconnected",
                     "connected", "telemetry", "disconnected"]
    assert events.call_args_list == [
        call("test", "connected"), call("test", "disconnected"),
        call("test", "connected"), call("test", "disconnected"),
    ]
    assert len(device["history"]) == 2
    assert all(s.close.call_count == 1 for s in connections)


@pytest.mark.parametrize("failure", [None, OSError("read failed")])
def test_repeated_read_failures_back_off_even_when_open_succeeds(
        monkeypatch, reader, failure):
    stop, device, connections, publish, events, trace = reader

    def read(*args, **kwargs):
        assert not device["connected"]
        if isinstance(failure, Exception):
            raise failure
        return failure

    def waited(delay):
        assert not device["connected"]
        assert device["latest"]["connected"] is False
        stop.stopped = True

    stop.on_wait = waited
    monkeypatch.setattr(api, "read_sensors", read)
    api.read_device_loop("/fake", "test")
    assert len(connections) == 10
    assert stop.waits == [min(api.RECONNECT_DELAY * 10, 30)]
    publish.assert_not_called()
    assert not device["history"]
    events.assert_not_called()
    assert all(s.close.call_count == 1 for s in connections)


def test_valid_sample_resets_failure_streak(monkeypatch, reader):
    stop, device, connections, publish, events, trace = reader
    reads = 0

    def read(*args, **kwargs):
        nonlocal reads
        reads += 1
        if reads > 30:
            stop.stopped = True
        if reads == 10:
            return object()
        return None

    def waited(delay):
        if delay == api.poll_interval:
            assert device["connected"]
        else:
            assert reads == 20
            stop.stopped = True

    stop.on_wait = waited
    monkeypatch.setattr(api, "read_sensors", read)
    api.read_device_loop("/fake", "test")
    assert stop.waits == [api.poll_interval, min(api.RECONNECT_DELAY * 10, 30)]
    publish.assert_called_once()
    assert len(device["history"]) == 1
    assert trace == ["connected", "telemetry", "disconnected"]


@pytest.mark.parametrize("initially_connected", [False, True])
def test_failed_initial_open_clears_discovery_connected_flag(
        monkeypatch, reader, initially_connected):
    stop, device, connections, publish, events, trace = reader
    device.update(connected=initially_connected, latest={"TS_2": 24.0})
    monkeypatch.setattr(api, "open_serial_connection", lambda _: None)

    def waited(delay):
        assert not device["connected"]
        assert device["latest"]["connected"] is False
        stop.stopped = True

    stop.on_wait = waited
    api.read_device_loop("/fake", "test")
    assert stop.waits == [min(api.RECONNECT_DELAY, 30)]
    publish.assert_not_called()
    assert trace == (["disconnected"] if initially_connected else [])


def test_steady_samples_emit_one_connection_and_shutdown_event(
        monkeypatch, reader):
    stop, device, connections, publish, events, trace = reader
    reads = 0

    def read(*args, **kwargs):
        nonlocal reads
        reads += 1
        if reads >= 3:
            stop.stopped = True
        return object()

    monkeypatch.setattr(api, "read_sensors", read)
    api.read_device_loop("/fake", "test")
    assert trace == ["connected", "telemetry", "telemetry", "telemetry",
                     "disconnected"]
    assert len(connections) == 1
    assert publish.call_count == 3
    assert not device["connected"]
    assert connections[0].close.call_count == 1


@pytest.mark.parametrize("failure", [None, OSError("read failed")])
def test_disconnect_event_precedes_backoff(monkeypatch, reader, failure):
    stop, device, connections, publish, events, trace = reader
    reads = 0
    backoff_traces = []

    def read(*args, **kwargs):
        nonlocal reads
        reads += 1
        if reads > 15:
            stop.stopped = True
        if reads == 1:
            return object()
        if isinstance(failure, Exception):
            raise failure
        return failure

    def waited(delay):
        if delay != api.poll_interval:
            backoff_traces.append(trace.copy())
            stop.stopped = True

    stop.on_wait = waited
    monkeypatch.setattr(api, "read_sensors", read)
    api.read_device_loop("/fake", "test")
    assert reads == 11
    assert backoff_traces == [["connected", "telemetry", "disconnected"]]
    assert trace == backoff_traces[0]
    assert stop.waits == [api.poll_interval, min(api.RECONNECT_DELAY * 10, 30)]
