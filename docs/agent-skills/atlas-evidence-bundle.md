# atlas-evidence-bundle

Purpose: machine-readable evidence pack composer.

## Bundle layout
- `.atlas/evidence/<object>/candidate.json`
- `ci.json`, `iv.json`, `claims.json`, `compatibility.json`
- `authorization.json`, `merge-receipt.json`, `postmerge-seal.json`

## Receipt minimum fields
- `repository`, `PR`, `head`, `tree`, `base`, `main_head`
- `timestamp`, `producer_identity`, `evidence_source`, `result`

