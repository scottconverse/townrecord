"""One SDK composition and trusted source policy for jobs and the local API."""

from __future__ import annotations

import json
import os
import sqlite3

from civic_data_connectors import Bounds, OperationContext, Source, create_engine

from .connector_storage import AppStorage
from .repo import Source as LocalSource
from .repo import get_source

READ_OPERATIONS = frozenset(
    {
        "providers",
        "capabilities",
        "identify",
        "validate_source",
        "discover",
        "fetch",
        "normalize",
        "import_preview",
        "import_bundle",
        "search",
        "export",
        "status",
        "cancel",
    }
)


def engine_for(conn: sqlite3.Connection, **kwargs):
    """Defaults retain production adapters but grant no credentials or permissions."""
    if os.environ.get("TOWNRECORD_CONNECTOR_WRITES_ENABLED", "true").casefold() in {
        "0",
        "false",
        "no",
    }:
        kwargs.setdefault("write_adapters", {})
    return create_engine(storage=AppStorage(conn), **kwargs)


def configuration(source: LocalSource) -> dict | None:
    settings = json.loads(source.settings)
    value = settings.get("connector")
    if value is not None and not isinstance(value, dict):
        raise ValueError("The source connector configuration must be an object.")
    return value


def configured_source(conn: sqlite3.Connection, source_id: int) -> tuple[LocalSource, Source]:
    from .records.portal import syncable_source

    local = syncable_source(conn, source_id)
    configured = configuration(local)
    if configured is None:
        raise ValueError("This source uses the legacy PrimeGov path, not a shared connector.")
    fields = {key: value for key, value in configured.items() if key != "bounds"}
    return local, Source.from_dict({**fields, "url": local.origin})


def source_context(local: LocalSource, *, operation_id: str | None = None) -> OperationContext:
    configured = configuration(local) or {}
    provider, namespace = configured.get("provider"), configured.get("namespace")
    credentials = json.loads(os.environ.get("TOWNRECORD_CONNECTOR_CREDENTIALS", "{}"))
    if not isinstance(credentials, dict):
        raise ValueError("The server connector credentials are invalid.")
    source_credentials = {provider: credentials[provider]} if provider in credentials else {}
    read_token = {
        "legistar": "legistar_token",
        "wistia": "wistia_token",
        "laserfiche-weblink": "laserfiche_token",
    }.get(provider)
    if read_token in credentials:
        source_credentials[read_token] = credentials[read_token]
    return OperationContext(
        principal="townrecord",
        client_id="townrecord",
        tenant=str(local.jurisdiction_id),
        operation_id=operation_id,
        allowed_operations=READ_OPERATIONS,
        credentials=source_credentials,
        allowed_namespaces=frozenset({namespace})
        if isinstance(namespace, str) and namespace
        else None,
    )


def bounds_for(local: LocalSource, **overrides) -> Bounds:
    from .records.portal import size_limits

    configured = configuration(local) or {}
    values = {**configured.get("bounds", {}), **overrides}
    transfer = size_limits()["packet_limit_bytes"]
    values["transfer_bytes"] = min(values.get("transfer_bytes", transfer), transfer)
    return Bounds(**values)


def write_context(conn: sqlite3.Connection, source_id: int, token_id: int) -> OperationContext:
    """Only process-owner configuration grants writes; request JSON cannot."""
    local = get_source(conn, source_id)
    if local is None:
        raise ValueError("The configured source does not exist.")
    grants = json.loads(os.environ.get("TOWNRECORD_CONNECTOR_WRITE_GRANTS", "{}"))
    credentials = json.loads(os.environ.get("TOWNRECORD_CONNECTOR_CREDENTIALS", "{}"))
    approved = grants.get(str(token_id), [])
    if not isinstance(approved, list) or any(
        permission not in {"write:plan", "write:commit"} for permission in approved
    ):
        raise ValueError("The server write grants are invalid.")
    return OperationContext(
        principal=f"api-token:{token_id}",
        client_id="townrecord",
        tenant=str(local.jurisdiction_id),
        authorization=frozenset(approved),
        credentials=credentials,
        allowed_operations=frozenset(
            {
                "plan_write",
                "commit_write",
                "receipt",
                "status",
                "cancel",
            }
        ),
    )
