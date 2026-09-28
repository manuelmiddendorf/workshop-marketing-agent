"""Historical reconciliation through the real service and offline Google HTTP."""

from dataclasses import replace
import json

import pytest

from workshop_marketing_agent import application as app
from workshop_marketing_agent.campaign_state import dump_campaign, restore_campaign, _check_successor
from workshop_marketing_agent.google_local_posts import GoogleLocalPosts, HttpResponse
from workshop_marketing_agent.publication import target_state, all_attempts
from workshop_marketing_agent.revision import _fingerprint_payload
from workshop_marketing_agent.service import PilotService, VerifiedPrincipal
from test_service import PRINCIPAL, call
from test_service_publication import pilot, harness, source, publish, attempt, LOCATION, POST, SaveFault


class ReadBoundary:
    def __init__(self, payload):
        self.data = payload.provider_payload() | {"name": POST, "state": "LIVE", "searchUrl": "https://example.org/post"}
        self.tokens = 0
        self.calls = []
        self.status = 200
        self.error = None
        self.raw = None
        self.before_result = lambda: None

    def token(self):
        self.tokens += 1
        return "synthetic-token"

    def http(self, **kwargs):
        assert kwargs["method"] == "GET" and kwargs["body"] is None
        assert kwargs["url"] == "https://mybusiness.googleapis.com/v4/" + POST
        self.calls.append(kwargs)
        self.before_result()
        if self.error:
            raise self.error
        return HttpResponse(self.status, self.raw if self.raw is not None else json.dumps(self.data).encode())

    def client(self):
        return GoogleLocalPosts(LOCATION, self.token, self.http, 15)


def setup_read(h):
    state = h.repo.load("c-1")
    h.read = ReadBoundary(state.rounds[0].channels[0].submissions[-1].package.payload)
    h.dependencies = replace(h.dependencies, google=h.read.client())
    h.service = PilotService(h.dependencies)
    h.reconcile = dict(action="reconcile_google_publication", workshop_reference="malws-copy",
        campaign_reference="c-1", round_reference="r-1", attempt_reference=attempt(h).reference,
        request_id="reconcile-1", expected_revision=state.revision)
    h.feed_calls.clear()
    return h


@pytest.fixture
def unknown(pilot):
    pilot.google.outcome, pilot.google.state = "pending", "PROCESSING"
    assert publish(pilot)["status"] == "outcome_unknown"
    return setup_read(pilot)


def reconcile(h, **changes):
    return h.service.handle(h.reconcile | changes, principal=PRINCIPAL)


def test_matching_exact_historical_payload_and_replay(unknown, capsys):
    h = unknown
    before = h.repo.load("c-1")
    h.data["source_version"] = "different-now"
    h.data["provenance"]["active"] = False
    result = reconcile(h)
    assert result["status"] == "published", result
    assert result["payload_matched"] is True and result["provider_state"] == "LIVE"
    assert not result["reconciliation_required"]
    assert result["public_url"] == "https://example.org/post"
    assert reconcile(h, expected_revision=0) == result
    assert len(h.read.calls) == h.read.tokens == 1 and not h.feed_calls
    after = h.repo.load("c-1")
    _check_successor(before, after)
    assert not target_state(all_attempts(after, "google_business"))[0]
    assert after.rounds[0].channels[0].versions == before.rounds[0].channels[0].versions
    raw = dump_campaign(after)
    assert dump_campaign(restore_campaign(raw)) == raw
    event = attempt(h).events[-1]
    assert event.source == "reconciliation" and event.reconciliation.reason == "matched"
    assert event.recorded_by == PRINCIPAL.actor_reference
    for forbidden in (POST, "summary", "fingerprint", "identity_json", "actor", "synthetic-token"):
        assert forbidden not in json.dumps(result)
    assert "synthetic-token" not in raw
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("state,status,reason", [
    ("RECURRING", "published", "matched"), ("PROCESSING", "outcome_unknown", "processing"),
    ("SCHEDULED", "outcome_unknown", "processing"), ("REJECTED", "failed", "provider_rejected"),
    ("FUTURE", "outcome_unknown", "unknown_state"), (None, "outcome_unknown", "unknown_state"),
])
def test_provider_states(unknown, state, status, reason):
    h = unknown
    h.read.data["state"] = state
    result = reconcile(h)
    assert result["status"] == status, result
    assert result["reconciliation_required"] == (status == "outcome_unknown")
    assert attempt(h).events[-1].reconciliation.reason == reason
    assert h.read.tokens == len(h.read.calls) == 1
    assert reconcile(h) == result and len(h.read.calls) == 1


