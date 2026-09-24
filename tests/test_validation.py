import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from workshop_marketing_agent import StudioDefaults, WorkshopInput, validate_workshop


NOW = datetime(2026, 10, 24, 10, tzinfo=UTC)


def check(data, **kwargs):
    return validate_workshop(data, now=NOW, **kwargs)


def has_diagnostic(result, field, kind):
    return any(d.field == field and d.kind == kind for d in result.diagnostics)


def set_field(data, path, value):
    group, field = path.split(".")
    data[group][field] = value


def test_complete_synthetic_input(complete_input):
    result = check(complete_input)
    assert result.usable
    assert result.diagnostics == ()
    assert result.claims.current_discount
    assert result.claims.remaining_persons
    assert result.claims.remaining_early_bird_units
    assert result.claims.image and result.claims.audience and result.claims.age
    assert result.claims.included_services
    assert not result.claims.no_early_bird_offer
    assert result.workshop.pricing.regular_price == Decimal("120.00")


def test_pilot_remains_unchanged_and_incomplete():
    path = Path(__file__).resolve().parents[1] / "examples/workshops/mal-yoga-2026-10-18.json"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == "68a55b8f6222e200357362aac97e814791ef2e684f9a16dd7b433b63cd0ea43d"
    data = json.loads(path.read_text())
    result = check(data)
    assert result.workshop is not None
    assert not result.usable
    for field in ("identity.workshop_id", "source_version", "verification.status", "content.public_copy_status", "pricing.regular_price", "pricing.displayed_price_kind", "location", "schedule.explicit_exceptions"):
        assert has_diagnostic(result, field, "missing")
    assert result.workshop.provenance["displayed_remaining_places"] == 5
    assert result.workshop.availability.remaining_persons is None
    assert not result.claims.remaining_persons
    assert not result.claims.current_discount


def test_exact_copy_and_decimal_serialization(complete_input):
    text = "  <p>Öl &amp; Farbe</p>\n\n<strong>Unverändert.</strong>  "
    complete_input["content"]["public_description_html"] = text
    complete_input["pricing"]["regular_price"] = "120.00000000000000000001"
    before = copy.deepcopy(complete_input)
    result = check(complete_input)
    assert result.usable
    assert result.workshop.content.public_description_html == text
    encoded = json.loads(result.workshop.model_dump_json())
    assert encoded["pricing"]["regular_price"] == "120.00000000000000000001"
    assert encoded["early_bird"]["price"] == "95.00"
    assert encoded["schedule"]["start_time"] == "11:00"
    assert encoded["content"] == complete_input["content"]
    assert complete_input == before
    assert WorkshopInput.model_validate_json(result.workshop.model_dump_json()) == result.workshop


@pytest.mark.parametrize("value", [None, "", " \n "])
def test_no_member_fallback(complete_input, value):
    complete_input["content"]["public_description_html"] = value
    result = check(complete_input)
    assert not result.usable
    assert has_diagnostic(result, "content.public_description_html", "missing")
    assert result.workshop.content.public_description_html == value
    assert result.workshop.content.member_description_html


@pytest.mark.parametrize("value", [12.5, 12, True, -1, "-1.00", "NaN", "Infinity", "12,50", "1e2", " 12.50", "12EUR", "", Decimal("NaN"), Decimal("Infinity"), Decimal("-1")])
def test_invalid_money(complete_input, value):
    complete_input["pricing"]["regular_price"] = value
    result = check(complete_input)
    assert result.workshop is None and not result.usable
    assert has_diagnostic(result, "pricing.regular_price", "invalid")


@pytest.mark.parametrize("value", ["0", "0.00", Decimal("0.00"), "120.1234567890123456789"])
def test_exact_nonnegative_money(value):
    model = WorkshopInput.model_validate({"pricing": {"regular_price": value}})
    assert json.loads(model.model_dump_json())["pricing"]["regular_price"] == str(value)


