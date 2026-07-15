# Review Findings Fixes Design

## Scope

Fix two verified regressions on `eu-description-quality-gate`:

1. Northern Ireland locations must not qualify as EU-member jobs through the
   `ireland` substring.
2. Persistently sparse jobs must remain eligible for description enrichment on
   later runs without generating the same deferred Telegram notification every
   day.

## Location Classification

EU-member classification will ignore the phrase `northern ireland` before it
tests EU-member country and city tokens. The broader region classifier may
continue classifying UK locations as `Region.EU`, because that enum represents
the project's broad Europe search region. The stricter `is_eu_member_job()`
predicate must return false for Belfast/Northern Ireland unless the same
location string independently names a genuine EU-member location.

Regression coverage will prove that:

- `Belfast, Northern Ireland` is not an EU-member location.
- An onsite Belfast role without relocation evidence fails the EU relocation
  gate.
- Genuine Ireland locations continue to qualify.

## Deferred Notification State

The pipeline will add namespaced deferral markers to the existing
`seen_jobs.json` string set. Markers are distinct from normal URL and
title/company/location keys, so they do not prevent a deferred job from being
selected and retried on later runs. Because the existing state branch merges
the file as a set of strings, the markers automatically synchronize across the
GitHub and Raspberry Pi runners without a schema or workflow change.

For each deferred job, the pipeline will derive markers from its normalized URL
and title/company/location identity. It will notify Telegram only when neither
marker was already present, then persist both markers. On later runs, the job is
still retried, but another deferred notification is suppressed. If enrichment
later succeeds, normal evaluation and seen-state behavior proceeds unchanged.

The marker namespace will be explicit and collision-resistant relative to
ordinary keys. Empty identity values will not create global markers.

Regression coverage will prove that:

- The first deferral emits one notification and stores markers without marking
  the job seen.
- A second run retries the same sparse job but emits no duplicate notification.
- A previously deferred job can later acquire enough description text and be
  evaluated normally.
- Different newly deferred jobs still produce a notification.

## Error Handling and Compatibility

Telegram failures remain soft. Deferral markers are persisted independently of
notification delivery to avoid daily retry noise from a failing notification;
the pipeline log remains the diagnostic record for that run. The on-disk state
format remains a sorted JSON list of strings, preserving the current state-sync
and union-merge contract.

## Validation

Run focused location and pipeline regression tests first, then the complete
pytest suite. Confirm the CV source still has no diff from `origin/main`; these
fixes do not alter CV content.
