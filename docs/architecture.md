# Architecture: workshop-marketing-agent

As of September 24, 2026. Status: **Draft for joint review; package and provisional input validation implemented**.

Confirmed product decisions are documented in [v1-scope.md](v1-scope.md).
The installable Python package uses Python 3.13 and Hatchling. Provisional
workshop input models and deterministic offline validation use Pydantic. The
remaining technical design is a proposal for later implementation. Open questions
are explicitly marked. Generation and integration with the teacher app remain unimplemented.

## 1. Purpose and architectural principles

The system helps a yoga studio promote existing workshops through suitable
channels with little manual effort. As a public portfolio project, it should also
demonstrate understandable Python development, applied LLM engineering, and evaluation.

- An independent, installable Python package with an explicit pipeline.
- A shared workshop data model that does not depend on the Firestore schema.
- Deterministic rules for facts, channels, links, statuses, and actions.
- An LLM for drafting and revising copy; people approve specific versions.
- The existing teacher app for the interface; Firebase integration for operation and data.
- Results and errors per channel; successful partial results are preserved.
- Small, understandable changes and evaluation from the start.

Initial scale: 1–2 workshops per month, 2–3 teachers, and promotion starting
2–3 months before each workshop. The design favors manageable operation and clear code.

## 2. Responsibilities

| Component | Responsibilities |
| --- | --- |
| Teacher app | Display drafts, allow direct editing and revision requests, select variants and images, disable channels, approve content, and confirm manual submissions or publications |
| Firebase integration | Authentication and workshop access, data mapping, image references, server-side secrets, persistent storage, atomic reservation of actions, and links to bookings |
| Python package | Normalize the package's public data model, determine channel suitability, assemble prompts, generate and revise copy, validate results, define business rules for state, build link parameters, provide channel adapters, and support evaluation |
| Channel adapters | Channel-specific fields and limits, presentation, listing preparation, and optional calls to official publishing and update APIs |

