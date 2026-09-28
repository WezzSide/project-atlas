"""Fail-closed selection of the Atlas executor job from Actions API data."""

from __future__ import annotations

from typing import Any

REQUIRED_VERIFIER_LABELS = frozenset(
    {"self-hosted", "linux", "x64", "atlas", "executor"}
)


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
