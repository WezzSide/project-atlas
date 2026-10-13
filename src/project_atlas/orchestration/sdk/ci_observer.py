"""Lightweight exact-head CI observer with required-job awareness. No LLM. No secrets."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from project_atlas.orchestration.autonomy.evidence import hash_payload
from project_atlas.orchestration.sdk.host import no_window_creationflags

CANONICAL_PR = 429
CANONICAL_REPO = "WezzSide/project-atlas"

REQUIRED_JOB_NAMES: Final[tuple[str, ...]] = (
    "control-plane",
    "quality (ubuntu-latest, 3.12, full)",
    "quality (ubuntu-latest, 3.13, compat)",
    "quality (windows-latest, 3.12, windows)",
)

CiStatus = Literal[
    "PENDING",
    "PASS",
    "FAIL",
    "UNKNOWN",
    "CANCELLED",
    "STALE_SUPERSEDED",
]

FailureClass = Literal[
    "CANDIDATE_DEFECT",
    "INFRA_TRANSIENT",
    "STALE_SUPERSEDED",
    "UNKNOWN_DIAGNOSTIC",
    "NONE",
]

# D-144: local `gh run watch` exit must not be treated as CI terminal failure.
CiWatchDisposition = Literal[
    "OBSERVER_EXITED",
    "CI_STILL_RUNNING",
    "CI_TERMINAL_PASS",
    "CI_TERMINAL_FAIL",
    "CI_UNKNOWN",
]


class CiJobObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    job_name: str
    job_status: str
    job_conclusion: str | None = None
    required: bool = False


class CiObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    head_sha: str
    run_id: str | None = None
    status: CiStatus
    conclusion: str | None = None
    run_status: str | None = None
    run_conclusion: str | None = None
    jobs: tuple[CiJobObservation, ...] = ()
    failed_required_job_id: str | None = None
    failure_digest: str | None = None
    failure_class: FailureClass = "NONE"
    observer: Literal["SDK_SUPERVISOR"] = "SDK_SUPERVISOR"
    merge_authorized: Literal[False] = False
    execution_authorized: Literal[False] = False


class PrHeadRef(BaseModel):
    """Live pull-request head. Not a certification."""

    model_config = ConfigDict(extra="forbid")

    pr_number: int = Field(ge=1)
    head_sha: str
    tree_sha: str | None = None


def classify_exact_head_status(
    *,
    raw_status: str,
    conclusion: str | None,
) -> CiStatus:
    """Classify one exact-head Actions run. Cancelled is not FAIL."""
    status_l = raw_status.lower()
    conclusion_l = (conclusion or "").lower() or None
    if status_l in {"queued", "in_progress", "waiting", "pending"}:
        return "PENDING"
    if conclusion_l == "success":
        return "PASS"
    if conclusion_l == "cancelled":
        return "CANCELLED"
    if conclusion_l in {"failure", "timed_out", "startup_failure"}:
        return "FAIL"
    return "UNKNOWN"


def _is_required_job(name: str) -> bool:
    return name in REQUIRED_JOB_NAMES


def classify_with_jobs(
    *,
    raw_status: str,
    conclusion: str | None,
    jobs: tuple[CiJobObservation, ...],
) -> tuple[CiStatus, str | None, str | None]:
    """Return status, failed_required_job_id, failure_digest.

    A required job COMPLETED+FAILURE is actionable while the run is still IN_PROGRESS.
    """
    for job in jobs:
        if not job.required:
            continue
        job_status = job.job_status.lower()
        job_conc = (job.job_conclusion or "").lower()
        if job_status == "completed" and job_conc in {
            "failure",
            "timed_out",
            "startup_failure",
        }:
            digest = hash_payload(
                {
                    "job_id": job.job_id,
                    "job_name": job.job_name,
                    "job_conclusion": job.job_conclusion,
                }
            )
            return "FAIL", job.job_id, digest

    status = classify_exact_head_status(raw_status=raw_status, conclusion=conclusion)
    if status == "PASS":
        # All required jobs must be success when claiming PASS.
        required = [j for j in jobs if j.required]
        if required and any(
            (j.job_conclusion or "").lower() != "success"
            or j.job_status.lower() != "completed"
            for j in required
        ):
            if any(
                j.job_status.lower() in {"queued", "in_progress", "waiting", "pending"}
                for j in required
            ):
                return "PENDING", None, None
            return "FAIL", None, None
    return status, None, None


def classify_against_live_head(
    *,
    exact: CiObservation,
    live_head: str | None,
) -> CiObservation:
    """Case C/D: cancelled or any observation for a superseded head is stale."""
    if live_head and live_head != exact.head_sha:
        return exact.model_copy(
            update={"status": "STALE_SUPERSEDED", "failure_class": "STALE_SUPERSEDED"}
        )
    return exact


def classify_failure(
    *,
    observation: CiObservation,
    live_head: str,
    current_generation: int,
    observation_generation: int | None = None,
) -> CiObservation:
    """Classify actionable failure only after live head / generation checks."""
    if live_head != observation.head_sha:
        return observation.model_copy(
            update={"status": "STALE_SUPERSEDED", "failure_class": "STALE_SUPERSEDED"}
        )
    if (
        observation_generation is not None
        and observation_generation != current_generation
    ):
        return observation.model_copy(
            update={"status": "STALE_SUPERSEDED", "failure_class": "STALE_SUPERSEDED"}
        )
    if observation.status != "FAIL":
        return observation.model_copy(update={"failure_class": "NONE"})
    conclusion = (observation.conclusion or observation.run_conclusion or "").lower()
    if conclusion in {"timed_out"} or "infrastructure" in (conclusion or ""):
        return observation.model_copy(update={"failure_class": "INFRA_TRANSIENT"})
    if observation.failed_required_job_id:
        return observation.model_copy(update={"failure_class": "CANDIDATE_DEFECT"})
    if conclusion in {"failure", "startup_failure"}:
        return observation.model_copy(update={"failure_class": "CANDIDATE_DEFECT"})
    return observation.model_copy(update={"failure_class": "UNKNOWN_DIAGNOSTIC"})


def failure_identity(
    *,
    head: str,
    run_id: str,
    job_id: str,
    failure_digest: str,
) -> str:
    return hash_payload(
        {"head": head, "run_id": run_id, "job_id": job_id, "failure_digest": failure_digest}
    )


def _fetch_jobs(
    *,
    run_id: str,
    repo: str,
    gh_bin: str,
) -> tuple[CiJobObservation, ...]:
    try:
        proc = subprocess.run(
            [
                gh_bin,
                "run",
                "view",
                run_id,
                "--repo",
                repo,
                "--json",
                "jobs",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=45,
            creationflags=no_window_creationflags(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return ()
    if proc.returncode != 0:
        return ()
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return ()
    rows = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return ()
    out: list[CiJobObservation] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "")
        job_id = str(row.get("databaseId") or row.get("id") or "")
        if not name or not job_id:
            continue
        out.append(
            CiJobObservation(
                job_id=job_id,
                job_name=name,
                job_status=str(row.get("status") or ""),
                job_conclusion=(str(row.get("conclusion") or "") or None),
                required=_is_required_job(name),
            )
        )
    return tuple(out)


def observe_exact_head_ci(
    *,
    head_sha: str,
    repo: str = CANONICAL_REPO,
    gh_bin: str = "gh",
) -> CiObservation:
    """Read GitHub Actions for one commit including required jobs."""
    if len(head_sha) != 40 or any(ch not in "0123456789abcdef" for ch in head_sha):
        return CiObservation(head_sha=head_sha, status="UNKNOWN")
    try:
        proc = subprocess.run(
            [
                gh_bin,
                "run",
                "list",
                "--repo",
                repo,
                "--commit",
                head_sha,
                "--limit",
                "1",
                "--json",
                "databaseId,status,conclusion,headSha",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=no_window_creationflags(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return CiObservation(head_sha=head_sha, status="UNKNOWN")
    if proc.returncode != 0:
        return CiObservation(head_sha=head_sha, status="UNKNOWN")
    try:
        rows = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return CiObservation(head_sha=head_sha, status="UNKNOWN")
    if not isinstance(rows, list) or not rows:
        return CiObservation(head_sha=head_sha, status="PENDING")
    row = rows[0]
    if not isinstance(row, dict):
        return CiObservation(head_sha=head_sha, status="UNKNOWN")
    run_id = str(row.get("databaseId") or "") or None
    raw_status = str(row.get("status") or "").lower()
    conclusion = str(row.get("conclusion") or "") or None
    jobs: tuple[CiJobObservation, ...] = ()
    if run_id:
        jobs = _fetch_jobs(run_id=run_id, repo=repo, gh_bin=gh_bin)
    status, failed_job, digest = classify_with_jobs(
        raw_status=raw_status, conclusion=conclusion, jobs=jobs
    )
    return CiObservation(
        head_sha=head_sha,
        run_id=run_id,
        status=status,
        conclusion=conclusion,
        run_status=raw_status,
        run_conclusion=conclusion,
        jobs=jobs,
        failed_required_job_id=failed_job,
        failure_digest=digest,
    )


def refresh_pr_head(
    *,
    pr_number: int = CANONICAL_PR,
    repo: str = CANONICAL_REPO,
    gh_bin: str = "gh",
) -> PrHeadRef | None:
    """Read the live PR head. Does not mutate. Returns None if gh is unavailable."""
    try:
        proc = subprocess.run(
            [
                gh_bin,
                "pr",
                "view",
                str(pr_number),
                "--repo",
                repo,
                "--json",
                "headRefOid",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=no_window_creationflags(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    head = str(payload.get("headRefOid") or "")
    if len(head) != 40:
        return None
    tree: str | None = None
    try:
        tree_proc = subprocess.run(
            [
                gh_bin,
                "api",
                f"repos/{repo}/commits/{head}",
                "--jq",
                ".commit.tree.sha",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=no_window_creationflags(),
        )
        candidate = (tree_proc.stdout or "").strip()
        if tree_proc.returncode == 0 and len(candidate) == 40:
            tree = candidate
    except (OSError, subprocess.TimeoutExpired):
        tree = None
    return PrHeadRef(pr_number=pr_number, head_sha=head, tree_sha=tree)


def classify_watch_session(
    *,
    watch_exit_code: int,
    watch_timed_out: bool,
    observation: CiObservation | None = None,
) -> CiWatchDisposition:
    """Map a local watch subprocess outcome to durable CI disposition.

    A timed-out or non-zero watch exit is OBSERVER_EXITED — never CI_TERMINAL_FAIL.
    """
    if observation is None:
        if watch_timed_out or watch_exit_code != 0:
            return "OBSERVER_EXITED"
        return "CI_UNKNOWN"
    if observation.status == "PENDING":
        return "CI_STILL_RUNNING"
    if observation.status == "PASS":
        return "CI_TERMINAL_PASS"
    if observation.status == "FAIL":
        return "CI_TERMINAL_FAIL"
    if watch_timed_out or watch_exit_code != 0:
        return "OBSERVER_EXITED"
    return "CI_UNKNOWN"


def persist_observation(root: Path, observation: CiObservation) -> Path:
    target = root / ".atlas" / "orchestration" / "sdk-runtime" / "ci-observer.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(observation.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target
