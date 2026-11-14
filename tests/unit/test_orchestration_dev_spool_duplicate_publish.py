"""Recovery / duplicate semantics, spool part: a second publication is not a second delivery.

Measured before this change (ATLAS-DEVQ-0008 residual, observed by a verifier): under
concurrent ticks a peer's ``recover`` can publish a record a second time while the
dispatcher's own publish is in flight. ``publish`` decided "not there yet" before the first
publication was claimed and created the record's name after the claimer had removed it. The
spool then held a pending copy of a record that was already consumed; the next ``claim``
parked it in ``rejected/`` and raised ``TransportError``: an ambiguous delivery for a record
that had been delivered exactly once.

Invariant under test: a record that was consumed is never pending again. The publisher takes
its late name back, and a claimer that still meets such a copy drops it silently. A claimed
slot that holds anything else than this record stays a blocked slot (parked once, raised).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

import project_atlas.orchestration.autonomy.dev_spool_transport as mod
from project_atlas.orchestration.autonomy.dev_contracts import Role, make_work
from project_atlas.orchestration.autonomy.dev_spool_transport import SpoolTransport
from project_atlas.orchestration.autonomy.dev_transport import Channel, TransportError, encode

BASE = "a" * 40
IMPL = "vps1-impl"


def work(tid: str = "T1"):
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


def claim(root: Path, who: str = IMPL):
    return SpoolTransport(root).claim(Channel.WORK, role=Role.IMPLEMENTER, identity=who)


def _late_publish(tmp_path: Path, monkeypatch, *, to: str | None) -> tuple[bool, object]:
    """Publisher B looks, then A publishes and the record is claimed, then B creates the name."""
    w = work()
    real = tempfile.mkstemp
    seen: dict[str, object] = {}

    def first_publication_completes_meanwhile(*args, **kw):
        monkeypatch.setattr(mod.tempfile, "mkstemp", real)
        assert SpoolTransport(tmp_path).publish(w, to=to) is True
        seen["first"] = claim(tmp_path)
        return real(*args, **kw)

    late = SpoolTransport(tmp_path)
    if to is not None:  # the address exists already, so the hook fires at the record's temp file
        (tmp_path / "WORK" / f"{w.seal}.to").write_text(to, encoding="utf-8")
    monkeypatch.setattr(mod.tempfile, "mkstemp", first_publication_completes_meanwhile)
    fresh = late.publish(w, to=to)
    assert seen["first"] is not None and seen["first"].seal == w.seal  # delivered exactly once
    return fresh, w


@pytest.mark.parametrize("to", [None, IMPL])
def test_a_publish_that_arrives_after_the_claim_takes_its_name_back(tmp_path, monkeypatch, to):
    fresh, w = _late_publish(tmp_path, monkeypatch, to=to)
    assert fresh is False  # the record was already delivered: nothing new was published
    assert not (tmp_path / "WORK" / f"{w.seal}.json").exists()  # never pending a second time
    assert claim(tmp_path) is None  # no second delivery and no TransportError
    assert not list((tmp_path / "WORK" / "rejected").glob("*"))  # a valid record is not parked
    assert (tmp_path / "WORK" / "claimed" / f"{w.seal}.json").read_text(encoding="utf-8") == encode(
        w
    )


def test_a_pending_copy_of_a_consumed_record_is_dropped_not_delivered_or_parked(tmp_path):
    """The claimer's side: the copy is there (a publisher died before taking it back)."""
    w = work()
    t = SpoolTransport(tmp_path)
    assert t.publish(w) is True
    assert claim(tmp_path) is not None
    pending = tmp_path / "WORK" / f"{w.seal}.json"
    pending.write_text(encode(w), encoding="utf-8")  # a second inode with the same record
    assert claim(tmp_path) is None  # not handed out again, and not an error
    assert not pending.exists()
    assert not list((tmp_path / "WORK" / "rejected").glob("*"))
    assert claim(tmp_path, "someone-else") is None
    assert [r.seal for r in t.claimed_records(Channel.WORK, identity=IMPL)] == [w.seal]


def test_a_pending_copy_does_not_hide_the_records_behind_it(tmp_path):
    a, b = work("A"), work("B")
    t = SpoolTransport(tmp_path)
    t.publish(a)
    assert claim(tmp_path).seal == a.seal
    (tmp_path / "WORK" / f"{a.seal}.json").write_text(encode(a), encoding="utf-8")
    t.publish(b)
    got = claim(tmp_path)  # one call: the copy is skipped, the next record is delivered
    assert got is not None and got.seal == b.seal


def test_a_claimed_slot_that_holds_something_else_is_still_a_blocked_slot(tmp_path):
    """Negative control: only this record's own bytes make a pending file a duplicate."""
    w = work()
    SpoolTransport(tmp_path).publish(w)
    slot = tmp_path / "WORK" / "claimed" / f"{w.seal}.json"
    slot.write_text(encode(work("OTHER")), encoding="utf-8")  # a valid record, but another one
    with pytest.raises(TransportError, match="could not be claimed"):
        claim(tmp_path)
    assert (tmp_path / "WORK" / "rejected" / f"{w.seal}.json").exists()
    assert slot.read_text(encoding="utf-8") == encode(work("OTHER"))


def test_a_publish_whose_own_record_was_claimed_at_once_still_reports_fresh(tmp_path, monkeypatch):
    """The take-back is only for a name that is NOT the claimed record's own file."""
    w = work()
    real = mod.os.link
    got: list[object] = []

    def claimed_right_after_the_name_exists(src, dst, *args, **kw):
        out = real(src, dst, *args, **kw)
        if Path(dst).parent.name == "WORK" and Path(dst).name == f"{w.seal}.json":
            monkeypatch.setattr(mod.os, "link", real)
            got.append(claim(tmp_path))
        return out

    monkeypatch.setattr(mod.os, "link", claimed_right_after_the_name_exists)
    assert SpoolTransport(tmp_path).publish(w) is True
    assert got and got[0] is not None and got[0].seal == w.seal
    assert claim(tmp_path) is None


