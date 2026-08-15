"""Non-hardware regression tests for the interactive menu redesign (issue #36).

Covers:
- prefs.py's load/save/record round-trip, and tolerance of a missing or
  corrupt prefs file.
- menu.py's pure-logic helpers (_available_sources filtering) that don't
  require a real TTY/prompt_toolkit interaction.
- main.py falls back to menu_classic.interactive_loop when benchlab.menu
  fails to import (e.g. prompt_toolkit not installed).
"""

import importlib
import sys
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# prefs.py
# ---------------------------------------------------------------------------

@pytest.fixture
def prefs_module(tmp_path, monkeypatch):
    import benchlab.prefs as prefs
    monkeypatch.setattr(prefs, "PREFS_FILE", tmp_path / "prefs.json")
    return prefs


def test_load_prefs_defaults_when_file_missing(prefs_module):
    prefs = prefs_module.load_prefs()
    assert prefs == {
        "last_tool_ids": [],
        "last_source": None,
        "source_params": {}}


def test_load_prefs_tolerates_corrupt_json(prefs_module):
    prefs_module.PREFS_FILE.write_text("{not valid json", encoding="utf-8")
    prefs = prefs_module.load_prefs()
    assert prefs["last_tool_ids"] == []


def test_record_launch_round_trips(prefs_module):
    prefs_module.record_launch(["tui", "vu"], "fastapi", {"port": 8000})
    loaded = prefs_module.load_prefs()
    assert loaded["last_tool_ids"] == ["tui", "vu"]
    assert loaded["last_source"] == "fastapi"
    assert loaded["source_params"]["fastapi"] == {"port": 8000}


def test_get_source_params_returns_empty_for_unset_source(prefs_module):
    prefs_module.record_launch(["tui"], "fastapi", {"port": 8000})
    assert prefs_module.get_source_params("mqtt") == {}


def test_record_launch_without_params_does_not_clear_prior_ones(prefs_module):
    prefs_module.record_launch(["tui"], "fastapi", {"port": 8000})
    prefs_module.record_launch(["tui"], "mqtt")  # no source_params for mqtt
    assert prefs_module.get_source_params("fastapi") == {"port": 8000}


def test_save_prefs_survives_unwritable_path(prefs_module, monkeypatch):
    """A failed save must not raise -- prefs are a convenience, not
    critical."""
    monkeypatch.setattr(
        prefs_module,
        "PREFS_FILE",
        Path("/nonexistent_dir_xyz/prefs.json"))
    prefs_module.save_prefs({"last_tool_ids": [],
                             "last_source": None,
                             "source_params": {}})  # must not raise


# ---------------------------------------------------------------------------
# menu.py pure-logic helpers
# ---------------------------------------------------------------------------

def test_available_sources_excludes_direct_for_multi_tool():
    from benchlab.menu import _available_sources
    sources = _available_sources(is_multi=True, supported_sources=None)
    assert "direct" not in [s for s, _ in sources]


def test_available_sources_includes_direct_for_single_tool():
    from benchlab.menu import _available_sources
    sources = _available_sources(is_multi=False, supported_sources=None)
    assert "direct" in [s for s, _ in sources]


def test_available_sources_filters_by_supported_sources():
    from benchlab.menu import _available_sources
    sources = _available_sources(
        is_multi=False, supported_sources=[
            "direct", "named_pipe"])
    keys = [s for s, _ in sources]
    assert set(keys) <= {"direct", "named_pipe"}
    assert "fastapi" not in keys


def test_available_sources_excludes_named_pipe_off_windows(monkeypatch):
    from benchlab.menu import _available_sources
    monkeypatch.setattr(sys, "platform", "linux")
    sources = _available_sources(is_multi=False, supported_sources=None)
    assert "named_pipe" not in [s for s, _ in sources]


# ---------------------------------------------------------------------------
# menu.py _combined_supported_sources (three-column picker's live filtering)
# ---------------------------------------------------------------------------