def test_no_implicit_defaults_or_optional_claims(complete_input):
    for group, value in (("early_bird", {}), ("availability", {}), ("image", None), ("audience", None), ("age", None), ("included_services", None)):
        complete_input[group] = value
    complete_input["pricing"].update(displayed_price=None, displayed_price_kind="unknown")
    result = check(complete_input)
    assert result.usable
    assert not any(vars(result.claims).values())
    assert result.workshop.pricing.displayed_price is None
    assert result.workshop.early_bird.status == "unknown"
    assert not result.claims.no_early_bird_offer
    empty = check({})
    assert empty.workshop.schedule.time_zone is None
    assert empty.workshop.pricing.currency is None
    assert not empty.usable


def test_explicit_defaults_record_origins_without_mutation(complete_input):
    complete_input["schedule"].update(time_zone=None, time_zone_origin=None)
    complete_input["pricing"].update(currency=None, currency_origin=None)
    assert not check(complete_input).usable
    result = check(complete_input, defaults=StudioDefaults(time_zone="Europe/Berlin", currency="EUR"))
    assert result.usable
    assert result.workshop.schedule.time_zone == "Europe/Berlin"
    assert result.workshop.schedule.time_zone_origin == "studio_configuration"
    assert result.workshop.pricing.currency == "EUR"
    assert result.workshop.pricing.currency_origin == "studio_configuration"
    assert complete_input["schedule"]["time_zone"] is None


def test_explicit_exceptions_are_preserved(complete_input):
    complete_input["schedule"].update(time_zone="America/New_York", time_zone_origin="workshop_exception", explicit_exceptions=["Termin in New York; Preise in US-Dollar."])
    complete_input["pricing"].update(currency="USD", currency_origin="workshop_exception")
    complete_input["early_bird"]["cutoff_exclusive"] = None
    result = check(complete_input, defaults=StudioDefaults(time_zone="Europe/Berlin", currency="EUR"))
    assert result.usable
    assert result.workshop.schedule.time_zone == "America/New_York"
    assert result.workshop.pricing.currency == "USD"
    assert result.workshop.schedule.explicit_exceptions == complete_input["schedule"]["explicit_exceptions"]


@pytest.mark.parametrize("exceptions,reviewed,origin", [(None, True, None), ([], False, None), (["Abweichender Ort/Zeitbezug"], True, None), ([], True, "workshop_exception")])
def test_defaults_do_not_resolve_unreviewed_or_conflicting_evidence(complete_input, exceptions, reviewed, origin):
    complete_input["schedule"].update(time_zone=None, time_zone_origin=origin, explicit_exceptions=exceptions)
    complete_input["verification"]["exceptions_reviewed"] = reviewed
    result = check(complete_input, defaults=StudioDefaults(time_zone="Europe/Berlin"))
    assert not result.usable
    assert result.workshop.schedule.time_zone is None


@pytest.mark.parametrize("path,value", [
    ("schedule.local_date", "2026-02-30"), ("schedule.local_date", "2026-1-8"),
    ("schedule.start_time", "24:00"), ("schedule.start_time", "11:00:30"),
    ("schedule.start_time", "11:00+02:00"), ("schedule.time_zone", "Not/AZone"),
    ("schedule.start_at", "2026-11-08T11:00:00"),
    ("pricing.pricing_unit", "pair"), ("pricing.currency", "eur"),
    ("availability.remaining_persons", True), ("availability.remaining_persons", -1),
    ("availability.remaining_persons", "5"), ("availability.remaining_early_bird_units", 2.0),
    ("availability.max_age_seconds", 0), ("early_bird.quota_limit", -1),
])
def test_invalid_structured_values(complete_input, path, value):
    set_field(complete_input, path, value)
    result = check(complete_input)
    assert not result.usable
    assert has_diagnostic(result, path, "invalid")


@pytest.mark.parametrize("url", ["/workshops/demo", "http://example.org/a", "https:///a", "https://user:secret@example.org/a", "https://example.org/a#pay", "https://example.org:99999/a", "https://example.org/a b", "https://example.org/%ZZ", "https://localhost/a", "https://example.org\\other/a"])
def test_invalid_booking_page_urls(complete_input, url):
    complete_input["booking_url"] = url
    result = check(complete_input)
    assert has_diagnostic(result, "booking_url", "invalid")
    assert not result.usable


