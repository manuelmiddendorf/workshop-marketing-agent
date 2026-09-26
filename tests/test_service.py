"""Teacher actions use only synthetic state and explicit offline server boundaries."""

import copy
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

import pytest

from workshop_marketing_agent.campaign_state import InMemoryCampaignRepository, dump_campaign, restore_campaign
from workshop_marketing_agent.feed import FeedResponse
from workshop_marketing_agent.firestore_repository import CommitOutcomeUnknown, StorageUnavailable
from workshop_marketing_agent.generation import GenerationTimeout, ModelCallResult
from workshop_marketing_agent.service import PilotService, ServiceDependencies, VerifiedPrincipal
from test_submission import NOW, IMAGE, source

WORKSHOP = "synthetic-workshop-01"
PRINCIPAL = VerifiedPrincipal(subject="synthetic-auth-subject")


class ObservedRepository(InMemoryCampaignRepository):
    def __init__(self, events):
        super().__init__()
        self.events = events
        self.save_count = 0
        self.fail_at = None
        self.failure = None

    def load(self, reference):
        self.events.append("load")
        return super().load(reference)

    def compare_and_save(self, state, *, expected_revision):
        self.events.append("save")
        self.save_count += 1
        if self.save_count == self.fail_at:
            if self.failure:
                raise self.failure
            return False
        return super().compare_and_save(state, expected_revision=expected_revision)


@pytest.fixture
def harness(source):
    events = []
    repo = ObservedRepository(events)
    wording = json.loads((Path(__file__).parent / "fixtures/generation/valid-wording.json").read_text())
    wording["google_body"] += " Bild: Fiktives Studioteam"
    wording["rausgegangen_description"] += " Bild: Fiktives Studioteam"
    h = SimpleNamespace(events=events, repo=repo, now=NOW, data=source, wording=wording,
                        allowed=True, model_calls=[], feed_calls=[], response=None, feed_response=None)

    def access(principal, workshop):
        events.append("access")
        assert principal == PRINCIPAL and workshop == WORKSHOP
        if isinstance(h.allowed, Exception):
            raise h.allowed
        return h.allowed

    def clock():
        events.append("clock")
        return h.now

    def endpoint(workshop):
        events.append("endpoint")
        assert workshop == WORKSHOP
        return "https://example.org/synthetic-feed"

    def feed(endpoint, timeout):
        events.append("feed")
        h.feed_calls.append((endpoint, timeout))
        if isinstance(h.feed_response, Exception):
            raise h.feed_response
        return h.feed_response or FeedResponse(200, json.dumps({
            "schema_version": "workshop-data.v1", "generated_at": h.now.isoformat(), "workshop": h.data,
        }).encode())

    def generate(request):
        events.append("model")
        h.model_calls.append(request)
        if isinstance(h.response, Exception):
            raise h.response
        return h.response or ModelCallResult("completed", output=h.wording)

    h.dependencies = ServiceDependencies(repository=repo, check_access=access, clock=clock,
        endpoint_for=endpoint, feed_loader=feed, client=SimpleNamespace(generate=generate),
        model="offline-model", model_timeout=10, feed_timeout=5,
        workshops=frozenset({WORKSHOP}), channels=frozenset({"google_business", "rausgegangen"}))
    h.service = PilotService(h.dependencies)
    h.counter = 0
    return h


def request(h, action, **changes):
    data = dict(action=action, workshop_reference=WORKSHOP, campaign_reference="c-1")
    if action != "get_campaign":
        h.counter += 1
        data["request_id"] = f"request-{h.counter}"
    if action not in ("get_campaign", "create_campaign"):
        state = h.repo.load("c-1")
        data.update(expected_revision=state.revision if state else 0, round_reference="r-1")
    data.update(changes)
    return data


def call(h, action, **changes):
    return h.service.handle(request(h, action, **changes), principal=PRINCIPAL)


def setup_round(h):
    assert call(h, "create_campaign")["status"] == "ok"
    assert call(h, "start_round", purpose="Erste Ankündigung", selected_channels=["google_business", "rausgegangen"])["status"] == "ok"


