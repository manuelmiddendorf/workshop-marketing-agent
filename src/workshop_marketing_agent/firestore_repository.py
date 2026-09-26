"""Atomic Firestore storage for server-owned pilot campaigns; no client creation."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

import grpc
from google.api_core import exceptions as google_errors
from google.cloud.firestore_v1 import Client, Transaction, transactional
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from .campaign import Reference
from .campaign_state import CampaignState, _check_successor, dump_campaign, restore_campaign
from .revision import _aware_utc

# Leave over 140 KiB for metadata, field names and document-name overhead.
MAX_SNAPSHOT_BYTES = 900_000
TRANSACTION_ATTEMPTS = 3
READ_TIMEOUT_SECONDS = 10
STORAGE_SCHEMA_VERSION = "pilot-campaign-storage.v1"
_REFERENCE = TypeAdapter(Reference)


class CampaignStorageError(Exception):
    """Public errors contain only fixed, safe messages."""


class InvalidStorageConfiguration(CampaignStorageError):
    pass


class InvalidCampaignState(CampaignStorageError):
    pass


class SnapshotTooLarge(InvalidCampaignState):
    pass


class CorruptCampaignStorage(CampaignStorageError):
    pass


class StorageUnavailable(CampaignStorageError):
    """The operation failed without a possibly committed candidate write."""


class CommitOutcomeUnknown(CampaignStorageError):
    """A candidate may have committed. Reload/reconcile; never rerun commands blindly."""


class _StoredDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    storage_schema_version: str
    campaign_reference: Reference
    workshop_reference: Reference
    revision: int = Field(ge=0)
    snapshot_fingerprint: str
    updated_at: datetime
    snapshot_json: str


def campaign_document_id(campaign_reference: str) -> str:
    """Treat the opaque reference as data, never as a Firestore path."""
    try:
        reference = _REFERENCE.validate_python(campaign_reference)
    except ValueError:
        raise InvalidCampaignState("Invalid campaign reference") from None
    return "campaign-" + hashlib.sha256(reference.encode("utf-8")).hexdigest()


def _candidate(state: CampaignState) -> tuple[CampaignState, dict[str, object]]:
    try:
        # Reject unchecked model_copy values before dump_campaign can emit them
        # in serializer warnings. Per-call handling avoids global warning filters.
        state = CampaignState.model_validate_json(json.dumps(
            state.model_dump(mode="json", warnings="error"), allow_nan=False))
        raw = dump_campaign(state)
        validated = restore_campaign(raw)
        size = len(raw.encode("utf-8"))
    except (ValueError, TypeError, AttributeError, OverflowError, RecursionError):
        raise InvalidCampaignState("Invalid campaign snapshot") from None
    if size > MAX_SNAPSHOT_BYTES:
        raise SnapshotTooLarge("Campaign snapshot exceeds the storage byte limit")
    return validated, {
        "storage_schema_version": STORAGE_SCHEMA_VERSION,
        "campaign_reference": validated.campaign_reference,
        "workshop_reference": validated.workshop_reference,
        "revision": validated.revision,
        "snapshot_fingerprint": json.loads(raw)["state_fingerprint"],
        "updated_at": validated.updated_at,
        "snapshot_json": raw,
    }


def _restore_document(snapshot, reference, requested: str) -> CampaignState | None:
    if not snapshot.exists:
        return None
    try:
        if snapshot.reference.path != reference.path or snapshot.id != reference.id:
            raise ValueError("Document identity mismatch")
        stored = _StoredDocument.model_validate(snapshot.to_dict())
        if stored.storage_schema_version != STORAGE_SCHEMA_VERSION:
            raise ValueError("Unsupported storage schema")
        if len(stored.snapshot_json.encode("utf-8")) > MAX_SNAPSHOT_BYTES:
            raise ValueError("Oversized stored snapshot")
        state = restore_campaign(stored.snapshot_json)
        _aware_utc(stored.updated_at, field="storage update time")
        if (state.campaign_reference != requested or stored.campaign_reference != requested
                or stored.workshop_reference != state.workshop_reference
                or stored.revision != state.revision or stored.updated_at != state.updated_at
                or stored.snapshot_fingerprint != json.loads(stored.snapshot_json)["state_fingerprint"]
                or dump_campaign(state) != stored.snapshot_json):
            raise ValueError("Storage metadata or canonical snapshot mismatch")
    except (ValueError, TypeError, AttributeError, OverflowError, RecursionError):
        raise CorruptCampaignStorage("Invalid or unsupported stored campaign") from None
    return state


def _confirmed_rejection(error: Exception) -> bool:
    """Do not misclassify a commit error hidden by an SDK rollback failure."""
    known = (google_errors.Aborted, google_errors.PermissionDenied, google_errors.Unauthenticated,
             google_errors.InvalidArgument, google_errors.FailedPrecondition,
             google_errors.AlreadyExists, google_errors.NotFound)
    pending = [error]
    visited = set()
    while pending:
        item = pending.pop()
        if id(item) in visited:
            continue
        visited.add(id(item))
        # The SDK wraps exhausted Aborted retries in ValueError.
        rpc_rejection = (isinstance(item, grpc.RpcError)
                         and item.code() in {kind.grpc_status_code for kind in known})
        if not isinstance(item, known) and not rpc_rejection and not (
                type(item) is ValueError and isinstance(item.__cause__, google_errors.Aborted)):
            return False
        pending.extend(e for e in (item.__cause__, item.__context__) if e is not None)
    return True


class _CommitTrackingTransaction(Transaction):
    """Observe the SDK commit boundary, including failures before a retried callback.

    The documented decorator uses _commit, not public batch commit. Keep this
    small protected-method override covered against the locked official SDK.
    """

    may_have_committed = False

    def _commit(self):
        self.may_have_committed = bool(self._write_pbs)
        try:
            return super()._commit()
        except Exception as error:
            if _confirmed_rejection(error):
                self.may_have_committed = False
            raise


class FirestoreCampaignRepository:
    """An injected synchronous official client, one explicitly named root collection."""

    def __init__(self, client: Client, *, collection_name: str) -> None:
        if (not isinstance(collection_name, str)
                or re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,99}", collection_name) is None):
            raise InvalidStorageConfiguration("Collection must be a simple explicit ASCII name of 1–100 characters")
        if client is None:
            raise InvalidStorageConfiguration("An explicitly configured Firestore client is required")
        self._client = client
        self._collection_name = collection_name

    def _reference(self, campaign_reference: str):
        document_id = campaign_document_id(campaign_reference)
        return self._client.collection(self._collection_name).document(document_id)

    def load(self, campaign_reference: str) -> CampaignState | None:
        # Validate before accessing the client, even for an absent document.
        campaign_document_id(campaign_reference)
        try:
            reference = self._reference(campaign_reference)
            snapshot = reference.get(retry=None, timeout=READ_TIMEOUT_SECONDS)
            return _restore_document(snapshot, reference, campaign_reference)
        except CampaignStorageError:
            raise
        except Exception:
            raise StorageUnavailable("Campaign storage read is unavailable") from None

    def compare_and_save(self, state: CampaignState, *, expected_revision: int | None) -> bool:
        if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 0):
            raise InvalidCampaignState("Expected revision must be a nonnegative integer or None")
        candidate, document = _candidate(state)
        # Fix candidate bytes before the retryable callback. Never execute a command here.
        transaction = None

        @transactional
        def save(transaction):
            snapshot = reference.get(transaction=transaction, retry=None, timeout=READ_TIMEOUT_SECONDS)
            existing = _restore_document(snapshot, reference, candidate.campaign_reference)
            if expected_revision is None:
                if existing is not None or candidate.revision != 0:
                    return False
            else:
                if (existing is None or existing.revision != expected_revision
                        or candidate.revision != expected_revision + 1):
                    return False
                try:
                    _check_successor(existing, candidate)
                except ValueError:
                    raise InvalidCampaignState("Candidate rewrites campaign history") from None
            transaction.set(reference, document, merge=False)
            return True

        try:
            reference = self._reference(candidate.campaign_reference)
            transaction = _CommitTrackingTransaction(self._client, max_attempts=TRANSACTION_ATTEMPTS)
            return save(transaction)
        except CampaignStorageError:
            raise
        except Exception as error:
            if transaction is not None and transaction.may_have_committed:
                raise CommitOutcomeUnknown("Campaign commit outcome is unknown; reload and reconcile") from None
            raise StorageUnavailable("Campaign storage operation is unavailable") from None
