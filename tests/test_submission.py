import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import pytest

from workshop_marketing_agent import (
    CampaignMetadata, FeedResponse, ModelCallResult,
    approve_draft_version, bind_campaign_link, build_campaign_link,
    check_approval_readiness, create_draft_workflow, generate_pilot_drafts,
    load_public_workshop, prepare_google_submission, prepare_rausgegangen_submission,
    revise_draft_directly, revise_draft_with_ai,
)
from workshop_marketing_agent.models import Conflict

NOW = datetime(2026, 10, 24, 10, tzinfo=UTC)
IMAGE = "https://example.org/image.png"


class FakeClient:
    def __init__(self, output):
        self.output = output
        self.requests = []

    def generate(self, request):
        self.requests.append(request)
        return ModelCallResult("completed", output=self.output)


def imported_data(data, now=NOW):
    return load_public_workshop(
        endpoint="https://example.org/feed", workshop_id="synthetic-workshop-01",
        timeout=5, now=now, http_get=lambda *_: FeedResponse(200, json.dumps({
            "schema_version": "workshop-data.v1", "generated_at": now.isoformat(),
            "workshop": data,
        }).encode()),
    )


@pytest.fixture
def source(complete_input):
    complete_input["provenance"].update(active=True, event_status="scheduled")
    complete_input["image"].update(reference=IMAGE)
    return complete_input


@pytest.fixture
def imported(source):
    return imported_data(source)


@pytest.fixture
def workflow(imported):
    wording = json.loads((Path(__file__).parent / "fixtures/generation/valid-wording.json").read_text())
    # Credits are reviewed as part of the exact wording, never appended at preparation.
    wording["google_body"] += " Bild: Fiktives Studioteam"
    wording["rausgegangen_description"] += " Bild: Fiktives Studioteam"
    generated = generate_pilot_drafts(
        imported, marketing_round_purpose="Erste Ankündigung", now=NOW,
        model="offline-fake", timeout=10, client=FakeClient(wording),
    )
    return create_draft_workflow(
        generated, imported, now=NOW,
        google_image_reference=IMAGE, rausgegangen_image_reference=IMAGE,
    )


def campaign(channel="google_business", **changes):
    return CampaignMetadata(**{
        "workshop": "synthetic-workshop-01", "campaign": "c-1", "round": "r-1",
        "channel": channel, "variant": "v-1", **changes,
    })


def approved(workflow, imported, channel="google_business"):
    result = bind_campaign_link(workflow, imported, campaign=campaign(channel), now=NOW)
    assert result.status == "revised", result.diagnostics
    version = result.version
    approval = approve_draft_version(
        version, imported, approving_person_reference="opaque-reviewer-1", approved_at=NOW,
    )
    assert approval.status == "approved", approval.diagnostics
    return version, approval.approval


@pytest.mark.parametrize("query", [
    "", "?x=", "?x=1&x=2&empty=&bare", "?encoded=%26%3D%2B&space=a+b",
    "?unicode=%C3%BC&escaped=%2526", "?",
])
def test_link_preserves_destination_and_unrelated_query_bytes(query):
    canonical = "https://example.org/workshop/path%20name" + query
    result = build_campaign_link(canonical, campaign())
    assert result.startswith(canonical)
    assert urlsplit(result).path == urlsplit(canonical).path
    old_pairs = parse_qsl(urlsplit(canonical).query, keep_blank_values=True)
    pairs = parse_qsl(urlsplit(result).query, keep_blank_values=True)
    assert pairs[:len(old_pairs)] == old_pairs
    assert pairs[len(old_pairs):] == list(campaign().parameters())
    assert result == build_campaign_link(canonical, campaign())


@pytest.mark.parametrize("query", [
    "utm_source=other", "utm_source=google_business", "utm_source=x&utm_source=y",
    "%75tm_source=x", "UTM_SOURCE=x", "wma_round=", "wma_round=x&%77ma_round=y",
    "utm_unknown=x", "wma_variant=x",
])
def test_reserved_keys_are_rejected_after_decoding(query):
    with pytest.raises(ValueError):
        build_campaign_link("https://example.org/event?" + query, campaign())


