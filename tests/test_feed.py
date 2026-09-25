import copy
import json
from datetime import UTC, datetime
from http.client import HTTPException

import pytest

from workshop_marketing_agent import FeedResponse, load_public_workshop, validate_workshop
from workshop_marketing_agent import feed


NOW = datetime(2026, 10, 24, 10, tzinfo=UTC)
ENDPOINT = "https://example.org/public-workshops?format=workshop-data.v1&id=synthetic-workshop-01"
WORKSHOP_ID = "synthetic-workshop-01"


@pytest.fixture
def envelope(complete_input):
    complete_input["provenance"].update(active=True, event_status="scheduled")
    return {
        "schema_version": "workshop-data.v1",
        "generated_at": "2026-10-24T09:59:59Z",
        "workshop": complete_input,
    }


class FakeHTTP:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, endpoint, timeout):
        self.calls.append((endpoint, timeout))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def import_response(payload, *, now=NOW):
    http_get = FakeHTTP(FeedResponse(200, json.dumps(payload, ensure_ascii=False).encode()))
    return load_public_workshop(
        endpoint=ENDPOINT, workshop_id=WORKSHOP_ID, timeout=5, now=now, http_get=http_get,
    )


def test_valid_response_passes_original_payload_and_now_to_validator(envelope, monkeypatch):
    calls = []
    original = copy.deepcopy(envelope)
    envelope["workshop"]["content"]["public_description_html"] = "  <p>Öl &amp; Farbe</p>\n\nUnverändert.  "
    envelope["workshop"]["pricing"]["regular_price"] = "120.00000000000000000001"
    expected = validate_workshop(envelope["workshop"], now=NOW)

    def observed_validator(payload, *, now):
        calls.append((payload, now))
        return validate_workshop(payload, now=now)

    monkeypatch.setattr(feed, "validate_workshop", observed_validator)
    http_get = FakeHTTP(FeedResponse(200, json.dumps(envelope, ensure_ascii=False).encode()))
    result = load_public_workshop(
        endpoint=ENDPOINT, workshop_id=WORKSHOP_ID, timeout=2.5, now=NOW, http_get=http_get,
    )
    assert http_get.calls == [(ENDPOINT, 2.5)]
    assert calls == [(envelope["workshop"], NOW)]
    assert result.validation == expected
    assert result.promotion_eligible
    assert result.diagnostics == ()
    assert result.generated_at == datetime(2026, 10, 24, 9, 59, 59, tzinfo=UTC)
    workshop = result.validation.workshop
    assert workshop.content.public_description_html == envelope["workshop"]["content"]["public_description_html"]
    assert json.loads(workshop.model_dump_json())["pricing"]["regular_price"] == "120.00000000000000000001"
    assert workshop.source_version == original["workshop"]["source_version"]
    assert workshop.provenance == original["workshop"]["provenance"]


@pytest.mark.parametrize("identity", [None, {}, [], {"workshop_id": "other"}, {"workshop_id": 1}])
def test_identity_must_match_the_request(envelope, identity):
    envelope["workshop"]["identity"] = identity
    result = import_response(envelope)
    assert result.validation is None
    assert not result.promotion_eligible
    assert result.diagnostics[0].field == "identity.workshop_id"
    assert result.diagnostics[0].kind == "conflicting"


@pytest.mark.parametrize("version", ["workshop-data.v2", "", None, 1])
def test_unsupported_schema(envelope, version):
    envelope["schema_version"] = version
    result = import_response(envelope)
    assert result.validation is None and not result.promotion_eligible
    assert result.diagnostics[0].field == "envelope.schema_version"
    assert "Unsupported schema" in result.diagnostics[0].message


@pytest.mark.parametrize("field", ["workshop", "schema_version", "generated_at"])
def test_missing_required_envelope_fields(envelope, field):
    del envelope[field]
    result = import_response(envelope)
    assert result.validation is None and not result.promotion_eligible
    assert result.diagnostics[0].field == "envelope." + field
    assert result.diagnostics[0].kind == "missing"


@pytest.mark.parametrize("value", [None, [], "missing", 0])
def test_non_object_workshop_is_not_marketing_input(envelope, value):
    envelope["workshop"] = value
    result = import_response(envelope)
    assert result.validation is None and not result.promotion_eligible
    assert result.diagnostics[0].field == "envelope.workshop"


@pytest.mark.parametrize("timestamp", [None, 123, "2026-10-24T10:00:00", "invalid", "2026-10-24T10:00:00+00:60"])
def test_generated_at_must_be_an_aware_instant(envelope, timestamp):
    envelope["generated_at"] = timestamp
    result = import_response(envelope)
    assert result.validation is None and not result.promotion_eligible
    assert result.diagnostics[0].field == "envelope.generated_at"


@pytest.mark.parametrize("payload", [[], None, "text", True])
def test_non_object_envelope(payload):
    result = import_response(payload)
    assert result.validation is None and not result.promotion_eligible
    assert result.diagnostics[0].field == "envelope"


