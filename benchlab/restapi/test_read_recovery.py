"""Fault injection for the collector's serial recovery; no hardware needed."""
from collections import deque
from unittest.mock import MagicMock

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
    monkeypatch.setattr(api, "translate_sensor_struct", lambda _: {"TS_2": 24.0})
    publish = MagicMock()
    monkeypatch.setattr(api, "schedule_update", publish)
    return stop, device, connections, publish


@pytest.mark.parametrize("failure", [None, OSError("USB disconnected")])
def test_failed_read_discards_stale_data_and_reconnects(monkeypatch, reader, failure):
    stop, device, connections, publish = reader
    reads = 0

    def read(*args, **kwargs):
        nonlocal reads
        reads += 1
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
    assert len(device["history"]) == 2
    assert all(s.close.call_count == 1 for s in connections)


@pytest.mark.parametrize("failure", [None, OSError("read failed")])
def test_repeated_read_failures_back_off_even_when_open_succeeds(monkeypatch, reader, failure):
    stop, device, connections, publish = reader

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
    assert all(s.close.call_count == 1 for s in connections)


def test_valid_sample_resets_failure_streak(monkeypatch, reader):
    stop, device, connections, publish = reader
    reads = 0

    def read(*args, **kwargs):
        nonlocal reads
        reads += 1
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


def test_failed_initial_open_clears_discovery_connected_flag(monkeypatch, reader):
    stop, device, connections, publish = reader
    device.update(connected=True, latest={"TS_2": 24.0})
    monkeypatch.setattr(api, "open_serial_connection", lambda _: None)

    def waited(delay):
        assert not device["connected"]
        assert device["latest"]["connected"] is False
        stop.stopped = True

    stop.on_wait = waited
    api.read_device_loop("/fake", "test")
    assert stop.waits == [min(api.RECONNECT_DELAY, 30)]
    publish.assert_not_called()
