"""Per-user Windows desktop host; the CLI service remains usable independently."""

from __future__ import annotations

import argparse
import contextlib
import ctypes
import json
import logging
import os
import secrets
import sys
import threading
import time
import webbrowser
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import uvicorn

from . import serving
from .api.app import create_app
from .api.tokens import SCOPE_READ_WRITE, create_token, find_token
from .cli import _open_database, bound_socket
from .config import Settings, parse_daily_time
from .service import build_service


def desktop_root() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "TownRecord"


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def protect(data: bytes, *, decrypt: bool = False) -> bytes:
    """Use current-user Windows DPAPI; no plaintext or portable fallback."""
    if os.name != "nt":
        raise OSError("The desktop credential requires Windows DPAPI.")
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(Blob),
    ]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    # CRYPTPROTECT_UI_FORBIDDEN; machine-wide encryption is deliberately absent.
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(output.data)


def load_settings(root: Path) -> Settings:
    data = read_json(root / "config.json")
    profile = Path(data.get("profile", str(root / "data"))).resolve()
    return Settings(
        db_path=profile / "townrecord.db",
        storage_root=profile / "storage",
        runtime_root=profile,
        time_zone=data.get("time_zone", ""),
        daily_time=parse_daily_time(data.get("daily_time", "06:00")),
        test_video_url=data.get("test_video_url", ""),
    )


def settings_environment(settings: Settings) -> dict[str, str]:
    """Give legacy environment-based handlers the selected desktop profile."""
    return {
        "TOWNRECORD_DB": str(settings.db_path),
        "TOWNRECORD_STORAGE": str(settings.storage_root),
        "TOWNRECORD_RUNTIME_ROOT": str(settings.runtime_root),
        "TOWNRECORD_TIME_ZONE": settings.time_zone,
        "TOWNRECORD_DAILY_TIME": settings.daily_time.strftime("%H:%M"),
        "TOWNRECORD_TEST_VIDEO": settings.test_video_url,
    }


def operator_credential(root: Path, settings: Settings) -> str:
    path = root / "operator.dpapi"
    token = protect(path.read_bytes(), decrypt=True).decode() if path.exists() else None
    with contextlib.closing(_open_database(settings.db_path)) as conn:
        if token is None or find_token(conn, token) is None:
            token = create_token(conn, "Windows desktop operator", SCOPE_READ_WRITE)
            path.write_bytes(protect(token.encode()))
    return token


class Desktop:
    def __init__(self, root: Path, settings: Settings, token: str):
        self.root, self.settings, self.token = root, settings, token
        self.codes: dict[str, float] = {}
        self.lock = threading.Lock()
        self.server: uvicorn.Server | None = None
        self.action = "stop"
        self.pending: Path | None = None
        self.transitioning = False
        self.operation_id: str | None = None

    def launch_code(self) -> str:
        with self.lock:
            now = time.monotonic()
            self.codes = {k: v for k, v in self.codes.items() if v > now}
            code = secrets.token_urlsafe(32)
            self.codes[code] = now + 120
            return code

    def exchange(self, code: str) -> str | None:
        with self.lock:
            expiry = self.codes.pop(code, 0)
        return self.token if expiry > time.monotonic() else None

    def info(self) -> dict:
        settings = self.settings
        configured = read_json(self.root / "config.json")
        current = {
            "time_zone": settings.time_zone,
            "daily_time": settings.daily_time.strftime("%H:%M"),
            "test_video_url": settings.test_video_url,
        }
        return {
            "desktop": True,
            **current,
            "data_path": str(settings.runtime_root),
            "backup_path": str(self.root / "backups"),
            "setup_required": not (settings.time_zone and settings.test_video_url),
            "restart_required": self.transitioning
            or any(configured.get(k, v) != v for k, v in current.items()),
            "last_operation": read_json(self.root / "last-operation.json"),
        }

    def save(self, data: dict) -> dict:
        ZoneInfo(data["time_zone"])
        parse_daily_time(data["daily_time"])
        url = httpx.URL(data["test_video_url"])
        if url.scheme not in {"http", "https"} or not url.host:
            raise ValueError("Choose an http or https URL for a known source recording.")
        with self.lock:
            if self.transitioning:
                raise ValueError("TownRecord is restarting; wait before changing settings.")
            config = read_json(self.root / "config.json")
            config.update(data)
            write_json(self.root / "config.json", config)
        return self.info()

    def request_action(self, action: str, pending: Path | None = None) -> None:
        with self.lock:
            if self.transitioning:
                raise ValueError(
                    "TownRecord is already stopping or restarting. Wait for it to finish."
                )
            self.action, self.pending = action, pending
            self.transitioning = True
            self.operation_id = secrets.token_urlsafe(16)

        # Delay until the API response has left the request handler.
        def stop():
            if self.server:
                self.server.should_exit = True

        threading.Timer(0.25, stop).start()


def stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")


def configure_path() -> None:
    import certifi

    python = Path(sys.executable).parent
    tools = python.parent / "tools"
    os.environ["PATH"] = os.pathsep.join([str(tools), str(python), os.environ.get("PATH", "")])
    # uv receives this interpreter explicitly; a clean desktop needs no system
    # Python discovery or download, and pythonw itself is not a venv base.
    os.environ["TOWNRECORD_RUNTIME_PYTHON"] = str(python / "python.exe")
    os.environ["TOWNRECORD_RUNTIME_SEED_ROOT"] = str(python.parent / "runtime-seeds")
    # Portable Python may not inherit a populated Windows certificate store.
    # Keep TLS verification and an explicit operator CA override intact.
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())


