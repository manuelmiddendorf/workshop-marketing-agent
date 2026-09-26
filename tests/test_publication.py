"""Durable publication evidence only: no provider transport or credentials."""

import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest

from workshop_marketing_agent.application import (
    ReservePublication, RecordPublicationResult, ConfirmSubmission, ConfirmPublication,
    SetChannelEnabled, DirectRevision, SelectVersion, ApproveVersion, PrepareSubmission,
    BindLink, execute_command,
)
from workshop_marketing_agent.campaign_state import dump_campaign, restore_campaign, _check_successor
from workshop_marketing_agent.publication import approval_fingerprint, payload_fingerprint, target_state
from workshop_marketing_agent.revision import _fingerprint_payload
from workshop_marketing_agent.service import REQUEST_ADAPTER
from workshop_marketing_agent.service_views import campaign_view, publication_views
from test_application import repo, start, prepared, channel_state, command, run, attach
from test_submission import NOW, IMAGE, campaign, source, imported, workflow, imported_data


def reserve_command(repo, id="reserve-1", channel="google_business", round_reference="r-1", **changes):
    state = channel_state(repo, channel, round_reference)
    package = state.submissions[-1]
    approval = next(a for a in state.approvals if a.reference == package.approval_reference)
    version = next(v for v in state.versions if v.reference == package.version_reference)
    fields = dict(channel=channel, round_reference=round_reference,
        operation_id="operation-" + id, version_reference=version.reference, version_fingerprint=version.fingerprint,
        approval_reference=approval.reference, approval_fingerprint=approval_fingerprint(approval.approval),
        submission_reference=package.reference, submission_fingerprint=package.fingerprint,
        payload_fingerprint=payload_fingerprint(package.package))
    return command(repo, ReservePublication, id, **(fields | changes))


@pytest.fixture
def ready(repo, workflow, imported):
    start(repo)
    prepared(repo, workflow, imported)
    prepared(repo, workflow, imported, "rausgegangen")
    return repo


def reserve(repo, imported, id="reserve-1", **changes):
    cmd = reserve_command(repo, id, **changes)
    outcome = execute_command(repo, cmd, evidence=imported)
    assert outcome.status == "committed", outcome
    assert outcome.result.status == "in_progress"
    return cmd


def automatic(repo, id="result-1", *, attempt="reserve-1", status="published", **changes):
    fields = {"attempt_reference": attempt, "status": status}
    if status == "published":
        fields["external_post_id"] = "accounts/example/locations/studio/localPosts/post-1"
    if status == "failed":
        fields["error_category"] = "provider_rejected"
    return command(repo, RecordPublicationResult, id, **(fields | changes))


def commit(repo, cmd, **kwargs):
    outcome = execute_command(repo, cmd, **kwargs)
    assert outcome.status == "committed", outcome
    return outcome


def test_reservation_exact_binding_roundtrip_and_replay(ready, imported):
    before = ready.load("c-1")
    cmd = reserve(ready, imported)
    state = ready.load("c-1")
    attempt = channel_state(ready).publications[0]
    assert attempt.reference == cmd.command_id and attempt.operation == "create" and attempt.update_post_id is None
    assert attempt.initiated_at == NOW and attempt.initiated_by == "teacher-1"
    assert attempt.version_fingerprint == cmd.version_fingerprint
    assert attempt.approval_fingerprint == cmd.approval_fingerprint
    assert attempt.submission_fingerprint == cmd.submission_fingerprint
    assert attempt.payload_fingerprint == cmd.payload_fingerprint
    assert state.revision == before.revision + 1
    assert state.receipts[-1].result.attempt_reference == attempt.reference
    raw = dump_campaign(state)
    assert restore_campaign(raw) == state and "Ankündigung" in raw and '"120.00"' in raw
    assert '"payload"' not in json.dumps(attempt.model_dump(mode="json"))
    assert execute_command(ready, cmd, evidence=imported).status == "replayed"
    assert dump_campaign(ready.load("c-1")) == raw
    changed = cmd.model_copy(update={"operation_id": "different"})
    assert execute_command(ready, changed, evidence=imported).status == "conflict"
    reused = reserve_command(ready, "different-command", operation_id=cmd.operation_id)
    assert execute_command(ready, reused, evidence=imported).status == "conflict"


@pytest.mark.parametrize("field,value", [
    ("version_reference", "missing"), ("approval_reference", "missing"),
    ("submission_reference", "missing"), ("version_fingerprint", "0" * 64),
    ("approval_fingerprint", "0" * 64), ("submission_fingerprint", "0" * 64),
    ("payload_fingerprint", "0" * 64), ("workshop_reference", "different"),
    ("round_reference", "missing"),
])
def test_reservation_rejects_wrong_associations(ready, imported, field, value):
    cmd = reserve_command(ready).model_copy(update={field: value})
    before = dump_campaign(ready.load("c-1"))
    assert execute_command(ready, cmd, evidence=imported).status == "rejected"
    assert dump_campaign(ready.load("c-1")) == before


