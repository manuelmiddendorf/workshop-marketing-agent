import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest

from workshop_marketing_agent.application import (
    AIRevision, ApproveVersion, AttachDraft, BindLink, DirectRevision, PrepareSubmission,
    SelectVersion, SetChannelEnabled, StartRound, execute_command, new_campaign,
)
from workshop_marketing_agent.campaign_state import (
    InMemoryCampaignRepository, dump_campaign, restore_campaign,
)
from workshop_marketing_agent.revision import _fingerprint_payload
from test_submission import NOW, IMAGE, FakeClient, campaign, imported_data, source, imported, workflow


@pytest.fixture
def repo():
    repo = InMemoryCampaignRepository()
    state = new_campaign(campaign_reference="c-1", workshop_reference="synthetic-workshop-01",
                         actor_reference="teacher-1", now=NOW)
    assert repo.compare_and_save(state, expected_revision=None)
    return repo


def command(repo, cls, id, **kwargs):
    defaults = dict(campaign_reference="c-1", workshop_reference="synthetic-workshop-01",
                    round_reference="r-1", command_id=id, actor_reference="teacher-1", now=NOW,
                    expected_revision=repo.load("c-1").revision)
    if cls is not StartRound:
        defaults["channel"] = "google_business"
    return cls(**(defaults | kwargs))


def run(repo, cls, id, *, evidence=None, client=None, **kwargs):
    cmd = command(repo, cls, id, **kwargs)
    result = execute_command(repo, cmd, evidence=evidence, client=client)
    assert result.status == "committed", result
    return result.result


def start(repo, round_reference="r-1"):
    return run(repo, StartRound, "start-" + round_reference, round_reference=round_reference,
               purpose="Erste Ankündigung – für alle", selected_channels=("google_business", "rausgegangen"))


def attach(repo, workflow, imported, channel="google_business", round_reference="r-1", id=None):
    version = workflow.current(channel)
    return run(repo, AttachDraft, id or f"draft-{round_reference}-{channel}", evidence=imported,
               round_reference=round_reference, channel=channel, content=version.content,
               facts=version.workshop_facts, metadata=version.generation_metadata,
               selected_image_reference=IMAGE)


def tracked(repo, workflow, imported, channel="google_business"):
    initial = attach(repo, workflow, imported, channel)
    return run(repo, BindLink, "bind-" + channel, evidence=imported, channel=channel,
               version_reference=initial.version_reference, campaign=campaign(channel))


def prepared(repo, workflow, imported, channel="google_business"):
    bound = tracked(repo, workflow, imported, channel)
    approved = run(repo, ApproveVersion, "approve-" + channel, evidence=imported,
                   channel=channel, version_reference=bound.version_reference)
    result = run(repo, PrepareSubmission, "prepare-" + channel, evidence=imported,
                 channel=channel, version_reference=bound.version_reference,
                 approval_reference=approved.approval_reference)
    assert result.status == ("ready" if channel == "google_business" else "ready_to_copy")
    return result


def channel_state(repo, channel="google_business", round_reference="r-1"):
    return next(c for r in repo.load("c-1").rounds if r.reference == round_reference
                for c in r.channels if c.channel == channel)


def test_two_channel_roundtrip(repo, workflow, imported):
    start(repo)
    prepared(repo, workflow, imported)
    prepared(repo, workflow, imported, "rausgegangen")
    state = repo.load("c-1")
    raw = dump_campaign(state)
    assert "Ankündigung" in raw
    assert restore_campaign(raw) == state
    assert json.loads(raw)["state"]["rounds"][0]["channels"][0]["versions"][0]["version"]["workshop_facts"]["regular_price"] == "120.00"


def test_independent_failure_and_multiple_rounds(repo, workflow, imported):
    start(repo)
    prepared(repo, workflow, imported)
    before = channel_state(repo)
    version = workflow.current("rausgegangen")
    result = run(repo, AttachDraft, "bad-raus", evidence=imported, channel="rausgegangen",
                 content=version.content.model_copy(update={"description": "Nicht belegte Angaben"}),
                 facts=version.workshop_facts, metadata=version.generation_metadata,
                 selected_image_reference=IMAGE)
    assert result.status == "review_required"
    assert channel_state(repo) == before
    start(repo, "r-2")
    attach(repo, workflow, imported, round_reference="r-2")
    assert channel_state(repo) == before
    assert len(repo.load("c-1").rounds) == 2