def test_combined_supported_sources_no_tools_selected():
    from benchlab.menu import _combined_supported_sources
    assert _combined_supported_sources([]) is None


def test_combined_supported_sources_unrestricted_tool():
    from benchlab.menu import _combined_supported_sources
    # tui has no supported_sources restriction
    assert _combined_supported_sources(["tui"]) is None


def test_combined_supported_sources_single_restricted_tool():
    from benchlab.menu import _combined_supported_sources
    result = _combined_supported_sources(["config"])
    assert set(result) == {"direct", "named_pipe"}


def test_combined_supported_sources_is_intersection_across_tools():
    """Regression: when multiple tools are selected in the Tools column,
    the Data Source column must only offer sources ALL selected tools
    support -- not the union (which could offer a source one of the tools
    can't actually use)."""
    from benchlab.menu import _combined_supported_sources

    # config only supports direct/named_pipe; tui supports everything.
    # The intersection with an unrestricted tool is still config's set.
    result = _combined_supported_sources(["config", "tui"])
    assert set(result) == {"direct", "named_pipe"}


def test_combined_supported_sources_disjoint_restrictions_yield_empty():
    """If two selected tools' supported_sources don't overlap at all, the
    picker should show 'no compatible source', not silently pick a source
    one of the tools can't actually use."""
    from benchlab.menu import _combined_supported_sources, CONSUMER_TOOLS
    import unittest.mock as mock

    fake_tools = {
        "a": {"supported_sources": ["direct"]},
        "b": {"supported_sources": ["mqtt"]},
    }
    with mock.patch.dict(CONSUMER_TOOLS, fake_tools, clear=True):
        result = _combined_supported_sources(["a", "b"])
    assert result == []


# ---------------------------------------------------------------------------
# menu.py _fuzzy_pick matching + console-encoding safety
# ---------------------------------------------------------------------------

def test_fuzzy_pick_exact_label_match():
    from benchlab.menu import _fuzzy_pick
    import unittest.mock as mock

    items = [("tui", "TUI - Interactive terminal user interface"),
             ("vu", "VU Dials - Analog-style VU meter dials")]
    with mock.patch("builtins.input", return_value=items[0][1]):
        assert _fuzzy_pick(items, "pick") == "tui"


def test_fuzzy_pick_unambiguous_substring_match():
    from benchlab.menu import _fuzzy_pick
    import unittest.mock as mock

    items = [("tui", "TUI - Interactive terminal user interface"),
             ("vu", "VU Dials - Analog-style VU meter dials")]
    with mock.patch("builtins.input", return_value="VU"):
        assert _fuzzy_pick(items, "pick") == "vu"


def test_fuzzy_pick_no_match_returns_none():
    from benchlab.menu import _fuzzy_pick
    import unittest.mock as mock

    items = [("tui", "TUI - Interactive terminal user interface")]
    with mock.patch("builtins.input", return_value="nonsense"):
        assert _fuzzy_pick(items, "pick") is None


def test_fuzzy_pick_ambiguous_substring_returns_none():
    from benchlab.menu import _fuzzy_pick
    import unittest.mock as mock

    items = [("tui", "TUI tool"), ("tui2", "TUI tool alternate")]
    with mock.patch("builtins.input", return_value="TUI"):
        assert _fuzzy_pick(items, "pick") is None


def test_fuzzy_pick_accepts_number_selection():
    """A first-time user who doesn't know what to type should be able to
    just enter the number shown next to an option."""
    from benchlab.menu import _fuzzy_pick
    import unittest.mock as mock

    items = [("tui", "TUI - Interactive terminal user interface"),
             ("vu", "VU Dials - Analog-style VU meter dials")]
    with mock.patch("builtins.input", return_value="2"):
        assert _fuzzy_pick(items, "pick") == "vu"