def test_cross_channel_and_disabled_reservation(ready, imported):
    cmd = reserve_command(ready).model_copy(update={"channel": "rausgegangen"})
    assert execute_command(ready, cmd, evidence=imported).status == "rejected"
    run(ready, SetChannelEnabled, "disable", enabled=False)
    assert execute_command(ready, reserve_command(ready), evidence=imported).status == "rejected"


def test_requires_current_approved_prepared_version(ready, imported):
    cmd = reserve_command(ready)
    current = channel_state(ready).versions[-1].version
    edit = run(ready, DirectRevision, "edit", evidence=imported, version_reference=cmd.version_reference,
               title=current.content.title, text=current.content.body, selected_image_reference=IMAGE)
    assert execute_command(ready, cmd.model_copy(update={"expected_revision": ready.load("c-1").revision}),
                           evidence=imported).status == "rejected"
    assert execute_command(ready, reserve_command(ready, version_reference=edit.version_reference),
                           evidence=imported).status == "rejected"


def test_reservation_requires_fresh_evidence(ready, imported, source):
    assert execute_command(ready, reserve_command(ready)).status == "rejected"
    source["source_version"] = "changed"
    assert execute_command(ready, reserve_command(ready), evidence=imported_data(source)).status == "rejected"
    source["provenance"]["active"] = False
    assert execute_command(ready, reserve_command(ready), evidence=imported_data(source)).status == "rejected"


def test_stale_and_competing_reservations(ready, imported):
    stale = reserve_command(ready, expected_revision=0)
    assert execute_command(ready, stale, evidence=imported).status == "conflict"
    commands = [reserve_command(ready, "reserve-a"), reserve_command(ready, "reserve-b")]
    barrier = Barrier(2)
    class ConcurrentRepository:
        def load(self, reference):
            snapshot = ready.load(reference)
            barrier.wait(timeout=5)
            return snapshot
        def compare_and_save(self, state, *, expected_revision):
            return ready.compare_and_save(state, expected_revision=expected_revision)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda cmd: execute_command(ConcurrentRepository(), cmd, evidence=imported), commands))
    assert sorted(r.status for r in results) == ["committed", "conflict"]
    assert len(channel_state(ready).publications) == 1


@pytest.mark.parametrize("status", ["in_progress", "outcome_unknown"])
def test_unresolved_target_blocks_new_attempts_and_other_rounds(ready, workflow, imported, status):
    reserve(ready, imported)
    if status == "outcome_unknown":
        commit(ready, automatic(ready, status=status))
    assert execute_command(ready, reserve_command(ready, "again"), evidence=imported).status == "rejected"
    start(ready, "r-2")
    draft = attach(ready, workflow, imported, round_reference="r-2")
    bound = run(ready, BindLink, "bind-r2", round_reference="r-2", evidence=imported,
                version_reference=draft.version_reference, campaign=campaign(round="r-2"))
    approval = run(ready, ApproveVersion, "approve-r2", round_reference="r-2", evidence=imported,
                   version_reference=bound.version_reference)
    run(ready, PrepareSubmission, "prepare-r2", round_reference="r-2", evidence=imported,
        version_reference=bound.version_reference, approval_reference=approval.approval_reference)
    assert execute_command(ready, reserve_command(ready, "new-round", round_reference="r-2"), evidence=imported).status == "rejected"
    if status == "outcome_unknown":
        assert execute_command(ready, automatic(ready, "late-failure", status="failed")).status == "rejected"


def test_success_update_failure_preserves_original_publication(ready, imported):
    reserve(ready, imported)
    success = automatic(ready, public_url="https://example.org/öffentlicher-beitrag")
    commit(ready, success)
    assert execute_command(ready, success).status == "replayed"
    first = channel_state(ready).publications[0]
    reserve(ready, imported, "update-1")
    second = channel_state(ready).publications[1]
    assert second.operation == "update" and second.update_post_id == first.events[-1].external_post_id
    wrong_id = automatic(ready, "bad-id", attempt="update-1", external_post_id="post-other")
    assert execute_command(ready, wrong_id).status == "rejected"
    commit(ready, automatic(ready, "failure", attempt="update-1", status="failed"))
    assert channel_state(ready).publications[0] == first
    reserve(ready, imported, "update-2")
    commit(ready, automatic(ready, "updated", attempt="update-2"))
    assert channel_state(ready).publications[-1].status == "published"
    assert channel_state(ready).publications[-1].update_post_id == second.update_post_id


