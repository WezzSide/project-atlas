"""WINDOWS_SPOOL_CLAIM_RACE_REGRESSION: one pending record, N claimers => exactly one owner.

Live evidence: ``test_concurrent_claimers_get_a_record_exactly_once`` observed THREE winners on the
Windows runner (main ``9a565c71`` and PR #1043). ``os.rename`` is not an exclusive ownership
primitive there (the source is opened by name, then renamed by handle, so several claimers that
opened the same file before the first rename completed can each succeed). The claim now takes
ownership by exclusively creating ``claimed/<seal>.json`` with ``os.link``.

Invariant under test (all platforms, identical contract): exactly one claimer acquires the record;
losers observe "not claimed"; the winner gets the original bytes; no valid record is rejected
because of a race; no retry turns a lost race into a second claim; cleanup never deletes another
claimer's acquired record.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest

import project_atlas.orchestration.autonomy.dev_spool_transport as mod
from project_atlas.orchestration.autonomy.dev_contracts import Role, make_work
from project_atlas.orchestration.autonomy.dev_spool_transport import SpoolTransport
from project_atlas.orchestration.autonomy.dev_transport import Channel, TransportError, encode

BASE = "a" * 40


def work(tid="T1"):
    return make_work(
        task_id=tid,
        execution_id=f"{tid}-E1",
        lineage_root=tid,
        repository="WezzSide/project-atlas",
        base_revision=BASE,
        authority_ref="AUTH-1",
        allowed_paths=("src/x",),
        forbidden_paths=("infra/atlas-runner/controller",),
        acceptance_contract=("tests pass",),
    )


def claim(root, who):
    return SpoolTransport(root).claim(Channel.WORK, role=Role.IMPLEMENTER, identity=who)


def _no_sleep(monkeypatch):
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)


# -- real concurrency: repeated rounds, exact outcome every round ---------------------------------
CLAIMERS, ROUNDS = 8, 60


def test_stress_exactly_one_winner_every_round(tmp_path):
    for rnd in range(ROUNDS):
        root = tmp_path / f"r{rnd}"
        w = work()
        SpoolTransport(root).publish(w)
        original = encode(w).encode("utf-8")
        got: dict[int, object] = {}
        errors: list[BaseException] = []
        barrier = threading.Barrier(CLAIMERS)

        def go(i, root=root, got=got, errors=errors, barrier=barrier):
            barrier.wait()
            try:
                got[i] = claim(root, f"impl-{i}")
            except BaseException as exc:  # any raise is a regression
                errors.append(exc)

        ts = [threading.Thread(target=go, args=(i,)) for i in range(CLAIMERS)]
        [t.start() for t in ts]
        [t.join() for t in ts]

        assert not errors, f"round {rnd}: a claimer raised {errors!r}"
        winners = [i for i, r in got.items() if r is not None]
        assert len(winners) == 1, f"round {rnd}: winners={winners}"
        assert got[winners[0]].seal == w.seal
        d = root / "WORK"
        claimed = d / "claimed" / f"{w.seal}.json"
        assert claimed.read_bytes() == original  # the winner owns the original bytes
        who = (d / "claimed" / f"{w.seal}.claim.json").read_text(encoding="utf-8")
        assert f"impl-{winners[0]}" in who  # one execution identity, the winner's
        assert not list((d / "rejected").glob("*")) if (d / "rejected").exists() else True
        leftovers = [p for p in d.glob("*.json") if not p.name.startswith(".")]
        # the pending name is gone, or (only if its cleanup was blocked) an inert same-file link
        assert all(os.path.samefile(p, claimed) for p in leftovers)
        assert claim(root, "late") is None  # nobody can claim it again


# -- deterministic interleavings (what the Windows runner hit by chance) ---------------------------
def _interleave_after_first_read(monkeypatch, root, a_result: dict, *, release: bool = True):
    """B has read the pending record; A then performs a COMPLETE claim; B continues."""
    real_decode, state = mod.decode, {"armed": True}

    def hook(wire):
        rec = real_decode(wire)
        if state["armed"]:
            state["armed"] = False
            a_result["rec"] = claim(root, "A")
        return rec

    monkeypatch.setattr(mod, "decode", hook)
    if not release:
        monkeypatch.setattr(mod, "_release_pending", lambda _p: None)  # A is still "in flight"


def test_loser_that_read_before_the_winner_finished_gets_nothing(tmp_path, monkeypatch):
    w = work()
    SpoolTransport(tmp_path).publish(w)
    a: dict = {}
    _interleave_after_first_read(monkeypatch, tmp_path, a)
    b = claim(tmp_path, "B")
    assert a["rec"] is not None and a["rec"].seal == w.seal and b is None
    assert not list((tmp_path / "WORK" / "rejected").glob("*"))


def test_loser_racing_an_in_flight_winner_gets_nothing_and_deletes_nothing(tmp_path, monkeypatch):
    w = work()
    SpoolTransport(tmp_path).publish(w)
    a: dict = {}
    _interleave_after_first_read(monkeypatch, tmp_path, a, release=False)
    b = claim(tmp_path, "B")
    d = tmp_path / "WORK"
    assert a["rec"] is not None and b is None
    assert (d / "claimed" / f"{w.seal}.json").exists(), "the winner's record survives the loser"
    assert (d / "claimed" / f"{w.seal}.claim.json").exists()
    assert (d / f"{w.seal}.json").exists(), "a loser never removes the pending name"
    assert not list((d / "rejected").glob("*")) if (d / "rejected").exists() else True
    assert claim(tmp_path, "C") is None  # the leftover pending name is inert


def test_windows_style_nonexclusive_rename_cannot_produce_a_second_winner(tmp_path, monkeypatch):
    """Model of the observed Windows behaviour: renaming a file whose handle was opened before the
    winner's rename completed reports success even though the destination already exists. The
    claim must not depend on ``os.rename`` at all."""
    real_rename = os.rename

    def windows_like(src, dst, *a, **k):
        if not os.path.exists(src) and os.path.exists(dst):
            return None  # by-handle rename onto the file's own new name "succeeds"
        return real_rename(src, dst, *a, **k)

    monkeypatch.setattr(mod.os, "rename", windows_like)
    w = work()
    SpoolTransport(tmp_path).publish(w)
    a: dict = {}
    _interleave_after_first_read(monkeypatch, tmp_path, a)
    assert claim(tmp_path, "B") is None and a["rec"] is not None


def test_ownership_never_uses_rename(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("os.rename must not be the ownership primitive")

    monkeypatch.setattr(mod.os, "rename", boom)
    w = work()
    SpoolTransport(tmp_path).publish(w)
    assert claim(tmp_path, "A") is not None and claim(tmp_path, "B") is None


# -- retries cannot turn a lost race into a claim -------------------------------------------------
def test_retry_after_a_lost_race_never_becomes_success(tmp_path, monkeypatch):
    _no_sleep(monkeypatch)
    w = work()
    SpoolTransport(tmp_path).publish(w)
    real_link, state, a = os.link, {"n": 0}, {}

    def link(src, dst, *x, **k):
        if Path(dst).parent.name == "claimed":
            state["n"] += 1
            if state["n"] == 1:
                monkeypatch.setattr(mod.os, "link", real_link)  # let A use the real primitive
                a["rec"] = claim(tmp_path, "A")  # A wins while B is blocked on a sharing violation
                monkeypatch.setattr(mod.os, "link", link)
                raise PermissionError(13, "sharing violation", str(src))
        return real_link(src, dst, *x, **k)

    monkeypatch.setattr(mod.os, "link", link)
    assert claim(tmp_path, "B") is None
    assert a["rec"] is not None and not list((tmp_path / "WORK" / "rejected").glob("*"))


def test_persistent_read_contention_never_rejects_a_valid_record(tmp_path, monkeypatch):
    _no_sleep(monkeypatch)
    w = work()
    SpoolTransport(tmp_path).publish(w)
    real, state = mod.Path.read_text, {"deny": True}

    def read(self, *a, **k):
        if state["deny"] and self.parent.name == "WORK" and self.name == f"{w.seal}.json":
            raise PermissionError(13, "sharing violation", str(self))
        return real(self, *a, **k)

    monkeypatch.setattr(mod.Path, "read_text", read)
    assert claim(tmp_path, "A") is None  # skipped, not parked
    assert not list((tmp_path / "WORK" / "rejected").glob("*"))
    state["deny"] = False
    assert claim(tmp_path, "A") is not None  # still pending: nothing was lost


def test_unremovable_pending_name_does_not_undo_or_duplicate_the_claim(tmp_path, monkeypatch):
    _no_sleep(monkeypatch)
    w = work()
    SpoolTransport(tmp_path).publish(w)
    real = mod.Path.unlink

    def unlink(self, *a, **k):
        if self.parent.name == "WORK" and self.name == f"{w.seal}.json":
            raise PermissionError(13, "sharing violation", str(self))
        return real(self, *a, **k)

    monkeypatch.setattr(mod.Path, "unlink", unlink)
    got = claim(tmp_path, "A")
    assert got is not None and got.seal == w.seal
    assert (tmp_path / "WORK" / "claimed" / f"{w.seal}.json").exists()
    assert claim(tmp_path, "B") is None  # the leftover pending name cannot be claimed again
    assert not list((tmp_path / "WORK" / "rejected").glob("*"))


# -- cross-platform negative controls -----------------------------------------------------------
def test_foreign_file_squatting_the_claimed_slot_is_parked_once_not_double_claimed(tmp_path):
    w = work()
    SpoolTransport(tmp_path).publish(w)
    slot = tmp_path / "WORK" / "claimed" / f"{w.seal}.json"
    slot.write_text("not this record's claim", encoding="utf-8")
    with pytest.raises(TransportError, match="could not be claimed"):
        claim(tmp_path, "A")
    assert slot.read_text(encoding="utf-8") == "not this record's claim"  # never overwritten
    assert (tmp_path / "WORK" / "rejected" / f"{w.seal}.json").exists()
    assert claim(tmp_path, "A") is None


def test_torn_record_is_rejected_once_and_never_claimed(tmp_path):
    w = work()
    SpoolTransport(tmp_path).publish(w)
    f = tmp_path / "WORK" / f"{w.seal}.json"
    f.write_text(f.read_text(encoding="utf-8")[:40], encoding="utf-8")  # torn write
    with pytest.raises(TransportError, match="undecodable"):
        claim(tmp_path, "A")
    assert not (tmp_path / "WORK" / "claimed" / f.name).exists()
    assert claim(tmp_path, "A") is None


def test_hostile_names_and_non_records_do_not_wedge_the_channel(tmp_path):
    w = work()
    SpoolTransport(tmp_path).publish(w)
    d = tmp_path / "WORK"
    (d / "dir.json").mkdir()  # a directory named like a record
    (d / ".tmp-leftover.json").write_text("{}", encoding="utf-8")  # publish temp litter
    got = claim(tmp_path, "A")
    assert got is not None and got.seal == w.seal
