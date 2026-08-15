"""Entry point for the Benchlab package.

Running ``python -m benchlab`` (or executing this file directly) will
invoke the package's :func:`benchlab.main.main` function. The module
now includes a short
docstring, basic logging, and error handling to provide a clearer failure mode
if the import or execution of ``main`` raises an exception.
"""

from .main import main
import logging
import sys

logger = logging.getLogger("benchlab.__main__")

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # pragma: no cover – defensive guard
        logger.error("Failed to execute Benchlab main entry point: %s", exc)
        sys.exit(1)
