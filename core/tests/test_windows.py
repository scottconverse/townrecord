"""Desktop authentication, per-user configuration and safe lifecycle contracts."""

import contextlib
import os
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from townrecord import serving, windows
from townrecord.api.app import create_app
from townrecord.api.tokens import SCOPE_READ, create_token, find_token


@pytest.fixture
def desktop(tmp_path):
    root = tmp_path / "TownRecord"
    root.mkdir()
    settings = windows.load_settings(root)
    with contextlib.closing(windows._open_database(settings.db_path)) as conn:
        token = create_token(conn, "Desktop", "read_write")
        read_only = create_token(conn, "Observer", SCOPE_READ)
    host = windows.Desktop(root, settings, token)
    app = create_app(settings)
    app.state.desktop = host
    with TestClient(app, base_url="http://127.0.0.1:8190") as client:
        yield host, client, {"Authorization": f"Bearer {token}"}, read_only


def test_exchange_one_use_expiry_origin_host_and_no_anonymous_operator(desktop, monkeypatch):
    host, client, headers, _ = desktop
    assert client.get("/v1/desktop/info").json() == {"desktop": True}
    assert client.get("/v1/desktop/settings").status_code == 401
    assert client.post("/v1/desktop/launch").status_code == 401
    code = host.launch_code()
    for origin in (None, "http://evil.example", "null"):
        origin_headers = {"Origin": origin} if origin else {}
        assert (
            client.post(
                "/v1/desktop/exchange", json={"code": code}, headers=origin_headers
            ).status_code
            == 403
        )
    assert (
        client.post(
            "/v1/desktop/exchange",
            json={"code": code},
            headers={"Origin": "http://127.0.0.1:8190", "Host": "evil.example:8190"},
        ).status_code
        == 403
    )
    origin = {"Origin": "http://127.0.0.1:8190"}
    response = client.post("/v1/desktop/exchange", json={"code": code}, headers=origin)
    assert response.json() == {"token": host.token}
    assert (
        client.post("/v1/desktop/exchange", json={"code": code}, headers=origin).status_code == 401
    )
    expired = host.launch_code()
    monkeypatch.setattr(windows.time, "monotonic", lambda: float("inf"))
    assert (
        client.post("/v1/desktop/exchange", json={"code": expired}, headers=origin).status_code
        == 401
    )
    assert client.get("/v1/desktop/settings", headers=headers).status_code == 200


def test_settings_persist_and_apply_next_launch_readonly_refused(desktop):
    host, client, headers, read_only = desktop
    values = {
        "time_zone": "America/Denver",
        "daily_time": "07:45",
        "test_video_url": "https://example.com/meeting.mp4",
    }
    assert (
        client.put(
            "/v1/desktop/settings", json=values, headers={"Authorization": f"Bearer {read_only}"}
        ).status_code
        == 403
    )
    assert (
        client.put(
            "/v1/desktop/settings", json={**values, "time_zone": "invalid"}, headers=headers
        ).status_code
        == 422
    )
    assert (
        client.put(
            "/v1/desktop/settings",
            json={**values, "test_video_url": "file:///secret"},
            headers=headers,
        ).status_code
        == 422
    )
    response = client.put("/v1/desktop/settings", json=values, headers=headers)
    assert response.json()["restart_required"] is True
    settings = windows.load_settings(host.root)
    assert settings.time_zone == values["time_zone"]
    assert settings.daily_time.strftime("%H:%M") == "07:45"
    assert settings.db_path == host.settings.db_path
    assert windows.Desktop(host.root, settings, host.token).info()["restart_required"] is False


def test_pending_backup_cannot_be_replaced_and_settings_wait(desktop, monkeypatch):
    host, client, headers, _ = desktop
    timer = Mock()
    monkeypatch.setattr(windows.threading, "Timer", timer)
    response = client.post("/v1/desktop/backup", headers=headers)
    assert response.status_code == 200
    assert Path(response.json()["path"]).is_relative_to(host.root / "backups")
    assert host.info()["restart_required"] is True
    assert client.post("/v1/desktop/stop", headers=headers).status_code == 409
    assert client.post("/v1/desktop/restart", headers=headers).status_code == 409
    assert host.action == "backup"
    assert timer.call_count == 1


def test_restore_keeps_original_and_validates_backup(desktop, monkeypatch):
    from townrecord.recovery import backup

    host, client, headers, _ = desktop
    monkeypatch.setattr(windows.threading, "Timer", Mock())
    backup_path = host.root / "backup"
    backup(host.settings, backup_path)
    original = host.settings.db_path.read_bytes()
    bad = client.post(
        "/v1/desktop/restore", json={"source": str(host.root / "missing")}, headers=headers
    )
    assert bad.status_code == 422
    assert not host.transitioning
    response = client.post(
        "/v1/desktop/restore", json={"source": str(backup_path)}, headers=headers
    )
    assert response.status_code == 200
    restored = windows.load_settings(host.root)
    assert restored.db_path != host.settings.db_path
    assert restored.db_path.is_file()
    assert host.settings.db_path.read_bytes() == original
    assert (backup_path / "manifest.json").is_file()
    assert host.action == "restart" and host.transitioning