def generated(h):
    setup_round(h)
    result = call(h, "generate_drafts", channels=["google_business", "rausgegangen"])
    assert result["status"] == "ok", result
    return {r["channel"]: r["version_reference"] for r in result["results"]}


def test_authorization_precedes_all_protected_operations(harness):
    h = harness
    data = request(h, "create_campaign")
    h.events.clear()
    assert h.service.handle(data, principal=PRINCIPAL)["status"] == "ok"
    assert h.events == ["access", "load", "clock", "save"]
    assert h.repo.load("c-1").created_by == PRINCIPAL.actor_reference


@pytest.mark.parametrize("allowed", [False, None, 1, "yes", RuntimeError("SECRET")])
def test_denied_access_has_no_side_effects_or_existence_disclosure(harness, allowed):
    h = harness
    call(h, "create_campaign")
    h.allowed = allowed
    data = request(h, "get_campaign")
    h.events.clear()
    present = h.service.handle(data, principal=PRINCIPAL)
    absent = h.service.handle(data | {"campaign_reference": "missing"}, principal=PRINCIPAL)
    assert present == absent
    assert present["status"] == "forbidden"
    assert h.events == ["access", "access"]


@pytest.mark.parametrize("principal", [None, {"subject": "forged"}, "teacher-1"])
def test_missing_verified_principal(harness, principal):
    data = request(harness, "create_campaign")
    assert harness.service.handle(data, principal=principal)["status"] == "unauthenticated"
    assert harness.events == []


@pytest.mark.parametrize("field,value", [
    ("actor_reference", "teacher-evil"), ("now", NOW.isoformat()), ("evidence", {}),
    ("source_version", "v1"), ("model", "evil"), ("endpoint", "https://evil.example"),
    ("timeout", 1000), ("credentials", "SECRET"), ("validation_status", "valid"),
    ("approval_status", "approved"), ("publication_result", "published"), ("enabled", True),
])
def test_forged_fields_rejected_before_access(harness, field, value):
    data = request(harness, "create_campaign", **{field: value})
    assert harness.service.handle(data, principal=PRINCIPAL)["status"] == "invalid_request"
    assert harness.events == []


@pytest.mark.parametrize("workshop,status", [("other", "forbidden"), ("../secret", "invalid_request")])
def test_invalid_or_disallowed_workshop_never_loads(harness, workshop, status):
    data = request(harness, "get_campaign", workshop_reference=workshop)
    assert harness.service.handle(data, principal=PRINCIPAL)["status"] == status
    assert harness.events == []


def test_campaign_workshop_binding_checked_before_response(harness):
    h = harness
    call(h, "create_campaign")
    # Trusted repository returns a valid campaign for a different workshop.
    from workshop_marketing_agent.application import new_campaign
    wrong = new_campaign(campaign_reference="c-1", workshop_reference="other", actor_reference="teacher", now=NOW)
    h.service = PilotService(replace(h.dependencies, repository=SimpleNamespace(load=lambda _: wrong)))
    result = call(h, "get_campaign")
    assert result == {"status": "not_found", "message": "Kampagne nicht gefunden."}
    assert not h.feed_calls and not h.model_calls


def test_creation_race_only_one_commits(harness):
    h = harness
    barrier = Barrier(2)
    original = h.repo.compare_and_save
    def save(state, *, expected_revision):
        barrier.wait(timeout=5)
        return original(state, expected_revision=expected_revision)
    h.repo.compare_and_save = save
    data = request(h, "create_campaign")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: h.service.handle(data, principal=PRINCIPAL), range(2)))
    assert sorted(r["status"] for r in results) == ["ok", "revision_conflict"]
    assert h.repo.load("c-1").revision == 0


