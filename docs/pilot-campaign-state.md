# Pilot campaign state and application commands

The package now combines the pilot domain workflow behind `execute_command` and a
serializable `CampaignState`. This is a local application boundary, not a server,
database adapter, permission system, or publishing implementation. It reuses the
[revision and approval rules](draft-revision-approval.md) and
[submission preparation](submission-preparation.md).
The separate [teacher service facade](pilot-teacher-service.md) now supplies
verified server context, injected access checks and strict client-intent requests.

## State and history

`pilot-campaign.v1` records the campaign and workshop references, an integer
aggregate revision, opaque creator references, aware creation/update timestamps,
and marketing rounds with purpose and creation metadata. Each round contains
Google Business and Rausgegangen independently, with an enabled flag, draft
history, explicit current-version reference, approvals, prepared packages, and
last-action diagnostics. Unselected channels start disabled.

Versions, approvals, and packages receive the ID of their creating command as a
record reference. References are resolved within the requested round and channel;
fingerprints bind content, not record identity. Two records may contain identical
content and therefore the same fingerprint. An approval still references only
its original record. Selecting another record never transfers that approval.
Parent record references preserve branching from an older selection, alongside
the existing domain parent fingerprint. Source HTML is retained per version
lineage, including German Unicode and line breaks; a later attached generation
can retain its own original source description.

Disabling a channel retains all records and blocks its draft actions, approval,
and preparation. Re-enabling or selecting an old version sets the last-action
status to `review_required`; it grants no readiness. Historical approvals and
packages survive later source changes. New approval/preparation actions check the
supplied current evidence and explicit time through the existing domain functions.
A blocked action records its diagnostics without removing earlier successes.
Publication attempts and their append-only evidence events are stored separately
from drafts. The commands below record reported outcomes without contacting or
independently verifying a provider.

## Explicit commands

Commands are frozen, strict Pydantic models in `workshop_marketing_agent.application`.
Every command includes campaign/workshop/round references, a campaign-scoped
`command_id`, an opaque `actor_reference`, aware `now`, and `expected_revision`.
Channel commands also name the channel.

| Command | Additional inputs and effect |
| --- | --- |
| `StartRound` | Purpose and unique selected channels; append a round |
| `AttachDraft` | One typed channel draft, facts, generation metadata and selected image; run the existing domain constructor/validation and append a version |
| `DirectRevision` | Current record reference, title, text and image; append through direct domain revision |
| `AIRevision` | Current record reference, instruction, image, model and bounded timeout; require an injected `DraftClient` and append only the domain result |
| `BindLink` | Current record reference and exact campaign metadata; bind final links into a new version before its review/approval |
| `SelectVersion` | Existing record reference in this round/channel; change selection without deleting history |
| `ApproveVersion` | Current record reference; recheck current evidence and record exact approval |
| `PrepareSubmission` | Current record and its approval references; recheck evidence and preserve the exact prepared package |
| `SetChannelEnabled` | Explicit boolean; preserve all history |
| `ReservePublication` | Exact current version, approval and preparation references/fingerprints, final payload fingerprint and a new stable operation ID; recheck readiness and atomically reserve the target |
| `RecordPublicationResult` | Trusted server-only Google result for a reserved attempt: published, known failure or unknown outcome |
| `RecordGoogleReconciliation` | Internal exact-version GET observation for an eligible uncertain Google attempt; append safe comparison evidence |
| `ConfirmSubmission` | Teacher confirmation that a reserved Rausgegangen listing was submitted |
| `ConfirmPublication` | Later teacher confirmation of actual Rausgegangen publication, with an optional public HTTPS URL |

Attach, revise, bind, approve and prepare require `FeedImportResult` evidence.
Generation itself remains an injected/upstream domain operation; attachment does
not accept `valid` or `approved` flags. It validates each channel independently,
allowing one channel's invalid draft or failed AI revision to coexist with the
other's successful work. Invalid transitions (for example a cross-round version,
wrong workshop, disabled channel, or missing AI boundary) return `rejected` without
a mutation. Domain outcomes such as `outdated` or `review_required` are committed
with diagnostics and a command receipt. An AI failure that yields no new version
preserves the current selection and complete history.

## Storage, command identity and concurrency

