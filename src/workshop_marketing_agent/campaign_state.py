"""Versioned JSON snapshots and a narrow optimistic-concurrency storage boundary."""

from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from datetime import datetime
from threading import Lock
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .campaign import Reference, build_campaign_link
from .revision import ChannelId, DraftApproval, DraftVersion, _aware_utc, _fingerprint_payload
from .submission import PreparedSubmission, _google_payload, _rausgegangen_payload
from .validation import Diagnostic

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class FrozenState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class VersionRecord(FrozenState):
    reference: Reference
    parent_reference: Reference | None
    fingerprint: Digest
    version: DraftVersion
    original_description_html: str


class ApprovalRecord(FrozenState):
    reference: Reference
    version_reference: Reference
    approval: DraftApproval


class SubmissionRecord(FrozenState):
    reference: Reference
    version_reference: Reference
    approval_reference: Reference
    fingerprint: Digest
    package: PreparedSubmission


class ChannelState(FrozenState):
    channel: ChannelId
    enabled: bool
    versions: tuple[VersionRecord, ...] = ()
    current_version: Reference | None = None
    approvals: tuple[ApprovalRecord, ...] = ()
    submissions: tuple[SubmissionRecord, ...] = ()
    # Historical observations, never authorization or proof of current readiness.
    last_status: Literal["review_required", "revised", "approved", "ready", "ready_to_copy", "outdated"] = "review_required"
    diagnostics: tuple[Diagnostic, ...] = ()
    updated_at: datetime


class MarketingRound(FrozenState):
    reference: Reference
    purpose: Annotated[str, Field(min_length=1, max_length=500)]
    created_by: Reference
    created_at: datetime
    channels: tuple[ChannelState, ChannelState]


class CommandResult(FrozenState):
    campaign_reference: Reference
    command_id: Reference
    revision: Annotated[int, Field(ge=1)]
    round_reference: Reference
    channel: ChannelId | None
    status: Literal["started", "selected", "enabled", "disabled", "revised", "review_required", "approved", "ready", "ready_to_copy", "outdated"]
    version_reference: Reference | None = None
    approval_reference: Reference | None = None
    submission_reference: Reference | None = None
    diagnostics: tuple[Diagnostic, ...] = ()


class CommandReceipt(FrozenState):
    command_id: Reference
    # Canonical command JSON plus the evidence digest, not credentials/client objects.
    identity_json: str
    input_fingerprint: Digest
    result: CommandResult


