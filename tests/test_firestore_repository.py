"""The real SDK transactional decorator drives a strictly offline storage fake."""

import copy
import json
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Lock
from types import SimpleNamespace

import pytest
from google.api_core import exceptions as errors
from google.cloud.firestore_v1 import _helpers

from workshop_marketing_agent.application import StartRound, execute_command, new_campaign
from workshop_marketing_agent.campaign_state import InMemoryCampaignRepository, dump_campaign
from workshop_marketing_agent.firestore_repository import (
    CommitOutcomeUnknown, CorruptCampaignStorage, FirestoreCampaignRepository,
    InvalidCampaignState, InvalidStorageConfiguration, SnapshotTooLarge, StorageUnavailable,
    TRANSACTION_ATTEMPTS, campaign_document_id,
)
from test_application import command, start, prepared, attach
from test_submission import NOW, source, imported, workflow


class FakeDocument:
    def __init__(self, client, collection, id):
        self.client = client
        self.path = f"{collection}/{id}"
        self.id = id
        self._document_path = client._database_string + "/documents/" + self.path

    def get(self, *, transaction=None, retry=None, timeout=None):
        assert retry is None and timeout == 10
        if self.client.read_error:
            raise self.client.read_error
        with self.client.lock:
            data, revision = self.client.documents.get(self.path, (None, 0))
            data = copy.deepcopy(data)
            if transaction:
                assert not transaction._write_pbs
                self.client.reads[transaction.id][self.path] = revision
            barrier = self.client.barrier if self.client.barrier_reads < 2 and transaction else None
            if barrier:
                self.client.barrier_reads += 1
        if barrier:
            barrier.wait(timeout=5)
        reference = self.client.snapshot_reference or self
        return SimpleNamespace(exists=data is not None, reference=reference,
                               id=reference.id, to_dict=lambda: copy.deepcopy(data))


class FakeFirestoreClient:
    """RPC fake beneath the real SDK Transaction, decorator and protobuf encoding."""

    _database_string = "projects/demo-offline/databases/(default)"
    _rpc_metadata = ()

    def __init__(self):
        self._firestore_api = self
        self.documents = {}
        self.reads = {}
        self.lock = Lock()
        self.attempts = self.commits = self.aborts = self.barrier_reads = 0
        self.queued = []
        self.barrier = None
        self.read_error = self.begin_error = self.commit_error = self.rollback_error = None
        self.after_abort_begin_error = None
        self.snapshot_reference = None
        self.fail_after_write = False

    def collection(self, name):
        return SimpleNamespace(document=lambda id: FakeDocument(self, name, id))

    def begin_transaction(self, *, request, metadata):
        if self.begin_error:
            raise self.begin_error
        self.attempts += 1
        id = str(self.attempts).encode()
        self.reads[id] = {}
        return SimpleNamespace(transaction=id)

    def commit(self, *, request, metadata):
        writes = {}
        for write in request["writes"]:
            path = write.update.name.split("/documents/", 1)[1]
            assert not write.update_mask.field_paths  # Full replacement, not a merge.
            data = _helpers.decode_dict(write.update.fields, self)
            writes[path] = data
            self.queued.append(copy.deepcopy(data))
        with self.lock:
            if self.aborts:
                self.aborts -= 1
                if self.after_abort_begin_error:
                    self.begin_error = self.after_abort_begin_error
                raise errors.Aborted("private-provider-detail")
            if any(self.documents.get(path, (None, 0))[1] != revision
                   for path, revision in self.reads[request["transaction"]].items()):
                raise errors.Aborted("concurrent write")
            if self.commit_error and not self.fail_after_write:
                raise self.commit_error
            for path, data in writes.items():
                revision = self.documents.get(path, (None, 0))[1] + 1
                self.documents[path] = (data, revision)
                self.commits += 1
            if self.commit_error:
                raise self.commit_error
        return SimpleNamespace(write_results=[], commit_time=NOW)

    def rollback(self, *, request, metadata):
        if self.rollback_error:
            raise self.rollback_error


@pytest.fixture
def client():
    return FakeFirestoreClient()


@pytest.fixture
def initial():
    return new_campaign(campaign_reference="c-1", workshop_reference="synthetic-workshop-01",
                        actor_reference="teacher-1", now=NOW)


@pytest.fixture
def repo(client, initial):
    repository = FirestoreCampaignRepository(client, collection_name="pilotCampaigns")
    assert repository.compare_and_save(initial, expected_revision=None)
    return repository