`CampaignRepository` defines only `load` and atomic `compare_and_save`.
`expected_revision=None` creates a previously absent revision-zero campaign;
subsequent saves must atomically compare the existing revision and advance it by
one, saving the new state and receipt together. `InMemoryCampaignRepository`
implements this with a lock and isolated JSON snapshots. It also rejects rewriting
existing history. Production adapters must preserve the same atomic and
append-only contract; a read followed by an unconditional write is insufficient.
The [Firestore adapter](firestore-campaign-storage.md) now implements that contract;
its production configuration and index prerequisites remain unverified.

`execute_command` loads the campaign, checks for a receipt, checks the expected
revision, executes once, and compare-and-saves. It returns `committed`, `replayed`,
`conflict`, or `rejected`, with recorded result references when available. It does
not retry after a save conflict. A future adapter should reload and let the caller
resolve the conflict; it must not blindly repeat a model call.

Command identity is SHA-256 of canonical UTF-8 JSON containing:

- All serialized command fields except `expected_revision`, including kind, IDs,
  actor, supplied time, wording, references, link metadata, model and timeout.
- A SHA-256 fingerprint of the complete serialized supplied `FeedImportResult`,
  including diagnostics and observation data, or explicit null if absent.

The receipt stores this canonical identity JSON, its fingerprint, and the exact
command result. It stores an evidence digest rather than another full feed copy.
Expected revision is a compare-and-save precondition, not intent: refreshing it
alone allows retrieval of a completed command's receipt. Changed evidence, time,
actor or wording requires a new command ID. Client implementation objects,
credentials and repository objects never enter command identity or stored state;
use explicit model identifiers and keep injected client configuration consistent.

A completed identical retry returns its recorded result without mutation or a
model call, even after JSON restoration. This is a **historical result**, not a
fresh readiness check. Use a new command ID and current evidence/time for a new
preparation. Reusing an ID with different input conflicts. A stale revision with
no matching receipt conflicts before executing. Concurrent attempts may both call
an injected model before compare-and-save, but at most one state mutation commits.
A crash before saving can likewise leave a model call unrecorded. There is no
external exactly-once guarantee or automatic retry. Publication reservations are
implemented below; durable model-execution reservations remain unimplemented.
Rejected transitions and losing save attempts have no committed receipt.

The teacher facade may additionally store a server-only `ServiceRequestBinding`
on a receipt (or on campaign creation). It binds authenticated actor and normalized
client intent independently of sampled time/evidence. The full command identity
above is unchanged. The facade retrieves matching historical receipts before
calling providers; it does not rebuild a different command under the old ID.
Absent bindings are omitted, preserving pre-service canonical JSON exactly.

## Publication target and immutable binding

One publication target covers **one campaign, workshop and channel across all
rounds**. Its fingerprint is derived by the server from those identities; callers
cannot choose another target to bypass a reservation. Each campaign represents
one canonical promotion history for that workshop/channel. The repository's CAS
contract protects that aggregate, not separate campaigns: a future integration
must reuse the canonical campaign, rather than create another campaign to retry.
Multiple provider accounts/locations or campaign-wide target reassignment are
not supported by this pilot.

`ReservePublication` uses its command ID as the attempt reference. It records a
separate campaign-unique operation ID, initiating actor/time, exact record
references and SHA-256 fingerprints for version, complete approval, prepared
submission and final payload. The payload fingerprint covers the existing
Google provider projection or Rausgegangen copy package without copying another
request body. The attempt's binding and target never change. Each status event
records its command reference, actor, aware time and reservation/automatic/manual
source; the current status is derived from the last event.

Reservation requires the enabled channel's explicitly selected version, its
matching approval/prepared package and fresh supplied evidence. Existing pure
preparation rules run again; changed sources, unsupported claims or changed
payloads reject reservation without a receipt. A recheck does not replace the
original prepared snapshot or its check timestamp. Draft status alone never
establishes readiness.

The reservation selects `create` only if no successful publication exists for the
target. Otherwise it selects `update` and binds the known external post ID. A
publication lacking an update identity blocks further attempts, rather than
silently becoming a new create. Google success requires a nonempty opaque external
post ID, and an update result must preserve its reserved ID. A failed update
retains all previous successes/IDs. The IDs are restricted non-personal strings,
not credentials or free-form provider responses.

## Publication transitions and uncertainty