class CampaignState(FrozenState):
    schema_version: Literal["pilot-campaign.v1"] = "pilot-campaign.v1"
    campaign_reference: Reference
    workshop_reference: Reference
    revision: Annotated[int, Field(ge=0)] = 0
    created_by: Reference
    created_at: datetime
    updated_at: datetime
    rounds: tuple[MarketingRound, ...] = ()
    receipts: tuple[CommandReceipt, ...] = ()

    @model_validator(mode="after")
    def consistent(self) -> CampaignState:
        _aware_values(self)
        if self.created_at > self.updated_at:
            raise ValueError("Campaign timestamps are inconsistent")
        _unique([r.reference for r in self.rounds])
        if len(self.receipts) != self.revision:
            raise ValueError("Every aggregate revision must have one command receipt")
        _unique([r.command_id for r in self.receipts])
        for round_ in self.rounds:
            if not round_.purpose.strip() or not self.created_at <= round_.created_at <= self.updated_at:
                raise ValueError("Invalid round creation metadata")
            if {c.channel for c in round_.channels} != {"google_business", "rausgegangen"}:
                raise ValueError("A round must contain each pilot channel exactly once")
            for channel in round_.channels:
                self._check_channel(round_, channel)
        previous_time = self.created_at
        for index, receipt in enumerate(self.receipts, 1):
            previous_time = self._check_receipt(receipt, index, previous_time)
        created_records = {r.command_id: r.result for r in self.receipts}
        for round_ in self.rounds:
            starts = [r for r in self.receipts
                      if r.result.round_reference == round_.reference and r.result.status == "started"]
            if len(starts) != 1:
                raise ValueError("Round must have exactly one matching creation receipt")
            for channel in round_.channels:
                for record in (*channel.versions, *channel.approvals, *channel.submissions):
                    result = created_records.get(record.reference)
                    field = ("version_reference" if isinstance(record, VersionRecord) else
                             "approval_reference" if isinstance(record, ApprovalRecord) else "submission_reference")
                    if (result is None or getattr(result, field) != record.reference
                            or result.round_reference != round_.reference or result.channel != channel.channel):
                        raise ValueError("Historical record has no matching creating command receipt")
        return self

    def _check_receipt(self, receipt: CommandReceipt, index: int, previous_time: datetime) -> datetime:
        # Deferred import: the application command schema uses these state value types.
        from .application import COMMAND_ADAPTER

        identity = _json_object(receipt.identity_json)
        if (set(identity) != {"command", "evidence_fingerprint"}
                or not isinstance(identity["command"], dict)
                or "expected_revision" in identity["command"]
                or receipt.input_fingerprint != _fingerprint_payload(identity)):
            raise ValueError("Command input fingerprint or identity mismatch")
        digest = identity["evidence_fingerprint"]
        if digest is not None and (not isinstance(digest, str) or len(digest) != 64
                                   or any(c not in "0123456789abcdef" for c in digest)):
            raise ValueError("Invalid evidence fingerprint")
        raw_command = identity["command"] | {"expected_revision": index - 1}
        command = COMMAND_ADAPTER.validate_json(json.dumps(raw_command))
        _no_ignored_fields(raw_command, command.model_dump(mode="json"))
        _aware_utc(command.now, field="receipt command time")
        result = receipt.result
        channel_id = getattr(command, "channel", None)
        if (result.revision != index or result.command_id != receipt.command_id
                or result.campaign_reference != self.campaign_reference
                or command.command_id != receipt.command_id
                or command.campaign_reference != self.campaign_reference
                or command.workshop_reference != self.workshop_reference
                or command.round_reference != result.round_reference or channel_id != result.channel
                or not previous_time <= command.now <= self.updated_at):
            raise ValueError("Command receipt binding mismatch")
        round_ = next((r for r in self.rounds if r.reference == result.round_reference), None)
        if round_ is None:
            raise ValueError("Receipt references a missing round")
        if command.kind == "start_round":
            if (result.status != "started" or any((result.version_reference, result.approval_reference,
                                                  result.submission_reference))
                    or round_.created_at != command.now or round_.created_by != command.actor_reference
                    or round_.purpose != command.purpose):
                raise ValueError("Round receipt does not match round creation")
            return command.now
        if command.now < round_.created_at:
            raise ValueError("Command predates its round")
        if command.kind not in ("select_version", "set_channel_enabled") and digest is None:
            raise ValueError("Command receipt requires an evidence fingerprint")
        channel = next(c for c in round_.channels if c.channel == channel_id)
        version = next((v for v in channel.versions if v.reference == result.version_reference), None)
        approval = next((a for a in channel.approvals if a.reference == result.approval_reference), None)
        package = next((p for p in channel.submissions if p.reference == result.submission_reference), None)
        for ref, record in ((result.version_reference, version), (result.approval_reference, approval),
                            (result.submission_reference, package)):
            if ref is not None and record is None:
                raise ValueError("Receipt contains a dangling result reference")
        if approval is not None and approval.version_reference != result.version_reference:
            raise ValueError("Receipt approval and version do not match")
        if package is not None and (package.version_reference != result.version_reference
                                   or package.approval_reference != result.approval_reference):
            raise ValueError("Receipt package bindings do not match")
        if command.kind != "prepare_submission" and result.submission_reference is not None:
            raise ValueError("Unexpected submission reference")
        if command.kind not in ("approve_version", "prepare_submission") and result.approval_reference is not None:
            raise ValueError("Unexpected approval reference")
        if command.kind == "set_channel_enabled":
            if result.version_reference is not None or result.status != ("enabled" if command.enabled else "disabled"):
                raise ValueError("Invalid channel toggle result")
        elif command.kind in ("attach_draft", "direct_revision", "ai_revision", "bind_link"):
            if result.status not in ("revised", "review_required"):
                raise ValueError("Invalid revision result status")
            if version is None:
                if command.kind != "ai_revision" or result.status != "review_required":
                    raise ValueError("Missing resulting draft version")
            else:
                expected_parent = getattr(command, "version_reference", None)
                expected_origin = {"attach_draft": "generation", "direct_revision": "human_edit",
                                   "ai_revision": "ai_revision", "bind_link": "campaign_link"}[command.kind]
                if (version.reference != command.command_id or version.parent_reference != expected_parent
                        or version.version.created_at != command.now
                        or version.version.revision_origin != expected_origin
                        or result.diagnostics != version.version.validation.diagnostics
                        or result.status != ("revised" if version.version.validation.status == "valid" else "review_required")):
                    raise ValueError("Version creation receipt mismatch")
                if command.kind == "attach_draft" and (
                        version.version.content != command.content or version.version.workshop_facts != command.facts
                        or version.version.generation_metadata != command.metadata
                        or version.version.selected_image_reference != command.selected_image_reference):
                    raise ValueError("Attached draft differs from its command")
                if command.kind == "bind_link" and version.version.campaign != command.campaign:
                    raise ValueError("Bound link differs from its command")
        else:
            if version is None or result.version_reference != command.version_reference:
                raise ValueError("Result does not reference the commanded version")
            if command.kind == "select_version":
                if result.status != "selected":
                    raise ValueError("Invalid selection result")
            elif command.kind == "approve_version":
                if approval is None:
                    if result.status not in ("outdated", "review_required"):
                        raise ValueError("Invalid failed approval result")
                elif (result.status != "approved" or approval.reference != command.command_id
                      or approval.approval.approved_at != command.now
                      or approval.approval.approving_person_reference != command.actor_reference):
                    raise ValueError("Approval creation receipt mismatch")
            elif command.kind == "prepare_submission":
                if approval is None or result.approval_reference != command.approval_reference:
                    raise ValueError("Preparation receipt references another approval")
                if package is None:
                    if result.status not in ("outdated", "review_required"):
                        raise ValueError("Invalid blocked preparation result")
                elif (result.status != ("ready" if channel_id == "google_business" else "ready_to_copy")
                      or package.reference != command.command_id or package.package.checked_at != command.now):
                    raise ValueError("Preparation creation receipt mismatch")
        return command.now

    def _check_channel(self, round_: MarketingRound, channel: ChannelState) -> None:
        if not round_.created_at <= channel.updated_at <= self.updated_at:
            raise ValueError("Channel timestamps are inconsistent")
        versions: dict[str, VersionRecord] = {}
        for record in channel.versions:
            version = record.version
            if record.reference in versions or record.fingerprint != version.fingerprint:
                raise ValueError("Duplicate version reference or corrupt fingerprint")
            if (version.workshop_id != self.workshop_reference or version.channel != channel.channel
                    or version.content.channel != channel.channel
                    or version.workshop_facts.workshop_id != version.workshop_id
                    or version.workshop_facts.source_version != version.source_version
                    or version.workshop_facts.booking_url != version.canonical_booking_url):
                raise ValueError("Version identity or canonical fact binding mismatch")
            if not round_.created_at <= version.created_at <= channel.updated_at:
                raise ValueError("Version timestamp is outside its channel history")
            parent = versions.get(record.parent_reference)
            if record.parent_reference is None:
                if version.parent_fingerprint is not None or version.revision_origin != "generation":
                    raise ValueError("Invalid generation parent")
            elif (parent is None or version.parent_fingerprint != parent.fingerprint
                  or parent.version.created_at > version.created_at
                  or record.original_description_html != parent.original_description_html):
                raise ValueError("Invalid or dangling version parent")
            if version.campaign is not None:
                campaign = version.campaign
                if (campaign.workshop != self.workshop_reference or campaign.campaign != self.campaign_reference
                        or campaign.round != round_.reference or campaign.channel != channel.channel
                        or version.final_booking_url != build_campaign_link(version.canonical_booking_url, campaign)):
                    raise ValueError("Campaign link binding mismatch")
            elif version.final_booking_url is not None:
                raise ValueError("A final URL requires campaign metadata")
            if version.generation_metadata is not None:
                if (version.generation_metadata.source_version != version.source_version
                        or version.generation_metadata.generated_at > version.created_at):
                    raise ValueError("Generation metadata does not match the version")
            if (version.validation.status == "valid") != (not version.validation.blocking_diagnostics):
                raise ValueError("Version diagnostic status is inconsistent")
            versions[record.reference] = record
        if (channel.current_version is None) != (not versions) or (
                channel.current_version is not None and channel.current_version not in versions):
            raise ValueError("Invalid current version reference")
        approvals: dict[str, ApprovalRecord] = {}
        for record in channel.approvals:
            version = versions.get(record.version_reference)
            if (record.reference in approvals or version is None
                    or version.version.validation.status != "valid"
                    or not record.approval.matches(version.version)
                    or not version.version.created_at <= record.approval.approved_at <= channel.updated_at):
                raise ValueError("Approval binding mismatch")
            approvals[record.reference] = record
        _unique([r.reference for r in channel.submissions])
        for record in channel.submissions:
            version = versions.get(record.version_reference)
            approval = approvals.get(record.approval_reference)
            if (version is None or approval is None or approval.version_reference != record.version_reference
                    or record.package.version != version.version or record.package.approval != approval.approval
                    or record.fingerprint != record.package.fingerprint
                    or not approval.approval.approved_at <= record.package.checked_at <= channel.updated_at):
                raise ValueError("Prepared package binding mismatch")
            mapping = _google_payload if channel.channel == "google_business" else _rausgegangen_payload
            if record.package.payload != mapping(version.version):
                raise ValueError("Prepared payload differs from its exact approved version")


