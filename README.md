# workshop-marketing-agent

A Python project for AI-assisted workshop marketing, with human approval,
channel adapters, and evaluation.

**Status:** The minimal Python package foundation is installable and importable.
V1 functionality and application integrations remain planned, with open decisions
recorded in the documentation.

## Local setup

Use a current Python 3.13 patch release. Python 3.13 is the verified baseline;
the package metadata (`>=3.13`) does not claim testing of other Python versions
or Firebase runtimes. The commands below use **zsh or bash on macOS/Linux**,
starting in the repository root.

```sh
python3.13 --version
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -c "import workshop_marketing_agent; print(workshop_marketing_agent.__file__)"
```

The virtual environment keeps project tools separate from other Python projects.
The editable installation imports from `src/workshop_marketing_agent`, so source
changes are available without reinstalling. Activate `.venv` in each new shell;
run `deactivate` when finished.

There are no runtime dependencies. Importing the package needs no credentials,
configuration, or network calls. Pydantic, the OpenAI SDK, Firebase libraries,
and pytest are deferred until needed by a later task.

## Build and verify a wheel

With `.venv` active and the shell in the repository root:

```sh
python -m pip install build
python -m build --wheel
repo_dir="$PWD"
verify_dir="$(mktemp -d)"
python -m venv "$verify_dir/venv"
(
    cd "$verify_dir"
    ./venv/bin/python -m pip install --no-index --no-deps \
        "$repo_dir/dist/workshop_marketing_agent-0.1.0-py3-none-any.whl"
    ./venv/bin/python -I -c "import workshop_marketing_agent; print(workshop_marketing_agent.__file__)"
)
```

The import path must point inside the temporary environment's `site-packages`,
outside the repository. `-I` isolates the import from the working directory,
user site packages, and Python environment variables. The wheel installation
uses only the local file. Hatchling is the build backend; `build` is a development
tool. Installing build tools may need package-index access. The Hatch application
is not required. The temporary verification directory can be removed afterward.

## Documentation

- [Architecture proposal](docs/architecture.md)
- [V1 scope and acceptance criteria](docs/v1-scope.md)
- [Project working guidelines](AGENTS.md)

Project documentation, code comments, issues, and pull requests use English.
Workshop content, generated marketing copy, and the teacher-facing interface use
German. Discussions and Python explanations with the project owner remain in German.
