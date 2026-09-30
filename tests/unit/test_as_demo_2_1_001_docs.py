"""AS-DEMO-2.1-001 — demo ADV / certificate docs phrases (NON_RELEASE)."""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEMO_DOCS = _REPO_ROOT / "docs" / "demo"

_REQUIRED_PHRASES = (
    "TECHNICAL DEMO — VERIFIED",
    "NOT RELEASE CERTIFIED",
    "NOT AUTHENTIC PILOT PASS",
)


def _demo_doc_texts() -> dict[str, str]:
    assert _DEMO_DOCS.is_dir(), f"missing demo docs dir: {_DEMO_DOCS}"
    texts: dict[str, str] = {}
    for path in sorted(_DEMO_DOCS.rglob("*")):
        if path.is_file() and path.suffix.lower() in {".md", ".txt"}:
            texts[path.relative_to(_REPO_ROOT).as_posix()] = path.read_text(encoding="utf-8")
    assert texts, f"no markdown/text under {_DEMO_DOCS}"
    return texts


def test_as_demo_2_1_001_docs_contain_certificate_phrases() -> None:
    """Docs-as-spec: demo ADV + certificate template lock honest outcome text."""
    texts = _demo_doc_texts()
    joined = "\n".join(texts.values())
    for phrase in _REQUIRED_PHRASES:
        assert phrase in joined, f"missing {phrase!r} under docs/demo/"


def test_as_demo_2_1_001_certificate_template_has_exact_phrases() -> None:
    path = _DEMO_DOCS / "CERTIFICATE-TEMPLATE.md"
    text = path.read_text(encoding="utf-8")
    for phrase in _REQUIRED_PHRASES:
        assert phrase in text, f"CERTIFICATE-TEMPLATE.md missing {phrase!r}"


def test_as_demo_2_1_001_adv_demo_has_exact_phrases() -> None:
    path = _DEMO_DOCS / "ADV-DEMO.md"
    text = path.read_text(encoding="utf-8")
    for phrase in _REQUIRED_PHRASES:
        assert phrase in text, f"ADV-DEMO.md missing {phrase!r}"