def test_fuzzy_pick_out_of_range_number_returns_none():
    from benchlab.menu import _fuzzy_pick
    import unittest.mock as mock

    items = [("tui", "TUI - Interactive terminal user interface")]
    with mock.patch("builtins.input", return_value="99"):
        assert _fuzzy_pick(items, "pick") is None


def test_fuzzy_pick_prints_full_option_list_up_front(capsys):
    """The sequential-fallback picker must show every option before
    prompting, so a first-time user isn't left guessing what to type."""
    from benchlab.menu import _fuzzy_pick
    import unittest.mock as mock

    items = [("tui", "TUI - Interactive terminal user interface"),
             ("vu", "VU Dials - Analog-style VU meter dials")]
    with mock.patch("builtins.input", return_value="1"):
        _fuzzy_pick(items, "Select a tool:")

    out = capsys.readouterr().out
    assert "1. TUI - Interactive terminal user interface" in out
    assert "2. VU Dials - Analog-style VU meter dials" in out


def test_multi_pick_parses_comma_separated_numbers():
    """_multi_pick is the sequential-fallback multi-select (used when the
    full-screen three-column picker can't run) -- plain input(), no
    prompt_toolkit dialog involved."""
    from benchlab.menu import _multi_pick
    import unittest.mock as mock

    items = [("tui", "TUI"), ("vu", "VU Dials"), ("graph", "Graph")]

    with mock.patch("builtins.input", return_value="1,3"):
        assert _multi_pick(items, "Select Tools") == ["tui", "graph"]


def test_multi_pick_supports_all():
    from benchlab.menu import _multi_pick
    import unittest.mock as mock

    items = [("tui", "TUI"), ("vu", "VU Dials")]

    with mock.patch("builtins.input", return_value="all"):
        assert _multi_pick(items, "Select Tools") == ["tui", "vu"]


def test_multi_pick_ignores_out_of_range_and_invalid_numbers():
    from benchlab.menu import _multi_pick
    import unittest.mock as mock

    items = [("tui", "TUI"), ("vu", "VU Dials")]

    with mock.patch("builtins.input", return_value="1,99,x"):
        assert _multi_pick(items, "Select Tools") == ["tui"]


# ---------------------------------------------------------------------------
# _sequential_pick (whole fallback flow: mode choice -> tools/provider)
# ---------------------------------------------------------------------------

def test_sequential_pick_provider_mode_returns_tagged_result():
    from benchlab.menu import _sequential_pick
    import unittest.mock as mock

    # First input(): mode choice ("2" = provider). Second: provider choice.
    with mock.patch("builtins.input", side_effect=["2", "1"]):
        result = _sequential_pick([], None)

    assert result == ("provider", "fastapi")


def test_sequential_pick_tools_mode_returns_tagged_result():
    from benchlab.menu import _sequential_pick
    import unittest.mock as mock

    # mode="1" (tools), single/multi="s", tool number, source number.
    with mock.patch("builtins.input", side_effect=["1", "s", "1", "1"]):
        result = _sequential_pick([], None)

    assert result[0] == "tools"
    assert isinstance(result[1], list) and len(result[1]) == 1


def test_sequential_pick_cancelled_mode_choice_returns_none():
    from benchlab.menu import _sequential_pick
    import unittest.mock as mock

    with mock.patch("builtins.input", side_effect=EOFError):
        assert _sequential_pick([], None) is None


# ---------------------------------------------------------------------------
# Textual full-screen picker (Tools | Data Source, plus Data Provider tab)
# ---------------------------------------------------------------------------
# Driven headlessly via Textual's own App.run_test() pilot -- no real
# terminal needed, and no extra pytest-asyncio dependency since these
# wrap a single asyncio.run() per test.

def _run_async(coro):
    import asyncio
    return asyncio.run(coro)


def test_picker_screen_initial_state_no_defaults():
    from benchlab.menu import _build_launcher_app
    from textual.widgets import RadioSet

    async def scenario():
        app = _build_launcher_app([], None)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app._selected_tool_ids() == []
            # Source list defaults to the first available source when
            # nothing is selected/remembered, so the picker never opens
            # with a genuinely empty/unusable source column.
            radio_set = app.query_one("#sources", RadioSet)
            assert radio_set.pressed_index == 0

    _run_async(scenario())


