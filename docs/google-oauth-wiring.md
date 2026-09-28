# Server-side Google OAuth for the pilot Callable

The current Callable uses a [temporary manual-only deployment](google-manual-handoff.md).
It binds only OpenAI and constructs no Google publisher. The implementation below
is retained for later controlled API activation; its tests opt in explicitly.
Google OAuth is **not** a prerequisite for this manual-only deployment. Do not
create placeholder credential versions. Automatic integration remains the goal.

## Server-owned parameters

The retained automatic integration previously bound **`PILOT_GOOGLE_OAUTH`** in
addition to OpenAI. Reactivation requires deliberately restoring that binding. Its
value must be exactly one JSON
object with these three fields. This example contains placeholders only:

```json
{
  "client_id": "123-REPLACE_WITH_CLIENT_ID.apps.googleusercontent.com",
  "client_secret": "REPLACE_WITH_CLIENT_SECRET",
  "refresh_token": "REPLACE_WITH_REFRESH_TOKEN"
}
```

Local validation limits are deliberate safety bounds, not claims about all OAuth
providers: JSON at most 16,384 UTF-8 bytes; `client_id` at most 256 characters;
`client_secret` and `refresh_token` each at most 4,096 characters. Values must be
nonempty printable ASCII without whitespace. Client IDs must match
`[0-9]+-[A-Za-z0-9_-]+.apps.googleusercontent.com` (literal dots). Duplicate,
missing/extra keys, malformed JSON, non-finite values and invalid types are rejected.
No error identifies which credential field failed. There are no credential files
or application environment files in this change.

For later API activation, **`PILOT_GOOGLE_LOCATION`** was a separate `StringParam`
(now removed from the deployed entrypoint). Its default was empty, which
disables publication before reservation while leaving review actions available.
Configure the verified canonical `accounts/{accountId}/locations/{locationId}`;
`accounts/REPLACE_ACCOUNT/locations/REPLACE_LOCATION` illustrates the shape only.
The existing service/transport rejects extra segments, URLs, traversal, queries,
fragments and other noncanonical input. Request/workshop data cannot override it.
Do not change the location while an existing campaign has publication history;
resolve target identity and pending outcomes before any operational reassignment.

The required scope is fixed to `https://www.googleapis.com/auth/business.manage`.
The deprecated `plus.business.manage` scope is not accepted as a substitute.
Consent and the original offline grant must include the required scope. Refresh
cannot add permissions: only the documented grant fields are sent, with no
client-controlled scope. If a token response includes `scope`, it must contain
the required scope; absence does not prove grant coverage. Live grant verification
remains an owner prerequisite, not something these offline tests establish.

## Lazy refresh and failures

Building the service constructs callables but evaluates neither Google nor OpenAI
secrets. Import/discovery evaluates no parameter values. Authorization, fresh
evidence and positively confirmed durable reservation precede Google secret access.
Denied, unauthenticated, unrelated, blocked, stale and replayed requests do not read
the Google secret or refresh a token. The existing Auth policy, App Check enforcement,
Firestore configuration, OpenAI laziness and pilot allowlists are preserved.

Each deliberate dispatch obtains one new access token through one form-encoded
POST to **`https://oauth2.googleapis.com/token`**, containing only `grant_type=refresh_token`,
`client_id`, `client_secret` and `refresh_token`. Standard-library HTTPS fixes the
destination and disables redirects and retries, avoiding an additional dependency
or an authentication client's implicit retry/refresh behavior. Token HTTP is injectable
for tests. The token timeout is **10 seconds**; Local Posts uses **15 seconds**.
These bound individual socket operations, not the full function's wall time; the
existing function timeout remains 300 seconds in `europe-west1`.

A token response is limited to 65,536 bytes and must be a valid JSON object without
duplicate keys/non-finite constants, HTTP 200, a nonempty Bearer access token (case-insensitive
token type), and valid optional scope. Access tokens are at most 8,192 printable ASCII
characters and must match Bearer-header syntax. Unexpected fields are discarded;
rotated refresh tokens returned in a response are not persisted automatically.

Invalid secret data prevents both refresh and Local Posts requests. OAuth rejection,
redirects, rate limiting, server failure, timeout, connection failure and malformed
responses all return the same private empty-token sentinel. No exception/body/credential
is exposed or retained by the provider. The existing Google adapter converts this to
`authentication_or_permission` before any Local Posts request, and orchestration
records **`failed / permission_denied`**. This intentionally coarse category means
that token acquisition failed, not that a particular credential was invalid. Teacher
responses use the existing fixed German failure message. There is no automatic retry.

Once a Local Posts write may have started, its existing conservative unknown-outcome
handling remains unchanged. Token refresh failures cannot have published a Local Post;
Local Posts timeouts cannot safely be described as failures. Credentials and token
responses are not logged, represented by provider/response objects, put in exceptions,
returned to teachers, or stored in campaign/Firestore snapshots. Access tokens are
transient return/header values only. Injected transports/getters must also avoid
logging sensitive data. There is no token cache, background refresh or token storage.

## Separate identities and operational preparation

