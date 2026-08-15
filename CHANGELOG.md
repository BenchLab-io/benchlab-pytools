# Changelog

All notable changes to BENCHLAB PyTools are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[SemVer](https://semver.org/).

## [Unreleased]

### Added
- Packaging: `pyproject.toml` for `pip install benchlab-pytools`, with
  per-tool optional extras (`[tui]`, `[graph]`, `[vu]`, `[wigidash]`,
  `[mqtt]`, `[restapi]`, `[csv_log]`, `[hwinfo]`, `[all]`) and a `benchlab`
  console-script entry point.
- `--version` CLI flag.
- Tag-triggered release workflow: builds and tests the package, publishes
  to PyPI, and attaches a source zip + wheel to a GitHub Release.

## [0.8.2] - Unreleased (pre-packaging baseline)

Snapshot of the codebase at the point packaging work began. Interactive
menu (prompt_toolkit-based), TUI, CSV logger, FastAPI server, graph, HWiNFO
export, MQTT publisher, VU dials, WigiDash, and config import/export tools,
sharing a common data-source layer (direct serial, FastAPI, MQTT, named
pipe, service HTTP).

[Unreleased]: https://github.com/BenchLab-io/benchlab-pytools/compare/v0.8.2...HEAD