def test_complete_review_workflow_and_safe_views(harness):
    h = harness
    versions = generated(h)
    for channel, version in versions.items():
        bound = call(h, "bind_link", channel=channel, version_reference=version, variant_reference="variant-1")
        version = bound["results"][0]["version_reference"]
        approved = call(h, "approve_version", channel=channel, version_reference=version)
        assert approved["status"] == "ok", approved
        approval = approved["results"][0]["approval_reference"]
        prepared = call(h, "prepare_submission", channel=channel, version_reference=version, approval_reference=approval)
        assert prepared["results"][0]["status"] == ("ready" if channel == "google_business" else "ready_to_copy")
    view = call(h, "get_campaign")["campaign"]
    assert view["revision"] == 9
    assert view["current_readiness"] == "not_checked"
    for channel in view["rounds"][0]["channels"]:
        assert channel["approvals"][0]["status"] == "recorded"
        assert channel["preparations"][0]["status"] == "recorded"
        assert channel["versions"][0]["image_reference"] == IMAGE
    text = json.dumps(view)
    for secret in ("identity_json", "input_fingerprint", "receipts", "original_description_html", "provenance", "usage", "source_version", PRINCIPAL.subject):
        assert secret not in text
    assert restore_campaign(dump_campaign(h.repo.load("c-1"))) == h.repo.load("c-1")


@pytest.mark.parametrize("action", ["create_campaign", "start_round", "generate_drafts"])
def test_replay_ignores_new_time_and_feed_but_reuses_committed_results(harness, action):
    h = harness
    if action == "start_round":
        call(h, "create_campaign")
    if action == "generate_drafts":
        setup_round(h)
    changes = ({"purpose": "Ankündigung", "selected_channels": ["google_business"]} if action == "start_round"
               else {"channels": ["google_business", "rausgegangen"]} if action == "generate_drafts" else {})
    data = request(h, action, **changes)
    first = h.service.handle(data, principal=PRINCIPAL)
    assert first["status"] == "ok"
    old_state = dump_campaign(h.repo.load("c-1"))
    h.now += timedelta(days=20)
    h.data["source_version"] = "changed"
    h.events.clear()
    # New facade instance, durable repository: no in-memory replay cache.
    replay = PilotService(h.dependencies).handle(data, principal=PRINCIPAL)
    assert replay["status"] == "replayed"
    assert replay["historical"] is True
    assert h.events == ["access", "load"]
    assert dump_campaign(h.repo.load("c-1")) == old_state
    if "results" in first:
        assert replay["results"] == first["results"]


def test_reused_request_id_conflicts_with_changed_intent_or_actor(harness):
    h = harness
    setup_round(h)
    data = request(h, "set_channel_enabled", channel="google_business", enabled=False)
    assert h.service.handle(data, principal=PRINCIPAL)["status"] == "ok"
    assert h.service.handle(data | {"enabled": True}, principal=PRINCIPAL)["status"] == "request_conflict"
    service = PilotService(replace(h.dependencies, check_access=lambda *_: True))
    assert service.handle(data, principal=VerifiedPrincipal(subject="other-teacher"))["status"] == "request_conflict"


def test_stale_revision_stops_before_providers(harness):
    h = harness
    setup_round(h)
    data = request(h, "generate_drafts", channels=["google_business"], expected_revision=0)
    h.events.clear()
    result = h.service.handle(data, principal=PRINCIPAL)
    assert result["status"] == "revision_conflict" and result["revision"] == 1
    assert h.events == ["access", "load"]


def test_feed_validates_at_explicit_post_fetch_time(harness):
    h = harness
    setup_round(h)
    original = h.dependencies.feed_loader
    def feed(endpoint, timeout):
        h.now += timedelta(seconds=5)
        h.data["availability"]["observed_at"] = h.now.isoformat()
        return original(endpoint, timeout)
    h.service = PilotService(replace(h.dependencies, feed_loader=feed))
    result = call(h, "generate_drafts", channels=["google_business"])
    assert result["status"] == "ok", result
    assert h.feed_calls == [("https://example.org/synthetic-feed", 5.0)]
    assert h.model_calls[0].timeout == 10


