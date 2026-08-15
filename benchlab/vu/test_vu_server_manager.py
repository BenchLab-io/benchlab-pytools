"""Non-hardware regression tests for benchlab.vu.vu_server_manager.

These tests exercise the process/config lifecycle bugs fixed in the vu bug
sweep (issue #30) with mocks — no real VU-Server subprocess or hardware
dial is required:
- start_vu_server() returning None (not a Popen handle) when its readiness
  check times out, instead of unconditionally reporting success
- vu_server.config only being written once the new server is confirmed up
- terminate_vu_server falling back to proc.kill() when graceful shutdown
  times out
- check_vu_server sending the API key as the query parameter the vendored
  server actually reads, not an HTTP header it ignores
- check_vu_server normalizing "localhost" to 127.0.0.1 so its stated
  timeout is actually honored on Windows (dual-stack IPv4/IPv6 resolution
  otherwise silently doubles the wait for a down server)
"""

import subprocess
from unittest.mock import MagicMock

import pytest

from benchlab.vu import vu_server_manager as vsm


@pytest.fixture
def fake_yaml_config(tmp_path, monkeypatch):
    """Point SERVER_YAML_CONFIG/VU_SERVER_CONFIG/VU_SERVER_DIR at a
    scratch dir."""
    server_dir = tmp_path / "VU-Server"
    server_dir.mkdir()
    (server_dir / "server.py").write_text("# stub\n")
    yaml_cfg = server_dir / "config.yaml"
    yaml_cfg.write_text(
        "server:\n  hostname: localhost\n  port: 5340\n  master_key: testkey\n"
    )
    vu_config = tmp_path / "vu_server.config"

    monkeypatch.setattr(vsm, "VU_SERVER_DIR", server_dir)
    monkeypatch.setattr(vsm, "SERVER_YAML_CONFIG", yaml_cfg)
    monkeypatch.setattr(vsm, "VU_SERVER_CONFIG", vu_config)
    return vu_config


def _fake_popen(returncode=None):
    proc = MagicMock()
    proc.stdout = None
    proc.poll.return_value = returncode
    return proc


def test_start_vu_server_returns_none_and_does_not_write_config_on_timeout(
        fake_yaml_config, monkeypatch):
    """Regression test for issue #30: a failed readiness check used to still
    return the Popen handle and had already overwritten vu_server.config
    before the process was even launched."""
    monkeypatch.setattr(vsm, "check_vu_server", lambda *a, **kw: False)
    monkeypatch.setattr(vsm.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        vsm.subprocess,
        "Popen",
        lambda *a,
        **kw: _fake_popen())

    result = vsm.start_vu_server()

    assert result is None
    assert not fake_yaml_config.exists(
    ), "config must not be written when startup verification fails"


def test_start_vu_server_writes_config_only_after_confirmed_ready(
        fake_yaml_config, monkeypatch):
    calls = {"n": 0}

    def fake_check(url, api_key=""):
        # First call is the "already running?" pre-check (must be False to
        # proceed to launch); subsequent calls are the readiness poll.
        calls["n"] += 1
        return calls["n"] > 1

    monkeypatch.setattr(vsm, "check_vu_server", fake_check)
    monkeypatch.setattr(vsm.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        vsm.subprocess,
        "Popen",
        lambda *a,
        **kw: _fake_popen())

    result = vsm.start_vu_server()

    assert result is not None
    assert fake_yaml_config.exists()
    import json
    written = json.loads(fake_yaml_config.read_text())
    assert written["vu_server_url"] == "http://localhost:5340"
    assert written["api_key"] == "testkey"


def test_start_vu_server_returns_none_when_already_running(
        fake_yaml_config, monkeypatch):
    monkeypatch.setattr(vsm, "check_vu_server", lambda *a, **kw: True)
    result = vsm.start_vu_server()
    assert result is None
    assert not fake_yaml_config.exists()


def test_terminate_vu_server_force_kills_on_timeout(monkeypatch):
    """Regression test for issue #30: a hung child that never responds to
    the graceful shutdown signal used to be left running (orphaned),
    holding the server port, with only a warning logged."""
    proc = _fake_popen(returncode=None)
    proc.wait.side_effect = subprocess.TimeoutExpired(
        cmd="server.py", timeout=5)

    if vsm.IS_WINDOWS:
        monkeypatch.setattr(proc, "send_signal", MagicMock())
    else:
        monkeypatch.setattr(vsm.os, "killpg", MagicMock())
        monkeypatch.setattr(vsm.os, "getpgid", MagicMock(return_value=1))

    vsm.terminate_vu_server(proc)

    proc.kill.assert_called_once()


def test_terminate_vu_server_noop_when_already_exited():
    proc = _fake_popen(returncode=0)
    vsm.terminate_vu_server(proc)
    proc.wait.assert_not_called()


def test_terminate_vu_server_noop_on_none():
    vsm.terminate_vu_server(None)  # must not raise


# ----------------------------------------------------------------------
# check_vu_server auth mechanism
# ----------------------------------------------------------------------

def test_check_vu_server_sends_key_as_query_param_not_header(monkeypatch):
    """Regression test for issue #30: the vendored server reads the API
    key via self.get_argument('key', None) -- a query parameter -- not an
    X-Master-Key header. Sending it as a header meant the key was never
    actually validated by the server."""
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["params"] = params
        captured["headers"] = headers
        resp = MagicMock()
        resp.status_code = 200
        return resp

    monkeypatch.setattr(vsm.requests, "get", fake_get)

    vsm.check_vu_server("http://localhost:5340", "mykey")

    assert captured["params"] == {"key": "mykey"}
    assert not captured["headers"]


def test_check_vu_server_no_params_when_key_empty(monkeypatch):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["params"] = params
        resp = MagicMock()
        resp.status_code = 200
        return resp

    monkeypatch.setattr(vsm.requests, "get", fake_get)

    vsm.check_vu_server("http://localhost:5340")

    assert captured["params"] == {}


def test_check_vu_server_normalizes_localhost_to_ipv4_loopback(monkeypatch):
    """Regression test: 'localhost' resolves to both IPv4 and IPv6 on
    Windows, and requests/urllib3 tries each in sequence, silently doubling
    the effective timeout for a down server. Force 127.0.0.1 explicitly so
    the requested timeout is actually honored."""
    captured = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        resp = MagicMock()
        resp.status_code = 200
        return resp

    monkeypatch.setattr(vsm.requests, "get", fake_get)

    vsm.check_vu_server("http://localhost:5340")

    assert captured["url"] == "http://127.0.0.1:5340/api/v0/dial/list"


def test_check_vu_server_leaves_custom_hostname_untouched(monkeypatch):
    captured = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        resp = MagicMock()
        resp.status_code = 200
        return resp

    monkeypatch.setattr(vsm.requests, "get", fake_get)

    vsm.check_vu_server("http://192.168.1.50:5340")

    assert captured["url"] == "http://192.168.1.50:5340/api/v0/dial/list"
