"""Offline Firebase boundary checks; all identities and records are synthetic."""

from datetime import UTC, datetime
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace as NS

from firebase_functions.https_fn import AuthData, CallableRequest
import pytest

import pilot_adapter as adapter
from workshop_marketing_agent.campaign_state import InMemoryCampaignRepository
from workshop_marketing_agent.service import PilotService, ServiceDependencies, _response

UID = "synthetic-teacher"
EMAIL = "teacher@example.invalid"
INTENT = {"action": "create_campaign", "workshop_reference": "malws-copy",
          "campaign_reference": "campaign-1", "request_id": "request-1"}


def context(**changes):
    return AuthData(UID, {"email": EMAIL, "firebase": {}, "auth_time": 100, **changes})


def account(**changes):
    return NS(uid=UID, email=EMAIL, disabled=False, tenant_id=None,
              tokens_valid_after_timestamp=0, **changes)


@pytest.fixture
def policy():
    events = []
    current = account()
    by_email = account()
    role = {"type": "MSN", "uid": UID}

    def lookup(name, value):
        events.append(name)
        if isinstance(value, Exception):
            raise value
        return value

    state = NS(current=current, by_email=by_email, role=role, events=events)
    state.check = lambda ctx, workshop=adapter.WORKSHOP: adapter.teacher_has_access(
        ctx, workshop, get_user=lambda uid: lookup("auth_uid", state.current),
        get_user_by_email=lambda email: lookup("auth_email", state.by_email),
        read_role=lambda email: lookup("role", state.role),
    )
    return state


@pytest.mark.parametrize("role", [{"type": "MSN"}, {"type": "SBN", "uid": UID}])
def test_teacher_policy_accepts_only_current_matching_accounts(policy, role):
    policy.role = role
    policy.current.email = " Teacher@Example.Invalid "
    assert policy.check(context(email=" TEACHER@example.invalid ")) is True
    assert policy.events == ["auth_uid", "auth_email", "role"]


@pytest.mark.parametrize("record,field,value", [
    ("current", "disabled", True), ("current", "disabled", None),
    ("current", "disabled", 0), ("current", "tenant_id", "tenant-a"),
    ("current", "tenant_id", ""), ("current", "uid", "other"),
    ("current", "email", "other@example.invalid"), ("current", "email", None),
    ("by_email", "uid", "ambiguous-account"), ("by_email", "disabled", True),
    ("by_email", "tenant_id", "tenant-a"), ("by_email", "email", EMAIL + "/path"),
    ("current", "tokens_valid_after_timestamp", 100001),
    ("current", "tokens_valid_after_timestamp", None),
])
def test_identity_denials_precede_role_lookup(policy, record, field, value):
    setattr(getattr(policy, record), field, value)
    assert policy.check(context()) is False
    assert "role" not in policy.events


@pytest.mark.parametrize("claims", [
    {"email": None}, {"email": ""}, {"email": "a/b@example.invalid"},
    {"email": "a\x00@example.invalid"}, {"email": [EMAIL]}, {"email": "a@@b"},
    {"firebase": {"tenant": "tenant-a"}}, {"firebase": {"tenant": None}},
    {"firebase": {"tenant": ""}}, {"firebase": None},
    {"auth_time": None}, {"auth_time": True}, {"auth_time": float("nan")},
    {"auth_time": float("inf")}, {"auth_time": -1},
])
def test_invalid_verified_claims_deny(policy, claims):
    assert policy.check(context(**claims)) is False
    assert "role" not in policy.events


@pytest.mark.parametrize("role", [None, [], "MSN", {}, {"type": "Mitglied"},
    {"type": ["MSN"]}, {"type": "msn"}, {"type": "MSN", "uid": "other"},
    {"type": "MSN", "uid": None}, {"type": "MSN", "uid": ""},
    {"type": "MSN", "uid": 1}])
def test_missing_malformed_or_conflicting_role_denies(policy, role):
    policy.role = role
    assert policy.check(context()) is False


@pytest.mark.parametrize("boundary", ["current", "by_email", "role"])
@pytest.mark.parametrize("value", [None, RuntimeError("PRIVATE provider detail")])
def test_missing_accounts_and_lookup_failures_deny(policy, boundary, value, capsys):
    setattr(policy, boundary, value)
    assert policy.check(context()) is False
    assert capsys.readouterr() == ("", "")


