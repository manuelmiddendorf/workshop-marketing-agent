"""Side-effect-free submission snapshots, never a send or publication result."""

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from .feed import FeedImportResult
from .generation import GoogleBusinessDraft, RausgegangenDraft, RausgegangenFactSheet, _price_text
from .models import _booking_url
from .revision import (
    DraftApproval, DraftVersion, _approval_payload, _aware_utc,
    _fingerprint_payload, check_approval_readiness,
)
from .validation import Diagnostic

RAUSGEGANGEN_PORTAL = "https://zentrale.rausgegangen.de/"


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class GoogleDate(_Frozen):
    year: int
    month: int
    day: int

    @classmethod
    def from_date(cls, value: date) -> "GoogleDate":
        return cls(year=value.year, month=value.month, day=value.day)


class GoogleTime(_Frozen):
    hours: int
    minutes: int
    seconds: Literal[0] = 0
    nanos: Literal[0] = 0

    @classmethod
    def from_time(cls, value: time) -> "GoogleTime":
        return cls(hours=value.hour, minutes=value.minute)


class GoogleSchedule(_Frozen):
    startDate: GoogleDate
    startTime: GoogleTime
    endDate: GoogleDate
    endTime: GoogleTime


class GoogleEvent(_Frozen):
    title: str
    schedule: GoogleSchedule


class GoogleCallToAction(_Frozen):
    actionType: Literal["BOOK"] = "BOOK"
    url: str


class GoogleMedia(_Frozen):
    # LocalPost's resource reference supports only sourceUrl for MediaItem.
    sourceUrl: str


class GoogleSubmissionPayload(_Frozen):
    languageCode: Literal["de-DE"] = "de-DE"
    topicType: Literal["EVENT"] = "EVENT"
    summary: str
    event: GoogleEvent
    callToAction: GoogleCallToAction
    media: tuple[GoogleMedia, ...] | None = None

    def provider_payload(self) -> dict[str, object]:
        """Only documented provider fields; omit media for text-only approval."""
        return self.model_dump(mode="json", exclude_none=True)


class RausgegangenCopyPackage(_Frozen):
    title: str
    description: str
    fact_sheet: RausgegangenFactSheet
    external_booking_link: str
    image_reference: str
    price_note: str
    portal_destination: Literal["https://zentrale.rausgegangen.de/"] = RAUSGEGANGEN_PORTAL
    copy_instructions: tuple[str, ...] = (
        "Titel und Beschreibung unverändert in die passenden Event-Felder kopieren.",
        "Termin und Ort anhand des Faktenblatts prüfen; den Preis nicht als Kassenzahlungszusage formulieren.",
        "Den freigegebenen Link beim Termin als externen Ticketlink eintragen.",
        "Das freigegebene Bild verwenden; einen erforderlichen Bildnachweis in der Beschreibung beibehalten.",
        "Pflichtfelder und Kontoberechtigungen im Portal prüfen. Vor dem Senden aktuelle Workshop-Daten erneut prüfen.",
        "Bereit zum Kopieren bedeutet weder eingereicht noch veröffentlicht.",
    )


@dataclass(frozen=True, slots=True)
class PreparedSubmission:
    version: DraftVersion
    approval: DraftApproval
    checked_at: datetime
    payload: GoogleSubmissionPayload | RausgegangenCopyPackage

    @property
    def fingerprint(self) -> str:
        payload = (
            self.payload.provider_payload() if isinstance(self.payload, GoogleSubmissionPayload)
            else self.payload.model_dump(mode="json")
        )
        return _fingerprint_payload({
            "serialization": "pilot-submission.v1",
            "version": _approval_payload(self.version),
            "approval": {
                "version_fingerprint": self.approval.version_fingerprint,
                "approving_person_reference": self.approval.approving_person_reference,
                "approved_at": self.approval.approved_at.isoformat(),
            },
            "checked_at": self.checked_at.isoformat(),
            "payload": payload,
        })


@dataclass(frozen=True, slots=True)
class PreparationResult:
    status: Literal["ready", "ready_to_copy", "outdated", "review_required"]
    package: PreparedSubmission | None
    diagnostics: tuple[Diagnostic, ...]


