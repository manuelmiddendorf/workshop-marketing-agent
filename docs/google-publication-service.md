# One recorded Google publication through PilotService

`publish_google` connects the existing publication commands to an explicitly
injected Google transport. It is limited to `malws-copy` and `google_business`.
This is framework-independent orchestration, verified offline only. Existing
Firebase wiring does not supply a Google boundary, so it cannot publish.

## Teacher intent and server authority

The strict request accepts only:

```json
{
  "action": "publish_google",
  "workshop_reference": "malws-copy",
  "campaign_reference": "campaign-demo",
  "round_reference": "round-demo",
  "version_reference": "version-demo",
  "approval_reference": "approval-demo",
  "submission_reference": "submission-demo",
  "request_id": "publish-request-demo",
  "expected_revision": 7
}
```

The server injects `ServiceDependencies.google`, implementing `GooglePublisher`
(`location`, `create_event`, `update_event`); the existing `GoogleLocalPosts` fits
this protocol. Other service actions require no Google configuration. Missing or
malformed publication configuration prevents reservation. Credentials remain in
the injected boundary; neither commands nor state construct or store them.

Authorization runs before repository/evidence access, including on replay. The
server derives the channel, actor, time, fingerprints and payload from trusted
context and loaded records. Existing reservation rules check campaign/workshop
ownership, enabled channel, selected version, matching exact approval/preparation,
fresh evidence and current readiness. No copy, image or workshop facts are changed.

## Two commits, one possible Google call

1. Refresh the configured public-feed evidence. Build `ReservePublication` from
   loaded records and the existing normalized service-request binding.
2. Atomically save reservation plus receipt with the expected revision. Reload
   and confirm the **exact command identity, fingerprint and service binding**.
3. Only the owning invocation calls the adapter once, using the committed attempt's
   create/update choice, stored update target and bound `GoogleSubmissionPayload`.
4. Build `RecordPublicationResult` and compare-and-save the result plus receipt.
   Reload and confirm that exact result receipt before reporting completion.

Each reservation uses a server-generated per-invocation operation nonce. Its
command ID remains derived from the campaign-scoped request ID. If a reservation
acknowledgement is lost, only the invocation with that exact nonce/command receipt
may proceed after positive confirmation. A concurrent identical request cannot
adopt the reservation. Conflicts and unresolved reads prevent dispatch; a replay
found inside command execution also never dispatches.

Result command identity is derived from the reservation reference, with its
initiating actor and a server timestamp. Result saving uses the latest loaded
revision so an intervening unrelated edit need not discard provider evidence.
After a result-save conflict or exception, the service checks its exact receipt;
it does not repeat a save or Google call. A missing/unreadable result receipt yields
`unknown_commit_outcome`, requiring reconciliation. Where a reservation was confirmed,
the response carries that last confirmed revision and `in_progress` status; it does
not assert that a possibly committed result is absent. Otherwise it makes no claim
about a committed attempt. Unconfirmed stored state remains blocking if reserved.

## Conservative outcome mapping

| Adapter result | Durable event |
| --- | --- |
| `visible`, valid configured-location resource, `LIVE`/`RECURRING` | `published` |
| `pending` / `unresolved` | `outcome_unknown`, optionally retaining a valid resource name |
| `ambiguous_write`, exception, malformed result or invalid visible identity/state | `outcome_unknown` |
| `rejected` / `provider_rejection` | `failed` / `provider_rejected` |
| `authentication_or_permission` | `failed` / `permission_denied` |
| `invalid_input` | `failed` / `invalid_submission` |
| `unavailable` / other unrecognized outcome | `outcome_unknown` |

The current adapter provides no proof-of-no-dispatch field for unavailable writes;
therefore orchestration never guesses `failed/provider_unavailable` from that result.
HTTP success alone is insufficient. `PROCESSING` and `SCHEDULED` remain blocking
unknown outcomes. Update results must retain the exact reserved provider identity.
There is no fallback from update to create and no automatic retry or `get_post` call.

Automatic unknown events may now retain an optional syntactically validated Google
post name for future reconciliation. Manual/failed/reservation events cannot gain
that field, and unknown events cannot gain a public URL. Existing update-target
consistency checks still apply. No fields or schema versions were added: legacy
snapshots and canonical serialization remain unchanged. Teacher views never expose
internal Google resource names, operation IDs, actor hashes, fingerprints or receipts.

For published events, an optional URL must meet the existing stricter `PublicURL`
storage policy. Google `searchUrl` values containing query strings, for example,
are omitted, never rewritten; the validated post identity still records publication.
Safe responses contain fixed German messages, necessary references, revision,
operation, publication status, reconciliation flag and an optional safe public URL.
Raw Google/storage details, request bodies and configuration are never returned.

## Replay, crashes and scope

The existing request identity is **campaign-scoped**, explicitly confirmed by the
owner for this pilot. Global cross-campaign request locking is out of scope. Within that scope the binding
includes normalized intent (including campaign, round, version, approval and
submission) and authenticated actor. Changed intent/actor conflicts. A changed
expected revision alone is a precondition change and can retrieve a historical
receipt. Another campaign is a separate namespace; the repository has no global
request index. As with existing publication targets, callers must reuse the canonical
campaign rather than create a new campaign to evade history or blocking state.

Identical replay returns the recorded result without refreshing evidence or calling
Google. If only the reservation exists, replay returns `in_progress`, requires
reconciliation and never resumes. A new request against a blocked target returns
`reconciliation_required`. A crash before dispatch can therefore require the same
manual investigation as a crash after dispatch. There is no external exactly-once
guarantee and elapsed time does not permit retrying an unknown outcome.

Remaining work: OAuth wiring, API approval, reconciliation, Firebase secret/Callable
configuration, teacher UI, deployment and live verification. Offline tests establish
neither production Google access nor successful external publication. No live Google
or Firebase requests, credentials, automatic reconciliation or deployment were used.

## Verification

`tests/test_service_publication.py` uses synthetic workshop evidence (under the pilot
identity), fixed clocks, in-memory storage and a fake Google adapter. It checks exact
payloads and ordering, strict intent, authorization/readiness, all outcome mappings,
replays, contention, lost acknowledgements and safe restoration/views. Existing
publication tests retain the legacy snapshot hash check.

Run with `uv run --locked --python <explicit-interpreter> python -m pytest` in separate
environments. An independent focused review found no actionable code issues.
Its two stale architecture statements were corrected and independently rechecked.
The owner-confirmed campaign-local request scope is covered by an explicit regression
case, including rejection of changed action within the original campaign.

The final complete runs used pytest 9.1.1 and uv 0.11.31 (Homebrew 2026-07-22)
on macOS 15.6.1 arm64. Both runs used separate existing temporary environments,
cleared inherited environment variables, offline dependency resolution and the
suite's socket guard. The project `.venv`, dependency lock, historical reconstruction,
prompts and provider transport were unchanged.

Actual commands from the repository root (interpreter paths are machine-specific):

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

Final results on 2026-09-27, after review corrections and the scope regression test:

| Interpreter | Full locked result |
| --- | --- |
| Python 3.11.15 | 848 passed in 33.31 seconds |
| Python 3.13.14 | 848 passed in 32.84 seconds |

These include 66 new orchestration cases and the existing legacy canonical snapshot
checks. The documented JSON request validates against `REQUEST_ADAPTER`; local
Markdown targets and `git diff --check` pass. No live publication is claimed.
