"""Non-hardware regression tests for the launcher bug sweep (issue #35).

Covers:
- launcher.py's _terminate_spawned_process correctly branches on platform
  instead of unconditionally calling os.killpg/os.getpgid (which don't
  exist on Windows and previously made spawned-terminal cleanup a silent
  no-op there).
- main.py's _export_link_env mirrors --remote-*/--no-tls/--topic-pattern
  CLI flags into the env vars link_main.py's _resolve_config already
  reads, so a spawned `link` subprocess (fresh argv, no CLI flags) still
  picks up the parent process's config.
- sources.py's check_and_setup_source("direct") now warns when no device
  is detected instead of unconditionally reporting ready.
- sources.py's check_named_pipe_service closes its pipe handle even when
  WriteFile/ReadFile raises after CreateFile succeeds.
"""

import os
import subprocess
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from benchlab.launcher import _terminate_spawned_process
from benchlab.main import _export_link_env


def _fake_running_proc():
    proc = MagicMock(spec=subprocess.Popen)
    proc.poll.return_value = None
    proc.pid = 4242
    return proc


def test_terminate_spawned_process_windows_uses_taskkill():
    proc = _fake_running_proc()
    with patch.object(sys, "platform", "win32"), \
            patch("benchlab.launcher.subprocess.run") as mock_run:
        _terminate_spawned_process(proc, force=False)
    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "taskkill"
    assert str(proc.pid) in cmd


def test_terminate_spawned_process_posix_uses_killpg():
    # os.killpg/os.getpgid/signal.SIGKILL don't exist on Windows (this test
    # may run there), so patch with create=True to simulate the POSIX
    # branch regardless of host OS.
    proc = _fake_running_proc()
    with (
        patch.object(sys, "platform", "linux"),
        patch(
            "benchlab.launcher.os.getpgid", return_value=99,
            create=True) as mock_getpgid,
        patch("benchlab.launcher.os.killpg", create=True) as mock_killpg,
        patch("benchlab.launcher.signal.SIGKILL", 9, create=True),
        patch("benchlab.launcher.signal.SIGTERM", 15, create=True),
    ):
        _terminate_spawned_process(proc, force=True)
    mock_getpgid.assert_called_once_with(proc.pid)
    mock_killpg.assert_called_once()


def test_terminate_spawned_process_never_raises_on_failure():
    """Regression: the old code's bare except swallowed AttributeError from
    os.killpg not existing on Windows, making cleanup a permanent no-op.
    The new helper must still never raise, but should attempt the correct
    platform call rather than always failing."""
    proc = _fake_running_proc()
    with patch.object(sys, "platform", "win32"), \
            patch("benchlab.launcher.subprocess.run",
                  side_effect=OSError("boom")):
        _terminate_spawned_process(proc, force=False)  # must not raise


def test_terminate_spawned_process_skips_already_exited():
    proc = MagicMock(spec=subprocess.Popen)
    proc.poll.return_value = 0
    with patch("benchlab.launcher.subprocess.run") as mock_run, \
            patch("benchlab.launcher.os.killpg", create=True) as mock_killpg:
        _terminate_spawned_process(proc, force=False)
    mock_run.assert_not_called()
    mock_killpg.assert_not_called()


@pytest.fixture(autouse=True)
def _clean_link_env():
    keys = ["REMOTE_MQTT_HOST", "REMOTE_MQTT_PORT", "REMOTE_MQTT_USER",
            "REMOTE_MQTT_PASS", "LINK_TOPIC_PATTERN", "REMOTE_MQTT_TLS"]
    saved = {k: os.environ.pop(k, None) for k in keys}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_export_link_env_sets_provided_fields():
    args = types.SimpleNamespace(
        remote_host="broker.example.com", remote_port=8883,
        remote_user="u", remote_pass="p", topic_pattern="x/{uid}/y",
        no_tls=True,
    )
    _export_link_env(args)
    assert os.environ["REMOTE_MQTT_HOST"] == "broker.example.com"
    assert os.environ["REMOTE_MQTT_PORT"] == "8883"
    assert os.environ["REMOTE_MQTT_USER"] == "u"
    assert os.environ["REMOTE_MQTT_PASS"] == "p"
    assert os.environ["LINK_TOPIC_PATTERN"] == "x/{uid}/y"
    assert os.environ["REMOTE_MQTT_TLS"] == "false"


def test_export_link_env_leaves_unset_fields_alone():
    args = types.SimpleNamespace(
        remote_host=None, remote_port=None, remote_user=None,
        remote_pass=None, topic_pattern=None, no_tls=False,
    )
    _export_link_env(args)
    for key in ["REMOTE_MQTT_HOST", "REMOTE_MQTT_PORT", "REMOTE_MQTT_USER",
                "REMOTE_MQTT_PASS", "LINK_TOPIC_PATTERN", "REMOTE_MQTT_TLS"]:
        assert key not in os.environ


def test_export_link_env_makes_spawned_link_config_reach_resolve_config():
    """End-to-end regression: values exported here must actually be picked
    up by link_main.py's own config resolution, matching what a spawned
    subprocess (fresh argv, args=None) would see."""
    pytest.importorskip("benchlab.link.link_main")
    from benchlab.link.link_main import _resolve_config

    args = types.SimpleNamespace(
        remote_host="cloud.example.com", remote_port=None,
        remote_user=None, remote_pass=None, topic_pattern=None, no_tls=False,
    )
    _export_link_env(args)
    # simulates a spawned process's fresh state
    cfg = _resolve_config(args=None)
    assert cfg["host"] == "cloud.example.com"


def test_check_and_setup_source_direct_warns_when_no_device(caplog):
    import logging
    from benchlab.sources import check_and_setup_source

    with patch("benchlab.sources._direct_device_available",
               return_value=False):
        with caplog.at_level(logging.WARNING):
            ready = check_and_setup_source("direct")

    # still tolerant — tool itself may catch a device later
    assert ready is True
    assert any("No BENCHLAB device" in rec.message for rec in caplog.records)


def test_check_and_setup_source_direct_no_warning_when_device_present(caplog):
    import logging
    from benchlab.sources import check_and_setup_source

    with patch("benchlab.sources._direct_device_available", return_value=True):
        with caplog.at_level(logging.WARNING):
            ready = check_and_setup_source("direct")

    assert ready is True
    assert not any(
        "No BENCHLAB device" in rec.message for rec in caplog.records)


def test_check_named_pipe_service_closes_handle_on_write_failure():
    """Regression: WriteFile/ReadFile raising after CreateFile succeeds must
    not leak the pipe handle."""
    from benchlab.sources import check_named_pipe_service

    fake_handle = object()
    fake_win32file = MagicMock()
    fake_win32file.CreateFile.return_value = fake_handle
    fake_win32file.WriteFile.side_effect = RuntimeError(
        "simulated write failure")
    fake_win32pipe = MagicMock()

    with patch.object(sys, "platform", "win32"), \
            patch("benchlab.sources._named_pipe_available",
                  return_value=True), \
            patch.dict(sys.modules, {
                "win32file": fake_win32file,
                "win32pipe": fake_win32pipe,
                "pywintypes": MagicMock()}):
        result = check_named_pipe_service()

    assert result is False
    fake_win32file.CloseHandle.assert_called_once_with(fake_handle)
