# Atomic Firestore campaign storage

`FirestoreCampaignRepository` implements the existing
[campaign repository contract](pilot-campaign-state.md) using the synchronous
official `google-cloud-firestore` client. This adapter adds storage only. It does
not create clients, discover credentials, select a project/database, authenticate
users, generate drafts or publish content. Importing it performs no network calls.

A later authenticated server adapter must verify teacher access, choose an
allowlisted project/database/collection, create the client explicitly, and pass it
into `FirestoreCampaignRepository(client, collection_name="pilotCampaigns")`.
The collection is a single root collection with an intentionally narrow name:
1–100 ASCII letters, digits, hyphens or underscores, beginning with a letter.
Paths, reserved names and implicit/default collection selection are rejected.
Actor references and stored approvals never authorize access or publication.

## Document contract

The document ID is `campaign-` followed by SHA-256 of the validated opaque campaign
reference's UTF-8 bytes. The reference is never interpreted as a Firestore path.
Each document is a complete replacement with these fields only:

| Field | Meaning |
| --- | --- |
| `storage_schema_version` | `pilot-campaign-storage.v1` |
| `campaign_reference`, `workshop_reference` | Exact restored state identities |
| `revision` | Strict integer aggregate revision |
| `snapshot_json` | Complete canonical `dump_campaign` JSON envelope |
| `snapshot_fingerprint` | The envelope's existing state fingerprint |
| `updated_at` | The state's aware domain update time |

Load and transaction reads use `restore_campaign` and verify document identity,
requested reference, schema, metadata and canonical snapshot agreement. Invalid
or unsupported data is rejected; hashes are never repaired. Unicode, exact decimal
strings, timestamps, approvals, prepared packages, histories and command receipts
remain inside the unchanged snapshot. Firestore operational commit/read times do
not replace domain timestamps or enter command identity. Hashes detect corruption;
they are not signatures and do not authenticate a supplied state.

## Atomic updates, retries and failures

The candidate is validated, serialized and size-checked before the transaction.
Inside one transaction the adapter reads and strictly restores the existing
document, checks the expected revision, reuses `_check_successor`, and queues the
fixed complete replacement. `expected_revision=None` permits only absence plus
revision zero. An integer requires an existing matching revision and exactly one
increment. Existing creation metadata and history cannot be rewritten.

The official transaction decorator makes at most **three callback attempts** for
aborted transactions. Callbacks contain only storage checks and the fixed candidate
write. Application commands, AI calls, clock reads and payload generation stay
outside them. Reads use a 10-second timeout and disable per-read RPC retries.
The locked SDK separately applies bounded default RPC retries: its begin/commit/
rollback transport policies have a 60-second retry deadline and 60-second default
RPC timeout. These are not a single end-to-end operation deadline. Retrying the
same transaction RPC does not rerun an application command. No application-level
retry loop is added.

A small `Transaction` subclass observes the SDK's protected `_commit` boundary to
distinguish attempted commits from confirmed aborts, including failures starting
a subsequent attempt or rolling back. This protected seam is covered against the
locked SDK by offline tests using its real decorator, transaction and protobuf
encoding. Recheck the seam and retry policies when upgrading the SDK.

| Outcome | Meaning |
| --- | --- |
| `load` returns `None` | Document absent |
| Save returns `False` | Expected-revision/create precondition does not match; no candidate write |
| `InvalidStorageConfiguration` | Invalid explicit collection/client configuration |
| `InvalidCampaignState` | Invalid candidate, expected revision or rewritten history |
| `SnapshotTooLarge` | Candidate exceeds the documented byte cap; no truncation or write |
| `CorruptCampaignStorage` | Stored schema, snapshot, metadata or binding is invalid |
| `StorageUnavailable` | Read/start failure or confirmed rejected write, including exhausted aborts |
| `CommitOutcomeUnknown` | Commit may have happened; no success or confirmed failure is claimed |

Public errors use fixed messages and suppress provider exception chains in normal
tracebacks. The adapter logs no snapshots, copy, credentials or provider details.
Server responses must expose only allowlisted error categories; do not serialize
exception internals, traceback locals, stored receipts or arbitrary diagnostics.

After an unknown outcome, reload and reconcile the stored revision and command
receipt against the original command identity. A matching receipt can establish
that mutation's recorded result; it is still historical, not fresh readiness.
Never blindly repeat `execute_command` or a model call. In-flight commits, process
crashes and concurrent model attempts do not provide external exactly-once
execution. Even a confirmed rejected storage write may follow a model call that
already happened outside the transaction.

## Size and index prerequisites

The entire canonical snapshot envelope must fit in **900,000 UTF-8 bytes**.
This is below Firestore's 1,048,576-byte document and 1,048,487-byte field-value
limits, leaving over 140 KiB for the bounded metadata, field names and document
name overhead. Characters are not bytes: German and other multibyte text counts
by its UTF-8 encoding. Oversized stored snapshots also fail validation.
The adapter neither truncates nor splits history. This storage contract bounds
how much campaign history fits; changing that contract requires later work.