def test_approval_does_not_transfer_and_selection_preserves_history(repo, workflow, imported):
    start(repo)
    package = prepared(repo, workflow, imported)
    before = channel_state(repo)
    version = before.versions[-1].version
    edited = run(repo, DirectRevision, "edit", evidence=imported, version_reference=before.current_version,
                 title=version.content.title, text=version.content.body + " Wir freuen uns auf dich!",
                 selected_image_reference=IMAGE)
    outcome = execute_command(repo, command(repo, PrepareSubmission, "wrong-approval", version_reference=edited.version_reference,
                                           approval_reference=package.approval_reference), evidence=imported)
    assert outcome.status == "rejected"
    run(repo, SelectVersion, "select-old", version_reference=before.current_version)
    after = channel_state(repo)
    assert after.last_status == "review_required"
    assert after.versions[:2] == before.versions
    assert after.approvals == before.approvals
    assert after.submissions == before.submissions
    assert len(after.versions) == 3
    # Branch from an old selection: parent is the selected record, not the last appended record.
    result = run(repo, DirectRevision, "branch-edit", evidence=imported, version_reference=before.current_version,
                 title=version.content.title, text=version.content.body, selected_image_reference=IMAGE)
    assert channel_state(repo).versions[-1].parent_reference == before.current_version
    assert result.version_reference == "branch-edit"


def test_disable_reenable_and_changed_evidence(repo, workflow, imported, source):
    start(repo)
    result = prepared(repo, workflow, imported)
    before = channel_state(repo)
    run(repo, SetChannelEnabled, "off", enabled=False)
    cmd = command(repo, PrepareSubmission, "blocked", version_reference=result.version_reference,
                  approval_reference=result.approval_reference)
    assert execute_command(repo, cmd, evidence=imported).status == "rejected"
    run(repo, SetChannelEnabled, "on", enabled=True)
    source["source_version"] = "changed"
    changed = imported_data(source)
    result = run(repo, PrepareSubmission, "stale", evidence=changed,
                 version_reference=result.version_reference, approval_reference=result.approval_reference)
    assert result.status == "outdated"
    after = channel_state(repo)
    assert after.submissions == before.submissions
    assert after.approvals == before.approvals
    assert result.submission_reference is None


def test_binding_before_approval(repo, workflow, imported):
    start(repo)
    initial = attach(repo, workflow, imported)
    approved = run(repo, ApproveVersion, "approve-plain", evidence=imported, version_reference=initial.version_reference)
    result = run(repo, PrepareSubmission, "plain", evidence=imported, version_reference=initial.version_reference,
                 approval_reference=approved.approval_reference)
    assert result.status == "review_required"
    bound = run(repo, BindLink, "bind", evidence=imported, version_reference=initial.version_reference, campaign=campaign())
    assert execute_command(repo, command(repo, PrepareSubmission, "old-approval", version_reference=bound.version_reference,
        approval_reference=approved.approval_reference), evidence=imported).status == "rejected"


def test_ai_replay_after_restore_and_conflicting_reuse(repo, workflow, imported):
    start(repo)
    attached = attach(repo, workflow, imported)
    version = workflow.current("google_business")
    fake = FakeClient({"title": version.content.title, "text": version.content.body})
    cmd = command(repo, AIRevision, "ai", version_reference=attached.version_reference,
                  instruction="Bitte freundlich formulieren", selected_image_reference=IMAGE,
                  model="offline-fake", timeout=5.0)
    first = execute_command(repo, cmd, evidence=imported, client=fake)
    assert first.status == "committed"
    assert len(fake.requests) == 1
    # Every load restores strict JSON; replay may use a refreshed CAS precondition.
    replay = execute_command(repo, cmd.model_copy(update={"expected_revision": repo.load("c-1").revision}),
                             evidence=imported, client=fake)
    assert replay.status == "replayed" and replay.result == first.result
    assert len(fake.requests) == 1
    conflict = execute_command(repo, cmd.model_copy(update={"instruction": "Bitte kürzer"}), evidence=imported, client=fake)
    assert conflict.status == "conflict" and len(fake.requests) == 1


def test_same_ai_command_concurrent_commits_at_most_once(repo, workflow, imported):
    start(repo)
    attached = attach(repo, workflow, imported)
    version = workflow.current("google_business")
    barrier = Barrier(2)

    class RacingClient(FakeClient):
        def generate(self, request):
            barrier.wait(timeout=5)
            return super().generate(request)

    client = RacingClient({"title": version.content.title, "text": version.content.body})
    cmd = command(repo, AIRevision, "race", version_reference=attached.version_reference,
                  instruction="Bitte freundlich", selected_image_reference=IMAGE, model="offline-fake", timeout=5.0)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(execute_command, repo, cmd, evidence=imported, client=client) for _ in range(2)]
        outcomes = [f.result() for f in futures]
    assert sorted(o.status for o in outcomes) == ["committed", "conflict"]
    assert len(client.requests) == 2  # CAS is not exactly-once external execution.
    assert len(channel_state(repo).versions) == 2
    assert execute_command(repo, cmd, evidence=imported, client=client).status == "replayed"
    assert len(client.requests) == 2