def test_known_create_failure_allows_explicit_new_identity(ready, imported):
    reserve(ready, imported)
    commit(ready, automatic(ready, status="failed"))
    reserve(ready, imported, "new-create")
    assert channel_state(ready).publications[-1].operation == "create"


def test_result_survives_channel_disable_and_draft_change(ready, imported):
    reserve(ready, imported)
    state = channel_state(ready)
    version = state.versions[-1].version
    run(ready, DirectRevision, "edit", evidence=imported, version_reference=state.current_version,
        title=version.content.title, text=version.content.body, selected_image_reference=IMAGE)
    run(ready, SetChannelEnabled, "off", enabled=False)
    commit(ready, automatic(ready))
    assert channel_state(ready).publications[0].version_reference == state.current_version


def test_manual_submission_then_publication_is_separate_evidence(ready, imported):
    reserve(ready, imported, channel="rausgegangen")
    early = command(ready, ConfirmPublication, "early", channel="rausgegangen", attempt_reference="reserve-1")
    assert execute_command(ready, early).status == "rejected"
    submit = command(ready, ConfirmSubmission, "submitted", channel="rausgegangen", attempt_reference="reserve-1")
    commit(ready, submit)
    assert execute_command(ready, submit).status == "replayed"
    assert channel_state(ready, "rausgegangen").publications[0].status == "submitted"
    assert execute_command(ready, reserve_command(ready, "duplicate", channel="rausgegangen"), evidence=imported).status == "rejected"
    publish = command(ready, ConfirmPublication, "published", channel="rausgegangen", attempt_reference="reserve-1",
                      actor_reference="teacher-2", now=NOW + timedelta(hours=1), public_url="https://example.org/event")
    commit(ready, publish)
    attempt = channel_state(ready, "rausgegangen").publications[0]
    assert [e.status for e in attempt.events] == ["in_progress", "submitted", "published"]
    assert attempt.events[-1].recorded_by == "teacher-2"
    assert attempt.events[-1].recorded_at == NOW + timedelta(hours=1)
    assert target_state([attempt]) == (True, None)  # No update identity: never silently create again.
    assert execute_command(ready, reserve_command(ready, "no-update-id", channel="rausgegangen",
        now=NOW + timedelta(hours=1)), evidence=imported).status == "rejected"


@pytest.mark.parametrize("cls", [ConfirmSubmission, ConfirmPublication])
def test_manual_google_rejected(ready, imported, cls):
    reserve(ready, imported)
    assert execute_command(ready, command(ready, cls, "manual", attempt_reference="reserve-1")).status == "rejected"


def test_automatic_rausgegangen_and_cross_association_rejected(ready, imported):
    reserve(ready, imported, channel="rausgegangen")
    assert execute_command(ready, automatic(ready, channel="rausgegangen")).status == "rejected"
    assert execute_command(ready, automatic(ready)).status == "rejected"


@pytest.mark.parametrize("changes", [
    {"status": "published", "external_post_id": None}, {"external_post_id": ""},
    {"external_post_id": "person@example.org"}, {"error_category": "exception text"},
    {"status": "failed", "external_post_id": None, "error_category": None},
    {"status": "outcome_unknown", "external_post_id": None, "public_url": "https://example.org/post"},
    {"now": NOW.replace(tzinfo=None)},
])
def test_invalid_provider_fields_rejected(ready, changes):
    with pytest.raises(ValueError):
        automatic(ready, **changes)


@pytest.mark.parametrize("url", ["http://example.org/post", "https://user:secret@example.org/post",
    "https://example.org/post?token=secret", "https://example.org/post#fragment", "https://127.0.0.1/post",
    "https://studio.local/post", "https://example.org/person%40example.org", "https://example.org/%GG",
    "https://example.org/line\nbreak", "https://example.org:99999/post", "https://127.000.0.1/post"])
def test_invalid_public_urls(ready, url):
    with pytest.raises(ValueError):
        command(ready, ConfirmPublication, "manual", channel="rausgegangen", attempt_reference="a", public_url=url)


def rewritten(raw, edit):
    envelope = json.loads(raw)
    edit(envelope["state"])
    envelope["state_fingerprint"] = _fingerprint_payload(envelope["state"])
    return json.dumps(envelope, ensure_ascii=False)


