from __future__ import annotations

import datetime as dt
import json

import pytest

from trading_agent.ls_nws import (
    LsNwsParseError,
    LsNwsRawFrame,
    LsNwsWireKind,
    parse_ls_nws_frame,
)

COLLECTION_DATE = dt.date(2026, 7, 15)
RECEIVED_AT = dt.datetime(
    2026,
    7,
    15,
    9,
    1,
    1,
    tzinfo=dt.timezone(dt.timedelta(hours=9)),
)
REALKEY = "202607150901000100000001"
PRIVATE_TITLE = "Synthetic semiconductor contract headline"


@pytest.mark.parametrize("wire_kind", tuple(LsNwsWireKind))
def test_parser_accepts_official_nws_shape_and_builds_flat_canonical_payload(
    wire_kind: LsNwsWireKind,
) -> None:
    frame = _frame(_document(), wire_kind=wire_kind)

    parsed = parse_ls_nws_frame(frame, collection_date=COLLECTION_DATE)

    assert parsed.realkey == REALKEY
    assert parsed.source_record_id == f"ls-nws://news/{REALKEY}"
    assert parsed.published_at == dt.datetime(
        2026,
        7,
        15,
        9,
        1,
        tzinfo=dt.timezone(dt.timedelta(hours=9)),
    )
    assert json.loads(parsed.canonical_payload) == {
        "bodysize": "4200",
        "code": "",
        "date": "20260715",
        "id": "23",
        "realkey": REALKEY,
        "time": "090100",
        "title": PRIVATE_TITLE,
        "tr_cd": "NWS",
        "tr_key": "NWS001",
    }
    assert PRIVATE_TITLE not in repr(frame)
    assert PRIVATE_TITLE not in repr(parsed)


def test_parser_rejects_duplicate_json_key_without_rendering_payload() -> None:
    payload = (
        '{"header":{"tr_cd":"NWS","tr_key":"NWS001"},'
        '"body":{"date":"20260715","code":"","realkey":"'
        + REALKEY
        + '","bodysize":"4200","time":"090100","id":"23",'
        '"title":"first","title":"private duplicate title"}}'
    ).encode()

    with pytest.raises(LsNwsParseError) as captured:
        _ = parse_ls_nws_frame(
            LsNwsRawFrame(1, RECEIVED_AT, LsNwsWireKind.TEXT, payload),
            collection_date=COLLECTION_DATE,
        )

    assert captured.value.failure_code == "duplicate_json_key"
    assert "private duplicate title" not in str(captured.value)


@pytest.mark.parametrize(
    ("payload", "failure_code"),
    (
        (b"\xff", "invalid_utf8"),
        (b"{not-json", "invalid_json"),
        (b"[]", "invalid_packet"),
        (b"{}", "invalid_packet"),
    ),
)
def test_parser_rejects_malformed_packet_safely(
    payload: bytes,
    failure_code: str,
) -> None:
    with pytest.raises(LsNwsParseError) as captured:
        _ = parse_ls_nws_frame(
            LsNwsRawFrame(1, RECEIVED_AT, LsNwsWireKind.BINARY, payload),
            collection_date=COLLECTION_DATE,
        )

    assert captured.value.failure_code == failure_code