def candidate(initial, id="next"):
    memory = InMemoryCampaignRepository()
    assert memory.compare_and_save(initial, expected_revision=None)
    result = execute_command(memory, command(memory, StartRound, id, purpose="Erinnerung für Eltern 🧘",
                                             selected_channels=("google_business",)))
    assert result.status == "committed"
    return memory.load("c-1")


def stored_data(client):
    return next(iter(client.documents.values()))[0]


def test_absent_create_update_and_identity(repo, initial, client):
    assert repo.load("absent") is None
    assert repo.load("c-1") == initial
    assert campaign_document_id("c-1") == campaign_document_id("c-1")
    assert campaign_document_id("c-1") != campaign_document_id("c-2")
    path = next(iter(client.documents))
    assert path == "pilotCampaigns/" + campaign_document_id("c-1")
    next_state = candidate(initial)
    assert repo.compare_and_save(next_state, expected_revision=0)
    assert repo.load("c-1") == next_state
    assert stored_data(client)["snapshot_json"] == dump_campaign(next_state)
    assert stored_data(client)["updated_at"] == next_state.updated_at


def test_full_workflow_and_replay_after_new_repository_instance(repo, client, workflow, imported):
    start(repo)
    result = prepared(repo, workflow, imported)
    prepared(repo, workflow, imported, "rausgegangen")
    before = repo.load("c-1")
    restarted = FirestoreCampaignRepository(client, collection_name="pilotCampaigns")
    assert restarted.load("c-1") == before
    from workshop_marketing_agent.application import PrepareSubmission
    cmd = command(restarted, PrepareSubmission, "prepare-google_business", version_reference=result.version_reference,
                  approval_reference=result.approval_reference)
    outcome = execute_command(restarted, cmd, evidence=imported)
    assert outcome.status == "replayed" and outcome.result == result
    assert restarted.load("c-1") == before
    assert '"regular_price":"120.00"' in stored_data(client)["snapshot_json"]
    assert "Ankündigung" in stored_data(client)["snapshot_json"]


def test_expected_conflicts_leave_bytes_unchanged(repo, initial, client):
    original = copy.deepcopy(client.documents)
    assert not repo.compare_and_save(initial, expected_revision=None)
    assert not repo.compare_and_save(initial, expected_revision=0)
    assert not repo.compare_and_save(candidate(initial), expected_revision=9)
    absent = FirestoreCampaignRepository(client, collection_name="empty")
    assert not absent.compare_and_save(candidate(initial), expected_revision=0)
    assert not absent.compare_and_save(candidate(initial), expected_revision=None)
    assert client.documents == original


def test_two_simultaneous_writers_only_one_commits(repo, initial, client):
    first, second = candidate(initial, "first"), candidate(initial, "second")
    client.barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(repo.compare_and_save, state, expected_revision=0) for state in (first, second)]
        assert sorted(f.result() for f in futures) == [False, True]
    assert repo.load("c-1") in (first, second)
    assert client.commits == 2  # initial create plus exactly one update


def test_callback_retries_fixed_snapshot_without_reexecuting_commands(repo, initial, client):
    state = candidate(initial)
    client.aborts = 2
    client.queued.clear()
    assert repo.compare_and_save(state, expected_revision=0)
    assert len(client.queued) == 3
    assert all(data == client.queued[0] for data in client.queued)
    assert client.commits == 2
    assert repo.load("c-1") == state


@pytest.mark.parametrize("field,value", [
    ("campaign_reference", "another"), ("workshop_reference", "another"),
    ("revision", 9), ("revision", True), ("snapshot_fingerprint", "0" * 64),
    ("storage_schema_version", "unsupported"), ("updated_at", NOW + timedelta(seconds=1)),
    ("updated_at", NOW.replace(tzinfo=None)), ("snapshot_json", "{broken-json"),
    ("extra_field", "unknown"),
])
def test_invalid_metadata_load_and_update_fail_without_writes(repo, initial, client, field, value):
    stored_data(client)[field] = value
    corrupt = copy.deepcopy(client.documents)
    with pytest.raises(CorruptCampaignStorage):
        repo.load("c-1")
    with pytest.raises(CorruptCampaignStorage):
        repo.compare_and_save(candidate(initial), expected_revision=0)
    assert client.documents == corrupt


