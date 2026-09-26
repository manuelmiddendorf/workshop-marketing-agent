# Google Business Local Posts transport

This synchronous adapter is offline-tested transport only. It does not call campaign
commands, record publication results, authorize a teacher, or run through Firebase.
The caller must enforce exact-version approval and reserve a publication attempt
before a future integration dispatches anything. A returned state is provider-reported
evidence, not independent verification or permission to publish.

## Configuration and requests

`GoogleLocalPosts` requires an exact server-owned location, access-token callable,
HTTP callable and explicit finite socket timeout (greater than zero, at most 60
seconds). The timeout bounds individual socket operations, not total elapsed time.
No import discovers credentials, refreshes tokens, or opens a connection.

```python
from workshop_marketing_agent.google_local_posts import GoogleLocalPosts, https_transport

# Server wiring supplies get_access_token and an already approved payload.
posts = GoogleLocalPosts(
    location="accounts/123/locations/456",  # synthetic; never teacher-selected
    token_provider=get_access_token,
    transport=https_transport,
    timeout=10,
)
result = posts.create_event(approved_payload)
# Inspect result.outcome and result.state; never blindly repeat ambiguous_write.
```

Use OAuth scope `https://www.googleapis.com/auth/business.manage`.
`create_event` and `update_event` accept only the existing `GoogleSubmissionPayload`,
revalidated to reject unchecked malformed Pydantic copies. They preserve its provider
payload, including German copy, dates, booking link and media URL. This boundary does
not repeat approval or workshop business checks and cannot establish provider acceptance.

| Method | Fixed origin `https://mybusiness.googleapis.com` plus path |
| --- | --- |
| `create_event(payload)` | `POST /v4/{location}/localPosts` |
| `update_event(post_name, payload)` | `PATCH /v4/{post_name}?updateMask=...` |
| `get_post(post_name)` | `GET /v4/{post_name}` |

Resource components accept ASCII letters, digits, underscores and hyphens. This is a
conservative local syntax restriction, not verification of an actual Google account.
Complete names must match `accounts/{id}/locations/{id}` and, for posts, its
`/localPosts/{id}` child. URLs, traversal, query strings, fragments, extra segments
and other locations are rejected before obtaining a token. Update/get responses
must also identify exactly the requested post.

PATCH masks contain the alphabetically sorted top-level fields actually sent:
`callToAction,event,languageCode,media,summary,topicType`. No output-only fields are
sent. **Updates without nonempty media are rejected locally**: retaining or removing
an old image implicitly could violate the approved snapshot. Supporting text-only
updates requires a separate explicit, verified clearing policy. Text-only creates
remain supported; absent media is omitted and an explicitly empty tuple stays an
empty array, exactly as the existing payload specifies.

Each operation obtains one token and makes at most one HTTP request (invalid local
input makes none). The supplied `https_transport` uses the standard library, fixes
the host, follows no redirects, performs no automatic retries, limits response bodies
to 1 MiB and closes the connection. Custom transports must honor the same single-request,
no-redirect, no-retry and no-sensitive-logging contract. Never use a retrying wrapper.
There is no update-to-create fallback.

## Results and failure policy

Results contain only operation, outcome, validated resource name, normalized state,
and optional validated HTTPS `searchUrl`. Search queries are allowed; credentials,
fragments, malformed URLs, non-global IP addresses and local/internal host suffixes
are rejected without DNS lookups. Extra response fields and raw Google
errors are discarded. No exception, body, token, headers, or transport details are
attached to a result. Request credentials and bodies are transient transport inputs;
custom boundary implementations must not log or persist them.

| Provider state | Outcome |
| --- | --- |
| `LIVE`, `RECURRING` | `visible` (provider-reported) |
| `PROCESSING`, `SCHEDULED` | `pending` (not confirmed visible) |
| `REJECTED` | `rejected` (not published) |
| Missing, malformed, unspecified or unknown | `unresolved`, state `None` |

HTTP success alone is not publication. Invalid JSON, resource names or public URLs
invalidate a success response: writes return `ambiguous_write`; reads return
`unavailable`. Unknown state alone retains the validated name/URL as unresolved.

| Condition | Safe outcome |
| --- | --- |
| Local configuration, resource or payload invalid; media absent on update | `invalid_input`, no dispatch |
| Token provider fails or returns an invalid Bearer token | `authentication_or_permission`, no dispatch |
| HTTP 401/403 | `authentication_or_permission` |
| HTTP 400/404/422 | `provider_rejection` (explicit request rejection) |
| Redirect, 429, other unexpected HTTP status, 5xx, timeout, interrupted connection or malformed response | Writes: `ambiguous_write`; reads: `unavailable` |

No result authorizes an automatic retry. Rate limiting and server unavailability do
not prove that a write did not happen. Transport exceptions carry no trusted
pre-dispatch evidence, so even connection errors are conservative for writes.
Only local validation and token acquisition failures are known to precede dispatch.
A provider rejection is distinct from a returned post's `REJECTED` moderation state.

## Official contract and remaining work

Sources checked on 2026-09-27:
[resource and states](https://developers.google.com/my-business/reference/rest/v4/accounts.locations.localPosts),
[create](https://developers.google.com/my-business/reference/rest/v4/accounts.locations.localPosts/create),
[patch](https://developers.google.com/my-business/reference/rest/v4/accounts.locations.localPosts/patch),
[get](https://developers.google.com/my-business/reference/rest/v4/accounts.locations.localPosts/get),
[posts guide](https://developers.google.com/my-business/content/posts-data),
[OAuth](https://developers.google.com/my-business/content/implement-oauth),
[access prerequisites](https://developers.google.com/my-business/content/prereqs), and
[basic setup / no sandbox](https://developers.google.com/my-business/content/basic-setup).

Google API approval and OAuth remain unverified prerequisites. There is no general
Business Profile sandbox. This adapter does not assume that `validateOnly` exists
on Local Posts methods. No live Google request was made for this implementation.
Offline tests do not verify API access, account eligibility, location identity,
provider payload acceptance, image reachability, or production publication.

Orchestration with durable [publication reservations](pilot-campaign-state.md#publication-target-and-immutable-binding),
Firebase secrets, reconciliation, Callable actions, UI and deployment remain later
work. No OAuth storage/refresh, discovery, retries, deletion or insights are added.

## Offline verification

Focused tests use fake token/HTTP boundaries and the suite's socket guard. They
exercise requests, media/masks, all states, rejected inputs, safe failures and
allowlisted outputs. An independent focused code review found no actionable issues.
After review, the complete suite passed on 2026-09-27 on macOS 15.6.1 / arm64:

| Interpreter | pytest | Full locked result |
| --- | --- | --- |
| Python 3.11.15 | 9.1.1 | 782 passed in 20.12s |
| Python 3.13.14 | 9.1.1 | 782 passed in 19.80s |

Both used uv 0.11.31 (Homebrew 2026-07-22), separate existing temporary environments,
an empty inherited environment, offline dependency resolution and the suite's socket
guard. The project `.venv`, dependencies and lockfile were unchanged. The total
includes 137 new adapter cases. No prompt, copy-generation or business rule changed.

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
