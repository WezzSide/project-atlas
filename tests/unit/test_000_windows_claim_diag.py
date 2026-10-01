# ruff: noqa
"""TEMPORARY evidence probe (not for merge): which primitive gives exactly one winner on Windows."""

from __future__ import annotations

import collections
import os
import sys
import threading
from pathlib import Path

from project_atlas.orchestration.autonomy.dev_contracts import Role, make_work
from project_atlas.orchestration.autonomy.dev_spool_transport import SpoolTransport
from project_atlas.orchestration.autonomy.dev_transport import Channel

N, ROUNDS = 8, 150


def _race(op, tmp: Path, prefix: str):
    wins: collections.Counter[int] = collections.Counter()
    errs: collections.Counter[str] = collections.Counter()
    for r in range(ROUNDS):
        d = tmp / f"{prefix}{r}"
        (d / "claimed").mkdir(parents=True)
        src = d / "rec.json"
        src.write_text("x" * 600, encoding="utf-8")
        dest = d / "claimed" / "rec.json"
        bar = threading.Barrier(N)
        res: list[str] = []

        def go():
            bar.wait()
            try:
                op(src, dest)
                res.append("OK")
            except OSError as e:
                res.append(f"{type(e).__name__}:{getattr(e, 'winerror', None)}")

        ts = [threading.Thread(target=go) for _ in range(N)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        wins[res.count("OK")] += 1
        errs.update(x for x in res if x != "OK")
    return dict(wins), dict(errs)


def _link_then_unlink(src, dest):
    os.link(src, dest)  # exclusive create of the claimed name
    try:
        os.unlink(src)
    except OSError:
        pass


def test_probe(tmp_path):
    out = {
        "platform": sys.platform,
        "rename": _race(os.rename, tmp_path, "a"),
        "link": _race(_link_then_unlink, tmp_path, "b"),
    }
    # transport level
    wins: collections.Counter[int] = collections.Counter()
    for r in range(ROUNDS):
        root = tmp_path / f"t{r}"
        w = make_work(
            task_id="T1", execution_id="T1-E1", lineage_root="T1",
            repository="o/r", base_revision="a" * 40, authority_ref="A",
            allowed_paths=("src/x",), forbidden_paths=(), acceptance_contract=("t",),
        )  # fmt: skip
        SpoolTransport(root).publish(w)
        got: list[str] = []
        exc: list[str] = []
        bar = threading.Barrier(N)

        def go(i, root=root, got=got, exc=exc, bar=bar):
            bar.wait()
            try:
                rec = SpoolTransport(root).claim(
                    Channel.WORK, role=Role.IMPLEMENTER, identity=f"i{i}"
                )
                if rec is not None:
                    got.append(rec.seal)
            except Exception as e:  # noqa: BLE001
                exc.append(type(e).__name__)

        ts = [threading.Thread(target=go, args=(i,)) for i in range(N)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        wins[len(got)] += 1
        if exc:
            wins[f"exc:{sorted(set(exc))}"] += 1
    out["transport"] = dict(wins)
    raise AssertionError("PROBE_RESULT " + repr(out))