@pytest.mark.parametrize("path,value,match", [
    (("summary",), "Anderer Text – Grüße\n", False), (("event", "title"), "Anderer Titel", False),
    (("event", "schedule", "startDate", "day"), 19, False),
    (("event", "schedule", "endDate", "day"), 19, False),
    (("event", "schedule", "startTime", "hours"), 9, False),
    (("event", "schedule", "endTime", "minutes"), 23, False),
    (("callToAction", "url"), "https://example.org/book?utm_source=changed", False),
    (("media",), [{"sourceUrl": "https://example.org/other.png"}], False),
    (("media",), [], False), (("media",), None, None),
    (("languageCode",), "de", None), (("topicType",), "STANDARD", None),
    (("callToAction", "actionType"), "LEARN_MORE", None), (("event",), {}, None),
])
def test_changed_or_unrepresentable_content_stays_blocked(unknown, path, value, match):
    h = unknown
    data = h.read.data
    for key in path[:-1]:
        data = data[key]
    data[path[-1]] = value
    result = reconcile(h)
    assert result["status"] == "outcome_unknown", result
    assert result["payload_matched"] is match and result["reconciliation_required"]
    assert len(h.read.calls) == h.read.tokens == 1


def test_missing_media_does_not_match_approved_image(unknown):
    del unknown.read.data["media"]
    result = reconcile(unknown)
    assert result["status"] == "outcome_unknown" and result["payload_matched"] is False


@pytest.mark.parametrize("case", ["404", "400", "401", "403", "429", "503", "timeout", "malformed", "wrong_resource",
                                  "no_url", "bad_url", "query_url"])
def test_inconclusive_reads_never_prove_failure(unknown, case):
    h = unknown
    if case.isdigit():
        h.read.status = int(case)
    elif case == "timeout":
        h.read.error = TimeoutError("private-read-error")
    elif case == "malformed":
        h.read.raw = b'private-read-error'
    elif case == "wrong_resource":
        h.read.data["name"] = POST + "-other"
    elif case == "no_url":
        del h.read.data["searchUrl"]
    elif case == "bad_url":
        h.read.data["searchUrl"] = "http://internal.invalid/private"
    else:
        h.read.data["searchUrl"] = "https://www.google.com/search?q=private-read-error"
    result = reconcile(h)
    assert result["status"] == "outcome_unknown" and result["reconciliation_required"]
    assert "private-read-error" not in json.dumps(result) + dump_campaign(h.repo.load("c-1"))
    assert h.read.tokens == len(h.read.calls) == 1


@pytest.mark.parametrize("field", ["resource_name", "external_post_id", "payload", "payload_fingerprint", "actor_reference",
    "now", "channel", "status", "location", "oauth", "result"])
def test_forged_fields_rejected_without_token(unknown, field):
    assert reconcile(unknown, **{field: "forged"})["status"] == "invalid_request"
    assert not unknown.read.calls and unknown.read.tokens == 0


@pytest.mark.parametrize("case", ["unauthenticated", "denied", "round", "attempt", "campaign", "workshop", "stale", "configuration"])
def test_preconditions_no_provider(unknown, case):
    h = unknown
    changes = {}
    if case == "denied":
        h.allowed = False
    elif case in ("round", "attempt", "campaign", "workshop"):
        changes[case + "_reference"] = "missing"
    elif case == "stale":
        changes["expected_revision"] = 0
    elif case == "configuration":
        h.service = PilotService(replace(h.dependencies, google=None))
    result = h.service.handle(h.reconcile | changes, principal=None if case == "unauthenticated" else PRINCIPAL)
    assert result["status"] in {"unauthenticated", "forbidden", "invalid_request", "not_found", "revision_conflict", "reconciliation_unavailable", "google_api_unavailable"}
    assert not h.read.calls and h.read.tokens == 0


