"""BENCHLAB PyTools v2 – Interactive Menu System (Textual).

A single full-screen TUI, replacing the old numbered-prompt flow (still
available as menu_classic.py, used as an automatic fallback if textual
isn't installed or the app can't start in the current terminal).

Flow:
  1. Launching straight into the picker screen -- no separate top-level
     "what would you like to do" prompt first.
  2. Two tabs: "Run Tool(s)" (Tools checklist stacked above a Data Source
     radio list, live-filtered to what the selected tool(s) support) and
     "Data Provider" (run FastAPI or MQTT standalone, no consumer tool).
  3. Last-used tool(s)/source are pre-selected on open.
  4. Launch/Start, then remember the choice for next time (tools mode only).
  5. Any source-specific connection params (broker host, API port, etc.)
     are prompted for afterward in the terminal, pre-filled from last use
     -- kept out of the TUI screen itself to avoid duplicating input
     validation in two places.
"""

import logging
import os
import sys
from typing import Dict, List, Optional, Tuple, Union

from .bootstrap import clear_screen
from .tools import CONSUMER_TOOLS
from .sources import (
    check_and_setup_source,
    check_mqtt_running,
    start_mqtt_broker,
    start_mqtt_source,
    cleanup_all_services,
    SERVICE_HTTP_DEFAULT_PORT,
)
from .launcher import launch_single_tool, launch_tools_concurrent
from .prefs import load_prefs, record_launch, get_source_params

logger = logging.getLogger("benchlab.launcher")

BANNER = r"""
██████╗ ███████╗███╗   ██╗ ██████╗██╗  ██╗██╗      █████╗ ██████╗
██╔══██╗██╔════╝████╗  ██║██╔════╝██║  ██║██║     ██╔══██╗██╔══██╗
██████╔╝█████╗  ██╔██╗ ██║██║     ███████║██║     ███████║██████╔╝
██╔══██╗██╔══╝  ██║╚██╗██║██║     ██╔══██║██║     ██╔══██║██╔══██╗
██████╔╝███████╗██║ ╚████║╚██████╗██║  ██║███████╗██║  ██║██████╔╝
╚═════╝ ╚══════╝╚═╝  ╚═══╝ ╚═════╝╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝╚═════╝

        ██████╗ ██╗   ██╗████████╗ ██████╗  ██████╗ ██╗     ███████╗
        ██╔══██╗╚██╗ ██╔╝╚══██╔══╝██╔═══██╗██╔═══██╗██║     ██╔════╝
        ██████╔╝ ╚████╔╝    ██║   ██║   ██║██║   ██║██║     ███████╗
        ██╔═══╝   ╚██╔╝     ██║   ██║   ██║██║   ██║██║     ╚════██║
        ██║        ██║      ██║   ╚██████╔╝╚██████╔╝███████╗███████║
        ╚═╝        ╚═╝      ╚═╝    ╚═════╝  ╚═════╝ ╚══════╝╚══════╝
"""

SOURCE_LABELS: Dict[str, str] = {
    "direct": "Direct (serial port)",
    "fastapi": "FastAPI server (Python)",
    "fastapi_custom": "FastAPI server (custom URL)",
    "mqtt": "MQTT (Python, experimental)",
    "mqtt_custom": "MQTT (custom)",
    "named_pipe": "BenchLab service - named pipe",
    "service_http": (
        f"BenchLab service - HTTP API (port {SERVICE_HTTP_DEFAULT_PORT})"
    ),
}

SOURCE_ORDER = [
    "direct",
    "fastapi",
    "fastapi_custom",
    "mqtt",
    "mqtt_custom",
    "named_pipe",
    "service_http"]

PROVIDER_LABELS: Dict[str, str] = {
    "fastapi": "FastAPI Server - REST API + WebSocket on port 8000",
    "mqtt": "MQTT Publisher - publish telemetry to MQTT broker",
}


def print_banner() -> None:
    print(BANNER)


# ──────────────────────────────────────────────────────────────
# Source compatibility (pure logic, reused by the picker and tests)
# ──────────────────────────────────────────────────────────────

