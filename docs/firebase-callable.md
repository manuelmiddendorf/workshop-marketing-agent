# Pilot Firebase Callable adapter

`functions/main.py` exposes **`pilot_teacher_service`** through the official
second-generation Python Callable SDK. `functions/pilot_adapter.py` supplies the
trusted wiring and teacher policy. The installable business package and its
[service contract](pilot-teacher-service.md) are unchanged. This boundary is
implemented and tested locally; it has **not been deployed**.

## Request and response

Send the existing teacher intent as Callable request data, for example:

```json
{
  "action": "get_campaign",
  "workshop_reference": "malws-copy",
  "campaign_reference": "campaign-1"
}
```

The Firebase client SDK supplies authentication and App Check tokens separately.
The adapter builds `VerifiedPrincipal` only from the SDK-verified `request.auth.uid`
and forwards `request.data` to `PilotService`. It never accepts a raw token from
the body or manually reads authorization headers. The strict service schema
rejects extra identity, role, actor, token, endpoint, collection, model and
permission fields. See the service document for all actions and intent fields.

The Callable SDK wraps the unchanged sanitized service dictionary in its protocol
`result`; Firebase clients expose that value as response data. Statuses/messages,
historical markers, diagnostics, partial completion and unknown commit outcomes
are preserved. A missing Auth context returns `unauthenticated`; invalid Auth or
missing/invalid App Check tokens can instead be rejected by the SDK before our
handler. Unexpected adapter exceptions return the fixed `internal_error` response
without exception text or logging. The adapter does not log request data or
Auth, Firestore, source or model responses.

No client or wrapper may automatically repeat mutations/model operations after
an uncertain response. Reconcile stored receipts and inspect partial results
first. There is no durable model execution reservation or exactly-once guarantee.

## Teacher access policy

Every valid service request, including a committed replay, checks authorization
before campaign reads/writes, feed requests or model execution:

1. Only `malws-copy` is enabled. The verified token must contain an email and a
   Firebase claims object with **no tenant key**, even a null/empty tenant value.
2. Fetch the current root Auth account by UID and by normalized email. Both must
   identify that same UID/email, be explicitly enabled and have no tenant. Missing
   records, conflicting lookups, malformed values and lookup failures deny access.
   This detects inconsistent UID/email resolution; it does not enumerate or audit
   all accounts or make a uniqueness claim beyond the Auth lookup API.
3. Normalize email using `strip().lower()`. Require one `@`, nonempty sides, no
   whitespace/control characters or `/`, and at most 254 UTF-8 bytes. Do not
   remove dots, plus suffixes, aliases or rewrite Unicode. Token and both account
   emails must agree after this normalization. A verified token is required;
   email verification flags are not an additional teacher-role requirement.
4. Check the token's finite, nonnegative `auth_time` against each current account's
   `tokens_valid_after_timestamp` (seconds versus milliseconds). Revoked/stale
   sign-ins fail closed; ordinary Callable token verification alone does not
   establish this. Missing or malformed timestamps deny access.
5. Read exactly `Users/{normalized-email}` in the configured database, with a
   ten-second timeout and no read retry. Require a dictionary with `type` exactly
   `MSN` or `SBN`. If a `uid` key exists, its value must equal the authenticated
   UID; null and empty values are not treated as absence. No role cache is used.

Roles apply to the single allowlisted pilot; this does not introduce per-workshop
ownership or extend existing permissions to other workshops. No client claims
such as `admin`, `role` or `type` grant access. Auth/Firestore failures deny rather
than falling back to token claims, development collections or cached roles.

Read-only reference inspection on September 26, 2026 was limited to the sibling
Firebase repository's `functions/createPasswordEmailRequest.js:isTeacherByEmail`
and `functions/members/memberAccountLinking.js:resolveMemberCaller/authority`.
The former uses `Users/{email}` and `MSN`/`SBN`, but allows falsey stored UIDs and
does not itself refresh the Auth account. The latter refreshes identity and
checks revocation for Callable context, but also accepts supplied/header tokens
and treats null/empty role UIDs as absent. This adapter deliberately has one
Callable-only identity source, consistent normalization, strict present-UID
matching, rejection of any tenant claim, and agreement between UID/email Auth
lookups. It neither changes those applications nor repeats the booking audit.

The [manual Google handoff](google-manual-handoff.md) needs no Google OAuth binding
or publisher. The [OAuth implementation](google-oauth-wiring.md) remains inactive
for later controlled reactivation.

The [assisted Rausgegangen actions](rausgegangen-teacher-workflow.md) use the existing
Callable forwarding and teacher policy, with no Google-secret access or new configuration.

## Explicit configuration and initialization