The Firebase integration runs the package in Python on the server. The web app
can call it through [Firebase Callable Functions](https://firebase.google.com/docs/functions/callable).
The package does not import application-specific Firestore models. Evaluation
and tests can run without Firebase.

The integration layer persists state; the package defines its meaning and
allowed transitions. Concrete storage and API credentials stay outside the
business logic. The exact execution model remains open in section 12.

## 3. Data and factual accuracy

The implemented [provisional input contract](workshop-data-contract.md#provisional-validation-contract)
distinguishes incomplete evidence, usable core facts and supported optional claims.
It checks supplied structure and consistency, without authenticating evidence,
interpreting prose, granting publishing approval or claiming Firestore compatibility.
The integration behavior described below remains planned.

The integration provides a validated snapshot of workshop data with a stable
workshop ID and source version. As needed, it includes the title, original
description, time zone, start and end times, location, audience, age limits,
included services, booking URL, prices and currency, early-bird deadlines,
and image references. Missing optional facts remain unknown. Required fields
depend on the channel.

Early-bird data already exists as structured fields in Firestore. It is taken
from those fields rather than inferred from the description. Deadlines must
have an unambiguous interpretation as a timestamp with a time zone. The owner
confirmed that a date-only early-bird deadline includes the entire stated local
day. Public checkout qualifies on successful payment; member bookings qualify
on registration, with bank transfer handled independently. The exclusive cutoff
is the start of the following local calendar day. The studio normally uses
`Europe/Berlin` and EUR; these are explicit configuration defaults, with workshop
exceptions preserved. Stored types and authoritative registration/payment
timestamps still need verification during data mapping. The
[booking integration findings](booking-integration-findings.md) distinguish
existing early-bird display logic from outstanding booking integration work.

Prices normally apply per person. Parent-child yoga has a base price for one
adult and one child, with additional people available during checkout. Pair
pricing is a possible future case. Early-bird places are normally limited.
Local parent-child booking code counts one family booking, including extra
people, as one quota unit; person capacity is counted separately. This does not
establish quota units for other workshops or verify live counts. The
[workshop contract](workshop-data-contract.md#business-rules-confirmed-by-the-owner)
records these confirmed rules and the remaining representation questions.

Member and public/non-member descriptions are separate in the existing workshop
data. External marketing uses public copy; missing public copy does not authorize
automatic substitution of member text. Original versions remain preserved.

The detailed original copy remains unchanged unless a teacher explicitly starts
a revision. Page elements such as "Vergangen" (past) and HTML encodings are cleaned
up when preparing input. A conflict between the description and structured facts
must be resolved before publishing affected content. It must not be resolved silently.

Dates, times, prices, age information, and booking links are formatted
deterministically for output. Time-sensitive facts are checked during generation
and again before publishing. An early-bird offer must include its deadline;
expired offers must not be promoted as current.

Free-form language can still contain unsupported claims. Schema and fact checks
do not guarantee complete semantic accuracy. Constrained text fields, semantic
evaluation, and human review complement each other. In particular, suitability
for children, included services, qualifications, availability, and claims about
benefits must not be inferred from plausible assumptions.

## 4. Pipeline and prompt construction

Load workshop → normalize → check conflicts → recommend channels → generate copy
→ validate → human review → publish or prepare submission → persist status
→ attribute clicks and bookings.

Channel selection uses understandable rules and provides a short explanation.
For example, recommending a family channel requires the workshop's stated audience
to fit that channel.
Adapting the wording does not broaden the workshop's suitability. Teachers can
select or deselect suitable channels; missing required facts still block publishing.

A prompt is assembled from small, versioned components:

1. Shared rules and boundaries for writing copy.
2. Validated workshop facts and a separately identified original description.
3. Audience, channel rules, and style: warm, welcoming, and clear.
4. Purpose of the marketing round and the generation timestamp.
5. For revisions: the current draft and the revision request.
6. A schema for the allowed output fields.

The planned tools are the official OpenAI Python SDK, Pydantic, and Structured
Outputs. The [official documentation](https://developers.openai.com/api/docs/guides/structured-outputs)
describes Pydantic-based schemas. Passing schema validation does not replace
content review; refusals, incomplete responses, and validation errors are handled
separately.

Each generation records the model identifier, prompt version, schema version,
source version, timestamp, and available usage data. Workshop text and revision
requests are input data. They cannot change system rules, permissions, destination
URLs, or publishing actions.

One good draft is preselected; up to two additional variants can be generated
on request. A revision uses the current version, preserves the previous one,
and requires renewed review and approval when adopted.

## 5. Campaigns, versions, and persistent state

Proposed model: a workshop has a marketing campaign with multiple marketing
rounds, such as an initial announcement and a reminder. Correcting a publication
is an update linked to the existing post; it does not automatically create a new round.

| Information | Purpose |
| --- | --- |
| Campaign and round | Workshop reference, purpose, creator, timestamps, and selected channels |
| Channel draft and versions | Text fields, variants, revision request, image, final link, source version, and validation result |
| Approval | Approving person, timestamp, and exact version including image and link |
| Publication record | Channel, external post ID and URL where available, published version, and creation or update timestamp |
| Execution attempt | Operation, stable retry identifier, processing state, error class, attempts, and timestamps |
| Manual confirmation | Who confirmed submission or publication and when, with an optional public URL |
| Attribution | Campaign, round, channel, selected variant, click reference, and confirmed booking reference |

Draft state and publication state are stored separately. A possible small set
of states is:

- Draft: generating, review required, approved, outdated, or error.
- Publication: not started, in progress, submitted, published, failed, or outcome unknown.
- Additional flags: channel disabled and change required, where applicable.

"Ready to copy" is a display state derived from a valid draft and manual mode.
For editorial platforms, "submitted" does not yet mean "published". An existing
published post remains recorded even while a new version awaits review or its
update has failed.

Approvals apply to a specific version. Changes to its text, image, or link
invalidate approval. Changes to relevant workshop facts and expired price
deadlines trigger another review. Immediately before sending, the integration
compares the current state with the approved source data.

Published posts that need changes are visibly flagged when the current data is
checked. V1 does not promise scheduled background corrections. Teachers initiate
updates and reminders.

## 6. Channel adapters

Adapters are explicitly registered in a small registry. Adding channel rules
should not require redesigning the central pipeline. The business interface
covers the responsibilities below; it is not a universal plugin or workflow engine.

| Part | Contents |
| --- | --- |
| Description | Channel ID, supported modes, audiences, image requirements, required fields, and known limits |
| Suitability | A deterministic result with an understandable explanation |
| Draft | Output schema, channel instructions, presentation, and validation |
| Preparation | Copyable fields, destination page, image reference, and required steps |
| Optional publishing | Official creation and, where supported, update operations with structured results and errors |

Modes: `automatic` publishes through a supported API after approval; `assisted`
prepares forms or submissions; `manual` provides copy, a link, and a subsequent
confirmation step. An adapter does not need to support all modes or implement
publishing. Account configuration and credentials also determine which supported
actions are currently available.

Google is the planned first external automatic channel. Rausgegangen and HIMBEER
start with assisted manual listings. A manual fallback may be offered without
reporting a failed API attempt as successful. The studio's workshop page remains
the canonical source of information and booking destination. Its existing
publishing workflow is integrated, not automatically replaced by a second
editorial workflow.

## 7. Images

The pool catalogs existing workshop and website images. Originals do not need
to be duplicated. The catalog contains stable image identifiers, references,
short descriptions, topic tags, usage permissions, and credits where required.
Known dimensions support channel validation.

The workshop image is preselected. Simple rules suggest up to five suitable
alternatives; the rest of the approved pool remains accessible. The selection is
stored per post and can be reused across channels and rounds. The integration
resolves filenames to accessible image data or URLs. A file in the frontend
project is not automatically accessible for server-side publishing. Reachability,
usage rights, and the channel's format requirements are checked before sending.

## 8. Tracking and bookings

Links contain deterministically assigned values for workshop, campaign, round,
channel, and variant, plus suitable UTM parameters. These values contain no
names, email addresses, or other participant data.

The website integration records a campaign visit and links it to the existing
booking flow where possible. A booking confirmed on the server is counted once;
starting checkout or returning from a payment page is not sufficient. Repeated
payment notifications must not produce additional bookings. Cancellations must
not appear as new bookings. Existing PayPal and Stripe payment logic is reused
for public bookings only. Member places are booked at registration, followed by
independent bank transfer; a later payment must not count as another booking.
The existing member-facing payment-receipt reminder is intentional and remains.
It does not require staff to confirm payment for booking, capacity, applicable
early-bird usage, or attribution to count. A member registration establishes a
booking, not evidence that the transfer has arrived.

The confirmed default attribution window is **90 days before the confirmed
booking**, scoped to the booked workshop. Credit goes to the latest recorded
valid marketing click for that workshop no more than 90 days before the booking.
A new valid click for the same workshop replaces the previous touchpoint; its
timestamp determines the new deadline. Touchpoints for other workshops are not
transferred to this booking.

A later direct visit does not overwrite the touchpoint or extend its deadline.
Without a matching touchpoint within the window, the source remains unknown.
The 90-day window is a configurable starting value based on the 2–3 month
promotion period. Later adjustments are evaluated against observed delays
between clicks and bookings, rather than applied automatically.

Complete tracking across devices and causal claims about a channel's impact
are not promised. Automated link previews and repeated visits may affect click counts.

The attribution window does not define the data retention period. Visitor
recognition, consent, retention, and deletion remain open. They must be defined
before production visitor attribution; this document does not claim an exemption
from privacy requirements. The studio's booking page must not add internal UTM
parameters that overwrite an already captured external source.

## 9. Errors, retries, and duplicate publishing

Errors are visible per channel and distinguish missing input or credentials,
generation timeouts, refusals, schema or fact errors, channel errors, and unknown
outcomes. Only valid drafts can be approved.

Transient generation errors and API failures known to be safe to retry may be
retried a limited number of times with delays. Permanent errors need a visible
correction. An automatically repaired draft does not receive automatic approval.
The exact retry count will be defined later.

Before sending, a stable operation identifier is reserved persistently.
Concurrent clicks must not start the same publishing action more than once.
Provider-native idempotency is used where available. External post IDs are stored
for updates. A new version does not automatically authorize recreating an already
published post.

If the connection breaks after sending, a post may already exist. The initial
state is then "outcome unknown". Reconciliation with the provider or human
clarification must precede another creation attempt. A local identifier alone
does not guarantee a single external publication.

## 10. Evaluation and tests

An initial reference set covers synthetic or anonymized children's yoga,
parent-child yoga, wellness, meditation, stress relief, and massage workshops.
Each case includes expected channel suitability and authoritative facts.
Additional cases cover missing information, conflicting copy, expired early-bird
deadlines, date changes, prompt injection, and unsuitable audiences.

Deterministic checks cover required fields, schemas, character limits, known
facts, price deadlines, image references, and links. Critical errors must not
be hidden by a good average score. The agreed reference set must pass the hard
checks; this does not prove error-free behavior for arbitrary free-form text.

Qualitative evaluation covers tone, audience, channel suitability, title, call
to action, repetition, and unsupported claims. A small reference set evaluated
by people provides the foundation; an LLM judge may supplement it later.
Evaluations use predefined criteria and examples. Specific scoring thresholds
still need to be agreed.

Comparisons control fixtures, evaluation criteria, and the generation timestamp.
They record prompt, schema, and model versions, results, runtime, and available
usage data. Live generations can vary; reproducibility means documented conditions
and stored results that can be analyzed.

pytest is the planned test tool. Routine tests and CI use fakes or stored
synthetic responses and require no production credentials. Live evaluation is a
separate, deliberate run. Integration tests focus particularly on invalidated
approvals, partial failures, concurrent actions, unknown publishing outcomes,
and repeated payment notifications.

## 11. Security and operation

- All write operations check the user and existing workshop permissions on the
  server. Teachers do not need additional administrator approval for workshops
  they may manage; the exact existing access rules still need to be checked.
- Generation and approval/publishing are separate actions. An approval status
  sent by the browser is not proof of authorization.
- Production keys, OAuth tokens, and Firebase credentials remain on the server.
  A future `.env.example` contains placeholders only.
- Privileged Firebase access requires its own authorization checks; frontend
  visibility is not access control.
- Logs contain identifiers, statuses, error classes, and necessary runtime data.
  Secrets, full payment payloads, and participant data are not logged.
- Public fixtures and screenshots use synthetic or anonymized data and exclude
  private project configuration.
- Workshop text cannot control tools, network destinations, or secrets. Booking
  and image URLs come from validated configuration or integration data.
- Required image permissions and presentation rules are respected per channel.

## 12. Open questions and when to resolve them

The merged documentation records the shared foundation and its open questions.
Each question is decided or verified before the implementation that depends on
it. The minimal Python package foundation was built independently of platform
access, tracking, and production Firebase integration.

| Topic | Next step | Required before |
| --- | --- | --- |
| Python runtime | Python 3.13 is the chosen and verified package baseline; metadata uses `>=3.13`. Other Python versions and Firebase runtimes remain unverified | Verify the target runtime before Firebase integration |
| Workshop data model | The [provisional input contract](workshop-data-contract.md#provisional-validation-contract) is implemented and tested offline. Obtain sanitized source evidence and verify identity/version, public-copy selection, pricing, exceptions and aggregates; the [Mal-Yoga pilot](workshop-data-contract.md#mal-yoga-pilot-input-proposal) remains incomplete | Accepting a real pilot snapshot or claiming source compatibility |
| Concrete data mapping | Check field names, versioning, stored date/price types, route-specific registration/payment event and timestamp, quota units/counts, permissions, and image paths; apply the confirmed studio defaults and full-day deadline rule | Implementing the respective Firebase integration |
| Execution | Proposal: short, separate server-side actions per channel; measure runtime and define behavior when a request is canceled or the app is closed | Integrating generation into the teacher app and promising background execution |
| Tracking | The 90-day window and workshop scope are decided; define identification, consent, retention, deletion, and what counts as a booking | Implementing and storing attribution linked to individual visitors |
| Evaluation | Define the scoring rubric, quality threshold, and acceptable correction effort | Evaluating the first generated copy and comparing prompt versions |
| Google access | Owner access is available; verify separate API approval, OAuth, and usable profile locations; apply for access early | Live verification and acceptance of the Google publishing adapter |
| Other channels | Check concrete required fields and image rules; set up provider accounts and measure listing time | Field rules before the respective channel draft; access and time measurement before its pilot acceptance |
| Model | Select the model and version pinning based on the first generation case | First live generation and comparative evaluation |

An outstanding integration must not be documented as an existing feature.
Changes to confirmed scope are decided with the user; routine details within an
approved issue are resolved without reopening fundamental decisions.