@pytest.fixture
def service(policy):
    events = policy.events

    class Repository(InMemoryCampaignRepository):
        def load(self, reference):
            events.append("campaign")
            return super().load(reference)

        def compare_and_save(self, state, *, expected_revision):
            events.append("save")
            return super().compare_and_save(state, expected_revision=expected_revision)

    def forbidden_boundary(*args, **kwargs):
        pytest.fail("This call must not use feeds, secrets or models")

    return PilotService(ServiceDependencies(
        repository=Repository(), check_access=lambda principal, workshop: policy.check(context(), workshop),
        clock=lambda: datetime(2026, 9, 26, tzinfo=UTC), endpoint_for=forbidden_boundary,
        feed_loader=forbidden_boundary, client=NS(generate=forbidden_boundary), model="synthetic-model",
        model_timeout=60, feed_timeout=10, workshops=frozenset({adapter.WORKSHOP}), channels=adapter.CHANNELS,
    ))


def invoke(data, service, *, auth_context=context()):
    return adapter.handle_callable(CallableRequest(data, None, auth=auth_context), service_factory=lambda _: service)


def test_unauthenticated_does_not_construct_dependencies():
    def never(_):
        pytest.fail("Unauthenticated request must not initialize dependencies")
    assert adapter.handle_callable(CallableRequest(INTENT, None), service_factory=never) == _response("unauthenticated")


def test_authorized_forwarding_and_replay_recheck_access(service, policy):
    first = invoke(INTENT, service)
    assert first["status"] == "ok"
    assert policy.events == ["auth_uid", "auth_email", "role", "campaign", "save"]
    policy.events.clear()
    assert invoke(INTENT, service)["status"] == "replayed"
    assert policy.events == ["auth_uid", "auth_email", "role", "campaign"]
    policy.events.clear()
    policy.current.disabled = True
    assert invoke(INTENT, service) == _response("forbidden")
    assert "campaign" not in policy.events and "save" not in policy.events


@pytest.mark.parametrize("field", ["uid", "email", "identity", "role", "token", "actor", "endpoint",
    "collection", "model", "permission", "principal", "auth", "api_key"])
def test_forged_server_fields_rejected_before_any_access(service, policy, field):
    assert invoke({**INTENT, field: "forged"}, service) == _response("invalid_request")
    assert policy.events == []


@pytest.mark.parametrize("data", [None, [], "bad", {**INTENT, "workshop_reference": "other-workshop"}])
def test_invalid_requests_and_nonpilot_are_blocked(service, policy, data):
    assert invoke(data, service)["status"] in {"forbidden", "invalid_request"}
    assert policy.events == []


@pytest.mark.parametrize("failure_at", ["factory", "service"])
def test_unexpected_wrapper_failures_are_sanitized(failure_at, capsys):
    def fail(*args, **kwargs):
        raise RuntimeError("PRIVATE secret source model firestore stack")
    factory = fail if failure_at == "factory" else lambda _: NS(handle=fail)
    result = adapter.handle_callable(CallableRequest(INTENT, None, auth=context()), service_factory=factory)
    assert result == _response("internal_error")
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("status", ["review_required", "partial_completion", "unknown_commit_outcome",
                                   "storage_unavailable", "provider_unavailable"])
def test_preserves_service_sanitized_responses_and_verified_principal(status):
    response = _response(status, automatic_resume=False)
    def handle(data, *, principal):
        assert data is INTENT
        assert principal.subject == UID
        return response
    assert invoke(INTENT, NS(handle=handle)) is response


def test_trusted_wiring_with_fake_auth_firestore_and_model(monkeypatch):
    events = []
    app = object()
    class Document:
        def get(self, **kwargs):
            assert kwargs == {"retry": None, "timeout": 10}
            return NS(exists=True, to_dict=lambda: {"type": "MSN", "uid": UID})
    class Database:
        def collection(self, name):
            events.append(("collection", name))
            return NS(document=lambda email: events.append(("document", email)) or Document())
    database = Database()
    def make_db(**kwargs):
        assert kwargs == {"app": app, "database_id": "(default)"}
        return database
    monkeypatch.setattr(adapter.firestore, "client", make_db)
    def lookup(identifier, **kwargs):
        assert kwargs == {"app": app}
        events.append(("auth", identifier))
        return account()
    monkeypatch.setattr(adapter.auth, "get_user", lookup)
    monkeypatch.setattr(adapter.auth, "get_user_by_email", lookup)
    monkeypatch.setattr(adapter, "FirestoreCampaignRepository",
        lambda client, **kwargs: events.append(("repository", client, kwargs)) or InMemoryCampaignRepository())
    def make_model(**kwargs):
        events.append(("model", kwargs))
        return NS(generate=lambda request: request)
    monkeypatch.setattr(adapter, "OpenAIDraftClient", make_model)
    def key():
        events.append("secret")
        return "synthetic-key"
    service = adapter.build_service(context(), app=app, model="synthetic-model", api_key=key)
    d = service.dependencies
    assert d.workshops == {"malws-copy"} and d.channels == {"google_business", "rausgegangen"}
    assert d.endpoint_for("malws-copy") == (
        "https://europe-west1-middendorf-yoga.cloudfunctions.net/WSGetPublicWorkshops"
        "?format=workshop-data.v1&id=malws-copy")
    assert d.feed_loader is adapter._http_get
    assert d.feed_timeout == 10 and d.model_timeout == 60 and d.model == "synthetic-model"
    assert d.clock().tzinfo == UTC
    assert ("repository", database, {"collection_name": "pilotMarketingCampaigns"}) in events
    assert invoke(INTENT, service)["status"] == "ok"
    assert ("document", EMAIL) in events and "secret" not in events
    assert d.client.generate("synthetic request") == "synthetic request"
    assert events[-2:] == ["secret", ("model", {"api_key": "synthetic-key"})]