def test_picker_screen_preselects_remembered_tools_and_source():
    from benchlab.menu import _build_launcher_app

    async def scenario():
        app = _build_launcher_app(["tui", "vu"], "fastapi")
        async with app.run_test() as pilot:
            await pilot.pause()
            assert set(app._selected_tool_ids()) == {"tui", "vu"}
            assert app._current_source_id() == "fastapi"

    _run_async(scenario())


def test_picker_screen_source_list_filters_when_tool_selected():
    """Regression: the Data Source column must live-update to only the
    sources compatible with the current Tools selection -- config only
    supports direct/named_pipe, so selecting it must narrow the source
    list from the full 7 down to those 2."""
    from benchlab.menu import _build_launcher_app
    from textual.widgets import SelectionList

    async def scenario():
        app = _build_launcher_app([], None)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert len(app._source_ids) == 7  # unrestricted

            tools = app.query_one("#tools", SelectionList)
            config_option = next(
                o for o in tools._options if o.value == "config")
            tools.select(config_option)
            await pilot.pause()

            assert set(app._source_ids) == {"direct", "named_pipe"}

    _run_async(scenario())


def test_picker_screen_deselecting_tool_restores_full_source_list():
    from benchlab.menu import _build_launcher_app
    from textual.widgets import SelectionList

    async def scenario():
        app = _build_launcher_app([], None)
        async with app.run_test() as pilot:
            await pilot.pause()
            tools = app.query_one("#tools", SelectionList)
            config_option = next(
                o for o in tools._options if o.value == "config")
            tools.select(config_option)
            await pilot.pause()
            tools.deselect(config_option)
            await pilot.pause()

            assert len(app._source_ids) == 7

    _run_async(scenario())


def test_picker_screen_launch_button_disabled_with_no_tools_selected():
    from benchlab.menu import _build_launcher_app
    from textual.widgets import Button

    async def scenario():
        app = _build_launcher_app([], None)
        async with app.run_test() as pilot:
            await pilot.pause()
            btn = app.query_one("#launch-btn", Button)
            assert btn.disabled is True

    _run_async(scenario())


def test_picker_screen_launch_button_enabled_once_tool_selected():
    from benchlab.menu import _build_launcher_app
    from textual.widgets import SelectionList, Button

    async def scenario():
        app = _build_launcher_app([], None)
        async with app.run_test() as pilot:
            await pilot.pause()
            tools = app.query_one("#tools", SelectionList)
            vu_option = next(o for o in tools._options if o.value == "vu")
            tools.select(vu_option)
            await pilot.pause()

            btn = app.query_one("#launch-btn", Button)
            assert btn.disabled is False

    _run_async(scenario())


def test_picker_screen_launch_button_sets_result():
    from benchlab.menu import _build_launcher_app
    from textual.widgets import SelectionList

    async def scenario():
        app = _build_launcher_app([], None)
        async with app.run_test() as pilot:
            await pilot.pause()
            tools = app.query_one("#tools", SelectionList)
            vu_option = next(o for o in tools._options if o.value == "vu")
            tools.select(vu_option)
            await pilot.pause()
            app.action_launch()
            await pilot.pause()

            assert app.result == ("tools", ["vu"], "direct")

    _run_async(scenario())


def test_picker_screen_quit_key_returns_none_result():
    from benchlab.menu import _build_launcher_app

    async def scenario():
        app = _build_launcher_app([], None)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("q")
            await pilot.pause()
            assert app.result is None

    _run_async(scenario())


def test_picker_screen_starts_on_tools_tab():
    from benchlab.menu import _build_launcher_app
    from textual.widgets import TabbedContent

    async def scenario():
        app = _build_launcher_app([], None)
        async with app.run_test() as pilot:
            await pilot.pause()
            tabs = app.query_one("#mode-tabs", TabbedContent)
            assert tabs.active == "tab-tools"

    _run_async(scenario())


