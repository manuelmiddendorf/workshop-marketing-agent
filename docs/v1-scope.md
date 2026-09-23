# V1 scope: workshop-marketing-agent

As of September 18, 2026. Status: **Draft for joint review; package foundation implemented**.

This document collects the requirements confirmed during the architecture
discussion. The proposed technical design is in [architecture.md](architecture.md),
with open details listed separately. Only the minimal Python package foundation
is implemented; V1 functionality has not been implemented yet.

## 1. Product goal

Existing workshops at a yoga studio should be promoted reliably with little
active work. The studio runs approximately 1–2 workshops per month with 2–3
teachers. Promotion starts 2–3 months before a workshop. Participants currently
come mainly from current or former studio members.

The project should also serve as a public Python portfolio with clear
architecture, controlled LLM use, evaluation, tests, and integration with a real
application. It initially demonstrates applied LLM and software engineering;
training a custom model is not a V1 goal.

## 2. Confirmed decisions

- A Python package instead of a TypeScript/npm package; it does not replace the
  teacher app. The existing web interface can retain its implementation language.
- Workshop data, including early-bird information, comes from Firestore. The
  integration maps it into the package's independent data model.
- Workshops normally use Berlin local time (`Europe/Berlin`) and EUR. These are
  explicit studio defaults; any workshop exceptions must be preserved.
- Prices normally apply per person. Parent-child yoga's base price covers one
  adult and one child, with additional people available during checkout. Pair
  pricing has not been used yet but may be needed in the future.
