"""Run the core from the source tree: `uv run python -m townrecord <command>`.

The installed console script (``townrecord`` in ``pyproject.toml``) calls the
same :func:`townrecord.cli.main`, so both ways in run the same code.
"""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