def test_changed_evidence_preserves_approval_and_requires_review(harness):
    h = harness
    version = generated(h)["google_business"]
    approved = call(h, "approve_version", channel="google_business", version_reference=version)
    assert approved["status"] == "ok"
    h.data["source_version"] = "new-version"
    h.data["verification"]["source_version"] = "new-version"
    result = call(h, "approve_version", channel="google_business", version_reference=version)
    assert result["status"] == "outdated"
    assert len(h.repo.load("c-1").rounds[0].channels[0].approvals) == 1


@pytest.mark.parametrize("active,status", [(False, "cancelled"), (True, "postponed"), (True, "unknown")])
def test_inactive_evidence_prevents_model(harness, active, status):
    h = harness
    setup_round(h)
    h.data["provenance"].update(active=active, event_status=status)
    assert call(h, "generate_drafts", channels=["google_business"])["status"] == "review_required"
    assert not h.model_calls


def test_stale_availability_prevents_generation(harness):
    h = harness
    setup_round(h)
    h.now += timedelta(days=1)
    result = call(h, "generate_drafts", channels=["google_business"])
    assert result["status"] == "review_required"
    assert not h.model_calls


@pytest.mark.parametrize("response", [GenerationTimeout("SECRET"), ModelCallResult("refused", detail="SECRET"),
                                      ModelCallResult("failed", detail="SECRET")])
def test_model_failures_are_sanitized(harness, response, caplog, capsys):
    h = harness
    setup_round(h)
    h.response = response
    result = call(h, "generate_drafts", channels=["google_business"])
    assert result["status"] == "provider_unavailable"
    assert len(h.model_calls) == 1
    assert "SECRET" not in json.dumps(result) + caplog.text + str(capsys.readouterr())


@pytest.mark.parametrize("response", [TimeoutError("SECRET"), FeedResponse(500, b"SECRET"), FeedResponse(200, b"SECRET")])
def test_feed_failures_never_call_model(harness, response):
    h = harness
    setup_round(h)
    h.feed_response = response
    result = call(h, "generate_drafts", channels=["google_business"])
    assert result["status"] == "provider_unavailable"
    assert "SECRET" not in json.dumps(result)
    assert not h.model_calls


def test_partial_generation_replay_never_resumes_model(harness):
    h = harness
    setup_round(h)
    h.repo.fail_at = h.repo.save_count + 2
    data = request(h, "generate_drafts", channels=["google_business", "rausgegangen"])
    result = h.service.handle(data, principal=PRINCIPAL)
    assert result["status"] == "partial_completion"
    assert len(result["results"]) == 1
    h.now += timedelta(seconds=50)
    h.repo.fail_at = None
    replay = PilotService(h.dependencies).handle(data, principal=PRINCIPAL)
    assert replay["status"] == "partial_completion" and replay["historical"]
    assert replay["automatic_resume"] is False
    assert len(h.model_calls) == len(h.feed_calls) == 1
    assert len(h.repo.load("c-1").rounds[0].channels[0].versions) == 1
    assert not h.repo.load("c-1").rounds[0].channels[1].versions


@pytest.mark.parametrize("failure,expected", [(StorageUnavailable("SECRET"), "storage_unavailable"),
    (CommitOutcomeUnknown("SECRET"), "unknown_commit_outcome"), (RuntimeError("SECRET"), "internal_error")])
def test_storage_failures_never_leak_and_do_not_retry(harness, failure, expected, caplog):
    h = harness
    h.repo.fail_at = 1
    h.repo.failure = failure
    result = call(h, "create_campaign")
    assert result["status"] == expected
    assert h.repo.save_count == 1
    assert "SECRET" not in json.dumps(result) + caplog.text


def test_unknown_second_commit_retains_first_result(harness):
    h = harness
    setup_round(h)
    h.repo.fail_at = h.repo.save_count + 2
    h.repo.failure = CommitOutcomeUnknown("SECRET")
    result = call(h, "generate_drafts", channels=["google_business", "rausgegangen"])
    assert result["status"] == "unknown_commit_outcome"
    assert len(result["results"]) == 1 and result["automatic_resume"] is False
    assert len(h.model_calls) == 1


