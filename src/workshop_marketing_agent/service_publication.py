"""Two committed commands around at most one Google write; no crash resumption."""

import re
from typing import Protocol
from uuid import uuid4

from pydantic import TypeAdapter

from . import application as app
from .firestore_repository import CommitOutcomeUnknown
from .google_local_posts import PostResult
from .publication import (
    GooglePostName, PublicURL, all_attempts, approval_fingerprint, payload_fingerprint, target_state,
)
from .revision import _fingerprint_payload
from .submission import GoogleSubmissionPayload


class GooglePublisher(Protocol):
    location: str

    def create_event(self, payload: GoogleSubmissionPayload) -> PostResult: ...

    def update_event(self, post_name: str, payload: GoogleSubmissionPayload) -> PostResult: ...

    def get_post(self, post_name: str) -> PostResult: ...


_MESSAGES = {
    "published": "Veröffentlichung von Google bestätigt und gespeichert.",
    "failed": "Google-Veröffentlichung fehlgeschlagen. Ergebnis gespeichert.",
    "in_progress": "Veröffentlichung reserviert. Nicht erneut senden; Abgleich erforderlich.",
    "outcome_unknown": "Veröffentlichungsausgang unklar. Nicht erneut senden; Abgleich erforderlich.",
    "unknown_commit_outcome": "Speicherergebnis unklar. Nicht erneut senden; Abgleich erforderlich.",
    "publication_unconfigured": "Google-Veröffentlichung ist nicht eingerichtet.",
    "reconciliation_required": "Ein früherer Veröffentlichungsversuch ist ungeklärt. Bitte erst abgleichen.",
    "review_required": "Freigabe, Auswahl und aktuelle Workshopdaten erneut prüfen.",
    "revision_conflict": "Der Stand wurde geändert. Bitte neu laden und prüfen.",
}


def _response(status, **fields):
    return {"status": status, "message": _MESSAGES[status], **fields}


def _attempt(state, reference):
    return next((a for r in state.rounds for c in r.channels for a in c.publications
                 if a.reference == reference), None)


def _view(state, receipt):
    attempt = _attempt(state, receipt.result.attempt_reference)
    event = next(e for e in attempt.events if e.reference == receipt.command_id)
    return _response(event.status, campaign_reference=state.campaign_reference,
        round_reference=receipt.result.round_reference, version_reference=attempt.version_reference,
        approval_reference=attempt.approval_reference, submission_reference=attempt.submission_reference,
        attempt_reference=attempt.reference, revision=receipt.result.revision, operation=attempt.operation,
        publication_status=event.status, reconciliation_required=event.status in ("in_progress", "outcome_unknown"),
        public_url=event.public_url)


def publication_replay(state, receipts):
    # Return the last recorded result for this intent, never resume a reservation.
    return _view(state, receipts[-1])


def _confirmed(service, request, command, binding, evidence=None):
    """Read an exact command receipt, not merely a matching request/attempt ID."""
    state, error = service._load(request)
    if error or state is None:
        return None, None
    identity, fingerprint = app._identity(command, evidence)
    receipt = next((r for r in state.receipts if r.command_id == command.command_id
                    and r.identity_json == identity and r.input_fingerprint == fingerprint
                    and r.service_request == binding), None)
    return state, receipt


def _resource(value, location, attempt):
    try:
        TypeAdapter(GooglePostName).validate_python(value)
        if not value.startswith(location + "/localPosts/"):
            return None
        if attempt.operation == "update" and value != attempt.update_post_id:
            return None
        return value
    except (ValueError, TypeError):
        return None


def _mapped(result, location, attempt):
    unknown = {"status": "outcome_unknown"}
    if type(result) is not PostResult or result.operation != attempt.operation:
        return unknown
    resource = _resource(result.resource_name, location, attempt)
    if result.outcome == "visible":
        if resource is None or result.state not in ("LIVE", "RECURRING"):
            return unknown
        fields = {"status": "published", "external_post_id": resource}
        # Storage's existing public-link contract is intentionally stricter than searchUrl.
        # Omit an incompatible optional link; never strip/rewrite its query or leak it.
        try:
            fields["public_url"] = TypeAdapter(PublicURL).validate_python(result.search_url)
        except (ValueError, TypeError):
            pass
        return fields
    if result.outcome in ("pending", "unresolved"):
        return unknown | ({"external_post_id": resource} if resource is not None else {})
    errors = {"rejected": "provider_rejected", "provider_rejection": "provider_rejected",
              "authentication_or_permission": "permission_denied", "invalid_input": "invalid_submission"}
    if result.outcome in errors:
        return {"status": "failed", "error_category": errors[result.outcome]}
    # The current boundary has no evidence-of-no-dispatch field. Unavailable cannot
    # establish a safe failure for a write; exceptions and ambiguous writes agree.
    return unknown


