# Shared workshop booking contract

September 18, 2026 — **Confirmed business rules; proposed technical contract.
Documentation only, not implemented or deployed by this task.**

**September 24 status note:** this contract retains its earlier evidence and
the later Task 9 implementation report below. The owner's new-route report is
limited to Mal-Yoga on October 18, 2026; it does not migrate other workshops or
verify deployment. See the [pilot marketing input proposal](workshop-data-contract.md#mal-yoga-pilot-input-proposal)
for current public-page observations and remaining input prerequisites.

## 1. Evidence and scope

- **Confirmed:** the owner's Task 2a decisions below govern booking and counting.
- **Observed locally:** Task 1's member display correction is present in
  `mgyoga/src/composables/useEarlyBirdDeadline.js` and used by
  `src/views/Workshops.vue`: strict Berlin date parsing, next-day exclusive
  cutoff, shared display calculation, timer/resume refresh and cleanup. This is
  a display correction, not persisted pricing or concurrent-booking protection.
  Registration still writes an email to `vorgemerkt`, without price or timestamp.
- **Historical evidence:** [integration findings](booking-integration-findings.md)
  and [source data contract](workshop-data-contract.md#parent-child-booking-code-inspection)
  record prior local public/general and parent-child code inspections. They do
  not establish deployed behavior or live counts. Only the member correction
  and teacher save boundary were rechecked for this documentation task.
- **Proposed:** field names, statuses and transition representation below. No
  Firestore schema, provider API or migration is prescribed. Open decisions are
  listed in section 6; missing evidence remains unknown.

Keep three integration paths distinguishable: member registration with independent
bank transfer; public general workshops with Stripe/PayPal; and the existing
parent-child flow with family pricing, extras and separate pass/bundle behavior.
Its observed pass redemption without a new payment is a compatibility case to
map separately, not a new rule for general workshops or proof of a new payment.
The existing booking applications and Cloud Functions own implementation. This
Python marketing repository is only the documentation home; marketing consumes
approved availability aggregates and confirmed booking references.

## 2. Confirmed rules and counting

For the **same workshop**, member and public bookings always share person capacity
and early-bird quota. Storage paths or payment providers never create separate pools.

Let `P` be all attending people and `E` the applicable discounted units:

| Pricing case | Person-capacity units `P` | Early-bird units `E` |
| --- | --- | --- |
| General workshop, early bird | All participants | One per participant receiving the early-bird price |
| Parent-child early-bird family | All adults and children, including extras | One per family booking, including extras |
| Regular-price booking | All participants | Zero |

Extra people added later to the same family increase capacity by the added people,
not another family early-bird unit. Do not extend family pricing to general workshops.
In the proposal, only a transition into `confirmed` commits `P` and `E`; repeated
operations commit zero additional units. Pending/review records contribute zero.
Legacy contributions require reconciliation, not automatic zeroes (section 5).

- Member registration books immediately and consumes the applicable shared units.
  Bank transfer is independent: no staff payment confirmation is required, and
  registration proves neither receipt nor non-receipt of payment. Preserve the
  intentional payment-reminder wording and original workshop copy. Member and
  public descriptions stay separate.
- Ordinary public early-bird qualification uses **verified payment-success time**,
  never checkout creation, browser return, notification receipt or processing time.
  Defaults are explicitly `Europe/Berlin` and `EUR`. A final date includes its
  whole local day: `qualifying_event_time < start_of_next_local_calendar_day`.
  Advance the calendar, not a fixed 24 hours. Missing payment time needed for
  early-bird qualification requires review; it proves neither timely nor late payment.
- A timely success remains timely despite delayed notification. A genuinely late
  success for an early-bird quote requires manual review. The usual resolution is
  acceptance at the amount already paid, without a surcharge. Do not automatically
  refund or demand additional payment. Regular-price bookings are not made late
  merely by an expired early-bird offer.
- Goodwill acceptance remains classified **early bird** and commits `P` and `E`
  exactly once. Record the exception separately; do not alter payment-success
  time or extend the general deadline. Room capacity must be respected. Task 9 authorizes no quota override: exhausted or unknown room/quota keeps the case in review.
  The owner explicitly permits reasoned goodwill when payment success, association and price are verified but timing remains unknown. Preserve unknown timing; consume the full applicable units exactly once.

## 3. Proposed minimum booking record

Names and grouping are illustrative, not an approved storage design. Timestamps
are instants with UTC/offset semantics and identified evidence, nullable when
unknown or not yet applicable; never invent legacy values. New registration and
confirmation times need trusted booking-service evidence, not the display clock.

| Proposed field(s) | Meaning and constraint |
| --- | --- |
| `booking_id`, `workshop_id` | Stable identities across retries and linked payment attempts; workshop identity must reconcile the existing application paths. |
| `channel`, `source_path` | Member/public channel, plus member registration/public general/parent-child integration path. A provider is payment evidence, not a separate workshop pool. |
| `participant_count`, `capacity_units`, `early_bird_units` | People, required room units, and applicable discount units under section 2. Known units contribute only on confirmation; legacy discount units may be unknown. |
| `agreed_amount_minor`, `currency` | Exact integer minor units, e.g. `9900` and `EUR` for EUR 99.00; no binary floating-point money. Unknown legacy amount is null, not zero. Preserve a public attempt's quoted amount as evidence until acceptance establishes the agreement. |
| `pricing_classification`, `acceptance_reason` | Separate `early_bird` / `regular` / `unknown` from `ordinary_registration` / `ordinary_payment` / `manual_goodwill` / null. A quote may identify an intended class while acceptance remains pending; classification alone commits nothing. |
| `pricing_decision`, `offer_terms` / `offer_version` | Record provisional/accepted/review-required/unknown decision and the actual applicable regular/discount prices, pricing scope, quota unit/limit, final date, resolved cutoff, zone and version evidence. Retain an immutable terms snapshot or resolvable immutable version, not only a mutable offer reference. |
| `created_at`, `registered_at`, `confirmed_at` | Record creation, member qualifying registration, and committed booking confirmation. They are distinct events even when their instants coincide. |
| `payment_succeeded_at`, `notification_received_at`, `processed_at` | Successful payment's evidenced instant, notification arrival, and processing of the operation. Processing time must never substitute for payment time. |
| `booking_status`, `payment_evidence` | Proposed booking states: `pending_payment`, `review_required`, `confirmed`. Independently retain unknown/unrecorded evidence or a verified outcome with provider/transaction reference, amount and currency. A pending payment is not a confirmed failure; failed-attempt evidence cannot erase a different successful payment. Member payment evidence may remain unknown indefinitely, with no staff maintenance obligation. |
| `operation_ref`, `payment_ref`, `review_decision_ref` | Stable registration-command, provider payment/capture, and manual-decision references; retain notification IDs as supporting deduplication evidence. Different notifications about the same payment must not produce another booking or increment. |
| `review_requested_at`, `reviewer_ref`, `review_decided_at`, `review_reason` | Pending review time and, for goodwill acceptance, attributable reviewer, decision instant and reason; link to the original payment and accepted amount. |

Later offer edits must not rewrite recorded quotes, agreed prices or accepted
decisions. Preserve the applicable terms for each attempt and booking; evaluate
payment timing against those terms and shared availability at commitment. Review
adds a decision and its evidence rather than erasing the original quote or event.
Any correction needs an explicit traceable action, not display recomputation.

## 4. Proposed transitions and counting

`ΔC` / `ΔE` below mean changes to **committed** shared person capacity / early-bird
usage. Every first confirmation must atomically check remaining capacity/quota
and record both the booking and its contributions across all channels. This is
required future behavior, not a claim about existing handlers. Successful payment
alone cannot override a full room or silently authorize quota overrun.

| Trigger and required evidence | Booking result | Independent payment evidence | `ΔC`, `ΔE` | Repeat of the operation |
| --- | --- | --- | --- | --- |
| Member registration; stable request, applicable price/terms, room and applicable quota available at registration | Immediately `confirmed`; reason `ordinary_registration` | Unknown unless separately evidenced; transfer not required | `+P`, `+E` (`E=0` if regular) | Return existing booking; `0`, `0` |
| Public checkout creation; stable attempt and quoted terms | `pending_payment`; quote is provisional, not early-bird qualification | Unknown/pending, no success inferred | `0`, `0` | Reuse attempt; no additional counts |
| Verified public success at agreed regular price, or timely early-bird success; identity, amount/currency, required timing and shared availability verified | `confirmed`; reason `ordinary_payment` | Verified success | `+P`, `+E` (`E=0` if regular) | Same payment/booking yields `0`, `0`, even with another notification ID |
| Verified late success for an early-bird quote | `review_required`; preserve original quote and event | Verified success remains recorded | `0`, `0` while awaiting review | Same review case; no counts and no automatic refund/surcharge |
| Verified success with missing timing needed for eligibility or insufficient room/applicable quota, including timely but delayed notifications | `review_required`; retain known timing without labeling timely payment late | Success known; missing facts stay unknown | `0`, `0` | Same unresolved case; not another payment or booking |
| Authorized goodwill decision on a paid late or unknown-time early-bird review case; reviewer/time/reason recorded, room and applicable quota available | `confirmed`, class `early_bird`, reason `manual_goodwill`; accepted amount equals amount paid | Original success evidence and time unchanged | `+P`, `+E` exactly once | Return accepted result; `0`, `0` |
| Goodwill review with full or unknown room/quota | Remain `review_required`: no room overrun or quota override | Success unchanged | `0`, `0` pending resolution | No repeated acceptance or omitted usage |

A member request with no room or applicable quota cannot silently confirm or
switch to a higher price: return the availability conflict for an explicit choice.
Unknown payment outcomes stay pending/reviewable; do not label them failed or
blindly initiate another charge. Successful evidence must not be downgraded by
older or repeated notifications. Idempotency must guard the booking's first
confirmation as well as individual operation references.

This minimum proposal introduces **no temporary holds**. If later proposed to
reduce checkout races, holds need separate units, lifetime and release rules.
They are not paid or committed bookings and do not satisfy the deadline.
Conversion must replace the held allocation with the single committed allocation,
not count both; unknown payment outcomes are not proof that a hold safely failed.

## 5. Existing data and ownership

Array lengths (`angemeldet` plus `vorgemerkt`) cannot establish historical prices,
payment receipt or exact discounted usage: they lack decisions/timestamps, may
overlap, and do not include all public bookings. Existing member registrations
remain bookings without bank-transfer confirmation. Unknown historical prices,
times and discounted units stay unknown; neither today's offer nor an import
timestamp reconstructs them.

Before enabling counters for existing workshops, reconcile workshop identities,
member/public duplicates, all people including family extras, already committed
bookings and available historical discount evidence. Agree explicit treatment of
unresolvable history; never initialize exact usage from guessed prices or array
lengths. Reconciliation and any live migration are subsequent tasks.

| Owner / future write boundary | Responsibility |
| --- | --- |
| Teacher content editor | Original member/public copy, schedule, images and prospective offer terms; explicit reviewed changes to configured limits. A normal content save must exclude registration arrays, booking records/status, usage, payment evidence and manual decisions, even from a stale editor snapshot. |
| Booking/payment service and authorized review actions | Stable records, qualifying events, agreed terms, committed allocations, payment evidence, idempotency and manual decisions. Operational changes use explicit booking actions, not arbitrary content saves. |
| Marketing integration | Read suitable content, verified aggregates and confirmed booking references; no booking/quota ownership and no participant lists in marketing inputs. |

Rechecked local risk: `msn/src/components/Workshops.vue`'s `saveWorkshop` sends
`cleanForSave(this.editorDoc)` through a merge save. Cleaning does not exclude
operational fields; merge does not protect fields included in a stale payload.
The proposed ownership boundary needs enforcement before new operational fields
or counters rely on it. This task changes no editor behavior or security rules.

## 6. Remaining decisions and smallest next task

- **Confirmed Task 9 limit:** no early-bird quota override. Unknown or exhausted
  room/quota blocks acceptance. Any future change requires a separate owner decision;
  accepted early-bird bookings must always consume the full `E`.
  Room overrun is not permitted. Paid cases that cannot be accommodated need a
  separate manual resolution; no automatic refund or surcharge is authorized.
- **Technical verification:** authoritative provider success-time evidence,
  cross-path workshop identity, operation keys, immutable offer versioning,
  reconciliation of legacy unknowns, and pass/bundle mapping remain to be designed
  or verified. Field names/storage are proposals. Holds and cancellation/release
  transitions are separate follow-up scope, not assumed behavior here.
- **Sequence:** secure and version the local `myfunctions` project before backend
  edits; the earlier inspection found no Git repository there. This prerequisite
  was not performed here. The smallest subsequent behavior task is an isolated
  booking-decision function in the existing booking backend for event timing,
  pricing classification, unit calculation and review results, with synthetic
  fixtures and no Firestore/provider calls. Shared atomic writes, editor boundary
  enforcement and reconciliation follow before enabling counters on live workshops.

## 7. Synthetic acceptance scenarios

These are expected outcomes, **not executed application tests**. Independent
fixtures use workshop `ws-demo`, early-bird EUR 99.00 (`9900`), regular EUR 120.00
(`12000`), final date `2026-09-18`, and cutoff `C = 2026-09-19T00:00:00+02:00`
(`2026-09-18T22:00:00Z`). Unless stated otherwise, there is one participant,
shared room/quota is available, and public attempts quote the early-bird amount.

| Scenario | Expected result | Committed change `(ΔC, ΔE)` |
| --- | --- | --- |
| Member registers before `C`; no transfer evidence | Confirmed early bird at registration; payment remains unknown | `(+1, +1)` |
| Public checkout created before `C` | Pending; quote alone grants no eligibility | `(0, 0)` |
| Ordinary verified public success before `C` | Confirmed early bird, EUR 99.00 | `(+1, +1)` |
| Success at `C − 1 ms` / exactly `C` / `C + 1 ms` | Respectively: timely confirmed / late review / late review | `(+1, +1)` / `(0, 0)` / `(0, 0)` |
| Success at `C − 1 ms`, notification processed the next day | Still timely; confirm if shared units remain available; otherwise review for availability, not lateness | `(+1, +1)` on confirmation; otherwise `(0, 0)` |
| Late success, then authorized goodwill acceptance with room/quota available | First review with success evidence; then confirmed early bird, reason `manual_goodwill`, same EUR 99.00, no surcharge | `(0, 0)` then `(+1, +1)`; total `(+1, +1)` |
| Repeat success notification (including different event ID for same payment) or repeat accepted manual decision | Same confirmed booking and evidence; no new allocation | Additional `(0, 0)` |
| Member/public compete for final early-bird unit; room available | At most one early-bird confirmation; other member request gets conflict, or already-paid public request remains in review; no silent repricing | Combined `ΔE=1`; only the confirmed booking contributes `ΔC=1` |
| Member/public compete for final room place; quota available | At most one confirmation; other request conflicts or paid case remains in review | Combined `ΔC=1`, `ΔE=1` for the early-bird winner |
| One regular-price participant pays EUR 120.00, even after `C` | Confirmed regular booking if room available | `(+1, 0)` |
| Family of two adults and two children, timely eligible success | Confirmed under separate family/add-on price terms; one discounted family unit | `(+4, +1)` |
| Legacy member registration with unknown price or timestamp | Remains a booking, payment/price/time unknown; reconcile its existing contribution before activating counters | No new booking increment; historical `E` remains unknown |
| Paid review case; room exhausted | Cannot accept into a full room; manual resolution pending, payment still known | `(0, 0)` while in review |
| Paid review case; room available, early-bird quota exhausted | No quota override authorized; retain review until a separate resolution, never omit usage for an accepted goodwill booking | `(0, 0)` while in review; any later authorized early-bird acceptance must add full `(+P, +E)` |

For DST date-only fixtures, the next local midnights are `2026-03-30T00:00:00+02:00`
after March 29 (a 23-hour day), and `2026-10-26T00:00:00+01:00` after October 25
(a 25-hour day). The same exclusive comparison applies in every browser zone.

## Task 9 implementation status

The inactive general one-person backend and existing teacher workflow support Stripe and PayPal paid reviews, explicit timely early-bird confirmation and explicit late/unknown-time goodwill. Unknown-time goodwill is now a confirmed owner rule, subject to verified success/association/price, its own workshop permission, a reason and both known available counts. Its immutable assessment and decision retain the original price and payment evidence. Provider creation/update timestamps do not establish exact success; unsupported timing profiles remain disabled. Synthetic tests establish implementation behavior, not provider guarantees or production readiness. Legacy reconciliation, allocation initialization and coordinated writer retirement remain outside Task 9. No marketing-agent behavior or workshop content changes here.
