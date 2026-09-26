"""Read one explicitly configured public feed without changing workshop evidence."""

import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from http.client import HTTPException, HTTPSConnection
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError

from .models import Instant
from .validation import Diagnostic, ValidationResult, validate_workshop


MAX_RESPONSE_BYTES = 1_048_576


@dataclass(frozen=True)
class FeedResponse:
    """The injectable HTTP boundary returns a status and undecoded body bytes."""

    status: int
    body: bytes


@dataclass(frozen=True)
class FeedImportResult:
    validation: ValidationResult | None = None
    generated_at: datetime | None = None
    import_diagnostics: tuple[Diagnostic, ...] = ()

    @property
    def diagnostics(self) -> tuple[Diagnostic, ...]:
        original = self.validation.diagnostics if self.validation is not None else ()
        return self.import_diagnostics + original

    @property
    def promotion_eligible(self) -> bool:
        """Core usability plus feed/event checks, never permission to publish."""
        workshop = self.validation.workshop if self.validation is not None else None
        provenance = workshop.provenance if workshop is not None else {}
        return (
            self.validation is not None
            and self.validation.usable
            and not self.import_diagnostics
            and provenance.get("active") is True
            and provenance.get("event_status") == "scheduled"
        )


class _Envelope(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    schema_version: str
    generated_at: Instant
    workshop: dict[str, JsonValue]


def _http_get(endpoint: str, timeout: float) -> FeedResponse:
    """One HTTPS GET, with a socket timeout, bounded body and no redirects/retries."""
    url = urlsplit(endpoint)
    connection = HTTPSConnection(url.hostname, url.port, timeout=timeout)
    path = url.path or "/"
    if url.query:
        path += "?" + url.query
    try:
        connection.request("GET", path, headers={"Accept": "application/json"})
        response = connection.getresponse()
        # Error pages are not evidence and need not be downloaded or exposed.
        body = response.read(MAX_RESPONSE_BYTES + 1) if response.status == 200 else b""
        return FeedResponse(response.status, body)
    finally:
        connection.close()


def _unique_object(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("Non-finite JSON constant")


def _finite_float(value: str) -> float:
    number = float(value)
    # Inspect the mantissa without parsing arbitrarily large exponents again.
    nonzero_mantissa = any(digit in "123456789" for digit in value.lower().split("e")[0])
    if not math.isfinite(number) or number == 0 and nonzero_mantissa:
        raise ValueError("JSON number overflows or underflows the supported range")
    return number


def load_public_workshop(
    *,
    endpoint: str,
    workshop_id: str,
    timeout: float,
    now: datetime,
    http_get: Callable[[str, float], FeedResponse] = _http_get,
) -> FeedImportResult:
    """Fetch workshop-data.v1 and retain the existing validator's full result.

    Configuration errors raise ValueError before HTTP. Feed/transport failures
    return diagnostics and no validation result. The caller supplies the complete
    endpoint, expected identity, finite positive socket timeout and aware time.
    """
    if not isinstance(workshop_id, str) or not workshop_id.strip():
        raise ValueError("workshop_id must be a nonblank string")
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout must be finite and positive")
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be an explicitly supplied offset-aware datetime")
    try:
        now.astimezone(UTC)
    except OverflowError as error:
        raise ValueError("now must be representable in UTC") from error
    if not isinstance(endpoint, str):
        raise ValueError("endpoint must be an absolute HTTPS URL")
    url = urlsplit(endpoint)
    if (
        url.scheme != "https" or not url.hostname or url.fragment
        or url.username is not None or url.password is not None
        or any(character.isspace() or ord(character) < 32 for character in endpoint)
        or "\\" in endpoint
    ):
        raise ValueError("endpoint must be an absolute HTTPS URL without credentials or fragment")
    url.port  # Reject invalid ports before making a request.

    def failed(field: str, kind: str, message: str) -> FeedImportResult:
        return FeedImportResult(import_diagnostics=(Diagnostic(field, kind, message, "promotion"),))

    try:
        response = http_get(endpoint, timeout)
    except TimeoutError:
        return failed("http.timeout", "limitation", "Public feed request timed out")
    except (OSError, HTTPException):
        return failed("http.request", "invalid", "Public feed request failed")
    if response.status != 200:
        return failed("http.status", "invalid", f"Expected HTTP 200; received {response.status}")
    if len(response.body) > MAX_RESPONSE_BYTES:
        return failed("http.body", "invalid", "Public feed exceeds the 1 MiB response limit")
    try:
        payload = json.loads(
            response.body,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
    except (ValueError, UnicodeError, RecursionError):
        return failed("envelope", "invalid", "Invalid JSON syntax, encoding, numeric range or duplicate keys")
    if not isinstance(payload, dict):
        return failed("envelope", "invalid", "Expected a JSON object envelope")
    if "schema_version" not in payload:
        return failed("envelope.schema_version", "missing", "Schema version is required")
    if payload["schema_version"] != "workshop-data.v1":
        return failed("envelope.schema_version", "invalid", "Unsupported schema version; expected workshop-data.v1")
    if payload.get("workshop") is None:
        return failed("envelope.workshop", "missing", "The requested workshop is missing")
    try:
        envelope = _Envelope.model_validate(payload)
    except ValidationError as error:
        diagnostics = tuple(
            Diagnostic(
                "envelope." + ".".join(map(str, item["loc"])),
                "missing" if item["type"] == "missing" else "invalid",
                item["msg"],
                "promotion",
            )
            for item in error.errors(include_input=False, include_url=False)
        )
        return FeedImportResult(import_diagnostics=diagnostics)
    identity = envelope.workshop.get("identity")
    if not isinstance(identity, dict) or identity.get("workshop_id") != workshop_id:
        return failed("identity.workshop_id", "conflicting", "Feed workshop identity does not match the request")

    # Do not promote envelope timestamps to source versions or aggregate evidence.
    validation = validate_workshop(envelope.workshop, now=now)
    status_diagnostics = []
    provenance = envelope.workshop.get("provenance")
    provenance = provenance if isinstance(provenance, dict) else {}
    for field, expected in (("active", True), ("event_status", "scheduled")):
        value = provenance.get(field)
        matches = value is True if field == "active" else value == expected
        if not matches:
            status_diagnostics.append(Diagnostic(
                "provenance." + field,
                "missing" if value is None else "limitation",
                f"Current promotion requires {field}={expected!r}",
                "promotion",
            ))
    return FeedImportResult(validation, envelope.generated_at, tuple(status_diagnostics))