def publish_google(service, request, state, binding):
    google = service.dependencies.google
    if (google is None or not isinstance(getattr(google, "location", None), str)
            or re.fullmatch(r"accounts/[A-Za-z0-9_-]+/locations/[A-Za-z0-9_-]+", google.location) is None
            or not callable(getattr(google, "create_event", None))
            or not callable(getattr(google, "update_event", None))):
        return _response("publication_unconfigured")
    channel = next((c for r in state.rounds if r.reference == request.round_reference
                    for c in r.channels if c.channel == "google_business"), None)
    if channel is None:
        return _response("review_required")
    version = next((v for v in channel.versions if v.reference == request.version_reference), None)
    approval = next((a for a in channel.approvals if a.reference == request.approval_reference), None)
    submission = next((s for s in channel.submissions if s.reference == request.submission_reference), None)
    if (version is None or approval is None or submission is None
            or type(submission.package.payload) is not GoogleSubmissionPayload):
        return _response("review_required")
    if target_state(all_attempts(state, "google_business"))[0]:
        return _response("reconciliation_required", reconciliation_required=True)
    evidence, now, error = service._evidence(request)
    if error:
        return error
    reservation = app.ReservePublication(
        campaign_reference=state.campaign_reference, workshop_reference=state.workshop_reference,
        round_reference=request.round_reference, channel="google_business",
        expected_revision=request.expected_revision,
        command_id="service-" + _fingerprint_payload({"request_id": request.request_id, "slot": "action"}),
        # A per-invocation nonce distinguishes simultaneous identical intents when
        # a CAS acknowledgement is lost. A different invocation cannot adopt it.
        operation_id="google-" + uuid4().hex, actor_reference=binding.actor_reference, now=now,
        version_reference=version.reference, version_fingerprint=version.fingerprint,
        approval_reference=approval.reference, approval_fingerprint=approval_fingerprint(approval.approval),
        submission_reference=submission.reference, submission_fingerprint=submission.fingerprint,
        payload_fingerprint=payload_fingerprint(submission.package))
    uncertain = _response("unknown_commit_outcome", reconciliation_required=True)
    try:
        outcome = app.execute_command(service.dependencies.repository, reservation,
                                      evidence=evidence, service_request=binding)
    except CommitOutcomeUnknown:
        # Only this invocation's unique reservation can permit dispatch below.
        outcome = None
    if outcome is not None and outcome.status != "committed":
        # A replay observed inside execute_command belongs to another invocation.
        return _response("review_required" if outcome.status == "rejected" else "revision_conflict")
    try:
        committed, receipt = _confirmed(service, request, reservation, binding, evidence)
    except Exception:
        return uncertain
    if receipt is None:
        return uncertain
    attempt = _attempt(committed, receipt.result.attempt_reference)
    if attempt.status != "in_progress":
        return _view(committed, next(r for r in committed.receipts if r.command_id == attempt.events[-1].reference))
    uncertain = _response("unknown_commit_outcome", reconciliation_required=True,
        campaign_reference=committed.campaign_reference, round_reference=request.round_reference,
        attempt_reference=attempt.reference, revision=receipt.result.revision,
        operation=attempt.operation, publication_status="in_progress")
    # Load the immutable bound payload from the positively confirmed snapshot.
    package = next(s.package for r in committed.rounds if r.reference == request.round_reference
                   for c in r.channels if c.channel == "google_business"
                   for s in c.submissions if s.reference == attempt.submission_reference)
    try:
        result = (google.create_event(package.payload) if attempt.operation == "create"
                  else google.update_event(attempt.update_post_id, package.payload))
        fields = _mapped(result, google.location, attempt)
    except Exception:
        fields = {"status": "outcome_unknown"}
    # No path below here dispatches again, even if storing the evidence fails.
    try:
        current, error = service._load(request)
        if error or current is None:
            return uncertain
        command = app.RecordPublicationResult(
            command_id="google-result-" + _fingerprint_payload({"attempt": attempt.reference}),
            campaign_reference=current.campaign_reference, workshop_reference=current.workshop_reference,
            round_reference=request.round_reference, channel="google_business",
            expected_revision=current.revision, actor_reference=attempt.initiated_by,
            now=service._now(), attempt_reference=attempt.reference, **fields)
        try:
            app.execute_command(service.dependencies.repository, command, service_request=binding)
        except Exception:
            # A receipt is the only positive evidence, regardless of exception class.
            pass
        saved, receipt = _confirmed(service, request, command, binding)
        if receipt is not None:
            return _view(saved, receipt)
    except Exception:
        pass
    return uncertain