def test_picker_screen_provider_tab_defaults_to_fastapi():
    from benchlab.menu import _build_launcher_app

    async def scenario():
        app = _build_launcher_app([], None)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app._current_provider() == "fastapi"

    _run_async(scenario())


def test_picker_screen_provider_tab_start_sets_result():
    """Regression: Data Provider must be reachable from the same picker
    screen as tools (no separate top-level prompt), and starting it must
    produce a distinctly-tagged result so interactive_loop() doesn't try
    to treat a provider choice as a tool launch."""
    from benchlab.menu import _build_launcher_app
    from textual.widgets import RadioButton

    async def scenario():
        app = _build_launcher_app([], None)
        async with app.run_test() as pilot:
            await pilot.pause()
            providers = app.query_one("#providers")
            buttons = list(providers.children)
            with providers.prevent(RadioButton.Changed):
                buttons[1].value = True  # mqtt
            providers._pressed_button = buttons[1]

            app._start_provider()
            await pilot.pause()

            assert app.result == ("provider", "mqtt")

    _run_async(scenario())


def test_run_picker_screen_falls_back_when_textual_app_fails():
    """Regression: if Textual's App.run() can't start in this terminal
    (no proper console control, e.g. certain CI/IDE terminals), the
    picker must degrade to the sequential prompt-based flow rather than
    crash the whole menu."""
    from benchlab.menu import _run_picker_screen
    import unittest.mock as mock

    with mock.patch("benchlab.menu._build_launcher_app") as mock_build:
        mock_app = mock.MagicMock()
        mock_app.run.side_effect = RuntimeError("no terminal")
        mock_build.return_value = mock_app

        with mock.patch(
            "benchlab.menu._sequential_pick",
            return_value=("tools", ["vu"], "direct"),
        ) as mock_seq:
            result = _run_picker_screen([], None)

        mock_seq.assert_called_once()
        assert result == ("tools", ["vu"], "direct")


def test_menu_module_has_no_non_ascii_runtime_output():
    """Regression: menu.py originally used Unicode arrows (Up/Down) and
    em-dashes in printed strings, which crash with UnicodeEncodeError on a
    plain cp1252 Windows console (the default unless UTF-8 codepage is set).
    Only the BANNER block (box-drawing art, printed via a single print() the
    same way menu_classic.py's banner always has been) is exempt."""
    import benchlab.menu as menu_mod
    from pathlib import Path

    src = Path(menu_mod.__file__).read_text(encoding="utf-8")
    lines = src.splitlines()

    in_banner = False
    offenders = []
    for lineno, line in enumerate(lines, 1):
        if line.strip().startswith('BANNER = '):
            in_banner = True
            continue
        if in_banner:
            if line.strip() == '"""':
                in_banner = False
            continue
        if line.strip().startswith(("#", '"""', "'''")):
            continue
        try:
            line.encode("cp1252")
        except UnicodeEncodeError:
            offenders.append((lineno, line))

    assert not offenders, f"Non-cp1252-safe lines outside BANNER: {offenders}"


# ---------------------------------------------------------------------------
# main.py fallback to menu_classic
# ---------------------------------------------------------------------------

def test_main_falls_back_to_classic_menu_when_menu_import_fails(monkeypatch):
    for mod in ("benchlab.main", "benchlab.menu"):
        sys.modules.pop(mod, None)
    monkeypatch.setitem(
        sys.modules,
        "benchlab.menu",
        None)  # forces ImportError

    import benchlab.main as m
    assert m.interactive_loop.__module__ == "benchlab.menu_classic"

    # Clean up so later tests re-import the real benchlab.menu
    sys.modules.pop("benchlab.main", None)
    sys.modules.pop("benchlab.menu", None)
    importlib.import_module("benchlab.main")
