"""Asking PyPI for the latest release (spec 8.9).

The client is injected, so no test here reaches the network: the fake transport
answers with the bytes PyPI's JSON API really sends, which were read live on
2026-09-27 (HTTP 200, ``info.version`` = ``2026.8.19``).
"""

from __future__ import annotations

import httpx
import pytest

from townrecord.runtime import pypi

from .fakes import PYPI_SPELLING, pypi_client, pypi_url


def test_the_latest_version_is_read_from_info_version() -> None:
    """The field is `info.version` under the package's JSON document."""
    calls: list[str] = []
    latest = pypi.latest_version(pypi_client(PYPI_SPELLING, calls=calls))
    assert latest == PYPI_SPELLING
    assert calls == [pypi_url()]


def test_a_refusal_is_a_plain_reason_and_not_an_empty_answer() -> None:
    """A 404 is an unknown version, never `no update available` (spec 16.3)."""
    with pytest.raises(pypi.RuntimeLookupFailed) as caught:
        pypi.latest_version(pypi_client(status_code=404))
    assert "404" in str(caught.value)


def test_a_network_that_is_not_there_is_a_plain_reason() -> None:
    """A transport error is reported, not swallowed into `no update`."""
    client = pypi_client(error=httpx.ConnectError("the network is not there"))
    with pytest.raises(pypi.RuntimeLookupFailed) as caught:
        pypi.latest_version(client)
    assert "the network is not there" in str(caught.value)


@pytest.mark.parametrize("payload", [{}, {"info": {}}, {"info": {"version": ""}}, ["nope"]])
def test_an_answer_without_a_version_is_a_plain_reason(payload: object) -> None:
    """A shape that is not the documented one is refused, not guessed at."""
    with pytest.raises(pypi.RuntimeLookupFailed) as caught:
        pypi.latest_version(pypi_client(payload=payload))
    assert "did not hold info.version" in str(caught.value) or "named no version" in str(
        caught.value
    )
