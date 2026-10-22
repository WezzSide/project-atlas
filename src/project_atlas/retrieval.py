"""Read-only exact and prefix retrieval over generated lexical indexes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar


@dataclass(frozen=True)
class RetrievalResult:
    """Stable retrieval result with the canonical record and its provenance."""

    record_type: str
    record_id: str
    record: dict[str, Any]
    provenance: tuple[dict[str, Any], ...]


class VaultRetriever:
    """Query derived indexes without mutating the Vault."""

    _INDEX_KEYS: ClassVar[dict[str, tuple[str, ...]]] = {
        "source": (
            "source_lineage_id",
            "source_id",
            "project_uuid",
            "current_path",
            "historical_path",
        ),
        "claim": ("claim_id", "source_lineage_id", "concept_id", "field"),
        "concept": ("concept_id", "type", "project_id", "tag", "relationship_target"),
        "conflict": ("conflict_id", "claim_pair"),
        "authority": ("authority_id", "source_lineage_id", "source_id"),
        "provenance": ("source_lineage_id", "receipt_id"),
    }

    def __init__(self, vault: Path) -> None:
        self.vault = vault.expanduser().resolve()

    def lookup(
        self,
        kind: str,
        value: str,
        *,
        prefix: bool = False,
        project_id: str | None = None,
    ) -> list[RetrievalResult]:
        """Look up records by an indexed exact or prefix value."""
        if kind not in self._INDEX_KEYS:
            raise ValueError(f"unsupported retrieval kind: {kind}")
        # Empty / whitespace-only queries fail closed: prefix="" would match every
        # indexed key and is not a deterministic lexical retrieval contract (AS-RET-001).
        if not value or not value.strip():
            raise ValueError("retrieval value must be non-empty")
        index = self._load_index(kind)
        matching_keys = [
            key
            for key in index
            if (key.startswith(value) if prefix else key == value)
        ]
        record_ids = sorted(
            {
                record_id
                for key in matching_keys
                for record_id in index[key]
            }
        )
        records = self._records(kind)
        hits = [
            self._result(kind, record_id, records[record_id])
            for record_id in record_ids
            if record_id in records
        ]
        if project_id is None:
            return hits
        scope = project_id.strip()
        return [hit for hit in hits if _in_project_scope(kind, hit.record, scope)]

    def search(
        self, value: str, *, kind: str | None = None, prefix: bool = False
    ) -> list[RetrievalResult]:
        """Search one or all indexed record kinds deterministically."""
        kinds = (kind,) if kind is not None else tuple(self._INDEX_KEYS)
        results = [
            result for selected in kinds for result in self.lookup(selected, value, prefix=prefix)
        ]
        return sorted(results, key=lambda item: (item.record_type, item.record_id))

    def retrieve(self, kind: str, value: str, *, prefix: bool = False) -> list[RetrievalResult]:
        """Explicit alias for :meth:`lookup` for callers building query APIs."""
        return self.lookup(kind, value, prefix=prefix)

    def bm25_corpus(
        self, kind: str, *, project_id: str | None = None
    ) -> list[tuple[str, str]]:
        """Return deterministic ``(record_id, document_text)`` pairs for BM25.

        Document text is derived from the record id plus string leaves in the
        record (sorted walk). Derived / regenerable — not Layer B authority.
        When ``project_id`` is set, only records in that project scope are included.
        """
        if kind not in self._INDEX_KEYS:
            raise ValueError(f"unsupported retrieval kind: {kind}")
        records = self._records(kind)
        scope = project_id.strip() if project_id is not None else None
        corpus: list[tuple[str, str]] = []
        for record_id in sorted(records):
            record = records[record_id]
            if scope is not None and not _in_project_scope(kind, record, scope):
                continue
            corpus.append((record_id, _record_document_text(record_id, record)))
        return corpus

    def _load_index(self, kind: str) -> dict[str, list[str]]:
        index_name = {
            "provenance": "provenance",
            "source": "sources",
            "claim": "claims",
            "concept": "concepts",
            "conflict": "conflicts",
            "authority": "authority",
        }[kind]
        legacy = self.vault / "indexes"
        if legacy.exists():
            raise ValueError(
                f"obsolete generated index directory: {legacy}; rebuild under generated/indexes"
            )
        path = self.vault / "generated" / "indexes" / f"{index_name}.json"
        if not path.is_file():
            raise ValueError(f"generated lexical index is missing: {path}")
        raw = json.loads(path.read_text(encoding="utf-8"))
        result: dict[str, list[str]] = {}
        for field in self._INDEX_KEYS[kind]:
            index_field = f"by_{field}"
            for key, values in raw.get(index_field, {}).items():
                if isinstance(values, list):
                    result.setdefault(key, []).extend(values)
        return {key: sorted(set(values)) for key, values in result.items()}

    def _records(self, kind: str) -> dict[str, dict[str, Any]]:
        if kind == "source":
            raw = self._json(self.vault / "state" / "sources.json", {"sources": []})
            return {
                str(item["source_lineage_id"]): item
                for item in raw.get("sources", [])
                if isinstance(item, dict) and item.get("source_lineage_id")
            }
        locations = {
            "claim": ("state/claims", "claims", "claim_id"),
            "concept": ("state/concepts", "concepts", "concept_id"),
            "conflict": ("review/conflicts", "entries", "conflict_id"),
            "authority": ("state/authority", "authorities", "authority_id"),
        }
        if kind == "provenance":
            result: dict[str, dict[str, Any]] = {}
            for selected in ("claim", "concept", "conflict"):
                result.update(self._records(selected))
            return result
        directory, key, id_key = locations[kind]
        result = {}
        root = self.vault / directory
        for path in sorted(root.glob("*.json")) if root.is_dir() else []:
            raw = self._json(path, {})
            for item in raw.get(key, []) if isinstance(raw, dict) else []:
                if (
                    isinstance(item, dict)
                    and item.get(id_key)
                    and _item_bound_to_stem(item, path.stem)
                ):
                    result[str(item[id_key])] = item
        return result

    def _result(self, kind: str, record_id: str, record: dict[str, Any]) -> RetrievalResult:
        provenance = record.get("provenance")
        if not isinstance(provenance, list):
            provenance = record.get("sources", [])
        return RetrievalResult(
            record_type=kind,
            record_id=record_id,
            record=record,
            provenance=tuple(item for item in provenance if isinstance(item, dict)),
        )

    @staticmethod
    def _json(path: Path, default: Any) -> Any:
        if not path.is_file():
            return default
        return json.loads(path.read_text(encoding="utf-8"))


_AGGREGATE_STATE_STEMS = frozenset(
    {"claims", "concepts", "authorities", "authority", "conflicts"}
)


def _item_bound_to_stem(item: dict[str, Any], stem: str) -> bool:
    """Filename stem is project ownership. In-record project_id is not authority.

    AS-RET-FILENAME-PROJECT-BIND-001: a sibling ``attacker.json`` that
    declares ``project_id: victim`` must not be loaded into victim
    retrieval or BM25 corpora.

    Aggregate test/legacy stems (``claims.json``, ``conflicts.json``) are
    still loaded; scoped consumers filter them by requested project_id.
    """
    if stem in _AGGREGATE_STATE_STEMS:
        return True
    value = item.get("project_id")
    return isinstance(value, str) and value.strip() == stem


def _record_scope_id(kind: str, record: dict[str, Any]) -> str | None:
    """Extract project scope from a record; ``None`` when unknown (fail-closed)."""
    field = "project_uuid" if kind == "source" else "project_id"
    value = record.get(field)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _in_project_scope(kind: str, record: dict[str, Any], project_id: str) -> bool:
    scope_id = _record_scope_id(kind, record)
    return scope_id == project_id if scope_id is not None else False


def _record_document_text(record_id: str, record: dict[str, Any]) -> str:
    """Flatten record string leaves into a stable BM25 document (NFR-001)."""
    parts: list[str] = [record_id]

    def _walk(node: Any) -> None:
        if isinstance(node, str):
            token = node.strip()
            if token:
                parts.append(token)
            return
        if isinstance(node, dict):
            for key in sorted(node):
                _walk(node[key])
            return
        if isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(record)
    return " ".join(parts)