Firebase Auth identifies the teacher and governs workshop access. Business Profile
OAuth identifies the Google account owning/managing the profile. These are separate
authorizations; a Firebase login grants no Business Profile permission. A future
operator must verify the exact account/location and its manager grant before deployment.

For rotation, replace the complete credential set as a new Secret Manager version
and explicitly redeploy the bound function when authorized; do not mix client/token
pairs or print values. Revoke old Google grants when required, then obtain replacement
offline consent separately. Revocation can invalidate other tokens/scopes for the same
project; plan its impact. Do not automatically obtain, revoke or rotate anything here.
Failed refreshes need operator investigation, while pending/unknown publication attempts
still require reconciliation before another send, even after credentials are repaired.

Owner checklist — **not executed**:

- [ ] **Unverified:** Confirm Business Profile API approval for the Google Cloud project and enable required APIs.
- [ ] **Unverified:** Configure the OAuth consent screen and an appropriate OAuth client.
- [ ] **Unverified:** Obtain offline access and a refresh token from the account owning or managing the profile.
- [ ] **Unverified:** Store the credential set in Firebase Secret Manager.
- [ ] **Unverified:** Confirm and configure the exact account/location resource.
- [ ] **Unverified:** Verify the function's IAM access to the secret.
- [ ] **Unverified:** Deploy only after explicit authorization.
- [ ] **Unverified:** Complete a separate controlled preflight before the first real publication.

Explicit [reconciliation](google-publication-reconciliation.md) now reuses this lazy OAuth
boundary for authorized eligible historical reads. Manual resolution of remaining
unknown outcomes, teacher UI, deployment, App Check verification and live pilot verification
remain separate work. Offline tests prove neither API access nor successful production
publication. The staging script includes `google_oauth.py`; it still only builds local
artifacts and never creates secrets or deploys.

Official sources checked 2026-09-27:
[Business Profile prerequisites](https://developers.google.com/my-business/content/prereqs),
[Business Profile OAuth](https://developers.google.com/my-business/content/implement-oauth),
[offline access and token refresh/revocation](https://developers.google.com/identity/protocols/oauth2/web-server),
and [Firebase secret parameters and rotation](https://firebase.google.com/docs/functions/config-env).

## Historical automatic-integration verification

`test_google_oauth.py` uses synthetic markers and fake HTTP for strict parsing,
request construction, scope/type/token validation, timeout limits, no retry/redirect,
cleanup and sanitized failures. `test_callable_google_oauth.py` runs the real Callable
protocol and service factory with fake verified Firebase contexts, Auth/Firestore,
secret getters and both HTTP boundaries. It checks reservation-before-secret ordering,
recorded success/token failure, blocked/replayed/conflicting calls, and continued
unknown handling after Local Posts timeout. Discovery tests block parameter evaluation,
credential discovery and networking, and assert both named secret bindings.

Verification on 2026-09-27 (macOS 15.6.1, arm64; uv 0.11.31;
pytest 9.1.1 on both interpreters), after the independent review:

| Interpreter | Full locked suite |
| --- | --- |
| Python 3.11.15 | 962 passed in 35.18s |
| Python 3.13.14 | 962 passed in 34.01s |

Exact commands used separate existing temporary environments and preserved the
project `.venv`. The offline cache was already populated:

```sh
env -i PATH=/usr/local/bin:/usr/bin:/bin UV_CACHE_DIR=/tmp/workshop-firestore-uv-cache UV_OFFLINE=1 UV_PROJECT_ENVIRONMENT=/tmp/workshop-callable-test311 /usr/local/bin/uv run --locked --python /tmp/workshop-python311-interpreters/cpython-3.11.15-macos-aarch64-none/bin/python3.11 python -m pytest
env -i PATH=/usr/local/bin:/usr/bin:/bin UV_CACHE_DIR=/tmp/workshop-firestore-uv-cache UV_OFFLINE=1 UV_PROJECT_ENVIRONMENT=/tmp/workshop-callable-test313 /usr/local/bin/uv run --locked --python /Users/my/.local/share/uv/python/cpython-3.13.14-macos-aarch64-none/bin/python3.13 python -m pytest
```

The independent reviewer found no actionable issues and separately ran the three
OAuth/Callable test files: **194 passed in 5.48s** on Python 3.11.15. No review
corrections were required. Local Markdown targets and placeholder JSON were checked.

Local Firebase staging also succeeded with the locked Python 3.11 environment:

```sh
UV_CACHE_DIR=/tmp/workshop-firestore-uv-cache UV_OFFLINE=1 UV_PROJECT_ENVIRONMENT=/tmp/workshop-callable-test311 uv run --locked --python /tmp/workshop-python311-interpreters/cpython-3.11.15-macos-aarch64-none/bin/python3.11 python scripts/prepare_firebase.py --output /tmp/workshop-google-oauth-stage --python /tmp/workshop-python311-interpreters/cpython-3.11.15-macos-aarch64-none/bin/python3.11
```

Discovery from the staged functions directory using the existing locked Python
3.11 environment confirmed one Gen 2 Callable, `europe-west1`, 300 seconds, and
both secret bindings while parameter evaluation, credential discovery and socket
networking were blocked. This was a local staging/discovery check, not a deployed
runtime or a new isolated wheel-installation check. No live smoke check was performed.