@pytest.mark.parametrize("change", [
    lambda a: a.update(operation="update"), lambda a: a.update(version_fingerprint="0"*64),
    lambda a: a.update(approval_fingerprint="0"*64), lambda a: a.update(payload_fingerprint="0"*64),
    lambda a: a.update(target_fingerprint="0"*64), lambda a: a.update(version_reference="missing"),
    lambda a: a.update(provider_response={"secret": "must not persist"}),
    lambda a: a["events"][-1].update(recorded_by="changed"),
    lambda a: a["events"][-1].update(external_post_id="changed"),
    lambda a: a["events"][-1].update(public_url="http://example.org"),
    lambda a: a["events"].reverse(), lambda a: a["events"].append(copy.deepcopy(a["events"][-1])),
])
def test_corrupt_publication_rejected_even_with_rehashed_envelope(ready, imported, change):
    reserve(ready, imported)
    commit(ready, automatic(ready))
    raw = dump_campaign(ready.load("c-1"))
    corrupted = rewritten(raw, lambda s: change(s["rounds"][0]["channels"][0]["publications"][0]))
    with pytest.raises(ValueError):
        restore_campaign(corrupted)


def test_events_and_attempts_are_append_only(ready, imported):
    reserve(ready, imported)
    before = ready.load("c-1")
    commit(ready, automatic(ready))
    after = ready.load("c-1")
    _check_successor(before, after)
    old = before.rounds[0].channels[0]
    publication = after.rounds[0].channels[0].publications[0]
    changed = publication.model_copy(update={"initiated_by": "changed"})
    bad_channel = old.model_copy(update={"publications": (changed,)})
    bad_round = after.rounds[0].model_copy(update={"channels": (bad_channel, after.rounds[0].channels[1])})
    with pytest.raises(ValueError, match="immutable"):
        _check_successor(before, after.model_copy(update={"rounds": (bad_round,)}))


def test_safe_views_exclude_provider_identity_and_raw_internals(ready, imported):
    reserve(ready, imported)
    commit(ready, automatic(ready, public_url="https://example.org/post"))
    state = ready.load("c-1")
    view = publication_views(state, channel_state(ready))
    assert set(view[0]) == {"channel", "operation", "status", "attempt_reference", "version_reference",
                            "initiated_at", "another_attempt_blocked", "events"}
    raw = json.dumps(view)
    for forbidden in ("teacher-1", "operation-reserve", "localPosts", "fingerprint", "payload", "identity_json"):
        assert forbidden not in raw
    assert view[0]["status"] == "published" and not view[0]["another_attempt_blocked"]
    assert campaign_view(state, {"google_business"})["rounds"][0]["channels"][0]["publications"] == view


@pytest.mark.parametrize("action", ["reserve_publication", "record_publication_result", "confirm_submission", "confirm_publication"])
def test_no_new_callable_actions(action):
    with pytest.raises(ValueError):
        REQUEST_ADAPTER.validate_json(json.dumps({"action": action, "workshop_reference": "malws-copy",
                                                 "campaign_reference": "c-1"}))


def test_legacy_snapshot_has_no_new_fields(ready):
    raw = dump_campaign(ready.load("c-1"))
    assert "publications" not in raw and "attempt_reference" not in raw
    assert dump_campaign(restore_campaign(raw)) == raw
    # Captured from merged PR17 (c7c4340), using this fixed two-channel workflow.
    assert hashlib.sha256(raw.encode()).hexdigest() == "49e10ff7cc8c82de36687a36c8f92778242af5548ae6a8eeef60a8ad3298a907"


@pytest.mark.parametrize("edit", [
    lambda s: s["rounds"][0]["channels"][0]["publications"].append(
        copy.deepcopy(s["rounds"][0]["channels"][0]["publications"][0])),
    lambda s: s["rounds"][0]["channels"][1].update(
        publications=s["rounds"][0]["channels"][0].pop("publications")),
    lambda s: s["receipts"][-1]["result"].update(attempt_reference="missing"),
    lambda s: s["receipts"][-1]["result"].update(status="failed"),
    lambda s: s["receipts"][-1]["result"].update(submission_reference="missing"),
])
def test_strict_event_associations_and_receipts(ready, imported, edit):
    reserve(ready, imported)
    commit(ready, automatic(ready))
    raw = dump_campaign(ready.load("c-1"))
    with pytest.raises(ValueError):
        restore_campaign(rewritten(raw, edit))


def test_unknown_outcome_never_turns_into_failure_and_remains_blocked_in_view(ready, imported):
    reserve(ready, imported)
    commit(ready, automatic(ready, status="outcome_unknown"))
    before = dump_campaign(ready.load("c-1"))
    assert execute_command(ready, automatic(ready, "later", status="failed")).status == "rejected"
    view = publication_views(ready.load("c-1"), channel_state(ready))
    assert view[0]["another_attempt_blocked"] and view[0]["status"] == "outcome_unknown"
    assert dump_campaign(ready.load("c-1")) == before
