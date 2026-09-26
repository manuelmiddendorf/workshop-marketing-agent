"""Recorded publication evidence and pure transitions; no publishing transport."""

from datetime import datetime
from ipaddress import ip_address
import json
from typing import Annotated, Literal
from urllib.parse import unquote, urlsplit

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, HttpUrl, TypeAdapter, model_validator

from .campaign import Reference
from .models import _booking_url
from .revision import DraftApproval, _aware_utc, _fingerprint_payload
from .submission import GoogleSubmissionPayload, prepare_google_submission, prepare_rausgegangen_submission

Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
ExternalPostId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._~/-]{0,511}$")]
GooglePostName = Annotated[str, Field(max_length=512, pattern=
    r"^accounts/[A-Za-z0-9_-]+/locations/[A-Za-z0-9_-]+/localPosts/[A-Za-z0-9_-]+$")]
PublicationStatus = Literal["in_progress", "submitted", "published", "failed", "outcome_unknown"]
SafeError = Literal["permission_denied", "invalid_submission", "provider_rejected", "provider_unavailable"]


def _public_url(value):
    _booking_url(value)
    parts = urlsplit(value)
    host = HttpUrl(value).host.lower().rstrip(".").strip("[]")
    if (parts.query or "?" in value or "@" in unquote(value)
            or host.endswith((".localhost", ".local", ".internal"))):
        raise ValueError("Expected a public HTTPS URL without query or identity data")
    try:
        address = ip_address(host)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise ValueError("Expected a public address")
    return value


PublicURL = Annotated[str, Field(max_length=2048), AfterValidator(_public_url)]


class PublicationEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    reference: Reference
    status: PublicationStatus
    recorded_by: Reference
    recorded_at: datetime
    source: Literal["reservation", "automatic", "manual"]
    error_category: SafeError | None = None
    external_post_id: ExternalPostId | None = None
    public_url: PublicURL | None = None

    @model_validator(mode="after")
    def valid_event(self):
        _aware_utc(self.recorded_at, field="publication event time")
        if ((self.status == "failed") != (self.error_category is not None)
                or (self.status != "published" and self.public_url is not None)
                or (self.external_post_id is not None and self.status != "published"
                    and not (self.source == "automatic" and self.status == "outcome_unknown"))
                or (self.source == "reservation") != (self.status == "in_progress")
                or (self.source == "automatic" and self.status not in ("published", "failed", "outcome_unknown"))
                or (self.source == "automatic" and self.status == "published" and self.external_post_id is None)
                or (self.source == "manual" and (self.status not in ("submitted", "published")
                                                 or self.external_post_id is not None))):
            raise ValueError("Invalid publication event fields")
        if self.status == "outcome_unknown" and self.external_post_id is not None:
            TypeAdapter(GooglePostName).validate_python(self.external_post_id)
        return self


class PublicationAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    reference: Reference
    target_fingerprint: Digest
    operation: Literal["create", "update"]
    operation_id: Reference
    initiated_by: Reference
    initiated_at: datetime
    version_reference: Reference
    version_fingerprint: Digest
    approval_reference: Reference
    approval_fingerprint: Digest
    submission_reference: Reference
    submission_fingerprint: Digest
    payload_fingerprint: Digest
    update_post_id: ExternalPostId | None = None
    events: Annotated[tuple[PublicationEvent, ...], Field(min_length=1)]

    @property
    def status(self):
        return self.events[-1].status

    @model_validator(mode="after")
    def valid_history(self):
        _aware_utc(self.initiated_at, field="publication initiation time")
        first = self.events[0]
        if ((self.operation == "update") != (self.update_post_id is not None)
                or first.status != "in_progress" or first.reference != self.reference
                or first.recorded_by != self.initiated_by or first.recorded_at != self.initiated_at):
            raise ValueError("Invalid publication reservation")
        previous = first
        references = {first.reference}
        for event in self.events[1:]:
            allowed = ((previous.status == "in_progress" and event.status in
                        ("submitted", "published", "failed", "outcome_unknown"))
                       or (previous.status == "submitted" and event.status == "published"))
            if (not allowed or event.recorded_at < previous.recorded_at or event.reference in references
                    or (event.source == "manual" and event.status == "published" and previous.status != "submitted")
                    or (event.source == "automatic" and previous.status != "in_progress")
                    or (event.external_post_id is not None and self.operation == "update"
                        and event.external_post_id != self.update_post_id)):
                raise ValueError("Invalid publication transition")
            references.add(event.reference)
            previous = event
        return self


def approval_fingerprint(approval):
    return _fingerprint_payload(TypeAdapter(DraftApproval).dump_python(approval, mode="json"))


def payload_fingerprint(package):
    payload = (package.payload.provider_payload() if isinstance(package.payload, GoogleSubmissionPayload)
               else package.payload.model_dump(mode="json"))
    return _fingerprint_payload(payload)


