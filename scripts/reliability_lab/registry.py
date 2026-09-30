"""Aggregates every domain's experiment list into one ordered registry."""

from __future__ import annotations

from collections.abc import Callable

from reliability_lab import (
    experiments_git,
    experiments_lock_io,
    experiments_path_provenance,
    experiments_process,
)
from reliability_lab.model import ExperimentResult

ExperimentFn = Callable[[str, int], ExperimentResult]

ALL_EXPERIMENTS: list[ExperimentFn] = [
    *experiments_process.EXPERIMENTS,
    *experiments_lock_io.EXPERIMENTS,
    *experiments_path_provenance.EXPERIMENTS,
    *experiments_git.EXPERIMENTS,
]


def experiment_names() -> list[str]:
    return [fn.__name__ for fn in ALL_EXPERIMENTS]
