"""Desktop lifecycle routes; privileged operations retain operator authorization."""

import secrets
import sqlite3
import threading
from pathlib import Path
from zoneinfo import ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .community import operator_token

router = APIRouter(tags=["desktop"])


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Exchange(Input):
    code: str = Field(min_length=32, max_length=100)


class Configuration(Input):
    time_zone: str = Field(min_length=1, max_length=100)
    daily_time: str = Field(min_length=5, max_length=5)
    test_video_url: str = Field(min_length=1, max_length=2000)


class Restore(Input):
    source: str = Field(min_length=1, max_length=32000)


def desktop(request: Request):
    host = getattr(request.app.state, "desktop", None)
    if host is None:
        raise HTTPException(404, "This operation is available in the Windows desktop installation.")
    return host


def same_origin(request: Request):
    """Prevent DNS rebinding and cross-site exchanges, including no-Origin form posts."""
    expected = f"127.0.0.1:{request.app.state.settings.port}"
    if (
        request.headers.get("host") != expected
        or request.headers.get("origin") != f"http://{expected}"
    ):
        raise HTTPException(403, "Open TownRecord using its Start menu shortcut.")


@router.get("/v1/desktop/info")
def info(request: Request):
    return {"desktop": getattr(request.app.state, "desktop", None) is not None}


@router.post("/v1/desktop/exchange", dependencies=[Depends(same_origin)])
def exchange(data: Exchange, host=Depends(desktop)):
    token = host.exchange(data.code)
    if token is None:
        raise HTTPException(401, "The launch code expired or was already used. Reopen TownRecord.")
    return {"token": token}


@router.post("/v1/desktop/launch", dependencies=[Depends(operator_token)])
def launch(host=Depends(desktop)):
    return {"code": host.launch_code()}


@router.get("/v1/desktop/settings", dependencies=[Depends(operator_token)])
def settings(host=Depends(desktop)):
    return host.info()


@router.put("/v1/desktop/settings", dependencies=[Depends(operator_token)])
def save(data: Configuration, host=Depends(desktop)):
    try:
        return host.save(data.model_dump())
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/v1/desktop/stop", dependencies=[Depends(operator_token)])
def stop(host=Depends(desktop)):
    action(host, "stop")
    return {"status": "stopping"}


@router.post("/v1/desktop/restart", dependencies=[Depends(operator_token)])
def restart(host=Depends(desktop)):
    action(host, "restart")
    return {"status": "restarting", "operation_id": host.operation_id}


@router.post("/v1/desktop/backup", dependencies=[Depends(operator_token)])
def backup(host=Depends(desktop)):
    from ..windows import stamp

    target = host.root / "backups" / stamp()
    action(host, "backup", target)
    return {"status": "restarting", "path": str(target), "operation_id": host.operation_id}


@router.post("/v1/desktop/restore", dependencies=[Depends(operator_token)])
def restore(data: Restore, host=Depends(desktop)):
    from ..recovery import restore as restore_profile
    from ..windows import read_json, stamp, write_json

    target = host.root / "profiles" / stamp()
    try:
        with host.lock:
            if host.transitioning:
                raise HTTPException(409, "TownRecord is already stopping or restarting.")
            manifest = restore_profile(Path(data.source), target)
            restored = manifest["settings"]
            from zoneinfo import ZoneInfo

            from ..config import parse_daily_time

            if restored.get("time_zone"):
                ZoneInfo(restored["time_zone"])
            parse_daily_time(restored.get("daily_time", "06:00"))
            config = read_json(host.root / "config.json")
            config.update({k: restored[k] for k in ("time_zone", "daily_time", "test_video_url")})
            config["profile"] = str(target)
            write_json(host.root / "config.json", config)
            # Keep configuration/action reservation atomic against other requests.
            host.transitioning = True
            host.operation_id = secrets.token_urlsafe(16)
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        raise HTTPException(422, str(exc)) from exc
    host.action = "restart"
    if host.server:
        threading.Timer(0.25, lambda: setattr(host.server, "should_exit", True)).start()
    return {"status": "restarting", "path": str(target), "operation_id": host.operation_id}


def action(host, name, pending=None):
    try:
        host.request_action(name, pending)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