def test_document_reference_and_requested_reference_are_bound(repo, client, initial):
    original = copy.deepcopy(client.documents)
    client.snapshot_reference = FakeDocument(client, "otherCollection", campaign_document_id("c-1"))
    with pytest.raises(CorruptCampaignStorage):
        repo.load("c-1")
    with pytest.raises(CorruptCampaignStorage):
        repo.compare_and_save(candidate(initial), expected_revision=0)
    client.snapshot_reference = None
    client.documents["pilotCampaigns/" + campaign_document_id("another")] = copy.deepcopy(next(iter(original.values())))
    with pytest.raises(CorruptCampaignStorage):
        repo.load("another")


@pytest.mark.parametrize("mutation", [
    lambda e: e["state"].update(schema_version="unknown"),
    lambda e: e.update(state_fingerprint="0" * 64),
    lambda e: e["state"].update(created_by="tampered"),
])
def test_corrupt_domain_snapshots_rejected(repo, initial, client, mutation):
    data = stored_data(client)
    envelope = json.loads(data["snapshot_json"])
    mutation(envelope)
    data["snapshot_json"] = json.dumps(envelope)
    unchanged = copy.deepcopy(client.documents)
    with pytest.raises(CorruptCampaignStorage):
        repo.compare_and_save(candidate(initial), expected_revision=0)
    assert client.documents == unchanged


def test_rewritten_history_rejected(repo, initial, client):
    forged = candidate(initial).model_copy(update={"created_by": "changed-owner"})
    original = copy.deepcopy(client.documents)
    with pytest.raises(InvalidCampaignState, match="history"):
        repo.compare_and_save(forged, expected_revision=0)
    assert client.documents == original


def test_invalid_candidate_never_leaks_serializer_warnings(repo, initial, client, recwarn, capsys):
    forged = initial.model_copy(update={"revision": "SECRET_WORKSHOP_COPY"})
    original = copy.deepcopy(client.documents)
    with pytest.raises(InvalidCampaignState) as caught:
        repo.compare_and_save(forged, expected_revision=None)
    assert "SECRET_WORKSHOP_COPY" not in "".join(traceback.format_exception(caught.value))
    assert not recwarn.list
    assert capsys.readouterr() == ("", "")
    assert client.documents == original


@pytest.mark.parametrize("name", ["", ".", "..", "__reserved__", "a/b", "x" * 101, "ümlaut", None])
def test_invalid_collection_names(client, name):
    with pytest.raises(InvalidStorageConfiguration):
        FirestoreCampaignRepository(client, collection_name=name)
    assert client.attempts == 0


@pytest.mark.parametrize("reference", ["../x", "a/b", "", "x" * 129, None])
def test_invalid_reference_never_becomes_a_path(repo, client, reference):
    before = copy.deepcopy(client.documents)
    with pytest.raises(InvalidCampaignState):
        repo.load(reference)
    assert client.documents == before


def test_multibyte_limit_uses_utf8_and_includes_entire_envelope(repo, client, initial, monkeypatch):
    from workshop_marketing_agent import firestore_repository as adapter

    state = candidate(initial)
    raw = dump_campaign(state)
    size = len(raw.encode("utf-8"))
    assert size > len(raw)
    before = copy.deepcopy(client.documents)
    monkeypatch.setattr(adapter, "MAX_SNAPSHOT_BYTES", size - 1)
    with pytest.raises(SnapshotTooLarge):
        repo.compare_and_save(state, expected_revision=0)
    assert client.documents == before
    monkeypatch.setattr(adapter, "MAX_SNAPSHOT_BYTES", size)
    assert repo.compare_and_save(state, expected_revision=0)
    assert repo.load("c-1") == state


def test_real_limit_rejects_history_without_truncation(repo, workflow, imported, client):
    from workshop_marketing_agent.firestore_repository import MAX_SNAPSHOT_BYTES

    start(repo)
    attach(repo, workflow, imported)
    state = repo.load("c-1")
    round_ = state.rounds[0]
    channel = round_.channels[0]
    record = channel.versions[0].model_copy(update={"original_description_html": "ü" * MAX_SNAPSHOT_BYTES})
    channel = channel.model_copy(update={"versions": (record,)})
    round_ = round_.model_copy(update={"channels": (channel, round_.channels[1])})
    oversized = state.model_copy(update={"rounds": (round_,)})
    before = copy.deepcopy(client.documents)
    with pytest.raises(SnapshotTooLarge):
        repo.compare_and_save(oversized, expected_revision=state.revision)
    assert client.documents == before