| Setting | Value or source |
| --- | --- |
| Runtime | `python311`; Python 3.13 remains preferred for development |
| Callable / codebase | `pilot_teacher_service` / `pilot-marketing` |
| Region / function timeout | `europe-west1` / 300 seconds |
| Project / database | `middendorf-yoga` / `(default)` |
| Campaign collection | `pilotMarketingCampaigns` (no production contents/configuration verified) |
| Role collection | `Users` |
| Feed / model timeout | 10 / 60 seconds |
| Retained inactive API timeouts | 10 / 15 seconds |
| Google secret / publisher | None in the manual-only entrypoint |
| Admin HTTP / role-read timeout | 10 / 10 seconds |
| Workshops | `malws-copy` only |
| Channels | `google_business`, `rausgegangen` only |
| Model | Required non-secret `PILOT_MODEL` string parameter, **no fallback** |
| OpenAI key | Secret Manager parameter `PILOT_OPENAI_API_KEY`, bound only to this function |
| App Check | Enforcement enabled; client registration/attestation remains an operational prerequisite |

The trusted feed endpoint is fixed in code:

```text
https://europe-west1-middendorf-yoga.cloudfunctions.net/WSGetPublicWorkshops?format=workshop-data.v1&id=malws-copy
```

Clients cannot override any configuration above. The existing `_http_get` keeps
its 1 MiB response bound and disables redirects and retries. `OpenAIDraftClient`
retains `max_retries=0`; each generation uses the explicit model timeout. Server
timestamps use `datetime.now(UTC)`. Existing bounded Firestore transaction retries
contain only storage work, never model calls. The 300-second function limit is
an explicit operational ceiling, not a proven end-to-end latency budget: Admin
and Firestore SDK retry policies and concurrent/in-flight operations still matter.

The default Admin app is initialized with explicit project/HTTP options before
Callable token verification. A lock plus `get_app()` permits repeated discovery
and invocations; a differently configured default app is rejected. Constructing
the Admin SDK's lazy credential holder does not resolve credentials or contact
services. Discovery reads neither model nor secret parameter values. Firestore
clients are constructed during requests; the OpenAI key and client are resolved
only when the authorized service actually invokes the model.

The model must be selected and configured by the operator before deployment;
no live model evaluation is implied. Set the key through Firebase/Secret Manager
secret management, never an ordinary environment file, frontend or repository.
No secret values or environment files are provided here.

## Reproducible local checks and deployment source layout

`firebase-admin` and `firebase-functions` are in the locked `firebase` dependency
group, included by `dev` for adapter tests. They are not dependencies of the
business-package wheel. Their official transitive HTTP/framework dependencies
are needed by the SDK; no separate application framework was selected. Existing
dependency versions remain unchanged. The lock selects Admin 7.7.0 and Functions
0.6.0. Python 3.11 and 3.13 tests include this group automatically.

From the repository root, set explicit native interpreter paths and create
separate environments, preserving the project's `.venv`:

```sh
py311=/absolute/path/to/python3.11
py313=/absolute/path/to/python3.13
work=$(mktemp -d /tmp/workshop-callable-XXXXXX)
export UV_CACHE_DIR=/tmp/workshop-firestore-uv-cache
UV_PROJECT_ENVIRONMENT="$work/test311" uv sync --locked --python "$py311"
UV_PROJECT_ENVIRONMENT="$work/test313" uv sync --locked --python "$py313"
env -i PATH="$PATH" UV_CACHE_DIR="$UV_CACHE_DIR" UV_OFFLINE=1 \
  UV_PROJECT_ENVIRONMENT="$work/test311" \
  uv run --locked --python "$py311" python -m pytest
env -i PATH="$PATH" UV_CACHE_DIR="$UV_CACHE_DIR" UV_OFFLINE=1 \
  UV_PROJECT_ENVIRONMENT="$work/test313" \
  uv run --locked --python "$py313" python -m pytest
```

Environment setup may download public packages. Tests use synthetic Auth,
Firestore, feed and model boundaries and block socket access. The real SDK
Callable decoder is exercised with fake verified tokens; this does not test
live token issuance, IAM or App Check attestation. A separate process blocks
credential discovery, parameter reads and networking while importing and running
the SDK's function-discovery/manifest code.

Stage a self-contained source directory with the business wheel and hash-pinned
runtime dependencies exported from the same lockfile (no editable or parent-path
dependency, no tests/dev dependencies):