def _available_sources(
        is_multi: bool,
        supported_sources: Optional[List[str]]) -> List[Tuple[str, str]]:
    """Return (source_id, label) pairs valid for the given tool selection."""
    is_windows = sys.platform.startswith("win")

    result = []
    for src in SOURCE_ORDER:
        if src == "direct" and is_multi:
            continue
        if src == "named_pipe" and not is_windows:
            continue
        if supported_sources is not None and src not in supported_sources:
            continue
        result.append((src, SOURCE_LABELS[src]))
    return result


def _combined_supported_sources(tool_ids: List[str]) -> Optional[List[str]]:
    """Intersection of supported_sources across selected tools.

    None means "no restriction" (tool doesn't declare supported_sources,
    or no tools selected yet).
    """
    restrictions = [
        CONSUMER_TOOLS[tid]["supported_sources"]
        for tid in tool_ids
        if tid in CONSUMER_TOOLS
        and CONSUMER_TOOLS[tid].get("supported_sources") is not None
    ]
    if not restrictions:
        return None
    combined = set(restrictions[0])
    for r in restrictions[1:]:
        combined &= set(r)
    return list(combined)


# ──────────────────────────────────────────────────────────────
# Textual app: "Run Tool(s)" tab + "Data Provider" tab
# ──────────────────────────────────────────────────────────────

# Result shape returned by the app / _run_picker_screen:
#   ("tools", tool_ids, source)      -- launch consumer tool(s)
#   ("provider", provider_type)      -- start a standalone data provider
#   None                             -- cancelled
PickerResult = Union[Tuple[str, List[str], str], Tuple[str, str], None]