@pytest.mark.parametrize("changes", [
    {"round_reference": "r-other"}, {"channel": "rausgegangen"},
    {"workshop_reference": "other"}, {"version_reference": "missing"},
])
def test_cross_references_rejected(repo, workflow, imported, changes):
    start(repo)
    initial = attach(repo, workflow, imported)
    before = repo.load("c-1")
    cmd = command(repo, ApproveVersion, "bad-ref", version_reference=initial.version_reference).model_copy(update=changes)
    assert execute_command(repo, cmd, evidence=imported).status == "rejected"
    assert repo.load("c-1") == before


def mutate_snapshot(state, mutation, *, rehash=True):
    envelope = json.loads(dump_campaign(state))
    mutation(envelope["state"])
    if rehash:
        envelope["state_fingerprint"] = _fingerprint_payload(envelope["state"])
    return json.dumps(envelope)


@pytest.mark.parametrize("mutation", [
    lambda s: s.update(schema_version="future.v2"),
    lambda s: s.pop("schema_version"),
    lambda s: s.update(revision=True),
    lambda s: s.update(created_at="2026-10-24T10:00:00"),
    lambda s: s["rounds"][0]["channels"][0].update(current_version="missing"),
    lambda s: s["rounds"][0]["channels"][0]["versions"][0].update(fingerprint="0" * 64),
    lambda s: s["rounds"][0]["channels"][0]["versions"][0]["version"].update(unexpected=True),
    lambda s: s["rounds"][0]["channels"][0]["versions"][1].update(parent_reference="missing"),
    lambda s: s["rounds"][0]["channels"][0]["approvals"][0].update(version_reference="missing"),
    lambda s: s["rounds"][0]["channels"][0]["submissions"][0].update(fingerprint="0" * 64),
    lambda s: s["receipts"][0].update(input_fingerprint="0" * 64),
    lambda s: s["receipts"][0]["result"].update(round_reference="missing"),
])
def test_corrupt_state_rejected_even_with_new_envelope_digest(repo, workflow, imported, mutation):
    start(repo)
    prepared(repo, workflow, imported)
    with pytest.raises(ValueError):
        restore_campaign(mutate_snapshot(repo.load("c-1"), mutation))


def test_corruption_and_duplicate_json_keys(repo):
    with pytest.raises(ValueError):
        restore_campaign(mutate_snapshot(repo.load("c-1"), lambda s: s.update(created_by="different"), rehash=False))
    with pytest.raises(ValueError):
        restore_campaign('{"state": {}, "state": {}, "state_fingerprint": "x"}')


def test_repository_isolation_and_stale_commands(repo):
    assert InMemoryCampaignRepository().load("c-1") is None
    old = repo.load("c-1")
    cmd = command(repo, StartRound, "stale", purpose="Erinnerung", selected_channels=())
    start(repo)
    assert execute_command(repo, cmd).status == "conflict"
    assert not repo.compare_and_save(old, expected_revision=None)
    assert not repo.compare_and_save(old, expected_revision=0)


def test_ai_failure_preserves_other_channel(repo, workflow, imported):
    start(repo)
    prepared(repo, workflow, imported, "rausgegangen")
    before = channel_state(repo, "rausgegangen")
    initial = attach(repo, workflow, imported)
    result = run(repo, AIRevision, "bad-ai", evidence=imported, client=FakeClient({"bad": "output"}),
                 version_reference=initial.version_reference, instruction="Bitte kürzer", selected_image_reference=IMAGE,
                 model="offline-fake", timeout=5.0)
    assert result.status == "review_required" and result.diagnostics
    assert len(channel_state(repo).versions) == 1
    assert channel_state(repo, "rausgegangen") == before


@pytest.mark.parametrize("mutation", [
    lambda s: s["receipts"][-1]["result"].update(version_reference="draft-r-1-google_business"),
    lambda s: s["receipts"][0]["result"].update(submission_reference="does-not-exist"),
    lambda s: s["receipts"][0]["result"].update(version_reference="bind-google_business"),
    lambda s: s["receipts"][-1]["result"].update(status="approved"),
])
def test_review_receipt_cross_bindings(repo, workflow, imported, mutation):
    start(repo)
    prepared(repo, workflow, imported)
    with pytest.raises(ValueError):
        restore_campaign(mutate_snapshot(repo.load("c-1"), mutation))


