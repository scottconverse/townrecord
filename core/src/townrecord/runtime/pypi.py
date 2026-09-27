"""Asking PyPI which yt-dlp release is the latest (spec 8.9).

The client is injected, so the caller decides how the network is reached (the
outbound guard of spec 17 is a later unit's job, and the tests use
``httpx.MockTransport``). Nothing here reads the environment for a token or a
proxy: the client the caller built is the whole answer.

Checked live from a shell on 2026-09-27 with the project's own Python:
``https://pypi.org/pypi/yt-dlp/json`` answered HTTP 200 and
``info.version`` was ``"2026.8.19"``, which is the field read here.
"""

from __future__ import annotations

import httpx

from .settings import PYPI_JSON_URL, TOOL_NAME


class RuntimeLookupFailed(RuntimeError):
    """The latest version could not be read, with a plain reason why."""


def latest_version(client: httpx.Client, *, tool: str = TOOL_NAME) -> str:
    """Return the latest version of `tool` on PyPI.

    A refusal, a broken answer or a network that is not there is raised as
    :class:`RuntimeLookupFailed` with one plain sentence. This is a probe, not
    a conclusion (spec 16.3): the caller records the reason rather than reading
    an empty answer as "no update".
    """
    url = PYPI_JSON_URL.format(tool=tool)
    try:
        response = client.get(url)
    except httpx.HTTPError as exc:
        raise RuntimeLookupFailed(f"PyPI could not be reached for {tool}: {exc}") from exc
    if response.status_code != 200:
        raise RuntimeLookupFailed(
            f"PyPI answered {response.status_code} for {tool}, so the latest version is unknown."
        )
    try:
        payload = response.json()
        version = payload["info"]["version"]
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeLookupFailed(
            f"PyPI's answer for {tool} did not hold info.version: {exc}"
        ) from exc
    if not isinstance(version, str) or not version.strip():
        raise RuntimeLookupFailed(f"PyPI's answer for {tool} named no version.")
    return version.strip()