@pytest.mark.parametrize("url", [
    "http://example.org/event", "https://user:secret@example.org/event",
    "https://example.org/event#frag", "https://example.org/event?x=%ZZ",
])
def test_invalid_canonical_destinations_are_rejected(url):
    with pytest.raises(ValueError):
        build_campaign_link(url, campaign())


@pytest.mark.parametrize("field,value", [
    ("campaign", "person@example.org"), ("variant", "a&redirect=elsewhere"),
    ("round", "John Doe"), ("utm_medium", "line\nbreak"),
])
def test_metadata_rejects_contact_syntax_and_query_injection(field, value):
    with pytest.raises(ValueError):
        campaign(**{field: value})


def test_binding_creates_new_history_and_changes_all_booking_fields(workflow, imported):
    original = workflow.current("google_business")
    old_approval = approve_draft_version(
        original, imported, approving_person_reference="reviewer", approved_at=NOW,
    ).approval
    result = bind_campaign_link(workflow, imported, campaign=campaign(), now=NOW)
    version = result.version
    assert version.parent_fingerprint == original.fingerprint
    assert version.fingerprint != original.fingerprint
    assert not old_approval.matches(version)
    assert version.canonical_booking_url == original.canonical_booking_url
    assert version.workshop_facts == original.workshop_facts
    assert version.content.booking_action.url == version.final_booking_url
    assert version.final_booking_url in version.content.body
    assert result.workflow.google_versions == (original, version)
    assert result.workflow.rausgegangen_versions is workflow.rausgegangen_versions
    assert result.workflow.original_description_html == workflow.original_description_html
    assert check_approval_readiness(version, imported, now=NOW).ready


@pytest.mark.parametrize("field,value", [
    ("campaign", "c-2"), ("round", "r-2"), ("variant", "v-2"),
    ("utm_medium", "social"),
])
def test_campaign_changes_require_new_approval(workflow, imported, field, value):
    first = bind_campaign_link(workflow, imported, campaign=campaign(), now=NOW)
    approval = approve_draft_version(
        first.version, imported, approving_person_reference="reviewer", approved_at=NOW,
    ).approval
    second = bind_campaign_link(
        first.workflow, imported, campaign=campaign(**{field: value}), now=NOW,
    )
    assert second.status == "revised", second.diagnostics
    assert second.version.fingerprint != first.version.fingerprint
    assert not approval.matches(second.version)
    assert second.version.content.body.count("wma_workshop=") == 1


def test_wrong_campaign_workshop_rejected(workflow, imported):
    with pytest.raises(ValueError):
        bind_campaign_link(workflow, imported, campaign=campaign(workshop="other"), now=NOW)


def test_google_payload_matches_approved_fields_exactly(workflow, imported):
    version, approval = approved(workflow, imported)
    result = prepare_google_submission(version, approval, imported, now=NOW)
    assert result.status == "ready", result.diagnostics
    assert result.package.payload.provider_payload() == {
        "languageCode": "de-DE", "topicType": "EVENT",
        "summary": version.content.body,
        "event": {"title": version.content.title, "schedule": {
            "startDate": {"year": 2026, "month": 11, "day": 8},
            "endDate": {"year": 2026, "month": 11, "day": 8},
            "startTime": {"hours": 11, "minutes": 0, "seconds": 0, "nanos": 0},
            "endTime": {"hours": 13, "minutes": 0, "seconds": 0, "nanos": 0},
        }},
        "callToAction": {"actionType": "BOOK", "url": version.final_booking_url},
        "media": [{"sourceUrl": IMAGE}],
    }
    assert result.package.approval is approval
    assert result.package.version is version


