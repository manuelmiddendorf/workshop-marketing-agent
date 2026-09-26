"""Synthetic pilot orchestration, including lost acknowledgements and contention."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from threading import Barrier, Lock

import pytest
from pydantic import ValidationError

from workshop_marketing_agent import application as app
from workshop_marketing_agent.campaign_state import dump_campaign, restore_campaign
from workshop_marketing_agent.firestore_repository import CommitOutcomeUnknown, StorageUnavailable
from workshop_marketing_agent.google_local_posts import PostResult
from workshop_marketing_agent.publication import PublicationEvent
from workshop_marketing_agent.service import PilotService, VerifiedPrincipal
from workshop_marketing_agent.service_views import campaign_view
from test_service import harness, request, call, generated, PRINCIPAL
from test_submission import source, NOW

LOCATION = "accounts/synthetic/locations/studio"
POST = LOCATION + "/localPosts/post-1"


class FakeGoogle:
    location = LOCATION

    def __init__(self, h):
        self.h = h
        self.calls = []
        self.outcome = "visible"
        self.state = "LIVE"
        self.resource = POST
        self.url = "https://example.org/public-post"
        self.error = None
        self.before_result = lambda: None

    def _call(self, operation, payload, target=None):
        state = self.h.repo.load("c-1")
        attempt = state.rounds[0].channels[0].publications[-1]
        assert attempt.status == "in_progress"
        assert state.receipts[-1].result.attempt_reference == attempt.reference
        assert attempt.operation == operation and attempt.update_post_id == target
        self.h.events.append("google")
        self.calls.append((operation, payload, target))
        self.before_result()
        if self.error:
            raise self.error
        return PostResult(operation, self.outcome, self.resource, self.state, self.url)

    def create_event(self, payload):
        return self._call("create", payload)

    def update_event(self, post_name, payload):
        return self._call("update", payload, post_name)


@pytest.fixture
def pilot(harness, monkeypatch):
    import test_service
    monkeypatch.setattr(test_service, "WORKSHOP", "malws-copy")
    h = harness
    for section in ("identity", "verification", "availability"):
        h.data[section]["workshop_id"] = "malws-copy"
    h.dependencies = replace(h.dependencies, workshops=frozenset({"malws-copy"}))
    h.service = PilotService(h.dependencies)
    initial = generated(h)["google_business"]
    bound = call(h, "bind_link", channel="google_business", version_reference=initial, variant_reference="v-1")
    version = bound["results"][0]["version_reference"]
    approved = call(h, "approve_version", channel="google_business", version_reference=version)
    approval = approved["results"][0]["approval_reference"]
    prepared = call(h, "prepare_submission", channel="google_business", version_reference=version,
                    approval_reference=approval)
    h.google = FakeGoogle(h)
    h.dependencies = replace(h.dependencies, google=h.google)
    h.service = PilotService(h.dependencies)
    h.intent = request(h, "publish_google", version_reference=version, approval_reference=approval,
                       submission_reference=prepared["results"][0]["preparation_reference"])
    h.events.clear()
    h.feed_calls.clear()
    return h


def publish(h, **changes):
    return h.service.handle(h.intent | changes, principal=PRINCIPAL)


def attempt(h):
    return h.repo.load("c-1").rounds[0].channels[0].publications[-1]


def test_create_exact_binding_and_replay(pilot):
    h = pilot
    before = h.repo.load("c-1")
    result = publish(h)
    assert result["status"] == "published", result
    assert result["revision"] == before.revision + 2
    assert result["operation"] == "create" and not result["reconciliation_required"]
    assert h.google.calls[0][1] == before.rounds[0].channels[0].submissions[-1].package.payload
    assert h.events.index("access") < h.events.index("feed") < h.events.index("save") < h.events.index("google")
    assert h.events[h.events.index("google") + 1:].count("save") == 1
    assert publish(h, expected_revision=0) == result
    assert len(h.google.calls) == len(h.feed_calls) == 1
    state = h.repo.load("c-1")
    raw = dump_campaign(state)
    assert dump_campaign(restore_campaign(raw)) == raw
    serialized = json.dumps(result)
    for forbidden in (POST, "fingerprint", "actor", "operation_id", "identity_json", "payload", "Bearer"):
        assert forbidden not in serialized


def test_update_targets_recorded_post(pilot):
    h = pilot
    assert publish(h)["status"] == "published"
    result = publish(h, request_id="publish-again", expected_revision=h.repo.load("c-1").revision)
    assert result["status"] == "published" and result["operation"] == "update"
    assert h.google.calls[-1][2] == POST
    assert len(h.google.calls) == 2


@pytest.mark.parametrize("field", ["channel", "actor_reference", "now", "operation_id", "payload",
    "version_fingerprint", "location", "status", "external_post_id"])
def test_strict_intent(pilot, field):
    assert publish(pilot, **{field: "private"})["status"] == "invalid_request"
    assert not pilot.google.calls and not pilot.feed_calls


@pytest.mark.parametrize("field", ["round_reference", "version_reference", "approval_reference", "submission_reference"])
def test_invalid_associations_and_changed_replay_intent(pilot, field):
    h = pilot
    assert publish(h, **{field: "missing"})["status"] == "review_required"
    assert not h.google.calls
    assert publish(h)["status"] == "published"
    assert publish(h, **{field: "changed"})["status"] == "request_conflict"
    assert len(h.google.calls) == 1


def test_actor_replay_conflicts(pilot):
    h = pilot
    publish(h)
    other = PilotService(replace(h.dependencies, check_access=lambda *_: True))
    assert other.handle(h.intent, principal=VerifiedPrincipal(subject="other"))["status"] == "request_conflict"
    assert len(h.google.calls) == 1


@pytest.mark.parametrize("case", ["denied", "unauthenticated", "wrong_workshop", "channel_allowlist", "missing_config", "bad_location", "stale_revision"])
def test_preconditions_never_reserve_or_dispatch(pilot, case):
    h = pilot
    before = dump_campaign(h.repo.load("c-1"))
    if case == "denied":
        h.allowed = False
    if case == "channel_allowlist":
        h.service = PilotService(replace(h.dependencies, channels=frozenset({"rausgegangen"})))
    if case == "missing_config":
        h.service = PilotService(replace(h.dependencies, google=None))
    if case == "bad_location":
        h.google.location = "https://internal.invalid"
    changes = {"workshop_reference": "other"} if case == "wrong_workshop" else {}
    if case == "stale_revision":
        changes["expected_revision"] = 0
    result = h.service.handle(h.intent | changes, principal=None if case == "unauthenticated" else PRINCIPAL)
    assert result["status"] in {"forbidden", "unauthenticated", "publication_unconfigured", "revision_conflict"}
    assert dump_campaign(h.repo.load("c-1")) == before
    assert not h.google.calls and not h.feed_calls


@pytest.mark.parametrize("case", ["inactive", "source_changed", "feed_error", "disabled", "selection_changed"])
def test_readiness_rechecked(pilot, case):
    h = pilot
    if case == "inactive":
        h.data["provenance"]["active"] = False
    if case == "source_changed":
        h.data["source_version"] = "new-version"
    if case == "feed_error":
        h.feed_response = RuntimeError("private feed")
    if case == "disabled":
        call(h, "set_channel_enabled", channel="google_business", enabled=False)
    if case == "selection_changed":
        initial = h.repo.load("c-1").rounds[0].channels[0].versions[0].reference
        call(h, "select_version", channel="google_business", version_reference=initial)
    result = publish(h, expected_revision=h.repo.load("c-1").revision)
    assert result["status"] in ("review_required", "provider_unavailable")
    assert not h.google.calls
    assert not h.repo.load("c-1").rounds[0].channels[0].publications


@pytest.mark.parametrize("outcome,state,status,error", [
    ("visible", "LIVE", "published", None), ("visible", "RECURRING", "published", None),
    ("pending", "PROCESSING", "outcome_unknown", None), ("pending", "SCHEDULED", "outcome_unknown", None),
    ("unresolved", None, "outcome_unknown", None), ("ambiguous_write", None, "outcome_unknown", None),
    ("unavailable", None, "outcome_unknown", None), ("rejected", "REJECTED", "failed", "provider_rejected"),
    ("provider_rejection", None, "failed", "provider_rejected"),
    ("authentication_or_permission", None, "failed", "permission_denied"),
    ("invalid_input", None, "failed", "invalid_submission"),
])
def test_all_mappings_and_unknown_blocks(pilot, outcome, state, status, error):
    h = pilot
    h.google.outcome, h.google.state = outcome, state
    result = publish(h)
    assert result["status"] == status, result
    event = attempt(h).events[-1]
    assert event.error_category == error
    expected_id = POST if outcome in ("visible", "pending", "unresolved") else None
    assert event.external_post_id == expected_id
    assert POST not in json.dumps(campaign_view(h.repo.load("c-1"), h.dependencies.channels))
    assert publish(h) == result
    if status == "outcome_unknown":
        assert publish(h, request_id="new", expected_revision=result["revision"])["status"] == "reconciliation_required"
    assert len(h.google.calls) == 1


@pytest.mark.parametrize("case", ["exception", "bad_name", "cross_location", "wrong_state", "bad_result"])
def test_untrusted_adapter_failures_are_unknown(pilot, case):
    h = pilot
    if case == "exception":
        h.google.error = RuntimeError("private OAuth and approved copy")
    if case == "bad_name":
        h.google.resource = "not a Google name"
    if case == "cross_location":
        h.google.resource = "accounts/other/locations/other/localPosts/x"
    if case == "wrong_state":
        h.google.state = "SCHEDULED"
    if case == "bad_result":
        original = h.google.create_event
        h.google.create_event = lambda payload: (original(payload), {"raw": "secret"})[1]
    assert publish(h)["status"] == "outcome_unknown"
    assert len(h.google.calls) == 1
    assert attempt(h).events[-1].external_post_id is None


@pytest.mark.parametrize("url", ["https://www.google.com/search?q=private", "http://example.org", "https://10.0.0.1/p"])
def test_optional_url_obeys_existing_storage_policy(pilot, url):
    pilot.google.url = url
    result = publish(pilot)
    assert result["status"] == "published" and result["public_url"] is None
    assert url not in json.dumps(result)


class SaveFault:
    def __init__(self, base, stage, mode):
        self.base, self.stage, self.mode = base, stage, mode
        self.count = 0
        self.lock = Lock()

    def load(self, reference):
        return self.base.load(reference)

    def compare_and_save(self, state, *, expected_revision):
        with self.lock:
            self.count += 1
            hit = self.count == self.stage
        if not hit:
            return self.base.compare_and_save(state, expected_revision=expected_revision)
        if self.mode in ("commit_unknown", "commit_conflict"):
            assert self.base.compare_and_save(state, expected_revision=expected_revision)
        if self.mode in ("conflict", "commit_conflict"):
            return False
        raise CommitOutcomeUnknown("private storage detail")


@pytest.mark.parametrize("mode", ["conflict", "unknown", "commit_unknown"])
def test_reservation_acknowledgement(pilot, mode):
    h = pilot
    fault = SaveFault(h.repo, 1, mode)
    h.service = PilotService(replace(h.dependencies, repository=fault))
    result = publish(h)
    if mode == "commit_unknown":
        assert result["status"] == "published" and len(h.google.calls) == 1
    else:
        assert result["status"] in ("revision_conflict", "unknown_commit_outcome")
        assert not h.google.calls


@pytest.mark.parametrize("mode", ["conflict", "unknown", "commit_unknown", "commit_conflict"])
def test_result_acknowledgement_and_replay(pilot, mode):
    h = pilot
    h.service = PilotService(replace(h.dependencies, repository=SaveFault(h.repo, 2, mode)))
    result = publish(h)
    replay = publish(h)
    if mode.startswith("commit_"):
        assert result["status"] == "published" and replay == result
    else:
        assert result["status"] == "unknown_commit_outcome"
        assert replay["status"] == "in_progress" and replay["reconciliation_required"]
    assert len(h.google.calls) == 1


def test_failed_confirmation_read_never_dispatches_and_replay_does_not_resume(pilot):
    h = pilot
    base = h.repo
    class Repository:
        def __init__(self):
            self.saved = False
            self.block_read = True
        def load(self, reference):
            if self.saved and self.block_read:
                raise StorageUnavailable("private")
            return base.load(reference)
        def compare_and_save(self, state, *, expected_revision):
            saved = base.compare_and_save(state, expected_revision=expected_revision)
            self.saved = True
            raise CommitOutcomeUnknown("private")
    repo = Repository()
    h.service = PilotService(replace(h.dependencies, repository=repo))
    assert publish(h)["status"] == "unknown_commit_outcome"
    repo.block_read = False
    assert publish(h)["status"] == "in_progress"
    assert not h.google.calls


@pytest.mark.parametrize("same_request", [False, True])
def test_competing_invocations_only_reservation_owner_dispatches(pilot, same_request):
    h = pilot
    base = h.repo
    barrier = Barrier(2)
    class Repository:
        def load(self, reference):
            return base.load(reference)
        def compare_and_save(self, state, *, expected_revision):
            if state.receipts[-1].result.status == "in_progress":
                barrier.wait(timeout=10)
                saved = base.compare_and_save(state, expected_revision=expected_revision)
                # Even both invocations losing their acknowledgements cannot share ownership.
                raise CommitOutcomeUnknown("private")
            return base.compare_and_save(state, expected_revision=expected_revision)
    h.service = PilotService(replace(h.dependencies, repository=Repository()))
    intents = [h.intent, h.intent | {"request_id": h.intent["request_id"] if same_request else "competing"}]
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda data: h.service.handle(data, principal=PRINCIPAL), intents))
    assert len(h.google.calls) == 1
    assert sorted(r["status"] for r in results) == ["published", "unknown_commit_outcome"]


def test_result_can_record_after_channel_disabled(pilot):
    h = pilot
    h.google.before_result = lambda: call(h, "set_channel_enabled", channel="google_business", enabled=False)
    assert publish(h)["status"] == "published"
    assert not h.repo.load("c-1").rounds[0].channels[0].enabled


def test_unknown_resource_extension_and_strict_restoration(pilot):
    h = pilot
    h.google.outcome, h.google.state = "pending", "PROCESSING"
    publish(h)
    state = h.repo.load("c-1")
    raw = dump_campaign(state)
    assert dump_campaign(restore_campaign(raw)) == raw
    event = attempt(h).events[-1]
    for changes in ({"external_post_id": "opaque-id"}, {"external_post_id": POST + "?secret=x"},
                    {"source": "manual"}, {"public_url": "https://example.org/p"}, {"arbitrary_metadata": {}}):
        with pytest.raises(ValidationError):
            PublicationEvent.model_validate(event.model_dump() | changes)


def test_update_unknown_cannot_switch_target(pilot):
    h = pilot
    publish(h)
    h.google.outcome, h.google.state = "pending", "PROCESSING"
    result = publish(h, request_id="update", expected_revision=h.repo.load("c-1").revision)
    assert result["status"] == "outcome_unknown"
    stored = attempt(h)
    assert stored.events[-1].external_post_id == POST
    wrong_event = stored.events[-1].model_copy(update={"external_post_id": LOCATION + "/localPosts/other"})
    with pytest.raises(ValidationError):
        type(stored).model_validate_json(stored.model_copy(update={"events": stored.events[:-1] + (wrong_event,)}).model_dump_json())
    assert len(h.google.calls) == 2 and h.google.calls[-1][0] == "update"


def test_pending_without_identity_stays_unknown(pilot):
    pilot.google.outcome, pilot.google.state, pilot.google.resource = "pending", "PROCESSING", None
    assert publish(pilot)["status"] == "outcome_unknown"
    assert attempt(pilot).events[-1].external_post_id is None


def test_existing_but_mismatched_approval_rejected(pilot):
    h = pilot
    newer = call(h, "approve_version", channel="google_business", version_reference=h.intent["version_reference"])
    result = publish(h, approval_reference=newer["results"][0]["approval_reference"],
                     expected_revision=h.repo.load("c-1").revision)
    assert result["status"] == "review_required"
    assert not h.google.calls


def test_result_receipt_read_failure_replay_confirms_without_dispatch(pilot):
    h = pilot
    base = h.repo
    class Repository:
        block = False
        def load(self, reference):
            if self.block:
                raise StorageUnavailable("private")
            return base.load(reference)
        def compare_and_save(self, state, *, expected_revision):
            saved = base.compare_and_save(state, expected_revision=expected_revision)
            if state.receipts[-1].result.status == "published":
                self.block = True
                raise CommitOutcomeUnknown("private")
            return saved
    repo = Repository()
    h.service = PilotService(replace(h.dependencies, repository=repo))
    result = publish(h)
    assert result["status"] == "unknown_commit_outcome"
    assert result["publication_status"] == "in_progress"
    repo.block = False
    assert publish(h)["status"] == "published"
    assert len(h.google.calls) == 1


def test_wrong_campaign_workshop_does_not_dispatch(pilot):
    h = pilot
    other = app.new_campaign(campaign_reference="other", workshop_reference="different", actor_reference="synthetic", now=NOW)
    h.repo.compare_and_save(other, expected_revision=None)
    assert publish(h, campaign_reference="other")["status"] == "not_found"
    assert not h.google.calls


def test_replay_works_without_google_configuration(pilot):
    h = pilot
    result = publish(h)
    h.service = PilotService(replace(h.dependencies, google=None))
    assert publish(h) == result
    assert len(h.google.calls) == 1


def test_request_namespace_is_campaign_local_but_action_is_bound(pilot):
    h = pilot
    assert publish(h)["status"] == "published"
    changed_action = request(h, "set_channel_enabled", request_id=h.intent["request_id"],
                             channel="google_business", enabled=False)
    assert h.service.handle(changed_action, principal=PRINCIPAL)["status"] == "request_conflict"
    other_campaign = request(h, "create_campaign", campaign_reference="other-campaign",
                             request_id=h.intent["request_id"])
    assert h.service.handle(other_campaign, principal=PRINCIPAL)["status"] == "ok"
    assert len(h.google.calls) == 1
