"""Deliberate CLI for one live pilot import and generation; it never publishes."""

import argparse
import json
import os
from datetime import UTC, datetime

from .feed import load_public_workshop
from .generation import generate_pilot_drafts


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--workshop-id", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--purpose", required=True)
    parser.add_argument("--timeout", type=float, default=30.0)
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is required for a deliberate live generation")

    imported = load_public_workshop(
        endpoint=arguments.endpoint,
        workshop_id=arguments.workshop_id,
        timeout=arguments.timeout,
        now=datetime.now(UTC),
    )
    result = generate_pilot_drafts(
        imported,
        marketing_round_purpose=arguments.purpose,
        now=datetime.now(UTC),
        model=arguments.model,
        timeout=arguments.timeout,
    )
    output = {
        "status": result.status,
        "publishing_approved": result.publishing_approved,
        "image_ready": result.image_ready,
        "facts": result.facts.model_dump(mode="json") if result.facts else None,
        "google": result.google.model_dump(mode="json") if result.google else None,
        "rausgegangen": result.rausgegangen.model_dump(mode="json") if result.rausgegangen else None,
        "metadata": result.metadata.model_dump(mode="json") if result.metadata else None,
        "diagnostics": [diagnostic.__dict__ for diagnostic in result.diagnostics],
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if result.status == "draft" else 2


if __name__ == "__main__":
    raise SystemExit(main())
