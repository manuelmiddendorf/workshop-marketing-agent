# Temporary manual Google Business handoff

The deployed Callable is manual-only while Business Profile API approval/OAuth
are pending. `main.py` binds only `PILOT_OPENAI_API_KEY`, has no Google parameter,
and uses the factory's default `google=None`. Import/discovery and manual actions
need no Google secret or publisher. The OpenAI secret has an active version
according to the owner; no secret/version was inspected or changed in this task.
Leave `PILOT_GOOGLE_OAUTH` unconfigured, without placeholder versions.

The OAuth helper, transport, publication and reconciliation code remain available
and covered by offline tests. Tests of the retained API factory explicitly opt in
with trusted `enable_google=True`; the deployed entrypoint never does. A request
cannot enable it. Authorized `publish_google` and `reconcile_google_publication`
requests return `google_api_unavailable` before campaign access/reservation, with:

> Automatische Google-Veröffentlichung und API-Abgleich sind nicht verfügbar: API-Zugang und OAuth stehen noch aus.

## Exact read action

After generation/revision, campaign-link binding, exact approval and submission
preparation, explicitly request:

```json
{
  "action": "get_google_handoff",
  "workshop_reference": "malws-copy",
  "campaign_reference": "campaign-1",
  "round_reference": "round-1",
  "expected_revision": 8,
  "version_reference": "version-1",
  "approval_reference": "approval-1",
  "submission_reference": "preparation-1"
}
```

This is a read with a revision precondition, no request ID, command receipt,
reservation, publication result or schema change. Every call reauthorizes access
and checks the server-derived Google channel, enabled/selected version, exact
approval and stored `GoogleSubmissionPayload`. It reuses current feed/preparation
rules, additionally checks the recorded original description, compares the freshly
checked payload to the stored payload, and rereads campaign state before returning.
Changed facts/copy/link/image/approval evidence yields `outdated` or `review_required`;
stale revisions require refresh. Existing blocking Google attempts across rounds
produce `reconciliation_required`. A handoff cannot bypass their unknown outcome.

Success is `ready_for_manual_publication` / **Zum manuellen Veröffentlichen bereit**.
The allowlisted package contains `summary`, `event_title`, exact structured
`schedule` (`startDate`, `startTime`, `endDate`, `endTime`), `time_zone`, complete
tracked `booking_url`, and nullable `image_reference`. Text comes from the stored
package, not a regenerated draft. Time zone and image reference come from its
approval-bound version. Existing required credit stays verbatim in the summary;
no attribution is inferred. Internal fingerprints, actors and provider identities
are excluded. Fresh validation does not establish provider acceptance.

The teacher app renders plain German text, preserves Unicode and line breaks,
and exposes **Text kopieren**, **Bild öffnen** for a safe approved reference, and
**Google-Unternehmensprofil öffnen** to the fixed
[Google Business Profile destination](https://business.google.com/) (checked
2026-09-28). No automatic navigation, image request or portal automation occurs.
Unopenable references remain text with an explanation; no URL/image is invented.
Explicit text-only approval stays text-only. Existing preparation still rejects
unsupported image permissions, alias conflicts and missing required credit.

Copying/opening does not mean submitted or published. External edits fall outside
the exact approval and must be reviewed again in the application. The package is a
point-in-time read, not a lock against concurrent edits or duplicate manual posts.
Request a fresh handoff before use; operators must check the actual profile for
prior manual publication. Manual confirmation/deduplication is deferred, without
repurposing automatic attempts or Rausgegangen's separate confirmations.

## Controlled follow-ups

1. Rebuild/review deployment artifacts for this source; prior two-secret artifacts
   do not represent the manual-only Callable. Deployment remains separately approved.
2. Current read-preflight IAM alone cannot support live generation/revision writes.
   Review any required transaction/write permissions separately; this task changes
   neither cloud IAM nor the existing release approvals.
3. Perform live generation and first manual use/publication only under separate
   controlled execution. No live content or publication was produced here.
4. Add manual confirmation in separate work. Preserve existing histories.
5. After API approval, deliberately configure OAuth/location, restore the binding
   and publisher wiring, resolve uncertain/manual history, and verify access before
   reactivating automatic publication. Automatic integration remains the goal.

Offline test results and exact verification commands are recorded in
[manual handoff verification](google-manual-handoff-verification.md).
