"""Small controlled comparison helper for the two pilot prompt versions."""

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from .feed import FeedImportResult
from .generation import (
    DraftGenerationResult,
    ModelCallResult,
    generate_pilot_drafts,
)


PROMPT_VERSIONS = ("pilot-drafts.v1", "pilot-drafts.v2")
QUALITATIVE_CRITERIA = (
    "tone",
    "clarity",
    "channel_fit",
    "call_to_action",
    "correction_effort",
)


class _StoredClient:
    def __init__(self, response: ModelCallResult) -> None:
        self.response = response

    def generate(self, request):
        return self.response


@dataclass(frozen=True)
class PromptEvaluation:
    prompt_version: str
    hard_checks_passed: bool
    diagnostic_fields: tuple[str, ...]
    qualitative_assessment: Mapping[str, str] | None


@dataclass(frozen=True)
class PromptComparison:
    model: str
    reference_time: datetime
    results: tuple[PromptEvaluation, PromptEvaluation]
    hard_check_regression: bool
    qualitative_status: str


def compare_prompt_versions(
    imported: FeedImportResult,
    *,
    responses: Mapping[str, ModelCallResult],
    marketing_round_purpose: str,
    now: datetime,
    model: str,
    timeout: float,
    qualitative: Mapping[str, Mapping[str, str]] | None = None,
) -> PromptComparison:
    """Use identical controls; stored responses do not measure model quality."""
    if set(responses) != set(PROMPT_VERSIONS):
        raise ValueError("responses must contain exactly the two pilot prompt versions")
    if qualitative is not None:
        for version in PROMPT_VERSIONS:
            if set(qualitative.get(version, {})) != set(QUALITATIVE_CRITERIA):
                raise ValueError("qualitative assessment must cover all predefined criteria")

    generated: list[DraftGenerationResult] = []
    evaluations = []
    for version in PROMPT_VERSIONS:
        result = generate_pilot_drafts(
            imported,
            marketing_round_purpose=marketing_round_purpose,
            now=now,
            model=model,
            timeout=timeout,
            client=_StoredClient(responses[version]),
            prompt_version=version,
        )
        generated.append(result)
        evaluations.append(PromptEvaluation(
            prompt_version=version,
            hard_checks_passed=result.status == "draft",
            diagnostic_fields=tuple(d.field for d in result.diagnostics if d.claim == "draft"),
            qualitative_assessment=qualitative[version] if qualitative is not None else None,
        ))

    regression = generated[0].status == "draft" and generated[1].status != "draft"
    return PromptComparison(
        model=model,
        reference_time=now,
        results=(evaluations[0], evaluations[1]),
        hard_check_regression=regression,
        qualitative_status="recorded" if qualitative is not None else "pending genuine outputs and human review",
    )
