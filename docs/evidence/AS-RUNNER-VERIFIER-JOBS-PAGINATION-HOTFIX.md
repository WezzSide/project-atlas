# Atlas Runner verifier jobs pagination hotfix

## Governance evidence

- Base: `3117567e0f000d156c4ccd55760e0fda547c1a91`
- Base tree: `95208f1495c806265f1d1c92abe85e63008761a5`
- Production state: `PRESERVED`
- Deploy authority: `NOT_GRANTED`

Independent IV comment `5867783462` reported a blocking P1 at
`2026-09-28T10:05:00Z`: verifier identity selection could run against only
the first 100 workflow-run jobs. PR #1025 then merged at
`2026-09-28T10:05:38Z`.

The repair requires the jobs API `total_count` and every required
`per_page=100` page to be present, well formed, count-consistent, and complete
before the existing exactly-one verifier selector runs. Any API failure,
malformed page, inconsistent count, missing jobs, or incomplete collection
keeps verifier authority unestablished.

Future merge execution must recheck evidence freshness and blocking verdicts
immediately before mutation. Any new P0 or P1 after authority issuance
invalidates that merge authority.