def target_fingerprint(state, channel):
    # One destination per campaign/channel across all rounds; clients cannot supply it.
    return _fingerprint_payload({"scope": "pilot-publication-target.v1", "campaign": state.campaign_reference,
                                 "workshop": state.workshop_reference, "channel": channel})


def target_state(attempts):
    """Return blocking state and last successful identity, retaining failed-update history."""
    blocked = any(a.status in ("in_progress", "submitted", "outcome_unknown") for a in attempts)
    successes = [e for a in attempts for e in a.events if e.status == "published"]
    ids = {e.external_post_id for e in successes if e.external_post_id is not None}
    insufficient = bool(successes) and (len(ids) != 1 or any(e.external_post_id is None for e in successes))
    return blocked or insufficient, next(iter(ids)) if len(ids) == 1 else None


def all_attempts(state, channel):
    return [a for r in state.rounds for c in r.channels if c.channel == channel for a in c.publications]


def apply_publication(state, channel, command, evidence, result_fields):
    from .campaign_state import CommandResult

    if command.kind == "reserve_publication":
        if not channel.enabled or channel.current_version != command.version_reference:
            raise ValueError("Reservation requires an enabled channel and its current version")
        if any(a.operation_id == command.operation_id for r in state.rounds for c in r.channels for a in c.publications):
            raise ValueError("Operation ID is already reserved")
        blocked, post_id = target_state(all_attempts(state, channel.channel))
        if blocked:
            raise ValueError("Publication target is blocked pending reconciliation")
        version = next((v for v in channel.versions if v.reference == command.version_reference), None)
        approval = next((a for a in channel.approvals if a.reference == command.approval_reference), None)
        submission = next((s for s in channel.submissions if s.reference == command.submission_reference), None)
        if (version is None or approval is None or submission is None
                or approval.version_reference != version.reference
                or submission.version_reference != version.reference or submission.approval_reference != approval.reference
                or command.version_fingerprint != version.fingerprint
                or command.approval_fingerprint != approval_fingerprint(approval.approval)
                or command.submission_fingerprint != submission.fingerprint
                or command.payload_fingerprint != payload_fingerprint(submission.package)):
            raise ValueError("Reservation must bind exact version, approval and submission")
        if evidence is None:
            raise ValueError("Reservation requires current imported evidence")
        prepare = prepare_google_submission if channel.channel == "google_business" else prepare_rausgegangen_submission
        ready = prepare(version.version, approval.approval, evidence, now=command.now)
        if ready.package is None:
            raise ValueError("Publication is not currently ready")
        if payload_fingerprint(ready.package) != command.payload_fingerprint:
            raise ValueError("Current payload differs from reserved submission")
        attempt = PublicationAttempt(reference=command.command_id, target_fingerprint=target_fingerprint(state, channel.channel),
            operation="update" if post_id is not None else "create", operation_id=command.operation_id,
            initiated_by=command.actor_reference, initiated_at=command.now,
            **{name: getattr(command, name) for name in ("version_reference", "version_fingerprint",
               "approval_reference", "approval_fingerprint", "submission_reference", "submission_fingerprint", "payload_fingerprint")},
            update_post_id=post_id, events=(PublicationEvent(reference=command.command_id, status="in_progress",
                recorded_by=command.actor_reference, recorded_at=command.now, source="reservation"),))
        publications = channel.publications + (attempt,)
    else:
        attempt = next((a for a in channel.publications if a.reference == command.attempt_reference), None)
        if attempt is None:
            raise ValueError("Publication attempt is not in this round and channel")
        automatic = command.kind == "record_publication_result"
        if automatic != (channel.channel == "google_business"):
            raise ValueError("Result source is not supported for this channel")
        status = command.status if automatic else ("submitted" if command.kind == "confirm_submission" else "published")
        event = PublicationEvent(reference=command.command_id, status=status, recorded_by=command.actor_reference,
            recorded_at=command.now, source="automatic" if automatic else "manual",
            error_category=getattr(command, "error_category", None),
            external_post_id=getattr(command, "external_post_id", None), public_url=getattr(command, "public_url", None))
        # Revalidate before saving, including transitions out of terminal/unknown states.
        attempt = PublicationAttempt.model_validate_json(attempt.model_copy(update={"events": attempt.events + (event,)}).model_dump_json())
        publications = tuple(attempt if a.reference == attempt.reference else a for a in channel.publications)
    channel = channel.model_copy(update={"publications": publications, "updated_at": command.now})
    return channel, CommandResult(**result_fields, status=attempt.status, attempt_reference=attempt.reference,
        version_reference=attempt.version_reference, approval_reference=attempt.approval_reference,
        submission_reference=attempt.submission_reference)


