"""Real Callable protocol and service factory with offline Auth/storage/HTTP seams."""

import json
from types import SimpleNamespace as NS

import pytest

import pilot_adapter as adapter
from workshop_marketing_agent.campaign_state import dump_campaign
from workshop_marketing_agent.google_local_posts import HttpResponse
from test_google_oauth import Boundary, CREDENTIALS, TOKEN
from test_service import PRINCIPAL
from test_service_publication import pilot, harness, source, LOCATION, POST, SaveFault
from test_submission import NOW


@pytest.fixture
def wired(pilot, monkeypatch):
    from flask import Flask, request
    from firebase_functions.private import util
    import main
    h = pilot
    token = Boundary()
    h.token = token
    h.posts = []
    h.secrets = []
    h.token.raw = json.dumps(CREDENTIALS)
    h.post_error = None
    h.allowed = True
    h.attested = True
    h.authenticated = True
    h.storage = h.repo
    class Secret:
        @property
        def value(self):
            h.secrets.append("google")
            state = h.repo.load("c-1")
            assert state.rounds[0].channels[0].publications[-1].status == "in_progress"
            return token.secret()
    class OpenAISecret:
        @property
        def value(self):
            pytest.fail("Unrelated OpenAI secret access")
    class Database:
        def collection(self, name):
            assert name == "Users"
            return NS(document=lambda email: NS(get=lambda **kwargs: NS(
                exists=True, to_dict=lambda: {"type": "MSN" if h.allowed else "Mitglied", "uid": PRINCIPAL.subject})))
    def account(*args, **kwargs):
        return NS(uid=PRINCIPAL.subject, email="teacher@example.invalid", disabled=False,
                  tenant_id=None, tokens_valid_after_timestamp=0)
    monkeypatch.setattr(adapter.firestore, "client", lambda **kwargs: Database())
    monkeypatch.setattr(adapter.auth, "get_user", account)
    monkeypatch.setattr(adapter.auth, "get_user_by_email", account)
    monkeypatch.setattr(adapter, "FirestoreCampaignRepository", lambda *a, **kw: h.storage)
    monkeypatch.setattr(adapter, "_http_get", h.dependencies.feed_loader)
    monkeypatch.setattr(adapter, "datetime", NS(now=lambda tz: NOW))
    monkeypatch.setattr(main, "GOOGLE_OAUTH", Secret())
    monkeypatch.setattr(main, "OPENAI_API_KEY", OpenAISecret())
    monkeypatch.setattr(main, "GOOGLE_LOCATION", NS(value=LOCATION))
    monkeypatch.setattr(main, "MODEL", NS(value="synthetic-model"))
    def posts_http(**kwargs):
        assert len(h.secrets) == len(token.calls) == 1
        assert kwargs["headers"]["Authorization"] == "Bearer " + TOKEN
        assert kwargs["timeout"] == 15
        assert kwargs["url"] == "https://mybusiness.googleapis.com/v4/" + LOCATION + "/localPosts"
        h.posts.append(kwargs)
        if h.post_error:
            raise h.post_error
        return HttpResponse(200, ('{"name":"' + POST + '","state":"LIVE"}').encode())
    def factory(ctx, **kwargs):
        return adapter.build_service(ctx, **kwargs, token_http=token.http, posts_http=posts_http)
    monkeypatch.setattr(main, "build_service", factory)
    monkeypatch.setattr(util, "on_call_check_tokens", lambda req: NS(
        auth=util.OnCallTokenState.VALID if h.authenticated else util.OnCallTokenState.MISSING,
        app=util.OnCallTokenState.VALID if h.attested else util.OnCallTokenState.MISSING,
        app_token={"sub": "synthetic-app"} if h.attested else None,
        auth_token={"uid": PRINCIPAL.subject, "email": "teacher@example.invalid", "firebase": {}, "auth_time": 100}
                   if h.authenticated else None))
    app = Flask(__name__)
    app.add_url_rule("/call", view_func=lambda: main.pilot_teacher_service(request), methods=["POST"])
    client = app.test_client()
    h.invoke = lambda data=None: client.post("/call", json={"data": h.intent if data is None else data}).get_json()
    h.main = main
    assert not h.secrets and not token.calls and not h.posts
    return h


