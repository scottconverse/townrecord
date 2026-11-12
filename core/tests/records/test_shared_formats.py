"""Original format corpus through TownRecord's configured SDK boundary."""

import hashlib
import json
import os
from pathlib import Path

import pytest
from civic_data_connectors.engine import canonical_hash

from townrecord import connectors
from townrecord.repo import insert_source

CORPUS = (
    Path(os.environ["TOWNRECORD_CONNECTOR_BASELINE"])
    if os.getenv("TOWNRECORD_CONNECTOR_BASELINE")
    else None
)
MANIFEST_SHA256 = "85667dab73fb1180fddb99c7dd2435a88e46d6f863b3122626e5109372942ed6"


def test_original_formats_through_configured_sources(area, sync):
    if CORPUS is None:
        pytest.skip("Set TOWNRECORD_CONNECTOR_BASELINE to the pinned original-format corpus")
    manifest = (CORPUS / "cases.json").read_bytes()
    assert hashlib.sha256(manifest).hexdigest() == MANIFEST_SHA256
    cases = json.loads(manifest)
    assert len(cases) == 34 and len({case["connector"] for case in cases}) == 31
    for case in cases:
        capture = (CORPUS / case["file"]).read_bytes()
        assert hashlib.sha256(capture).hexdigest() == case["sha256"]
        source_id = insert_source(
            sync.conn,
            jurisdiction_id=area.jurisdiction_id,
            type="meeting_portal",
            origin=case["source_url"],
            suggested_by="test",
            reason="original capture",
            status="accepted",
            body_id=area.body_id,
            settings=json.dumps(
                {"connector": {"provider": case["connector"], "input_format": case["input_format"]}}
            ),
        )
        local, source = connectors.configured_source(sync.conn, source_id)
        result = connectors.engine_for(sync.conn).normalize(
            source,
            capture,
            input_format=case["input_format"],
            context=connectors.source_context(local),
        )
        assert result.status in {"completed", "partial"}, (case, result.public_dict())
        assert canonical_hash(result.data["compatibility"]) == case["original_output_sha256"]
        assert result.data["capture"]["sha256"] == case["sha256"]
        if case["connector"] == "first-party":
            assert result.status == "partial" and result.coverage["truncated"]