def test_replay_binding_and_reauthorization(unknown):
    h = unknown
    reconcile(h)
    for change in ({"attempt_reference": "other"}, {"round_reference": "other"}):
        assert reconcile(h, **change)["status"] == "request_conflict"
    assert h.service.handle(h.reconcile, principal=VerifiedPrincipal(subject="other"))["status"] in {"forbidden", "request_conflict"}
    h.allowed = False
    assert reconcile(h)["status"] == "forbidden"
    assert len(h.read.calls) == 1


@pytest.mark.parametrize("mode", ["commit_unknown", "commit_conflict", "unknown", "conflict"])
def test_save_uncertainty_and_exact_receipt_recovery(unknown, mode):
    h = unknown
    h.service = PilotService(replace(h.dependencies, repository=SaveFault(h.repo, 1, mode)))
    result = reconcile(h)
    if mode.startswith("commit_"):
        assert result["status"] == "published", result
        assert reconcile(h) == result
    else:
        assert result["status"] in {"unknown_commit_outcome", "revision_conflict"}
        assert attempt(h).status == "outcome_unknown" and result["reconciliation_required"]
    assert len(h.read.calls) == h.read.tokens == 1


def test_multiple_explicit_observations_append_history(unknown):
    h = unknown
    before = attempt(h).events
    h.read.data["state"] = "PROCESSING"
    first = reconcile(h)
    h.read.data["state"] = "LIVE"
    assert reconcile(h) == first
    second = reconcile(h, request_id="reconcile-2", expected_revision=h.repo.load("c-1").revision)
    assert second["status"] == "published"
    assert attempt(h).events[:len(before)] == before and len(attempt(h).events) == len(before) + 2
    assert len(h.read.calls) == 2
    assert reconcile(h) == first  # Historical receipt is not silently rewritten.


@pytest.mark.parametrize("mode", ["in_progress_create", "unknown_no_resource", "terminal", "in_progress_update"])
def test_eligibility_and_trusted_update_target(pilot, mode):
    h = pilot
    if mode == "terminal" or mode == "in_progress_update":
        assert publish(h)["status"] == "published"
    if mode.startswith("in_progress"):
        # Lose reservation confirmation: orchestration does not dispatch.
        h.service = PilotService(replace(h.dependencies, repository=SaveFault(h.repo, 1, "commit_conflict")))
        publish(h, request_id="interrupted", expected_revision=h.repo.load("c-1").revision)
        assert attempt(h).status == "in_progress"
    elif mode == "unknown_no_resource":
        h.google.error = TimeoutError("private")
        assert publish(h)["status"] == "outcome_unknown"
    setup_read(h)
    result = reconcile(h)
    if mode == "in_progress_update":
        assert result["status"] == "published", result
        assert len(h.read.calls) == h.read.tokens == 1
        assert len(all_attempts(h.repo.load("c-1"), "google_business")) == 2
    else:
        assert result["status"] == ("published" if mode == "terminal" else "manual_reconciliation_required")
        assert not h.read.calls and h.read.tokens == 0


def test_concurrent_change_keeps_read_result_uncommitted(unknown):
    h = unknown
    h.read.before_result = lambda: call(h, "set_channel_enabled", channel="google_business", enabled=False)
    result = reconcile(h)
    assert result["status"] == "revision_conflict" and result["reconciliation_required"]
    assert attempt(h).status == "outcome_unknown" and len(h.read.calls) == 1


@pytest.mark.parametrize("field,value", [("reason", "matched"), ("provider_state", "LIVE"), ("payload_matched", True),
                                         ("extra", "private")])