def assert_counts(h, secret, refresh, posts):
    assert (len(h.secrets), h.token.reads, len(h.token.calls), len(h.posts)) == (secret, secret, refresh, posts)


def test_callable_publication_success_and_replay(wired, capsys):
    h = wired
    result = h.invoke()["result"]
    assert result["status"] == "published", result
    assert h.invoke()["result"] == result
    assert_counts(h, 1, 1, 1)
    snapshot = dump_campaign(h.repo.load("c-1"))
    for secret in (*CREDENTIALS.values(), TOKEN):
        assert secret not in snapshot and secret not in str(result)
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("case", ["unauthenticated", "app_check", "denied", "read", "conflict", "blocked",
                                  "invalid_location", "override", "reservation_conflict"])
def test_protected_lazy_access(wired, case):
    h = wired
    data = h.intent
    if case == "unauthenticated":
        h.authenticated = False
    elif case == "app_check":
        h.attested = False
    elif case == "denied":
        h.allowed = False
    elif case == "read":
        data = {"action": "get_campaign", "workshop_reference": "malws-copy", "campaign_reference": "c-1"}
    elif case == "conflict":
        data = h.intent | {"expected_revision": 0}
    elif case == "invalid_location":
        h.main.GOOGLE_LOCATION.value = "https://evil.invalid/location"
    elif case == "override":
        data = h.intent | {"google_location": LOCATION, "scope": "forged"}
    elif case == "reservation_conflict":
        h.storage = SaveFault(h.repo, 1, "conflict")
    elif case == "blocked":
        # Store a reservation but lose its confirmation, without acquiring credentials.
        base = h.repo
        class Repository:
            blocked = False
            def load(self, reference):
                if self.blocked:
                    from workshop_marketing_agent.firestore_repository import StorageUnavailable
                    raise StorageUnavailable("synthetic")
                return base.load(reference)
            def compare_and_save(self, state, *, expected_revision):
                saved = base.compare_and_save(state, expected_revision=expected_revision)
                self.blocked = True
                return saved
        h.storage = Repository()
        assert h.invoke()["result"]["status"] == "unknown_commit_outcome"
        h.storage = base
    h.invoke(data)
    assert_counts(h, 0, 0, 0)


@pytest.mark.parametrize("failure", ["secret", "invalid_secret", "rejected", "redirect", "rate_limit", "unavailable",
                                     "timeout", "connection", "malformed", "invalid_token"])
def test_token_failure_is_recorded_before_any_post(wired, failure, capsys):
    h = wired
    if failure == "secret":
        h.token.raw = RuntimeError("synthetic-private-secret-error")
    elif failure == "invalid_secret":
        h.token.raw = "{}"
    elif failure in ("rejected", "redirect", "rate_limit", "unavailable"):
        h.token.response = HttpResponse({"rejected": 400, "redirect": 302, "rate_limit": 429, "unavailable": 503}[failure],
                                        b'{"error":"synthetic-private-error"}')
    elif failure == "timeout":
        h.token.error = TimeoutError("synthetic-private-error")
    elif failure == "connection":
        h.token.error = ConnectionError("synthetic-private-error")
    elif failure == "malformed":
        h.token.response = HttpResponse(200, b"synthetic-private-error")
    else:
        h.token.response = HttpResponse(200, b'{"access_token":"bad token","token_type":"Bearer"}')
    result = h.invoke()["result"]
    assert result["status"] == "failed", result
    event = h.repo.load("c-1").rounds[0].channels[0].publications[-1].events[-1]
    assert event.status == "failed" and event.error_category == "permission_denied"
    assert h.invoke()["result"] == result
    assert_counts(h, 1, 0 if failure in ("secret", "invalid_secret") else 1, 0)
    snapshot = dump_campaign(h.repo.load("c-1"))
    assert "synthetic-private" not in str(result) + snapshot
    assert capsys.readouterr() == ("", "")


def test_local_post_timeout_still_records_unknown(wired):
    h = wired
    h.post_error = TimeoutError("synthetic-private")
    assert h.invoke()["result"]["status"] == "outcome_unknown"
    assert h.invoke()["result"]["status"] == "outcome_unknown"
    assert_counts(h, 1, 1, 1)
