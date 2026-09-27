"""Offline transport contract: no Google credentials, API calls or campaign writes."""

from dataclasses import asdict
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from workshop_marketing_agent.google_local_posts import (
    API_ROOT, GoogleLocalPosts, HttpResponse, https_transport,
)
from workshop_marketing_agent.submission import GoogleSubmissionPayload

LOCATION = "accounts/123/locations/456"
POST = LOCATION + "/localPosts/post-789"
SECRET = "private-token"


@pytest.fixture
def payload():
    return GoogleSubmissionPayload.model_validate_json(json.dumps({
        "summary": "Mal-Yoga – Zeit für dich.\nOriginaltext bleibt erhalten.",
        "event": {"title": "Mal-Yoga", "schedule": {
            "startDate": {"year": 2026, "month": 11, "day": 7},
            "endDate": {"year": 2026, "month": 11, "day": 7},
            "startTime": {"hours": 10, "minutes": 0},
            "endTime": {"hours": 12, "minutes": 30},
        }},
        "callToAction": {"url": "https://example.org/book?utm_source=google&x=%C3%A4"},
        "media": [{"sourceUrl": "https://example.org/bild.png?size=large"}],
    }))


class Boundary:
    def __init__(self, data=None, status=200, error=None):
        self.response = HttpResponse(status, json.dumps(data or {"name": POST, "state": "LIVE"}).encode())
        self.error = error
        self.requests = []
        self.tokens = 0

    def token(self):
        self.tokens += 1
        return SECRET

    def http(self, **request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return self.response

    def client(self, **kwargs):
        return GoogleLocalPosts(**dict(location=LOCATION, token_provider=self.token,
                                       transport=self.http, timeout=7.5) | kwargs)


def invoke(client, operation, payload):
    if operation == "create":
        return client.create_event(payload)
    if operation == "update":
        return client.update_event(POST, payload)
    return client.get_post(POST)


@pytest.mark.parametrize("operation,method", [("create", "POST"), ("update", "PATCH"), ("get", "GET")])
def test_exact_request(operation, method, payload):
    fake = Boundary()
    result = invoke(fake.client(), operation, payload)
    assert result.outcome == "visible"
    assert result.resource_name == POST
    assert len(fake.requests) == fake.tokens == 1
    request = fake.requests[0]
    assert request["method"] == method
    assert request["timeout"] == 7.5
    assert request["headers"] == {"Authorization": "Bearer " + SECRET, "Content-Type": "application/json"}
    expected = API_ROOT + "/v4/" + (LOCATION + "/localPosts" if operation == "create" else POST)
    assert request["url"].split("?")[0] == expected
    if operation == "get":
        assert request["body"] is None
    else:
        assert json.loads(request["body"]) == payload.provider_payload()
    if operation == "update":
        assert parse_qs(urlsplit(request["url"]).query) == {
            "updateMask": ["callToAction,event,languageCode,media,summary,topicType"]}
    else:
        assert "?" not in request["url"]


@pytest.mark.parametrize("media", [None, ()])
def test_absent_media_policy(media, payload):
    fake = Boundary()
    text_only = payload.model_copy(update={"media": media})
    assert fake.client().update_event(POST, text_only).outcome == "invalid_input"
    assert fake.tokens == 0 and fake.requests == []
    assert fake.client().create_event(text_only).outcome == "visible"
    body = json.loads(fake.requests[0]["body"])
    assert body.get("media") == (None if media is None else [])


@pytest.mark.parametrize("state,outcome", [
    ("LIVE", "visible"), ("RECURRING", "visible"), ("PROCESSING", "pending"),
    ("SCHEDULED", "pending"), ("REJECTED", "rejected"),
    (None, "unresolved"), ("LOCAL_POST_STATE_UNSPECIFIED", "unresolved"),
    ("FUTURE_STATE", "unresolved"), ({"private": SECRET}, "unresolved"),
])
def test_states_and_allowlist(state, outcome, payload):
    fake = Boundary({"name": POST, "state": state, "searchUrl": "https://www.google.com/search?q=post",
                     "summary": SECRET, "error": {"details": SECRET}})
    result = fake.client().create_event(payload)
    assert result.outcome == outcome
    assert result.state == (state if outcome != "unresolved" else None)
    assert result.search_url == "https://www.google.com/search?q=post"
    assert set(asdict(result)) == {"operation", "outcome", "resource_name", "state", "search_url", "observed_payload"}
    assert SECRET not in repr(result)


def test_missing_state(payload):
    assert Boundary({"name": POST}).client().create_event(payload).outcome == "unresolved"


@pytest.mark.parametrize("location", ["https://example.org", LOCATION + "/", LOCATION + "?x=1",
    LOCATION + "#fragment", "accounts/../locations/456", "accounts/123/locations/%2e%2e",
    "accounts/123/locations/456/extra", "accounts//locations/456", None])
def test_invalid_location_before_token(location, payload):
    fake = Boundary()
    assert fake.client(location=location).create_event(payload).outcome == "invalid_input"
    assert fake.tokens == 0 and not fake.requests


@pytest.mark.parametrize("name", [POST + "/extra", POST + "?q=1", POST + "#x", POST + "/..",
    LOCATION + "/localPosts/%2f", "accounts/999/locations/456/localPosts/x", API_ROOT + "/v4/" + POST, None])
@pytest.mark.parametrize("operation", ["get", "update"])
def test_invalid_post_before_token(name, operation, payload):
    fake = Boundary()
    result = fake.client().get_post(name) if operation == "get" else fake.client().update_event(name, payload)
    assert result.outcome == "invalid_input"
    assert fake.tokens == 0 and not fake.requests


@pytest.mark.parametrize("timeout", [0, -1, 61, float("inf"), float("nan"), True, "3"])
def test_invalid_timeout(timeout, payload):
    fake = Boundary()
    assert fake.client(timeout=timeout).create_event(payload).outcome == "invalid_input"
    assert fake.tokens == 0


@pytest.mark.parametrize("kind", ["dict", "unchecked", "nested"])
def test_requires_validated_payload(kind, payload):
    bad = {"dict": payload.provider_payload(),
           "unchecked": payload.model_copy(update={"summary": 123}),
           "nested": payload.model_copy(update={"event": {"title": 123}})}[kind]
    fake = Boundary()
    assert fake.client().create_event(bad).outcome == "invalid_input"
    assert fake.tokens == 0


@pytest.mark.parametrize("operation", ["create", "update", "get"])
@pytest.mark.parametrize("status", [301, 302, 307, 400, 401, 403, 404, 422, 429, 500, 502, 503])
def test_http_errors_never_retry_or_expose_details(operation, status, payload):
    fake = Boundary({"error": SECRET}, status=status)
    result = invoke(fake.client(), operation, payload)
    expected = ("authentication_or_permission" if status in (401, 403)
                else "provider_rejection" if status in (400, 404, 422)
                else "unavailable" if operation == "get" else "ambiguous_write")
    assert result.outcome == expected
    assert len(fake.requests) == 1
    assert result.resource_name is None
    assert SECRET not in repr(result)


@pytest.mark.parametrize("operation", ["create", "update", "get"])
@pytest.mark.parametrize("error", [TimeoutError(SECRET), ConnectionError(SECRET), RuntimeError(SECRET)])
def test_transport_errors_sanitized(operation, error, payload):
    fake = Boundary(error=error)
    result = invoke(fake.client(), operation, payload)
    assert result.outcome == ("unavailable" if operation == "get" else "ambiguous_write")
    assert len(fake.requests) == 1
    assert SECRET not in repr(result)


@pytest.mark.parametrize("token", [None, "", "secret\r\nHeader: injected", "private token"])
def test_invalid_tokens_never_dispatch(token, payload):
    fake = Boundary()
    assert fake.client(token_provider=lambda: token).create_event(payload).outcome == "authentication_or_permission"
    assert not fake.requests


def test_token_exception_sanitized(payload):
    def fail():
        raise RuntimeError(SECRET)
    fake = Boundary()
    result = fake.client(token_provider=fail).create_event(payload)
    assert result.outcome == "authentication_or_permission"
    assert SECRET not in repr(result) and not fake.requests


@pytest.mark.parametrize("operation", ["create", "update", "get"])
@pytest.mark.parametrize("body", [b"not json private-token", b"[]", b"null", b"{}", b"\xff",
    json.dumps({"name": "accounts/other/locations/456/localPosts/x", "state": "LIVE"}).encode(),
    json.dumps({"name": POST + "/extra", "state": "LIVE"}).encode(),
])
def test_malformed_success_is_not_publication(operation, body, payload):
    fake = Boundary()
    fake.response = HttpResponse(200, body)
    result = invoke(fake.client(), operation, payload)
    assert result.outcome == ("unavailable" if operation == "get" else "ambiguous_write")
    assert SECRET not in repr(result) and len(fake.requests) == 1


@pytest.mark.parametrize("url", ["http://example.org/p", "https://user:pass@example.org/p",
    "https://example.org/p#secret", "https://example.org/white space", "not a url", 123,
    "https://127.0.0.1/p", "https://127.1/p", "https://10.0.0.1/p", "https://service.internal/p"])
def test_invalid_search_url(url, payload):
    fake = Boundary({"name": POST, "state": "LIVE", "searchUrl": url})
    assert fake.client().create_event(payload).outcome == "ambiguous_write"


@pytest.mark.parametrize("operation", ["update", "get"])
def test_response_must_match_exact_post(operation, payload):
    fake = Boundary({"name": LOCATION + "/localPosts/other", "state": "LIVE"})
    assert invoke(fake.client(), operation, payload).outcome == ("ambiguous_write" if operation == "update" else "unavailable")


def test_stdlib_transport_single_request_no_redirect(monkeypatch):
    calls = []
    class Connection:
        def __init__(self, host, timeout):
            calls.append((host, timeout))
        def request(self, *args, **kwargs):
            calls.append((args, kwargs))
        def getresponse(self):
            return self
        status = 302
        def read(self, limit):
            assert limit == 1_048_577
            return b"redirect secret"
        def close(self):
            calls.append("closed")
    monkeypatch.setattr("workshop_marketing_agent.google_local_posts.HTTPSConnection", Connection)
    result = https_transport(method="POST", url=API_ROOT + "/v4/" + LOCATION + "/localPosts",
                             headers={"Authorization": "Bearer " + SECRET}, body=b"{}", timeout=5)
    assert result.status == 302
    assert len(calls) == 3 and calls[-1] == "closed"
    assert SECRET not in repr(result)


@pytest.mark.parametrize("failure", ["request", "oversized"])
def test_stdlib_transport_closes_on_failure(monkeypatch, failure, payload):
    calls = []
    class Connection:
        def __init__(self, host, timeout):
            pass
        def request(self, *args, **kwargs):
            calls.append("request")
            if failure == "request":
                raise TimeoutError(SECRET)
        def getresponse(self):
            return self
        status = 200
        def read(self, limit):
            return b"x" * limit
        def close(self):
            calls.append("closed")
    monkeypatch.setattr("workshop_marketing_agent.google_local_posts.HTTPSConnection", Connection)
    result = Boundary().client(transport=https_transport).create_event(payload)
    assert result.outcome == "ambiguous_write"
    assert calls == ["request", "closed"]
    assert SECRET not in repr(result)


def test_stdlib_transport_rejects_other_origin(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Must not construct a connection")
    monkeypatch.setattr("workshop_marketing_agent.google_local_posts.HTTPSConnection", forbidden)
    with pytest.raises(ValueError, match="Invalid API host"):
        https_transport(method="GET", url="https://example.org/secret", headers={}, body=None, timeout=5)
