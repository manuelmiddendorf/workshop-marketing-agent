# Workshop source data and proposed input contract

As of September 18, 2026. Status: **Business rules partly confirmed; technical contract proposed; no models or mapping implemented**.

This document prepares the independent workshop input described in
[architecture section 3](architecture.md#3-data-and-factual-accuracy) and
the [confirmed V1 scope](v1-scope.md#2-confirmed-decisions). Field names below
are proposals, not an approved schema.

The [shared booking contract](workshop-booking-contract.md) records the later
Task 2a decisions on shared quotas, counting units, payment timing and goodwill,
with proposed booking records and transitions. This document remains the marketing
input/source mapping; it does not assign booking implementation to the Python package.

## Evidence and limits

- **Confirmed requirements:** preserve original copy, keep missing facts unknown,
  and resolve conflicts before publishing affected content. The architecture
  records structured early-bird data as existing project knowledge.
- **Owner-confirmed business rules:** the September 18 clarification below defines
  studio defaults, price scope, early-bird eligibility, and separate member/public
  descriptions. It does not verify stored field types or payment implementation.
- **Observed application code:** static, read-only inspection of
  `msn/src/components/Workshops.vue`. The application was not run. Editor defaults,
  accepted values, preview fallbacks, and save operations do not establish which
  fields or types occur in stored documents.
- **Observed booking code:** read-only inspection of local
  `myfunctions/functions/kinderYoga` and the separate `workshops` booking path on
  September 18. Parent-child quota counting is described below. The functions
  were not run, and the deployed version and live counts were not verified.
- **Observed website and member code:** subsequent inspection traced the public
  website export/payment path and the member registration/bank-transfer path.
  See the [booking integration findings](booking-integration-findings.md) for
  evidence and gaps; member bookings do not require online payment.
- **Verified document data:** none. No sanitized real workshop document was
  supplied or reviewed, and production Firestore was not accessed.
- **Reconstructed examples:** the two JSON files below contain invented German
  content using shapes found in the component. They do not verify production data.

The inspected `msn` checkout was on `master` at
`dc6d35627b5daeb101944a2b73c9369b66228be7`, with existing local changes.
`Workshops.vue` was **untracked**, so that commit does not contain the inspected
file. Its 3,970-line local snapshot had SHA-256
`0cb368a82e13dfcc276038de8beabf531fdaa9ad99522a20d0e5efc7ddca3ff0`.
References below are local snapshot references, not repository permalinks.
No teacher-app files were changed.

## Business rules confirmed by the owner

The owner clarified the following rules on September 18, 2026:

- **Studio defaults:** workshops normally use Berlin local time, including summer
  and winter time, and euro prices. Represent these defaults as explicit studio
  configuration (`Europe/Berlin`, `EUR`). They are not values discovered in a
  source field. Preserve and review any explicit workshop exception.
- **Price scope:** prices normally apply per person. For parent-child yoga, the
  base price covers one adult and one child; additional people can be added during
  checkout. Do not present that base price as a price per individual or assume
  extra people are included for free. Pair pricing has not been used yet but is
  a possible future case; do not describe it as existing source data.
- **Early-bird deadline:** the stated final calendar day is fully included.
  On the public website, successful payment is the qualifying event, not checkout
  start. In the member area, registration books the place immediately; payment
  follows by bank transfer independently. The owner's later clarification
  replaces the earlier blanket payment-success criterion. The full-day rule
  implies an exclusive cutoff for ordinary eligibility at the start of the next
  local calendar day. Authoritative event timestamps still need verification.
- **Member payment workflow:** preserve the existing payment-receipt reminder;
  the owner uses it to encourage prompt bank transfer. Registration already
  counts as a booking and uses capacity and any applicable early-bird quota.
  No separate staff confirmation of transfer receipt is required. The reminder
  is not a payment-status check, and registration alone does not establish that
  a member has paid.
- **Shared quota and capacity:** member/public bookings for the same workshop
  always share early-bird quota and person capacity. General workshops consume
  one discount unit per discounted participant; parent-child bookings consume one
  per family including extras, while capacity counts every person. These Task 2a
  decisions replace the earlier uncertainty about other units and channel pools.
  Ordinary eligibility requires applicable quota availability. Exact limits and
  authoritative usage still need integration evidence; defaults do not establish them.
- **Late notifications and goodwill:** payment-success time controls public
  eligibility; timely success remains timely when notification arrives later.
  Genuinely late success for an early-bird quote requires review. Usual goodwill
  acceptance keeps the paid amount without a surcharge, remains early bird, and
  consumes the applicable units exactly once. Do not automatically refund or
  request extra payment. Room capacity must be respected; permission to exceed
  exhausted early-bird quota is unresolved, as detailed in the booking contract.
- **Separate audiences:** workshop data contains different descriptions for
  members and for the public/non-members. External marketing uses the public
  description. Preserve member copy separately; do not automatically substitute
  it when public copy is missing. Text selection is separate from approval to publish.

These confirmations do not approve every proposed field name, validation rule,
or representation below. No sanitized real document has been reviewed yet.

## Source evidence

References S1–S10 use the inspected `msn/src/components/Workshops.vue` snapshot.
Ranges are inclusive; the function names also help locate the code after edits.

| ID | Lines | Observed code |
| --- | --- | --- |
| S1 | 3127–3171 | `defaultWorkshopDoc`: empty text, numeric `capacity: 0`, booleans, nested `publicContent` and `earlyBird`, empty registration arrays |
| S2 | 1344–1418; 2997–3003 | Selected save ID; preview title, descriptions, public aliases, time, location, price; true-like flag handling |
| S3 | 1135–1137; 2728–2760 | German locale selection; list title and date precedence; timestamp-like and other date inputs; time, location, and price fallbacks |
| S4 | 3181–3233 | `normalizeEditorValue` and `normalizeEditorDoc`: recursive normalization, default merging, and removal of source metadata |
| S5 | 3290–3302; 3440–3446; 3537–3608 | JSON `_id` suggestion, generated ID, `cleanForSave`, and save to `MgEvents/{docId}` with metadata and merge enabled |
| S6 | 3057–3112; 3317–3373; 3385–3426 | Boolean, numeric, date, string, and arbitrary JSON editing; blank numeric input becomes zero; adding fields |
| S7 | 1420–1437; 2373–2384 | Preview image resolution and different tile-image precedence |
| S8 | 374–388; 2336–2354; 2624–2635 | Fields labeled relevant to web sync; separate tile route object using `WorkshopDetail` and `params.workshopName` |
| S9 | 2813–2834; 2866–2870; 3005–3055 | Document-key identity, grouping, registration counting, and legacy/operational field names |
| S10 | 573–648 | HTML rendering of original and public descriptions; public description shown separately when different |

The editor accepts arbitrary field names and JSON values. A string default is
evidence of an editor convention, not a string-only production constraint.
No structured audience, age, currency, time-zone, or separate start/end fields
were identified in this component; their absence elsewhere is not established.

## Parent-child booking code inspection

The following references describe the local `myfunctions/functions` files inspected
on September 18, 2026. `msn/myfunctions` points to that same directory. No Git
revision was available for this folder. These are local code observations, not
evidence of deployed behavior, live availability, or approved changes to billing.

| Source file | Lines | Observed behavior |
| --- | --- | --- |
| `kinderYoga/KYStripeWebhook.js` | 58–89; 212–235; 274–285 | Processes `checkout.session.completed` for a pending registration. For `single` or `bundle3`, increases `earlyBirdQuotaUsed` by exactly one if the current phase is early bird and a numeric quota exists, then marks the registration paid in the transaction. Extra-person counts do not multiply the quota increment. |
| `kinderYoga/KYCapturePayPalOrder.js` | 110–119; 148–162; 316–365 | Requires PayPal capture status `COMPLETED` and a pending registration; uses the same one-per-family quota increment and paid transition. |
| `kinderYoga/KYCreateAddonBooking.js` | 76–80; 138–169 | Later extra participants become an `addon` registration linked to a paid/confirmed parent. That booking type is excluded from the quota increment in both payment handlers. |
| `kinderYoga/common.js` | 140–162; 203–225 | Single-booking price is the base price plus separate child/adult add-on charges. Capacity counts two base people plus extras; an `addon` counts only its extra people. |
| `kinderYoga/KYBookWithPass.js` | 172–215 | Redeeming an existing pass can also consume one early-bird quota unit for the course and creates a confirmed registration without a new payment. |

Thus, an eligible family booking with two adults and two children consumes **one
early-bird quota unit and four person-capacity places**. Adding people later
does not consume another quota unit. Remaining discounted family bookings and
remaining person-capacity places must stay distinct in marketing data and copy.

Several integration details remain separate from this confirmed counting unit:

- `KYCreateBooking.js` (285–315) creates a pending registration without updating
  the quota. The payment handlers update it later; they recompute eligibility
  using their processing time (`new Date()`), not a recorded payment-success
  timestamp. A price/phase change between checkout creation and processing can
  cause their price check to reject the update after payment
  (`KYStripeWebhook.js`, 153–181; `KYCapturePayPalOrder.js`, 258–287).
- `common.js` (50–69) compares the current instant with the stored
  `earlyBirdUntil`. It does not extend that value to the end of a Berlin calendar
  day. Full-day eligibility depends on how that timestamp is written and still
  needs verification against the owner's rule.
- Pass redemption is an existing alternative to a new payment. Bundle pricing
  also has separate rules (`common.js`, 90–137). These historical implementation
  observations do not establish a complete mapping of every pass/bundle case to
  the confirmed counting rules, or imply that each consumed unit represents a
  new payment. The Stripe handler uses the completion event above
  without an explicit `payment_status` check; its checkout currently allows only
  cards (`KYCreateStripeCheckout.js`, 99–101). This inspection is not a complete
  payment-success audit.
- The general workshop path is separate: `workshops/WSCreateBooking.js` (60–89)
  reads a price from `workshops/catalog.js` and writes `workshopRegistrations`.
  It does not read the editor's `MgEvents.earlyBird` fields or update this quota.
  Parent-child handlers instead use `KinderYoga` and `registrations`. Their field
  names and counting rule must not be silently applied to all `MgEvents` records.

## Proposed field mapping

Representations use ordinary strings, numbers, booleans, lists, and records;
they contain no Firebase objects or application-specific field names. A source
timestamp must be converted by the future integration, not exposed as a Firebase
class. Text and raw evidence remain unchanged; normalized facts are separate.

| Marketing input | Observed source names, shapes, and evidence | Proposed representation and treatment of gaps |
| --- | --- | --- |
| Identity | Document key is the list item ID. Save writes string `_id` and `_path`; imported `_id` only suggests the selected target ID (S2, S5, S9). Legacy `id` and `slug` are listed, without an identity guarantee (S9). | Opaque `workshop_id` string from a verified source identity. Compare metadata with the document key; disagreement is a conflict. Do not derive a new identity from a changed title or date. Missing identity blocks a usable snapshot. |
| Source version | Save assigns a server timestamp to `updatedAt`, and `createdAt` for a new document. Editor normalization removes both (S4, S5). No revision counter is identified. | Opaque `source_version` string supplied by the integration. Do not substitute `createdAt`, the file hash above, or a fabricated revision. Missing version prevents source-version checks and approval; the versioning mechanism remains open. |
| Title | Empty-string `title` default; `title`, `titel`, `name` are truthy fallbacks with different precedence (S1–S3). Alias types are not enforced. | `title` string from a reviewed source. Preserve its text; absent/blank or incompatible types are unresolved, not the UI placeholder `Ohne Titel`. Conflicting nonempty aliases require review. |
| Original description and summary | `description`, `beschreibung`, `text`; `short`, `summary`, `info` (S1, S2). HTML is rendered for description and `info` (S10). Defaults are strings; generic JSON editing permits other shapes (S6). | Separate `original_description` and `original_summary`, retaining the selected original text, markup, and line breaks. Preserve alternate originals in source evidence when they differ. Missing text stays unknown; do not concatenate or rewrite aliases to hide a conflict. |
| Content for public promotion | String defaults in `publicContent.short` and `.description`, plus flat public aliases. Boolean defaults `publicForNonMembers` and `allowNonMembers` are false; the preview also accepts true-like strings/numbers (S1, S2). | Separate `public_summary`, `public_description`, and an explicit public-eligibility value, with unknown distinct from false. Only reviewed public content is eligible for drafting. Public eligibility is not approval to publish. See the content rules below. |
| Workshop date | Empty-string `date`; `datum` and `date` readers accept `toDate()`, truthy `seconds`, or values passed to Moment (S1, S3). The editor can produce `YYYY-MM-DD` strings (S4, S6). | `local_date` as a calendar date, retaining source precision. No midnight or instant is invented for a date-only value. Conflicting aliases, invalid values, and already-truncated timestamps remain unresolved. |
| Start/end times and zone | Empty-string `time`, fallback `uhrzeit`; preview treats it as display text and appends `Uhr`. No separate endpoints or zone field identified (S1, S2). | Preserve `schedule_text`; propose separate `start_time`, `end_time`, and IANA `time_zone`. Missing times remain unknown; do not infer an end time or overnight rollover. The integration may supply the confirmed studio default `Europe/Berlin` from configuration, preserving explicit exceptions and recording that the zone came from configuration rather than the source document. |
| Location | Empty-string `location`; aliases `ort`, `studio`, `standort`. `studio`/`standort` also group list entries (S1–S3, S9). | `location_text`; optional structured address only from verified additional data. A studio code or grouping label is not an address. Missing location remains unknown. |
| Audience and age | No dedicated fields identified. `tags` defaults to `["Workshop"]`; public access flags describe membership access, not age or audience (S1, S2). | Optional `audience_text`, `minimum_age`, `maximum_age`, and any required accompanying-adult condition. Carry an explicit, reviewed statement from suitable source content; do not infer suitability from a title, tag, image, or public flag. Numeric bounds stay unknown without evidence and confirmed units. |
| Included services | No dedicated field identified; descriptive text may contain explicit statements (S1, S2). | Optional `included_services` from reviewed statements only. Missing evidence means unknown, not an empty list proving that nothing is included. |
| Regular price and currency | `price` defaults to a string; `preis` is a fallback. `beitrag` defaults to an empty string but uses a numeric editor; `priceNumber` is also listed as numeric (S1, S2, S3, S6). No dedicated currency field identified. | Retain `regular_price_text`; propose money as an exact decimal amount string plus currency code and explicit pricing scope. The integration may supply the confirmed studio default EUR from configuration; a bare amount alone does not establish currency. Distinguish the usual per-person price from the parent-child base package (one adult and one child) and additional checkout items. Future pair pricing must remain distinguishable. Missing price is not free; multiple rates or contradictory fields require clarification. |
| Early-bird price and deadline | `earlyBird` defaults to an object with string `price` and `until`. It is edited as JSON and merged with defaults on load (S1, S4, S6). Task 1 replaced the member view's browser-local calculation with a shared Berlin next-day cutoff and reactive refresh; the historical public export lacked a structured deadline (see booking findings). | Separate early-bird money and deadline from the regular price. Preserve raw deadline text. For ordinary eligibility with a valid date-only deadline, apply the confirmed full-day rule and zone to member registration or successful public payment. Delayed notification does not change payment time; goodwill is a recorded booking exception, not an extended offer. Missing/empty object or partial offer is not proof that no offer exists. No discount claim without verified price, currency, deadline, and applicable quota/conditions. |
| Early-bird restrictions | Numeric default `earlyBird.limit: 0` and string `earlyBird.note: ""` (S1, S6). The member view treats nonpositive limits as uncapped and counts `angemeldet` plus `vorgemerkt`. The separate parent-child path uses `earlyBirdQuotaTotal`, `earlyBirdQuotaUsed`, and `earlyBirdQuotaRemaining` as described above. | Preserve `restriction_text`; limits and authoritative usage need evidence. Shared member/public quota and person capacity are confirmed: one unit per discounted general-workshop participant, one per parent-child family including extras; capacity counts all people. Member registration counts without transfer confirmation. Goodwill still consumes applicable early-bird units once. Current UI defaults do not approve zero as a normalized unlimited offer. Implementation and reconciliation remain open; unknown notes/caps do not authorize an unrestricted offer. |
| Canonical booking URL | Empty-string `stripeUrl`/`paypalUrl` defaults and web-sync labels (S1, S8); `ctaLink`/`link` merely appear among legacy fields (S9). Separate tiles store a route name and workshop parameter (S8), not a canonical public URL. | `booking_url` from a verified public workshop booking page or an explicitly configured route. Payment-related URLs are not a substitute. Do not invent a website path from `_id` or use an unverified `ctaLink`. Missing canonical destination stays unknown and blocks a booking call to action. |
| Images | `imageJpg`, `imageUrl` string defaults; preview and tiles also consume `image`. URLs, static paths, and filenames are handled. `imageWebp`/`imagePng` are only listed as legacy fields (S1, S7, S9). | `image_references` containing opaque references plus known metadata. Preserve references; do not imply that a filename is a public URL, an existing asset, or licensed for promotion. Selection conflicts and resolution/rights require verification. |
| Availability and active status | `capacity: 0`, `isActive: true`, `active: true`, `archived: false` are defaults. `maxnr` is a legacy numeric field. Registration counts use several sources (S1, S6, S9). | Availability remains unknown without an authoritative, current aggregate. Do not equate defaults or active flags with available seats, subtract partial counts, or include registration arrays/participant details in the marketing input. Conflicting status flags need integration review. |

## Content, conflicts, and time semantics

**Observed behavior:** many readers use JavaScript truthiness, so zero and blank
strings can fall through to another field. This is not a safe general rule for
missing data. The list favors `titel` over `title`, while the preview reverses
that order (S2, S3). Date reading favors `datum`, while ID generation favors
`date` (S3, S5). The preview price adds `beitrag` as a fallback, but the list does
not (S2, S3). Preview images favor `imageJpg`; tile images favor `image` (S7).
These are observed choices, not an approved mapping precedence.

Public-summary fallbacks are `publicContent.short`, `publicShort`, `shortPublic`,
`shortForNonMembers`, `nonMemberShort`, then `short`, `summary`, `info`.
Public-description fallbacks are `publicContent.description`, `publicDescription`,
`descriptionPublic`, `descriptionForNonMembers`, `nonMemberDescription`, then
`description` only; the ordinary description also supports `beschreibung` and
`text` (S2). The two public flags are combined with OR, so an explicit false can
be masked by another true-like value. The preview is not proof that a fallback
text was intended for public promotion.

**Proposed mapping rules:** inspect original source data before editor defaults
are added. Distinguish absent, null, empty, invalid, and conflicting evidence;
do not manufacture zero, false, an empty offer, or an empty participant count.
The simplest proposed normalized convention is null for an unknown optional
fact, with the reason retained in mapping diagnostics outside the marketing
facts. Confirm how an explicitly absent offer differs from an unknown offer
before models are implemented. Different nonempty aliases must be compared and
reported, not silently overwritten; differently targeted public/member copy is
kept separate rather than treated as interchangeable text.

Preserve original copy exactly. A separate plain-text view may decode HTML and
remove presentation artifacts for drafting, without writing back to the original.
Member-only instructions do not become public content through fallback. If no
explicitly suitable public text exists, its suitability needs review before
drafting from it. Review statements about audience, ages, included services, and
pricing conditions against the source; absence does not justify new claims.
Contradictions between prose and structured fields block affected content.

**Confirmed deadline rule and unresolved source mapping:** normalization converts valid Date/`toDate()` values
to `toISOString().slice(0, 10)` and thereby discards time and offset information;
other date display paths use Moment without an explicit zone (S3, S4, S6).
German locale selection does not establish a time zone. Use the original source,
not a reconstructed instant from editor text. Berlin time is now an explicitly
confirmed studio default. For a valid date-only early-bird `until`, the whole
local day is included; ordinary eligibility requires member registration or
successful public payment before the next local day starts. Preserve explicit workshop
exceptions. Do not append UTC,
borrow the workshop start time, or reinterpret a truncated timestamp as intact.
The qualifying events and delayed-notification rule are settled: use actual
payment-success time, not receipt/processing time. Authoritative timestamp sources
and implementation still need verification. Goodwill acceptance records a separate
exception without changing the original time or deadline. Shared quotas and both
general/family units are confirmed; mapping and authoritative usage remain
unverified. Task 1 fixes member display timing only. Editor fields alone do not
prove current availability or a remaining number of discounted places. See the
[booking contract](workshop-booking-contract.md#5-existing-data-and-ownership)
for reconciliation and protection of booking state from teacher content saves.

## Synthetic source examples

- [With early-bird information](../examples/workshops/synthetic-with-early-bird.json):
  invented family workshop, separate original/public copy, display price and
  nested early-bird strings with a numeric limit. Audience and ages occur only
  in the invented copy. The raw date-only deadline contains no zone; a future
  normalized input can use the confirmed studio configuration and full-day rule.
  The invented note about six bookings does not establish a production quota unit.
- [Without early-bird information](../examples/workshops/synthetic-without-early-bird.json):
  invented member-facing workshop using supported aliases, with blank time and
  price, default-shaped zero capacity, and no `earlyBird` field. Omission means
  no early-bird evidence in this reconstruction, not a verified absence of offers.

Both are **partial synthetic source reconstructions**, not exports, validated
package inputs, or documents to upload. `_id`/`_path` follow the save shape but
are invented. Missing `updatedAt` means no source version is established; no
Firebase timestamp serialization is fabricated. Example image filenames are
invented references, with no asset or usage-rights claim. No participant data,
payment identifiers, payment URLs, or private contact details are included.
The source examples do not embed configured zone/currency defaults as if these
were observed source fields. Missing canonical booking URL and other facts are
not filled with guesses. Editor normalization would add defaults, including an empty
early-bird object to the second example; that would not add source evidence.

## Questions before workshop models

These technical contract decisions remain open after the business clarification:

1. **Required fields and unknowns:** agree the smallest useful input, when identity
   and source version must be present, and how unknown facts differ from invalid
   or conflicting input and an explicitly confirmed absence of an offer.
2. **Schedule and offer precision:** agree whether first models retain partial
   local dates/times and date-only offer deadlines or require resolved instants.
   Agree marketing money precision and representation of the confirmed pricing
   scopes and units without suggesting a usable discount. The booking contract
   proposes integer minor units for agreed amounts; the marketing input above
   proposes exact decimal strings, requiring lossless mapping. Shared quotas,
   general/family units, defaults, cutoff and qualifying events are settled.
3. **Content representation:** separate member/public descriptions and use of
   public copy for external promotion are settled. Choose the concrete text
   fields and public-eligibility/unknown representation. Missing public copy does
   not authorize automatic fallback to member-facing content.
4. **Evidence for supported shapes:** review an offline, sanitized real workshop
   example, especially price scope and early-bird restrictions, before claiming
   compatibility with stored data. If models proceed before that evidence exists,
   explicitly limit them to the agreed provisional contract.

## Questions for later integration

Once the representations above are agreed, their concrete sources can be
verified in the integration task:

- Confirm document-key/metadata consistency, actual stored types and legacy
  aliases, writer coverage of `updatedAt`, and a reliable source-version token.
  Merge saves do not establish that obsolete alias fields disappear (S5).
- Apply the confirmed studio defaults, pricing scopes, full-day deadline, and
  route-specific registration/payment rule. Verify explicit exceptions, actual
  stored price/offer shapes, authoritative event timestamps, and implementation
  of the confirmed shared counting rules and usage data.
  Use the parent-child counting evidence above while verifying the deployed
  version, current counts, pass/bundle cases, and the separate general-workshop
  mapping. This verifies implementation of the settled business rules rather
  than reopening those rules.
- Resolve the public website route and canonical booking page, web-sync behavior,
  public access/status rules, and conflicts in actual public/member content.
- Resolve studio codes/addresses, image locations, rights, attribution and
  dimensions, and authoritative availability aggregates without participant data.

This issue does not require production access or implement any of those checks.