def test_rausgegangen_copy_package_matches_approved_content(workflow, imported):
    version, approval = approved(workflow, imported, "rausgegangen")
    result = prepare_rausgegangen_submission(version, approval, imported, now=NOW)
    assert result.status == "ready_to_copy", result.diagnostics
    payload = result.package.payload
    assert payload.title == version.content.title
    assert payload.description == version.content.description
    assert payload.fact_sheet == version.content.fact_sheet
    assert payload.fact_sheet.ticket_url == version.final_booking_url
    assert payload.external_booking_link == version.final_booking_url
    assert payload.price_note == "120,00 € pro Person"
    assert payload.image_reference == IMAGE
    assert payload.portal_destination == "https://zentrale.rausgegangen.de/"
    assert "weder eingereicht noch veröffentlicht" in payload.copy_instructions[-1]


@pytest.mark.parametrize("channel", ["google_business", "rausgegangen"])
def test_repeatable_preparation_does_not_mutate_history_or_approval(workflow, imported, channel):
    version, approval = approved(workflow, imported, channel)
    prepare = prepare_google_submission if channel == "google_business" else prepare_rausgegangen_submission
    first = prepare(version, approval, imported, now=NOW)
    second = prepare(version, approval, imported, now=NOW)
    assert first == second
    assert first.package.fingerprint == second.package.fingerprint
    assert first.package.fingerprint != replace(first.package, checked_at=NOW + timedelta(seconds=1)).fingerprint
    assert len(workflow.google_versions) == len(workflow.rausgegangen_versions) == 1


def test_no_tracking_is_attached_to_an_older_approval(workflow, imported):
    version = workflow.current("google_business")
    approval = approve_draft_version(
        version, imported, approving_person_reference="reviewer", approved_at=NOW,
    ).approval
    result = prepare_google_submission(version, approval, imported, now=NOW)
    assert result.package is None
    assert result.diagnostics[-1].field == "preparation.tracking"


@pytest.mark.parametrize("change", ["missing", "link", "text", "image", "channel", "structured", "campaign"])
def test_missing_or_mismatched_approval_never_yields_a_package(workflow, imported, change):
    version, approval = approved(workflow, imported)
    if change == "missing":
        approval = None
    elif change == "link":
        version = replace(version, final_booking_url=version.final_booking_url + "&extra=1")
    elif change == "text":
        version = replace(version, content=version.content.model_copy(update={"body": version.content.body + " Neu."}))
    elif change == "image":
        version = replace(version, selected_image_reference=None)
    elif change == "channel":
        version = replace(version, channel="rausgegangen")
    elif change == "structured":
        version = replace(version, content=version.content.model_copy(update={
            "schedule": version.content.schedule.model_copy(update={"start_time": datetime.min.time()})
        }))
    else:
        version = replace(version, campaign=campaign(round="other"))
    result = prepare_google_submission(version, approval, imported, now=NOW)
    assert result.status == "review_required" and result.package is None


@pytest.mark.parametrize("change", ["source", "location", "cancelled", "stale", "conflict", "permission", "image"])
def test_fresh_import_is_rechecked(workflow, imported, source, change):
    version, approval = approved(workflow, imported)
    if change == "source":
        source["source_version"] = source["verification"]["source_version"] = "v2"
    elif change == "location":
        source["location"] = "Anderer Ort"
    elif change == "cancelled":
        source["provenance"]["event_status"] = "cancelled"
    elif change == "stale":
        source["availability"]["observed_at"] = "2026-10-23T10:00:00Z"
    elif change == "conflict":
        source.setdefault("conflicts", []).append({"field": "image.reference", "message": "Alias conflict"})
    elif change == "permission":
        source["image"]["marketing_permission"] = None
    else:
        source["image"]["reference"] = "https://example.org/other.png"
    result = prepare_google_submission(version, approval, imported_data(source), now=NOW)
    assert result.status in {"outdated", "review_required"}
    assert result.package is None and result.diagnostics


