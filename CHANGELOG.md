# Changelog

All notable changes to BENCHLAB PyTools are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[SemVer](https://semver.org/).

## [3.3.0] - 2026-10-01

### Added
- Firmware flash tool (`-flash`, `benchlab.flash`): flashes new firmware
  onto BENCHLAB1/2 devices (STM32F303RB) over USB DFU, commanding the
  running firmware to jump into ST's factory ROM bootloader and talking to
  it via a pure-Python `pyusb` client (no external flashing binary
  required). Supports `.bin` (primary), `.hex`, and `.elf` images, and
  `direct`/`named_pipe`/`service_http` sources for the initial CDC-side
  bootloader jump. Falls back to a guided manual BOOT0 entry for firmware
  too old to support the CDC bootloader command. (#84)
- `HttpConfigClient` (`service_http` source) now implements fan/RGB/
  calibration read-write (`read_fan_config`/`write_fan_config`,
  `read_rgb_config`/`write_rgb_config`, `read_calibration`/
  `write_calibration`), via a new translation layer
  (`benchlab.config.http_dto`) between the BL_Service HTTP API's DTO JSON
  shapes and this repo's existing config-dict convention. Handles BL2's
  wider calibration wire struct correctly. `config_tool.py` (`-config`)
  gained `--source service_http` CLI support to match. (#86)

## [3.2.1] - 2026-10-01

### Fixed
- Collector recovery after incomplete sensor reads
  (`benchlab.restapi.telemetry_api.read_device_loop`): incomplete serial
  responses (`read_sensors()` returning `None`) are now treated as read
  failures instead of being silently ignored, so the collector reconnects
  instead of continuing to publish stale telemetry. The read-error
  backoff counter now survives serial port reopen attempts and resets
  only after a successfully decoded sample, restoring the intended
  retry/backoff policy. A device is marked `connected` only once a
  sample has actually been decoded, and stale telemetry is cleared
  before the collector waits to retry. (#77)

## [3.2.0] - 2026-09-29

### Added
- `GET /events` WebSocket on the FastAPI server (`benchlab.restapi.telemetry_api`):
  a multiplexed event stream matching the frame envelope of the C# BenchLab
  service's `/events` (`hello`/`telemetry`/`device` frames), so
  `ServiceWsDataSource` (`--source service_ws`) can point at either server.
  `telemetry.v` keeps pytools' own TUI-style sensor-key names (e.g.
  `EPS1_Power`) rather than the C# service's `ShortName` convention (e.g.
  `EPS1_P`) -- there's no pycore equivalent of `ShortName` to translate
  into, and `ServiceWsDataSource`'s key mapping already passes unrecognised
  keys through unchanged, so no client-side changes are needed. The
  existing per-device `/device/{uid}/stream` WebSocket is unchanged.
- `ConfigClient.factory_cal_unlock()` (`direct` and `named_pipe` sources):
  temporarily lifts write-protection on BL2's factory calibration slot
  (BL2 firmware 7+ only). Disruptive -- a successful call resets the
  device immediately; reconnect before issuing further commands.

### Changed
- Bumped the `benchlab-pycore` floor to `>=0.8.0` (from `>=0.6.0`).
  0.8.0 fixes `write_calibration()`'s wire format on BL1 firmware 6+ /
  BL2 firmware 7+, where `WRITE_CALIBRATION` changed from an
  offset-addressed partial write to a whole-struct replace; sending the
  old format there silently corrupts calibration data.
  `DirectConfigClient.write_calibration()` was already firmware-agnostic
  (it doesn't pass `fw_version`, so pycore auto-probes it), so no code
  change was needed here beyond the floor bump. Companion release:
  BENCHLAB_Service v2.6.0, which adds the same `FACTORY_CAL_UNLOCK`
  command plus BL1/BL2 firmware compatibility and a stuck-disconnect
  reconnect fix.

### Fixed
- TUI: a disconnected remote data source (`fastapi_custom`, `mqtt_custom`,
  `named_pipe`, `service_http`, `service_ws`, etc.) fell back to scanning
  local serial ports for the Fleet view, which could probe ports already
  owned by another collector and show unrelated local devices instead of
  an empty fleet. (#74)
- TUI: the terminal-too-small warning was drawn but never flushed to the
  screen before `render()` returned, leaving the terminal blank instead
  of showing the warning. (#75)
- Packaging: `benchlab-pytools[tui]` was missing `pydantic` and `requests`,
  both required to initialize an HTTP-backed TUI (`fastapi`/`service_http`
  sources); a clean `[tui]`-only install crashed with `ModuleNotFoundError`
  unless the server/all extras happened to already pull them in. (#76)

## [3.1.0] - 2026-09-08

### Added
- `service_ws` data source: consumes the BENCHLAB service `/events`
  WebSocket event stream (added in the C# service's PR #76). Telemetry is
  pushed at the service's own poll cadence rather than polled, and device
  connect/disconnect is reflected without a manual rescan. Output is
  normalized to the same shape as `service_http`. New CLI flags
  `--service-ws-url` (default `ws://localhost:8585/events`) and
  `--service-token`; new optional-dependency group
  `benchlab-pytools[service_ws]` (`websockets`).

## [3.0.4] - 2026-08-18

### Fixed
- 12VHPWR tab per-pin sense lines (`HPWR{1,2}_W{1..6}`) read 0.0 over
  `service_http`/`named_pipe`, even though other apps using the same
  `service_http` API showed correct values. The C# service's telemetry
  normalization mapped the HPWR1/HPWR2 rail summaries but never mapped
  the 12 individual per-pin sense-line sensors, so their C#
  `ShortName`s (`HPWR{n}_W{m}_{P,I,V}`) passed through unmapped instead
  of becoming the `..._{Power,Current,Voltage}` keys the TUI reads.

## [3.0.3] - 2026-08-18

### Fixed
- TUI: the 12VHPWR tab's Voltage section was cut off with no way to
  scroll, since `MIN_TERMINAL_ROWS` (35) was well under the ~48 rows
  the stacked Power/Current/Voltage layout plus status bar actually
  needs. Raised to 48.
- `mqtt`, `service_http`, and `named_pipe` data sources never surfaced
  `vendor_id`/`product_id`/`firmware_version`, so the Fleet TUI's
  Model column (BL1 vs BL2 detection) was always wrong for these
  sources:
  - `mqtt`: the publisher's retained info payload only sent
    `uid`/`com_port`/`firmware`; it now also reads
    `ProductId`/`VendorId`/`FwVersion` via `read_device()` and
    computes `variant`.
  - `named_pipe`: the C# service's camelCase `productId` is now
    mapped to the PascalCase keys the TUI expects
    (`vendorId`/`firmwareVersion` also mapped), with `variant`
    computed — field names verified against the
    `BENCHLAB.BENCHLAB_Service` source.
  - `service_http`: added a device-info normalization step (the C#
    HTTP API only ever sends `productId`, confirmed against source —
    vendor/firmware are unavailable there and default to unknown).

## [3.0.2] - 2026-08-18

### Fixed
- Several f-strings had their `{...}` expression split across a line
  break, which is [PEP 701](https://peps.python.org/pep-0701/) syntax
  only valid on Python 3.12+. Despite `requires-python = ">=3.10"`, this
  raised `SyntaxError` immediately on 3.10/3.11 (e.g. `benchlab -tui`
  failing with `SyntaxError: unterminated string literal` in
  `benchlab/core/datasource.py`) — invisible in CI since every workflow
  pins Python 3.13. Collapsed all such f-strings back onto single lines
  across 27 files.

## [3.0.1] - 2026-08-18

### Fixed
- PyPI install was broken: `bootstrap.py` and the core `requirements.txt`
  lived at the repo root and were never included in the wheel (only
  `benchlab/` is packaged), so `pip install benchlab-pytools` followed by
  `benchlab ...` failed with `ModuleNotFoundError: No module named
  'bootstrap'`. Both files moved into `benchlab/`, with internal imports
  updated to relative imports.
- `pywin32` was missing from `pyproject.toml`'s core dependencies, so
  PyPI installs on Windows did not pull it in despite the `named_pipe`
  data source depending on it. Added `pywin32>=306; platform_system ==
  'Windows'` alongside the existing `windows-curses` entry.

## [3.0.0] - 2026-08-15

### Added
- Packaging: `pyproject.toml` for `pip install benchlab-pytools`, with
  per-tool optional extras (`[tui]`, `[graph]`, `[vu]`, `[wigidash]`,
  `[mqtt]`, `[restapi]`, `[csv_log]`, `[hwinfo]`, `[all]`) and a `benchlab`
  console-script entry point.
- `--version` CLI flag.
- Tag-triggered release workflow: builds and tests the package, publishes
  to PyPI, and attaches a source zip + wheel to a GitHub Release.
- `benchlab/vu/VU-Server/NOTICE.md` documenting redistribution permission
  for the vendored VU-Server app.
- flake8 lint CI gate (`.flake8`, `.github/workflows/lint.yml`) for pull
  requests targeting `main`.

### Changed
- Link: `CloudMQTTClient` now pins paho-mqtt's `callback_api_version`
  explicitly to `VERSION2` instead of relying on the deprecated implicit
  default, with `on_connect`/`on_disconnect` updated to the matching
  5-arg signature.
- Link: MQTT topic pattern now also supports a `{client_uuid}` token
  alongside `{uid}` (`LINK_TOPIC_PATTERN`), so deployments needing a
  `clients/{client_uuid}/devices/{uid}/...` scheme are configurable
  without code changes.
- Whole codebase now passes flake8's default ruleset (2,443 findings
  fixed: unused imports/variables, an undefined-name issue in
  `benchlab/core/datasource.py` resolved via a `TYPE_CHECKING`-guarded
  import, and formatting/line-length cleanup — no behavior changes).

## [0.8.2] - Unreleased (pre-packaging baseline)

Snapshot of the codebase at the point packaging work began. Interactive
menu (prompt_toolkit-based), TUI, CSV logger, FastAPI server, graph, HWiNFO
export, MQTT publisher, VU dials, WigiDash, and config import/export tools,
sharing a common data-source layer (direct serial, FastAPI, MQTT, named
pipe, service HTTP, service WebSocket).

[3.3.0]: https://github.com/BenchLab-io/benchlab-pytools/compare/v3.2.1...v3.3.0
[3.2.1]: https://github.com/BenchLab-io/benchlab-pytools/compare/v3.2.0...v3.2.1
[3.2.0]: https://github.com/BenchLab-io/benchlab-pytools/compare/v3.1.0...v3.2.0
[3.1.0]: https://github.com/BenchLab-io/benchlab-pytools/compare/v3.0.4...v3.1.0
[3.0.4]: https://github.com/BenchLab-io/benchlab-pytools/compare/v3.0.3...v3.0.4
[3.0.3]: https://github.com/BenchLab-io/benchlab-pytools/compare/v3.0.2...v3.0.3
[3.0.2]: https://github.com/BenchLab-io/benchlab-pytools/compare/v3.0.1...v3.0.2
[3.0.1]: https://github.com/BenchLab-io/benchlab-pytools/compare/v3.0.0...v3.0.1
[3.0.0]: https://github.com/BenchLab-io/benchlab-pytools/compare/v0.8.2...v3.0.0