@pytest.mark.parametrize(
    ("mutate", "failure_code"),
    (
        (("header", "tr_cd", "SC0"), "invalid_packet"),
        (("header", "tr_key", "OTHER"), "invalid_packet"),
        (("header", "extra", "value"), "invalid_packet"),
        (("body", "extra", "value"), "invalid_packet"),
        (("body", "date", "20260230"), "invalid_packet"),
        (("body", "date", "2026-07-15"), "invalid_packet"),
        (("body", "time", "250000"), "invalid_packet"),
        (("body", "time", "0901"), "invalid_packet"),
        (("body", "realkey", "1" * 23), "invalid_packet"),
        (("body", "realkey", "x" * 24), "invalid_packet"),
        (("body", "bodysize", "-1"), "invalid_packet"),
        (("body", "bodysize", "1" * 11), "invalid_packet"),
        (("body", "id", ""), "invalid_packet"),
        (("body", "id", "news"), "invalid_packet"),
        (("body", "code", "contains space"), "invalid_packet"),
        (("body", "code", "한"), "invalid_packet"),
        (("body", "title", ""), "invalid_packet"),
        (("body", "title", " padded "), "invalid_packet"),
        (("body", "title", "private\ncontrol"), "invalid_packet"),
        (("body", "title", "x" * 2001), "invalid_packet"),
    ),
)
def test_parser_strictly_rejects_invalid_or_extra_fields(
    mutate: tuple[str, str, str],
    failure_code: str,
) -> None:
    document = _document()
    section, name, value = mutate
    nested = document[section]
    assert isinstance(nested, dict)
    nested[name] = value

    with pytest.raises(LsNwsParseError) as captured:
        _ = parse_ls_nws_frame(
            _frame(document),
            collection_date=COLLECTION_DATE,
        )

    assert captured.value.failure_code == failure_code
    assert PRIVATE_TITLE not in str(captured.value)


def test_parser_rejects_missing_official_field() -> None:
    document = _document()
    body = document["body"]
    assert isinstance(body, dict)
    del body["id"]

    with pytest.raises(LsNwsParseError, match="invalid_packet"):
        _ = parse_ls_nws_frame(_frame(document), collection_date=COLLECTION_DATE)


def test_parser_rejects_escaped_lone_surrogate_as_parse_error() -> None:
    document = _document()
    body = document["body"]
    assert isinstance(body, dict)
    body["title"] = "\ud800"
    payload = json.dumps(document, ensure_ascii=True).encode("ascii")

    with pytest.raises(LsNwsParseError) as captured:
        _ = parse_ls_nws_frame(
            LsNwsRawFrame(1, RECEIVED_AT, LsNwsWireKind.TEXT, payload),
            collection_date=COLLECTION_DATE,
        )

    assert captured.value.failure_code == "invalid_packet"


def test_parser_rejects_packet_for_different_collection_date() -> None:
    document = _document()
    body = document["body"]
    assert isinstance(body, dict)
    body["date"] = "20260714"

    with pytest.raises(LsNwsParseError) as captured:
        _ = parse_ls_nws_frame(_frame(document), collection_date=COLLECTION_DATE)

    assert captured.value.failure_code == "collection_date_mismatch"


def test_parser_rejects_publication_after_receipt() -> None:
    document = _document()
    body = document["body"]
    assert isinstance(body, dict)
    body["time"] = "090102"

    with pytest.raises(LsNwsParseError) as captured:
        _ = parse_ls_nws_frame(_frame(document), collection_date=COLLECTION_DATE)

    assert captured.value.failure_code == "future_publication"


@pytest.mark.parametrize(
    ("sequence", "received_at", "payload"),
    (
        (0, RECEIVED_AT, b"{}"),
        (1_000_000, RECEIVED_AT, b"{}"),
        (1, dt.datetime(2026, 7, 15), b"{}"),
        (1, RECEIVED_AT, b""),
        (1, RECEIVED_AT, b"x" * 262_145),
    ),
)
def test_raw_frame_rejects_invalid_bounds(
    sequence: int,
    received_at: dt.datetime,
    payload: bytes,
) -> None:
    with pytest.raises(ValueError, match="invalid LS NWS raw frame"):
        _ = LsNwsRawFrame(
            sequence,
            received_at,
            LsNwsWireKind.TEXT,
            payload,
        )


def _frame(
    document: dict[str, object],
    *,
    wire_kind: LsNwsWireKind = LsNwsWireKind.TEXT,
) -> LsNwsRawFrame:
    return LsNwsRawFrame(
        1,
        RECEIVED_AT,
        wire_kind,
        json.dumps(document, ensure_ascii=False).encode(),
    )


def _document() -> dict[str, object]:
    return {
        "header": {"tr_cd": "NWS", "tr_key": "NWS001"},
        "body": {
            "date": "20260715",
            "code": "",
            "realkey": REALKEY,
            "bodysize": "4200",
            "time": "090100",
            "id": "23",
            "title": PRIVATE_TITLE,
        },
    }
