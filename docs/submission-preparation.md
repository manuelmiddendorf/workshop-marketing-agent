# Preparing approved pilot submissions

Preparation is a pure, local operation. It returns a checked Google payload or
Rausgegangen copy package, never a publication record, external ID, authorization,
or submission-success status. Callers supply a newly imported workshop and an
explicit timezone-aware current time. No provider, feed, or model request is made
by preparation.

## Campaign links before approval

The frozen `CampaignMetadata` contract contains non-personal opaque references.
References use 1–128 ASCII letters, digits, dots, underscores, tildes or hyphens
and start with a letter or digit. Callers must allocate opaque IDs, never names,
email addresses, or other participant/teacher personal data. Syntax checks do
not establish anonymity. Workshop and channel must match the selected draft.

| Parameter | Value |
| --- | --- |
| `wma_workshop` | Workshop reference |
| `wma_campaign` | Campaign reference |
| `wma_round` | Marketing round reference |
| `wma_channel` | `google_business` or `rausgegangen` |
| `wma_variant` | Variant reference |
| `utm_source` | Channel |
| `utm_medium` | Explicit metadata value; default `event_listing` |
| `utm_campaign` | Campaign reference |
| `utm_content` | Variant reference |

This is the package's small link contract, not a claim about an existing website
integration. It does not record clicks or implement booking attribution.

`build_campaign_link` validates the existing HTTPS canonical URL and appends
these parameters in the table's order using percent encoding. It preserves the
original destination and existing query bytes, including repeated/blank values.
Existing keys beginning with `wma_` or `utm_` are reserved: after URL decoding
and case normalization, any occurrence is rejected, even a matching value.
This also rejects conflicting and duplicate reserved keys instead of silently
overwriting them. Fragments, credentials, and malformed escapes are rejected.

`bind_campaign_link` appends a new version for the selected channel. It updates
every exact booking-URL occurrence in title/body or description and the structured
booking field, then reuses the factual checks. Unrelated links are not repaired
or adopted. The original canonical URL and workshop facts remain separate.
Direct and AI revisions preserve campaign metadata and validate against the
final URL while source checks still use the canonical facts.

The exact campaign metadata and final URL are included in the version fingerprint
and approval record. Any change requires another review and approval. Existing
untracked fingerprints retain their original serialization; an old approval
cannot be upgraded or used to prepare a tracked package. Preparation performs no
text or link replacement.

## Verified channel mapping and remaining unknowns

Primary sources checked September 26, 2026:

- Google's [LocalPost reference](https://developers.google.com/my-business/reference/rest/v4/accounts.locations.localPosts)
  and [event-post examples](https://developers.google.com/my-business/content/posts-data)
  document language, summary, EVENT topic, event title/start/end dates and times,
  BOOK action, and media. The payload uses only these fields. Dates and local
  times come directly from the approved schedule; internal time-zone context is
  retained in the version, not invented as a provider field. The reference says
  only `sourceUrl` is supported for LocalPost media, so that is the only emitted
  media property, despite the broader example including `mediaFormat`.
- Rausgegangen's [event creation guide](https://rausgegangen-assist.freshdesk.com/support/solutions/articles/44002202915-wie-erstelle-ich-ein-event-)
  and [creation steps](https://rausgegangen-assist.freshdesk.com/support/solutions/articles/44002671893-erste-schritte-in-der-zentrale-events-anlegen)
  describe title, time/place, content and ticketing stages, with a price note or
  external ticket link. The [external-link instructions](https://rausgegangen-assist.freshdesk.com/support/solutions/articles/44002486114-kann-ich-einen-externen-ticketlink-hinterlegen-)
  locate that link in the event date's settings. The package points to the
  [provider portal](https://zentrale.rausgegangen.de/) and includes exact approved
  wording, the fact sheet, a regular-price note, image reference, and German copy
  instructions. It is a local copy contract, not an undocumented provider API.

No account IDs, output-only fields, provider limits or API acceptance guarantees
are invented. Google location/time-zone configuration, credentials, account
eligibility, image reachability and actual provider acceptance remain unverified.
The detailed Rausgegangen content-step link returned a fetch error during this
check; its exact current field labels, image specifications, text-only acceptance,
and account-specific required fields remain unknown. They need manual review in
the portal. “Ready to copy” means the supplied approved content is prepared,
not that every account-specific form requirement has been satisfied.

For images, current validation must support the exact selected reference,
permission and attribution. Existing alias conflicts remain blocking; pilot
permission is not hardcoded. Google requires an absolute HTTPS reference here:
filenames are not silently resolved. Explicitly approved text-only Google drafts
omit media. Rausgegangen retains the existing image requirement pending verified
text-only support. If credit is required, it must already occur verbatim in the
approved body/description. Missing credit blocks preparation; it is never appended
after approval. Required attribution placement beyond this remains a manual
provider check.

## Freshness and package fingerprint

Both preparation functions first match the complete approval, then call
`check_approval_readiness` with the supplied feed import and current time.
Changed sources/facts/status, expired promoted offers, stale required evidence,
new blocking conflicts or unsupported image evidence return `outdated` or
`review_required`, with diagnostics and no package. An unpromoted expired offer
does not by itself invalidate otherwise supported core copy.

`PreparedSubmission.fingerprint` is SHA-256 of UTF-8 JSON with sorted keys,
compact separators, preserved Unicode and no non-finite numbers. It covers the
`pilot-submission.v1` serialization tag, full approval-bound version payload,
approval fingerprint/person/time, checked time, and the complete typed submission
payload. These include workshop/source, channel, campaign, canonical/final URLs,
exact content, selected image and structured channel fields. Provider payloads
contain no internal approval or campaign fields except the approved URL.

Identical inputs and check time yield identical results without mutating history.
A package is a dated checked snapshot. A later sending boundary must check the
approval and current evidence again. Deterministic checks still do not establish
complete semantic accuracy.

## Synthetic flow

Given a successful synthetic `generated` result and `imported` feed result:

```python
from datetime import datetime
from workshop_marketing_agent import (
    CampaignMetadata, approve_draft_version, bind_campaign_link,
    check_approval_readiness, create_draft_workflow,
    prepare_google_submission, prepare_rausgegangen_submission,
)

now = datetime.fromisoformat("2026-10-24T10:00:00+00:00")
workflow = create_draft_workflow(
    generated, imported, now=now,
    google_image_reference=None,
    rausgegangen_image_reference=imported.validation.workshop.image.reference,
)
# Required image credit must already be part of the reviewed Rausgegangen text.
for channel, prepare in (
    ("google_business", prepare_google_submission),
    ("rausgegangen", prepare_rausgegangen_submission),
):
    metadata = CampaignMetadata(
        workshop="synthetic-workshop-01", campaign="c-1", round="r-1",
        channel=channel, variant="v-1",
    )
    bound = bind_campaign_link(workflow, imported, campaign=metadata, now=now)
    workflow = bound.workflow
    version = bound.version
    # Human reviews version.content and version.final_booking_url here.
    approval = approve_draft_version(
        version, imported, approving_person_reference="opaque-reviewer-1",
        approved_at=now,
    )
    # Supply a freshly imported snapshot and actual time in real usage.
    readiness = check_approval_readiness(version, imported, now=now)
    result = prepare(version, approval.approval, imported, now=now)
    if result.package is None:
        print(result.status, result.diagnostics)
    else:
        print(result.status, result.package.fingerprint)
```

The offline tests exercise both channels with synthetic responses, fixed times
and the socket guard. Compare the existing generation/revision tests with the
new preparation cases: untracked behavior stays supported, while tracked
versions require the exact final link. No live generation or preparation against
production evidence is claimed; no qualitative model-quality measurement was
performed.
