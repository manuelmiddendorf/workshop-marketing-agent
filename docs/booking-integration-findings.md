# Workshop booking integration findings

September 18, 2026 baseline. Status: **Historical local inspection, not current production verification**.

Task 2a adds the [shared booking contract](workshop-booking-contract.md): confirmed
counting and review rules, proposed records/transitions, and remaining questions.
The booking contract separately records later Task 9 implementation status.
Earlier inspection evidence below is retained; the member deadline correction
was rechecked after Task 1. For the September 24 owner-reported October Mal-Yoga
pilot and narrowly verified current inputs, see the
[pilot input proposal](workshop-data-contract.md#mal-yoga-pilot-input-proposal).
The follow-up list below is historical, not a fresh list of missing backend work.

## Confirmed booking rules

The owner clarified that the two workshop booking routes intentionally differ:

- **Public website:** payment through Stripe or PayPal; successful payment is
  the qualifying booking event for the early-bird rule.
- **Member area:** registration books the place immediately. Payment follows by
  bank transfer independently; receipt of that transfer is not a prerequisite
  for booking or early-bird eligibility. Keep this route without Stripe or PayPal.
- **Member payment reminder:** preserve the existing wording about payment
  receipt. The owner intentionally uses it to encourage bank transfer promptly
  after registration. It does not introduce a payment-confirmation step: staff
  must not have to record receipt of the transfer to make the booking count.
- The stated final early-bird day is fully included, normally in `Europe/Berlin`.
  For ordinary eligibility, the qualifying event precedes the next local day.
- **Shared usage:** member/public bookings for the same workshop share both
  person capacity and early-bird quota. General workshops use one discount unit
  per discounted participant; parent-child bookings use one per family including
  extras, while room capacity counts every person. These are now owner decisions,
  not open questions about separate channel allocations.
- **Payment timing and review:** timely success remains timely when notification
  arrives later. Genuinely late success for an early-bird quote requires review;
  the usual goodwill acceptance keeps the paid amount without surcharge, remains
  early bird, and consumes the applicable shared units exactly once. No automatic
  refund or additional charge. The later Task 9 rule authorizes no quota override:
  exhausted or unknown room/quota keeps the case in review.

This clarification replaces the earlier blanket payment-success requirement.
Booking and payment are distinct facts; a counted member booking does not prove
payment and does not require a separately maintained payment status. The existing
parent-child yoga pass/extra-person flow is a third, distinct code path, described
in the [workshop data contract](workshop-data-contract.md#parent-child-booking-code-inspection).

## Evidence and limits

The original findings came from read-only local code inspection, not a live booking
test. No production Firestore access, payment, sync, deployment, or application
code change was performed during that inspection or Task 2a. Task 1 separately
changed the local member display. Deployed versions and live counts remain unverified.

Sources are local snapshots, not commit permalinks:

- `webseite-neu`: checkout at `08f70a2`, with existing changes. The inspected
  workshop JavaScript and sync script are untracked, so that commit does not
  contain them. The inspected files are in the root site, not its nested copies.
- `mgyoga`: checkout at `e36dcc1`, with local changes including the inspected
  workshop view, router, and auth store.
- `myfunctions`: the previously inspected local Cloud Functions folder; no Git
  revision available. Paths below start at its `functions` directory.

## Public workshop website

The public workshop flow does not implement a complete early-bird offer. It
exports a fixed price to the page and booking backend. An early-bird sentence in
the description does not affect the amount charged.

| Source | Lines | Observed behavior |
| --- | --- | --- |
| `webseite-neu/scripts/sync-workshops-from-mgyoga.js` | 1675–1725; 1938–1947 | Can read workshop IDs from `MgEvents/aktuell`, load their `MgEvents` documents, and filter for public visibility before mapping. It also supports explicit file inputs. |
| Same sync script | 1455–1513; 1994–2021 | Produces website data and `myfunctions/functions/workshops/catalog.js`. Exports regular `priceLabel` and catalog `priceEUR`; does not carry a structured offer deadline, limit, or usage count. If regular price is missing, it can fall back to the early-bird price for the catalog without checking eligibility. This fallback is not an early-bird implementation. |
| `webseite-neu/js/workshops-booking.js` | 308–312; 354; 622–665 | Displays the exported price, creates a registration through the configured API, then starts Stripe/PayPal using the returned registration ID. |
| `myfunctions/workshops/WSCreateBooking.js` | 60–89 | Reads the generated catalog price and stores it as `totalPriceEUR` in `workshopRegistrations`, initially `pendingPayment`. No offer or quota evaluation here. |
| `myfunctions/workshops/WSCreateStripeCheckout.js` / `WSCreatePayPalOrder.js` | 59–86 / 103–129 | Uses that stored amount for the payment provider. |
| `myfunctions/workshops/WSStripeWebhook.js` / `WSCapturePayPalOrder.js` | 69–92 / 131–166 | Marks the registration paid and records processing-time `paidAt`; does not update `MgEvents` registration arrays or early-bird quotas. |

The local generated data illustrates the mismatch: `js/workshops-data.js`
(38–40) describes a 99 EUR early-bird offer in the copy but supplies a 120 EUR
price label. This is evidence of different text and structured price in a local
artifact, not proof that a customer was charged incorrectly.

This finding applies to general workshops. The public site's separate
`js/kinderyoga-booking.js` (384–447), loaded by
`charlottenburg/kinderyoga.html` (708), already calculates early-bird display
prices from course deadlines and remaining quota. Do not replace that observation
with a claim that early-bird logic is absent from the entire website.

## Member workshop area

The active detail route uses `mgyoga/src/views/Workshops.vue`
(`src/router/index.js`, 134–135). Workshop documents are loaded from `MgEvents`
by `src/stores/auth.js` (339–362).

| Source in `mgyoga/src/views/Workshops.vue` | Lines | Observed behavior |
| --- | --- | --- |
| Early-bird eligibility and display | 325–432 | Reads `earlyBird.price`, `.until`, `.limit`, and `.note`; shows the reduced price in the facts and registration modal while eligible. |
| Count and capacity | 286–302; 334–343; 368–372 | Counts `angemeldet.length + vorgemerkt.length` for both capacity use and early-bird use. The inclusion of registrations awaiting bank transfer fits the owner's clarified member rule. These arrays do not identify which bookings actually received a discount. |
| Deadline — historical, before Task 1 | 337–339; 382–384 in the original snapshot | Appended browser-local `T23:59:59`, excluding the final fractional second and using the browser's zone. Task 1 replaced this display calculation; this is not a current defect. |
| Registration | 658–672 | Adds the member's email to `vorgemerkt`. This operation does not store a booked price, early-bird decision, or registration timestamp. |
| Payment and status wording | 57–62; 112–119; 182–197 | Shows bank transfer details, labels the member as provisionally registered, and says the place is reserved once payment arrives. The owner explicitly wants to retain this wording to encourage prompt transfer. For booking, capacity, early-bird use, and attribution, registration is still the qualifying event; no separate payment confirmation is required. |

The table's line numbers refer to the earlier snapshot. **Rechecked after Task 1:**
`src/composables/useEarlyBirdDeadline.js` strictly parses date-only values in
Europe/Berlin and uses the next local midnight exclusively, including DST changes.
The view shares eligibility/date formatting across card, modal and generated
information. Timers and visibility/focus/pageshow listeners refresh it and are
cleaned up on unmount. Omitted/null/empty deadlines retain the prior unlimited-time
behavior; invalid supplied dates do not qualify. Quota counting and registration
writes remain unchanged. This was a code inspection, not a new application test.

The existing early-bird count is therefore not wrong merely because it includes
unpaid member registrations. However, the displayed current offer is not a
persistent record of the price agreed at registration. Filling the quota or
passing the deadline can change the displayed price for an already registered
member when the view recomputes it.

## Recommended integration follow-up

Treat this as scoped work on the existing booking applications and Cloud
Functions, separate from implementing the Python marketing package:

1. Add public-workshop offer evaluation using current structured data, including
   the full Berlin-local deadline, quota unit, and authoritative usage count.
   Ensure the displayed price and payable amount agree. Handle a deadline or
   quota change during payment explicitly, including delayed notifications.
2. Keep member booking at registration, payment by bank transfer, and the existing
   payment-reminder wording. Persist the booked price, offer decision, and
   registration time independently of subsequent transfer receipt. Do not add a
   manual payment-confirmation step as a condition for counting the booking.
3. Implement the confirmed shared person capacity and early-bird quota with the
   path-specific units in the [booking contract](workshop-booking-contract.md).
   No shared update was present in the originally inspected paths. Reconcile
   historical bookings before enabling counters; array lengths do not establish
   paid public bookings, historical prices or exact discounted usage. Protect
   operational fields from teacher content saves. Backend version control is a
   prerequisite before backend edits, not work performed by this document.
4. Use an authoritative booking event appropriate to each route for marketing
   attribution: member registration or successful public payment. Count each
   booking once; a later member bank transfer must not create another booking.

Acceptance checks should cover an eligible offer, expiry, exhausted quota,
simultaneous bookings for the last discounted place, failed or repeated payment
notifications, and a member registration that counts without any payment
confirmation, delayed timely payment, and reviewed goodwill counted exactly once.
A later transfer must not create another booking. Use synthetic
data and offline fakes; no real bookings as tests. These checks are proposed for
the implementation task and have not been run in this inspection.
