# Pilot draft revision and exact-version approval

The package provides a small in-memory workflow for revising and approving one
Google Business or Rausgegangen draft. It has no persistence, authentication,
teacher-app UI, submission, or publishing behavior. Approval records that one
exact version is ready for a later authorized step; it does not authorize that
step or perform it.

## Versions and history

`create_draft_workflow` converts a successful `DraftGenerationResult` into two
independent immutable histories. Each `DraftVersion` records the workshop and
channel, source version, deterministic workshop facts, typed channel content,
canonical booking URL, exact image selection or absence, creation time, parent
fingerprint, revision origin, validation result, and generation metadata when a
model was used. The workflow also keeps the unchanged original public workshop
description.

`revise_draft_directly` accepts human-edited German title and text.
`revise_draft_with_ai` accepts a German instruction of at most 500 characters,
an explicit model, and a timeout of at most 120 seconds. Both append one version
only to the selected channel and use the same deterministic checks. The other
channel and every parent version remain unchanged.

The AI path reuses the official OpenAI Responses API boundary with strict
Structured Outputs, `store=False`, zero automatic retries, explicit model and
bounded timeout. It records the model, `pilot-revision.v1` prompt and schema
versions, source version, time, and available usage. Refusal, incomplete output,
schema failure, timeout, and API failure remain distinct. Current copy, original
description, and revision instruction are JSON-encoded untrusted input; they
cannot change permissions, links, facts, or external actions.

## Fingerprint and approval

The fingerprint is lowercase SHA-256 over UTF-8 JSON with sorted object keys,
no insignificant whitespace, and preserved Unicode. The canonical payload
contains every approval-bound field:

- workshop ID, channel, and source version;
- deterministic workshop facts;
- the complete typed channel content, including structured channel fields;
- canonical booking URL; and
- exact selected image reference or JSON `null` for explicit absence.

Creation time, parent fingerprint, revision origin, validation diagnostics, and
generation metadata describe the version but do not change its publishable
payload. Changing text, a structured channel field, facts, link, image selection,
workshop, channel, or source version changes the fingerprint.

`approve_draft_version` accepts only a valid version that still passes
`check_approval_readiness` against a newly imported snapshot. The immutable
approval copies the exact content, link, image selection, workshop/source/channel,
fingerprint, approving-person reference, and approval time. It never transfers
to another fingerprint. The approving-person value is an opaque reference; this
package does not authenticate it or decide access.

The same readiness function is intended for a later submission-preparation
boundary. It revalidates with the supplied current time and returns `outdated` or
`review_required` for a changed source version or facts, unusable evidence,
inactive or unsupported event status, stale availability, an expired promoted
offer, unsupported wording, image problems, or a new conflict. It never revises,
approves, submits, or publishes automatically.

## Images and remaining channel limitations

An image selection is approval-bound. A selected reference must exactly match
the current workshop evidence, and current validation must support its permission
and any required attribution. Existing alias conflicts remain blocking for that
image; the pilot owner's reported permission is not hardcoded.

Google's `LocalPost.media[]` is not marked required in the official
[LocalPost resource](https://developers.google.com/my-business/reference/rest/v4/accounts.locations.localPosts),
checked September 26, 2026, so the current Google draft definition supports an
explicit text-only version. A verified text-only Rausgegangen event definition
is still unavailable. Absence of an image therefore remains review-required for
Rausgegangen instead of guessing portal behavior.

Deterministic wording checks cover known exact facts and known unsupported-claim
patterns. They do not prove complete semantic accuracy. A human still reviews
every version before approval.

## Small offline example

```python
from datetime import datetime

from workshop_marketing_agent import (
    approve_draft_version,
    check_approval_readiness,
    create_draft_workflow,
    revise_draft_directly,
)

now = datetime.fromisoformat("2026-10-24T10:00:00+00:00")

# `generated` is a successful DraftGenerationResult and `imported` is the
# current FeedImportResult. Both are created by the documented earlier steps.
workflow = create_draft_workflow(
    generated,
    imported,
    now=now,
    google_image_reference=None,
    rausgegangen_image_reference="https://example.org/images/approved-workshop.jpg",
)
parent = workflow.current("google_business")
revision = revise_draft_directly(
    workflow,
    imported,
    channel="google_business",
    title=parent.content.title,
    text=parent.content.body + " Wir freuen uns auf dich.",
    selected_image_reference=None,
    now=now,
)
assert revision.version.validation.status == "valid"

approval = approve_draft_version(
    revision.version,
    imported,
    approving_person_reference="teacher:opaque-reference",
    approved_at=now,
)
assert approval.status == "approved"

# After a newly imported snapshot changes `source_version`, the old exact
# version remains in history but is no longer ready for a later submission.
readiness = check_approval_readiness(
    revision.version,
    newly_imported_snapshot,
    now=datetime.fromisoformat("2026-10-24T11:00:00+00:00"),
)
assert readiness.status == "outdated"
```

Routine tests use synthetic feed/model responses, fixed times, and the repository
socket guard. No live revision was run for this change.
