"""One historical Google read and one append-only observation; never a provider write."""

import re
from uuid import uuid4

from pydantic import TypeAdapter

from . import application as app
from .google_local_posts import PostResult, observed_payload
from .publication import PublicURL, ReconciliationObservation, payload_fingerprint, reconciliation_resource
from .revision import _fingerprint_payload
from .service_publication import _confirmed
from .submission import GoogleSubmissionPayload

_MESSAGES = {
    "matched": "Veröffentlichung der exakt freigegebenen Version bestätigt und gespeichert.",
    "provider_rejected": "Google hat den Beitrag abgelehnt. Ergebnis gespeichert.",
    "processing": "Google verarbeitet den Beitrag noch. Veröffentlichung bleibt ungeklärt.",
    "content_mismatch": "Der sichtbare Inhalt entspricht nicht der freigegebenen Version. Bitte prüfen.",
    "content_unresolved": "Der veröffentlichte Inhalt lässt sich nicht vollständig abgleichen. Bitte prüfen.",
    "public_url_unavailable": "Ein gültiger öffentlicher Google-Link fehlt. Abgleich bleibt ungeklärt.",
    "read_unavailable": "Automatischer Abgleich derzeit nicht verfügbar. Veröffentlichung bleibt ungeklärt.",
    "resource_mismatch": "Google-Zuordnung nicht bestätigt. Veröffentlichung bleibt ungeklärt.",
    "unknown_state": "Google-Status nicht eindeutig. Veröffentlichung bleibt ungeklärt.",
    "manual_reconciliation_required": "Kein sicher zugeordneter Google-Beitrag vorhanden. Manueller Abgleich erforderlich.",
    "reconciliation_unavailable": "Automatischer Abgleich ist nicht eingerichtet. Bitte manuell prüfen.",
    "recorded_state": "Bereits gespeicherten Veröffentlichungsstand angezeigt. Kein erneuter Google-Abruf.",
    "unknown_commit_outcome": "Speicherergebnis des Abgleichs unklar. Bitte neu laden; nicht automatisch wiederholen.",
    "revision_conflict": "Der Stand wurde während des Abgleichs geändert. Bitte neu laden und prüfen.",
    "invalid_request": "Bitte Kampagne, Runde und Veröffentlichungsversuch prüfen.",
}


def _view(state, round_reference, attempt, *, event=None, status=None, revision=None):
    event = event or attempt.events[-1]
    observation = event.reconciliation
    reason = status or (observation.reason if observation else "recorded_state")
    return {"status": status or event.status, "message": _MESSAGES[reason],
        "campaign_reference": state.campaign_reference, "round_reference": round_reference,
        "attempt_reference": attempt.reference, "revision": state.revision if revision is None else revision,
        "publication_status": event.status, "reconciliation_required": event.status in ("in_progress", "outcome_unknown"),
        "payload_matched": observation.payload_matched if observation else None,
        "provider_state": observation.provider_state if observation else None, "public_url": event.public_url}


def reconciliation_replay(state, receipts):
    receipt = receipts[-1]
    attempt = next(a for r in state.rounds if r.reference == receipt.result.round_reference
                   for c in r.channels if c.channel == "google_business" for a in c.publications
                   if a.reference == receipt.result.attempt_reference)
    event = next(e for e in attempt.events if e.reference == receipt.command_id)
    return _view(state, receipt.result.round_reference, attempt, event=event, revision=receipt.result.revision)