| Source | Transition | Effect on another reservation |
| --- | --- | --- |
| Reservation | New attempt → `in_progress` | Blocked; state plus command receipt commit atomically |
| Trusted Google result | `in_progress` → `published` | A new explicit attempt may update the same known post |
| Trusted Google result | `in_progress` → `failed` | A new explicit attempt with new command/operation IDs is permitted |
| Trusted Google result | `in_progress` → `outcome_unknown` | Blocked until definitive reconciliation |
| Trusted Google reconciliation | Eligible `in_progress`/`outcome_unknown` → `published`, `failed` or `outcome_unknown` | Only an exact confirmation or definitive rejection unblocks |
| Teacher, Rausgegangen only | `in_progress` → `submitted` | Blocked; submitted is not published |
| Teacher, Rausgegangen only | `submitted` → `published` | Remains blocked without an external update identity |

Manual publication is a separate event and may contain an optional HTTPS public
URL; submission cannot carry a publication URL. Manual confirmations cannot
target Google. Automatic-result commands cannot target Rausgegangen. Rausgegangen
confirmations do not accept provider IDs or technical error/result fields. There
is no manual cancellation/retry override. The internal reconciliation command is
described in [exact-version reconciliation](google-publication-reconciliation.md).

Known failures require one of `permission_denied`, `invalid_submission`,
`provider_rejected` or `provider_unavailable`. These categories mean a *known*
negative result; a timeout, lost connection or crash that could follow a send
must be recorded as `outcome_unknown`, never guessed to have failed. Unknown
results have no terminal-failure shortcut. Automatic unknown results may retain an
optional validated Google post name internally for explicit reconciliation; this does
not mark publication or unblock another attempt, and updates must match their target. An unresolved `in_progress` record
after a crash also blocks recreation; elapsed time is not permission to retry.

Result recording remains possible after a channel is disabled or a different
draft is selected: it describes the original reserved operation. It grants no
approval to the new draft. Published and failed attempts are terminal. Eligible unknown attempts can gain
reconciliation observations; earlier events are never edited or removed.

Only a committed reservation may precede a future provider operation. Concurrent
reservations compete on the same aggregate revision; at most one commits. The
application makes no provider call and performs no automatic retry. Identical
command replay returns its historical receipt. Changed intent under a command ID
conflicts. An operation ID is single-use; even a new command with that ID must
instead replay the original command or use a new identity after a known failure.

`RecordPublicationResult` is a trusted application command, not teacher input.
The [publication service](google-publication-service.md) now authorizes Google
reservation and result orchestration. Manual confirmation service actions remain
later work. OAuth wiring and explicit reconciliation are offline-tested; actual
Firebase Google configuration, teacher UI and deployment remain unverified or unimplemented.

## Publication restoration and views

Restoration checks strict event shapes, legal transitions, unique references and
operation IDs, immutable bindings, exact fingerprints and one matching receipt
per event. It reconstructs target availability, enabled state and version
selection in receipt order, including same-timestamp commands across rounds.
Reservations cannot refer to records created by later receipts. Append-only
repository checks prohibit changing an old event or attempt binding.

Absent `publications` and `attempt_reference` fields are omitted from canonical
JSON. Legacy `pilot-campaign.v1` snapshots retain their original bytes and hashes,
without migration or repair; old readers are not promised to understand new
publication fields. The existing repository revision contract is unchanged.

`publication_views` and the optional channel `publications` view expose only
channel, operation, safe status, attempt/version references, timestamps, public
URLs, safe error categories and whether another attempt is blocked. Events retain
earlier recorded statuses. Views omit actors, operation IDs, external provider
IDs, fingerprints, receipts, command identity JSON and payloads. An unblocked
target is not publishing approval; current readiness must still be checked.

Public URLs must be absolute HTTPS without credentials, fragments, query strings,
malformed escapes or embedded email addresses. Local/private IP addresses and
local host suffixes are rejected, including normalized numeric IP spellings.
URLs are not resolved or fetched, so reachability/ownership are not verified.
Callers must supply non-personal opaque references and public listing URLs;
syntax validation is not a general secret or personal-data detector.

## Strict JSON restoration

`dump_campaign` produces a JSON envelope with the state and a snapshot fingerprint.
`restore_campaign` rejects unsupported versions, extra/duplicate keys, non-finite
numbers, malformed typed values, naive timestamps, broken references, inconsistent
parents, mismatched content fingerprints, approvals, packages and command receipts.
Decimal prices serialize as strings, preserving their exact value and scale.
Domain versions, typed channel content, metadata, diagnostics and their original
fingerprints survive round trips. Package restoration checks the existing pure
channel mapping as well as its fingerprint; it does not rewrite a corrupted hash.

