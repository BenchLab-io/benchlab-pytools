# Changelog

All notable changes to BENCHLAB PyTools are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[SemVer](https://semver.org/).

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
pipe, service HTTP).

[3.0.4]: https://github.com/BenchLab-io/benchlab-pytools/compare/v3.0.3...v3.0.4
[3.0.3]: https://github.com/BenchLab-io/benchlab-pytools/compare/v3.0.2...v3.0.3
[3.0.2]: https://github.com/BenchLab-io/benchlab-pytools/compare/v3.0.1...v3.0.2
[3.0.1]: https://github.com/BenchLab-io/benchlab-pytools/compare/v3.0.0...v3.0.1
[3.0.0]: https://github.com/BenchLab-io/benchlab-pytools/compare/v0.8.2...v3.0.0
