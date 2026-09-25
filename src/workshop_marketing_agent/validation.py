"""Offline consistency checks and limitations on using supplied workshop facts."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from .models import Evidence, StudioDefaults, WorkshopInput


@dataclass(frozen=True)
class Diagnostic:
    field: str
    kind: Literal["missing", "invalid", "conflicting", "limitation"]
    message: str
    claim: str


@dataclass(frozen=True)
class ClaimSupport:
    current_discount: bool = False
    no_early_bird_offer: bool = False
    remaining_persons: bool = False
    remaining_early_bird_units: bool = False
    image: bool = False
    audience: bool = False
    age: bool = False
    included_services: bool = False


@dataclass(frozen=True)
class ValidationResult:
    workshop: WorkshopInput | None
    usable: bool
    diagnostics: tuple[Diagnostic, ...]
    claims: ClaimSupport
    early_bird_cutoff: datetime | None = None


def _local_instants(day: date, local_time: time, zone: ZoneInfo) -> set[datetime]:
    """Round trips detect gaps and keep both possible instants during a fold."""
    wall = datetime.combine(day, local_time)
    candidates = set()
    for fold in (0, 1):
        instant = wall.replace(tzinfo=zone, fold=fold).astimezone(UTC)
        if instant.astimezone(zone).replace(tzinfo=None) == wall:
            candidates.add(instant)
    return candidates


def _apply_defaults(workshop: WorkshopInput, defaults: StudioDefaults | None) -> None:
    if defaults is None or workshop.verification.exceptions_reviewed is not True:
        return
    if workshop.schedule.explicit_exceptions != []:
        return
    for record, field, origin, value in (
        (workshop.schedule, "time_zone", "time_zone_origin", defaults.time_zone),
        (workshop.pricing, "currency", "currency_origin", defaults.currency),
    ):
        if value is not None and getattr(record, field) is None and getattr(record, origin) in (None, "studio_configuration"):
            setattr(record, field, value)
            setattr(record, origin, "studio_configuration")


def validate_workshop(
    data: dict[str, object], *, now: datetime, defaults: StudioDefaults | None = None
) -> ValidationResult:
    """Check supplied evidence; never authenticate it or authorize publication.

    ``now`` is mandatory so deadlines and freshness can be reproduced offline.
    A usable result supports core facts; optional claims have separate gates.
    """
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be an explicitly supplied offset-aware datetime")
    try:
        now = now.astimezone(UTC)
    except OverflowError as error:
        raise ValueError("now must be representable in UTC") from error
    try:
        workshop = WorkshopInput.model_validate(data).model_copy(deep=True)
    except ValidationError as error:
        diagnostics = tuple(
            Diagnostic(".".join(map(str, entry["loc"])) or "$", "invalid", entry["msg"], "snapshot")
            for entry in error.errors(include_input=False, include_url=False)
        )
        return ValidationResult(None, False, diagnostics, ClaimSupport())

    _apply_defaults(workshop, defaults)
    diagnostics: list[Diagnostic] = []

    def issue(field: str, kind: str, message: str, claim: str = "snapshot") -> None:
        diagnostics.append(Diagnostic(field, kind, message, claim))

    def required(field: str, value: object, claim: str = "snapshot") -> None:
        if value is None or isinstance(value, str) and not value.strip():
            issue(field, "missing", "Required evidence is unknown or blank", claim)

    def verified(field: str, evidence: Evidence, claim: str) -> None:
        if evidence.status != "verified":
            issue(field + ".status", "missing", "Supplied verification is required", claim)
        required(field + ".source_ref", evidence.source_ref, claim)

    identity, verification = workshop.identity, workshop.verification
    content, schedule, pricing = workshop.content, workshop.schedule, workshop.pricing
    offer, availability, image = workshop.early_bird, workshop.availability, workshop.image
    for field, value in (
        ("identity.workshop_id", identity.workshop_id),
        ("source_version", workshop.source_version),
        ("content.title", content.title),
        ("content.public_description_html", content.public_description_html),
        ("schedule.local_date", schedule.local_date),
        ("schedule.start_time", schedule.start_time),
        ("schedule.end_time", schedule.end_time),
        ("schedule.time_zone", schedule.time_zone),
        ("schedule.time_zone_origin", schedule.time_zone_origin),
        ("schedule.explicit_exceptions", schedule.explicit_exceptions),
        ("location", workshop.location),
        ("booking_url", workshop.booking_url),
        ("pricing.regular_price", pricing.regular_price),
        ("pricing.currency", pricing.currency),
        ("pricing.currency_origin", pricing.currency_origin),
        ("pricing.pricing_unit", pricing.pricing_unit),
    ):
        required(field, value)
    verified("verification", verification, "snapshot")
    for field, value, expected in (
        ("verification.workshop_id", verification.workshop_id, identity.workshop_id),
        ("verification.source_version", verification.source_version, workshop.source_version),
    ):
        required(field, value)
        if value is not None and expected is not None and value != expected:
            issue(field, "conflicting", "Verification does not match the supplied snapshot")
    if verification.exceptions_reviewed is not True:
        issue("verification.exceptions_reviewed", "missing", "Explicit exception review is required")
    if content.public_copy_status != "confirmed_public":
        issue("content.public_copy_status", "missing", "Public-copy selection has not been confirmed")

    if schedule.start_time is not None and schedule.end_time is not None and schedule.end_time <= schedule.start_time:
        issue("schedule.end_time", "conflicting", "Only same-day events ending after their start are supported")
    if schedule.local_date is not None and schedule.time_zone is not None:
        zone = ZoneInfo(schedule.time_zone)
        instants = {}
        for name in ("start", "end"):
            local_time = getattr(schedule, name + "_time")
            supplied = getattr(schedule, name + "_at")
            if local_time is None:
                continue
            try:
                candidates = _local_instants(schedule.local_date, local_time, zone)
            except OverflowError:
                issue("schedule." + name + "_time", "invalid", "Local time falls outside the supported UTC calendar range")
                continue
            if not candidates:
                issue("schedule." + name + "_time", "invalid", "Local time does not exist in this zone")
            elif supplied is not None and supplied.astimezone(UTC) not in candidates:
                issue("schedule." + name + "_at", "conflicting", "Instant disagrees with the local date, time or zone")
            elif len(candidates) > 1 and supplied is None:
                issue("schedule." + name + "_at", "missing", "Ambiguous local time needs an explicit matching instant")
            else:
                instants[name] = supplied.astimezone(UTC) if supplied is not None else next(iter(candidates))
        if len(instants) == 2 and instants["end"] <= instants["start"]:
            issue("schedule.end_at", "conflicting", "End instant must be after start instant")

    if pricing.displayed_price is not None:
        if pricing.displayed_price_kind == "unknown":
            issue("pricing.displayed_price_kind", "missing", "Displayed price classification is unknown")
        elif pricing.displayed_price_kind == "regular":
            if pricing.regular_price is not None and pricing.displayed_price != pricing.regular_price:
                issue("pricing.displayed_price", "conflicting", "Displayed regular price differs from regular price")
        elif offer.status != "present" or offer.price is None or pricing.displayed_price != offer.price:
            issue("pricing.displayed_price", "conflicting", "Displayed early-bird price needs a matching present offer")
    elif pricing.displayed_price_kind != "unknown":
        issue("pricing.displayed_price", "missing", "Classified displayed price has no amount")

    quota_unit = {"person": "discounted_person", "parent_child_family": "family_booking"}.get(pricing.pricing_unit)
    cutoff = None
    if offer.status == "unknown":
        issue("early_bird.status", "missing", "Offer existence remains unknown", "offer")
    else:
        verified("early_bird.verification", offer.verification, "offer")
    if offer.status == "absent":
        for field in ("price", "final_date", "cutoff_exclusive", "restriction_text", "quota_limit", "restrictions_reviewed"):
            if getattr(offer, field) is not None:
                issue("early_bird." + field, "conflicting", "An absent offer cannot contain offer terms", "offer")
    if offer.status == "present":
        for field in ("price", "final_date", "quota_limit", "quota_unit"):
            required("early_bird." + field, getattr(offer, field), "offer")
        if offer.restrictions_reviewed is not True:
            issue("early_bird.restrictions_reviewed", "missing", "Offer restrictions need explicit review", "offer")
        if offer.restriction_text is None:
            issue("early_bird.restriction_text", "missing", "Unknown restrictions are not unrestricted terms", "offer")
        if quota_unit is not None and offer.quota_unit is not None and offer.quota_unit != quota_unit:
            issue("early_bird.quota_unit", "conflicting", "Discount unit disagrees with pricing unit", "offer")
        if offer.price is not None and pricing.regular_price is not None and offer.price >= pricing.regular_price:
            issue("early_bird.price", "conflicting", "A discount must be below the regular price", "offer")
        if offer.final_date is not None and schedule.local_date is not None and offer.final_date > schedule.local_date:
            issue("early_bird.final_date", "conflicting", "Offer final day is after the workshop date", "offer")
        if offer.final_date is not None and schedule.time_zone is not None:
            try:
                zone = ZoneInfo(schedule.time_zone)
                candidates = _local_instants(offer.final_date + timedelta(days=1), time.min, zone)
                if len(candidates) != 1:
                    raise ValueError("Next local midnight is not unique or does not exist")
                cutoff = next(iter(candidates)).astimezone(zone)
            except (ValueError, OverflowError) as error:
                issue("early_bird.final_date", "invalid", str(error), "offer")
            if cutoff is not None:
                if offer.cutoff_exclusive is not None and offer.cutoff_exclusive.astimezone(UTC) != cutoff.astimezone(UTC):
                    issue("early_bird.cutoff_exclusive", "conflicting", "Cutoff disagrees with the full final local day", "offer")
                if now >= cutoff.astimezone(UTC):
                    issue("early_bird.final_date", "limitation", "Offer is expired at the supplied current time", "discount")

    verified("availability.verification", availability.verification, "availability")
    for field in ("source", "workshop_id", "observed_at", "max_age_seconds"):
        required("availability." + field, getattr(availability, field), "availability")
    if availability.status != "verified":
        issue("availability.status", "limitation" if availability.status == "stale" else "missing", "Aggregate must be supplied as verified and fresh", "availability")
    if availability.shared_pool is not True:
        issue("availability.shared_pool", "missing", "Aggregate must cover the shared member/public pool", "availability")
    if availability.workshop_id is not None and availability.workshop_id != identity.workshop_id:
        issue("availability.workshop_id", "conflicting", "Aggregate belongs to a different or unknown workshop", "availability")
    if availability.observed_at is not None:
        age = (now - availability.observed_at.astimezone(UTC)).total_seconds()
        if age < 0:
            issue("availability.observed_at", "invalid", "Aggregate observation is in the future", "availability")
        elif availability.max_age_seconds is not None and age > availability.max_age_seconds:
            issue("availability.observed_at", "limitation", "Aggregate is stale under the supplied freshness policy", "availability")
    for field, claim in (("person_unit", "remaining_persons"), ("remaining_persons", "remaining_persons"), ("early_bird_unit", "remaining_early_bird_units"), ("remaining_early_bird_units", "remaining_early_bird_units")):
        required("availability." + field, getattr(availability, field), claim)
    if availability.early_bird_unit is not None and quota_unit is not None and availability.early_bird_unit != quota_unit:
        issue("availability.early_bird_unit", "conflicting", "Aggregate discount unit disagrees with pricing unit", "remaining_early_bird_units")
    if availability.remaining_early_bird_units is not None:
        if offer.quota_limit is not None and availability.remaining_early_bird_units > offer.quota_limit:
            issue("availability.remaining_early_bird_units", "conflicting", "Remaining discount units exceed the supplied limit", "remaining_early_bird_units")
        if offer.status == "absent" and availability.remaining_early_bird_units > 0:
            issue("availability.remaining_early_bird_units", "conflicting", "Discount units contradict an absent offer", "offer")
            issue("availability.remaining_early_bird_units", "conflicting", "Discount units contradict an absent offer", "remaining_early_bird_units")

    required("audience", workshop.audience, "audience")
    required("included_services", workshop.included_services, "included_services")
    if workshop.age is None or workshop.age.minimum is None and workshop.age.maximum is None:
        issue("age", "missing", "Age bounds are unknown", "age")
    elif workshop.age.minimum is not None and workshop.age.maximum is not None and workshop.age.minimum > workshop.age.maximum:
        issue("age.maximum", "conflicting", "Maximum age is below minimum age", "age")
    if image is None:
        issue("image", "missing", "Image and usage evidence are unknown", "image")
    else:
        required("image.reference", image.reference, "image")
        if image.marketing_permission is not True:
            issue("image.marketing_permission", "missing" if image.marketing_permission is None else "limitation", "Explicit image permission is required", "image")
        required("image.attribution_required", image.attribution_required, "image")
        if image.attribution_required is True:
            required("image.credit", image.credit, "image")

    # Supplied conflicts are evidence, not instructions or replacements for facts.
    fields = workshop.model_dump()
    for conflict in workshop.conflicts:
        target = fields
        for part in conflict.field.split("."):
            if not isinstance(target, dict) or part not in target:
                issue(conflict.field, "invalid", "Conflict names an unknown input path")
                break
            target = target[part]
        else:
            prefix = conflict.field.split(".")[0]
            claim = {"early_bird": "offer", "availability": "availability", "image": "image", "audience": "audience", "age": "age", "included_services": "included_services"}.get(prefix, "snapshot")
            issue(conflict.field, "conflicting", conflict.message, claim)

    blocked = {diagnostic.claim for diagnostic in diagnostics}
    usable = "snapshot" not in blocked
    persons = usable and not blocked.intersection({"availability", "remaining_persons"})
    discounts = usable and not blocked.intersection({"availability", "remaining_early_bird_units"})
    current_discount = bool(
        usable and offer.status == "present" and cutoff is not None
        and not blocked.intersection({"offer", "discount"}) and persons and discounts
        and availability.remaining_persons > 0 and availability.remaining_early_bird_units > 0
    )
    claims = ClaimSupport(
        current_discount=current_discount,
        no_early_bird_offer=usable and offer.status == "absent" and "offer" not in blocked,
        remaining_persons=bool(persons),
        remaining_early_bird_units=bool(discounts),
        image=usable and "image" not in blocked,
        audience=usable and "audience" not in blocked,
        age=usable and "age" not in blocked,
        included_services=usable and "included_services" not in blocked,
    )
    if offer.status == "present" and not current_discount:
        issue("early_bird", "limitation", "A current discount needs a usable snapshot, complete unexpired terms and positive verified counts", "discount")
    return ValidationResult(workshop, usable, tuple(diagnostics), claims, cutoff)
