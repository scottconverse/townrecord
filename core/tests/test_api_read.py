"""The read endpoints of the local API (spec 13.2, decision 10).

Every route is behind the token check, and the list of routes is read from the
application rather than written down here, so a route added later is covered by
the check the moment it exists and cannot be forgotten.

The database is filled through the real repository functions and the real
artifact store, so a document served by ``/file`` is served from bytes the
store wrote, and its hash is the hash in its name.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from tests.conftest import TEST_VERSION
from townrecord import artifacts
from townrecord.api.app import create_app
from townrecord.api.tokens import SCOPE_READ, create_token
from townrecord.captions import parse_vtt
from townrecord.config import Settings
from townrecord.db import connect, migrate
from townrecord.repo import (
    insert_agenda_item,
    insert_body,
    insert_jurisdiction,
    insert_meeting,
    insert_record,
    insert_record_page,
    insert_segment,
    insert_source,
    insert_transcript,
    insert_video,
    insert_vote,
)

#: The bytes of the minutes document. What comes back from /file has to be
#: these bytes, and they have to hash to the SHA-256 in the artifact name.
MINUTES = b"%PDF-1.4\n% Longmont City Council minutes, September 8 2026\n%%EOF\n"

#: The spoken line that the transcript tests read back.
SPEECH = "The ordinance 2026-54 comes before us tonight."

#: A line holding the two characters WebVTT reads as markup: a link in angle
#: brackets, which is what a caption of a spoken web address looks like, and an
#: ampersand. Both have to survive a round trip through the VTT renderer, so
#: this line is the one that says whether the renderer escaped its text.
URL_LINE = "The packet is at <https://longmont.invalid/agenda> & the clerk has copies."


@dataclass(frozen=True)
class Seeded:
    """The ids of one small area, and a read token for it."""

    county: int
    city: int
    body: int
    meeting: int
    quiet_meeting: int
    silent_meeting: int
    video: int
    record: int
    document_sha256: str
    aligned_item: int
    loose_item: int
    token: str


def _seed(conn: sqlite3.Connection, storage_root: Path) -> Seeded:
    """Fill a database with one meeting that has everything and two that do not."""
    county = insert_jurisdiction(conn, type="county", name="Boulder County")
    city = insert_jurisdiction(conn, type="city", name="Longmont", parent_id=county)
    body = insert_body(conn, jurisdiction_id=city, name="City Council")
    portal = insert_source(
        conn,
        jurisdiction_id=city,
        body_id=body,
        type="meeting_portal",
        origin="https://portal.test.invalid",
        suggested_by="discovery",
        reason="The city links to it.",
        status="accepted",
    )
    channel = insert_source(
        conn,
        jurisdiction_id=city,
        type="video_channel",
        origin="https://videos.test.invalid/channel/UC-test",
        suggested_by="discovery",
        reason="The portal lists this channel.",
        status="accepted",
    )
    meeting = insert_meeting(
        conn,
        body_id=body,
        title="City Council Regular Session",
        starts_at="2026-09-08T19:00:00-06:00",
    )
    quiet_meeting = insert_meeting(
        conn,
        body_id=body,
        title="City Council Study Session",
        starts_at="2026-10-01T18:00:00-06:00",
    )
    silent_meeting = insert_meeting(
        conn,
        body_id=body,
        title="City Council Special Session",
        starts_at="2026-11-05T18:00:00-07:00",
    )
    video = insert_video(
        conn,
        source_id=channel,
        meeting_id=meeting,
        platform_video_id="v-0001",
        title="City Council Regular Session",
        is_primary=True,
    )
    # The silent meeting has a recording and no transcript: the two gaps are
    # different facts and answer differently.
    insert_video(
        conn,
        source_id=channel,
        meeting_id=silent_meeting,
        platform_video_id="v-0002",
        is_primary=True,
    )

    caption_bytes = (
        "WEBVTT\n\n"
        "00:00:00.000 --> 00:00:05.000\n"
        "Good evening, everyone.\n\n"
        "01:06:18.000 --> 01:06:25.000\n"
        f"<v Mayor>{SPEECH}</v>\n\n"
        "01:06:25.000 --> 01:06:30.000\n"
        f"<v Clerk>{URL_LINE}</v>\n"
    ).encode()
    transcript_artifact = artifacts.store(conn, storage_root, "transcript", caption_bytes, "vtt")
    transcript = insert_transcript(
        conn,
        video_id=video,
        artifact_id=transcript_artifact.id,
        origin="publisher_captions",
    )
    insert_segment(
        conn,
        transcript_id=transcript,
        start_ms=0,
        end_ms=5_000,
        text="Good evening, everyone.",
    )
    insert_segment(
        conn,
        transcript_id=transcript,
        start_ms=3_978_000,
        end_ms=3_985_000,
        text=SPEECH,
        speaker_label="Mayor",
    )
    insert_segment(
        conn,
        transcript_id=transcript,
        start_ms=3_985_000,
        end_ms=3_990_000,
        text=URL_LINE,
    )

    document = artifacts.store(conn, storage_root, "document", MINUTES, "pdf")
    record = insert_record(
        conn,
        meeting_id=meeting,
        kind="minutes",
        artifact_id=document.id,
        source_id=portal,
        title="Minutes of September 8, 2026",
        page_count=1,
    )
    insert_record_page(
        conn,
        record_id=record,
        page_number=1,
        footer_page_number=3,
        text="Ordinance 2026-54 was adopted on first reading by a voice vote.",
    )
    aligned_item = insert_agenda_item(
        conn,
        meeting_id=meeting,
        number="9A",
        title="Ordinance 2026-54, second reading",
        identifiers={"ordinance": "2026-54"},
        start_ms=3_960_000,
        end_ms=4_000_000,
        alignment_method="html_video_times",
    )
    insert_vote(
        conn,
        agenda_item_id=aligned_item,
        result="passed",
        source_kind="minutes",
        evidence="Adopted on first reading by a voice vote.",
        tally={"yes": 7, "no": 0},
    )
    loose_item = insert_agenda_item(
        conn,
        meeting_id=meeting,
        number="12",
        title="Council comments",
        alignment_method="none",
        alignment_reason="The recording ends before this item.",
    )
    conn.commit()
    return Seeded(
        county=county,
        city=city,
        body=body,
        meeting=meeting,
        quiet_meeting=quiet_meeting,
        silent_meeting=silent_meeting,
        video=video,
        record=record,
        document_sha256=document.sha256,
        aligned_item=aligned_item,
        loose_item=loose_item,
        token=create_token(conn, "reader", SCOPE_READ),
    )


@pytest.fixture
def api(db_path: Path, api_storage_root: Path) -> Iterator[tuple[TestClient, Seeded]]:
    """A running application over a database that holds one filled meeting."""
    conn = connect(db_path)
    migrate(conn)
    try:
        seeded = _seed(conn, api_storage_root)
    finally:
        conn.close()
    app = create_app(Settings(db_path=db_path, version=TEST_VERSION, storage_root=api_storage_root))
    with TestClient(app) as client:
        yield client, seeded


@pytest.fixture
def headers(api: tuple[TestClient, Seeded]) -> dict[str, str]:
    """The Authorization header of a read token."""
    return {"Authorization": f"Bearer {api[1].token}"}


def _routes_of(holder: object) -> Iterator[APIRoute]:
    """Yield every route an application serves, however it was mounted.

    An included router is not flattened into ``app.routes`` by this version of
    FastAPI: it stays one wrapper holding the routes it was given. The walk
    therefore goes one level down into whatever the wrapper offers, so a route
    inside a router is as visible here as one declared on the application.
    """
    routes = getattr(holder, "routes", None)
    if routes is None:
        original = getattr(holder, "original_router", None)
        routes = getattr(original, "routes", None) if original is not None else None
    for route in list(routes or ()):
        if isinstance(route, APIRoute):
            yield route
        else:
            yield from _routes_of(route)


def route_paths(client: TestClient) -> list[str]:
    """Every path the application serves, with its parameters filled in.

    Read from the application rather than listed here, so the token check
    below covers a route that is added later without anyone remembering to
    add it.
    """
    paths = []
    for route in _routes_of(client.app):
        path = route.path
        while "{" in path:
            before, _, after = path.partition("{")
            _, _, after = after.partition("}")
            path = f"{before}1{after}"
        paths.append(path)
    return sorted(set(paths))


#: The read routes of spec 13.2. The list is not what the token check runs on;
#: it is the guard that says the check ran on something.
EXPECTED_PATHS = {
    "/v1/area",
    "/v1/bodies/1/meetings",
    "/v1/health",
    "/v1/meetings/1",
    "/v1/meetings/1/items",
    "/v1/meetings/1/transcript",
    "/v1/records/1",
    "/v1/records/1/file",
    "/v1/records/1/pages/1",
    "/v1/search",
    "/docs",
    "/redoc",
    "/openapi.json",
}


def test_the_route_list_read_from_the_app_holds_every_read_route(client: TestClient) -> None:
    assert set(route_paths(client)) >= EXPECTED_PATHS


@pytest.mark.parametrize("path", sorted(EXPECTED_PATHS))
def test_every_route_is_401_without_a_token(client: TestClient, path: str) -> None:
    response = client.get(path)

    assert response.status_code == 401, f"{path} answered {response.status_code}"


@pytest.mark.parametrize("path", sorted(EXPECTED_PATHS))
def test_every_route_is_401_with_a_wrong_token(client: TestClient, path: str) -> None:
    response = client.get(path, headers={"Authorization": "Bearer tr_not-a-real-token"})

    assert response.status_code == 401, f"{path} answered {response.status_code}"


def test_every_route_read_from_the_app_is_401_without_a_token(
    client: TestClient,
) -> None:
    """The same check over the whole list, so a new route cannot be missed."""
    for path in route_paths(client):
        response = client.get(path)
        assert response.status_code == 401, f"{path} answered {response.status_code}"


def test_the_area_holds_the_tree_the_bodies_and_the_sources(
    api: tuple[TestClient, Seeded], headers: dict[str, str]
) -> None:
    client, seeded = api
    body = client.get("/v1/area", headers=headers).json()

    roots = {node["id"]: node for node in body["jurisdictions"]}
    assert set(roots) == {seeded.county}
    assert [child["id"] for child in roots[seeded.county]["children"]] == [seeded.city]
    assert roots[seeded.county]["type"] == "county"
    assert [one["name"] for one in body["bodies"]] == ["City Council"]
    assert {one["type"] for one in body["sources"]} == {"meeting_portal", "video_channel"}


def test_the_meetings_of_a_body_carry_their_documents_and_videos(
    api: tuple[TestClient, Seeded], headers: dict[str, str]
) -> None:
    client, seeded = api
    meetings = client.get(f"/v1/bodies/{seeded.body}/meetings", headers=headers).json()

    assert [one["id"] for one in meetings] == [
        seeded.meeting,
        seeded.quiet_meeting,
        seeded.silent_meeting,
    ]
    first = meetings[0]
    assert first["starts_at"] == "2026-09-08T19:00:00-06:00"
    assert [one["kind"] for one in first["documents"]] == ["minutes"]
    assert first["documents"][0]["pages_read"] == 1
    assert [one["platform_video_id"] for one in first["videos"]] == ["v-0001"]
    assert first["videos"][0]["transcript_id"] is not None
    assert first["videos"][0]["is_primary"] is True


def test_the_meetings_of_a_body_can_be_limited_to_a_range_of_dates(
    api: tuple[TestClient, Seeded], headers: dict[str, str]
) -> None:
    client, seeded = api
    url = f"/v1/bodies/{seeded.body}/meetings"

    october = client.get(url, params={"from": "2026-10-01", "to": "2026-10-31"}, headers=headers)
    assert [one["id"] for one in october.json()] == [seeded.quiet_meeting]

    after = client.get(url, params={"from": "2026-10-01"}, headers=headers)
    assert [one["id"] for one in after.json()] == [seeded.quiet_meeting, seeded.silent_meeting]


def test_an_unknown_body_is_a_404_with_a_plain_message(
    api: tuple[TestClient, Seeded], headers: dict[str, str]
) -> None:
    client, _ = api
    response = client.get("/v1/bodies/9999/meetings", headers=headers)

    assert response.status_code == 404
    assert response.json()["detail"] == "There is no body 9999."


def test_one_meeting_holds_its_items_its_votes_and_its_notes(
    api: tuple[TestClient, Seeded], headers: dict[str, str]
) -> None:
    client, seeded = api
    body = client.get(f"/v1/meetings/{seeded.meeting}", headers=headers).json()

    assert body["meeting"]["id"] == seeded.meeting
    assert body["body"]["name"] == "City Council"
    assert body["jurisdiction"]["name"] == "Longmont"
    assert [item["number"] for item in body["items"]] == ["9A", "12"]
    aligned = body["items"][0]
    assert aligned["start_ms"] == 3_960_000
    assert aligned["end_ms"] == 4_000_000
    assert aligned["alignment_method"] == "html_video_times"
    assert aligned["identifiers"] == {"ordinance": "2026-54"}
    assert [vote["result"] for vote in aligned["votes"]] == ["passed"]
    assert aligned["votes"][0]["tally"] == {"yes": 7, "no": 0}
    # The untimed item says why it has no time, in plain words.
    assert body["notes"] == [
        "Item 12 has no time in the recording: The recording ends before this item."
    ]


def test_a_meeting_with_nothing_yet_says_so_in_plain_sentences(
    api: tuple[TestClient, Seeded], headers: dict[str, str]
) -> None:
    client, seeded = api
    body = client.get(f"/v1/meetings/{seeded.quiet_meeting}", headers=headers).json()

    assert body["items"] == []
    assert body["notes"] == [
        "No documents are stored for this meeting yet.",
        "No recording is listed for this meeting yet.",
        "No agenda items are stored for this meeting yet.",
    ]


def test_a_meeting_with_a_recording_but_no_transcript_says_that(
    api: tuple[TestClient, Seeded], headers: dict[str, str]
) -> None:
    client, seeded = api
    body = client.get(f"/v1/meetings/{seeded.silent_meeting}", headers=headers).json()

    assert "The recording of this meeting has no transcript yet." in body["notes"]
    response = client.get(f"/v1/meetings/{seeded.silent_meeting}/transcript", headers=headers)
    assert response.status_code == 404
    assert response.json()["detail"] == (
        f"The recording of meeting {seeded.silent_meeting} has no transcript yet."
    )


def test_an_unknown_meeting_is_a_404_with_a_plain_message(
    api: tuple[TestClient, Seeded], headers: dict[str, str]
) -> None:
    client, _ = api
    for path in ("/v1/meetings/9999", "/v1/meetings/9999/items", "/v1/meetings/9999/transcript"):
        response = client.get(path, headers=headers)
        assert response.status_code == 404, path
        assert response.json()["detail"] == "There is no meeting 9999."


def test_the_items_of_a_meeting_come_in_time_order_untimed_last(
    api: tuple[TestClient, Seeded], headers: dict[str, str]
) -> None:
    client, seeded = api
    items = client.get(f"/v1/meetings/{seeded.meeting}/items", headers=headers).json()

    assert [item["id"] for item in items] == [seeded.aligned_item, seeded.loose_item]
    assert items[1]["alignment_method"] == "none"
    assert items[1]["alignment_reason"] == "The recording ends before this item."


def test_the_transcript_holds_the_times_the_speakers_and_the_item(
    api: tuple[TestClient, Seeded], headers: dict[str, str]
) -> None:
    client, seeded = api
    body = client.get(f"/v1/meetings/{seeded.meeting}/transcript", headers=headers).json()

    assert body["meeting_id"] == seeded.meeting
    assert body["video_id"] == seeded.video
    assert body["platform_video_id"] == "v-0001"
    assert body["origin"] == "publisher_captions"
    assert body["artifact_sha256"]
    assert [segment["text"] for segment in body["segments"]] == [
        "Good evening, everyone.",
        SPEECH,
        URL_LINE,
    ]
    mayor = body["segments"][1]
    assert mayor["start_ms"] == 3_978_000
    assert mayor["end_ms"] == 3_985_000
    assert mayor["speaker_label"] == "Mayor"
    # The item is resolved from the time ranges at read time, not stored.
    assert mayor["agenda_item_id"] == seeded.aligned_item
    assert mayor["agenda_item_number"] == "9A"
    assert body["segments"][0]["agenda_item_id"] is None


def test_the_vtt_transcript_parses_back_to_the_same_times_and_the_same_text(
    api: tuple[TestClient, Seeded], headers: dict[str, str]
) -> None:
    client, seeded = api
    url = f"/v1/meetings/{seeded.meeting}/transcript"
    response = client.get(url, params={"format": "vtt"}, headers=headers)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/vtt")
    parsed = parse_vtt(response.content)

    expected = client.get(url, headers=headers).json()["segments"]
    assert [(one.start_ms, one.text) for one in parsed] == [
        (one["start_ms"], one["text"]) for one in expected
    ]
    # The voice markup came off on the way back in, and the text of the last
    # line is the text that went in: the link and the ampersand are characters
    # in a line of speech, not markup.
    assert parsed[-1].text == URL_LINE


def test_the_text_transcript_holds_the_times_and_the_speakers(
    api: tuple[TestClient, Seeded], headers: dict[str, str]
) -> None:
    client, seeded = api
    response = client.get(
        f"/v1/meetings/{seeded.meeting}/transcript",
        params={"format": "txt"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    lines = response.text.splitlines()
    assert lines[1] == f"[01:06:18] Mayor: {SPEECH}"
    assert lines[0] == "[00:00:00] Good evening, everyone."


def test_an_unknown_transcript_format_is_refused(
    api: tuple[TestClient, Seeded], headers: dict[str, str]
) -> None:
    client, seeded = api
    response = client.get(
        f"/v1/meetings/{seeded.meeting}/transcript",
        params={"format": "srt"},
        headers=headers,
    )

    assert response.status_code == 400
    assert "json, vtt or txt" in response.json()["detail"]


def test_one_record_is_read_from_its_row(
    api: tuple[TestClient, Seeded], headers: dict[str, str]
) -> None:
    client, seeded = api
    body = client.get(f"/v1/records/{seeded.record}", headers=headers).json()

    assert body["meeting_id"] == seeded.meeting
    assert body["kind"] == "minutes"
    assert body["title"] == "Minutes of September 8, 2026"
    assert body["artifact_sha256"] == seeded.document_sha256
    assert body["media_type"] == "application/pdf"
    assert body["page_count"] == 1
    assert body["pages_read"] == 1


def test_the_file_of_a_record_hashes_to_the_hash_in_its_name(
    api: tuple[TestClient, Seeded], headers: dict[str, str]
) -> None:
    client, seeded = api
    response = client.get(f"/v1/records/{seeded.record}/file", headers=headers)

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content == MINUTES
    assert hashlib.sha256(response.content).hexdigest() == seeded.document_sha256


def test_one_page_of_a_record_comes_back_with_its_footer_number(
    api: tuple[TestClient, Seeded], headers: dict[str, str]
) -> None:
    client, seeded = api
    body = client.get(f"/v1/records/{seeded.record}/pages/1", headers=headers).json()

    assert body["page_number"] == 1
    assert body["footer_page_number"] == 3
    assert "Ordinance 2026-54" in body["text"]


def test_an_unknown_record_and_page_are_404_with_a_plain_message(
    api: tuple[TestClient, Seeded], headers: dict[str, str]
) -> None:
    client, seeded = api

    assert client.get("/v1/records/9999", headers=headers).json()["detail"] == (
        "There is no record 9999."
    )
    assert client.get("/v1/records/9999/file", headers=headers).status_code == 404
    assert client.get("/v1/records/9999/pages/1", headers=headers).json()["detail"] == (
        "Record 9999 has no page 1."
    )
    assert (
        client.get(f"/v1/records/{seeded.record}/pages/99", headers=headers).json()["detail"]
        == f"Record {seeded.record} has no page 99."
    )


def test_the_search_finds_a_spoken_line_and_a_page_of_a_document(
    api: tuple[TestClient, Seeded], headers: dict[str, str]
) -> None:
    client, seeded = api
    body = client.get("/v1/search", params={"q": "ordinance 2026-54"}, headers=headers).json()

    assert body["query"] == "ordinance 2026-54"
    assert body["count"] == len(body["hits"]) == 2
    spoken, page = body["hits"]
    assert spoken["scope"] == "segment"
    assert spoken["start_ms"] == 3_978_000
    assert spoken["excerpt"] == SPEECH
    assert spoken["speaker_label"] == "Mayor"
    assert spoken["level"] == "city"
    assert spoken["body_name"] == "City Council"
    assert spoken["citation"]["kind"] == "video"
    assert spoken["citation"]["platform_video_id"] == "v-0001"
    assert spoken["citation"]["transcript_artifact_sha256"]
    assert page["scope"] == "record_page"
    assert page["page_number"] == 1
    assert page["citation"]["kind"] == "record"
    assert page["citation"]["document_artifact_sha256"] == seeded.document_sha256


def test_the_search_filters_by_level_body_date_and_kind(
    api: tuple[TestClient, Seeded], headers: dict[str, str]
) -> None:
    client, seeded = api
    url = "/v1/search"

    def count(**params: object) -> int:
        response = client.get(url, params={"q": "ordinance", **params}, headers=headers)
        assert response.status_code == 200, response.text
        return response.json()["count"]

    assert count() == 2
    assert count(level="city") == 2
    assert count(level="county") == 0
    assert count(body=seeded.body) == 2
    assert count(body=9999) == 0
    assert count(**{"from": "2026-09-08", "to": "2026-09-08"}) == 2
    assert count(**{"from": "2026-09-09"}) == 0
    assert count(type="minutes") == 1
    assert count(type="packet") == 0
    assert count(limit=1) == 1


def test_a_hostile_search_query_is_refused_by_neither_the_syntax_nor_the_route(
    api: tuple[TestClient, Seeded], headers: dict[str, str]
) -> None:
    client, _ = api

    for q in ('ordinance "2026-54', "AND OR", '"', "***", "text) OR (text", ""):
        response = client.get("/v1/search", params={"q": q}, headers=headers)
        assert response.status_code == 200, f"{q!r} answered {response.status_code}"
        assert isinstance(response.json()["hits"], list)


def test_the_openapi_description_documents_the_read_routes(
    api: tuple[TestClient, Seeded], headers: dict[str, str]
) -> None:
    client, _ = api
    schema = client.get("/openapi.json", headers=headers).json()

    for path in (
        "/v1/area",
        "/v1/bodies/{body_id}/meetings",
        "/v1/meetings/{meeting_id}",
        "/v1/meetings/{meeting_id}/transcript",
        "/v1/meetings/{meeting_id}/items",
        "/v1/records/{record_id}",
        "/v1/records/{record_id}/file",
        "/v1/records/{record_id}/pages/{page_number}",
        "/v1/search",
    ):
        assert path in schema["paths"], path
    components = schema["components"]["schemas"]
    for model in (
        "AreaOut",
        "MeetingDetail",
        "MeetingSummary",
        "TranscriptOut",
        "RecordOut",
        "RecordPageOut",
        "SearchResults",
        "SearchHitOut",
        "SearchCitationOut",
    ):
        assert model in components, model
    assert components["SearchHitOut"]["properties"]["citation"]["$ref"].endswith(
        "/SearchCitationOut"
    )
