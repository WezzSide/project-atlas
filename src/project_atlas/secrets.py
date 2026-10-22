"""Conservative content-based secret detection for source ingestion (NFR-004).

CODEX-SEC-006 / NFR-004: findings are metadata-only. Matched secret values are
never returned, logged, or persisted. Use ``redact_text`` when a surrounding
string must be retained after detection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Fixed placeholder — never includes matched secret material (CODEX-SEC-006).
REDACTED_PLACEHOLDER = "[redacted]"


@dataclass(frozen=True)
class SecretFinding:
    pattern: str
    confidence: str
    redacted_hint: str


_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    # SEC-SCAN-A-021: redact the full PEM block (header + body + footer), not
    # only the BEGIN line — otherwise key material remains after redact_text.
    (
        "private-key",
        "high",
        re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
            r"[\s\S]*?"
            r"-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
        ),
    ),
    ("bearer-token", "high", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{20,}")),
    (
        "api-key-assignment",
        "high",
        re.compile(
            r"(?i)\b(?:api[_-]?key|secret[_-]?key)\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{20,}"
        ),
    ),
    (
        "password-assignment",
        "medium",
        re.compile(r"(?i)\b(?:password|passwd|pwd)\s*[:=]\s*[^\s,;]{8,}"),
    ),
    (
        "connection-string",
        "high",
        re.compile(r"(?i)\b(?:postgres|mysql|mongodb(?:\+srv)?|redis)://[^\s]+"),
    ),
    ("cloud-access-key", "high", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    # AS-SEC-SCAN-F1: well-known token prefixes persist when only
    # assignment-shaped / AKIA patterns are scanned. Bare ghp_/sk-
    # must fail closed. Min 32 payload chars on sk- so short fixtures
    # (sk-test) stay unmatched.
    ("github-pat", "high", re.compile(r"\bghp_[A-Za-z0-9]{36}\b")),
    (
        "github-fine-grained-pat",
        "high",
        re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    ),
    ("openai-api-key", "high", re.compile(r"\bsk-[A-Za-z0-9]{32,}\b")),
    (
        "openai-project-key",
        "high",
        re.compile(r"\bsk-proj-[A-Za-z0-9_-]{16,}\b"),
    ),
)


def scan_text(text: str) -> list[SecretFinding]:
    """Return metadata-only findings; matched content is never returned."""
    return [
        SecretFinding(name, confidence, "content redacted")
        for name, confidence, pattern in _PATTERNS
        if pattern.search(text)
    ]


def redact_text(text: str) -> str:
    """Replace matched secret spans with ``REDACTED_PLACEHOLDER`` (CODEX-SEC-006).

    Safe for persistence of surrounding context. Does not return raw matches.
    """
    result = text
    for _name, _confidence, pattern in _PATTERNS:
        result = pattern.sub(REDACTED_PLACEHOLDER, result)
    return result