def _unique(values: list[str]) -> None:
    if len(values) != len(set(values)):
        raise ValueError("Duplicate references")


def _aware_values(value: object) -> None:
    if isinstance(value, datetime):
        _aware_utc(value, field="stored timestamp")
    elif isinstance(value, BaseModel):
        for name in type(value).model_fields:
            _aware_values(getattr(value, name))
    elif is_dataclass(value):
        for field in fields(value):
            _aware_values(getattr(value, field.name))
    elif isinstance(value, (list, tuple)):
        for item in value:
            _aware_values(item)


def _json_object(raw: str) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def invalid_constant(value):
        raise ValueError("Non-finite JSON value")

    result = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_constant)
    if not isinstance(result, dict):
        raise ValueError("Expected a JSON object")
    return result


def _no_ignored_fields(raw: object, parsed: object) -> None:
    # Standard-library dataclasses otherwise ignore extra JSON properties.
    if isinstance(raw, dict) and isinstance(parsed, dict):
        if raw.keys() - parsed.keys():
            raise ValueError("Unknown stored field")
        for key in raw:
            _no_ignored_fields(raw[key], parsed[key])
    elif isinstance(raw, list) and isinstance(parsed, list):
        for left, right in zip(raw, parsed, strict=True):
            _no_ignored_fields(left, right)