def _build_launcher_app(
        default_tool_ids: List[str],
        default_source: Optional[str]):
    """Construct (but don't run) the Textual LauncherApp. Split out from
    _run_picker_screen so tests can inspect app state without a real
    terminal (Textual apps can run headless via App.run_test())."""
    from textual.app import App, ComposeResult
    from textual.containers import Vertical
    from textual.widgets import (
        Footer,
        SelectionList,
        RadioSet,
        RadioButton,
        Button,
        Static,
        TabbedContent,
        TabPane,
    )
    from textual.widgets.selection_list import Selection
    from textual.binding import Binding

    class LauncherApp(App):
        CSS = """
        Screen {
            background: $surface;
        }
        #banner {
            width: 100%;
            content-align: center middle;
            color: $accent;
            margin-bottom: 1;
        }
        .panel {
            border: round $primary;
            padding: 0 1;
            margin-bottom: 1;
        }
        .panel:focus-within {
            border: round $accent;
        }
        #tools-panel {
            height: 1fr;
        }
        #source-panel {
            height: auto;
            max-height: 40%;
        }
        #provider-panel {
            height: auto;
        }
        #tools-summary, #provider-summary {
            height: auto;
            margin-bottom: 1;
            color: $text-muted;
        }
        .launch-btn {
            width: 100%;
            margin-top: 1;
        }
        .launch-btn.-ready {
            background: $success;
        }
        """

        BINDINGS = [
            Binding("q", "quit_app", "Quit"),
            Binding("escape", "quit_app", "Quit"),
            Binding("ctrl+l", "launch", "Launch"),
        ]

        TITLE = "BENCHLAB PyTools"

        def __init__(self):
            super().__init__()
            self.result: PickerResult = None
            self._default_tool_ids = default_tool_ids
            self._default_source = default_source

        def compose(self) -> ComposeResult:
            yield Static(BANNER, id="banner")
            with TabbedContent(id="mode-tabs"):
                with TabPane("Run Tool(s)", id="tab-tools"):
                    with Vertical(id="tools-panel", classes="panel"):
                        yield Static("[b]Tools[/b] (space to toggle)")
                        tool_selections = [
                            Selection(
                                f"{t['name']} - {t['description']}",
                                tid,
                                tid in self._default_tool_ids)
                            for tid, t in CONSUMER_TOOLS.items()
                        ]
                        yield SelectionList[str](*tool_selections, id="tools")
                    with Vertical(id="source-panel", classes="panel"):
                        yield Static("[b]Data Source[/b]")
                        yield RadioSet(id="sources")
                    yield Static("", id="tools-summary")
                    yield Button(
                        "Launch", id="launch-btn", variant="primary",
                        classes="launch-btn")
                with TabPane("Data Provider", id="tab-provider"):
                    with Vertical(classes="panel", id="provider-panel"):
                        yield Static(
                            "[b]Provider[/b] - runs standalone, "
                            "no consumer tool")
                        yield RadioSet(
                            RadioButton(
                                PROVIDER_LABELS["fastapi"], value=True),
                            RadioButton(PROVIDER_LABELS["mqtt"]),
                            id="providers",
                        )
                    yield Static("", id="provider-summary")
                    yield Button(
                        "Start Provider", id="provider-btn",
                        variant="primary", classes="launch-btn")
            yield Footer()

        async def on_mount(self) -> None:
            await self._refresh_sources()
            self._update_provider_summary()

        # ── Tools tab ────────────────────────────────────────────

        def _selected_tool_ids(self) -> List[str]:
            return list(self.query_one("#tools", SelectionList).selected)

        def _current_source_items(self) -> List[Tuple[str, str]]:
            tool_ids = self._selected_tool_ids()
            supported = _combined_supported_sources(tool_ids)
            sources = _available_sources(
                is_multi=len(tool_ids) > 1,
                supported_sources=supported)
            return sources

        async def _refresh_sources(self) -> None:
            sources = self._current_source_items()
            radio_set = self.query_one("#sources", RadioSet)
            self._source_ids = [s for s, _ in sources]

            radio_set._pressed_button = None
            await radio_set.remove_children()
            if not sources:
                await radio_set.mount(
                    RadioButton("(no compatible source)", disabled=True))
            else:
                preferred_idx = 0
                if self._default_source in self._source_ids:
                    preferred_idx = self._source_ids.index(
                        self._default_source)
                buttons = [RadioButton(label) for _, label in sources]
                await radio_set.mount(*buttons)
                # RadioSet only auto-selects a pre-checked button in its own
                # one-time _on_mount scan; that doesn't re-run on a dynamic
                # remount, and setting .value=True just posts an async
                # Changed message we'd have to await-settle before reading
                # pressed_index. Simpler and immediate: set the internal
                # pointer directly, matching what _on_mount itself does.
                with radio_set.prevent(RadioButton.Changed):
                    buttons[preferred_idx].value = True
                radio_set._pressed_button = buttons[preferred_idx]
            self._update_tools_summary()

        def _current_source_id(self) -> Optional[str]:
            radio_set = self.query_one("#sources", RadioSet)
            idx = radio_set.pressed_index
            if idx is None or idx < 0 or not getattr(
                    self, "_source_ids", None):
                return None
            if idx >= len(self._source_ids):
                return None
            return self._source_ids[idx]

        def _update_tools_summary(self) -> None:
            tool_ids = self._selected_tool_ids()
            source = self._current_source_id()
            tool_names = ", ".join(
                CONSUMER_TOOLS[tid]["name"]
                for tid in tool_ids) or "(none selected)"
            source_label = SOURCE_LABELS.get(
                source, "(none)") if source else "(none)"
            summary = self.query_one("#tools-summary", Static)
            summary.update(f"Tools: {tool_names}   |   Source: {source_label}")

            btn = self.query_one("#launch-btn", Button)
            ready = bool(tool_ids and source)
            btn.disabled = not ready
            btn.set_class(ready, "-ready")

        async def on_selection_list_selected_changed(self, event) -> None:
            if event.selection_list.id == "tools":
                await self._refresh_sources()

        def on_radio_set_changed(self, event) -> None:
            if event.radio_set.id == "sources":
                self._update_tools_summary()
            elif event.radio_set.id == "providers":
                self._update_provider_summary()

        def on_button_pressed(self, event) -> None:
            if event.button.id == "launch-btn":
                self.action_launch()
            elif event.button.id == "provider-btn":
                self._start_provider()

        def action_launch(self) -> None:
            tool_ids = self._selected_tool_ids()
            source = self._current_source_id()
            if tool_ids and source:
                self.result = ("tools", tool_ids, source)
                self.exit()

        # ── Data Provider tab ───────────────────────────────────

        def _current_provider(self) -> str:
            idx = self.query_one("#providers", RadioSet).pressed_index
            return "mqtt" if idx == 1 else "fastapi"

        def _update_provider_summary(self) -> None:
            provider = self._current_provider()
            summary = self.query_one("#provider-summary", Static)
            summary.update(f"Provider: {PROVIDER_LABELS[provider]}")

        def _start_provider(self) -> None:
            self.result = ("provider", self._current_provider())
            self.exit()

        # ── Global actions ──────────────────────────────────────

        def action_quit_app(self) -> None:
            self.result = None
            self.exit()

    return LauncherApp()


