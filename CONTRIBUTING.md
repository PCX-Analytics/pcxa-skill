# Contributing

Start with a GitHub issue and its PCXA Project fields. State the acceptance
criterion, risk, affected CLI/plugin surface, and the smallest test layer that
can prove it. Keep runtime changes and release/process changes reviewable; do
not add credentials, live PREPROD access, or generated evidence to normal CI.

## Before requesting review

- Add or update focused tests, demonstrate the expected failure when practical,
  then run `python -m pytest -q` (the full offline suite).
- Install `.[dev]`, then run `python -m ruff check .`,
  `python -m compileall -q pcxa scripts`, and
  `python scripts/validate_versions.py`. A release additionally validates its
  `pcxa--vMAJOR.MINOR.PATCH` tag against package, plugin, and marketplace
  publication versions and requires that exact tag ref to resolve to `HEAD`.
  `marketplace.metadata.version` is schema metadata, not a release-version
  source.
- Record an issue/PR evidence table with criterion/risk, scenario, test node,
  command/run and source SHAs, observed result, cleanup or gap, and independent
  reviewer. A generic green CI result is not acceptance evidence.

## Sync and release changes

For behavior that affects `files sync`, release publishing, or candidate
promotion, hand off applicable PREPROD validation to PCXA QA and record the
exact deployed application SHA, authorized run/attempt, observed surfaces,
cleanup evidence, and any remaining gap. Do not replace this with CI or merge
status. In particular, PR #29's required PREPROD scenarios remain required
before release acceptance.

The normative evidence guidance is pinned to [PCXA Governance
`5a406de`](https://github.com/PCX-Analytics/pcxa-governance/blob/5a406debe69928d695a1d032dc7f9d4dd8d1781e/docs/contracts/testing.md)
and its [evidence-manifest contract](https://github.com/PCX-Analytics/pcxa-governance/blob/5a406debe69928d695a1d032dc7f9d4dd8d1781e/docs/contracts/evidence-manifests.md).
Those documents distinguish offline/static checks from executed QA evidence,
release readiness, and GO/NO-GO authorization.

## Ownership

`.github/CODEOWNERS` routes repository changes to the designated maintainer.
Request a fresh-context review of the final patch; renewed review is required
when the reviewed patch changes. Release authority remains separate from code
review and QA validation.