def test_expired_promoted_offer_blocks_even_with_fresh_availability(workflow, imported, source):
    parent = workflow.current("google_business")
    edited = revise_draft_directly(
        workflow, imported, channel="google_business", title=parent.content.title,
        text=parent.content.body + " Frühbuchpreis 95,00 € bis 25.10.2026.",
        selected_image_reference=IMAGE, now=NOW,
    )
    version, approval = approved(edited.workflow, imported)
    later = datetime(2026, 10, 26, 10, tzinfo=UTC)
    source["availability"]["observed_at"] = later.isoformat()
    result = prepare_google_submission(version, approval, imported_data(source, later), now=later)
    assert result.package is None
    assert any("discount" in d.message or "price" in d.message for d in result.diagnostics)


def test_google_text_only_is_explicit_and_rausgegangen_still_requires_image(workflow, imported):
    for channel in ("google_business", "rausgegangen"):
        parent = workflow.current(channel)
        edited = revise_draft_directly(
            workflow, imported, channel=channel, title=parent.content.title,
            text=parent.content.body if channel == "google_business" else parent.content.description,
            selected_image_reference=None, now=NOW,
        )
        bound = bind_campaign_link(edited.workflow, imported, campaign=campaign(channel), now=NOW)
        result = approve_draft_version(
            bound.version, imported, approving_person_reference="reviewer", approved_at=NOW,
        )
        if channel == "google_business":
            prepared = prepare_google_submission(bound.version, result.approval, imported, now=NOW)
            assert "media" not in prepared.package.payload.provider_payload()
        else:
            assert result.approval is None


def test_preparation_never_inserts_missing_image_credit(workflow, imported):
    parent = workflow.current("google_business")
    edited = revise_draft_directly(
        workflow, imported, channel="google_business", title=parent.content.title,
        text=parent.content.body.replace(" Bild: Fiktives Studioteam", ""),
        selected_image_reference=IMAGE, now=NOW,
    )
    version, approval = approved(edited.workflow, imported)
    result = prepare_google_submission(version, approval, imported, now=NOW)
    assert result.package is None
    assert result.diagnostics[0].field == "preparation.image.credit"


def test_direct_and_ai_revisions_preserve_tracking(workflow, imported):
    bound = bind_campaign_link(workflow, imported, campaign=campaign(), now=NOW)
    parent = bound.version
    direct = revise_draft_directly(
        bound.workflow, imported, channel="google_business", title=parent.content.title,
        text=parent.content.body + " Willkommen.", selected_image_reference=IMAGE, now=NOW,
    )
    client = FakeClient({"title": parent.content.title, "text": parent.content.body + " Willkommen."})
    ai = revise_draft_with_ai(
        bound.workflow, imported, channel="google_business", instruction="Bitte herzlicher.",
        selected_image_reference=IMAGE, now=NOW, model="offline-fake", timeout=10, client=client,
    )
    for result in (direct, ai):
        assert result.status == "revised", result.diagnostics
        assert result.version.campaign == parent.campaign
        assert result.version.final_booking_url == parent.final_booking_url
    assert json.loads(client.requests[0].input_json)["fact_display"]["booking_url"] == parent.final_booking_url


def test_tracking_rejects_extra_parameters_even_if_approval_is_reconstructed(workflow, imported):
    version, approval = approved(workflow, imported)
    altered = replace(version, final_booking_url=version.final_booking_url + "&redirect=elsewhere")
    reconstructed = replace(
        approval, version_fingerprint=altered.fingerprint,
        final_booking_url=altered.final_booking_url,
    )
    result = prepare_google_submission(altered, reconstructed, imported, now=NOW)
    assert result.package is None
    assert any(d.field == "draft.tracking" for d in result.diagnostics)


def test_unresolved_image_alias_is_not_turned_into_google_url(workflow, imported, source):
    source["image"]["reference"] = "studio-image.png"
    current = imported_data(source)
    parent = workflow.current("google_business")
    revised = revise_draft_directly(
        workflow, current, channel="google_business", title=parent.content.title,
        text=parent.content.body, selected_image_reference="studio-image.png", now=NOW,
    )
    version, approval = approved(revised.workflow, current)
    result = prepare_google_submission(version, approval, current, now=NOW)
    assert result.package is None
    assert result.diagnostics[0].field == "preparation.image.source_url"