@pytest.mark.parametrize("authenticated,attested,expected", [
    (False, True, "unauthenticated"), (True, True, "ok"), (True, False, "UNAUTHENTICATED"),
])
def test_real_callable_protocol_with_fake_verified_context(monkeypatch, service, policy,
                                                         authenticated, attested, expected):
    from flask import Flask, request
    from firebase_functions.private import util
    import main

    seen = []
    def factory(ctx, **kwargs):
        seen.append(ctx)
        assert ctx.uid == UID and ctx.token["email"] == EMAIL
        return service
    monkeypatch.setattr(main, "build_service", factory)
    monkeypatch.setenv("PILOT_MODEL", "synthetic-model")
    # Only the SDK's verified-context seam is faked; protocol decoding stays real.
    monkeypatch.setattr(util, "on_call_check_tokens", lambda request: NS(
        auth=util.OnCallTokenState.VALID if authenticated else util.OnCallTokenState.MISSING,
        app=util.OnCallTokenState.VALID if attested else util.OnCallTokenState.MISSING,
        app_token={"sub": "synthetic-app"} if attested else None,
        auth_token={"uid": UID, **context().token} if authenticated else None,
    ))
    app = Flask(__name__)
    app.add_url_rule("/call", view_func=lambda: main.pilot_teacher_service(request), methods=["POST"])
    response = app.test_client().post("/call", json={"data": INTENT})
    payload = response.get_json()
    if attested:
        assert response.status_code == 200
        assert payload["result"]["status"] == expected
    else:
        assert response.status_code == 401 and payload["error"]["status"] == expected
    if not authenticated or not attested:
        assert not seen and not policy.events


def test_admin_initialization_rejects_wrong_project(monkeypatch):
    monkeypatch.setattr(adapter.firebase_admin, "get_app", lambda: NS(
        project_id="another-project", options={"httpTimeout": 10}))
    with pytest.raises(ValueError, match="Unexpected Firebase"):
        adapter.initialize_admin()


def test_import_and_sdk_discovery_without_secrets_credentials_or_network():
    source = Path(__file__).resolve().parents[1] / "functions"
    script = r'''
import socket
def blocked(*args, **kwargs):
    raise AssertionError("Forbidden discovery side effect")
socket.create_connection = socket.getaddrinfo = blocked
socket.socket.connect = blocked
import google.auth
google.auth.default = blocked
import firebase_admin.credentials
firebase_admin.credentials.ApplicationDefault.get_credential = blocked
from firebase_functions.params import SecretParam, StringParam
SecretParam.value = StringParam.value = property(blocked)
from firebase_functions.private.serving import get_functions, functions_as_yaml
functions = get_functions()
assert list(functions) == ["pilot_teacher_service"]
import yaml
spec = yaml.safe_load(functions_as_yaml(functions))
endpoint = spec["endpoints"]["pilot_teacher_service"]
assert endpoint["platform"] == "gcfv2"
assert endpoint["region"] == ["europe-west1"]
assert endpoint["timeoutSeconds"] == 300
assert endpoint["secretEnvironmentVariables"] == [{"key": "PILOT_OPENAI_API_KEY"}, {"key": "PILOT_GOOGLE_OAUTH"}]
import pilot_adapter
assert pilot_adapter.initialize_admin() is pilot_adapter.initialize_admin()
print("Import, discovery, manifest and repeat initialization passed")
'''
    result = subprocess.run([sys.executable, "-I", "-c", script], cwd=source,
                            env={}, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "passed" in result.stdout