@pytest.mark.parametrize("path,value", [("schedule.end_time", "10:00"), ("schedule.start_at", "2026-11-08T11:00:00+02:00"), ("schedule.start_at", "2026-11-09T11:00:00+01:00"), ("verification.workshop_id", "other"), ("verification.source_version", "other")])
def test_core_contradictions(complete_input, path, value):
    set_field(complete_input, path, value)
    result = check(complete_input)
    assert has_diagnostic(result, path, "conflicting")
    assert not result.usable


def test_nonexistent_and_ambiguous_local_times(complete_input):
    complete_input["schedule"].update(local_date="2026-03-29", start_time="02:30", end_time="04:00")
    result = check(complete_input)
    assert has_diagnostic(result, "schedule.start_time", "invalid")
    assert not result.usable
    complete_input["schedule"]["local_date"] = "2026-10-25"
    assert has_diagnostic(check(complete_input), "schedule.start_at", "missing")
    complete_input["schedule"]["start_at"] = "2026-10-25T02:30:00+02:00"
    assert check(complete_input).usable


@pytest.mark.parametrize("final_day,cutoff,day_hours", [("2026-03-29", "2026-03-30T00:00:00+02:00", 23), ("2026-10-25", "2026-10-26T00:00:00+01:00", 25)])
def test_full_local_day_deadline_boundaries(complete_input, final_day, cutoff, day_hours):
    complete_input["early_bird"].update(final_date=final_day, cutoff_exclusive=None)
    expected = datetime.fromisoformat(cutoff)
    for delta, supports_discount in [(-1, True), (0, False), (1, False)]:
        now = expected.astimezone(UTC) + timedelta(microseconds=delta)
        complete_input["availability"]["observed_at"] = now.isoformat()
        result = validate_workshop(complete_input, now=now)
        assert result.early_bird_cutoff.isoformat() == cutoff
        assert result.claims.current_discount is supports_discount
    midnight = datetime.fromisoformat(final_day).replace(tzinfo=result.early_bird_cutoff.tzinfo)
    assert (result.early_bird_cutoff.astimezone(UTC) - midnight.astimezone(UTC)).total_seconds() == day_hours * 3600
    assert result.workshop.early_bird.cutoff_exclusive is None


@pytest.mark.parametrize("path,value", [("early_bird.cutoff_exclusive", "2026-10-25T22:00:00Z"), ("early_bird.price", "120.00"), ("early_bird.final_date", "2026-11-09"), ("early_bird.quota_unit", "family_booking")])
def test_contradictory_offers_cannot_support_discount(complete_input, path, value):
    set_field(complete_input, path, value)
    result = check(complete_input)
    assert has_diagnostic(result, path, "conflicting")
    assert not result.claims.current_discount


@pytest.mark.parametrize("field", ["price", "final_date", "quota_limit", "quota_unit", "restriction_text", "restrictions_reviewed"])
def test_incomplete_offers(complete_input, field):
    complete_input["early_bird"][field] = None
    result = check(complete_input)
    assert has_diagnostic(result, "early_bird." + field, "missing")
    assert not result.claims.current_discount


def test_verified_absence_and_contradictory_terms(complete_input):
    complete_input["pricing"].update(displayed_price="120.00", displayed_price_kind="regular")
    complete_input["early_bird"] = {"status": "absent"}
    complete_input["availability"]["remaining_early_bird_units"] = 0
    assert not check(complete_input).claims.no_early_bird_offer
    complete_input["early_bird"]["verification"] = {"status": "verified", "source_ref": "synthetic:absence-review"}
    assert check(complete_input).claims.no_early_bird_offer
    complete_input["early_bird"]["price"] = "95.00"
    result = check(complete_input)
    assert has_diagnostic(result, "early_bird.price", "conflicting")
    assert not result.claims.no_early_bird_offer


@pytest.mark.parametrize("path,value,kind", [
    ("availability.observed_at", "2026-10-24T09:54:59Z", "limitation"),
    ("availability.observed_at", "2026-10-24T10:00:01Z", "invalid"),
    ("availability.workshop_id", "other", "conflicting"),
    ("availability.max_age_seconds", None, "missing"),
    ("availability.shared_pool", None, "missing"),
    ("availability.source", None, "missing"),
    ("availability.early_bird_unit", "family_booking", "conflicting"),
])
def test_unusable_aggregate_evidence(complete_input, path, value, kind):
    set_field(complete_input, path, value)
    result = check(complete_input)
    assert result.usable
    assert has_diagnostic(result, path, kind)
    assert not result.claims.current_discount
    assert not result.claims.remaining_early_bird_units


