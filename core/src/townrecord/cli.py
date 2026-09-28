"""The ``townrecord`` command (spec 14.2, 16.1, 16.2, 13.1, 16.3).

Four commands, and each one is a small piece of the same core:

``townrecord serve``
    Starts the local API, the job workers of spec 16.1 and the daily schedule
    of spec 16.2 in one process tree. Every path it uses -- the database, the
    artifact storage root, the private runtime root -- comes from
    :class:`townrecord.config.Settings` and is never invented here; the port
    comes from the same place, and a port that is already in use is a plain
    sentence naming the port and the setting to change. Ctrl+C, and the
    desktop shell's stop, end the workers cleanly: a job that was running
    keeps its checkpoint and is claimable again (spec 16.1).

``townrecord token create`` / ``token list``
    The tokens of spec 13.1. A token is printed once, at the moment it is
    created; the list shows names and scopes and never a token.

``townrecord status``
    What the service is actually doing, read from real content (spec 16.3):
    job counts by lane and state, sources and their health, the newest capture
    of every body, the active yt-dlp version, and the runs the schedule made or
    could not make. It never answers "OK", and it reports the service that is
    running and not the settings of the shell that asked: when a service holds
    the app-data root, its address, database, schedule and time zone are what
    the report shows, and when none does the report says so and says that what
    it shows is this shell's configuration.

The API is started on a socket this module binds itself, so a busy port is
reported before any worker starts rather than as a stack trace from inside
uvicorn. On Windows ``SO_REUSEADDR`` is deliberately not set: on that platform
it lets a second process bind a port another program is listening on, which is
exactly the surprise this check exists to prevent.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import json
import os
import socket
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from . import serving
from .api.app import create_app
from .api.tokens import SCOPE_READ, SCOPE_READ_WRITE, create_token
from .config import Settings
from .db import connect, migrate
from .service import build_service
from .status import collect, render

#: The scopes the command line accepts, and the scope each one means. "write"
#: is the name a person types; ``read_write`` is what the table stores
#: (spec 13.1).
SCOPE_NAMES = {"read": SCOPE_READ, "write": SCOPE_READ_WRITE}

#: The exit code of a command that could not do what it was asked.
EXIT_REFUSED = 2

#: The backlog of the API socket. It is the number ``Config.bind_socket`` uses.
LISTEN_BACKLOG = 128


class PortBusy(RuntimeError):
    """The port the API was asked to listen on is already in use."""


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command named by ``argv`` and return its exit code."""
    parser = _parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    return args.run(args)


def _parser() -> argparse.ArgumentParser:
    """Build the command line."""
    parser = argparse.ArgumentParser(
        prog="townrecord",
        description="Run TownRecord: the local API, the job workers and the daily schedule.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    serve = commands.add_parser(
        "serve", help="start the API, the job workers and the daily schedule"
    )
    serve.set_defaults(run=_serve)

    token = commands.add_parser("token", help="create and list API tokens")
    token_commands = token.add_subparsers(dest="token_command", required=True)
    create = token_commands.add_parser("create", help="create a token and print it once")
    create.add_argument("--name", required=True, help="what this token is for")
    create.add_argument(
        "--scope",
        choices=sorted(SCOPE_NAMES),
        default="read",
        help="read for reading only, write for reading and changing (default: read)",
    )
    create.set_defaults(run=_token_create)
    listing = token_commands.add_parser("list", help="list tokens by name and scope")
    listing.set_defaults(run=_token_list)

    status = commands.add_parser("status", help="report what the service is doing")
    status.add_argument("--json", action="store_true", help="print the report as JSON")
    status.set_defaults(run=_status)
    return parser


# -- serve -----------------------------------------------------------------


