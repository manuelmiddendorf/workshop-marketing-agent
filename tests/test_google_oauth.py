"""Only synthetic credential markers; failures never expose credential contents."""

import json
from urllib.parse import parse_qs

import pytest

import google_oauth as oauth
from workshop_marketing_agent.google_local_posts import HttpResponse

CREDENTIALS = {"client_id": "123-synthetic.apps.googleusercontent.com",
               "client_secret": "synthetic-secret+&=", "refresh_token": "synthetic-refresh/+="}
TOKEN = "synthetic-access-token"


class Boundary:
    def __init__(self):
        self.raw = json.dumps(CREDENTIALS)
        self.reads = 0
        self.calls = []
        self.error = None
        self.response = HttpResponse(200, json.dumps({"access_token": TOKEN, "token_type": "Bearer"}).encode())

    def secret(self):
        self.reads += 1
        if isinstance(self.raw, Exception):
            raise self.raw
        return self.raw

    def http(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response

    def provider(self, timeout=10):
        return oauth.RefreshTokenProvider(self.secret, self.http, timeout)


def test_exact_refresh_and_no_cache():
    fake = Boundary()
    provider = fake.provider()
    assert fake.reads == 0 and fake.calls == []
    assert provider() == TOKEN
    assert len(fake.calls) == fake.reads == 1
    request = fake.calls[0]
    assert request == {"method": "POST", "url": "https://oauth2.googleapis.com/token",
        "headers": {"Content-Type": "application/x-www-form-urlencoded"}, "timeout": 10,
        "body": request["body"]}
    assert parse_qs(request["body"].decode(), strict_parsing=True) == {
        key: [value] for key, value in CREDENTIALS.items() | {("grant_type", "refresh_token")}}
    assert provider() == TOKEN and len(fake.calls) == fake.reads == 2
    assert "synthetic" not in repr(provider)
    assert "synthetic" not in repr(fake.response)


@pytest.mark.parametrize("raw", [None, b"bytes", "", "not-json", "[]", "null", "{}",
    '{"client_id":"duplicate","client_id":"second"}',
    json.dumps(CREDENTIALS)[:-1] + ',"refresh_token":"duplicate"}',
    json.dumps(CREDENTIALS | {"extra": "value"}),
    json.dumps({k: v for k, v in CREDENTIALS.items() if k != "refresh_token"}),
    " " * (oauth.MAX_JSON_BYTES + 1), RuntimeError("synthetic-private-failure")])
def test_bad_secret_never_refreshes_or_exposes_error(raw, capsys):
    fake = Boundary()
    fake.raw = raw
    assert fake.provider()() == ""
    assert fake.reads == 1 and not fake.calls
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("field", list(CREDENTIALS))
@pytest.mark.parametrize("value", ["", " ", "bad\nvalue", 123, None, "x" * 4097, "é"])
def test_secret_field_limits(field, value):
    fake = Boundary()
    fake.raw = json.dumps(CREDENTIALS | {field: value})
    assert fake.provider()() == "" and not fake.calls


@pytest.mark.parametrize("client", ["not-google", "apps.googleusercontent.com", "123@evil.invalid",
    "123.apps.googleusercontent.com", "x-synthetic.apps.googleusercontent.com",
    "123-synthetic.apps.googleusercontent.com.evil.invalid", "123-" + "x" * 256 + ".apps.googleusercontent.com"])
def test_client_id_syntax(client):
    fake = Boundary()
    fake.raw = json.dumps(CREDENTIALS | {"client_id": client})
    assert fake.provider()() == "" and not fake.calls


@pytest.mark.parametrize("timeout", [0, -1, 61, True, "10", float("inf"), float("nan")])
def test_timeout_before_secret(timeout):
    fake = Boundary()
    assert fake.provider(timeout)() == ""
    assert fake.reads == 0 and not fake.calls


@pytest.mark.parametrize("status", [0, 201, 204, 301, 302, 307, 400, 401, 403, 429, 500, 503, True])
def test_all_http_errors_no_retry_or_details(status, capsys):
    fake = Boundary()
    fake.response = HttpResponse(status, b'{"error":"synthetic-private-response"}')
    assert fake.provider()() == ""
    assert len(fake.calls) == 1 and fake.reads == 1
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("body", [b"not-json", b"null", b"[]", b"{}", b"\xff", b"x" * 65537,
    b'{"access_token":"first","access_token":"second","token_type":"Bearer"}',
    b'{"access_token":NaN,"token_type":"Bearer"}',
    *[json.dumps(data).encode() for data in [
        {"access_token": "", "token_type": "Bearer"}, {"access_token": TOKEN},
        {"token_type": "Bearer"}, {"access_token": TOKEN, "token_type": "Basic"},
        {"access_token": TOKEN, "token_type": None}, {"access_token": 123, "token_type": "Bearer"},
        {"access_token": "bad\r\nheader", "token_type": "Bearer"},
        {"access_token": "x" * 8193, "token_type": "Bearer"},
        {"access_token": TOKEN, "token_type": "Bearer", "scope": "https://www.googleapis.com/auth/plus.business.manage"},
        {"access_token": TOKEN, "token_type": "Bearer", "scope": None},
    ]]])
def test_invalid_response(body):
    fake = Boundary()
    fake.response = HttpResponse(200, body)
    assert fake.provider()() == "" and len(fake.calls) == 1


def test_response_allowlist_and_fixed_required_scope():
    fake = Boundary()
    fake.response = HttpResponse(200, json.dumps({"access_token": TOKEN, "token_type": "bearer",
        "scope": oauth.REQUIRED_SCOPE, "refresh_token": "ignored-new-token", "private": "ignored"}).encode())
    assert fake.provider()() == TOKEN
    assert fake.raw == json.dumps(CREDENTIALS)


@pytest.mark.parametrize("error", [TimeoutError("synthetic-private"), ConnectionError("synthetic-private"),
                                   RuntimeError("synthetic-private")])
def test_transport_exceptions_do_not_escape(error, capsys):
    fake = Boundary()
    fake.error = error
    assert fake.provider()() == "" and len(fake.calls) == 1
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("case", ["success", "redirect", "timeout", "oversize", "close_error"])
def test_concrete_transport_one_call_closes_and_sanitizes(monkeypatch, case):
    events = []
    class Connection:
        def __init__(self, host, timeout):
            assert host == "oauth2.googleapis.com" and timeout == 10
        def request(self, method, path, **kwargs):
            assert method == "POST" and path == "/token"
            events.append("send")
            if case == "timeout":
                raise TimeoutError("synthetic-private")
        def getresponse(self):
            return self
        status = 302 if case == "redirect" else 200
        def read(self, limit):
            assert limit == oauth.MAX_RESPONSE_BYTES + 1
            return b"x" * limit if case == "oversize" else b"{}"
        def close(self):
            events.append("close")
            if case == "close_error":
                raise RuntimeError("synthetic-private")
    monkeypatch.setattr(oauth, "HTTPSConnection", Connection)
    response = oauth.token_transport(method="POST", url=oauth.TOKEN_URL, headers={}, body=b"synthetic", timeout=10)
    assert response.status == (0 if case in ("timeout", "oversize") else 302 if case == "redirect" else 200)
    assert events == ["send", "close"]
    assert "synthetic" not in repr(response)


@pytest.mark.parametrize("url", ["http://oauth2.googleapis.com/token", oauth.TOKEN_URL + "?x=1",
                                  oauth.TOKEN_URL + "#x", "https://evil.invalid/token"])
def test_transport_fixed_destination(monkeypatch, url):
    monkeypatch.setattr(oauth, "HTTPSConnection", lambda *a, **kw: pytest.fail("No connection expected"))
    assert oauth.token_transport(method="POST", url=url, headers={}, body=b"", timeout=10).status == 0