def test_unsupported_fields_are_not_discarded(envelope):
    envelope["future_field"] = "unknown"
    result = import_response(envelope)
    assert result.validation is None
    assert result.diagnostics[0].field == "envelope.future_field"
    del envelope["future_field"]
    envelope["workshop"]["future_field"] = "unknown"
    result = import_response(envelope)
    assert not result.validation.usable and not result.promotion_eligible
    assert result.validation == validate_workshop(envelope["workshop"], now=NOW)
    assert result.diagnostics[0].field == "future_field"


@pytest.mark.parametrize("response,field", [
    (FeedResponse(404, b"not found"), "http.status"),
    (FeedResponse(503, b"internal error"), "http.status"),
    (FeedResponse(302, b"redirect"), "http.status"),
    (TimeoutError("private details"), "http.timeout"),
    (OSError("private details"), "http.request"),
    (HTTPException("private details"), "http.request"),
    (FeedResponse(200, b"<html>error</html>"), "envelope"),
    (FeedResponse(200, b'\xff'), "envelope"),
    (FeedResponse(200, b""), "envelope"),
    (FeedResponse(200, b'{"x": NaN}'), "envelope"),
    (FeedResponse(200, b'{"x": 1, "x": 2}'), "envelope"),
    (FeedResponse(200, b" " * (feed.MAX_RESPONSE_BYTES + 1)), "http.body"),
])
def test_transport_and_json_failures(response, field):
    http_get = FakeHTTP(response)
    result = load_public_workshop(
        endpoint=ENDPOINT, workshop_id=WORKSHOP_ID, timeout=5, now=NOW, http_get=http_get,
    )
    assert result.validation is None and not result.promotion_eligible
    assert result.diagnostics[0].field == field
    assert "private details" not in result.diagnostics[0].message
    assert len(http_get.calls) == 1


@pytest.mark.parametrize("number", [
    "1e9999", "-1e9999", "1e-9999", "-1e-9999",
    "1e-9999999999999999999999999999999999999999",
])
def test_json_number_range_errors_cannot_change_evidence(envelope, number):
    envelope["workshop"]["provenance"]["opaque_number"] = "replace_number"
    body = json.dumps(envelope).replace('"replace_number"', number).encode()
    result = load_public_workshop(
        endpoint=ENDPOINT, workshop_id=WORKSHOP_ID, timeout=5, now=NOW,
        http_get=FakeHTTP(FeedResponse(200, body)),
    )
    assert result.validation is None and not result.promotion_eligible
    assert result.diagnostics[0].field == "envelope"
    assert "numeric range" in result.diagnostics[0].message


def test_zero_with_extreme_exponent_remains_zero(envelope):
    envelope["workshop"]["provenance"]["opaque_number"] = "replace_number"
    body = json.dumps(envelope).replace('"replace_number"', "0e9999999999999999999999999999999999999999").encode()
    result = load_public_workshop(
        endpoint=ENDPOINT, workshop_id=WORKSHOP_ID, timeout=5, now=NOW,
        http_get=FakeHTTP(FeedResponse(200, body)),
    )
    assert result.promotion_eligible
    assert result.validation.workshop.provenance["opaque_number"] == 0.0
    assert result.diagnostics == ()


def test_binary_float_money_is_not_converted_to_accepted_decimal(envelope):
    envelope["workshop"]["pricing"]["regular_price"] = 120.5
    result = import_response(envelope)
    assert not result.promotion_eligible
    assert result.validation == validate_workshop(envelope["workshop"], now=NOW)
    assert any(d.field == "pricing.regular_price" and d.kind == "invalid" for d in result.diagnostics)


def test_observation_after_explicit_now_stays_future_dated(envelope):
    envelope["workshop"]["availability"]["observed_at"] = "2026-10-24T10:00:01Z"
    envelope["generated_at"] = "2026-10-24T10:00:02Z"
    result = import_response(envelope)
    assert result.promotion_eligible
    assert not result.validation.claims.remaining_persons
    assert result.validation == validate_workshop(envelope["workshop"], now=NOW)
    assert any(d.field == "availability.observed_at" and d.kind == "invalid" for d in result.diagnostics)


@pytest.mark.parametrize("field,value", [
    ("active", False), ("active", None), ("active", "true"), ("active", 1),
    ("event_status", "cancelled"), ("event_status", "postponed"),
    ("event_status", "unknown"), ("event_status", None),
    ("event_status", "future_status"), ("event_status", ["scheduled", "cancelled"]),
])
def test_event_status_blocks_promotion_but_preserves_core(envelope, field, value):
    envelope["workshop"]["provenance"][field] = value
    result = import_response(envelope)
    assert result.validation.usable
    assert not result.promotion_eligible
    assert result.validation == validate_workshop(envelope["workshop"], now=NOW)
    assert result.import_diagnostics[0].field == "provenance." + field
    assert result.import_diagnostics[0].claim == "promotion"


