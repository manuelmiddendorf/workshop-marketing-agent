# workshop-marketing-agent

A Python project for AI-assisted workshop marketing, with human approval,
channel adapters, and evaluation.

**Status:** The package includes provisional workshop input models, deterministic
offline validation and a read-only public pilot feed importer. Generation,
publishing and teacher-app integration remain planned. Firestore document
compatibility has not been verified.

## Local setup

Install uv using the [official installation instructions](https://docs.astral.sh/uv/getting-started/installation/).
The commands below use **zsh or bash on macOS/Linux**, starting in the repository
root. `.python-version` selects Python 3.13; uv can download it if needed.
The package metadata (`>=3.13`) does not claim testing of other Python versions
or Firebase runtimes.

```sh
uv --version
uv sync --locked
uv run --locked python --version
uv run --locked python -c "import workshop_marketing_agent"
uv run --locked python -m pytest --version
```

uv creates and manages `.venv`, installs the package in editable mode, and includes
pytest from the `dev` dependency group. Source changes under
`src/workshop_marketing_agent` are available without reinstalling. `uv run` uses
this environment without manual activation.

The committed `uv.lock` records exact runtime and development dependency versions. `--locked`
checks that it matches `pyproject.toml` and fails instead of changing it. Intentional
dependency changes should update both files together; do not edit the lockfile by
hand. See [uv's locking and syncing documentation](https://docs.astral.sh/uv/concepts/projects/sync/).

For clean-install verification without changing an existing `.venv`, set
`UV_PROJECT_ENVIRONMENT` to a fresh absolute path before running the same commands:

```sh
export UV_PROJECT_ENVIRONMENT="$(mktemp -d)/venv"
```

Keep that setting for the verification session, then run
`unset UV_PROJECT_ENVIRONMENT` to return to the default project environment.

Pydantic is the runtime dependency. Importing the package and validating supplied
data need no credentials, application configuration or network calls. Initial setup may need
network access to download Python, dependencies and build tools. The OpenAI SDK
and Firebase libraries remain deferred.

Run the offline behavior tests with:

```sh
uv run --locked python -m pytest
```

Tests use synthetic evidence, explicit current times and a socket guard against
network access. No application services or credentials are needed.

## Validate workshop input

The [provisional contract](docs/workshop-data-contract.md#provisional-validation-contract)
defines required facts, supplied verification and optional claim limitations.
For the invented complete example, from the repository root:

```python
import json
from datetime import datetime
from pathlib import Path

from workshop_marketing_agent import validate_workshop

data = json.loads(Path("examples/workshops/synthetic-complete-input.json").read_text())
result = validate_workshop(data, now=datetime.fromisoformat("2026-10-24T10:00:00+00:00"))
print(result.usable, result.claims.current_discount)  # True True at this example time
```

Parsing incomplete evidence is allowed; `usable` requires verified core facts.
Inspect `diagnostics` and the separate `claims` before using optional facts.
Revalidate with the actual current time before using time-sensitive claims.
Validation checks supplied structure and consistency, not factual truth or
permission to publish. The existing Mal-Yoga reconstruction remains incomplete.

To fetch the configured public pilot, see the
[feed import example and limitations](docs/public-workshop-feed.md). HTTP handling
is separate from these offline rules; importing is neither publishing approval
nor booking verification.

## Build and verify a wheel

From the repository root:

```sh
uv build --wheel
repo_dir="$PWD"
wheel_check_dir="$(mktemp -d)"
uv venv --python 3.13 "$wheel_check_dir/venv"
uv export --locked --no-dev --no-emit-project -o "$wheel_check_dir/requirements.txt"
uv pip install --python "$wheel_check_dir/venv/bin/python" \
    -r "$wheel_check_dir/requirements.txt"
uv pip install --python "$wheel_check_dir/venv/bin/python" --no-index --no-deps \
    "$repo_dir/dist/workshop_marketing_agent-0.1.0-py3-none-any.whl"
(
    cd "$wheel_check_dir"
    env -i ./venv/bin/python -I -c "import workshop_marketing_agent; print(workshop_marketing_agent.__file__)"
)
```

The import path must point inside the temporary environment's `site-packages`,
outside the repository. `-I` isolates the import from the working directory,
user site packages, and Python environment variables. Runtime dependencies are
installed from the lockfile first; that setup may download packages. The wheel
installation uses only the local file. `env -i` clears inherited environment
variables, including credentials, for the offline import.

uv runs the build; Hatchling remains the backend that creates the wheel. Its
isolated build dependencies are resolved separately from `uv.lock` using
`build-system.requires`; the lockfile does not pin them. The Hatch application and
a separate `build` installation are unnecessary. Temporary verification
directories can be removed afterward.

Verification commands, exact versions and actual results are recorded in each PR.
Other Python versions and target deployment runtimes remain unverified.

## Documentation

- [Architecture proposal](docs/architecture.md)
- [V1 scope and acceptance criteria](docs/v1-scope.md)
- [Project working guidelines](AGENTS.md)

Project documentation, code comments, issues, and pull requests use English.
Workshop content, generated marketing copy, and the teacher-facing interface use
German. Discussions and Python explanations with the project owner remain in German.
