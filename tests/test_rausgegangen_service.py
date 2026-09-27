"""Offline manual workflow: exact stored copy and separate teacher evidence."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
import json
from threading import Barrier

import pytest

from workshop_marketing_agent.campaign_state import dump_campaign, restore_campaign
from workshop_marketing_agent.firestore_repository import CommitOutcomeUnknown, StorageUnavailable
from workshop_marketing_agent.service import PilotService, VerifiedPrincipal
from test_service import PRINCIPAL, call, request
from test_service_publication import pilot, harness, source, SaveFault


def prepare_manual(h):
    channel = h.repo.load("c-1").rounds[0].channels[1]
    bound = call(h, "bind_link", channel="rausgegangen", version_reference=channel.current_version, variant_reference="r-v1")
    version = bound["results"][0]["version_reference"]
    approval = call(h, "approve_version", channel="rausgegangen", version_reference=version)["results"][0]["approval_reference"]
    submission = call(h, "prepare_submission", channel="rausgegangen", version_reference=version,
                      approval_reference=approval)["results"][0]["preparation_reference"]
    h.manual = request(h, "begin_rausgegangen_submission", version_reference=version,
                       approval_reference=approval, submission_reference=submission)
    h.feed_calls.clear()
    h.model_calls.clear()
    h.events.clear()
    return h


@pytest.fixture
def manual(pilot):
    return prepare_manual(pilot)


def begin(h, **changes):
    return h.service.handle(h.manual | changes, principal=PRINCIPAL)


def attempt(h):
    return h.repo.load("c-1").rounds[0].channels[1].publications[-1]


def confirmation(h, publication=False, **changes):
    return request(h, "confirm_rausgegangen_publication" if publication else "confirm_rausgegangen_submission",
                   **({"attempt_reference": attempt(h).reference} | changes))


def send(h, data, principal=PRINCIPAL):
    return h.service.handle(data, principal=principal)


def test_exact_copy_projection_after_confirmed_reservation(manual):
    h = manual
    before = h.repo.load("c-1")
    stored = before.rounds[0].channels[1].submissions[-1].package.payload
    saves = h.repo.save_count
    h.events.clear()
    result = begin(h)
    assert result["status"] == "ready_to_copy", result
    assert result["recorded_status"] == result["manual_status"] == "in_progress" and result["package_active"]
    assert result["package"] == {
        "title": stored.title, "description": stored.description,
        "local_date": stored.fact_sheet.local_date.isoformat(),
        "start_time": stored.fact_sheet.start_time.isoformat(), "end_time": stored.fact_sheet.end_time.isoformat(),
        "time_zone": stored.fact_sheet.time_zone, "location": stored.fact_sheet.location,
        "regular_price": str(stored.fact_sheet.regular_price), "currency": stored.fact_sheet.currency,
        "pricing_unit": stored.fact_sheet.pricing_unit, "price_note": stored.price_note,
        "external_booking_link": stored.external_booking_link, "image_reference": stored.image_reference,
        "copy_instructions": list(stored.copy_instructions), "portal_destination": stored.portal_destination}
    assert "Bild: Fiktives Studioteam" in result["package"]["description"]
    assert isinstance(result["package"]["regular_price"], str)
    assert h.repo.save_count == saves + 1 and len(h.feed_calls) == 1
    assert not h.google.calls and not h.model_calls
    assert h.events.index("access") < h.events.index("load") < h.events.index("feed") < h.events.index("save")
    after = h.repo.load("c-1")
    assert after.rounds[0].channels[0] == before.rounds[0].channels[0]
    for field in ("versions", "approvals", "submissions"):
        assert getattr(after.rounds[0].channels[1], field) == getattr(before.rounds[0].channels[1], field)
    raw = dump_campaign(after)
    assert dump_campaign(restore_campaign(raw)) == raw
    for marker in ("fingerprint", "identity_json", "recorded_by", "actor_reference", "original_description_html", "prompt", "usage"):
        assert json.dumps(marker) not in json.dumps(result)


def test_separate_confirmations_and_begin_replay_report_current_state(manual):
    h = manual
    initial = begin(h)
    submission = confirmation(h)
    publication = confirmation(h, True, public_url="https://example.org/event")
    assert send(h, publication)["status"] == "invalid_request"  # No state skipping.
    h.feed_calls.clear()
    h.data["source_version"] = "now-changed"
    h.data["provenance"]["active"] = False
    saves = h.repo.save_count
    submitted = send(h, submission)
    assert submitted["status"] == submitted["manual_status"] == "submitted" and "package" not in submitted
    assert send(h, submission)["recorded_status"] == "submitted"
    publication["expected_revision"] = h.repo.load("c-1").revision
    published = send(h, publication)
    assert published["status"] == published["manual_status"] == "published"
    assert published["public_url"] == "https://example.org/event"
    replay = begin(h)
    assert replay["status"] == "existing_reservation" and not replay["package_active"] and replay["historical"]
    assert replay["recorded_status"] == "in_progress" and replay["manual_status"] == "published"
    assert replay["package"] == initial["package"]
    old_confirmation = send(h, submission)
    assert old_confirmation["recorded_status"] == "submitted" and old_confirmation["manual_status"] == "published"
    assert h.repo.save_count == saves + 2 and not h.feed_calls and not h.google.calls
    assert [e.status for e in attempt(h).events] == ["in_progress", "submitted", "published"]
    blocked = begin(h, request_id="new-begin", expected_revision=h.repo.load("c-1").revision)
    assert blocked["status"] == "existing_reservation" and "package" not in blocked


@pytest.mark.parametrize("case", ["source", "price", "inactive", "image", "availability"])
def test_changed_evidence_does_not_reserve(manual, case):
    h = manual
    if case == "source":
        h.data["source_version"] = "changed"
    elif case == "price":
        h.data["pricing"]["regular_price"] = "999.00"
    elif case == "inactive":
        h.data["provenance"]["active"] = False
    elif case == "image":
        h.data["image"]["permission"] = "unknown"
    else:
        h.now += timedelta(days=2)
    saves = h.repo.save_count
    result = begin(h)
    assert result["status"] in {"outdated", "review_required"}, result
    assert "package" not in result and h.repo.save_count == saves
    assert len(h.feed_calls) == 1 and not h.google.calls


@pytest.mark.parametrize("case", ["disabled", "unselected", "version", "approval", "submission", "round", "google_version", "google_package"])
def test_begin_associations_before_feed(manual, case):
    h = manual
    changes = {}
    if case == "disabled":
        call(h, "set_channel_enabled", channel="rausgegangen", enabled=False)
    elif case == "unselected":
        old = h.repo.load("c-1").rounds[0].channels[1].versions[0].reference
        call(h, "select_version", channel="rausgegangen", version_reference=old)
    elif case in ("version", "approval", "submission", "round"):
        changes[case + "_reference"] = "missing"
    elif case == "google_version":
        changes["version_reference"] = h.intent["version_reference"]
    else:
        changes["submission_reference"] = h.intent["submission_reference"]
    changes["expected_revision"] = h.repo.load("c-1").revision
    saves = h.repo.save_count
    result = begin(h, **changes)
    assert result["status"] in {"review_required", "invalid_request"}
    assert not h.feed_calls and h.repo.save_count == saves


@pytest.mark.parametrize("action", ["begin", "submission", "publication"])
@pytest.mark.parametrize("field", ["channel", "payload", "fingerprint", "operation_id", "external_post_id", "status", "actor_reference", "now"])
def test_extra_fields_rejected(manual, action, field):
    h = manual
    data = h.manual if action == "begin" else dict(action="confirm_rausgegangen_" + action,
        workshop_reference="malws-copy", campaign_reference="c-1", round_reference="r-1", request_id="forged",
        expected_revision=0, attempt_reference="attempt-1")
    assert send(h, data | {field: "private"})["status"] == "invalid_request"
    assert not h.feed_calls and not h.google.calls


@pytest.mark.parametrize("url", ["http://example.org/post", "https://user:secret@example.org/post", "https://127.0.0.1/post",
    "https://10.0.0.1/post", "https://example.local/post", "https://example.org/post?token=private", "https://example.org/post#private",
    "https://example.org/%40private", "not a URL"])
def test_invalid_public_url_rejected(manual, url):
    h = manual
    begin(h)
    send(h, confirmation(h))
    saves = h.repo.save_count
    assert send(h, confirmation(h, True, public_url=url))["status"] == "invalid_request"
    assert h.repo.save_count == saves


def test_submission_does_not_accept_url_and_publication_url_is_optional(manual):
    h = manual
    begin(h)
    assert send(h, confirmation(h, public_url="https://example.org/post"))["status"] == "invalid_request"
    assert send(h, confirmation(h))["status"] == "submitted"
    assert send(h, confirmation(h, True))["status"] == "published"
    assert attempt(h).events[-1].public_url is None


def test_another_authorized_teacher_can_confirm_after_disabled_channel(manual):
    h = manual
    begin(h)
    call(h, "set_channel_enabled", channel="rausgegangen", enabled=False)
    other = VerifiedPrincipal(subject="second-authorized-teacher")
    h.service = PilotService(replace(h.dependencies, check_access=lambda *_: True))
    h.now += timedelta(minutes=1)
    data = confirmation(h)
    assert send(h, data, other)["status"] == "submitted"
    assert attempt(h).events[-1].recorded_by == other.actor_reference
    assert attempt(h).events[-1].recorded_at == h.now
    assert send(h, data, PRINCIPAL)["status"] == "request_conflict"
    assert send(h, confirmation(h, True), PRINCIPAL)["status"] == "published"


@pytest.mark.parametrize("action", ["begin", "submission", "publication"])
@pytest.mark.parametrize("mode", ["unknown", "conflict", "commit_unknown", "commit_conflict"])
def test_uncertain_commit_recovery(manual, action, mode):
    h = manual
    data = h.manual
    if action != "begin":
        begin(h)
        if action == "publication":
            send(h, confirmation(h))
        data = confirmation(h, action == "publication")
    before = h.repo.load("c-1").revision
    h.service = PilotService(replace(h.dependencies, repository=SaveFault(h.repo, 1, mode)))
    result = send(h, data)
    if mode.startswith("commit_"):
        assert result["status"] == {"begin": "ready_to_copy", "submission": "submitted", "publication": "published"}[action], result
        assert h.repo.load("c-1").revision == before + 1
        assert send(h, data)["historical"]
    else:
        assert result["status"] in {"unknown_commit_outcome", "revision_conflict"}
        assert not result["package_active"] and "package" not in result
        assert h.repo.load("c-1").revision == before


@pytest.mark.parametrize("same_id", [False, True])
def test_competing_begins_have_one_reservation(manual, same_id):
    h = manual
    barrier = Barrier(2)
    base = h.repo
    class Repository:
        def load(self, reference):
            return base.load(reference)
        def compare_and_save(self, state, *, expected_revision):
            barrier.wait(timeout=10)
            return base.compare_and_save(state, expected_revision=expected_revision)
    h.service = PilotService(replace(h.dependencies, repository=Repository()))
    other = h.manual if same_id else h.manual | {"request_id": "competing"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda data: send(h, data), [h.manual, other]))
    assert [r["status"] for r in results].count("ready_to_copy") == 1
    assert sum("package" in r for r in results) == 1
    assert len(h.repo.load("c-1").rounds[0].channels[1].publications) == 1


@pytest.mark.parametrize("action", ["begin", "submission", "publication"])
def test_authorization_replay_and_stale_revision(manual, action):
    h = manual
    data = h.manual
    if action != "begin":
        begin(h)
        if action == "publication":
            send(h, confirmation(h))
        data = confirmation(h, action == "publication")
    assert send(h, data | {"expected_revision": 0})["status"] == "revision_conflict"
    h.allowed = False
    h.events.clear()
    assert send(h, data)["status"] == "forbidden" and h.events == ["access"]
    h.allowed = True
    assert send(h, data)["status"] in {"ready_to_copy", "submitted", "published"}
    saves = h.repo.save_count
    assert send(h, data)["historical"]
    assert send(h, data | {"round_reference": "other"})["status"] == "request_conflict"
    h.allowed = False
    h.events.clear()
    assert send(h, data)["status"] == "forbidden" and h.events == ["access"]
    assert h.repo.save_count == saves


@pytest.mark.parametrize("stage", ["begin", "submission", "publication"])
def test_unconfirmed_save_never_returns_active_package(manual, stage):
    h = manual
    data = h.manual
    if stage != "begin":
        begin(h)
        if stage == "publication":
            send(h, confirmation(h))
        data = confirmation(h, stage == "publication")
    base = h.repo
    class Repository:
        saved = False
        def load(self, reference):
            if self.saved:
                raise StorageUnavailable("private-storage")
            return base.load(reference)
        def compare_and_save(self, state, *, expected_revision):
            assert base.compare_and_save(state, expected_revision=expected_revision)
            self.saved = True
            raise CommitOutcomeUnknown("private-storage")
    h.service = PilotService(replace(h.dependencies, repository=Repository()))
    result = send(h, data)
    assert result["status"] == "unknown_commit_outcome" and not result["package_active"]
    assert "package" not in result and "private-storage" not in str(result)
    saves = base.save_count
    h.service = PilotService(h.dependencies)
    recovered = send(h, data)
    assert recovered["historical"] and not recovered["package_active"]
    assert base.save_count == saves


@pytest.mark.parametrize("kind", ["submission", "publication"])
def test_confirmation_wrong_round_channel_and_attempt(manual, kind):
    from test_service_publication import publish
    h = manual
    # Independent Google publication must never become a manual confirmation target.
    assert publish(h, expected_revision=h.repo.load("c-1").revision)["status"] == "published"
    google_attempt = h.repo.load("c-1").rounds[0].channels[0].publications[0].reference
    begin(h, expected_revision=h.repo.load("c-1").revision)
    if kind == "publication":
        send(h, confirmation(h))
    h.feed_calls.clear()
    saves = h.repo.save_count
    for changes in ({"round_reference": "missing"}, {"attempt_reference": "missing"}, {"attempt_reference": google_attempt}):
        result = send(h, confirmation(h, kind == "publication", **changes))
        assert result["status"] == "invalid_request"
    assert h.repo.save_count == saves and not h.feed_calls


def test_replay_can_not_switch_action_or_actor(manual):
    h = manual
    begin(h)
    changed = confirmation(h, request_id=h.manual["request_id"])
    assert send(h, changed)["status"] == "request_conflict"
    other = VerifiedPrincipal(subject="other-teacher")
    h.service = PilotService(replace(h.dependencies, check_access=lambda *_: True))
    assert send(h, h.manual, other)["status"] == "request_conflict"


def test_missing_and_wrong_pilot_authorization(manual):
    h = manual
    h.events.clear()
    assert send(h, h.manual, None)["status"] == "unauthenticated" and h.events == []
    h.service = PilotService(replace(h.dependencies, channels=frozenset({"google_business"})))
    assert begin(h)["status"] == "forbidden"
    h.service = PilotService(replace(h.dependencies, workshops=frozenset({"other-workshop"}), check_access=lambda *_: True))
    assert begin(h, workshop_reference="other-workshop")["status"] == "forbidden"
    assert "load" not in h.events and not h.feed_calls


def test_existing_but_mismatched_approval_and_cross_round(manual):
    h = manual
    another = call(h, "approve_version", channel="rausgegangen", version_reference=h.manual["version_reference"])
    h.feed_calls.clear()
    saves = h.repo.save_count
    assert begin(h, approval_reference=another["results"][0]["approval_reference"],
                 expected_revision=h.repo.load("c-1").revision)["status"] == "review_required"
    assert h.repo.save_count == saves and not h.feed_calls
    assert begin(h, expected_revision=h.repo.load("c-1").revision)["status"] == "ready_to_copy"
    call(h, "start_round", round_reference="r-2", purpose="Erinnerung", selected_channels=["rausgegangen"])
    h.feed_calls.clear()
    assert send(h, confirmation(h, round_reference="r-2"))["status"] == "invalid_request"
    assert not h.feed_calls