def _serve(_args: argparse.Namespace) -> int:
    """Start the API, the job workers and the schedule, and run until stopped."""
    settings = Settings.from_env()
    _prepare_database(settings)
    service = build_service(settings)
    try:
        listener = bound_socket(settings.host, settings.port)
    except PortBusy as exc:
        print(str(exc), file=sys.stderr)
        service.close()
        return EXIT_REFUSED
    server = uvicorn.Server(uvicorn.Config(app=create_app(settings), log_level="info"))
    service.start()
    # Say what this service runs with, where another shell's `townrecord status`
    # reads it (spec 16.3), and hold the claim on this app-data root for as long
    # as it serves. It is taken once the socket is ours, so a refused port never
    # claims a root it is not serving from.
    claim = serving.record(
        settings.runtime_root,
        host=settings.host,
        port=settings.port,
        daily_time=settings.daily_time,
        time_zone=settings.time_zone,
        db_path=str(settings.db_path),
    )
    print(
        f"TownRecord is serving at http://{settings.host}:{settings.port} "
        f"and running {len(service.registry.kinds())} job kinds.",
        flush=True,
    )
    try:
        server.run(sockets=[listener])
    except KeyboardInterrupt:  # a second Ctrl+C while the server is stopping
        print("TownRecord is stopping.", flush=True)
    finally:
        serving.clear(claim)
        if not service.stop():
            print(
                "A worker did not stop within its timeout. A job it was running keeps its "
                "checkpoint and stays claimable.",
                file=sys.stderr,
            )
        service.close()
        with contextlib.suppress(OSError):
            listener.close()
    return 0


def bound_socket(host: str, port: int) -> socket.socket:
    """Bind the API socket, or refuse with the port and the setting to change.

    Binding here rather than inside uvicorn is what turns a busy port into one
    plain sentence before any worker starts. ``SO_REUSEADDR`` is set only where
    it means what it says: on Windows it would let this process share a port
    another program is already listening on.
    """
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name != "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((host, port))
        listener.listen(LISTEN_BACKLOG)
    except OSError as exc:
        listener.close()
        raise PortBusy(
            f"Port {port} is already in use, so the TownRecord API could not start. "
            f"Stop the program using it, or set TOWNRECORD_PORT to a free port ({exc})."
        ) from exc
    return listener


def _prepare_database(settings: Settings) -> None:
    """Create the database's folder and apply the migrations.

    The API does the same thing when it starts, and doing it here as well means
    the workers and the schedule never open a database whose tables are not
    there yet. Migrations are applied once each, so running them twice is the
    same as running them once.
    """
    with contextlib.closing(_open_database(settings.db_path)):
        pass


# -- tokens ----------------------------------------------------------------


def _token_create(args: argparse.Namespace) -> int:
    """Create a token and print it once (spec 13.1)."""
    settings = Settings.from_env()
    scope = SCOPE_NAMES[args.scope]
    with contextlib.closing(_open_database(settings.db_path)) as conn:
        token = create_token(conn, args.name, scope)
    print(f"Created the token {args.name!r} with scope {scope}.")
    print("It is shown once, here, and is not stored in a readable form:")
    print(token)
    return 0


def _token_list(_args: argparse.Namespace) -> int:
    """List the tokens by name and scope, and never print a token (spec 13.1)."""
    settings = Settings.from_env()
    with contextlib.closing(_open_database(settings.db_path)) as conn:
        rows = conn.execute(
            "SELECT id, name, scope, created_at FROM api_tokens ORDER BY id"
        ).fetchall()
    if not rows:
        print("No tokens have been created yet. Create one with `townrecord token create`.")
        return 0
    print(f"{len(rows)} token(s); the tokens themselves are never shown:")
    for row in rows:
        print(f"  {row['id']:>3}  {row['name']:<20} {row['scope']:<11} {row['created_at']}")
    return 0


# -- status ----------------------------------------------------------------


def _status(args: argparse.Namespace) -> int:
    """Report what the service is doing, from real content (spec 16.3)."""
    settings = Settings.from_env()
    report = collect(settings)
    if args.json:
        print(json.dumps(dataclasses.asdict(report), indent=2, sort_keys=True))
    else:
        print(render(report))
    return 0


def _open_database(db_path: str | Path) -> sqlite3.Connection:
    """Open the database, creating its folder and applying migrations first."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        migrate(conn)
    except BaseException:
        conn.close()
        raise
    return conn


__all__ = [
    "EXIT_REFUSED",
    "LISTEN_BACKLOG",
    "SCOPE_NAMES",
    "PortBusy",
    "bound_socket",
    "main",
]
