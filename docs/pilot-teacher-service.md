# Authenticated pilot teacher service

`PilotService` in `workshop_marketing_agent.service` is a synchronous,
framework-independent server facade over the existing campaign application.
It accepts verified server context separately from strictly validated request
JSON. It adds neither an authentication provider nor a Callable/HTTP endpoint.
There are no new dependencies, credentials, environment configuration, client
initialization or network calls at import time. Existing Pydantic internals may
read their plugin switch; the facade reads no application environment values.

## Trusted boundary and order

Construct `ServiceDependencies` explicitly with:

- `repository`: the existing `CampaignRepository`, including the
  [Firestore adapter](firestore-campaign-storage.md) with a server-created client.
- `check_access(principal, workshop_reference)`: existing teacher-access policy,
  supplied by a future integration. Only the literal result `True` grants access;
  missing, ambiguous or exceptional decisions fail closed.
- `clock()`: aware server time. Domain timestamps are never browser values.
- `endpoint_for(workshop_reference)`: trusted configuration returning the complete
  HTTPS public-feed URL, without credentials or a fragment.
- `feed_loader(endpoint, timeout) -> FeedResponse`: a bounded HTTP boundary with
  the same signature as the public importer's HTTP boundary. It must enforce the
  timeout and response-size bound, disable redirects/retries, and not log bodies.
  The existing importer supplies `_http_get` for server wiring; routine tests
  inject a synthetic response instead.
- `client`, `model`, `model_timeout`: an explicitly configured `DraftClient` and
  model. No fallback client is constructed. The official `OpenAIDraftClient`
  already disables automatic model retries.
- `feed_timeout`: explicit positive timeout. Both configured timeouts are capped
  at 120 seconds. These are per-boundary limits, not a total request deadline.
- `workshops`, `channels`: explicit pilot allowlists. Only `malws-copy` is confirmed
  as the real pilot; tests use a separate synthetic workshop. Supported channels
  are `google_business` and `rausgegangen`.

`handle(request_data, principal=...)` requires a `VerifiedPrincipal` constructed
by trusted server code. Its subject is the verified authentication identity, not
a name/email or a browser field. Recorded actors are deterministic `teacher-`
plus SHA-256 of that subject. This is a stable pseudonymous reference, not proof
of access or anonymization. Project/tenant scoping must be consistent in the
future wrapper; do not combine unrelated authentication namespaces.

The sequence is authentication context → strict request validation → workshop
allowlist → affirmative workshop access → channel allowlist → campaign load and
workshop binding → committed-request lookup → expected revision → required evidence
and domain action → atomic save. Authorization is checked again on every replay.
Wrong-workshop campaigns appear as `not_found`; denied requests reveal no campaign
existence. The service delegates round/version/approval association checks to the
existing application. It never silently chooses another version or channel.

For fresh evidence, the loader first obtains the bounded response. The service
then samples the server clock and passes that **post-fetch time** to
`load_public_workshop`, using the already fetched bytes. Thus an observation made
during the fetch is not incorrectly diagnosed as future evidence. Feed timestamps
never replace source versions, observation timestamps or freshness policies.
Existing domain functions preserve inactive, stale, conflicting, outdated and
review-required outcomes. A failed feed import returns no usable input.

## Request schema

The discriminated `TeacherRequest` union / `REQUEST_ADAPTER` is the executable
schema. All fields are strict and extra fields are forbidden. The public entry
accepts a JSON-compatible dictionary. Opaque references use the existing ASCII
reference syntax (1–128 characters). JSON arrays represent channel tuples.

Every action requires `action`, `workshop_reference`, `campaign_reference`.
Every mutation also requires `request_id`. Except for creation, mutations require
`expected_revision` (nonnegative integer) and `round_reference`. Channel actions
require `channel`; version actions additionally require `version_reference`.

