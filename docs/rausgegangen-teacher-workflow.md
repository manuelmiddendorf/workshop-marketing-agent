# Assisted Rausgegangen teacher workflow

**Manual Google pilot update (2026-09-28):** The Callable now uses the
[manual-only Google handoff](google-manual-handoff.md), without a Google OAuth
binding/publisher. Exact approved copy can be handed off without recording
publication. Automatic integration remains the goal; manual confirmation and live
activation are separate work. Rausgegangen confirmation rules are unchanged.

The existing `PilotService` and Firebase Callable now expose reservation and two
separate manual confirmations for `malws-copy`. The server derives the
`rausgegangen` channel and reuses `ReservePublication`, `ConfirmSubmission` and
`ConfirmPublication`. No portal/API integration or automated submission is added.

## Three explicit actions

Every request includes `action`, `workshop_reference`, `campaign_reference`,
`round_reference`, `request_id`, and the current `expected_revision`.

| Action | Additional fields | Effect |
| --- | --- | --- |
| `begin_rausgegangen_submission` | Exact `version_reference`, `approval_reference`, `submission_reference` | Recheck fresh evidence, commit reservation, return stored copy |
| `confirm_rausgegangen_submission` | `attempt_reference` | Record the teacher's confirmation: `in_progress` → `submitted` |
| `confirm_rausgegangen_publication` | `attempt_reference`, optional `public_url` | Record the teacher's confirmation: `submitted` → `published` |

Example begin request (synthetic references):

```json
{
  "action": "begin_rausgegangen_submission",
  "workshop_reference": "malws-copy",
  "campaign_reference": "campaign-1",
  "round_reference": "round-1",
  "request_id": "begin-manual-1",
  "expected_revision": 8,
  "version_reference": "version-1",
  "approval_reference": "approval-1",
  "submission_reference": "preparation-1"
}
```

After manually submitting the copied listing in the portal, a teacher sends:

```json
{
  "action": "confirm_rausgegangen_submission",
  "workshop_reference": "malws-copy",
  "campaign_reference": "campaign-1",
  "round_reference": "round-1",
  "request_id": "confirm-submitted-1",
  "expected_revision": 9,
  "attempt_reference": "stored-attempt-1"
}
```

Only after separately observing publication should a teacher send:

```json
{
  "action": "confirm_rausgegangen_publication",
  "workshop_reference": "malws-copy",
  "campaign_reference": "campaign-1",
  "round_reference": "round-1",
  "request_id": "confirm-published-1",
  "expected_revision": 10,
  "attempt_reference": "stored-attempt-1",
  "public_url": "https://example.org/synthetic-event"
}
```

The server does not visit the URL or verify visibility. Submission cannot include
a URL; publication can omit it. The existing strict `PublicURL` rejects HTTP,
credentials, fragments, query strings, private IP addresses and local/internal
host suffixes. It is not a provider-domain verification mechanism. All three
schemas reject extra channel, content, fingerprint, operation, provider-result,
actor, timestamp, credential and configuration fields.

## Reserve first, copy the exact stored package

Authentication, the pilot/channel allowlists and affirmative workshop access
precede campaign access. Campaign/workshop/round/record associations and the exact
selected version are checked. Begin requires an enabled channel, matching stored
approval and prepared package, and no blocking prior manual attempt across rounds.
The existing fresh-feed/approval/preparation rules check current source facts,
availability, event eligibility, image evidence and required credit. Changed
facts produce `outdated` or `review_required` without reservation or an active
package. Feed failures retain the existing safe service errors.

A successful begin commits the reservation and request receipt atomically, then
positively reloads the exact receipt before returning active copy. The response
projects the original `SubmissionRecord` referenced by the reservation. A fresh
preparation is used only to check readiness; its output never replaces stored
wording, timestamps, approval or package history.

The explicit `package` projection contains:

- `title`, `description`: exact approved text, preserving German Unicode and line breaks.
- `local_date`, `start_time`, `end_time`, `time_zone`, `location`: stored fact-sheet values in ISO string form where appropriate.
- `regular_price`: an exact decimal string; `currency`, `pricing_unit`, `price_note`: stored values.
- `external_booking_link`: the complete approved tracked URL.
- `image_reference`: the approved stored reference, without alias repair or inferred permission.
- `copy_instructions`: the existing German instruction list.
- `portal_destination`: the existing fixed `https://zentrale.rausgegangen.de/` package destination.

Existing attribution is retained verbatim in `description`. The current copy
package has no separate attribution field; no credit is extracted, appended or
invented. Raw source HTML, full state, receipts, fingerprints, actor references,
prompts, model metadata and credentials are excluded. Treat copy as display/copy
data, never executable HTML or instructions controlling the application.

## Status and replay semantics

`ready_to_copy` describes a successful initial response; the durable reservation
status is `in_progress`. The response includes safe campaign/round/attempt
references, `revision` (the loaded current revision), `recorded_revision`,
`recorded_status`, `manual_status` (the loaded current attempt status),
`historical`, `package_active`, and an optional public URL.