def test_prepared_payload_must_match_approved_version(repo, workflow, imported):
    from pydantic import TypeAdapter
    from workshop_marketing_agent.submission import PreparedSubmission

    start(repo)
    prepared(repo, workflow, imported)

    def corrupt(state):
        record = state["rounds"][0]["channels"][0]["submissions"][0]
        record["package"]["payload"]["summary"] = "Nicht der freigegebene Text"
        # An internally self-consistent payload hash is insufficient: bindings must also match.
        package = TypeAdapter(PreparedSubmission).validate_json(json.dumps(record["package"]))
        record["fingerprint"] = package.fingerprint

    with pytest.raises(ValueError, match="payload differs"):
        restore_campaign(mutate_snapshot(repo.load("c-1"), corrupt))


def test_restored_ready_status_never_bypasses_source_check(repo, workflow, imported, source):
    start(repo)
    result = prepared(repo, workflow, imported)
    assert channel_state(repo).last_status == "ready"
    source["provenance"]["event_status"] = "cancelled"
    outcome = run(repo, PrepareSubmission, "check-again", evidence=imported_data(source),
                  version_reference=result.version_reference, approval_reference=result.approval_reference)
    assert outcome.status == "outdated" and outcome.submission_reference is None
    assert len(channel_state(repo).submissions) == 1


def test_generation_flags_cannot_bypass_validation(repo, workflow, imported):
    start(repo)
    version = workflow.current("google_business")
    cmd = command(repo, AttachDraft, "unchecked", content=version.content.model_copy(update={"body": "Unsinn"}),
                  facts=version.workshop_facts, metadata=version.generation_metadata, selected_image_reference=IMAGE)
    outcome = execute_command(repo, cmd, evidence=imported)
    assert outcome.result.status == "review_required"
    approval = run(repo, ApproveVersion, "not-approved", evidence=imported, version_reference="unchecked")
    assert approval.approval_reference is None
    from workshop_marketing_agent.application import COMMAND_ADAPTER
    raw = json.loads(cmd.model_dump_json()) | {"valid": True, "approved": True}
    with pytest.raises(ValueError):
        COMMAND_ADAPTER.validate_json(json.dumps(raw))


def test_evidence_and_time_are_part_of_command_identity(repo, workflow, imported, source):
    start(repo)
    initial = attach(repo, workflow, imported)
    cmd = command(repo, ApproveVersion, "identity", version_reference=initial.version_reference)
    assert execute_command(repo, cmd, evidence=imported).status == "committed"
    changed_time = cmd.model_copy(update={"now": NOW + timedelta(seconds=1)})
    assert execute_command(repo, changed_time, evidence=imported).status == "conflict"
    source["provenance"]["event_status"] = "cancelled"
    assert execute_command(repo, cmd, evidence=imported_data(source)).status == "conflict"


def test_replay_successful_package_is_historical_without_rechecking(repo, workflow, imported):
    start(repo)
    result = prepared(repo, workflow, imported)
    cmd = command(repo, PrepareSubmission, "prepare-google_business", version_reference=result.version_reference,
                  approval_reference=result.approval_reference)
    outcome = execute_command(repo, cmd, evidence=imported)
    assert outcome.status == "replayed" and outcome.result == result
    assert outcome.result.revision < cmd.expected_revision + 1


def test_source_html_is_preserved_per_attached_lineage(repo, workflow, imported, source):
    start(repo)
    attach(repo, workflow, imported)
    original = channel_state(repo).versions[0].original_description_html
    source["content"]["public_description_html"] = "<p>Neue Quelle mit ü und\nZeilenumbruch</p>"
    changed = imported_data(source)
    attach(repo, workflow, changed, id="new-source")
    newest = channel_state(repo).versions[-1]
    assert newest.original_description_html != original
    version = newest.version
    fake = FakeClient({"title": version.content.title, "text": version.content.body})
    run(repo, AIRevision, "new-source-ai", evidence=changed, client=fake, version_reference="new-source",
        instruction="Bitte freundlich", selected_image_reference=IMAGE, model="offline-fake", timeout=5.0)
    assert channel_state(repo).versions[-1].original_description_html == newest.original_description_html
    assert channel_state(repo).versions[0].original_description_html == original
    assert "Neue Quelle" in fake.requests[0].input_json


