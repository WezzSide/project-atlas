"""Fail-closed selection of the Atlas executor job from Actions API data."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

REQUIRED_VERIFIER_LABELS = frozenset(
    {"self-hosted", "linux", "x64", "atlas", "executor"}
)
JOBS_PAGE_SIZE = 100


def validate_job_ids(jobs: list[Any]) -> None:
    """Require one valid, globally unique GitHub Actions job ID per record.

    GitHub Actions job IDs are positive integers. A missing, malformed, or
    duplicated ID is evidence ambiguity: the collection must fail closed
    (UNESTABLISHED) rather than let one record of a conflicting identity
    establish verifier authority. Identical duplicates are rejected too —
    no deduplication, no first-wins, no label preference.
    """
    seen: set[int] = set()
    for job in jobs:
        if not isinstance(job, dict):
            raise ValueError("jobs API entries must be objects")
        job_id = job.get("id")
        if isinstance(job_id, bool) or not isinstance(job_id, int):
            raise ValueError("jobs API job id must be an integer")
        if job_id <= 0:
            raise ValueError("jobs API job id must be a positive integer")
        if job_id in seen:
            raise ValueError(f"jobs API duplicate job id {job_id}")
        seen.add(job_id)


def fetch_complete_jobs(
    fetch_page: Callable[[int, int], Any],
) -> dict[str, Any]:
    """Fetch and validate the complete workflow-run jobs collection."""
    all_jobs: list[Any] = []
    expected_total: int | None = None
    required_pages: int | None = None

    page = 1
    while required_pages is None or page <= required_pages:
        payload = fetch_page(page, JOBS_PAGE_SIZE)
        if not isinstance(payload, dict):
            raise ValueError("jobs API page payload must be an object")

        total_count = payload.get("total_count")
        if (
            isinstance(total_count, bool)
            or not isinstance(total_count, int)
            or total_count < 0
        ):
            raise ValueError("jobs API total_count must be a non-negative integer")

        jobs = payload.get("jobs")
        if not isinstance(jobs, list):
            raise ValueError("jobs API page must contain a jobs list")

        if expected_total is None:
            expected_total = total_count
            required_pages = max(
                1, (expected_total + JOBS_PAGE_SIZE - 1) // JOBS_PAGE_SIZE
            )
        elif total_count != expected_total:
            raise ValueError("jobs API total_count changed between pages")

        if required_pages is None or expected_total is None:
            raise ValueError("jobs API pagination state was not established")
        expected_page_size = min(
            JOBS_PAGE_SIZE,
            max(0, expected_total - ((page - 1) * JOBS_PAGE_SIZE)),
        )
        if len(jobs) != expected_page_size:
            raise ValueError(
                f"jobs API page {page} contained {len(jobs)} jobs; "
                f"expected {expected_page_size}"
            )

        all_jobs.extend(jobs)
        page += 1

    if expected_total is None:
        raise ValueError("jobs API pagination completed without a total_count")
    if len(all_jobs) != expected_total:
        raise ValueError(
            f"jobs API retrieved {len(all_jobs)} jobs; expected {expected_total}"
        )
    validate_job_ids(all_jobs)
    return {"total_count": expected_total, "jobs": all_jobs}


def select_verifier_runner(jobs_payload: Any) -> tuple[str, tuple[str, ...]]:
    """Return the sole job carrying the complete Atlas executor label set.

    GitHub Actions job labels are strings. Missing, malformed, partial,
    duplicate, or ambiguous identity data is not sufficient to establish a
    verifier runner and must be treated by the caller as UNESTABLISHED.
    """
    if not isinstance(jobs_payload, dict):
        raise ValueError("jobs API payload must be an object")

    jobs = jobs_payload.get("jobs")
    if not isinstance(jobs, list):
        raise ValueError("jobs API payload must contain a jobs list")
    validate_job_ids(jobs)

    candidates: list[tuple[str, tuple[str, ...]]] = []
    for job in jobs:
        if not isinstance(job, dict):
            raise ValueError("jobs API entries must be objects")

        labels = job.get("labels")
        if not isinstance(labels, list) or any(
            not isinstance(label, str) for label in labels
        ):
            raise ValueError("jobs API labels must be a list of strings")
        if len(labels) != len(set(labels)):
            raise ValueError("jobs API labels must not contain duplicates")

        if set(labels) != REQUIRED_VERIFIER_LABELS:
            continue

        runner_name = job.get("runner_name")
        if not isinstance(runner_name, str) or not runner_name.strip():
            raise ValueError("Atlas executor job must have a runner name")
        candidates.append((runner_name, tuple(labels)))

    if len(candidates) != 1:
        raise ValueError(
            "expected exactly one job with the complete Atlas executor labels; "
            f"found {len(candidates)}"
        )
    return candidates[0]
