"""Non-hardware regression tests for benchlab.vu.vu_tui.

These tests exercise the pure-logic bugs fixed in the vu bug sweep
(issue #30) with no curses screen or hardware dial required:
- _parse_float_or falling back on invalid input instead of raising
  (previously the MIN/MAX prompts in dial_mapping() crashed the whole TUI
  on a ValueError from a bare float(inp) call)
- VUTUI.cleanup() no longer taking a dead server_proc parameter
"""

from unittest.mock import MagicMock

from benchlab.vu.vu_tui import _parse_float_or, VUTUI


def test_parse_float_or_valid_input():
    assert _parse_float_or("3.5", 0) == 3.5


def test_parse_float_or_empty_input_uses_fallback():
    assert _parse_float_or("", 42) == 42


def test_parse_float_or_invalid_input_uses_fallback_instead_of_raising():
    """Regression test for issue #30: dial_mapping()'s MIN/MAX prompts used
    to call float(inp) directly with no guard, crashing the entire curses
    TUI session on a typo."""
    assert _parse_float_or("not-a-number", 42) == 42


def test_parse_float_or_negative_and_zero():
    assert _parse_float_or("-5", 0) == -5.0
    assert _parse_float_or("0", 42) == 0.0


def test_cleanup_closes_benchlab_ports_without_server_proc_arg():
    """Regression test for issue #30: cleanup() used to take a server_proc
    parameter unrelated to self.server_proc, making its "terminate server"
    branch dead code since callers always invoked cleanup() with no args."""
    fake = MagicMock()
    fake.benchlabs = [MagicMock(close=MagicMock())]
    fake.benchlabs[0].get = lambda k, d=None: "COM3" if k == "port" else d

    VUTUI.cleanup(fake)  # must not raise, must not require server_proc

    fake.benchlabs[0].close.assert_called_once()