def test_booking_text_with_multiple_exact_occurrences_is_bound(workflow, imported):
    parent = workflow.current("google_business")
    revised = revise_draft_directly(
        workflow, imported, channel="google_business",
        title=parent.content.title + " " + parent.canonical_booking_url,
        text=parent.content.body + " " + parent.canonical_booking_url,
        selected_image_reference=IMAGE, now=NOW,
    )
    bound = bind_campaign_link(revised.workflow, imported, campaign=campaign(), now=NOW)
    assert bound.status == "revised", bound.diagnostics
    assert bound.version.content.body.count(bound.version.final_booking_url) == 2
    assert bound.version.final_booking_url in bound.version.content.title


def test_no_prefix_replacement_can_repair_an_unrelated_destination(workflow, imported):
    parent = workflow.current("google_business")
    revised = revise_draft_directly(
        workflow, imported, channel="google_business", title=parent.content.title,
        text=parent.content.body + " " + parent.canonical_booking_url + "other",
        selected_image_reference=IMAGE, now=NOW,
    )
    bound = bind_campaign_link(revised.workflow, imported, campaign=campaign(), now=NOW)
    assert bound.status == "review_required"
    assert parent.canonical_booking_url + "other" in bound.version.content.body


def test_unchecked_invalid_channel_fields_cannot_be_prepared(workflow, imported):
    version, approval = approved(workflow, imported)
    altered = replace(version, content=version.content.model_copy(update={"topic_type": "OFFER"}))
    reconstructed = replace(approval, approved_content=altered.content, version_fingerprint=altered.fingerprint)
    result = prepare_google_submission(altered, reconstructed, imported, now=NOW)
    assert result.package is None
    assert result.diagnostics[-1].field == "preparation.content"


def test_prepared_fingerprint_covers_payload_approval_and_campaign(workflow, imported):
    version, approval = approved(workflow, imported)
    package = prepare_google_submission(version, approval, imported, now=NOW).package
    variants = [
        replace(package, approval=replace(approval, approving_person_reference="other-reviewer")),
        replace(package, approval=replace(approval, approved_at=NOW - timedelta(seconds=1))),
        replace(package, payload=package.payload.model_copy(update={"summary": "Changed"})),
        replace(package, version=replace(version, campaign=campaign(variant="v2"))),
        replace(package, version=replace(version, selected_image_reference=None)),
    ]
    assert all(p.fingerprint != package.fingerprint for p in variants)


def test_naive_time_is_rejected(workflow, imported):
    version, approval = approved(workflow, imported)
    with pytest.raises(ValueError):
        prepare_google_submission(version, approval, imported, now=NOW.replace(tzinfo=None))


def test_campaign_identifiers_are_not_interpreted_as_marketing_claims(workflow, imported):
    bound = bind_campaign_link(
        workflow, imported, campaign=campaign(round="2026-12-31", variant="rabatt."),
        now=NOW,
    )
    assert bound.status == "revised", bound.diagnostics
    rebound = bind_campaign_link(bound.workflow, imported, campaign=campaign(), now=NOW)
    assert rebound.status == "revised", rebound.diagnostics


@pytest.mark.parametrize("canonical", [
    "https://example.org/event#", "https://example.org/event?existing=1#",
])
def test_empty_fragment_delimiter_is_rejected(canonical):
    with pytest.raises(ValueError, match="fragment"):
        build_campaign_link(canonical, campaign())


def test_campaign_date_cannot_replace_visible_event_date(workflow, imported):
    bound = bind_campaign_link(
        workflow, imported, campaign=campaign(round="08.11.2026"), now=NOW,
    )
    version = bound.version
    # Keep the date in the tracking URL, remove only its visible prose occurrence.
    edited = revise_draft_directly(
        bound.workflow, imported, channel="google_business", title=version.content.title,
        text=version.content.body.replace("am 08.11.2026", "am "),
        selected_image_reference=IMAGE, now=NOW,
    )
    assert edited.status == "review_required"
    assert any("missing exact date" in d.message for d in edited.diagnostics)