def validate_publications(state):
    """Bind every event to one receipt and reconstruct target state in commit order."""
    attempts = {}
    events = {}
    operation_ids = set()
    for round_ in state.rounds:
        for channel in round_.channels:
            for attempt in channel.publications:
                if attempt.reference in attempts or attempt.operation_id in operation_ids:
                    raise ValueError("Duplicate publication identity")
                operation_ids.add(attempt.operation_id)
                attempts[attempt.reference] = attempt
                version = next((v for v in channel.versions if v.reference == attempt.version_reference), None)
                approval = next((a for a in channel.approvals if a.reference == attempt.approval_reference), None)
                submission = next((s for s in channel.submissions if s.reference == attempt.submission_reference), None)
                if (version is None or approval is None or submission is None
                        or approval.version_reference != version.reference or submission.version_reference != version.reference
                        or submission.approval_reference != approval.reference or version.fingerprint != attempt.version_fingerprint
                        or approval_fingerprint(approval.approval) != attempt.approval_fingerprint
                        or submission.fingerprint != attempt.submission_fingerprint
                        or payload_fingerprint(submission.package) != attempt.payload_fingerprint
                        or attempt.target_fingerprint != target_fingerprint(state, channel.channel)
                        or not submission.package.checked_at <= attempt.initiated_at <= channel.updated_at):
                    raise ValueError("Invalid publication record binding")
                for event in attempt.events:
                    if (event.reference in events or event.recorded_at > channel.updated_at
                            or (event.source == "manual" and channel.channel != "rausgegangen")
                            or (event.source == "automatic" and channel.channel != "google_business")):
                        raise ValueError("Invalid publication event association")
                    events[event.reference] = (round_, channel, attempt, event)
    seen = {}
    current_versions = {}
    enabled = {}
    creation_revisions = {r.command_id: r.result.revision for r in state.receipts}
    for receipt in state.receipts:
        command = json.loads(receipt.identity_json)["command"]
        result = receipt.result
        key = (result.round_reference, result.channel)
        kind = command["kind"]
        if kind == "start_round":
            for channel_id in ("google_business", "rausgegangen"):
                enabled[(result.round_reference, channel_id)] = channel_id in command["selected_channels"]
        elif kind == "set_channel_enabled":
            enabled[key] = command["enabled"]
        elif kind in ("attach_draft", "direct_revision", "ai_revision", "bind_link", "select_version"):
            if result.version_reference is not None:
                current_versions[key] = result.version_reference
        if result.attempt_reference is None:
            if kind in PUBLICATION_KINDS or receipt.command_id in events:
                raise ValueError("Publication receipt is missing its attempt")
            continue
        if kind not in PUBLICATION_KINDS or receipt.command_id not in events:
            raise ValueError("Unexpected publication result")
        round_, channel, attempt, event = events.pop(receipt.command_id)
        if (result.attempt_reference != attempt.reference or result.round_reference != round_.reference
                or result.channel != channel.channel or result.status != event.status or result.diagnostics
                or result.version_reference != attempt.version_reference
                or result.approval_reference != attempt.approval_reference
                or result.submission_reference != attempt.submission_reference
                or event.recorded_by != command["actor_reference"]
                or event.recorded_at != datetime.fromisoformat(command["now"])):
            raise ValueError("Publication receipt and event differ")
        if kind == "reserve_publication":
            prior = [a for a in seen.values() if a.target_fingerprint == attempt.target_fingerprint]
            blocked, post_id = target_state(prior)
            if (blocked or attempt.update_post_id != post_id
                    or not enabled.get(key) or current_versions.get(key) != attempt.version_reference
                    or any(creation_revisions.get(ref, state.revision + 1) >= result.revision
                           for ref in (attempt.version_reference, attempt.approval_reference, attempt.submission_reference))
                    or any(getattr(attempt, name) != command[name] for name in (
                        "operation_id", "version_reference", "version_fingerprint", "approval_reference",
                        "approval_fingerprint", "submission_reference", "submission_fingerprint", "payload_fingerprint"))):
                raise ValueError("Reservation conflicts with historical target or selection")
            seen[attempt.reference] = attempt.model_copy(update={"events": (event,)})
        else:
            old = seen.get(attempt.reference)
            if old is None or command["attempt_reference"] != attempt.reference:
                raise ValueError("Result precedes reservation")
            expected_source = "automatic" if kind == "record_publication_result" else "manual"
            expected_status = command.get("status") if expected_source == "automatic" else (
                "submitted" if kind == "confirm_submission" else "published")
            if (event.source != expected_source or event.status != expected_status
                    or event.public_url != command.get("public_url")
                    or event.external_post_id != command.get("external_post_id")
                    or event.error_category != command.get("error_category")):
                raise ValueError("Publication event differs from commanded result")
            seen[attempt.reference] = old.model_copy(update={"events": old.events + (event,)})
    if events or seen != attempts:
        raise ValueError("Publication history has missing or reordered receipts")


PUBLICATION_KINDS = {"reserve_publication", "record_publication_result", "confirm_submission", "confirm_publication"}