def run(root: Path, *, open_browser: bool) -> int:
    from .recovery import backup

    # A separate launcher claim spans service restart and backup lock release.
    launcher = serving.ServiceLock.acquire(root / "desktop")
    if not launcher.held:
        return contact(root, "open" if open_browser else "start")
    previous_environment = {key: os.environ.get(key) for key in settings_environment(Settings())}
    try:
        while True:
            settings = load_settings(root)
            # Some connector handlers still obtain settings from the process.
            # Rebind after every restart, including a restored profile switch.
            os.environ.update(settings_environment(settings))
            token = operator_credential(root, settings)
            listener = bound_socket(settings.host, settings.port)
            desktop = Desktop(root, settings, token)
            app = create_app(settings)
            app.state.desktop = desktop
            service = build_service(settings)
            claim = serving.record(
                settings.runtime_root,
                host=settings.host,
                port=settings.port,
                daily_time=settings.daily_time,
                time_zone=settings.time_zone,
                db_path=str(settings.db_path),
            )
            if not claim.held:
                listener.close()
                service.close()
                raise RuntimeError("Another TownRecord service already owns this data folder.")
            write_json(
                root / "desktop-service.json",
                {"api": f"http://127.0.0.1:{settings.port}", "profile": str(settings.runtime_root)},
            )
            server = uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False))
            desktop.server = server
            service.start()
            if open_browser:
                code = desktop.launch_code()

                def browse(code=code, server=server, port=settings.port):
                    for _ in range(300):
                        if server.started:
                            webbrowser.open(f"http://127.0.0.1:{port}/operator#launch={code}")
                            return
                        if server.should_exit:
                            return
                        time.sleep(0.1)

                threading.Thread(target=browse, daemon=True).start()
                open_browser = False
            try:
                server.run(sockets=[listener])
            finally:
                while not service.stop(timeout=30):
                    # Keep both claims until workers finish. The installer stop
                    # call can time out safely without changing installed files.
                    logging.warning("Waiting for an active worker before releasing the profile.")
                service.close()
                serving.clear(claim)
                listener.close()
            if desktop.action == "backup":
                try:
                    backup(settings, desktop.pending)
                    result = {"kind": "backup", "success": True, "path": str(desktop.pending)}
                except Exception as exc:
                    logging.exception("Desktop backup failed")
                    result = {"kind": "backup", "success": False, "error": str(exc)}
                result["operation_id"] = desktop.operation_id
                write_json(root / "last-operation.json", result)
            elif desktop.action == "restart":
                write_json(
                    root / "last-operation.json",
                    {"kind": "restart", "success": True, "operation_id": desktop.operation_id},
                )
            if desktop.action == "stop":
                return 0
    finally:
        (root / "desktop-service.json").unlink(missing_ok=True)
        launcher.release()
        for key, value in previous_environment.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def contact(root: Path, command: str) -> int:
    """Authenticate to the running service, never signal a PID from disk."""
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if not serving.is_running(root / "desktop"):
            if command == "stop":
                return 0
            raise RuntimeError("TownRecord is not running. Open its Start menu shortcut again.")
        info = read_json(root / "desktop-service.json")
        if info:
            settings = load_settings(root)
            profile = Path(info.get("profile", "")).resolve()
            if not profile.is_relative_to(root.resolve()):
                raise RuntimeError("The desktop profile is outside the TownRecord data folder.")
            served = serving.read(profile)
            if not (
                serving.is_running(profile)
                and served
                and served.host == "127.0.0.1"
                and served.port == settings.port
                and Path(served.db_path).resolve() == profile / "townrecord.db"
            ):
                time.sleep(0.2)
                continue
            token_path = root / "operator.dpapi"
            if token_path.exists():
                token = protect(token_path.read_bytes(), decrypt=True).decode()
                # Never send credentials to an address supplied by a state file.
                api = f"http://127.0.0.1:{settings.port}"
                try:
                    with httpx.Client(trust_env=False, timeout=5) as client:
                        endpoint = "/v1/desktop/stop" if command == "stop" else "/v1/desktop/launch"
                        response = client.post(
                            api + endpoint, headers={"Authorization": f"Bearer {token}"}
                        )
                        response.raise_for_status()
                        if command == "open":
                            webbrowser.open(api + "/operator#launch=" + response.json()["code"])
                    break
                except httpx.ConnectError:
                    pass
        if command == "stop" and not serving.is_running(root / "desktop"):
            return 0
        time.sleep(0.2)
    else:
        raise RuntimeError("TownRecord did not answer within 60 seconds. See desktop.log.")
    if command == "stop":
        while time.monotonic() < deadline:
            if not serving.is_running(root / "desktop"):
                return 0
            time.sleep(0.2)
        raise RuntimeError(
            "TownRecord has not finished stopping; installed files were not changed."
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TownRecord Windows desktop")
    parser.add_argument("command", nargs="?", choices=["open", "start", "stop"], default="open")
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)
    root = desktop_root()
    root.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=root / "desktop.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    # pythonw has no streams; libraries may write diagnostics on startup/shutdown.
    if sys.stdout is None:
        sys.stdout = open(root / "desktop-console.log", "a", encoding="utf-8")  # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = sys.stdout
    configure_path()
    try:
        if args.stop or args.command == "stop":
            return contact(root, "stop")
        return run(root, open_browser=args.command == "open" and not args.no_browser)
    except Exception as exc:
        logging.exception("Desktop operation failed")
        if os.name == "nt" and not (args.stop or args.command == "stop"):
            ctypes.windll.user32.MessageBoxW(
                None, f"{exc}\n\nDetails: {root / 'desktop.log'}", "TownRecord", 0x10
            )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
