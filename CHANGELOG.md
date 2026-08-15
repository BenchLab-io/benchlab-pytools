# Changelog

All notable changes to BENCHLAB PyTools are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[SemVer](https://semver.org/).

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

[3.0.0]: https://github.com/BenchLab-io/benchlab-pytools/compare/v0.8.2...v3.0.0