def dump_campaign(state: CampaignState) -> str:
    data = state.model_dump(mode="json")
    # Validate even objects made with unchecked model_copy updates.
    parsed = CampaignState.model_validate_json(json.dumps(data, allow_nan=False))
    data = parsed.model_dump(mode="json")
    return json.dumps({"state": data, "state_fingerprint": _fingerprint_payload(data)},
                      ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def restore_campaign(raw: str) -> CampaignState:
    envelope = _json_object(raw)
    if set(envelope) != {"state", "state_fingerprint"} or not isinstance(envelope["state"], dict):
        raise ValueError("Invalid campaign envelope")
    data = envelope["state"]
    if data.get("schema_version") != "pilot-campaign.v1":
        raise ValueError("Missing or unsupported campaign schema version")
    if envelope["state_fingerprint"] != _fingerprint_payload(data):
        raise ValueError("Campaign snapshot fingerprint mismatch")
    state = CampaignState.model_validate_json(json.dumps(data, allow_nan=False))
    _no_ignored_fields(data, state.model_dump(mode="json"))
    return state


class CampaignRepository(Protocol):
    def load(self, campaign_reference: str) -> CampaignState | None: ...

    def compare_and_save(self, state: CampaignState, *, expected_revision: int | None) -> bool:
        """Atomically save iff stored revision matches; None requires absence."""
        ...


class InMemoryCampaignRepository:
    """Isolated JSON copies with a lock; no production storage or cross-process lock."""

    def __init__(self) -> None:
        self._snapshots: dict[str, str] = {}
        self._lock = Lock()

    def load(self, campaign_reference: str) -> CampaignState | None:
        with self._lock:
            raw = self._snapshots.get(campaign_reference)
        return restore_campaign(raw) if raw is not None else None

    def compare_and_save(self, state: CampaignState, *, expected_revision: int | None) -> bool:
        raw = dump_campaign(state)
        with self._lock:
            existing = self._snapshots.get(state.campaign_reference)
            if expected_revision is None:
                if existing is not None or state.revision != 0:
                    return False
            else:
                if (existing is None or restore_campaign(existing).revision != expected_revision
                        or state.revision != expected_revision + 1):
                    return False
            if existing is not None:
                _check_successor(restore_campaign(existing), state)
            self._snapshots[state.campaign_reference] = raw
        return True


def _check_successor(previous: CampaignState, updated: CampaignState) -> None:
    """A compare-and-save cannot rewrite historical records or campaign identity."""
    if (previous.workshop_reference != updated.workshop_reference
            or previous.created_by != updated.created_by or previous.created_at != updated.created_at
            or updated.updated_at < previous.updated_at or updated.receipts[:-1] != previous.receipts
            or len(updated.rounds) < len(previous.rounds)):
        raise ValueError("Save must extend the existing campaign history")
    for old, new in zip(previous.rounds, updated.rounds):
        if (old.reference != new.reference or old.purpose != new.purpose
                or old.created_by != new.created_by or old.created_at != new.created_at):
            raise ValueError("Existing round creation metadata is immutable")
        for old_channel in old.channels:
            new_channel = next(c for c in new.channels if c.channel == old_channel.channel)
            for field in ("versions", "approvals", "submissions"):
                before = getattr(old_channel, field)
                after = getattr(new_channel, field)
                if after[:len(before)] != before:
                    raise ValueError("Existing channel history is immutable")