def _blocked(field: str, message: str) -> PreparationResult:
    return PreparationResult("review_required", None, (
        Diagnostic(field, "limitation", message, "preparation"),
    ))


def _check(
    version: DraftVersion, approval: DraftApproval | None,
    imported: FeedImportResult, now: datetime, channel: str,
) -> PreparationResult | None:
    if version.channel != channel or version.content.channel != channel:
        return _blocked("preparation.channel", "Draft belongs to another channel")
    if approval is None or not approval.matches(version):
        return _blocked("preparation.approval", "An approval matching the complete exact version is required")
    if approval.approved_at > now or version.created_at > now:
        return _blocked("preparation.time", "Approval and version cannot be in the future")
    if version.campaign is None or version.final_booking_url is None:
        return _blocked("preparation.tracking", "Tracking must be bound and approved before preparation")
    readiness = check_approval_readiness(version, imported, now=now)
    if not readiness.ready:
        return PreparationResult(readiness.status, None, readiness.diagnostics)
    # Reparse public typed records to reject unchecked model_copy updates.
    content_type = GoogleBusinessDraft if channel == "google_business" else RausgegangenDraft
    try:
        content_type.model_validate_json(version.content.model_dump_json())
    except ValidationError:
        return _blocked("preparation.content", "Channel content does not satisfy its typed schema")
    if version.selected_image_reference is not None:
        image = imported.validation.workshop.image
        # The readiness check has already required the exact supported reference.
        text = version.content.body if channel == "google_business" else version.content.description
        if image.attribution_required and (not image.credit or image.credit not in text):
            return _blocked(
                "preparation.image.credit",
                "Required image credit must already occur in the approved body or description",
            )
        if channel == "google_business":
            try:
                _booking_url(version.selected_image_reference)
            except ValueError:
                return _blocked(
                    "preparation.image.source_url",
                    "Google media needs an absolute HTTPS source URL; unresolved aliases are not converted",
                )
    return None


def prepare_google_submission(
    version: DraftVersion, approval: DraftApproval | None,
    current_import: FeedImportResult, *, now: datetime,
) -> PreparationResult:
    """Map the approved Google event exactly; never modify it or call Google."""
    now = _aware_utc(now, field="now")
    failure = _check(version, approval, current_import, now, "google_business")
    if failure is not None:
        return failure
    payload = _google_payload(version)
    return PreparationResult(
        "ready", PreparedSubmission(version, approval, now, payload), current_import.diagnostics,
    )


def _google_payload(version: DraftVersion) -> GoogleSubmissionPayload:
    """Exact field mapping, also used to verify restored historical packages."""
    draft = version.content
    schedule = draft.schedule
    day = GoogleDate.from_date(schedule.local_date)
    return GoogleSubmissionPayload(
        summary=draft.body,
        event=GoogleEvent(title=draft.title, schedule=GoogleSchedule(
            startDate=day, endDate=day,
            startTime=GoogleTime.from_time(schedule.start_time),
            endTime=GoogleTime.from_time(schedule.end_time),
        )),
        callToAction=GoogleCallToAction(url=version.final_booking_url),
        media=(GoogleMedia(sourceUrl=version.selected_image_reference),)
        if version.selected_image_reference is not None else None,
    )


def prepare_rausgegangen_submission(
    version: DraftVersion, approval: DraftApproval | None,
    current_import: FeedImportResult, *, now: datetime,
) -> PreparationResult:
    """Prepare exact copy fields and instructions for a later authorized manual step."""
    now = _aware_utc(now, field="now")
    failure = _check(version, approval, current_import, now, "rausgegangen")
    if failure is not None:
        return failure
    payload = _rausgegangen_payload(version)
    return PreparationResult(
        "ready_to_copy", PreparedSubmission(version, approval, now, payload), current_import.diagnostics,
    )


def _rausgegangen_payload(version: DraftVersion) -> RausgegangenCopyPackage:
    """Exact copy mapping without evaluating present-day eligibility."""
    draft = version.content
    return RausgegangenCopyPackage(
        title=draft.title, description=draft.description, fact_sheet=draft.fact_sheet,
        external_booking_link=version.final_booking_url,
        image_reference=version.selected_image_reference,
        price_note=_price_text(version.workshop_facts),
    )