- Early-bird deadlines include the entire stated local day. Eligibility requires
  availability within any applicable discounted-place quota and the respective
  qualifying event: successful payment on the public website, registration in
  the member area. Member places are booked immediately at registration; payment
  follows independently by bank transfer, without Stripe or PayPal. Quotas are
  normally limited. Local parent-child booking code counts
  one family booking including extra people as one quota unit, separately from
  person capacity. Other workshop units and authoritative payment/counting data
  still need verification; see the
  [booking-code findings](workshop-data-contract.md#parent-child-booking-code-inspection).
- Preserve the member area's existing payment-receipt reminder to encourage
  prompt bank transfer. Member bookings, capacity use, applicable early-bird
  usage, and attribution count at registration without separate staff
  confirmation of payment. Counting a booking does not mark it as paid.
- The detailed original copy is preserved unless a revision is explicitly
  requested. Channel copy is derived from it in a warm, clear, and suitable style.
- Workshop data contains separate member and public/non-member descriptions.
  External marketing uses public copy, with no automatic fallback to member copy.
- Conflicts between structured data and text must be resolved before affected
  content is published.
- Teachers can fully manage the workshops they have access to and approve posts
  themselves; additional administrator approval is not required.
- Copy can be edited directly or revised by the LLM using a short instruction.
  Individual channels can be regenerated or disabled.
- One draft is preselected; optional variants allow up to three suggestions in
  total. The current selection must be explicitly approved.
- Multiple marketing rounds and updates are planned. Teachers initiate reminders
  in V1; they are not automatically scheduled.
- Existing workshop and website images form a shared image pool. The workshop
  image is the default; suitable alternatives can be selected.
- Links, recorded clicks, and confirmed bookings should be attributable. The
  latest recorded valid marketing click for the booked workshop within 90 days
  before the confirmed booking receives credit; otherwise the source remains
  unknown. The default window is configurable.

## 3. Channels

| Channel | Role in V1 | Publishing route |
| --- | --- | --- |
| Studio workshop page | Authoritative information and booking destination; integrate existing publishing | Through the existing app; no automatic replacement of the original copy |
| Google Business Profile | First external automatic channel | Official API after approval; access is a prerequisite |
| Rausgegangen | Free listing with the studio's own booking URL | Prepared fields, entry through the provider portal, and status confirmation |
| HIMBEER / Berlin mit Kind | Free course listings for suitable children's, parent, and family activities | Prepared fields for the course directory and status confirmation |

Channels are selected per workshop. Not every workshop belongs on every channel.
New reminders do not automatically create duplicate calendar listings. Existing
listings are updated when that is the appropriate supported workflow.

**Verified prerequisites and limitations:**

- Google supports [creating and editing posts](https://developers.google.com/my-business/content/posts-data).
  Studio owner access is confirmed; separate
  [API approval](https://developers.google.com/my-business/content/prereqs) is still pending.
- Rausgegangen confirms [free event listings](https://zentrale.rausgegangen.de/)
  and [external booking links](https://rausgegangen-assist.freshdesk.com/support/solutions/articles/44002486114-kann-ich-einen-externen-ticketlink-hinterlegen-).
  Its own ticketing service is not required for V1.
- HIMBEER describes [free course listings and one-off workshops](https://berlinmitkind.de/anleitung-kurse/).
  The course directory is separate from the editorial event calendar. Its guide
  does not provide for embedded images in course listings.
- No suitable public, official publishing API has been verified for Rausgegangen
  or HIMBEER. Automatic publishing is not promised.

Sources for channel selection were checked on September 16–17, 2026. Platform
terms and technical capabilities must be checked again against current official
information before integration.

## 4. User workflow

1. A teacher opens an existing workshop and starts "Marketing erstellen"
   (create marketing content).
2. The system validates the input and recommends suitable channels with explanations.
3. Each channel shows a validated draft, the appropriate publishing route, a
   complete booking link, and an image suggestion where applicable.
4. The teacher can edit copy directly, enter a revision request, choose a variant
   or another image, and disable channels.
5. After review, the exact version is approved.
6. Google publishes automatically; for other channels, fields are copied and
   submission or publication is subsequently confirmed.
7. The overview shows timestamps, errors, retry options, and the last published
   version or public URL for each channel.
8. Later marketing rounds and updates use current facts and preserve the history
   of previous publications.

An editorial inquiry or submitted form is not automatically a publication.
The status must not overstate what is known.

## 5. Images and content

Workshop image filenames are already stored in Firestore. The files are in
folders in the teacher app and website. The image pool catalogs suitable existing
images and their permitted uses; copying all files is unnecessary.

Topics such as yoga, meditation, wellness, children, parent-child activities,
massage, and the studio help selection. An image may be reused for multiple
workshops when its permissions allow it. Channels without image requirements
remain text-based.

Example revision requests remain in the product's German language: "Bitte kürzer"
(please shorten), "Herzlicher formulieren" (make it warmer), or "Betone, dass man
auch alleine teilnehmen kann" (emphasize that people can attend on their own),
provided that statement is supported. Revisions must not introduce new facts
or stronger claims about benefits.

## 6. Tracking

V1 includes channel-specific links, basic click recording, and attribution to
existing confirmed bookings. The public website already uses Firebase Cloud
Functions with PayPal and Stripe. Member bookings remain registration-based,
with independent bank transfer. Preserve both booking routes; see the
[booking integration findings](booking-integration-findings.md).

At minimum, links need workshop, campaign, round, channel, and variant references,
plus UTM values. The confirmed attribution rule is:

- The latest valid marketing click for the same workshop, no more than 90 days
  before the confirmed booking; measured from the booking, not the workshop date.
- A new valid marketing click for that workshop replaces the previous one and
  starts a new window from its click timestamp.
- Direct visits do not overwrite the source or extend the window.
- Clicks for other workshops are not transferred. Without a matching touchpoint,
  the source remains unknown.

The 90-day window is a configurable starting value matching the 2–3 month
promotion period. It can later be reviewed using observed booking delays.
Visitor recognition, data storage, and consent are defined before production
tracking; the 90 days do not establish a data retention period. V1 needs
verifiable reporting but does not require a dedicated analytics dashboard.

## 7. Success and acceptance

**Confirmed time targets after initial setup:**

- Approximately one minute of active work per automatically published channel.
- At most approximately three minutes per channel requiring manual form entry.
- At most ten minutes of active work for a complete marketing round for one
  workshop across all selected channels, including typical copy corrections.

Processing time and waiting for external platforms are measured separately.
These figures are pilot targets, not demonstrated performance claims. Copy
should need few corrections. More participants are the business goal; with few
workshops, changes in booking numbers must be interpreted cautiously.

**Proposed verifiable acceptance criteria:**

- Reference workshops pass through input validation, channel selection,
  generation, and review.
- Required fields, formats, known facts, and price deadlines pass the agreed
  hard checks; results with unresolved conflicts cannot be published.
- Direct editing, LLM revision, image changes, and channel disabling work.
- With approved access, Google can create and update an approved post. A manual
  fallback does not replace this integration goal.
- Rausgegangen and HIMBEER listings can be prepared, confirmed, and updated;
  actual listing time is measured with suitable workshops.
- Partial failures preserve successful results. Double-clicks and retries do
  not create uncontrolled additional posts.
- At least one controlled booking flow checks attribution and counting each
  booking once despite repeated payment notifications.
- Targeted attribution cases check the 90-day boundary, a more recent marketing
  click, a later direct visit, and clicks for another workshop.
- A small qualitative reference evaluation and a documented comparison of two
  prompt versions demonstrate how changes are assessed.
- The pilot runs in the existing teacher app. Missing access and untested
  functionality are documented as outstanding.

Specific quality thresholds and the pilot sample size remain to be defined.

## 8. Later or outside V1

- Kindaling: explicitly deferred; fees and separate booking flows would need
  clarification before inclusion.
- visitBerlin: a possible later free addition.
- nebenan.de, Berlin.de, Instagram, Facebook, and other platforms: no committed
  V1 integrations.
- Facebook groups remain manual if added later; no bots.
- Scheduled reminders, autonomous publishing without approval, autonomous browser
  control, and workarounds for missing APIs.
- New workshop ideas, reels, videos, and ongoing creation of new images.
- Paid ads, budget optimization, automatic learning from bookings, and a large dashboard.
- A large SaaS platform, additional tenant management, and agent frameworks.

The architecture allows later extensions through small channel adapters without
implementing those extensions in V1.

## 9. Development and documentation

The merged architecture and scope documentation records the agreed foundation
and open questions. The minimal Python package foundation is implemented;
remaining questions are resolved or explicitly assigned to a later issue before
the implementation that depends on them starts.

Development follows individual issues with clear acceptance criteria, small
changes, appropriate tests, user review, a pull request, and a merge. Review,
state, and evaluation rules are clarified before the first complete generation
flow. The Firebase integration is checked early with a small data example so
that integration assumptions do not surface only at the end.

Project documentation, code comments, new issues, pull requests, and commit
messages use English. Workshop content, generated marketing copy, and the
teacher-facing interface use German; German example data is appropriate.
Discussions and Python explanations with the project owner remain in German.

The README and further documentation grow alongside actual functionality.
A few justified architectural decisions may later be recorded as ADRs. This
document is not an instruction to implement all of V1 in a single step.