| Action | Additional intent | Fresh evidence? |
| --- | --- | --- |
| `get_campaign` | No mutation fields | No; historical view only |
| `create_campaign` | `request_id`; creation requires absence | No |
| `start_round` | `purpose` (1–500 chars), unique `selected_channels` | No |
| `generate_drafts` | Unique `channels` (1–2); only enabled channels without versions | Yes |
| `direct_revision` | Exact version, `title` (1–500), `text` (1–10,000), explicit `selected_image_reference` or null | Yes |
| `ai_revision` | Exact version, `instruction` (1–500), explicit `selected_image_reference` or null | Yes |
| `bind_link` | Exact version, `variant_reference`; other campaign/UTM values are server-derived | Yes |
| `select_version` | Exact existing version | No; never establishes readiness |
| `approve_version` | Exact selected version | Yes |
| `prepare_submission` | Exact selected version and `approval_reference` | Yes |
| `set_channel_enabled` | Strict boolean `enabled` | No; enabling requires review |

The initial image is the imported workshop image reference, including unknown
permission/conflicts; absence stays null. No image permission is inferred.
Direct/AI revisions may request an image selection, but the existing validator
checks it against current evidence. Original public copy is never modified.
Actors, times, feed objects, source versions, model settings, endpoints,
credentials, validation flags, approval statuses and publication results are not
accepted as request fields. Text is untrusted content, not an instruction to the
service or a source of authority.

Example request (synthetic; the server supplies the separate principal):

```json
{
  "action": "start_round",
  "workshop_reference": "synthetic-workshop-01",
  "campaign_reference": "campaign-1",
  "request_id": "start-1",
  "expected_revision": 0,
  "round_reference": "round-1",
  "purpose": "Erste Ankündigung",
  "selected_channels": ["google_business", "rausgegangen"]
}
```

## Generation, durable replay and concurrency

Initial generation invokes the existing two-channel generator **once**, with its
existing prompt and schema unchanged. Only requested, enabled empty channels are
attached. Each attachment has a stable distinct command ID derived from request
ID and channel; other actions derive one ID with the `action` slot. Channel
attachments commit independently and never overwrite previous versions.

`ServiceRequestBinding` stores request ID, derived actor and a fingerprint over
`teacher-intent.v1`, actor and all normalized client intent except expected
revision. It contains no raw request copy. Creation stores the binding in
`CampaignState.creation_request`; mutations store it atomically with their
`CommandReceipt.service_request`. Bindings cannot rewrite existing history and
must match the recorded actor. Absent bindings are omitted from canonical JSON,
so pre-service Firestore snapshots retain their original bytes and fingerprints.
No migration or hash repair is performed.

Before calling clocks, feeds or models, the service matches the persisted binding.
Identical committed intent returns the recorded domain results even when server
time, feed timestamps or configuration have since changed. A changed revision
precondition alone does not change intent. Reusing the same campaign-scoped
request ID with a different actor/action/wording/selection conflicts. The existing
application's full command identity (including its original time and evidence
digest) and checks remain intact: the facade retrieves the bound historical
receipt instead of rebuilding a different command under the old ID.

If only some generation attachments committed, replay returns `partial_completion`
and `pending_channels`, with `automatic_resume: false`, without invoking the model
or silently attaching new content. A teacher must inspect the stored result and
explicitly request remaining empty channels under a new request ID/revision.
Generation failure before any attachment has no durable receipt. Failed AI
revision outcomes that the application commits do have a replayable receipt.

Concurrent requests may both reach the model before a receipt exists; CAS permits
only one state mutation per revision. A crash or failure before commit can leave
an unrecorded model call. This facade has no durable execution reservation and
makes no exactly-once or automatic-resume promise. Disable automatic transport
retries for provider actions in the later wrapper/client. After an unknown commit,
reload and reconcile the request's receipt before any deliberate new action;
absence alone cannot prove a still-in-flight commit failed. Never blindly retry
an uncertain action or model call.

## Response schema and safe failures

Responses are JSON-compatible dictionaries built only from explicit projections.
Every response has stable English `status` and a fixed German `message`.
Optional fields depend on the action/outcome:

- `campaign`: requested campaign/workshop references, `revision`, rounds with
  reference/purpose, allowlisted channels with enabled flag/current version,
  version reference/title/text/image/booking URL/validation status, recorded
  approval and preparation references, and safe diagnostics.
- `results`: committed/historical revision, round/channel, status, exact version,
  approval and preparation references, safe diagnostics and
  `readiness: "historical_check_only"`. Uncommitted model output is never returned.