Before deployment, exempt **`snapshot_json` from all single-field indexes** in the
configured collection group and do not include it in composite indexes. This
large opaque field is never queried; indexed field values have a 1,500-byte limit.
Index configuration is a deployment prerequisite, not applied or verified by this
PR. No index, security-rule, existing-data or production deployment change is made.
Only direct deterministic document reads are used.

## Verification and remaining prerequisites

The dependency is justified by its official transaction and serialization support;
no Firebase Admin SDK, ORM or repository framework is added. The lockfile currently
selects `google-cloud-firestore` **2.32.0**. Local verification uses native ARM
**Python 3.13.14**. The previous Intel Python environment could not install current
`cryptography` wheels; a native Python 3.13 environment resolved that without
pinning an older cryptography release. On Apple Silicon, use a native interpreter
when creating the locked environment.

Routine tests use synthetic campaign state and an injected transactional RPC fake
beneath the real SDK transaction/decorator, with the existing socket guard. They
cover create/load/update, exact package and receipt round trips, command replay,
stale and simultaneous writers, corrupt data and bindings, immutable history,
multibyte boundaries, callback retries and sanitized/uncertain failures. A separate
credential-free process checks import safety. Run:

```sh
uv run --locked python -m pytest
```

On September 26, 2026, the complete suite passed: **429 tests in 4.03 seconds**
on Python 3.13.14, with a cleared environment, offline uv and the socket guard:

```sh
env -i PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin \
  UV_CACHE_DIR=/tmp/workshop-firestore-uv-cache UV_OFFLINE=1 \
  uv run --locked python -m pytest
```

An independent review identified commit-outcome classification after an aborted
attempt and sensitive serializer warnings on invalid candidates. Both were fixed;
targeted adapter/application verification passed **92 tests**. Lockfile consistency,
Python compilation and 58 local documentation links passed. The emulator example
compiles and rejects a missing local endpoint before client creation.

The Firestore emulator and production IAM, project/database settings, index
exemptions, throughput and live transactions have **not been verified**. No
production credentials, production data or live model calls were used.

## Optional local emulator smoke check

This is separate from routine pytest. With an installed Google Cloud CLI and its
Firestore emulator prerequisites, start a local emulator explicitly:

```sh
gcloud emulators firestore start --host-port=127.0.0.1:8080
```

In another terminal, run the following from the repository. It requires the exact
local endpoint before creating a client, uses anonymous credentials and a synthetic
project/collection, and has no production fallback. It writes one fresh synthetic
campaign to that local emulator. If the emulator is unavailable, stop and report
the storage error; do not remove the endpoint guard to make it connect elsewhere.

```sh
FIRESTORE_EMULATOR_HOST=127.0.0.1:8080 uv run --locked python - <<'PY'
import os
from datetime import UTC, datetime
from uuid import uuid4

if os.environ.get("FIRESTORE_EMULATOR_HOST") != "127.0.0.1:8080":
    raise SystemExit("This check requires the explicit local Firestore emulator")

from google.auth.credentials import AnonymousCredentials
from google.cloud.firestore_v1 import Client
from workshop_marketing_agent.application import StartRound, execute_command, new_campaign
from workshop_marketing_agent.firestore_repository import FirestoreCampaignRepository

client = Client(project="demo-workshop-marketing", database="(default)",
                credentials=AnonymousCredentials())
repository = FirestoreCampaignRepository(client, collection_name="pilotCampaignSmoke")
reference = "smoke-" + uuid4().hex
now = datetime.now(UTC)
state = new_campaign(campaign_reference=reference, workshop_reference="synthetic-workshop",
                     actor_reference="synthetic-teacher", now=now)
assert repository.compare_and_save(state, expected_revision=None)
assert repository.load(reference) == state
command = StartRound(campaign_reference=reference, workshop_reference="synthetic-workshop",
                     round_reference="round-1", command_id="start-1",
                     actor_reference="synthetic-teacher", now=now, expected_revision=0,
                     purpose="Synthetischer Emulator-Test", selected_channels=("google_business",))
assert execute_command(repository, command).status == "committed"
assert execute_command(repository, command).status == "replayed"
assert not repository.compare_and_save(state, expected_revision=None)
assert repository.load(reference).revision == 1
print("Local emulator create/load/update/replay/conflict check passed", now.isoformat())
client.close()
PY
```

Record the timestamp and actual outcome if this optional check is run. Emulator
behavior does not establish production index, limit or concurrency behavior.

## Official sources

Checked September 26, 2026, together with the installed official SDK source:

- [Python client](https://docs.cloud.google.com/python/docs/reference/firestore/latest/google.cloud.firestore_v1.client.Client)
  and [transaction reference](https://docs.cloud.google.com/python/docs/reference/firestore/latest/google.cloud.firestore_v1.transaction.Transaction).
- [Transaction guidance](https://firebase.google.com/docs/firestore/manage-data/transactions): reads before writes, atomicity and repeatable callbacks.
- [Firestore limits](https://firebase.google.com/docs/firestore/quotas): document, field, name and indexed-value bounds.
- [Index best practices](https://firebase.google.com/docs/firestore/best-practices): exemptions for large unqueried strings.
- [Local emulator guidance and limitations](https://docs.cloud.google.com/firestore/native/docs/emulator).