def test_availability_verification_and_independent_counts(complete_input):
    complete_input["availability"]["verification"] = {}
    result = check(complete_input)
    assert result.usable and not result.claims.remaining_persons
    complete_input["availability"]["verification"] = {"status": "verified", "source_ref": "synthetic:review"}
    complete_input["availability"]["observed_at"] = "2026-10-24T09:55:00Z"
    complete_input["availability"]["remaining_persons"] = 0
    result = check(complete_input)
    assert result.claims.remaining_persons and result.claims.remaining_early_bird_units
    assert not result.claims.current_discount
    complete_input["availability"]["remaining_early_bird_units"] = None
    result = check(complete_input)
    assert result.claims.remaining_persons and not result.claims.remaining_early_bird_units


def test_unknown_image_permissions_and_attribution(complete_input):
    complete_input["image"]["attribution_required"] = None
    result = check(complete_input)
    assert result.usable and not result.claims.image
    assert has_diagnostic(result, "image.attribution_required", "missing")
    complete_input["image"].update(attribution_required=True, credit=" ")
    assert not check(complete_input).claims.image
    complete_input["image"].update(attribution_required=False, credit=None)
    assert check(complete_input).claims.image
    complete_input["image"]["marketing_permission"] = None
    assert has_diagnostic(check(complete_input), "image.marketing_permission", "missing")


def test_supplied_conflicts_and_age_bounds(complete_input):
    complete_input["conflicts"] = [{"field": "availability.remaining_persons", "message": "Zwei Quellen widersprechen sich."}]
    result = check(complete_input)
    assert result.usable and not result.claims.remaining_persons
    assert has_diagnostic(result, "availability.remaining_persons", "conflicting")
    complete_input["conflicts"][0]["field"] = "pricing.regular_price"
    assert not check(complete_input).usable
    complete_input["conflicts"][0]["field"] = "nonexistent"
    assert has_diagnostic(check(complete_input), "nonexistent", "invalid")
    complete_input["conflicts"] = []
    complete_input["age"] = {"minimum": 18, "maximum": 12}
    result = check(complete_input)
    assert result.usable and not result.claims.age
    assert has_diagnostic(result, "age.maximum", "conflicting")


def test_now_required_and_extra_fields_rejected(complete_input):
    with pytest.raises(ValueError, match="now"):
        validate_workshop(complete_input, now=datetime(2026, 10, 24))
    complete_input["invented"] = True
    result = check(complete_input)
    assert has_diagnostic(result, "invented", "invalid")


@pytest.mark.parametrize("offset", ["+00:60", "-00:60", "+24:00"])
def test_malformed_offsets_are_not_normalized(complete_input, offset):
    complete_input["schedule"]["start_at"] = "2026-11-08T11:00:00" + offset
    result = check(complete_input)
    assert has_diagnostic(result, "schedule.start_at", "invalid")
    assert not result.usable


@pytest.mark.parametrize("path", ["schedule.start_at", "early_bird.cutoff_exclusive", "availability.observed_at"])
@pytest.mark.parametrize("instant", ["0001-01-01T00:00:00+01:00", "9999-12-31T23:59:00-01:00"])
def test_unrepresentable_instants_return_diagnostics(complete_input, path, instant):
    set_field(complete_input, path, instant)
    result = check(complete_input)
    assert has_diagnostic(result, path, "invalid")
    assert not result.usable


def test_calendar_boundaries_return_diagnostics(complete_input):
    complete_input["schedule"].update(local_date="0001-01-01", start_time="00:00", end_time="03:00")
    result = check(complete_input)
    assert has_diagnostic(result, "schedule.start_time", "invalid")
    complete_input["schedule"]["local_date"] = "9999-12-31"
    complete_input["early_bird"]["final_date"] = "9999-12-31"
    result = check(complete_input)
    assert has_diagnostic(result, "early_bird.final_date", "invalid")
    assert not result.claims.current_discount
    with pytest.raises(ValueError, match="UTC"):
        validate_workshop(complete_input, now=datetime.fromisoformat("0001-01-01T00:00:00+01:00"))
