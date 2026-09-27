"""Reserve exact manual copy, then record separate teacher confirmations."""

from uuid import uuid4

from . import application as app
from .publication import all_attempts, approval_fingerprint, payload_fingerprint, target_state
from .revision import _fingerprint_payload
from .service_publication import _confirmed
from .service_views import diagnostic_views
from .submission import RausgegangenCopyPackage, prepare_rausgegangen_submission

_MESSAGES = {
    "ready_to_copy": "Paket reserviert und zum Kopieren bereit. Die Einreichung erfolgt manuell im Portal.",
    "existing_reservation": "Ein Vorgang ist bereits gespeichert. Aktuellen Stand prüfen; nicht erneut einreichen.",
    "submitted": "Einreichung als erfolgt bestätigt. Dies bestätigt noch keine öffentliche Veröffentlichung.",
    "published": "Veröffentlichung als erfolgt bestätigt. Eine Prüfung im Portal wurde nicht durchgeführt.",
    "outdated": "Workshopdaten oder Freigabe sind nicht mehr aktuell. Bitte erneut prüfen und freigeben.",
    "review_required": "Auswahl, Freigabe und vorbereitetes Paket bitte erneut prüfen.",
    "invalid_request": "Bitte Runde und Rausgegangen-Vorgang prüfen. Die Bestätigung passt nicht zum gespeicherten Stand.",
    "revision_conflict": "Der Stand wurde geändert. Bitte neu laden und prüfen.",
    "unknown_commit_outcome": "Speicherergebnis unklar. Bitte neu laden; nicht erneut einreichen oder automatisch wiederholen.",
}


def _response(status, **fields):
    return {"status": status, "message": _MESSAGES[status], **fields}


def copy_package_view(record):
    """Explicit projection of approved stored copy; no extraction from source HTML."""
    payload = record.package.payload
    facts = payload.fact_sheet
    return {"title": payload.title, "description": payload.description,
        "local_date": facts.local_date.isoformat(), "start_time": facts.start_time.isoformat(),
        "end_time": facts.end_time.isoformat(), "time_zone": facts.time_zone,
        "location": facts.location, "regular_price": str(facts.regular_price),
        "currency": facts.currency, "pricing_unit": facts.pricing_unit, "price_note": payload.price_note,
        "external_booking_link": payload.external_booking_link, "image_reference": payload.image_reference,
        # Any existing credit is retained verbatim in description; never infer a separate credit.
        "copy_instructions": list(payload.copy_instructions), "portal_destination": payload.portal_destination}


def manual_result_view(state, receipt, *, replay=False):
    channel = next(c for r in state.rounds if r.reference == receipt.result.round_reference
                   for c in r.channels if c.channel == "rausgegangen")
    attempt = next(a for a in channel.publications if a.reference == receipt.result.attempt_reference)
    event = next(e for e in attempt.events if e.reference == receipt.command_id)
    begin = event.source == "reservation"
    active = begin and not replay and attempt.status == "in_progress"
    status = ("ready_to_copy" if active else "existing_reservation") if begin else event.status
    result = _response(status, campaign_reference=state.campaign_reference,
        round_reference=receipt.result.round_reference, attempt_reference=attempt.reference,
        revision=state.revision, recorded_revision=receipt.result.revision,
        recorded_status=event.status, manual_status=attempt.status, historical=replay,
        package_active=active, public_url=attempt.events[-1].public_url)
    if begin:
        record = next(s for s in channel.submissions if s.reference == attempt.submission_reference)
        result["package"] = copy_package_view(record)
    return result


def manual_replay(state, receipts):
    return manual_result_view(state, receipts[-1], replay=True)


def _save(service, request, command, binding, evidence=None):
    outcome = None
    try:
        outcome = app.execute_command(service.dependencies.repository, command,
                                      evidence=evidence, service_request=binding)
    except Exception:
        # Even a failed acknowledgement can follow a committed transaction.
        pass
    try:
        saved, receipt = _confirmed(service, request, command, binding, evidence)
        if receipt is not None:
            return manual_result_view(saved, receipt, replay=outcome is not None and outcome.status == "replayed")
    except Exception:
        pass
    if outcome is not None and outcome.status in ("conflict", "rejected"):
        return _response("revision_conflict", refresh_required=True, package_active=False)
    return _response("unknown_commit_outcome", refresh_required=True, package_active=False)


def rausgegangen_action(service, request, state, binding):
    channel = next((c for r in state.rounds if r.reference == request.round_reference
                    for c in r.channels if c.channel == "rausgegangen"), None)
    if channel is None:
        return _response("invalid_request")
    common = dict(campaign_reference=state.campaign_reference, workshop_reference=state.workshop_reference,
        round_reference=request.round_reference, channel="rausgegangen", expected_revision=state.revision,
        command_id="service-" + _fingerprint_payload({"request_id": request.request_id, "slot": "action"}),
        actor_reference=binding.actor_reference)
    if request.action == "begin_rausgegangen_submission":
        attempts = all_attempts(state, "rausgegangen")
        if target_state(attempts)[0]:
            return _response("existing_reservation", revision=state.revision, package_active=False,
                             attempt_reference=attempts[-1].reference, manual_status=attempts[-1].status)
        version = next((v for v in channel.versions if v.reference == request.version_reference), None)
        approval = next((a for a in channel.approvals if a.reference == request.approval_reference), None)
        submission = next((s for s in channel.submissions if s.reference == request.submission_reference), None)
        if (not channel.enabled or channel.current_version != request.version_reference
                or version is None or approval is None or submission is None
                or approval.version_reference != version.reference
                or submission.version_reference != version.reference or submission.approval_reference != approval.reference
                or type(submission.package.payload) is not RausgegangenCopyPackage):
            return _response("review_required")
        evidence, now, error = service._evidence(request)
        if error:
            return error
        # Reuse preparation only as a fresh readiness check. Return the stored record,
        # never this newly checked package or reconstructed wording.
        readiness = prepare_rausgegangen_submission(version.version, approval.approval, evidence, now=now)
        if readiness.package is None:
            return _response(readiness.status, diagnostics=diagnostic_views(readiness.diagnostics))
        command = app.ReservePublication(**common, now=now, operation_id="manual-" + uuid4().hex,
            version_reference=version.reference, version_fingerprint=version.fingerprint,
            approval_reference=approval.reference, approval_fingerprint=approval_fingerprint(approval.approval),
            submission_reference=submission.reference, submission_fingerprint=submission.fingerprint,
            payload_fingerprint=payload_fingerprint(submission.package))
        return _save(service, request, command, binding, evidence)
    attempt = next((a for a in channel.publications if a.reference == request.attempt_reference), None)
    submission_confirmation = request.action == "confirm_rausgegangen_submission"
    expected = "in_progress" if submission_confirmation else "submitted"
    if attempt is None or attempt.status != expected:
        return _response("invalid_request")
    cls = app.ConfirmSubmission if submission_confirmation else app.ConfirmPublication
    fields = {} if submission_confirmation else {"public_url": request.public_url}
    command = cls(**common, now=service._now(), attempt_reference=attempt.reference, **fields)
    return _save(service, request, command, binding)
