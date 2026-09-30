"""EXECUTOR_PY312_DEPLOYMENT_CLOSURE: one governed deploy of an exact revision makes the
worker image the controller launches correspond to that same revision.

Sandboxed (fake git / systemctl / docker; no daemon, no network, no root). SANDBOXED != LIVE:
the real image build and a live run are owner-bound and proven only after activation.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
from pathlib import Path

import pytest
from conftest import drive_to_state  # noqa: F401  (fixture module import side effects)
from test_failure_matrix import _definition, _submit
from test_trusted_deploy_closure import DEPLOY, FakeGitRepo, _run

from controller import lifecycle
from controller.dockerctl import DockerCtl
from controller.health import run_health
from controller.worker_image import (
    WorkerImageError,
    bind_config,
    expected_tag,
    load_binding,
)

REV_A = "a" * 40
REV_B = "b" * 40
REMOTE = "https://github.com/WezzSide/project-atlas.git"
ID_X = "sha256:" + "c" * 64


def _deploy(repo: FakeGitRepo, tmp_path: Path, rev: str, **extra: str):
    env = repo.env(tmp_path)
    env["ATLAS_RUNNER_BIN"] = "/bin/true"
    env.update(extra)
    return _run(["bash", str(DEPLOY), rev, "--skip-tests"], env=env), env


def _binding(env: dict, rev: str) -> dict:
    return json.loads((Path(env["ATLAS_RELEASES_DIR"]) / rev / "worker-image.json").read_text())


# ---------------------------------------------------------------------------
# deploy-release.sh: build, Python contract, binding, ordering
# ---------------------------------------------------------------------------


def test_deploy_builds_revision_image_validates_python_and_binds_identity(tmp_path):
    repo = FakeGitRepo(tmp_path, remote_url=REMOTE)
    proc, env = _deploy(repo, tmp_path, REV_A)
    assert proc.returncode == 0, proc.stdout.decode() + proc.stderr.decode()
    binding = _binding(env, REV_A)
    assert binding["tag"] == f"atlas-runner-worker:{REV_A}" == expected_tag(REV_A)
    assert binding["revision"] == REV_A and binding["python_version"] == "3.12.14"
    assert binding["image_id"].startswith("sha256:") and len(binding["image_id"]) == 71
    builds = repo.docker.calls("build")
    assert len(builds) == 1
    assert f"atlas.runner.revision={REV_A}" in builds[0] and "latest" not in builds[0]
    # the Python contract is proven in the exact built image (by ID), isolated
    (run,) = repo.docker.calls("run")
    assert binding["image_id"] in run and "--network none" in run
    assert "--cap-drop ALL" in run and "no-new-privileges" in run and "--entrypoint python3" in run
    # evidence: the deploy log names the image actually selected
    out = proc.stdout.decode()
    assert f"[deploy] worker-image tag=atlas-runner-worker:{REV_A} id={binding['image_id']}" in out
    assert "python=3.12.14" in out and f"revision={REV_A}" in out
    # activated only after the image was built and bound
    assert (Path(env["ATLAS_CURRENT_LINK"]).resolve() / "worker-image.json").exists()


def test_image_is_built_and_validated_before_activation(tmp_path):
    repo = FakeGitRepo(tmp_path, remote_url=REMOTE)
    proc, _ = _deploy(repo, tmp_path, REV_A)
    out = proc.stdout.decode()
    assert out.index("[deploy] worker-image") < out.index("[deploy] activated")


def test_image_build_failure_leaves_previous_release_active(tmp_path):
    repo = FakeGitRepo(tmp_path, remote_url=REMOTE)
    proc_a, env = _deploy(repo, tmp_path, REV_A)
    assert proc_a.returncode == 0
    link = Path(env["ATLAS_CURRENT_LINK"])
    before = link.resolve()
    proc_b, _ = _deploy(repo, tmp_path, REV_B, FAKE_DOCKER_BUILD_FAILS="1")
    assert proc_b.returncode != 0
    assert b"worker image build failed" in proc_b.stdout
    assert link.resolve() == before  # no half-upgrade
    assert not (Path(env["ATLAS_RELEASES_DIR"]) / REV_B / "worker-image.json").exists()


def test_python_contract_failure_blocks_activation(tmp_path):
    repo = FakeGitRepo(tmp_path, remote_url=REMOTE)
    proc_a, env = _deploy(repo, tmp_path, REV_A)
    assert proc_a.returncode == 0
    link = Path(env["ATLAS_CURRENT_LINK"])
    proc_b, _ = _deploy(repo, tmp_path, REV_B, FAKE_DOCKER_PY="3.11.2")
    assert proc_b.returncode != 0
    assert b"Python >= 3.12 contract failed" in proc_b.stdout
    assert link.resolve() == Path(env["ATLAS_RELEASES_DIR"]) / REV_A
    assert not (Path(env["ATLAS_RELEASES_DIR"]) / REV_B / "worker-image.json").exists()


def test_missing_docker_fails_closed_before_activation(tmp_path):
    repo = FakeGitRepo(tmp_path, remote_url=REMOTE)
    proc, env = _deploy(repo, tmp_path, REV_A, ATLAS_DOCKER="/nonexistent/docker")
    assert proc.returncode != 0
    assert b"docker not found" in proc.stdout
    assert not Path(env["ATLAS_CURRENT_LINK"]).exists()


def test_stale_prior_image_under_the_tag_is_rebuilt_never_trusted(tmp_path):
    repo = FakeGitRepo(tmp_path, remote_url=REMOTE)
    stale_id = "sha256:" + "d" * 64
    repo.docker.seed(REV_A, stale_id, label_revision=REV_B)  # tag exists, wrong revision label
    proc, env = _deploy(repo, tmp_path, REV_A)
    assert proc.returncode == 0, proc.stdout.decode()
    assert len(repo.docker.calls("build")) == 1
    assert _binding(env, REV_A)["image_id"] != stale_id


def test_redeploy_of_same_revision_reuses_the_image_and_is_deterministic(tmp_path):
    repo = FakeGitRepo(tmp_path, remote_url=REMOTE)
    proc1, env = _deploy(repo, tmp_path, REV_A)
    first = _binding(env, REV_A)
    proc2, _ = _deploy(repo, tmp_path, REV_A)
    assert proc1.returncode == proc2.returncode == 0
    assert len(repo.docker.calls("build")) == 1  # no rebuild
    assert len(repo.docker.calls("run")) == 2  # Python contract re-proven each deploy
    assert _binding(env, REV_A) == first
    assert b"reused=1" in proc2.stdout


def test_newer_revision_after_older_gets_its_own_image_and_binding(tmp_path):
    repo = FakeGitRepo(tmp_path, remote_url=REMOTE)
    _, env = _deploy(repo, tmp_path, REV_A)
    proc_b, _ = _deploy(repo, tmp_path, REV_B)
    assert proc_b.returncode == 0
    a, b = _binding(env, REV_A), _binding(env, REV_B)
    assert a["tag"] != b["tag"] and a["image_id"] != b["image_id"]
    assert Path(env["ATLAS_CURRENT_LINK"]).resolve() == Path(env["ATLAS_RELEASES_DIR"]) / REV_B
    # the older release keeps its own (still present) image => rollback stays coherent
    assert _binding(env, REV_A) == a


def test_health_failure_rolls_back_release_and_its_image_binding(tmp_path):
    repo = FakeGitRepo(tmp_path, remote_url=REMOTE)
    _, env = _deploy(repo, tmp_path, REV_A)
    previous = _binding(env, REV_A)
    proc_b, _ = _deploy(repo, tmp_path, REV_B, FAKE_HEALTH_FAILS="1")
    assert proc_b.returncode != 0
    assert b"rolling back" in proc_b.stdout
    current = Path(env["ATLAS_CURRENT_LINK"]).resolve()
    assert current == Path(env["ATLAS_RELEASES_DIR"]) / REV_A
    assert json.loads((current / "worker-image.json").read_text()) == previous


# ---------------------------------------------------------------------------
# controller: release-bound image identity
# ---------------------------------------------------------------------------


def _release(tmp_path: Path, *, revision=REV_A, binding=None, source_revision=None) -> Path:
    root = tmp_path / "release"
    root.mkdir()
    (root / ".source-revision").write_text((source_revision or revision) + "\n")
    doc = binding or {
        "schema_version": 1,
        "revision": revision,
        "tag": expected_tag(revision),
        "image_id": ID_X,
        "python_version": "3.12.14",
    }
    (root / "worker-image.json").write_text(json.dumps(doc))
    return root


def test_bind_config_pins_worker_image_to_release_binding(tmp_path, config):
    bound = bind_config(config, _release(tmp_path))
    assert bound.worker.image == f"atlas-runner-worker:{REV_A}"
    assert bound.worker.image_id == ID_X and bound.worker.image_revision == REV_A


def test_release_without_binding_keeps_configured_image(tmp_path, config):
    root = tmp_path / "legacy"
    root.mkdir()
    assert load_binding(root) is None
    assert bind_config(config, root) is config


@pytest.mark.parametrize(
    "mutation",
    [
        {"tag": "atlas-runner-worker:latest"},
        {"image_id": "latest"},
        {"python_version": "3.11.2"},
        {"revision": "nothex"},
        {"extra": 1},
    ],
)
def test_malformed_binding_fails_closed(tmp_path, mutation):
    doc = {
        "schema_version": 1,
        "revision": REV_A,
        "tag": expected_tag(REV_A),
        "image_id": ID_X,
        "python_version": "3.12.14",
    }
    doc.update(mutation)
    with pytest.raises(WorkerImageError):
        load_binding(_release(tmp_path, binding=doc))


def test_binding_for_another_revision_than_the_release_fails_closed(tmp_path):
    with pytest.raises(WorkerImageError):
        load_binding(_release(tmp_path, revision=REV_A, source_revision=REV_B))


def test_toml_cannot_set_the_release_binding():
    from controller.config import ConfigError, parse_config

    base = {"github": {"owner": "o", "repo": "r"}}
    for key in ("image_id", "image_revision"):
        with pytest.raises(ConfigError):
            parse_config({**base, "worker": {key: ID_X}})


def _bound(config, *, image_id=ID_X):
    worker = dataclasses.replace(
        config.worker, image=expected_tag(REV_A), image_id=image_id, image_revision=REV_A
    )
    return dataclasses.replace(config, worker=worker)


def test_worker_launches_only_from_the_bound_image(
    config, store, fake_docker, fake_github, fake_clock
):
    fake_docker.bound_image_id = ID_X
    execution_id = _submit(
        _bound(config), store, fake_docker, fake_github, fake_clock, _definition()
    )
    launches = [c for c in fake_docker.calls if c[0] == "run_worker"]
    assert len(launches) == 1
    assert launches[0][2]["image"] == expected_tag(REV_A)  # revision-specific, never latest
    assert store.get_execution(execution_id)["status"] != lifecycle.FAILED


def test_stale_host_image_blocks_worker_launch(config, store, fake_docker, fake_github, fake_clock):
    fake_docker.bound_image_id = "sha256:" + "e" * 64  # host tag moved / stale image
    execution_id = _submit(
        _bound(config), store, fake_docker, fake_github, fake_clock, _definition()
    )
    row = store.get_execution(execution_id)
    assert row["status"] == lifecycle.FAILED
    assert row["failure_reason"] == "provision_failed:WorkerImageMismatch"
    assert "run_worker" not in fake_docker.call_names()


def test_health_blocks_on_image_mismatch_and_passes_when_bound(
    config, store, fake_docker, fake_github
):
    store.heartbeat(1)
    fake_docker.bound_image_id = ID_X
    ok, code_ok = run_health(
        config=_bound(config), store=store, docker=fake_docker, github=fake_github,
        now=store.last_heartbeat(),
    )
    assert ok["checks"]["worker_image"]["ok"] is True and code_ok == 0
    assert ID_X in ok["checks"]["worker_image"]["detail"]
    fake_docker.bound_image_id = "sha256:" + "e" * 64
    bad, code_bad = run_health(
        config=_bound(config), store=store, docker=fake_docker, github=fake_github,
        now=store.last_heartbeat(),
    )
    assert bad["checks"]["worker_image"]["ok"] is False
    assert bad["status"] == "blocked" and code_bad == 2


def test_evidence_digest_is_the_content_id_for_locally_built_images(tmp_path):
    class Stub(DockerCtl):
        def _run(self, argv, *, timeout=None):
            if argv[:1] == ["images"]:  # no registry digest for a local build
                return subprocess.CompletedProcess(argv, 0, f"{expected_tag(REV_A)} <none>\n", "")
            assert argv[:2] == ["image", "inspect"]
            return subprocess.CompletedProcess(argv, 0, ID_X + "\n", "")

    assert Stub(jobs_root=tmp_path).image_digest(expected_tag(REV_A)) == ID_X