- `historical`, `refresh_required`, current `revision` on known conflicts,
  `automatic_resume`, `pending_channels`, safe `failure` or `uncertain_channel`.

Campaign/channel views always say `current_readiness: "not_checked"`.
`last_recorded_status`, approvals and preparations are historical records. Even
new preparation results record a check at that action's time; they neither grant
publishing permission nor promise continued readiness. Copy remains German and
must be rendered as text by a future UI, not executed as markup.

| Status | Meaning / handling |
| --- | --- |
| `ok`, `replayed` | Result available; replay is explicitly historical |
| `unauthenticated`, `forbidden` | No protected state/provider access |
| `not_found` | Missing campaign or workshop binding does not match |
| `invalid_request` | Invalid shape, disallowed channel or invalid domain association |
| `request_conflict` | Request ID belongs to different authenticated intent |
| `revision_conflict` | Refresh and explicitly resolve stale/concurrent state |
| `outdated`, `review_required` | Preserve domain facts and correct/review explicitly |
| `provider_unavailable` | Feed failure or model refusal/timeout/invalid response |
| `storage_unavailable` | Storage failed; no uncertain commit is represented as failure |
| `unknown_commit_outcome` | Candidate may have committed; reconcile, do not automatically retry |
| `partial_completion` | Keep committed channels; no automatic continuation |
| `internal_error` | Unexpected failure, with no internal error details |

Diagnostic fields and messages are **not forwarded**. Explicit categories produce
fixed German correction messages (image, availability, early bird, event, source,
copy, provider, generic review); unknown diagnostics get the generic message.
Responses exclude source HTML/provenance/private source references, raw command
receipts/identity JSON, model prompts/usage/provider response objects, credential
values and exception details. Reviewable draft wording is intentionally returned;
this is not a semantic redactor for arbitrary sensitive text supplied as copy.
The service logs nothing. Injected boundaries and the future wrapper must also
avoid logging raw requests, source/model bodies, exceptions or traceback locals.

## Future Callable wiring and remaining prerequisites

The [official Callable documentation](https://firebase.google.com/docs/functions/callable)
(checked September 26, 2026) distinguishes Python `req.data` from authenticated
`req.auth` context. A future wrapper must construct `VerifiedPrincipal` from
verified `req.auth.uid` (or pass null when unauthenticated), and call
`service.handle(req.data, principal=principal)`. Never construct the principal
from a UID, actor or token supplied inside `req.data`. Authentication alone does
not establish workshop access; inject the separately verified teacher policy.

Still required: inspect/implement existing teacher access rules; confirm tenant
and project/database/collection allowlists; configure server secrets and clients;
verify Firestore indexes/IAM; choose the model; measure a total deployment timeout
covering feed/model/storage; define client cancellation/retry behavior; then add
and deploy the Callable wrapper. None is configured or verified by this change.
There are no live provider calls, Firestore writes, teacher-app modifications,
Firebase decorators, web framework, publishing or participant data in this task.

## Offline verification

`tests/test_service.py` uses fake verified principals, access decisions, repository,
clock, bounded feed responses and model client. It covers authorization order,
forged fields, creation races, complete review workflow, durable replay with time
changes, request conflicts, stale revisions/evidence, partial results, model and
storage failures, uncertain commits, concurrent generation and sanitized views.
The Firestore tests verify legacy canonical snapshots with and without receipts.
Run `uv run --locked python -m pytest`; no credentials or network are needed.
Prompts, model choices and channel rules were not changed; no new live/qualitative
model evaluation is claimed.

Verified September 26, 2026 with native Python **3.13.14**:

```sh
env -i PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin \
  UV_CACHE_DIR=/tmp/workshop-firestore-uv-cache UV_OFFLINE=1 \
  uv run --locked python -m pytest
```

**499 tests passed in 5.80 seconds**, including 68 service tests and two legacy
storage regression cases. An independent review found the initial optional-field
serialization broke legacy canonical snapshots; omitting absent bindings fixed
it. Independent correction verification ran the service, application and Firestore
tests: **162 passed in 4.92 seconds**, with no remaining actionable findings.
Lockfile consistency, Python compilation, `git diff --check`, 63 local
documentation links and the documented JSON request schema check also passed.
No emulator, production database or live model was contacted.
