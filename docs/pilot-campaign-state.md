# Pilot campaign state and application commands

The package now combines the pilot domain workflow behind `execute_command` and a
serializable `CampaignState`. This is a local application boundary, not a server,
database adapter, permission system, or publishing implementation. It reuses the
[revision and approval rules](draft-revision-approval.md) and
[submission preparation](submission-preparation.md).

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
No command can produce a submitted/published status or external post ID.

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
external exactly-once guarantee, reservation infrastructure, or automatic retry.
Rejected transitions and losing save attempts have no committed receipt.

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
package. Production storage, authorization, HTTP endpoints, UI, durable work
scheduling and external publishing remain later work.

## Offline verification

`tests/test_application.py` uses synthetic workshop evidence, fixed current times,
fake model boundaries and isolated in-memory repositories. It covers both
channels, independent failure, multiple rounds, disabled channels, branch/approval
history, links before approval, changed evidence, JSON round trips and corruption,
receipt replay and conflicts, and concurrent model attempts with one committed
mutation. Run through `uv run --locked python -m pytest`. Existing generation,
revision and submission tests continue to cover the reused domain rules.
