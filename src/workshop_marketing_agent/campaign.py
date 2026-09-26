"""Deterministic, local campaign URLs; no click storage or attribution."""

from typing import Annotated, Literal
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

from pydantic import BaseModel, ConfigDict, Field

from .models import _booking_url

# Opaque application references, not human names or contact details. Callers are
# responsible for assigning non-personal IDs; syntax cannot prove anonymity.
Reference = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.~-]{0,127}$")]


class CampaignMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    workshop: Reference
    campaign: Reference
    round: Reference
    channel: Literal["google_business", "rausgegangen"]
    variant: Reference
    utm_medium: Reference = "event_listing"

    def parameters(self) -> tuple[tuple[str, str], ...]:
        return (
            ("wma_workshop", self.workshop),
            ("wma_campaign", self.campaign),
            ("wma_round", self.round),
            ("wma_channel", self.channel),
            ("wma_variant", self.variant),
            ("utm_source", self.channel),
            ("utm_medium", self.utm_medium),
            ("utm_campaign", self.campaign),
            ("utm_content", self.variant),
        )


def build_campaign_link(canonical_url: str, campaign: CampaignMetadata) -> str:
    """Append only documented parameters, preserving the original query bytes.

    All existing wma_/utm_ keys are reserved, including encoded/case variants.
    Even matching reserved values are rejected rather than silently adopted.
    """
    _booking_url(canonical_url)
    if "#" in canonical_url:
        raise ValueError("Canonical URL must not contain a fragment delimiter")
    campaign = CampaignMetadata.model_validate_json(campaign.model_dump_json())
    query = urlsplit(canonical_url).query
    for key, _ in parse_qsl(query, keep_blank_values=True, errors="strict"):
        if key.casefold().startswith(("wma_", "utm_")):
            raise ValueError("Canonical URL already contains a reserved tracking parameter")
    encoded = urlencode(campaign.parameters(), quote_via=quote, safe="")
    separator = "&" if query else ("" if canonical_url.endswith("?") else "?")
    return canonical_url + separator + encoded