def test_missing_status_and_supplied_status_conflicts(envelope):
    del envelope["workshop"]["provenance"]["active"]
    result = import_response(envelope)
    assert result.validation.usable and not result.promotion_eligible
    assert result.import_diagnostics[0].kind == "missing"
    envelope["workshop"]["provenance"]["active"] = True
    envelope["workshop"]["conflicts"] = [
        {"field": "provenance.event_status", "message": "Zwei Quellen widersprechen sich."},
    ]
    result = import_response(envelope)
    assert not result.promotion_eligible
    assert result.validation == validate_workshop(envelope["workshop"], now=NOW)
    assert any(d.field == "provenance.event_status" and d.kind == "conflicting" for d in result.diagnostics)


def test_expired_offer_and_stale_availability_keep_core_facts(envelope):
    later = datetime(2026, 10, 26, 10, tzinfo=UTC)
    result = import_response(envelope, now=later)
    assert result.promotion_eligible and result.validation.usable
    assert not result.validation.claims.current_discount
    assert not result.validation.claims.remaining_persons
    assert not result.validation.claims.remaining_early_bird_units
    assert result.validation == validate_workshop(envelope["workshop"], now=later)
    assert result.diagnostics == result.validation.diagnostics
    assert any(d.field == "early_bird.final_date" and d.kind == "limitation" for d in result.diagnostics)
    assert any(d.field == "availability.observed_at" and d.kind == "limitation" for d in result.diagnostics)


@pytest.mark.parametrize("group,field", [
    (None, "source_version"), ("availability", "observed_at"),
    ("availability", "max_age_seconds"), ("availability", "verification"),
])
def test_fetch_never_fabricates_version_observation_freshness_or_verification(envelope, group, field):
    target = envelope["workshop"][group] if group else envelope["workshop"]
    del target[field]
    result = import_response(envelope)
    assert result.validation == validate_workshop(envelope["workshop"], now=NOW)
    assert not result.validation.claims.remaining_persons
    assert any(d.kind == "missing" for d in result.diagnostics)


@pytest.mark.parametrize("unknown,conflict", [(True, False), (False, True), (True, True)])
def test_image_limitations_survive_import(envelope, unknown, conflict):
    if unknown:
        envelope["workshop"]["image"].update(marketing_permission=None, attribution_required=None)
    if conflict:
        envelope["workshop"]["conflicts"] = [
            {"field": "image.reference", "message": "Widersprüchliche Bild-Aliase."},
        ]
    result = import_response(envelope)
    assert result.promotion_eligible and result.validation.usable
    assert not result.validation.claims.image
    assert result.validation == validate_workshop(envelope["workshop"], now=NOW)
    assert result.diagnostics == result.validation.diagnostics
    if unknown:
        assert result.validation.workshop.image.marketing_permission is None
        assert result.validation.workshop.image.attribution_required is None
    if conflict:
        assert result.validation.workshop.conflicts[0].message == "Widersprüchliche Bild-Aliase."


@pytest.mark.parametrize("field,value", [
    ("now", datetime(2026, 10, 24)), ("now", "2026-10-24T10:00:00Z"),
    ("timeout", 0), ("timeout", -1), ("timeout", float("inf")),
    ("timeout", float("nan")), ("timeout", True), ("timeout", "5"),
    ("workshop_id", " "), ("endpoint", "http://example.org/feed"),
    ("endpoint", "https://user:password@example.org/feed"),
    ("endpoint", "https://example.org/feed#fragment"),
])
def test_invalid_configuration_fails_before_http(field, value):
    http_get = FakeHTTP(FeedResponse(200, b"{}"))
    arguments = dict(endpoint=ENDPOINT, workshop_id=WORKSHOP_ID, timeout=5, now=NOW)
    arguments[field] = value
    with pytest.raises(ValueError):
        load_public_workshop(**arguments, http_get=http_get)
    assert http_get.calls == []


@pytest.mark.parametrize("status,failure", [(200, False), (200, True), (302, False)])
def test_standard_library_boundary_uses_get_timeout_size_limit_and_closes(monkeypatch, status, failure):
    calls = []

    class Response:
        def __init__(self):
            self.status = status

        def read(self, limit):
            calls.append(("read", limit))
            if failure:
                raise TimeoutError()
            return b"{}"

    class Connection:
        def __init__(self, host, port, *, timeout):
            calls.append(("connect", host, port, timeout))

        def request(self, method, path, *, headers):
            calls.append(("request", method, path, headers))

        def getresponse(self):
            return Response()

        def close(self):
            calls.append(("close",))

    monkeypatch.setattr(feed, "HTTPSConnection", Connection)
    result = load_public_workshop(endpoint=ENDPOINT, workshop_id=WORKSHOP_ID, timeout=3, now=NOW)
    expected = [
        ("connect", "example.org", None, 3),
        ("request", "GET", "/public-workshops?format=workshop-data.v1&id=synthetic-workshop-01", {"Accept": "application/json"}),
    ]
    if status == 200:
        expected.append(("read", feed.MAX_RESPONSE_BYTES + 1))
    assert calls == expected + [("close",)]
    expected_field = "envelope.schema_version" if status == 200 else "http.status"
    assert result.diagnostics[0].field == ("http.timeout" if failure else expected_field)
