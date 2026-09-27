"""Build an isolated Firebase source tree; this script never deploys anything."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New or empty staging directory")
    parser.add_argument("--python", required=True, help="Explicit Python 3.11 interpreter path")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    version = subprocess.check_output(
        [args.python, "-I", "-c", "import sys; print('.'.join(map(str, sys.version_info[:2])))"], text=True,
    ).strip()
    if version != "3.11":
        parser.error("The deployment build requires Python 3.11")
    if output.exists() and any(output.iterdir()):
        parser.error("Output must be empty; existing files will not be overwritten")
    source = output / "functions"
    wheels = source / "vendor"
    wheels.mkdir(parents=True)
    subprocess.run(["uv", "build", "--wheel", "--python", args.python, "--out-dir", str(wheels)],
                   cwd=root, check=True)
    requirements = subprocess.check_output(
        ["uv", "export", "--locked", "--python", args.python,
         "--group", "firebase", "--no-dev", "--no-emit-project"],
        cwd=root, text=True,
    )
    wheel, = wheels.glob("*.whl")
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    (source / "requirements.txt").write_text(
        requirements + f"\n./vendor/{wheel.name} --hash=sha256:{digest}\n", encoding="utf-8",
    )
    for filename in ("main.py", "pilot_adapter.py", "google_oauth.py"):
        shutil.copyfile(root / "functions" / filename, source / filename)
    (output / "firebase.json").write_text(json.dumps({"functions": [{
        "source": "functions", "codebase": "pilot-marketing", "runtime": "python311",
        "ignore": ["venv", ".venv", "__pycache__", "*.pyc", ".env*", ".secret*"],
    }]}, indent=2) + "\n", encoding="utf-8")
    print(f"Prepared {output}; wheel SHA-256 {digest}. No deployment performed.")


if __name__ == "__main__":
    main()
