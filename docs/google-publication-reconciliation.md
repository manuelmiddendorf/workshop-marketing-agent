# Exact-version Google publication reconciliation

`reconcile_google_publication` is an explicit teacher action for historical,
uncertain Google attempts. It performs at most one provider GET, records one
append-only observation, and never publishes, updates, lists, deletes or retries a
post. The existing [Callable and OAuth wiring](google-oauth-wiring.md) supplies
teacher authorization, App Check, lazy token acquisition and bounded transport.
No new configuration, dependencies or credentials are introduced.

## Request and eligibility

Example request (synthetic references; use the campaign's actual current revision):

```json
{
  "action": "reconcile_google_publication",
  "workshop_reference": "malws-copy",
  "campaign_reference": "campaign-1",
  "round_reference": "round-1",
  "attempt_reference": "attempt-1",
  "request_id": "reconcile-1",
  "expected_revision": 12
}
```

These are the only accepted fields. Channel is always `google_business`; identity,
time, resource and provider evidence are server-derived. Authentication, workshop
access and the pilot allowlist are checked before loading campaign data. Campaign,
workshop, round and attempt associations, stored fingerprints, receipts and revision
are checked before any Google secret or provider access.

Eligible targets:

- `outcome_unknown` with one previously stored validated Google resource name.
- `in_progress` update with its trusted existing `update_post_id`.

The resource must also belong to the configured server location. Missing or
incompatible configuration causes no token access. An interrupted create or an
unknown outcome without a recorded resource requires manual investigation and
stays blocked. A teacher cannot supply a resource as a workaround. An unknown
update without a recorded result resource also stays manual; the in-progress
update exception does not silently broaden that policy.

Terminal attempts return their recorded safe state without GET. Invalid
associations return the existing safe request/not-found error. Reconciliation can
observe an attempt after editing or disabling its channel: neither action rewrites
the historical operation. No feed fetch, model call, approval check against new
workshop facts, or submission preparation occurs.

## What constitutes an exact match

A visible post alone cannot prove an update succeeded: it may still contain the
previous approved version. The service resolves the attempt's original version,
approval and submission records, verifies their stored bindings, then compares the
provider projection with the reserved **payload fingerprint**. The prepared
**package fingerprint** also binds approval, version and preparation time; it is
verified separately and is never used as the provider-content digest.

Comparison uses the existing SHA-256 over sorted, compact canonical JSON with
German Unicode preserved. Text and line breaks, language, topic, event title,
complete dates and local times, CTA type, the complete tracked URL, and ordered
media source URLs must match exactly. Absent media, an empty list, and a nonempty
list remain distinct. Nothing is trimmed, repaired, URL-normalized or inferred
from current workshop data.

The GET result exposes an optional `GoogleSubmissionPayload` observation, excluded
from its representation. It extracts only supported editable content and the
existing safe resource/state/public URL. Duplicate JSON keys and non-finite JSON
are rejected. Missing required fields, wrong types, invalid dates/times or URLs,
and content outside the current `de-DE`/`EVENT`/`BOOK` payload remain unresolved.
All date and time components, including explicit zero seconds/nanoseconds, are
required in the observation. Missing zero fields are not guessed from provider
serialization conventions.

Documented output-only fields are discarded. For nested events this includes
`recurringInstanceTime`; for media it includes provider URLs, names, creation time,
dimensions, insights and attribution. A provider URL never substitutes for a
missing `sourceUrl`. Unsupported editable schedule/offer/alert/recurrence or media
representations cannot be dropped to manufacture a match. Unrelated top-level
response fields are discarded and never exposed. Changed representations remain
unresolved; the implementation makes no promise about Google's live normalization.

## Mapping and stored history

| Trusted observation | Recorded result |
| --- | --- |
| Exact resource, `visible`, `LIVE`/`RECURRING`, exact payload and safe public URL | `published`, reason `matched` |
| Exact resource with `rejected` and state `REJECTED` | `failed`, error/reason `provider_rejected` |
| `pending` with `PROCESSING`/`SCHEDULED` | `outcome_unknown`, reason `processing` |
| Visible, representable but different payload | `outcome_unknown`, reason `content_mismatch`, match `false` |
| Visible but incomplete/unrepresentable content | `outcome_unknown`, reason `content_unresolved`, match unknown |
| Matching visible content without a storage-safe public URL | `outcome_unknown`, reason `public_url_unavailable` |
| Unknown state, mismatched identity, or inconclusive read | `outcome_unknown`, allowlisted safe reason |

404, permission errors, OAuth failures, unavailable reads and generic transport
`provider_rejection` do not prove the original write failed. Invalid resource/URL
responses are rejected by the transport before content comparison. A query-bearing
Google `searchUrl` may pass the transport but fail the existing stricter storage
`PublicURL` contract; reconciliation then remains unresolved. URLs are never
rewritten to make them acceptable. This is deliberately stricter than the original
write path, which can record publication while omitting an incompatible optional URL.

`RecordGoogleReconciliation` is an internal command, not a teacher action accepting
provider evidence. Its event reference, actor and aware timestamp accompany a safe
reason, allowlisted state, tri-state match result, the existing resource, optional
safe public URL and resulting status. The attempt reference is bound by its containing
history and command receipt. No retrieved content, raw response, credentials or error
body enters the event, command identity or receipt.

Only eligible `in_progress`/`outcome_unknown` attempts can gain these observations.
Repeated unresolved observations are allowed. Terminal attempts cannot be rewritten.
Previous successful publications survive failed or unresolved updates. Strict
restoration checks evidence/status consistency, target identity, event ordering and
receipt bindings. The optional event `reconciliation` field is absent from old
events, preserving their canonical JSON and `pilot-campaign.v1` compatibility.
The repository continues to enforce immutable prior history.

## Replay, crashes and storage uncertainty

Request IDs remain campaign-scoped and bind normalized action, workshop, campaign,
round, attempt and authenticated actor. Expected revision remains a precondition,
not intent. Every replay reauthorizes the teacher. An existing receipt returns that
exact historical observation without another GET, even if later observations exist.
A changed action, attempt or actor under a recorded ID conflicts.

A read uses the revision checked before GET. Its observation and receipt must be
saved atomically against that revision. A concurrent change cannot be overwritten
with the older observation. After a save exception or conflict, the service reloads
and searches for the exact command identity, fingerprint and request binding. A
positively found receipt is returned; otherwise it reports storage uncertainty or
conflict without claiming the target is unblocked and without a second GET.

No-op precondition/manual responses and uncommitted observations do not create
receipts, matching the existing service contract. Use a new request ID for a later
explicit observation unless the previous request is proven absent by a successful
repository read. A crash after GET but before saving can leave no receipt; repeating
a read after confirmed absence is safe because it has no external write effect.
Simultaneous invocations may each read before either receipt exists, but each performs
at most one GET and the original revision permits only one observation commit.
There is no automatic polling, retry or exactly-once provider-read guarantee.

Teacher responses contain fixed German messages, safe references/revision,
publication status, reconciliation requirement, allowlisted state, safe public URL,
and `payload_matched` (`true`, `false`, or null when unestablished). They omit copy,
provider payloads, resource paths, fingerprints, actors and private error details.

## Sources and remaining work

Official sources checked 2026-09-27:
[LocalPost fields and states](https://developers.google.com/my-business/reference/rest/v4/accounts.locations.localPosts),
[GET contract](https://developers.google.com/my-business/reference/rest/v4/accounts.locations.localPosts/get),
and [MediaItem fields](https://developers.google.com/my-business/reference/rest/v4/accounts.locations.media).

Resource-less attempts, representation differences and other unresolved evidence
still need manual investigation; no manual success or “no post exists” override is
implemented. Teacher-app integration, deployment, App Check integration, production
Firestore/IAM, Business Profile API approval and real OAuth/location setup remain
unverified. A separate authorized live pilot must verify access and provider behavior.
All verification here is offline and proves neither production access nor successful
publication. No live Google/Firebase request, deployment or publication is performed.

## Verification

Tests use synthetic content, fixed clocks, fake HTTP/OAuth/Firebase contexts and
in-memory repositories. They cover strict observations, each comparison field,
previous-version update mismatches, status mapping, eligibility, exact call counts,
replays, uncertain saves/receipt recovery, append-only history, legacy restoration
and sanitized Callable responses. The full suite's socket guard remains active.

An independent focused review found no actionable implementation or documentation
issues and independently ran **318 tests: all passed in 31.70s** on Python 3.11.
Additional regression checks and the final suites include the old-visible-version
update case, unconfirmed observation-save recovery, corrupt payload bindings and
Callable requests without eligible resources. No code corrections were required
by review; affected older documentation was updated to reflect explicit reconciliation.

Final complete locked runs on 2026-09-27, after review, used macOS 15.6.1 arm64,
uv 0.11.31 and pytest 9.1.1 in separate existing temporary environments:

| Interpreter | Actual result |
| --- | --- |
| Python 3.11.15 | 1083 passed in 56.68 seconds |
| Python 3.13.14 | 1083 passed in 54.41 seconds |

These include 121 new cases. Exact commands (machine-specific interpreter paths):

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

The project `.venv`, lockfile, dependencies, historical workshop reconstruction,
prompts and Firebase/OAuth configuration were unchanged. The strict request example,
affected local Markdown targets and `git diff --check` also passed. No live smoke
check was performed.