An initial positively confirmed reservation has `package_active: true` while the
attempt is still `in_progress`. An exact begin replay may return the same package,
but uses `existing_reservation`, `historical: true`, and `package_active: false`.
Its historical `recorded_status` remains `in_progress`, while `manual_status` can
already be `submitted` or `published`. The fixed message says:

> Ein Vorgang ist bereits gespeichert. Aktuellen Stand prüfen; nicht erneut einreichen.

A replay is not a new freshness check or another instruction to submit. Later
confirmations never include a package. Their recorded status remains historical
on replay, while `manual_status` reports subsequent progress. For example, replaying
a submission confirmation after publication returns `recorded_status: submitted`
and `manual_status: published`.

Any currently authorized teacher with access may continue another teacher's
attempt using a new request ID. Each confirmation records the actual confirming
teacher and aware server time. Confirmations load no workshop evidence and do not
rewrite historical content, even after source changes or channel disabling.
A direct `in_progress` → `published` jump is rejected. Submitted does not imply
public visibility; published here is teacher-reported evidence, not server verification.

The existing target remains blocked while in progress/submitted, and after a
manual publication lacking a provider update identity. No cancellation, retry,
replacement or “not published” override is supplied. Google state is independent.

## Concurrency and uncertain storage

Request IDs remain campaign-scoped and bind normalized intent and authenticated
actor. Every request and replay reauthorizes access. Changed action, references,
public URL or actor under a recorded request ID conflicts. Expected revision is a
precondition rather than intent; a stale revision can retrieve an exact receipt
but cannot start a new action.

Competing begin calls use independent server operation IDs and the existing atomic
compare-and-save contract: only one can reserve. No losing call receives active
copy. Confirmations use the same revision/receipt contract and cannot duplicate
an event on exact replay. Failed preconditions and uncommitted operations do not
create receipts, matching existing service behavior.

After a save exception or conflict, reload checks the exact command identity,
fingerprint and request binding. If positively found, return its recorded result.
Without that confirmation, return a fixed storage-uncertain/conflict response,
`package_active: false`, and no package. Never assume a failed acknowledgement
means nothing committed, retry automatically, or perform an external action.
A later replay can recover the stored event without adding another.

No persistence schema or domain transition changes were needed. Existing canonical
snapshots remain compatible, and earlier versions, approvals, packages, Google
records and manual events remain immutable.

## Offline verification and remaining work

Tests use synthetic workshop evidence and packages, fixed times, in-memory storage
and injected failures. They cover exact projections, readiness, associations,
competing reservations, both confirmations, actor continuation, request replay,
URL restrictions, uncertain-save receipt recovery and strict/legacy restoration.
Callable tests retain App Check and current teacher authorization, and assert zero
Google-secret reads, token refreshes and Google transport calls for all manual
actions. Only begin uses a fake feed; confirmations and replays use none. Existing
draft and Google tests remain part of the full suite.

Teacher-app components, manual portal checks, deployment, App Check integration,
production Firestore/IAM and live-pilot verification remain separate work. The
existing [preparation contract](submission-preparation.md) documents outstanding
portal field/image requirements; this task does not inspect or automate the portal.
No actual submission, publication, live feed, Google or Firebase call is performed.
Offline tests prove neither public visibility nor production operation.

The independent focused review found no actionable implementation or documentation
issues. Its initial locked Python 3.11 run passed **143 tests in 35.11 seconds**;
its follow-up check of seven added storage/association/authorization cases passed
**7 tests in 3.15 seconds**. No review corrections were required. The final service
check, including the additional existing-approval/cross-round regression, passed
**75 tests in 21.81 seconds**. All three documented request examples validate
against the strict request schema; affected local Markdown targets and
`git diff --check` pass.

The complete suite uses separate existing temporary environments, preserving the
project `.venv`, with explicit interpreters and offline dependency resolution:

```sh
env -i PATH=/usr/local/bin:/usr/bin:/bin \
  UV_CACHE_DIR=/tmp/workshop-firestore-uv-cache UV_OFFLINE=1 \
  UV_PROJECT_ENVIRONMENT=/tmp/workshop-callable-test311 \
  /usr/local/bin/uv run --locked \
  --python /tmp/workshop-python311-interpreters/cpython-3.11.15-macos-aarch64-none/bin/python3.11 \
  python -m pytest

env -i PATH=/usr/local/bin:/usr/bin:/bin \
  UV_CACHE_DIR=/tmp/workshop-firestore-uv-cache UV_OFFLINE=1 \
  UV_PROJECT_ENVIRONMENT=/tmp/workshop-callable-test313 \
  /usr/local/bin/uv run --locked \
  --python /Users/my/.local/share/uv/python/cpython-3.13.14-macos-aarch64-none/bin/python3.13 \
  python -m pytest
```

Final complete runs on 2026-09-27, after independent review, used macOS 15.6.1
arm64, uv 0.11.31 and pytest 9.1.1:

| Interpreter | Actual result |
| --- | --- |
| Python 3.11.15 | 1168 passed in 80.59 seconds |
| Python 3.13.14 | 1168 passed in 77.47 seconds |

These include 85 new service/Callable cases and the existing strict/legacy snapshot,
draft and Google regression checks. No dependencies, lockfile, business rules,
copy, prompts, persistence schema or Firebase configuration changed. No live check
was performed.