def test_a_symlink_in_the_claimed_slot_is_a_blocked_slot_even_with_the_same_bytes(tmp_path):
    """A link is not this record's claim, whatever it points at: parked, not dropped."""
    w = work()
    SpoolTransport(tmp_path).publish(w)
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text(encode(w), encoding="utf-8")
    slot = tmp_path / "WORK" / "claimed" / f"{w.seal}.json"
    try:
        slot.symlink_to(elsewhere)
    except OSError:
        pytest.skip("cannot create a symbolic link here")
    with pytest.raises(TransportError, match="could not be claimed"):
        claim(tmp_path)
    assert (tmp_path / "WORK" / "rejected" / f"{w.seal}.json").exists()


def test_a_publish_that_cannot_tell_whose_claim_it_is_takes_its_name_back(tmp_path, monkeypatch):
    """``samefile`` failing is "cannot tell": cleanup, never a second pending copy."""
    w = work()
    t = SpoolTransport(tmp_path)
    t.publish(w)
    assert claim(tmp_path) is not None
    (tmp_path / "WORK" / "claimed" / f"{w.seal}.json").rename(tmp_path / "aside.json")
    real_link = mod.os.link

    def claimed_again_just_before_the_name_exists(src, dst, *args, **kw):
        if Path(dst).parent.name == "WORK":
            monkeypatch.setattr(mod.os, "link", real_link)
            (tmp_path / "aside.json").rename(tmp_path / "WORK" / "claimed" / f"{w.seal}.json")
        return real_link(src, dst, *args, **kw)

    def cannot_tell(_a, _b):
        raise PermissionError(13, "access denied")

    monkeypatch.setattr(mod.os, "link", claimed_again_just_before_the_name_exists)
    monkeypatch.setattr(mod.os.path, "samefile", cannot_tell)
    assert t.publish(w) is False
    assert not (tmp_path / "WORK" / f"{w.seal}.json").exists()


# -- real concurrency: several publishers of one seal against several claimers ---------------------
PUBLISHERS, CLAIMERS, ROUNDS = 6, 4, 40


def test_stress_many_publishers_of_one_seal_deliver_it_exactly_once(tmp_path):
    import threading

    for n in range(ROUNDS):
        root = tmp_path / f"r{n}"
        w = work(f"T{n}")
        SpoolTransport(root)  # layout exists before the threads start
        start = threading.Barrier(PUBLISHERS + CLAIMERS)
        delivered: list[object] = []
        errors: list[BaseException] = []
        lock = threading.Lock()

        def publisher(root=root, w=w, start=start, errors=errors, lock=lock):
            try:
                start.wait()
                for _ in range(5):  # a recover that runs again and again
                    SpoolTransport(root).publish(w)
            except BaseException as exc:
                with lock:
                    errors.append(exc)

        def claimer(i, root=root, start=start, delivered=delivered, errors=errors, lock=lock):
            try:
                start.wait()
                for _ in range(40):
                    got = claim(root, f"impl-{i}")
                    if got is not None:
                        with lock:
                            delivered.append(got)
            except BaseException as exc:
                with lock:
                    errors.append(exc)

        threads = [threading.Thread(target=publisher) for _ in range(PUBLISHERS)]
        threads += [threading.Thread(target=claimer, args=(i,)) for i in range(CLAIMERS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == [], f"round {n}: {errors!r}"
        rest = claim(root)  # whatever the threads left: at most the one record, never a copy
        seals = [r.seal for r in delivered] + ([rest.seal] if rest is not None else [])
        assert seals == [w.seal], f"round {n}: delivered {len(seals)} times"
        assert claim(root) is None
        assert not list((root / "WORK" / "rejected").glob("*")), f"round {n}: parked a copy"
        assert not (root / "WORK" / f"{w.seal}.json").exists(), f"round {n}: pending copy left"


def test_a_copy_taken_back_between_the_two_looks_is_not_parked(tmp_path, monkeypatch):
    """Deterministic form of the race the stress test found in a first version of this fix.

    The claimer loses the exclusive create, then the copy's publisher takes the name back,
    then the next publisher creates it again. Parking at that point would reject a valid
    record over a race, and the claimer would report a ``TransportError`` for a record that
    was delivered exactly once.
    """
    w = work()
    t = SpoolTransport(tmp_path)
    t.publish(w)
    assert claim(tmp_path) is not None
    pending = tmp_path / "WORK" / f"{w.seal}.json"
    pending.write_text(encode(w), encoding="utf-8")
    real = mod._read_bytes
    state = {"armed": True}

    def taken_back_then_published_again(path):
        if state["armed"] and path == pending:
            state["armed"] = False
            pending.unlink()  # the first copy's publisher takes it back
            try:
                return real(path)  # FileNotFoundError, as the claimer would see it
            finally:
                pending.write_text(encode(w), encoding="utf-8")  # the next publisher's copy
        return real(path)

    monkeypatch.setattr(mod, "_read_bytes", taken_back_then_published_again)
    assert claim(tmp_path) is None  # no error for this look
    assert state["armed"] is False, "the window was not reached"
    assert not list((tmp_path / "WORK" / "rejected").glob("*"))
    assert claim(tmp_path) is None and not pending.exists()  # the new copy is dropped next