def test_disabled_channel_preserves_records_across_json(repo, workflow, imported):
    start(repo)
    prepared(repo, workflow, imported)
    previous = channel_state(repo)
    run(repo, SetChannelEnabled, "disable", enabled=False)
    restored = channel_state(repo)
    assert not restored.enabled
    assert restored.versions == previous.versions
    assert restored.approvals == previous.approvals
    assert restored.submissions == previous.submissions


def test_wrong_campaign_link_and_missing_ai_boundary_are_rejected(repo, workflow, imported):
    start(repo)
    result = attach(repo, workflow, imported)
    for metadata in (campaign(round="wrong"), campaign(campaign="wrong"), campaign("rausgegangen")):
        cmd = command(repo, BindLink, "bad-link", version_reference=result.version_reference, campaign=metadata)
        assert execute_command(repo, cmd, evidence=imported).status == "rejected"
    cmd = command(repo, AIRevision, "missing-client", version_reference=result.version_reference,
                  instruction="Bitte kürzer", selected_image_reference=IMAGE, model="offline-fake", timeout=5.0)
    assert execute_command(repo, cmd, evidence=imported).status == "rejected"


def test_unknown_typed_content_and_approval_bindings_fail_restore(repo, workflow, imported):
    start(repo)
    prepared(repo, workflow, imported)
    for mutation in (
        lambda s: s["rounds"][0]["channels"][0]["versions"][0]["version"]["content"].update(channel="rausgegangen"),
        lambda s: s["rounds"][0]["channels"][0]["approvals"][0]["approval"].update(version_fingerprint="0" * 64),
        lambda s: s["rounds"][0]["channels"][0]["versions"][1]["version"]["campaign"].update(round="other"),
    ):
        with pytest.raises(ValueError):
            restore_campaign(mutate_snapshot(repo.load("c-1"), mutation))


def test_compare_and_save_rejects_rewriting_existing_creation_metadata(repo):
    from workshop_marketing_agent.campaign_state import _check_successor

    old = repo.load("c-1")
    start(repo)
    successor = repo.load("c-1")
    forged = successor.model_copy(update={"created_by": "another-creator"})
    # Independently well-formed JSON cannot authorize rewriting a previous snapshot.
    assert restore_campaign(dump_campaign(forged)).created_by == "another-creator"
    with pytest.raises(ValueError, match="extend"):
        _check_successor(old, forged)
    fresh = InMemoryCampaignRepository()
    assert fresh.compare_and_save(old, expected_revision=None)
    with pytest.raises(ValueError, match="extend"):
        fresh.compare_and_save(forged, expected_revision=0)
    assert fresh.load("c-1") == old


def test_missing_evidence_records_no_mutation(repo, workflow, imported):
    start(repo)
    result = attach(repo, workflow, imported)
    before = repo.load("c-1")
    cmd = command(repo, ApproveVersion, "missing-evidence", version_reference=result.version_reference)
    assert execute_command(repo, cmd).status == "rejected"
    assert repo.load("c-1") == before


def test_nonfinite_and_unknown_nested_json_are_rejected(repo, workflow, imported):
    start(repo)
    prepared(repo, workflow, imported)
    state = repo.load("c-1")
    with pytest.raises(ValueError):
        restore_campaign('{"state": NaN, "state_fingerprint": "x"}')
    mutation = lambda s: s["rounds"][0]["channels"][0]["versions"][0]["version"]["validation"].update(authorized=True)
    with pytest.raises(ValueError):
        restore_campaign(mutate_snapshot(state, mutation))


def test_review_orphan_round_requires_creation_receipt(repo):
    from copy import deepcopy

    start(repo)

    def append_orphan(state):
        extra = deepcopy(state["rounds"][0])
        extra["reference"] = "orphan-round"
        state["rounds"].append(extra)

    with pytest.raises(ValueError, match="creation receipt"):
        restore_campaign(mutate_snapshot(repo.load("c-1"), append_orphan))


def test_review_duplicate_selection_in_restored_command_rejected(repo):
    start(repo)

    def corrupt(state):
        receipt = state["receipts"][0]
        identity = json.loads(receipt["identity_json"])
        identity["command"]["selected_channels"] = ["google_business", "google_business"]
        receipt["identity_json"] = json.dumps(identity)
        receipt["input_fingerprint"] = _fingerprint_payload(identity)

    with pytest.raises(ValueError, match="unique"):
        restore_campaign(mutate_snapshot(repo.load("c-1"), corrupt))