def test_direct_ai_selection_and_channel_toggle(harness):
    h = harness
    version = generated(h)["google_business"]
    common = dict(channel="google_business", version_reference=version)
    edited = call(h, "direct_revision", **common, title=h.wording["google_title"], text=h.wording["google_body"],
                  selected_image_reference=IMAGE)
    assert edited["status"] == "ok", edited
    edited_version = edited["results"][0]["version_reference"]
    h.response = ModelCallResult("completed", output={"title": h.wording["google_title"], "text": h.wording["google_body"]})
    revised = call(h, "ai_revision", channel="google_business", version_reference=edited_version,
                   instruction="Bitte freundlich formulieren", selected_image_reference=IMAGE)
    assert revised["status"] == "ok", revised
    assert call(h, "select_version", **common)["status"] == "ok"
    assert call(h, "set_channel_enabled", channel="google_business", enabled=False)["status"] == "ok"
    assert call(h, "approve_version", **common)["status"] == "invalid_request"
    assert call(h, "set_channel_enabled", channel="google_business", enabled=True)["status"] == "ok"
    channel = call(h, "get_campaign")["campaign"]["rounds"][0]["channels"][0]
    assert channel["current_readiness"] == "not_checked" and channel["last_recorded_status"] == "review_required"
    assert len(channel["versions"]) == 3


def test_wrong_version_and_approval_associations(harness):
    h = harness
    versions = generated(h)
    assert call(h, "approve_version", channel="google_business", version_reference=versions["rausgegangen"])["status"] == "invalid_request"
    assert call(h, "prepare_submission", channel="google_business", version_reference=versions["google_business"],
                approval_reference="missing")["status"] == "invalid_request"
    assert call(h, "generate_drafts", channels=["google_business"])["status"] == "invalid_request"
    assert len(h.model_calls) == 1


def test_diagnostics_never_forward_arbitrary_fields_or_messages():
    from workshop_marketing_agent.service_views import diagnostic_views
    from workshop_marketing_agent.validation import Diagnostic
    result = diagnostic_views([Diagnostic("SECRET.field", "invalid", "SECRET message", "draft"),
                               Diagnostic("image.SECRET", "invalid", "SECRET image", "draft")])
    assert "SECRET" not in json.dumps(result)
    assert [r["code"] for r in result] == ["review", "image"]


