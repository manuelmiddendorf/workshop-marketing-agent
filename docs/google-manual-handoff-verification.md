# Manual Google handoff verification

Verified locally on 2026-09-28, macOS 15.6.1 arm64, uv 0.11.31, pytest 9.1.1.
No deployment, cloud mutation, secret access, live generation or publication was
performed. Public documentation navigation is not a provider/API acceptance check.

Focused checks cover exact stored projection, strict fields, access and App Check,
wrong/unselected references, disabled channels, source/description/image changes,
concurrent campaign changes, text-only approval, unknown publication blocking,
unchanged history, fake OpenAI generation/revision and failing Google boundary spies.
Existing Rausgegangen, OAuth, transport, publication and reconciliation tests remain.
The retained API Callable tests explicitly inject the inactive publisher; current
manual-only tests and discovery exercise the actual deployed default.

## Complete suites

Final complete runs after corrections:

| Runtime | Result |
| --- | --- |
| Python 3.11.15 | 1197 passed in 86.86 seconds |
| Python 3.13.14 | 1197 passed in 82.83 seconds |

Environments are separate and
preserve the project `.venv`; the already populated dependency cache is offline.

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

The initial complete runs found two obsolete expectations for API calls when no
publisher is supplied (1195 passed, 2 failed on each runtime). The tests now require
the deliberate manual-only denial and assert historical state remains unchanged.
An earlier focused test exposed a source-description change not covered by the
existing fact-sheet comparison; the handoff now compares the stored original
public description too, without changing shared preparation/business schemas.

## Staged wheel and Callable discovery

```sh
env -i PATH=/usr/local/bin:/usr/bin:/bin \
  UV_CACHE_DIR=/tmp/workshop-firestore-uv-cache UV_OFFLINE=1 \
  /tmp/workshop-callable-test311/bin/python scripts/prepare_firebase.py \
  --python /tmp/workshop-python311-interpreters/cpython-3.11.15-macos-aarch64-none/bin/python3.11 \
  --output /tmp/workshop-manual-google-stage
uv venv /tmp/workshop-manual-google-wheel311 --python "$PYTHON311"
# From /tmp/workshop-manual-google-stage/functions, with the same offline cache:
uv pip install --python /tmp/workshop-manual-google-wheel311/bin/python \
  --require-hashes -r requirements.txt
```

Build and fresh hashed installation passed (65 distributions, all versions checked
against `uv.lock`). An isolated
`env -i .../bin/python -I` process outside the checkout blocked sockets, credential
discovery, `SecretParam.value` and `StringParam.value`, then called the SDK's
`get_functions()` and `functions_as_yaml()`. It found exactly `pilot_teacher_service`,
Gen 2, `europe-west1`, and **only `PILOT_OPENAI_API_KEY`** in secret bindings. It
asserted no Google secret in the manifest and package import from the installed
wheel's environment. Result: passed on Python 3.11.15. No deployment readiness or
actual runtime identity/enforcement is inferred from this local discovery check.

Wheel SHA-256:
`c8039fcc6f7057d6ec8982b4016595fc31165b55a655bb6ed027b1e191e142e0`.

## Local teacher-app companion

From the separate `msn` repository:

```sh
/Users/my/.config/nvm/versions/node/v20.20.1/bin/node --test \
  test/pilotRelease.test.cjs test/marketingApplication.test.cjs \
  test/marketingUi.test.cjs test/marketingClient.test.cjs \
  test/marketingWorkflow.test.cjs test/marketingCallableHost.test.cjs \
  test/marketingGoogleHandoff.test.cjs
/usr/local/bin/node scripts/preparePilotFrontend.cjs --mode disabled \
  --output .pilot-release/manual-google-final-2026-09-28
```

Frontend tests: **165 passed**, Node 20.20.1, 1.358 seconds. Tests compile/render the
Vue template, preserve plain text, exercise explicit copy/open handlers, block
unsafe image links and automatic controls/replay, and verify no journal/ID/state
mutation from handoff and no late response after signout. The final production build passed (disabled artifact, no upload); its only warning
was the existing outdated Browserslist data. It uses existing Node 12.14.0/npm 7.10.0 and ignored build tooling/sibling dependencies;
no clean-checkout reproducibility or browser/live-portal verification is claimed.