def test_stale_desktop_state_never_sends_credential(tmp_path, monkeypatch):
    windows.write_json(tmp_path / "desktop-service.json", {"api": "http://127.0.0.1:8190"})
    (tmp_path / "operator.dpapi").write_bytes(b"stale-secret")
    decrypt = Mock(side_effect=AssertionError("must not decrypt without ownership"))
    monkeypatch.setattr(windows, "protect", decrypt)
    assert windows.contact(tmp_path, "stop") == 0
    decrypt.assert_not_called()
    with pytest.raises(RuntimeError, match="not running"):
        windows.contact(tmp_path, "open")


@pytest.mark.skipif(os.name != "nt", reason="Windows current-user DPAPI")
def test_dpapi_roundtrip_and_credential_survives_restart(tmp_path):
    settings = windows.load_settings(tmp_path)
    first = windows.operator_credential(tmp_path, settings)
    encrypted = (tmp_path / "operator.dpapi").read_bytes()
    assert first.encode() not in encrypted
    assert windows.protect(encrypted, decrypt=True).decode() == first
    assert windows.operator_credential(tmp_path, settings) == first
    with contextlib.closing(windows._open_database(settings.db_path)) as conn:
        assert find_token(conn, first)["scope"] == "read_write"


def test_developer_cli_keeps_existing_auth(tmp_path):
    app = create_app(windows.load_settings(tmp_path))
    with TestClient(app) as client:
        assert client.get("/v1/desktop/info").json() == {"desktop": False}
        assert client.post("/v1/desktop/exchange", json={"code": "x" * 43}).status_code == 401
        assert client.get("/v1/operator/overview").status_code == 401
        assert client.get("/").status_code == 200


def test_backup_waits_for_workers_and_keeps_launcher_claim(tmp_path, monkeypatch):
    from townrecord import recovery

    root = tmp_path / "TownRecord"
    root.mkdir()
    profile = windows.load_settings(root).runtime_root
    calls = []

    class Service:
        stopped = False

        def start(self):
            calls.append("start")

        def stop(self, timeout):
            assert serving.is_running(profile)
            assert serving.is_running(root / "desktop")
            if calls.count("wait") == 0:
                calls.append("wait")
                return False
            self.stopped = True
            calls.append("stopped")
            return True

        def close(self):
            assert self.stopped
            calls.append("closed")

    class Server:
        def __init__(self, config):
            self.host = config.app.state.desktop

        def run(self, sockets):
            if "backup" not in calls:
                self.host.action = "backup"
                self.host.pending = root / "backups" / "one"
                self.host.operation_id = "first-backup"
            else:
                self.host.action = "stop"

    def backup(settings, destination):
        assert not serving.is_running(profile)
        assert serving.is_running(root / "desktop")
        assert calls[-2:] == ["stopped", "closed"]
        calls.append("backup")

    monkeypatch.setattr(windows, "operator_credential", lambda *_: "test-token")
    monkeypatch.setattr(windows, "bound_socket", lambda *_: Mock())
    monkeypatch.setattr(windows, "build_service", lambda *_: Service())
    monkeypatch.setattr(windows.uvicorn, "Server", Server)
    monkeypatch.setattr(recovery, "backup", backup)
    assert windows.run(root, open_browser=False) == 0
    assert calls == ["start", "wait", "stopped", "closed", "backup", "start", "stopped", "closed"]
    assert not serving.is_running(root / "desktop")
    assert not serving.is_running(profile)
    assert windows.read_json(root / "last-operation.json")["operation_id"] == "first-backup"


def test_desktop_ca_bundle_is_verified_default_and_explicit_override_is_preserved(monkeypatch):
    import ssl

    import certifi

    from townrecord.proc import allowed_environment

    monkeypatch.setenv("SSL_CERT_FILE", "initial-ca-fixture")
    monkeypatch.delenv("SSL_CERT_FILE")
    monkeypatch.setenv("PATH", "original-path")
    monkeypatch.setenv("TOWNRECORD_RUNTIME_PYTHON", "original-runtime-python")
    monkeypatch.setenv("TOWNRECORD_RUNTIME_SEED_ROOT", "original-seed-root")
    windows.configure_path()
    assert os.environ["SSL_CERT_FILE"] == certifi.where()
    assert allowed_environment()["SSL_CERT_FILE"] == certifi.where()
    assert ssl.create_default_context().verify_mode == ssl.CERT_REQUIRED
    monkeypatch.setenv("SSL_CERT_FILE", "C:/operator/explicit-ca.pem")
    windows.configure_path()
    assert allowed_environment()["SSL_CERT_FILE"] == "C:/operator/explicit-ca.pem"