def test_import_has_no_environment_credentials_or_network_side_effects():
    import subprocess
    import sys
    script = '''
import os, socket
import google.auth
from google.cloud.firestore_v1 import Client
import openai

def forbidden(*args, **kwargs):
    raise AssertionError("Import accessed a forbidden boundary")
# Pydantic itself reads its plugin switch when creating any schema.
os.getenv = lambda key, *args: None if key == "PYDANTIC_DISABLE_PLUGINS" else forbidden()
os.environ = type("NoEnvironment", (), {"get": forbidden, "__getitem__": forbidden})()
google.auth.default = forbidden
Client.__init__ = forbidden
openai.OpenAI.__init__ = forbidden
socket.socket.connect = forbidden
socket.getaddrinfo = forbidden
import workshop_marketing_agent.service
print("import-safe")
'''
    result = subprocess.run([sys.executable, "-c", script], env={"PATH": "/usr/bin:/bin"},
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "import-safe"


def test_independent_channel_validation_keeps_both_results(harness):
    h = harness
    setup_round(h)
    h.data["image"] = None
    result = call(h, "generate_drafts", channels=["google_business", "rausgegangen"])
    assert result["status"] == "review_required", result
    assert [r["status"] for r in result["results"]] == ["revised", "review_required"]
    state = h.repo.load("c-1")
    assert all(len(c.versions) == 1 for c in state.rounds[0].channels)
    assert result["results"][1]["diagnostics"][-1]["code"] == "image"


def test_ai_failure_receipt_replays_without_model(harness):
    h = harness
    version = generated(h)["google_business"]
    h.response = GenerationTimeout("SECRET")
    data = request(h, "ai_revision", channel="google_business", version_reference=version,
                   instruction="Bitte kürzer", selected_image_reference=IMAGE)
    result = h.service.handle(data, principal=PRINCIPAL)
    assert result["status"] == "provider_unavailable", result
    h.now += timedelta(minutes=10)
    replay = h.service.handle(data, principal=PRINCIPAL)
    assert replay["status"] == "replayed"
    assert replay["results"] == result["results"]
    assert len(h.model_calls) == 2  # Initial generation and one failed revision, no replay call.
    assert len(h.repo.load("c-1").rounds[0].channels[0].versions) == 1


def test_unknown_commit_reconciles_saved_receipt_without_provider_retry(harness):
    h = harness
    setup_round(h)
    original = h.repo.compare_and_save
    def uncertain(state, *, expected_revision):
        assert original(state, expected_revision=expected_revision)
        raise CommitOutcomeUnknown("SECRET")
    h.repo.compare_and_save = uncertain
    data = request(h, "generate_drafts", channels=["google_business"])
    assert h.service.handle(data, principal=PRINCIPAL)["status"] == "unknown_commit_outcome"
    h.repo.compare_and_save = original
    h.events.clear()
    result = h.service.handle(data, principal=PRINCIPAL)
    assert result["status"] == "replayed" and len(result["results"]) == 1
    assert h.events == ["access", "load"]


def test_concurrent_generation_calls_commit_only_one_set(harness):
    h = harness
    setup_round(h)
    barrier = Barrier(2)
    original = h.dependencies.client.generate
    def generate(request):
        barrier.wait(timeout=5)
        return original(request)
    h.service = PilotService(replace(h.dependencies, client=SimpleNamespace(generate=generate)))
    data = request(h, "generate_drafts", channels=["google_business", "rausgegangen"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: h.service.handle(data, principal=PRINCIPAL), range(2)))
    assert any(r["status"] == "ok" for r in results)
    assert len(h.model_calls) == 2  # Deliberately no exactly-once promise before persistence.
    state = h.repo.load("c-1")
    assert state.revision == 3
    assert all(len(c.versions) == 1 for c in state.rounds[0].channels)


@pytest.mark.parametrize("field,value", [("expected_revision", True), ("expected_revision", "1"),
                                          ("enabled", 1), ("enabled", "false")])
def test_strict_types(harness, field, value):
    h = harness
    setup_round(h)
    data = request(h, "set_channel_enabled", channel="google_business", enabled=False)
    data[field] = value
    h.events.clear()
    assert h.service.handle(data, principal=PRINCIPAL)["status"] == "invalid_request"
    assert not h.events


def test_channel_allowlist_limits_commands_and_views(harness):
    h = harness
    generated(h)
    h.service = PilotService(replace(h.dependencies, channels=frozenset({"google_business"})))
    assert call(h, "set_channel_enabled", channel="rausgegangen", enabled=True)["status"] == "invalid_request"
    view = call(h, "get_campaign")["campaign"]
    assert [c["channel"] for c in view["rounds"][0]["channels"]] == ["google_business"]


def test_binding_cannot_be_rewritten_or_assigned_to_another_actor(harness):
    h = harness
    setup_round(h)
    state = h.repo.load("c-1")
    raw = json.loads(dump_campaign(state))
    raw["state"]["receipts"][0]["service_request"]["actor_reference"] = "other"
    from workshop_marketing_agent.revision import _fingerprint_payload
    raw["state_fingerprint"] = _fingerprint_payload(raw["state"])
    with pytest.raises(ValueError, match="actor mismatch"):
        restore_campaign(json.dumps(raw))


@pytest.mark.parametrize("timeout", [0, -1, 121, float("nan"), float("inf"), True])
def test_configuration_requires_bounded_timeouts(harness, timeout):
    with pytest.raises(ValueError):
        replace(harness.dependencies, model_timeout=timeout)