def test_strict_restoration_rejects_tampered_observation(unknown, field, value):
    h = unknown
    h.read.data["state"] = "PROCESSING"
    reconcile(h)
    envelope = json.loads(dump_campaign(h.repo.load("c-1")))
    data = envelope["state"]
    data["rounds"][0]["channels"][0]["publications"][0]["events"][-1]["reconciliation"][field] = value
    envelope["state_fingerprint"] = _fingerprint_payload(data)
    with pytest.raises(ValueError):
        restore_campaign(json.dumps(envelope))


def test_previous_visible_version_cannot_confirm_interrupted_update(pilot):
    h = pilot
    assert publish(h)["status"] == "published"
    previous = h.repo.load("c-1").rounds[0].channels[0].submissions[-1].package.payload
    bound = call(h, "bind_link", channel="google_business", version_reference=h.intent["version_reference"], variant_reference="v-2")
    version = bound["results"][0]["version_reference"]
    approved = call(h, "approve_version", channel="google_business", version_reference=version)
    approval = approved["results"][0]["approval_reference"]
    prepared = call(h, "prepare_submission", channel="google_business", version_reference=version, approval_reference=approval)
    h.service = PilotService(replace(h.dependencies, repository=SaveFault(h.repo, 1, "commit_conflict")))
    publish(h, request_id="update-new-link", expected_revision=h.repo.load("c-1").revision,
        version_reference=version, approval_reference=approval,
        submission_reference=prepared["results"][0]["preparation_reference"])
    assert attempt(h).status == "in_progress" and attempt(h).operation == "update"
    setup_read(h)
    h.read.data.update(previous.provider_payload())
    result = reconcile(h)
    assert result["status"] == "outcome_unknown" and result["payload_matched"] is False
    attempts = all_attempts(h.repo.load("c-1"), "google_business")
    assert attempts[0].status == "published" and target_state(attempts)[0]
    assert len(h.read.calls) == 1


def test_legacy_publication_events_serialize_without_new_fields(unknown):
    state = unknown.repo.load("c-1")
    raw = dump_campaign(state)
    assert '"reconciliation"' not in raw
    assert dump_campaign(restore_campaign(raw)) == raw
    for event in attempt(unknown).events:
        assert set(event.model_dump()) == {"reference", "status", "recorded_by", "recorded_at", "source",
                                         "error_category", "external_post_id", "public_url"}


def test_unconfirmed_observation_save_does_not_claim_success(unknown):
    from workshop_marketing_agent.firestore_repository import CommitOutcomeUnknown, StorageUnavailable
    h = unknown
    base = h.repo
    class Repository:
        saved = False
        def load(self, reference):
            if self.saved:
                raise StorageUnavailable("private-read-error")
            return base.load(reference)
        def compare_and_save(self, state, *, expected_revision):
            assert base.compare_and_save(state, expected_revision=expected_revision)
            self.saved = True
            raise CommitOutcomeUnknown("private-save-error")
    h.service = PilotService(replace(h.dependencies, repository=Repository()))
    result = reconcile(h)
    assert result["status"] == "unknown_commit_outcome" and result["reconciliation_required"]
    assert result["publication_status"] == "outcome_unknown"
    assert len(h.read.calls) == 1
    h.service = PilotService(h.dependencies)
    assert reconcile(h)["status"] == "published" and len(h.read.calls) == 1


def test_corrupt_reserved_payload_binding_never_reads_google(unknown):
    h = unknown
    state = h.repo.load("c-1")
    bad = attempt(h).model_copy(update={"payload_fingerprint": "0" * 64})
    channel = state.rounds[0].channels[0].model_copy(update={"publications": (bad,)})
    round_ = state.rounds[0].model_copy(update={"channels": (channel, state.rounds[0].channels[1])})
    corrupt = state.model_copy(update={"rounds": (round_,)})
    class Repository:
        def load(self, _):
            return corrupt
    h.service = PilotService(replace(h.dependencies, repository=Repository()))
    assert reconcile(h)["status"] == "internal_error"
    assert h.read.tokens == 0 and not h.read.calls