@pytest.mark.parametrize("phase", ["read_error", "begin_error"])
def test_unavailable_before_write_is_safe(repo, initial, client, phase):
    setattr(client, phase, errors.ServiceUnavailable("SECRET private-provider-detail"))
    before = copy.deepcopy(client.documents)
    with pytest.raises(StorageUnavailable) as caught:
        repo.compare_and_save(candidate(initial), expected_revision=0)
    assert "SECRET" not in "".join(traceback.format_exception(caught.value))
    assert client.documents == before


def test_read_failure_is_sanitized(repo, client):
    client.read_error = errors.PermissionDenied("SECRET customer project details")
    with pytest.raises(StorageUnavailable) as caught:
        repo.load("c-1")
    assert "SECRET" not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize("fail_after_write", [False, True])
def test_commit_transport_failure_is_unknown_and_never_retried(repo, initial, client, fail_after_write):
    client.commit_error = errors.DeadlineExceeded("SECRET timeout")
    client.fail_after_write = fail_after_write
    attempts = client.attempts
    with pytest.raises(CommitOutcomeUnknown) as caught:
        repo.compare_and_save(candidate(initial), expected_revision=0)
    assert client.attempts == attempts + 1
    assert "SECRET" not in "".join(traceback.format_exception(caught.value))
    assert repo.load("c-1").revision == (1 if fail_after_write else 0)


def test_aborted_retry_exhaustion_is_confirmed_unavailable(repo, initial, client):
    client.aborts = TRANSACTION_ATTEMPTS
    before = copy.deepcopy(client.documents)
    attempts = client.attempts
    with pytest.raises(StorageUnavailable):
        repo.compare_and_save(candidate(initial), expected_revision=0)
    assert client.attempts == attempts + TRANSACTION_ATTEMPTS
    assert client.documents == before


def test_explicit_commit_rejection(repo, initial, client):
    client.commit_error = errors.PermissionDenied("SECRET")
    before = copy.deepcopy(client.documents)
    with pytest.raises(StorageUnavailable):
        repo.compare_and_save(candidate(initial), expected_revision=0)
    assert client.documents == before


def test_rollback_failure_cannot_hide_uncertain_commit(repo, initial, client):
    client.commit_error = errors.DeadlineExceeded("SECRET uncertain")
    client.rollback_error = errors.PermissionDenied("SECRET rollback")
    with pytest.raises(CommitOutcomeUnknown) as caught:
        repo.compare_and_save(candidate(initial), expected_revision=0)
    assert "SECRET" not in "".join(traceback.format_exception(caught.value))


def test_import_does_not_initialize_client_or_discover_credentials():
    import os
    import subprocess
    import sys

    code = """
import google.auth
import google.cloud.firestore_v1
from unittest.mock import patch

def forbidden(*args, **kwargs):
    raise AssertionError('unexpected client, credentials or network')

with patch('google.auth.default', forbidden), patch('google.cloud.firestore_v1.Client', forbidden), \
     patch('socket.socket.connect', forbidden), patch('socket.create_connection', forbidden):
    import workshop_marketing_agent.firestore_repository
print('import-safe')
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                            env={"PATH": os.defpath, "PYTHONNOUSERSITE": "1"}, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "import-safe"


def test_review_aborted_write_then_failed_retry_begin_is_not_unknown(repo, initial, client):
    client.aborts = 1
    client.after_abort_begin_error = errors.ServiceUnavailable("SECRET retry begin")
    before = copy.deepcopy(client.documents)
    with pytest.raises(StorageUnavailable):
        repo.compare_and_save(candidate(initial), expected_revision=0)
    assert client.documents == before


@pytest.mark.parametrize("api_error,code", [(errors.Aborted, "ABORTED"), (errors.PermissionDenied, "PERMISSION_DENIED")])
def test_realistic_grpc_cause_preserves_confirmed_rejection(repo, initial, client, api_error, code):
    import grpc

    class RPCFailure(grpc.RpcError):
        def code(self):
            return getattr(grpc.StatusCode, code)

    try:
        try:
            raise RPCFailure("SECRET rpc detail")
        except RPCFailure as error:
            raise api_error("SECRET mapped detail") from error
    except api_error as error:
        client.commit_error = error
    before = copy.deepcopy(client.documents)
    with pytest.raises(StorageUnavailable) as caught:
        repo.compare_and_save(candidate(initial), expected_revision=0)
    assert "SECRET" not in "".join(traceback.format_exception(caught.value))
    assert client.documents == before
