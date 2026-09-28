"""Read-only handoff of exact approved Google copy; never publication evidence."""

from .publication import all_attempts, payload_fingerprint, target_state
from .service_views import diagnostic_views
from .submission import GoogleSubmissionPayload, prepare_google_submission

API_PENDING_MESSAGE = "Automatische Google-Veröffentlichung und API-Abgleich sind nicht verfügbar: API-Zugang und OAuth stehen noch aus."
MESSAGES = {
    "ready_for_manual_publication": "Zum manuellen Veröffentlichen bereit",
    "review_required": "Auswahl, Freigabe und vorbereitetes Paket bitte erneut prüfen.",
    "outdated": "Workshopdaten oder Freigabe sind nicht mehr aktuell. Bitte erneut prüfen und freigeben.",
    "reconciliation_required": "Ein Google-Vorgang ist noch ungeklärt. Nicht erneut manuell veröffentlichen.",
}


def google_handoff(service, request, state):
    def response(status, **fields):
        return {"status": status, "message": MESSAGES[status], **fields}

    if target_state(all_attempts(state, "google_business"))[0]:
        return response("reconciliation_required")
    channel = next((c for r in state.rounds if r.reference == request.round_reference
                    for c in r.channels if c.channel == "google_business"), None)
    if channel is None or not channel.enabled or channel.current_version != request.version_reference:
        return response("review_required")
    version = next((v for v in channel.versions if v.reference == request.version_reference), None)
    approval = next((a for a in channel.approvals if a.reference == request.approval_reference), None)
    submission = next((s for s in channel.submissions if s.reference == request.submission_reference), None)
    if (version is None or approval is None or submission is None
            or approval.version_reference != version.reference
            or submission.version_reference != version.reference
            or submission.approval_reference != approval.reference
            or type(submission.package.payload) is not GoogleSubmissionPayload):
        return response("review_required")
    evidence, now, error = service._evidence(request)
    if error:
        return error
    if evidence.validation.workshop.content.public_description_html != version.original_description_html:
        return response("outdated")
    ready = prepare_google_submission(version.version, approval.approval, evidence, now=now)
    if ready.package is None:
        return response(ready.status, diagnostics=diagnostic_views(ready.diagnostics))
    if payload_fingerprint(ready.package) != payload_fingerprint(submission.package):
        return response("review_required")
    # Detect changes during the fresh evidence fetch; this is not an atomic reservation.
    current, error = service._load(request)
    if error:
        return error
    if current != state:
        return response("review_required")
    payload = submission.package.payload
    return response("ready_for_manual_publication", campaign_reference=state.campaign_reference,
        round_reference=request.round_reference, version_reference=version.reference,
        approval_reference=approval.reference, submission_reference=submission.reference,
        revision=state.revision, package={
            "summary": payload.summary, "event_title": payload.event.title,
            "schedule": payload.event.schedule.model_dump(mode="json"),
            "time_zone": submission.package.version.workshop_facts.time_zone,
            "booking_url": payload.callToAction.url,
            "image_reference": submission.package.version.selected_image_reference,
            # Required credit is already in approved summary; never reconstruct attribution.
        })
