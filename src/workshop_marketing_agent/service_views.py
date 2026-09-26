"""Explicit public projections; never serialize domain state or diagnostic messages wholesale."""

from .campaign_state import CampaignState, CommandResult
from .generation import GoogleBusinessDraft


_DIAGNOSTICS = {
    "provider": "Der Text- oder Datendienst konnte kein verwendbares Ergebnis liefern.",
    "image": "Bildauswahl, Nutzungsrechte und Bildnachweis prüfen.",
    "availability": "Aktuelle Verfügbarkeit ist nicht ausreichend belegt.",
    "early_bird": "Rabattbedingungen und Gültigkeit prüfen.",
    "event": "Der Workshop ist nicht für aktuelle Werbung bestätigt.",
    "source": "Workshopdaten haben sich geändert oder sind nicht ausreichend belegt.",
    "copy": "Text und belegte Workshopangaben prüfen.",
    "review": "Angaben vor der Freigabe prüfen.",
}


def diagnostic_views(diagnostics) -> list[dict]:
    codes = []
    for diagnostic in diagnostics:
        field = diagnostic.field
        if field.startswith("draft.image"):
            code = "image"
        elif field == "draft.source":
            code = "source"
        elif field in {"generation.timeout", "generation.api", "generation.refusal", "generation.incomplete",
                     "generation.schema", "revision.timeout", "revision.api", "revision.refusal",
                     "revision.incomplete", "revision.schema", "http.timeout", "http.request", "http.status"}:
            code = "provider"
        else:
            prefix = field.split(".", 1)[0]
            code = {"image": "image", "availability": "availability", "early_bird": "early_bird",
                    "provenance": "event", "source": "source", "source_version": "source",
                    "content": "copy", "draft": "copy", "generation": "copy"}.get(prefix, "review")
        if code not in codes:
            codes.append(code)
    return [{"code": code, "message": _DIAGNOSTICS[code]} for code in codes]


def result_view(result: CommandResult) -> dict:
    diagnostics = diagnostic_views(result.diagnostics)
    status = result.status
    if status == "review_required" and any(d["code"] == "provider" for d in diagnostics):
        status = "provider_unavailable"
    return {"revision": result.revision, "round_reference": result.round_reference,
            "channel": result.channel, "status": status,
            "version_reference": result.version_reference, "approval_reference": result.approval_reference,
            "preparation_reference": result.submission_reference, "diagnostics": diagnostics,
            "readiness": "historical_check_only"}


def campaign_view(state: CampaignState, allowed_channels) -> dict:
    rounds = []
    for round_ in state.rounds:
        channels = []
        for channel in round_.channels:
            if channel.channel not in allowed_channels:
                continue
            versions = []
            for record in channel.versions:
                version = record.version
                content = version.content
                # Only reviewable channel fields. No source HTML, provenance, metadata or prompts.
                versions.append({"reference": record.reference,
                    "title": content.title,
                    "text": content.body if isinstance(content, GoogleBusinessDraft) else content.description,
                    "image_reference": version.selected_image_reference,
                    "booking_url": version.final_booking_url or version.canonical_booking_url,
                    "validation_status": version.validation.status,
                    "diagnostics": diagnostic_views(version.validation.diagnostics)})
            channels.append({"channel": channel.channel, "enabled": channel.enabled,
                "current_version": channel.current_version, "last_recorded_status": channel.last_status,
                "versions": versions,
                "approvals": [{"reference": a.reference, "version_reference": a.version_reference,
                               "status": "recorded"} for a in channel.approvals],
                "preparations": [{"reference": p.reference, "version_reference": p.version_reference,
                                  "approval_reference": p.approval_reference, "status": "recorded"}
                                 for p in channel.submissions],
                "diagnostics": diagnostic_views(channel.diagnostics), "current_readiness": "not_checked"})
        rounds.append({"reference": round_.reference, "purpose": round_.purpose, "channels": channels})
    return {"campaign_reference": state.campaign_reference, "workshop_reference": state.workshop_reference,
            "revision": state.revision, "rounds": rounds, "current_readiness": "not_checked"}