```sh
UV_PROJECT_ENVIRONMENT="$work/test311" uv run --locked --python "$py311" \
  python scripts/prepare_firebase.py --python "$py311" --output "$work/stage"
uv venv "$work/layout311" --python "$py311"
cd "$work/stage/functions"
uv pip install --python "$work/layout311/bin/python" --require-hashes -r requirements.txt
uv pip check --python "$work/layout311/bin/python"
env -i "$work/layout311/bin/python" -I - <<'PY'
import socket
def blocked(*args, **kwargs):
    raise AssertionError("Forbidden discovery side effect")
socket.create_connection = socket.getaddrinfo = socket.socket.connect = blocked
import google.auth
google.auth.default = blocked
from firebase_admin.credentials import ApplicationDefault
ApplicationDefault.get_credential = blocked
from firebase_functions.params import SecretParam, StringParam
SecretParam.value = StringParam.value = property(blocked)
from firebase_functions.private.serving import get_functions, functions_as_yaml
functions = get_functions()
assert list(functions) == ["pilot_teacher_service"]
assert "gcfv2" in functions_as_yaml(functions)
import sys
from pathlib import Path
import workshop_marketing_agent
assert Path(workshop_marketing_agent.__file__).is_relative_to(Path(sys.prefix))
print("Isolated wheel import and Callable discovery passed")
PY
```

The generated tree contains `firebase.json`, `functions/main.py`,
`functions/pilot_adapter.py`, `functions/google_oauth.py`, `functions/requirements.txt` and
`functions/vendor/workshop_marketing_agent-0.1.0-py3-none-any.whl`. The staging
command requires Python 3.11 and an empty output directory, builds through
Hatchling, exports locked dependencies with hashes, and adds the wheel's hash.
It does not run Firebase CLI, deploy, create secrets or access production. Staging
under `/tmp` or the ignored root `build/` keeps artifacts out of Git. The chosen
project/database are explicit deployment targets, not verified production access.

## Remaining operational work

- Authorize and perform deployment separately; verify Python 3.11 on the actual
  Linux Firebase runtime and Firebase CLI/cloud-build packaging.
- Configure model and Secret Manager value/access, service-account IAM for Auth,
  the intended Firestore database/collections and the model secret. Server Admin
  access bypasses Firestore rules; protect teacher role writes independently.
- Verify campaign collection/index exemptions and limits described in the
  [storage document](firestore-campaign-storage.md), and production role records.
- Register the teacher frontend with App Check, obtain valid attestation and
  test enforcement. Integrate the frontend in `europe-west1` with explicit
  reconciliation and no automatic retry of uncertain mutations.
- Measure total latency, costs and concurrent calls; choose operational limits
  and monitoring before rollout. No publishing, UI or booking behavior is added.

## Verification record

Verified September 26, 2026 on macOS 15.6.1 arm64, with uv 0.11.31 and pytest
9.1.1, in separate environments with cleared process environments and offline uv:

| Interpreter | Complete locked suite after independent review |
| --- | --- |
| Python 3.11.15 | 579 passed in 6.45 seconds |
| Python 3.13.14 | 579 passed in 9.79 seconds |

The independent review reported no actionable findings and independently ran
the 80 adapter tests: 80 passed in 1.44 seconds on Python 3.11. The final suites
include these tests, including real SDK protocol handling with synthetic verified
tokens and App Check rejection. No corrections were required after review.

The exact commands were the two `env -i ... uv run --locked --python ... python
-m pytest` invocations above, with `UV_PROJECT_ENVIRONMENT` set separately to
`/tmp/workshop-callable-test311` and `/tmp/workshop-callable-test313`, and:

```sh
py311=/tmp/workshop-python311-interpreters/cpython-3.11.15-macos-aarch64-none/bin/python3.11
py313=/Users/my/.local/share/uv/python/cpython-3.13.14-macos-aarch64-none/bin/python3.13
# PATH for the final cleared test environments:
PATH=/usr/local/bin:/usr/bin:/bin
```

The staged Python 3.11 layout installed 65 runtime distributions, all matching
the applicable locked versions. Isolated wheel import and SDK discovery/manifest
checks passed outside the checkout with credentials, parameter values and network
access blocked. The generated entrypoint is `gcfv2`, in `europe-west1`, with the
300-second timeout and only the named model secret. The final wheel SHA-256 is
`9b41a22aaec7a97edefa54bdc2bd808f475ec52d0101ecd251f04b3ca841c4aa`.
All pre-existing locked dependency versions are unchanged; the existing project
`.venv` was not synchronized. Local links and `git diff --check` passed. Initial
package installation used public registry downloads; no production services,
writes, credentials or deployment were used in verification.

Official sources checked September 26, 2026:
[Callable Functions](https://firebase.google.com/docs/functions/callable),
[configuration and secret parameters](https://firebase.google.com/docs/functions/config-env).
SDK-specific behavior was also checked against the installed locked Python SDKs.
