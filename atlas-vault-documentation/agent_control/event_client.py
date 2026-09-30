"""Stable event command surface over the certified capture path."""

from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, cast

from agent_control import session


@contextmanager
def _normalization_lock(vault_root: Path) -> Iterator[None]:
    """Serialize capture through routing across same-Vault processes.

    The normalizer intentionally rejects unexpected files in its output
    directory. A per-Vault process lock prevents another managed event from
    being mistaken for an unsafe provider side effect while the current event
    is captured, normalized, verified and routed.
    """
    lock_path = vault_root / ".atlas-normalization.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        try:
            import fcntl
        except ImportError:  # pragma: no cover - Windows fallback
            yield
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def document(*, vault_root: Path, session_id: str, event_type: str, summary: str, work_package: str | None = None, validation: list[str] | None = None, decision: list[str] | None = None, changed_files: list[str] | None = None, spool: bool = False) -> dict[str, Any]:
    """Capture and process one event under the same-Vault provider lock."""
    if spool:
        return _document(
            vault_root=vault_root, session_id=session_id, event_type=event_type,
            summary=summary, work_package=work_package, validation=validation,
            decision=decision, changed_files=changed_files, spool=True,
        )
    with _normalization_lock(vault_root):
        return _document(
            vault_root=vault_root, session_id=session_id, event_type=event_type,
            summary=summary, work_package=work_package, validation=validation,
            decision=decision, changed_files=changed_files, spool=False,
        )


def _document(*, vault_root: Path, session_id: str, event_type: str, summary: str, work_package: str | None = None, validation: list[str] | None = None, decision: list[str] | None = None, changed_files: list[str] | None = None, spool: bool = False) -> dict[str, Any]:
    state = session.load(vault_root, session_id)
    if state.get("skill", {}).get("id") == "atlas-governed-work" and event_type != "session-start":
        if not state.get("skill_acknowledgement") or not state.get("capability", {}).get("ready", False):
            raise ValueError("governed event rejected before skill acknowledgement and capability readiness")
    spool = spool or bool(state.get("preflight", {}).get("spool", {}).get("mode"))
    # Certified capture binary only. A vault-adjacent
    # ``atlas-vault-documentation/scripts/capture_event.py`` is untrusted
    # (default connect vault is ``<project>/.atlas-vault``).
    script = Path(__file__).resolve().parents[1] / "scripts" / "capture_event.py"
    if not script.is_file():
        raise RuntimeError("certified capture_event.py missing")
    target = vault_root if not spool else Path(str(state["preflight"]["project_root"]))
    args = [sys.executable, str(script), "--spool" if spool else "--vault", str(target), "--project-id", f"PRJ-{state['session']['project_id'].upper()}", "--project-slug", str(state["session"]["project_id"]), "--event-kind", event_type, "--summary", summary, "--agent", str(state["agent"]["agent_id"]), "--adapter-id", str(state["agent"].get("adapter_id", "unknown")), "--skill-id", str(state["skill"].get("id", "unknown")), "--skill-version", str(state["skill"].get("version", "unknown")), "--skill-sha256", str(state["skill"].get("sha256", "unknown")), "--session-id", session_id, "--work-package", work_package or str(state["session"]["task_id"]), "--json"]
    for value in changed_files or []:
        args.extend(["--changed-file", value])
    for value in validation or []:
        args.extend(["--validation", value])
    for value in decision or []:
        args.extend(["--decision", value])
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "capture failed")[-1000:])
    payload = cast(dict[str, Any], json.loads(result.stdout))
    state["events"].setdefault(event_type, []).append(payload["event_id"])
    state["pipeline"]["captured"] += 1
    if spool:
        state["pipeline"]["pending_spool"] += 1
        state.setdefault("spool_hashes", {})[payload["event_id"]] = hashlib.sha256(Path(payload["path"]).read_bytes()).hexdigest()
    elif os.environ.get("ATLAS_MDA_COMMAND"):
        normalize = Path(__file__).resolve().parents[1] / "scripts" / "normalize_event.py"
        route = Path(__file__).resolve().parents[1] / "scripts" / "route_event.py"
        normalized = subprocess.run([sys.executable, str(normalize), "--event", str(payload["path"]), "--root", str(vault_root), "--mda-command", os.environ["ATLAS_MDA_COMMAND"], "--skill-dir", str(Path(__file__).resolve().parents[1]), "--skill", "atlas-governed-work", "--json"], capture_output=True, text=True, check=False)
        if normalized.returncode != 0:
            raise RuntimeError((normalized.stderr or normalized.stdout or "normalization failed")[-1000:])
        normalized_payload = cast(dict[str, Any], json.loads(normalized.stdout))
        routed = subprocess.run([sys.executable, str(route), "--normalized-event", str(normalized_payload["normalized_event"]), "--vault", str(vault_root), "--json"], capture_output=True, text=True, check=False)
        if routed.returncode != 0:
            raise RuntimeError((routed.stderr or routed.stdout or "routing failed")[-1000:])
        state["pipeline"]["normalized"] += 1
        state["pipeline"]["verified"] += 1
        state["pipeline"]["routed"] += 1
    session.save(vault_root, state)
    return payload
