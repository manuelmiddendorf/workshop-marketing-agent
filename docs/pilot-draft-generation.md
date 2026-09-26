# Validated pilot draft generation

As of September 26, 2026, the package can generate one review-only German draft
for Google Business Profile and one for manual Rausgegangen entry. It imports and
revalidates the workshop before one bounded model call. It does not publish,
approve, persist, or edit the original public description.

## Boundary and result

`generate_pilot_drafts` accepts a `FeedImportResult`, an offset-aware generation
time, an explicit model identifier, a timeout of at most 120 seconds, and an
injectable generation client. It requires usable core facts, importer promotion
eligibility, and supported availability at generation time before calling the
model.

The model returns wording only. Python owns and renders workshop identity, source
version, event schedule, location, exact regular price and pricing unit, canonical
booking URL, Google `EVENT`/`BOOK` fields, and the Rausgegangen fact sheet.
Metadata records the model, prompt and schema versions, source version,
generation time, available usage, and zero automatic retries.

The prompt sends validated facts, supported optional claims, the separately
identified original public description, channel instructions, and the stated
marketing-round purpose. Workshop copy and purpose are JSON-encoded untrusted
data. They cannot grant permission, change destinations, or make unsupported
claims valid.

The OpenAI boundary uses the official Python SDK's Responses API and Pydantic
Structured Outputs, following the
[official Structured Outputs guide](https://developers.openai.com/api/docs/guides/structured-outputs).
The model is deliberately configurable because account access and model support
must be confirmed when a live run is made. Credentials stay in
`OPENAI_API_KEY`; the repository contains no key or model default.

## Channel definitions

Google output is an event draft with German title/body, deterministic start and
end schedule, and a `BOOK` action pointing to the canonical workshop URL. This
matches Google's documented event fields and call-to-action behavior. Sources:
[Create Posts on Google](https://developers.google.com/my-business/content/posts-data)
and the
[LocalPost reference](https://developers.google.com/my-business/reference/rest/v4/accounts.locations.localPosts),
verified September 26, 2026.

Rausgegangen output contains title and description wording plus a deterministic
manual-entry fact sheet with time, location, regular price and external ticket
URL. Rausgegangen documents event content, a price note, and external ticket
links. Sources:
[event creation](https://rausgegangen-assist.freshdesk.com/support/solutions/articles/44002202915-wie-erstelle-ich-ein-event-),
[external ticket links](https://rausgegangen-assist.freshdesk.com/support/solutions/articles/44002486114-kann-ich-einen-externen-ticketlink-hinterlegen-),
and the [event portal](https://zentrale.rausgegangen.de/), verified September 26,
2026.

Public sources did not establish character limits, category selections, exact
portal field mappings beyond the documented content/price/ticket fields, account
capabilities, or publication acceptance. Editorial brevity guidance in the
prompt is not a platform limit. Rausgegangen entry remains manual.

## Deterministic checks and limits

Both generated texts must contain the exact workshop title, date, time, location,
regular price with unit, and booking URL. Checks reject conflicting dates, times,
prices and URLs plus known patterns for unsupported discounts, counts, included
services, suitability, urgency, benefits, and booking guarantees. Refusal,
incomplete output, timeout, API failure, schema failure and fact failure have
separate diagnostics and return no usable drafts.

These checks are deliberately conservative and do not understand every possible
German paraphrase or implication. Structured output and passing checks do not
establish complete factual accuracy. Every result remains an unapproved draft for
human review. Image permission, attribution and reference conflicts remain in the
diagnostics; supported text can proceed while `image_ready` is false.

## Deliberate live pilot command

This opt-in command is excluded from routine tests. It makes one public feed
request and, only after all gates pass, one OpenAI request. It never publishes.

```sh
export OPENAI_API_KEY="your-key-from-a-secure-local-source"
export OPENAI_MODEL="an-explicit-structured-output-capable-model"

uv run --locked python -m workshop_marketing_agent.live_generate \
  --endpoint 'https://europe-west1-middendorf-yoga.cloudfunctions.net/WSGetPublicWorkshops?format=workshop-data.v1&id=malws-copy' \
  --workshop-id malws-copy \
  --model "$OPENAI_MODEL" \
  --timeout 30 \
  --purpose 'Erste Ankündigung des Workshops'
```

No live generation was run for this change. Therefore no actual model, token
usage, generated copy, or live qualitative result is claimed. If the command is
run later, retain only sanitized output and report the actual model, prompt/schema
versions, source version, timestamps, usage and diagnostics. Availability must be
treated as the dated observation it is.

## Evaluation

The [prompt comparison](../evaluations/prompt-comparison.md) runs both prompt
versions through identical controls. Stored synthetic responses verify the
evaluation and hard-check machinery. Genuine qualitative comparison is pending
credentials, an explicit model choice, and human review.
