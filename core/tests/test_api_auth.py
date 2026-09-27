"""Every route needs a bearer token (spec 13.1, decision 10 rule 2)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_VERSION

#: The four routes that must answer 401 without a token.
PROTECTED_PATHS = ["/v1/health", "/docs", "/redoc", "/openapi.json"]


@pytest.mark.parametrize("path", PROTECTED_PATHS)
def test_no_token_is_401(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 401, f"{path} answered {response.status_code}"


@pytest.mark.parametrize("path", PROTECTED_PATHS)
def test_a_wrong_token_is_401(client: TestClient, path: str) -> None:
    response = client.get(path, headers={"Authorization": "Bearer tr_not-a-real-token"})
    assert response.status_code == 401, f"{path} answered {response.status_code}"


@pytest.mark.parametrize("path", PROTECTED_PATHS)
def test_a_valid_token_is_200(client: TestClient, read_token: str, path: str) -> None:
    response = client.get(path, headers={"Authorization": f"Bearer {read_token}"})
    assert response.status_code == 200, f"{path} answered {response.status_code}"


def test_the_401_carries_a_bearer_challenge(client: TestClient) -> None:
    response = client.get("/v1/health")
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


def test_health_with_a_read_token(client: TestClient, read_token: str) -> None:
    response = client.get("/v1/health", headers={"Authorization": f"Bearer {read_token}"})
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": TEST_VERSION}


def test_health_with_a_read_write_token(client: TestClient, read_write_token: str) -> None:
    response = client.get("/v1/health", headers={"Authorization": f"Bearer {read_write_token}"})
    assert response.status_code == 200


@pytest.mark.parametrize(
    "header",
    ["", "Bearer", "Bearer ", "Basic dXNlcjpwYXNz", "tr_missing-the-scheme"],
)
def test_a_malformed_authorization_header_is_401(client: TestClient, header: str) -> None:
    response = client.get("/v1/health", headers={"Authorization": header})
    assert response.status_code == 401


def test_a_read_token_cannot_write(client: TestClient, read_token: str) -> None:
    client.app.post("/v1/write-something")(lambda: {"ok": True})
    response = client.post("/v1/write-something", headers={"Authorization": f"Bearer {read_token}"})
    assert response.status_code == 403


def test_a_read_write_token_can_write(client: TestClient, read_write_token: str) -> None:
    client.app.post("/v1/write-something")(lambda: {"ok": True})
    response = client.post(
        "/v1/write-something", headers={"Authorization": f"Bearer {read_write_token}"}
    )
    assert response.status_code == 200


def test_the_docs_page_lists_the_health_route(client: TestClient, read_token: str) -> None:
    response = client.get("/openapi.json", headers={"Authorization": f"Bearer {read_token}"})
    assert response.status_code == 200
    assert "/v1/health" in response.json()["paths"]
