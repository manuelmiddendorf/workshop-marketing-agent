"""Independent workshop evidence; parsing does not establish factual truth."""

import re
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Annotated, Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    HttpUrl,
    JsonValue,
    PlainSerializer,
    TypeAdapter,
)


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("Expected nonblank text")
    return value


def _money(value: object) -> Decimal:
    if isinstance(value, str) and re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", value):
        return Decimal(value)
    if isinstance(value, Decimal) and value.is_finite() and not value.is_signed():
        return value
    raise ValueError("Expected an unsigned decimal string or nonnegative finite Decimal; floats are not accepted")


def _date(value: object) -> date:
    if type(value) is date:
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        return date.fromisoformat(value)
    raise ValueError("Expected a calendar date in YYYY-MM-DD form")


def _time(value: object) -> time:
    if isinstance(value, str) and re.fullmatch(r"[0-9]{2}:[0-9]{2}", value):
        value = time.fromisoformat(value)
    if type(value) is time and value.tzinfo is None and not value.second and not value.microsecond:
        return value
    raise ValueError("Expected a local time in HH:MM form, without an offset")


def _instant(value: object) -> datetime:
    if isinstance(value, str) and re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
        r"(?:\.[0-9]{1,6})?(?:Z|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9])", value
    ):
        value = datetime.fromisoformat(value)
    if isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None:
        try:
            value.astimezone(UTC)
        except OverflowError as error:
            raise ValueError("Instant falls outside the supported UTC calendar range") from error
        return value
    raise ValueError("Expected an offset-aware datetime")


def _zone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError("Expected an available IANA time zone") from error
    return value


_http_url = TypeAdapter(HttpUrl)


def _booking_url(value: str) -> str:
    _http_url.validate_python(value)
    parts = urlsplit(value)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or "." not in parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
        or any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value)
        or "\\" in value
        or re.search(r"%(?![0-9a-fA-F]{2})", value)
    ):
        raise ValueError("Expected an absolute HTTPS page URL without credentials, whitespace or a fragment")
    # Accessing port also rejects malformed or out-of-range ports.
    parts.port
    return value


Text = Annotated[str, AfterValidator(_nonblank)]
Money = Annotated[Decimal, BeforeValidator(_money), PlainSerializer(lambda value: format(value, "f"), return_type=str, when_used="json")]
LocalDate = Annotated[date, BeforeValidator(_date)]
LocalTime = Annotated[time, BeforeValidator(_time), PlainSerializer(lambda value: value.isoformat(timespec="minutes"), return_type=str, when_used="json")]
Instant = Annotated[datetime, BeforeValidator(_instant)]
TimeZone = Annotated[str, AfterValidator(_zone)]
BookingURL = Annotated[str, AfterValidator(_booking_url)]
Count = Annotated[int, Field(ge=0)]
PositiveSeconds = Annotated[int, Field(gt=0)]
Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
Origin = Literal["source", "public_page", "studio_configuration", "workshop_exception"]
PricingUnit = Literal["person", "parent_child_family"]
QuotaUnit = Literal["discounted_person", "family_booking"]


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, validate_default=True)


class Evidence(_Record):
    status: Literal["unknown", "verified"] = "unknown"
    source_ref: Text | None = None


class SourceVerification(Evidence):
    workshop_id: Text | None = None
    source_version: Text | None = None
    exceptions_reviewed: bool | None = None


class Identity(_Record):
    workshop_id: Text | None = None
    public_route_id: Text | None = None
    member_route_id: Text | None = None
    cross_route_status: Literal["unverified", "verified"] = "unverified"


class Content(_Record):
    title: str | None = None
    public_summary_html: str | None = None
    public_description_html: str | None = None
    member_description_html: str | None = None
    public_copy_status: Literal["source_selection_unverified", "confirmed_public"] = "source_selection_unverified"


class Schedule(_Record):
    local_date: LocalDate | None = None
    start_time: LocalTime | None = None
    end_time: LocalTime | None = None
    time_zone: TimeZone | None = None
    time_zone_origin: Origin | None = None
    schedule_text: str | None = None
    explicit_exceptions: list[Text] | None = None
    start_at: Instant | None = None
    end_at: Instant | None = None


class Pricing(_Record):
    displayed_price: Money | None = None
    displayed_price_kind: Literal["unknown", "regular", "early_bird"] = "unknown"
    regular_price: Money | None = None
    currency: Currency | None = None
    currency_origin: Origin | None = None
    pricing_unit: PricingUnit | None = None


class EarlyBird(_Record):
    status: Literal["unknown", "absent", "present"] = "unknown"
    verification: Evidence = Field(default_factory=Evidence)
    price: Money | None = None
    final_date: LocalDate | None = None
    cutoff_exclusive: Instant | None = None
    restriction_text: str | None = None
    restrictions_reviewed: bool | None = None
    quota_limit: Count | None = None
    quota_unit: QuotaUnit | None = None


class Availability(_Record):
    status: Literal["unknown", "verified", "stale"] = "unknown"
    verification: Evidence = Field(default_factory=Evidence)
    source: Text | None = None
    workshop_id: Text | None = None
    shared_pool: bool | None = None
    observed_at: Instant | None = None
    max_age_seconds: PositiveSeconds | None = None
    person_unit: Literal["person"] | None = None
    remaining_persons: Count | None = None
    early_bird_unit: QuotaUnit | None = None
    remaining_early_bird_units: Count | None = None


class Image(_Record):
    reference: Text | None = None
    public_use_status: str = "unknown"
    marketing_permission: bool | None = None
    attribution_required: bool | None = None
    credit: str | None = None


class Age(_Record):
    minimum: Count | None = None
    maximum: Count | None = None


class Conflict(_Record):
    field: Text
    message: Text


class StudioDefaults(_Record):
    time_zone: Literal["Europe/Berlin"] | None = None
    currency: Literal["EUR"] | None = None


class WorkshopInput(_Record):
    identity: Identity = Field(default_factory=Identity)
    source_version: Text | None = None
    verification: SourceVerification = Field(default_factory=SourceVerification)
    content: Content = Field(default_factory=Content)
    schedule: Schedule = Field(default_factory=Schedule)
    location: str | None = None
    booking_url: BookingURL | None = None
    pricing: Pricing = Field(default_factory=Pricing)
    early_bird: EarlyBird = Field(default_factory=EarlyBird)
    availability: Availability = Field(default_factory=Availability)
    image: Image | None = None
    audience: str | None = None
    age: Age | None = None
    included_services: list[Text] | None = None
    provenance: dict[str, JsonValue] = Field(default_factory=dict)
    gaps: list[str] = Field(default_factory=list)
    conflicts: list[Conflict] = Field(default_factory=list)
