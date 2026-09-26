"""Small pilot application commands; authentication and production storage are external."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, model_validator

from .campaign import CampaignMetadata
from .campaign_state import (
    ApprovalRecord, CampaignRepository, CampaignState, ChannelState, CommandReceipt,
    CommandResult, FrozenState, MarketingRound, Reference, SubmissionRecord, VersionRecord,
    dump_campaign, restore_campaign,
)
from .feed import FeedImportResult
from .generation import DraftClient, GenerationMetadata, WorkshopFacts
from .revision import (
    ChannelDraft, ChannelId, DraftVersion, DraftWorkflow, _aware_utc, _fingerprint_payload, _new_version,
    approve_draft_version, bind_campaign_link, revise_draft_directly, revise_draft_with_ai,
)
from .submission import prepare_google_submission, prepare_rausgegangen_submission


class Command(FrozenState):
    campaign_reference: Reference
    workshop_reference: Reference
    round_reference: Reference
    command_id: Reference
    actor_reference: Reference
    now: datetime
    expected_revision: Annotated[int, Field(ge=0)]


class StartRound(Command):
    kind: Literal["start_round"] = "start_round"
    purpose: Annotated[str, Field(min_length=1, max_length=500)]
    selected_channels: tuple[ChannelId, ...]

    @model_validator(mode="after")
    def valid_selection(self) -> StartRound:
        if not self.purpose.strip() or len(set(self.selected_channels)) != len(self.selected_channels):
            raise ValueError("Round purpose must be nonblank and selected channels unique")
        return self


class ChannelCommand(Command):
    channel: ChannelId


class AttachDraft(ChannelCommand):
    kind: Literal["attach_draft"] = "attach_draft"
    content: ChannelDraft
    facts: WorkshopFacts
    metadata: GenerationMetadata
    selected_image_reference: str | None


class VersionCommand(ChannelCommand):
    version_reference: Reference


class DirectRevision(VersionCommand):
    kind: Literal["direct_revision"] = "direct_revision"
    title: str
    text: str
    selected_image_reference: str | None


class AIRevision(VersionCommand):
    kind: Literal["ai_revision"] = "ai_revision"
    instruction: str
    selected_image_reference: str | None
    model: Annotated[str, Field(min_length=1)]
    timeout: Annotated[float, Field(gt=0, le=120, allow_inf_nan=False)]


class BindLink(VersionCommand):
    kind: Literal["bind_link"] = "bind_link"
    campaign: CampaignMetadata


class SelectVersion(VersionCommand):
    kind: Literal["select_version"] = "select_version"


class ApproveVersion(VersionCommand):
    kind: Literal["approve_version"] = "approve_version"


class PrepareSubmission(VersionCommand):
    kind: Literal["prepare_submission"] = "prepare_submission"
    approval_reference: Reference


class SetChannelEnabled(ChannelCommand):
    kind: Literal["set_channel_enabled"] = "set_channel_enabled"
    enabled: bool


PilotCommand = Annotated[
    StartRound | AttachDraft | DirectRevision | AIRevision | BindLink | SelectVersion
    | ApproveVersion | PrepareSubmission | SetChannelEnabled,
    Field(discriminator="kind"),
]
COMMAND_ADAPTER = TypeAdapter(PilotCommand)
EVIDENCE_ADAPTER = TypeAdapter(FeedImportResult)


class CommandOutcome(FrozenState):
    status: Literal["committed", "replayed", "conflict", "rejected"]
    result: CommandResult | None = None
    message: str | None = None


def new_campaign(*, campaign_reference: str, workshop_reference: str,
                 actor_reference: str, now: datetime) -> CampaignState:
    """Construct revision zero; create it with compare_and_save(expected_revision=None)."""
    now = _aware_utc(now, field="now")
    return CampaignState(campaign_reference=campaign_reference, workshop_reference=workshop_reference,
                         created_by=actor_reference, created_at=now, updated_at=now)


def _identity(command: Command, evidence: FeedImportResult | None) -> tuple[str, str]:
    data = command.model_dump(mode="json", exclude={"expected_revision"})
    evidence_digest = None if evidence is None else _fingerprint_payload(
        EVIDENCE_ADAPTER.dump_python(evidence, mode="json")
    )
    identity = {"command": data, "evidence_fingerprint": evidence_digest}
    return (json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False),
            _fingerprint_payload(identity))


def execute_command(repository: CampaignRepository, command: PilotCommand, *,
                    evidence: FeedImportResult | None = None,
                    client: DraftClient | None = None) -> CommandOutcome:
    """Load, execute once, atomically compare-and-save state plus receipt. Never retry AI."""
    # Reject unchecked model_copy changes before consulting receipts or invoking a boundary.
    command = COMMAND_ADAPTER.validate_json(command.model_dump_json())
    _aware_utc(command.now, field="now")
    if evidence is not None:
        evidence = EVIDENCE_ADAPTER.validate_json(EVIDENCE_ADAPTER.dump_json(evidence))
    identity_json, fingerprint = _identity(command, evidence)
    state = repository.load(command.campaign_reference)
    if state is None:
        return CommandOutcome(status="rejected", message="Campaign does not exist")
    # Protocol implementations are expected to validate storage; enforce that at this boundary too.
    state = restore_campaign(dump_campaign(state))
    prior = next((r for r in state.receipts if r.command_id == command.command_id), None)
    if prior is not None:
        if prior.input_fingerprint != fingerprint:
            return CommandOutcome(status="conflict", message="Command ID was used with different input")
        return CommandOutcome(status="replayed", result=prior.result)
    if state.revision != command.expected_revision:
        return CommandOutcome(status="conflict", message="Expected aggregate revision is stale")
    if state.workshop_reference != command.workshop_reference:
        return CommandOutcome(status="rejected", message="Command belongs to another workshop")
    if command.now < state.updated_at:
        return CommandOutcome(status="rejected", message="Command time predates current state")
    try:
        rounds, result = _apply(state, command, evidence, client)
    except ValueError as error:
        return CommandOutcome(status="rejected", message=str(error))
    receipt = CommandReceipt(command_id=command.command_id, identity_json=identity_json,
                             input_fingerprint=fingerprint, result=result)
    updated = state.model_copy(update={
        "rounds": rounds, "revision": state.revision + 1, "updated_at": command.now,
        "receipts": state.receipts + (receipt,),
    })
    # Check cross-record invariants before allowing a repository to persist anything.
    updated = restore_campaign(dump_campaign(updated))
    if not repository.compare_and_save(updated, expected_revision=state.revision):
        return CommandOutcome(status="conflict", message="Concurrent save won; this result was not committed")
    return CommandOutcome(status="committed", result=result)


def _apply(state: CampaignState, command: PilotCommand, evidence: FeedImportResult | None,
           client: DraftClient | None) -> tuple[tuple[MarketingRound, ...], CommandResult]:
    result_fields = dict(campaign_reference=state.campaign_reference, command_id=command.command_id,
                         revision=state.revision + 1, round_reference=command.round_reference,
                         channel=None if isinstance(command, StartRound) else command.channel)
    if isinstance(command, StartRound):
        if any(r.reference == command.round_reference for r in state.rounds):
            raise ValueError("Round already exists")
        round_ = MarketingRound(reference=command.round_reference, purpose=command.purpose,
                                created_by=command.actor_reference, created_at=command.now,
                                channels=tuple(ChannelState(channel=channel,
                                    enabled=channel in command.selected_channels, updated_at=command.now)
                                    for channel in ("google_business", "rausgegangen")))
        return state.rounds + (round_,), CommandResult(**result_fields, status="started")
    round_ = next((r for r in state.rounds if r.reference == command.round_reference), None)
    if round_ is None:
        raise ValueError("Round does not exist")
    channel = next(c for c in round_.channels if c.channel == command.channel)
    if isinstance(command, SetChannelEnabled):
        channel = channel.model_copy(update={"enabled": command.enabled, "last_status": "review_required",
                                             "diagnostics": (), "updated_at": command.now})
        result = CommandResult(**result_fields, status="enabled" if command.enabled else "disabled")
    else:
        if not channel.enabled:
            raise ValueError("Channel is disabled")
        record = None
        if isinstance(command, VersionCommand):
            record = next((v for v in channel.versions if v.reference == command.version_reference), None)
            if record is None:
                raise ValueError("Version is not in this round and channel")
            if not isinstance(command, SelectVersion) and channel.current_version != record.reference:
                raise ValueError("Action requires the explicitly selected current version")
        if isinstance(command, SelectVersion):
            # Selection intentionally establishes no current readiness or approval.
            channel = channel.model_copy(update={"current_version": record.reference,
                                                  "last_status": "review_required", "diagnostics": (),
                                                  "updated_at": command.now})
            result = CommandResult(**result_fields, status="selected", version_reference=record.reference)
        else:
            if evidence is None:
                raise ValueError("Current imported evidence is required")
            workshop = evidence.validation.workshop if evidence.validation is not None else None
            if workshop is not None and workshop.identity.workshop_id != state.workshop_reference:
                raise ValueError("Evidence belongs to another workshop")
            if isinstance(command, AttachDraft):
                if (workshop is None or command.facts.workshop_id != state.workshop_reference
                        or command.content.channel != command.channel
                        or command.metadata.source_version != command.facts.source_version
                        or command.metadata.generated_at > command.now):
                    raise ValueError("Generated draft identity or metadata mismatch")
                # Call the same constructor/validator as create_draft_workflow, one channel at a time.
                version = _new_version(imported=evidence, channel=command.channel, content=command.content,
                    selected_image_reference=command.selected_image_reference, now=command.now,
                    origin="generation", parent_fingerprint=None, metadata=command.metadata,
                    bound_facts=command.facts)
                channel, result = _append(channel, version, None, command, result_fields,
                                          workshop.content.public_description_html)
            elif isinstance(command, (DirectRevision, AIRevision, BindLink)):
                # Domain workflow.current uses the last entry. Supply only the selected parent;
                # the aggregate retains the full history and its explicit parent reference.
                workflow = DraftWorkflow(state.workshop_reference, record.original_description_html,
                    (record.version,) if command.channel == "google_business" else (),
                    (record.version,) if command.channel == "rausgegangen" else ())
                if isinstance(command, DirectRevision):
                    revised = revise_draft_directly(workflow, evidence, channel=command.channel,
                        title=command.title, text=command.text, selected_image_reference=command.selected_image_reference,
                        now=command.now)
                elif isinstance(command, AIRevision):
                    if client is None:
                        raise ValueError("AI revision requires an explicitly injected client")
                    revised = revise_draft_with_ai(workflow, evidence, channel=command.channel,
                        instruction=command.instruction, selected_image_reference=command.selected_image_reference,
                        now=command.now, model=command.model, timeout=command.timeout, client=client)
                else:
                    campaign = command.campaign
                    if (campaign.workshop != state.workshop_reference or campaign.campaign != state.campaign_reference
                            or campaign.round != round_.reference or campaign.channel != command.channel):
                        raise ValueError("Campaign metadata belongs to another campaign, round, workshop or channel")
                    revised = bind_campaign_link(workflow, evidence, campaign=campaign, now=command.now)
                if revised.version is None:
                    result = CommandResult(**result_fields, status="review_required", diagnostics=revised.diagnostics)
                    channel = channel.model_copy(update={"last_status": "review_required",
                                                         "diagnostics": revised.diagnostics, "updated_at": command.now})
                else:
                    channel, result = _append(channel, revised.version, record.reference, command, result_fields,
                                              record.original_description_html)
            elif isinstance(command, ApproveVersion):
                approved = approve_draft_version(record.version, evidence,
                    approving_person_reference=command.actor_reference, approved_at=command.now)
                approvals = channel.approvals
                approval_ref = None
                if approved.approval is not None:
                    approval_ref = command.command_id
                    approvals += (ApprovalRecord(reference=approval_ref, version_reference=record.reference,
                                                 approval=approved.approval),)
                channel = channel.model_copy(update={"approvals": approvals, "last_status": approved.status,
                                                     "diagnostics": approved.diagnostics, "updated_at": command.now})
                result = CommandResult(**result_fields, status=approved.status, version_reference=record.reference,
                                       approval_reference=approval_ref, diagnostics=approved.diagnostics)
            elif isinstance(command, PrepareSubmission):
                approval = next((a for a in channel.approvals if a.reference == command.approval_reference), None)
                if approval is None or approval.version_reference != record.reference:
                    raise ValueError("Approval does not reference this exact selected version")
                prepare = prepare_google_submission if command.channel == "google_business" else prepare_rausgegangen_submission
                prepared = prepare(record.version, approval.approval, evidence, now=command.now)
                submissions = channel.submissions
                submission_ref = None
                if prepared.package is not None:
                    submission_ref = command.command_id
                    submissions += (SubmissionRecord(reference=submission_ref, version_reference=record.reference,
                        approval_reference=approval.reference, fingerprint=prepared.package.fingerprint,
                        package=prepared.package),)
                channel = channel.model_copy(update={"submissions": submissions, "last_status": prepared.status,
                                                      "diagnostics": prepared.diagnostics, "updated_at": command.now})
                result = CommandResult(**result_fields, status=prepared.status, version_reference=record.reference,
                    approval_reference=approval.reference, submission_reference=submission_ref, diagnostics=prepared.diagnostics)
            else:
                raise ValueError("Unsupported command")
    round_ = round_.model_copy(update={"channels": tuple(channel if c.channel == channel.channel else c for c in round_.channels)})
    return tuple(round_ if r.reference == round_.reference else r for r in state.rounds), result


def _append(
    channel: ChannelState, version: DraftVersion, parent_reference: str | None,
    command: ChannelCommand, result_fields: dict[str, object], original_description_html: str,
) -> tuple[ChannelState, CommandResult]:
    record = VersionRecord(reference=command.command_id, parent_reference=parent_reference,
                           fingerprint=version.fingerprint, version=version,
                           original_description_html=original_description_html)
    status = "revised" if version.validation.status == "valid" else "review_required"
    channel = channel.model_copy(update={"versions": channel.versions + (record,),
        "current_version": record.reference, "last_status": status,
        "diagnostics": version.validation.diagnostics, "updated_at": command.now})
    return channel, CommandResult(**result_fields, status=status, version_reference=record.reference,
                                  diagnostics=version.validation.diagnostics)
