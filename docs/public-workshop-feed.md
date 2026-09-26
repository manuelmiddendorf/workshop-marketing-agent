# Read-only public workshop feed

`load_public_workshop` loads one explicitly configured `workshop-data.v1`
response and reuses `WorkshopInput` / `validate_workshop`. Only `malws-copy` is
confirmed as the active public pilot. Other workshop feeds are not verified.

```python
from datetime import UTC, datetime

from workshop_marketing_agent import load_public_workshop

result = load_public_workshop(
    endpoint=(
        "https://europe-west1-middendorf-yoga.cloudfunctions.net/"
        "WSGetPublicWorkshops?format=workshop-data.v1&id=malws-copy"
    ),
    workshop_id="malws-copy",
    timeout=10.0,
    now=datetime.now(UTC),
)
for diagnostic in result.diagnostics:
    print(diagnostic.field, diagnostic.kind, diagnostic.message)
if result.validation is not None:
    print("Usable core:", result.validation.usable)
    print("Supported claims:", result.validation.claims)
print("Eligible for current promotion:", result.promotion_eligible)
```

`promotion_eligible` requires usable core facts and the exact feed status pair
`provenance.active: true`, `provenance.event_status: scheduled`. Inactive,
cancelled, postponed, missing, unsupported or conflicting status evidence blocks
promotion. The original validator result remains available in `validation`;
optional claim limitations do not erase usable core facts. Even with eligible
status, check each claim before using discounts, counts, images or other optional
facts. Neither this flag nor a successful import is publishing approval or an
end-to-end booking verification.

## Boundary and failures

- The standard-library HTTP boundary makes one HTTPS GET with a finite positive
  socket timeout and a 1 MiB body limit. It uses standard TLS verification and
  does not retry, follow redirects, poll, authenticate or access Firestore.
  The timeout bounds socket operations, not a hard total wall-clock deadline
  including DNS resolution. No runtime dependency was added.
- `http_get(endpoint, timeout)` can be injected and returns `FeedResponse(status,
  body)` with raw bytes. Offline tests use synthetic responses and fixed times,
  with the existing socket guard. Transport `TimeoutError` and `OSError` /
  `HTTPException` become diagnostics. Invalid caller configuration raises
  `ValueError` before the request.
- HTTP statuses other than 200, timeouts, transport failures, excessive bodies,
  malformed JSON/encoding, numeric overflow/underflow, duplicate object keys,
  invalid envelopes, unsupported schema versions, null/missing workshops and mismatched identities return no
  `validation` and cannot support promotion. Field paths identify each failure.
- The envelope requires `schema_version`, offset-aware `generated_at` and a
  workshop object. Unknown envelope fields are rejected. All workshop fields
  reach the existing validator, including unsupported fields and supplied
  conflicts; none are silently removed. `diagnostics` combines import diagnostics
  and the unchanged validator diagnostics.
- Original German copy, HTML, line breaks, decimal-string prices, source version
  and provenance are preserved. No defaults, verification or rights are invented.
  `generated_at` is retained only as envelope metadata. It never replaces source
  version or aggregate `observed_at`; freshness uses only the supplied policy.
- `now` is the caller's explicit validation reference time. It is not advanced
  after fetching. An aggregate observed during the request can be future-dated
  relative to a time captured before the request and therefore unsupported.
  Revalidate with an explicit later aware time before using time-sensitive facts;
  never substitute the feed's generation timestamp for the current time.

## Remaining upstream limitations

The owner confirmed permission for the pilot image, but that permission and any
attribution requirements still need to be recorded in the upstream feed.
Image-alias comparisons also need correction upstream. The importer retains
unknown values and image conflicts, and cannot support an image claim while the
required evidence is missing or conflicting. Permission is never hardcoded for
the pilot or inferred for other images.

Compatibility observations apply only to this public envelope and its supplied
evidence. They do not authenticate upstream verification or audit booking logic,
source writers or live quota calculations. The historical
[Mal-Yoga reconstruction](../examples/workshops/mal-yoga-2026-10-18.json) remains
unchanged and incomplete; it is not a live feed fixture. See the
[input contract](workshop-data-contract.md#provisional-validation-contract) for
the existing factual and optional-claim rules.

## Recorded read-only observation

On September 25, 2026, the configured pilot returned HTTP 200 during one GET from
`18:21:03.849531Z` to `18:21:07.175928Z` (6,025 bytes; SHA-256
`426cab4389be1d478fdfdd5f4ea9c7da555083b2bb0241d281f116d06cfae72b`). Core facts were
usable and active/scheduled status supported promotion. The offer was expired;
unknown permission/attribution and the image-alias conflict blocked image claims.
Audience, age and included-service claims remained unsupported.

The initial pre-request `now` correctly blocked the newer aggregate observation.
Rechecking the same bytes offline with the explicitly supplied completion time
supported its count claims: observation `18:21:05.682Z`, policy 60 seconds, five
remaining persons and two remaining discount units. These are transient supplied
observations, not permanent fixture facts or a current-discount claim. No bookings,
payments or Firestore operations were performed. The earlier endpoint inspection
at `14:39:01.894211Z`–`14:39:04.143785Z` also returned HTTP 200 with the same kinds
of limitations. No captured live response is committed or used by routine tests.