Snapshot and record hashes detect corruption and inconsistent bindings, not
malicious forgery. They are not signatures. Restored statuses and receipts cannot
prove authentication, permissions, source authenticity, or current readiness.
Untrusted clients must not be allowed to replace server-owned campaign snapshots.
The application revalidates supplied evidence on new approval/preparation actions.
Restore never sends requests or treats a stored approval/package as permission to
publish. It does not expire or delete historical records simply because time passed.

## Synthetic adapter flow

A future server adapter must authenticate and authorize the teacher for the
workshop **before** loading or executing commands. Actor references are recorded
opaque data; the package does not authenticate them or determine workshop access.
The server obtains sanitized current evidence and supplies trusted boundary
configuration. Do not put secrets, participant/payment data, or private credentials
in command text, metadata, diagnostics, source content or stored snapshots.

After that external authorization step, this synthetic local example creates a
campaign and starts a round without credentials or network access:

```python
from datetime import UTC, datetime
from workshop_marketing_agent.application import StartRound, execute_command, new_campaign
from workshop_marketing_agent.campaign_state import InMemoryCampaignRepository

now = datetime(2026, 10, 24, 10, tzinfo=UTC)
repository = InMemoryCampaignRepository()
initial = new_campaign(
    campaign_reference="campaign-demo", workshop_reference="workshop-demo",
    actor_reference="teacher-demo", now=now,
)
assert repository.compare_and_save(initial, expected_revision=None)
loaded = repository.load("campaign-demo")
outcome = execute_command(repository, StartRound(
    campaign_reference=loaded.campaign_reference,
    workshop_reference=loaded.workshop_reference,
    round_reference="round-demo", command_id="start-demo",
    actor_reference="teacher-demo", now=now, expected_revision=loaded.revision,
    purpose="Erste Ankündigung", selected_channels=("google_business", "rausgegangen"),
))
# execute_command loads again and atomically compare-and-saves state plus receipt.
response = {"outcome": outcome.status}
if outcome.result is not None:
    response["revision"] = outcome.result.revision
    response["action_status"] = outcome.result.status
```

The complete adapter flow is: authenticate and authorize → load campaign → invoke
one command → atomic compare-and-save inside the application service → return an
allowlisted, sanitized result. Do not expose arbitrary diagnostics, raw source
copy or command receipts to unauthorized callers. A conflict returns no unsaved
package. The pilot teacher policy and Callable adapter are implemented separately;
publication Callable actions, UI, durable work scheduling, production deployment
and external publishing remain later work.

## Offline verification

`tests/test_application.py` uses synthetic workshop evidence, fixed current times,
fake model boundaries and isolated in-memory repositories. It covers both
channels, independent failure, multiple rounds, disabled channels, branch/approval
history, links before approval, changed evidence, JSON round trips and corruption,
receipt replay and conflicts, and concurrent model attempts with one committed
mutation. Run through `uv run --locked python -m pytest`. Existing generation,
revision and submission tests continue to cover the reused domain rules.

`tests/test_publication.py` adds synthetic reservations, create/update selection,
target contention across rounds, results, manual confirmations, replay, strict
restoration, immutable history and safe views. A golden hash generated with the
merged PR17 implementation checks the unchanged canonical two-channel legacy
snapshot (37,492 UTF-8 bytes). That actual predecessor snapshot was also restored
and re-serialized byte-for-byte with the new implementation.

Verification on September 26, 2026, macOS 15.6.1 arm64, uv 0.11.31 and pytest 9.1.1:

| Interpreter | Full locked suite after independent review |
| --- | --- |
| Python 3.11.15 | 645 passed in 19.71 seconds |
| Python 3.13.14 | 645 passed in 19.91 seconds |

Each run used `uv run --locked --python <explicit-interpreter> python -m pytest`,
with `UV_OFFLINE=1`, a cleared process environment and a separate
`UV_PROJECT_ENVIRONMENT` (`/tmp/workshop-callable-test311` and
`/tmp/workshop-callable-test313`). The project's `.venv` was not synchronized.
The independent review found no actionable issues and independently ran all
66 publication tests on Python 3.11.15: 66 passed in 13.17 seconds. No review
corrections were needed. The focused publication/application/Firestore/service
run passed 228 tests in 18.10 seconds before the final full runs.

Local documentation links and `git diff --check` passed. Dependencies, lockfile,
Callable actions, prompts, channel rules and historical examples are unchanged.
No production services, provider requests, credentials or deployment were used.

Explicit [Google reconciliation](google-publication-reconciliation.md) now appends trusted
observations to eligible uncertain attempts without changing earlier records.
