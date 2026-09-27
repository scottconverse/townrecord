# TownRecord core service

Install uv (https://docs.astral.sh/uv/), then run these commands in this folder.

- `uv sync` installs the dependencies.
- `uv run pytest` runs the tests.
- `uv run ruff check` and `uv run ruff format --check` check the code.
- `uv run python -m townrecord.api` runs the API on 127.0.0.1:8190. Set
  `TOWNRECORD_PORT` to change the port. Every path needs a bearer token, including
  `/docs`. Add a token with `townrecord.api.tokens.create_token`.
