# Python runtime compatibility

Python **3.13 remains preferred for local development and the portfolio**;
`.python-version` still selects `3.13`. Package metadata permits `>=3.11`.
The tested interpreter matrix is CPython **3.11.15** and **3.13.14** on
**macOS 15.6.1 arm64**, not every interpreter/platform admitted by that metadata.
Python 3.11 is the Firebase-target interpreter tested here.

The [official Firebase Python runtime documentation](https://firebase.google.com/docs/functions/manage-functions#set_python_version),
checked **September 26, 2026**, documents `python310` and `python311`.
This package does not support Python below 3.11. Package compatibility does not
establish successful Firebase deployment. Callable wiring, teacher access-rule
mapping, deployment configuration and the production runtime remain unimplemented
or unverified. No Firebase libraries or deployment files are added.

## Compatibility audit

All 15 executable package modules were read, and each parses using Python 3.11's
grammar and compiles under that interpreter. No syntax or standard-library change
was necessary. In particular, `datetime.UTC`, offset-aware `fromisoformat` with
`Z`, `zoneinfo`, dataclass slots and strict `zip` are available in Python 3.11.
Original Decimal handling, canonical JSON, fingerprints, public models, schema
versions and serialized fixtures are unchanged. Existing tests cover these risks;
no duplicate compatibility tests were introduced.

All existing locked versions are retained, including Pydantic **2.13.5**, OpenAI
**2.54.0**, google-cloud-firestore **2.32.0** and pytest **9.1.1**. Their installed
`Requires-Python` metadata admits 3.11, as does the installed transitive dependency
set. The refreshed lock adds compatible artifact records for the wider range;
it neither adds a package nor upgrades one. The Windows-only colorama entry is
not installed/tested on this Mac. Hatchling remains the build backend; its isolated
build requirements are resolved separately from `uv.lock`.

## Reproduce isolated locked tests

Use zsh/bash from the repository root. Replace the two absolute interpreter paths
with installed **native** interpreters for your platform. Native arm64 interpreters
were used on Apple Silicon; the installed Intel Python is not this test matrix.
`uv python install cpython-3.11-macos-aarch64-none --no-bin` can install the native
3.11 interpreter on that platform. Initial interpreter/package/build installation
may use the network; all tests below run offline with a cleared environment.

```sh
repo_dir="$PWD"
check_dir="$(mktemp -d)"
py311="/absolute/path/to/python3.11"
py313="/absolute/path/to/python3.13"
uv_bin="$(command -v uv)"
uv_cache="$check_dir/cache"

UV_CACHE_DIR="$uv_cache" UV_PROJECT_ENVIRONMENT="$check_dir/test311" \
  "$uv_bin" sync --locked --python "$py311"
UV_CACHE_DIR="$uv_cache" UV_PROJECT_ENVIRONMENT="$check_dir/test313" \
  "$uv_bin" sync --locked --python "$py313"

shasum -a 256 uv.lock > "$check_dir/lock-before.sha256"
for runtime in 311 313; do
  if [ "$runtime" = 311 ]; then interpreter="$py311"; else interpreter="$py313"; fi
  env -i PATH=/usr/bin:/bin UV_CACHE_DIR="$uv_cache" UV_OFFLINE=1 \
    UV_PROJECT_ENVIRONMENT="$check_dir/test$runtime" \
    "$uv_bin" run --locked --python "$interpreter" python -m pytest
  env -i "$check_dir/test$runtime/bin/python" -I -c \
    'import platform, sys, pytest; print(sys.version); print(platform.platform()); print(pytest.__version__)'
done
shasum -a 256 -c "$check_dir/lock-before.sha256"
"$uv_bin" --version
```

These are two separate fresh environments. Both explicitly select their interpreter
instead of relying on `.python-version`; neither syncs or replaces the project's
existing `.venv`. The test suite's autouse socket guard prevents accidental network
access. The final complete suite is run once per runtime after independent review.

## Build one wheel; check that exact artifact twice

Continue with the same shell variables. Export the lock including the existing
dev group solely to run selected existing tests against the wheel. The project
itself is excluded: no editable installation enters either wheel environment.
The export's environment markers retain platform-specific dependency choices.

```sh
UV_CACHE_DIR="$uv_cache" "$uv_bin" build --wheel --python "$py311" \
  --out-dir "$check_dir/dist"
wheel="$check_dir/dist/workshop_marketing_agent-0.1.0-py3-none-any.whl"
shasum -a 256 "$wheel"
UV_CACHE_DIR="$uv_cache" "$uv_bin" export --locked --no-emit-project \
  --output-file "$check_dir/requirements.txt"

for runtime in 311 313; do
  if [ "$runtime" = 311 ]; then interpreter="$py311"; else interpreter="$py313"; fi
  UV_CACHE_DIR="$uv_cache" "$uv_bin" venv --python "$interpreter" "$check_dir/wheel$runtime"
  UV_CACHE_DIR="$uv_cache" "$uv_bin" pip install \
    --python "$check_dir/wheel$runtime/bin/python" --require-hashes \
    -r "$check_dir/requirements.txt"
  UV_CACHE_DIR="$uv_cache" "$uv_bin" pip install \
    --python "$check_dir/wheel$runtime/bin/python" --no-index --no-deps "$wheel"
  UV_CACHE_DIR="$uv_cache" "$uv_bin" pip check --python "$check_dir/wheel$runtime/bin/python"
done

mkdir "$check_dir/outside"
cp uv.lock "$check_dir/outside/uv.lock"
cp -R tests examples evaluations "$check_dir/outside/"
```

The copied files contain existing synthetic tests/data, **no package source**.
Write this import check outside the checkout. It blocks network and credential
lookup, verifies every package module is loaded from the wheel environment, and
checks installed package versions against the same lock. It does not create a
client or use credentials. Normal reviewable application calls are not executed.

```sh
cat > "$check_dir/outside/check_imports.py" <<'PY'
import importlib
import importlib.metadata as metadata
import json
import pathlib
import pkgutil
import re
import socket
import sys
import tomllib


def forbidden(*args, **kwargs):
    raise AssertionError("Import attempted network, credential discovery or client creation")


socket.socket.connect = forbidden
socket.create_connection = forbidden
socket.getaddrinfo = forbidden
import google.auth
import google.auth._default
google.auth.default = forbidden
google.auth._default.default = forbidden
import pydantic
import openai
from google.cloud import firestore
openai.OpenAI.__init__ = forbidden
firestore.Client.__init__ = forbidden
import workshop_marketing_agent
import workshop_marketing_agent.firestore_repository

prefix = pathlib.Path(sys.prefix).resolve()
for module in pkgutil.walk_packages(workshop_marketing_agent.__path__, workshop_marketing_agent.__name__ + "."):
    imported = importlib.import_module(module.name)
    assert pathlib.Path(imported.__file__).resolve().is_relative_to(prefix), imported.__file__
assert pathlib.Path(workshop_marketing_agent.__file__).resolve().is_relative_to(prefix)
project = metadata.distribution("workshop-marketing-agent")
assert project.metadata["Requires-Python"] == ">=3.11"
assert not json.loads(project.read_text("direct_url.json")).get("dir_info", {}).get("editable", False)
normalize = lambda name: re.sub(r"[-_.]+", "-", name).lower()
lock = tomllib.loads(pathlib.Path("uv.lock").read_text())
locked = {normalize(p["name"]): p["version"] for p in lock["package"]}
installed = {normalize(d.metadata["Name"]): d.version for d in metadata.distributions()}
assert all(locked.get(name) == version for name, version in installed.items())
print(sys.version)
print(workshop_marketing_agent.__file__)
print("Offline imports and exact locked dependency versions passed:", len(installed), "distributions")
PY

for runtime in 311 313; do
  (
    cd "$check_dir/outside"
    env -i "$check_dir/wheel$runtime/bin/python" -I check_imports.py
    env -i "$check_dir/wheel$runtime/bin/python" -I -m pytest \
      tests/test_validation.py::test_exact_copy_and_decimal_serialization \
      tests/test_validation.py::test_nonexistent_and_ambiguous_local_times \
      tests/test_validation.py::test_full_local_day_deadline_boundaries \
      tests/test_application.py::test_two_channel_roundtrip \
      tests/test_application.py::test_ai_replay_after_restore_and_conflicting_reuse
  )
done
shasum -a 256 -c "$check_dir/lock-before.sha256"
```

`-I` removes the checkout/current directory, user site packages and Python environment
variables from import resolution. Pytest adds only the copied test directory for
its existing helper imports. Its fixtures/guard are reused without changes. This
checks Berlin DST gaps/folds and local-day deadlines, exact Decimal strings,
two-channel campaign restoration and replay of recorded identities on both runtimes.
The one wheel is built under 3.11 to exercise the build backend there, then installed
unchanged under both versions. Build tools are not runtime dependencies.

## Recorded verification

Verified **September 26, 2026**, after an independent review with no actionable
findings. The reviewer checked the code, unchanged dependency versions, expanded
lock artifacts and isolation procedure before the final complete suite runs.

| Check | CPython 3.11.15 | CPython 3.13.14 |
| --- | --- | --- |
| Platform | macOS 15.6.1 arm64 | macOS 15.6.1 arm64 |
| pytest | 9.1.1 | 9.1.1 |
| Fresh locked environment | 40 distributions installed | 40 distributions installed |
| Full suite, once after review | **499 passed in 5.78s** | **499 passed in 8.03s** |
| Same wheel, isolated offline imports | All 15 modules imported; 40 distributions match lock | All 15 modules imported; 40 distributions match lock |
| Existing wheel behavior checks above | **6 passed in 0.59s** | **6 passed in 0.60s** |
| `uv pip check` | All installed packages compatible | All installed packages compatible |

Both columns used **uv 0.11.31** (`Homebrew 2026-07-22 x86_64-apple-darwin`);
uv's own executable architecture does not change the explicitly selected native
arm64 Python interpreters. Both interpreters report Clang 22.1.3. The actual
variable values for the commands above were:

```sh
check_dir=/tmp/workshop-runtime-Ox6TJ8
uv_cache=/tmp/workshop-firestore-uv-cache
uv_bin=/usr/local/bin/uv
py311=/tmp/workshop-python311-interpreters/cpython-3.11.15-macos-aarch64-none/bin/python3.11
py313=/Users/my/.local/share/uv/python/cpython-3.13.14-macos-aarch64-none/bin/python3.13
```

The one wheel, `workshop_marketing_agent-0.1.0-py3-none-any.whl`, was built under
3.11 with **Hatchling 1.32.4** (`Requires-Python: >=3.10`). Its SHA-256 is
`0aa2ce52cab05eb832ac6a189f3932c775f48f863f4ff9c3ed07d146df7c664c`.
It was installed unchanged in `wheel311` and `wheel313`. Imports resolved to
each environment's `lib/python3.x/site-packages/workshop_marketing_agent`, with
no editable install. The installed runtime and test versions exactly match the
exported lock; no looser dependency resolution was used for wheel testing.

Setup required registry downloads/metadata for initially uncached artifacts;
an initial offline 3.13 sync and offline wheel-dependency installation could not
complete from cache alone. Normal setup with network access succeeded without
changing any version pins. Final tests/imports and the wheel build were offline.

Repeated locked synchronization succeeded for each interpreter with identical
before/after `uv.lock` SHA-256:
`5e1bbeffe24d5f04cc0c9a1ecd0d55ca3053d6d8c218209e04cf237739d26547`.
The existing project `.venv` configuration and installed distribution versions
were checked against their initial values and remain unchanged. Package source,
schema versions and fixture files are unchanged. No emulator or Firebase
deployment, authentication integration, production Firestore or live model call
was tested; no claim is made about Linux/Firebase production behavior.
