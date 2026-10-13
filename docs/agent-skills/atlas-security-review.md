# atlas-security-review

Purpose: static + dependency + secret scanning reviewer for Atlas.

## Scope
- Semgrep local scans
- Gitleaks secret scans
- Trivy/Syft/Grype dependency and SBOM inspection
- No automatic dependency rewrites in this role

## Output
- Severity-ranked findings with file/location and remediation suggestions.