def _mapped(result, resource, reserved):
    reason, provider_state, matched = "read_unavailable", None, None
    public_url = None
    if type(result) is PostResult and result.operation == "get":
        if result.resource_name is not None and result.resource_name != resource:
            reason = "resource_mismatch"
        elif result.resource_name == resource:
            if result.outcome == "rejected" and result.state == "REJECTED":
                reason, provider_state = "provider_rejected", "REJECTED"
            elif result.outcome == "pending" and result.state in ("PROCESSING", "SCHEDULED"):
                reason, provider_state = "processing", result.state
            elif result.outcome == "visible" and result.state in ("LIVE", "RECURRING"):
                reason, provider_state = "content_unresolved", result.state
                # Revalidate even injected/unchecked typed values; do not trust model_copy.
                payload = result.observed_payload
                normalized = (observed_payload(payload.provider_payload())
                              if type(payload) is GoogleSubmissionPayload else None)
                if normalized is not None:
                    matched = _fingerprint_payload(normalized.provider_payload()) == reserved
                    reason = "content_mismatch"
                    if matched:
                        try:
                            public_url = TypeAdapter(PublicURL).validate_python(result.search_url)
                        except (ValueError, TypeError):
                            reason = "public_url_unavailable"
                        else:
                            reason = "matched"
            elif result.outcome == "unresolved":
                reason = "unknown_state"
    observation = ReconciliationObservation(reason=reason, provider_state=provider_state, payload_matched=matched)
    return {"status": "published" if reason == "matched" else "failed" if reason == "provider_rejected" else "outcome_unknown",
        "external_post_id": resource, "public_url": public_url,
        "error_category": "provider_rejected" if reason == "provider_rejected" else None,
        "reconciliation": observation}


def reconcile_google(service, request, state, binding):
    channel = next((c for r in state.rounds if r.reference == request.round_reference
                    for c in r.channels if c.channel == "google_business"), None)
    attempt = next((a for a in channel.publications if a.reference == request.attempt_reference), None) if channel else None
    if attempt is None:
        return {"status": "invalid_request", "message": _MESSAGES["invalid_request"]}
    if attempt.status not in ("in_progress", "outcome_unknown"):
        return _view(state, request.round_reference, attempt)
    resource = reconciliation_resource(attempt)
    if resource is None:
        return _view(state, request.round_reference, attempt, status="manual_reconciliation_required")
    google = service.dependencies.google
    if (google is None or not isinstance(getattr(google, "location", None), str)
            or re.fullmatch(r"accounts/[A-Za-z0-9_-]+/locations/[A-Za-z0-9_-]+", google.location) is None
            or not resource.startswith(google.location + "/localPosts/")
            or not callable(getattr(google, "get_post", None))):
        return _view(state, request.round_reference, attempt, status="reconciliation_unavailable")
    # _load has already checked all version/approval/package/fingerprint bindings.
    submission = next(s for s in channel.submissions if s.reference == attempt.submission_reference)
    if (type(submission.package.payload) is not GoogleSubmissionPayload
            or submission.fingerprint != submission.package.fingerprint
            or payload_fingerprint(submission.package) != attempt.payload_fingerprint):
        return _view(state, request.round_reference, attempt, status="reconciliation_unavailable")
    try:
        result = google.get_post(resource)
        fields = _mapped(result, resource, attempt.payload_fingerprint)
    except Exception:
        fields = _mapped(None, resource, attempt.payload_fingerprint)
    # Retain the pre-read revision: a concurrent change must not be overwritten by
    # an old observation. No save/reload path below performs another provider read.
    uncertain = _view(state, request.round_reference, attempt, status="unknown_commit_outcome")
    try:
        command = app.RecordGoogleReconciliation(
            command_id="reconcile-" + uuid4().hex, campaign_reference=state.campaign_reference,
            workshop_reference=state.workshop_reference, round_reference=request.round_reference,
            expected_revision=state.revision, actor_reference=binding.actor_reference,
            now=service._now(), attempt_reference=attempt.reference, **fields)
        outcome = None
        try:
            outcome = app.execute_command(service.dependencies.repository, command, service_request=binding)
        except Exception:
            pass
        saved, receipt = _confirmed(service, request, command, binding)
        if receipt is not None:
            return reconciliation_replay(saved, [receipt])
        if outcome is not None and outcome.status in ("conflict", "rejected"):
            return _view(state, request.round_reference, attempt, status="revision_conflict")
    except Exception:
        pass
    return uncertain
