# Contributing to BENCHLAB PyTools

## Development setup

```bash
git clone https://github.com/BenchLab-io/benchlab-pytools.git
cd benchlab-pytools
pip install -r requirements.txt
pip install -r benchlab/csv_log/requirements.txt
pip install -r benchlab/graph/requirements.txt
pip install -r benchlab/wigidash/requirements.txt
pip install -r benchlab/vu/requirements.txt
pip install pytest
```

Or install everything (including test tooling) via the package extras:

```bash
pip install -e ".[all]"
pip install pytest
```

Requires Python 3.10+.

## Running tests

```bash
pytest
```

CI (`.github/workflows/pycore-compat.yml`) runs the full suite on
Windows against both the pinned `benchlab-pycore` version and the latest
release on PyPI, to catch drift between this repo and that package's
published API early (see issue #9).

## Adding a new tool

1. Add an entry to `CONSUMER_TOOLS` in `benchlab/tools.py` with `name`,
   `flag`, `module`, `function`, and `requirements`.
2. Implement the tool's entry function, accepting an `args` namespace (see
   `benchlab/launcher.py::_build_args_namespace`).
3. Add a CLI flag in `benchlab/main.py::get_parser()` and dispatch it in
   `launch_mode()`.
4. Add a `requirements.txt` in the tool's directory if it needs extra
   dependencies, and a matching extra in `pyproject.toml`.
5. Write a `README.md` in the tool's directory following the style of the
   existing ones.
6. Add tests and wire them into `.github/workflows/pycore-compat.yml`.

## Versioning and releases

The version lives in `benchlab/__init__.py::__version__` and is the single
source of truth (`pyproject.toml` reads it dynamically). To cut a release:

1. Bump `__version__` in `benchlab/__init__.py`.
2. Move the `[Unreleased]` section in `CHANGELOG.md` to a new version
   heading with today's date.
3. Commit, then tag: `git tag vX.Y.Z && git push origin vX.Y.Z`.
4. The `release` workflow builds, tests, publishes to PyPI, and creates a
   GitHub Release with the wheel/sdist and a source zip attached.

## Pull requests

- Keep changes scoped to one tool/concern where possible — this repo is
  organized as independent subpackages under `benchlab/` sharing a common
  core.
- Include or update tests for behavior changes.
- CI must pass before merge.