def _run_picker_screen(
        default_tool_ids: List[str],
        default_source: Optional[str]) -> PickerResult:
    """Run the full-screen picker. Returns a PickerResult (see above) or
    None if cancelled.

    Falls back to the sequential prompt-based picker (_sequential_pick) if
    Textual's app can't start in this terminal.
    """
    app = _build_launcher_app(default_tool_ids, default_source)
    try:
        app.run()
    except Exception as e:
        logger.debug(
            f"Textual picker failed ({e}); "
            "falling back to sequential prompts")
        return _sequential_pick(default_tool_ids, default_source)
    except KeyboardInterrupt:
        return None
    return app.result


# ──────────────────────────────────────────────────────────────
# Sequential fallback (used if the full-screen app can't run)
# ──────────────────────────────────────────────────────────────

def _fuzzy_pick(items: List[Tuple[str, str]], title: str,
                default: Optional[str] = None) -> Optional[str]:
    """Single-select picker over (value, label) pairs, printing the full
    option list up front. Accepts a number or enough of a label to match
    exactly one option.
    """
    default_index = next(
        (i for i, (value, _) in enumerate(
            items, 1) if value == default), None)

    print(f"\n{title}")
    for i, (_, label) in enumerate(items, 1):
        marker = " (last used)" if i == default_index else ""
        print(f"  {i}. {label}{marker}")
    default_str = str(default_index) if default_index else ""
    suffix = f" [{default_str}]" if default_str else ""
    try:
        answer = input(f"Choice{suffix}: ").strip() or default_str
    except (EOFError, KeyboardInterrupt):
        return None

    if not answer:
        return None

    if answer.isdigit():
        idx = int(answer)
        if 1 <= idx <= len(items):
            return items[idx - 1][0]
        print(f"  No option numbered {idx}.")
        return None

    labels = [label for _, label in items]
    matches = [label for label in labels if answer.lower() in label.lower()]
    if len(matches) == 1:
        idx = labels.index(matches[0])
        return items[idx][0]

    print(f"  No unambiguous match for {answer!r}.")
    return None


