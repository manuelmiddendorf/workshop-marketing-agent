# workshop-marketing-agent

A Python project for AI-assisted workshop marketing, with human approval,
channel adapters, and evaluation.

**Status:** The minimal Python package foundation is installable and importable.
V1 functionality and application integrations remain planned, with open decisions
recorded in the documentation.

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

The committed `uv.lock` records exact development dependency versions. `--locked`
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

There are no runtime dependencies. Importing the package needs no credentials,
configuration, or network calls. Initial setup may need network access to download
Python and build/development tools. Pydantic, the OpenAI SDK, and Firebase libraries
remain deferred.

Run future behavior tests with:

```sh
uv run --locked python -m pytest
```

No behavior tests exist yet. The pytest version check above verifies that the test
runner is installed; it is not a passing test suite. The first tests belong with
workshop validation, not this setup change.

## Build and verify a wheel

From the repository root:

```sh
uv build --wheel
repo_dir="$PWD"
wheel_check_dir="$(mktemp -d)"
uv venv --python 3.13 "$wheel_check_dir/venv"
uv pip install --python "$wheel_check_dir/venv/bin/python" --no-index --no-deps \
    "$repo_dir/dist/workshop_marketing_agent-0.1.0-py3-none-any.whl"
(
    cd "$wheel_check_dir"
    env -i ./venv/bin/python -I -c "import workshop_marketing_agent; print(workshop_marketing_agent.__file__)"
)
```

The import path must point inside the temporary environment's `site-packages`,
outside the repository. `-I` isolates the import from the working directory,
user site packages, and Python environment variables. The wheel installation
uses only the local file, and `env -i` clears inherited environment variables,
including credentials, for the import.

uv runs the build; Hatchling remains the backend that creates the wheel. Its
isolated build dependencies are resolved separately from `uv.lock` using
`build-system.requires`; the lockfile does not pin them. The Hatch application and
a separate `build` installation are unnecessary. Temporary verification
directories can be removed afterward.

Verified on macOS with **Python 3.13.5** and **uv 0.11.31**: locked sync into a fresh
temporary project environment, package import, pytest version check, wheel build,
and wheel installation/import in a second clean environment outside the repository.
The lockfile and the existing local `.venv` were unchanged by these checks.

## Documentation

- [Architecture proposal](docs/architecture.md)
- [V1 scope and acceptance criteria](docs/v1-scope.md)
- [Project working guidelines](AGENTS.md)

Project documentation, code comments, issues, and pull requests use English.
Workshop content, generated marketing copy, and the teacher-facing interface use
German. Discussions and Python explanations with the project owner remain in German.
