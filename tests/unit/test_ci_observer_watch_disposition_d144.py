"""D-144 — gh run watch timeout must not imply CI terminal failure."""

from project_atlas.orchestration.sdk.ci_observer import (
    CiObservation,
    classify_watch_session,
)


def test_watch_timeout_with_pending_ci_is_still_running() -> None:
    obs = CiObservation(head_sha="a" * 40, status="PENDING")
    assert (
        classify_watch_session(watch_exit_code=1, watch_timed_out=True, observation=obs)
        == "CI_STILL_RUNNING"
    )


def test_watch_timeout_without_observation_is_observer_exited() -> None:
    assert (
        classify_watch_session(watch_exit_code=1, watch_timed_out=True, observation=None)
        == "OBSERVER_EXITED"
    )


def test_watch_success_with_pass_is_terminal_pass() -> None:
    obs = CiObservation(head_sha="b" * 40, status="PASS")
    assert (
        classify_watch_session(watch_exit_code=0, watch_timed_out=False, observation=obs)
        == "CI_TERMINAL_PASS"
    )


def test_watch_exit_with_fail_observation_is_terminal_fail() -> None:
    obs = CiObservation(head_sha="c" * 40, status="FAIL")
    assert (
        classify_watch_session(watch_exit_code=0, watch_timed_out=False, observation=obs)
        == "CI_TERMINAL_FAIL"
    )
