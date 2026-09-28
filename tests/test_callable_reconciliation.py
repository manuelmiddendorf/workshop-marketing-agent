"""Reconciliation uses existing Callable authorization and lazy OAuth wiring."""

import json

import pytest

import pilot_adapter as adapter
from workshop_marketing_agent.campaign_state import dump_campaign
from workshop_marketing_agent.google_local_posts import HttpResponse
from test_callable_google_oauth import wired, pilot, harness, source, assert_counts
from test_google_oauth import CREDENTIALS, TOKEN
from test_service_publication import publish, attempt, LOCATION, POST
from test_reconciliation import setup_read


@pytest.fixture
def reconciliation_callable(wired, monkeypatch):
    h = wired
    # Prepare historical state entirely through the synthetic non-Callable service.
    h.google.outcome, h.google.state = "pending", "PROCESSING"
    assert publish(h)["status"] == "outcome_unknown"
    setup_read(h)
    class Secret:
        @property
        def value(self):
            h.secrets.append("google")
            assert attempt(h).status == "outcome_unknown"
            return h.token.secret()
    monkeypatch.setattr(h.main, "GOOGLE_OAUTH", Secret())
    def http(**kwargs):
        assert len(h.secrets) == len(h.token.calls) == 1
        assert kwargs["method"] == "GET" and kwargs["body"] is None
        assert kwargs["url"] == "https://mybusiness.googleapis.com/v4/" + POST
        assert kwargs["headers"]["Authorization"] == "Bearer " + TOKEN
        h.posts.append(kwargs)
        return HttpResponse(200, json.dumps(h.read.data).encode())
    def factory(ctx, **kwargs):
        return adapter.build_service(ctx, **kwargs, token_http=h.token.http, posts_http=http, enable_google=True,
            google_location=h.main.GOOGLE_LOCATION.value, google_oauth=lambda: h.main.GOOGLE_OAUTH.value)
    monkeypatch.setattr(h.main, "build_service", factory)
    return h


def test_callable_reconciliation_and_replay(reconciliation_callable, capsys):
    h = reconciliation_callable
    result = h.invoke(h.reconcile)["result"]
    assert result["status"] == "published", result
    assert h.invoke(h.reconcile)["result"] == result
    assert_counts(h, 1, 1, 1)
    assert not h.feed_calls
    for marker in (*CREDENTIALS.values(), TOKEN):
        assert marker not in str(result) + dump_campaign(h.repo.load("c-1"))
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("case", ["auth", "app_check", "denied", "forged", "stale", "missing_attempt", "missing_location"])
def test_callable_denials_do_not_read_secret(reconciliation_callable, case):
    h = reconciliation_callable
    data = h.reconcile.copy()
    if case == "auth":
        h.authenticated = False
    elif case == "app_check":
        h.attested = False
    elif case == "denied":
        h.allowed = False
    elif case == "forged":
        data["resource_name"] = POST
    elif case == "stale":
        data["expected_revision"] = 0
    elif case == "missing_attempt":
        data["attempt_reference"] = "missing"
    else:
        h.main.GOOGLE_LOCATION.value = ""
    h.invoke(data)
    assert_counts(h, 0, 0, 0)


def test_callable_failed_refresh_remains_unknown(reconciliation_callable):
    h = reconciliation_callable
    h.token.response = HttpResponse(401, b'{"error":"private"}')
    result = h.invoke(h.reconcile)["result"]
    assert result["status"] == "outcome_unknown" and result["reconciliation_required"]
    assert attempt(h).events[-1].error_category is None
    assert h.invoke(h.reconcile)["result"] == result
    assert_counts(h, 1, 1, 0)


@pytest.mark.parametrize("terminal", [False, True])
def test_callable_no_eligible_resource_never_reads_secret(wired, terminal):
    h = wired
    if not terminal:
        h.google.error = TimeoutError("synthetic-private")
    assert publish(h)["status"] == ("published" if terminal else "outcome_unknown")
    setup_read(h)
    result = h.invoke(h.reconcile)["result"]
    assert result["status"] == ("published" if terminal else "manual_reconciliation_required")
    assert_counts(h, 0, 0, 0)