def _multi_pick(items: List[Tuple[str, str]], title: str) -> List[str]:
    """Comma-separated number list multi-select (plain input())."""
    print(f"\n{title}")
    for i, (_, label) in enumerate(items, 1):
        print(f"  {i}. {label}")
    print("  Enter numbers separated by commas (e.g. 1,3), or 'all'.")
    try:
        answer = input("> ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return []
    if not answer:
        return []
    if answer == "all":
        return [value for value, _ in items]
    selected = []
    for part in answer.split(","):
        part = part.strip()
        if part.isdigit() and 1 <= int(part) <= len(items):
            selected.append(items[int(part) - 1][0])
    return selected


def _sequential_pick(
        default_tool_ids: List[str],
        default_source: Optional[str]) -> PickerResult:
    """Fallback flow when the full-screen picker can't run: choose tools
    vs. data-provider mode, then pick accordingly, via sequential prompts."""
    mode_choice = _fuzzy_pick(
        [
            (
                "tools",
                "Run tool(s) - pick one or more consumer tools and a "
                "data source",
            ),
            (
                "provider",
                "Data provider - run FastAPI or MQTT standalone for "
                "other processes",
            ),
        ],
        "What would you like to do?",
    )
    if mode_choice is None:
        return None

    if mode_choice == "provider":
        provider = _fuzzy_pick(
            list(
                PROVIDER_LABELS.items()),
            "Select a data provider:")
        if provider is None:
            return None
        return ("provider", provider)

    tool_values = [(tid, f"{t['name']} - {t['description']}")
                   for tid, t in CONSUMER_TOOLS.items()]

    print("\n=== Select Tool(s) ===")
    try:
        sub_mode = input(
            "Single tool or multiple? (s/m) [s]: ").strip().lower() or "s"
    except (EOFError, KeyboardInterrupt):
        return None

    if sub_mode.startswith("m"):
        tool_ids = _multi_pick(tool_values, "Select Tools")
        if not tool_ids:
            print("  No tools selected.")
            return None
    else:
        tool_id = _fuzzy_pick(
            tool_values,
            "Select a tool:",
            default=default_tool_ids[0] if default_tool_ids else None)
        if tool_id is None:
            return None
        tool_ids = [tool_id]

    supported = _combined_supported_sources(tool_ids)
    sources = _available_sources(
        is_multi=len(tool_ids) > 1,
        supported_sources=supported)
    if not sources:
        print("  No compatible data source available for this selection.")
        return None

    valid_defaults = [s for s, _ in sources]
    default = default_source if default_source in valid_defaults else None
    source = _fuzzy_pick(sources, "Select a data source:", default=default)
    if source is None:
        return None

    return ("tools", tool_ids, source)


# ──────────────────────────────────────────────────────────────
# Source connection params (pre-filled from remembered prefs)
# ──────────────────────────────────────────────────────────────

def _prompt_default(label: str, default: str) -> str:
    try:
        val = input(f"  {label} [{default}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        return default
    return val or default


_last_source_params: Dict[str, Dict] = {}


def _setup_source(source_type: str) -> bool:
    """Prompt for any source-specific params, then check/start the source."""
    remembered = get_source_params(source_type)
    setup_kwargs: Dict = {}

    if source_type == "fastapi":
        port = int(remembered.get("port", os.environ.get("API_PORT", "8000")))
        setup_kwargs = {"port": port}

    elif source_type == "fastapi_custom":
        host = _prompt_default("Host/IP", remembered.get("host", "127.0.0.1"))
        port = _prompt_default("Port", str(remembered.get("port", 8000)))
        try:
            port = int(port)
        except ValueError:
            print("  Invalid port number.")
            return False
        base_url = f"http://{host}:{port}"
        setup_kwargs = {"base_url": base_url}
        os.environ["BENCHLAB_FASTAPI_CUSTOM_URL"] = base_url
        remembered = {"host": host, "port": port}

    elif source_type == "mqtt":
        broker = os.environ.get(
            "MQTT_BROKER", remembered.get(
                "broker", "localhost"))
        mqtt_port = int(
            os.environ.get(
                "MQTT_PORT",
                remembered.get(
                    "mqtt_port",
                    1883)))
        setup_kwargs = {"broker": broker, "mqtt_port": mqtt_port}

    elif source_type == "mqtt_custom":
        broker = _prompt_default(
            "Broker host", remembered.get(
                "broker", "localhost"))
        mqtt_port = _prompt_default(
            "Broker port", str(
                remembered.get(
                    "mqtt_port", 1883)))
        try:
            mqtt_port = int(mqtt_port)
        except ValueError:
            print("  Invalid port number.")
            return False
        setup_kwargs = {"broker": broker, "mqtt_port": mqtt_port}
        remembered = {"broker": broker, "mqtt_port": mqtt_port}

    elif source_type == "service_http":
        import urllib.parse
        svc_url = os.environ.get(
            "BENCHLAB_SERVICE_URL",
            f"http://localhost:{SERVICE_HTTP_DEFAULT_PORT}")
        parsed = urllib.parse.urlparse(svc_url)
        setup_kwargs = {"host": parsed.hostname or "localhost",
                        "port": parsed.port or SERVICE_HTTP_DEFAULT_PORT}

    ready = check_and_setup_source(source_type, **setup_kwargs)
    _last_source_params[source_type] = remembered
    return ready


# ──────────────────────────────────────────────────────────────
# Data Provider Mode (standalone FastAPI/MQTT, no consumer tool)
# ──────────────────────────────────────────────────────────────

def _run_data_provider(provider: str) -> None:
    if provider == "fastapi":
        port = _prompt_default("Port", "8000")
        try:
            port = int(port)
        except ValueError:
            print("  Invalid port number.")
            return
        os.environ["API_PORT"] = str(port)
        if not check_and_setup_source("fastapi", port=port):
            logger.error("Could not start FastAPI server.")
            return
        print("FastAPI server running. Press Ctrl+C to stop the provider.")
        input("  (Press Enter to return to menu after verifying...) ")

    elif provider == "mqtt":
        host = _prompt_default("Broker host", "localhost")
        port = _prompt_default("Broker port", "1883")
        try:
            port = int(port)
        except ValueError:
            print("  Invalid port number.")
            return
        if not check_mqtt_running(host, port):
            logger.warning(f"No MQTT broker at {host}:{port}")
            logger.info("Starting embedded broker...")
            if not start_mqtt_broker(port):
                logger.error("Could not start MQTT broker.")
                return
        else:
            logger.info(f"MQTT broker available at {host}:{port}")
        os.environ["MQTT_BROKER"] = host
        os.environ["MQTT_PORT"] = str(port)
        start_mqtt_source(host, port)
        logger.info("Press Ctrl+C to stop the provider.")
        input("  (Press Enter to return to menu after verifying...) ")


# ──────────────────────────────────────────────────────────────
# Top-level flow
# ──────────────────────────────────────────────────────────────

def _launch(tool_ids: List[str], source: str) -> None:
    tool_names = [CONSUMER_TOOLS[tid]["name"] for tid in tool_ids]

    _last_source_params.clear()
    if not _setup_source(source):
        print(f"\n  Could not set up '{source}' data source.")
        if source in ("named_pipe", "service_http"):
            print(
                "  Start the BenchLab Windows service (BL_Service.exe) "
                "and try again.")
        return

    print("\n=== Launch Summary ===")
    print(f"Tools: {', '.join(tool_names)}")
    print(f"Data source: {source}")
    try:
        if input("Launch? (Y/n): ").strip().lower() in ("n", "no"):
            print("Aborted.")
            return
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        return

    os.environ["BENCHLAB_DATA_SOURCE"] = source
    record_launch(tool_ids, source, _last_source_params.get(source))

    try:
        if len(tool_ids) > 1:
            launch_tools_concurrent(tool_ids)
        else:
            launch_single_tool(tool_ids[0])
    except KeyboardInterrupt:
        print("\n  Interrupted.")
    finally:
        cleanup_all_services()


def interactive_loop() -> None:
    """Drive the interactive menu until the user quits."""
    while True:
        try:
            prefs = load_prefs()
            default_tool_ids = [
                tid for tid in (
                    prefs.get("last_tool_ids") or []) if tid in CONSUMER_TOOLS]
            default_source = prefs.get("last_source")

            picked = _run_picker_screen(default_tool_ids, default_source)
            clear_screen()

            if picked is None:
                print("Goodbye!")
                cleanup_all_services()
                return

            if picked[0] == "provider":
                _, provider = picked
                _run_data_provider(provider)
            elif picked[0] == "tools":
                _, tool_ids, source = picked
                _launch(tool_ids, source)

            input("\n  Press Enter to continue... ")

        except (EOFError, KeyboardInterrupt):
            print("Goodbye!")
            cleanup_all_services()
            return
