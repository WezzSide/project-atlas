"""Worker lifecycle orchestration (AS-RUNNER-001): provision -> register ->
run -> collect -> deregister -> destroy.

Contract: one WorkerManager drives one execution through the lifecycle graph,
always through the state store (single writer of truth). Every terminal path
collects logs + evidence BEFORE container removal and attempts cleanup;
cleanup failure lands in CLEANUP_REQUIRED, never silent COMPLETE.
RUNNER_DEREGISTERED != VERIFIED; EPHEMERAL_EXIT != JOB_SUCCESS.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path

from controller import evidence, lifecycle
from controller.config import ControllerConfig
from controller.dockerctl import DockerCtl, worker_container_name
from controller.github import AuthError, GitHubClient, GitHubError, TokenProvider, redact_secrets
from controller.state import StateStore


class WorkerFailure(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class WorkerManager:
    def __init__(
        self,
        *,
        config: ControllerConfig,
        store: StateStore,
        docker: DockerCtl,
        github: GitHubClient | None,
        token_provider: TokenProvider | None = None,
        source_revision: str = "unknown",
        clock=time.time,
    ):
        self.config = config
        self.store = store
        self.docker = docker
        self.github = github
        self.token_provider = token_provider
        self.source_revision = source_revision
        self.clock = clock

    # -- paths ---------------------------------------------------------------
    def workspace_for(self, execution_id: str) -> Path:
        from atlas_contracts.identity import join_under_root

        return Path(
            join_under_root(
                Path(self.config.paths.jobs_dir),
                execution_id,
                label="jobs_dir",
            )
        )

    # -- registration ---------------------------------------------------------
    def _write_secret_file(self, workspace: Path, name: str, content: str) -> Path:
        """Write registration material readable by the in-container runner user.

        Mode 0644 (not 0600): the worker container runs as a non-root user
        whose host uid is not atlas-runner, so a 0600 file is unreadable
        inside the container and registration fails with an empty token
        (observed live on VPS-02: "Invalid configuration provided for
        token"). Exposure is bounded: the file lives in a 0750
        controller-owned directory, is mounted only into the disposable
        single-use worker, and the entrypoint deletes it right after
        registration.
        """
        target = workspace / name
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.chmod(target, 0o644)
        return target

    def register(self, execution_id: str, workspace: Path, runner_name: str) -> dict[str, str]:
        """Mint JIT (preferred) or registration-token fallback material."""
        if self.github is None:
            raise WorkerFailure("no_github_client")
        env: dict[str, str] = {"RUNNER_NAME": runner_name, "RUNNER_WORK_FOLDER": "/workspace/_work"}
        # Custom labels must also reach config.sh in the token fallback path,
        # or GitHub never assigns label-gated jobs to the runner (observed
        # live: runner registered with only the default self-hosted/linux/x64).
        env["RUNNER_LABELS"] = ",".join(self.config.labels)
        try:
            jit = self.github.generate_jitconfig(
                name=runner_name,
                labels=list(self.config.labels),
                work_folder="/workspace/_work",
            )
            encoded = jit.get("encoded_jit_config")
            if not encoded:
                raise WorkerFailure("jitconfig_missing")
            secret_file = self._write_secret_file(workspace, ".runner-jit", str(encoded))
            env["RUNNER_JIT_CONFIG_FILE"] = "/workspace/.runner-jit"
            env["RUNNER_EPHEMERAL"] = "true"
            return {"env": env, "secret_file": str(secret_file)}
        except AuthError:
            raise
        except GitHubError:
            # Fallback: classic registration token + ephemeral config path.
            token = self.github.generate_registration_token()
            secret_file = self._write_secret_file(workspace, ".runner-token", token)
            env["RUNNER_REGISTRATION_TOKEN_FILE"] = "/workspace/.runner-token"
            env["RUNNER_EPHEMERAL"] = "true"
            return {"env": env, "secret_file": str(secret_file)}

    # -- lifecycle --------------------------------------------------------------
    def run_execution(self, execution: dict, *, definition: dict) -> dict:
        """Drive one execution to a terminal state. Returns updated execution row."""
        execution_id = execution["execution_id"]
        task_id = execution["task_id"]
        store = self.store
        runner_name = definition.get("runner_name") or f"atlas-{execution_id}"
        worker_name = worker_container_name(execution_id)
        workspace = self.workspace_for(execution_id)
        workspace.mkdir(parents=True, exist_ok=True)
        # The mounted workspace may be owned by an arbitrary host uid; the
        # container-side runner user (non-root) must be able to write it.
        with contextlib.suppress(OSError):
            workspace.chmod(0o777)
        secrets: list[str] = self.token_provider.redaction_values() if self.token_provider else []
        started_iso = evidence.utc_now_iso()
        cleanup_status = "unknown"
        terminal_status = "unknown"
        exit_code: int | None = None
        failure_reason: str | None = None
        evidence_doc: dict | None = None

        try:
            store.set_execution_fields(
                execution_id, worker_name=worker_name, runner_name=runner_name
            )
            store.transition(execution_id, lifecycle.PROVISIONING)
            # --- provision + register -----------------------------------------
            try:
                reg = self.register(execution_id, workspace, runner_name)
            except AuthError as exc:
                raise WorkerFailure(f"auth:{exc}") from exc
            except WorkerFailure:
                raise
            except Exception as exc:
                raise WorkerFailure(f"registration_error:{type(exc).__name__}") from exc
            finally_secrets = secrets

            store.transition(execution_id, lifecycle.REGISTERING)
            store.transition(execution_id, lifecycle.READY)
            task_env = {k: str(v) for k, v in (definition.get("env") or {}).items()}
            env = {
                **reg["env"],
                "GITHUB_REPOSITORY": f"{self.config.github.owner}/{self.config.github.repo}",
                "ATLAS_EVIDENCE_DIR": "/workspace/evidence",
                "ATLAS_TASK_ID": task_id,
                "ATLAS_EXECUTION_ID": execution_id,
                **{k: str(v) for k, v in self.config.worker.env.items()},
                **task_env,
            }
            try:
                self.docker.run_worker(
                    name=worker_name,
                    image=self.config.worker.image,
                    workspace=workspace,
                    env=env,
                    cpus=self.config.worker.cpus,
                    memory_mb=self.config.worker.memory_mb,
                    pids=self.config.worker.pids,
                    timeout_seconds=self.config.worker.timeout_seconds,
                    network=self.config.worker.network,
                )
            except Exception as exc:
                self._safe_rm(reg["secret_file"])
                raise WorkerFailure(f"provision_failed:{type(exc).__name__}") from exc
            # NOTE: the secret file must NOT be deleted here. `docker run -d`
            # returns once the container is created, but the entrypoint reads
            # the registration material a moment later — deleting now races
            # the container start and intermittently yields an empty
            # --jitconfig ("Not configured"; observed live on VPS-02).
            # Deletion happens after the worker exits (see cleanup below);
            # the entrypoint also deletes the token file itself post-config.
            secrets = finally_secrets

            # --- run ------------------------------------------------------------
            store.transition(execution_id, lifecycle.ASSIGNED)
            store.transition(execution_id, lifecycle.RUNNING)
            deadline = self.clock() + self.config.worker.timeout_seconds
            exit_code = self._wait_for_exit(worker_name, deadline)
            if exit_code is None:
                self._enforce_timeout(worker_name)
                terminal_status, failure_reason = "timed_out", "job_timeout"
            elif exit_code == -1:
                terminal_status, failure_reason = "failed", "worker_lost"
            elif exit_code != 0:
                terminal_status, failure_reason = "failed", f"nonzero_exit:{exit_code}"
            else:
                terminal_status = "complete"
            store.transition(execution_id, lifecycle.COLLECTING_EVIDENCE)

            # --- collect evidence (BEFORE destruction) ---------------------------
            self._collect_logs(worker_name, secrets)
            evidence_doc = self._build_evidence(
                execution=execution,
                task_id=task_id,
                execution_id=execution_id,
                worker_name=worker_name,
                runner_name=runner_name,
                definition=definition,
                workspace=workspace,
                started_iso=started_iso,
                terminal_status=terminal_status,
                exit_code=exit_code if exit_code is not None else None,
                cleanup_status="unknown",
            )
            evidence_path = self._write_evidence_checked(evidence_doc, execution_id)
            if evidence_path:
                store.set_execution_fields(execution_id, evidence_path=str(evidence_path))

            # --- deregister + destroy --------------------------------------------
            store.transition(execution_id, lifecycle.DEREGISTERING)
            self._safe_rm(reg["secret_file"])  # worker has exited; safe now
            cleanup_status = self._deregister_and_destroy(worker_name, runner_name, secrets)
            evidence_doc["cleanup_status"] = cleanup_status
            self._write_evidence_checked(evidence_doc, execution_id)
            store.transition(execution_id, lifecycle.DESTROYING)

            final = {
                "complete": lifecycle.COMPLETE,
                "failed": lifecycle.FAILED,
                "timed_out": lifecycle.TIMED_OUT,
            }[terminal_status]
            if cleanup_status != "ok":
                store.transition(
                    execution_id, lifecycle.CLEANUP_REQUIRED,
                    terminal_status="cleanup_required",
                    failure_reason=failure_reason or "cleanup_failed",
                    cleanup_status=cleanup_status,
                )
            else:
                store.transition(
                    execution_id, final,
                    terminal_status=terminal_status,
                    failure_reason=failure_reason,
                    cleanup_status="ok",
                )
        except WorkerFailure as exc:
            failure_reason = exc.reason
            terminal_status = "failed"
            self._cleanup_after_failure(worker_name, secrets)
            self._fail_if_not_terminal(execution_id, failure_reason)
        except Exception as exc:  # defensive: never leave a half-terminal row
            self._cleanup_after_failure(worker_name, secrets)
            self._fail_if_not_terminal(execution_id, f"internal:{type(exc).__name__}")
        return self.store.get_execution(execution_id) or {}

    def _fail_if_not_terminal(self, execution_id: str, reason: str) -> None:
        row = self.store.get_execution(execution_id)
        if row is None or lifecycle.is_terminal(row["status"]):
            return  # already terminal (e.g. evidence_validation failure path)
        self.store.transition(
            execution_id, lifecycle.FAILED,
            terminal_status="failed", failure_reason=reason, cleanup_status="unknown",
        )

    # -- helpers -----------------------------------------------------------------
    def _wait_for_exit(self, worker_name: str, deadline: float, *, poll: float = 2.0) -> int | None:
        """Poll container state; return exit code, None on timeout, -1 on lost."""
        while self.clock() < deadline:
            try:
                info = self.docker.inspect(worker_name)
            except Exception:
                return -1  # worker_lost
            state = info.get("State", {})
            if state.get("Running"):
                self.clock_sleep(min(poll, 5.0))
                continue
            return int(state.get("ExitCode", 1))
        return None

    def clock_sleep(self, seconds: float) -> None:
        """Advance an injectable fake clock; real time stays untouched."""
        advance = getattr(self.clock, "sleep", None)
        if callable(advance):
            advance(seconds)
        time.sleep(0)

    def _enforce_timeout(self, worker_name: str) -> None:
        with contextlib.suppress(Exception):
            self.docker.stop(worker_name, grace_seconds=10)
        with contextlib.suppress(Exception):
            self.docker.kill(worker_name)

    def _collect_logs(self, worker_name: str, secrets: list[str]) -> str:
        try:
            raw = self.docker.logs(worker_name)
        except Exception:
            short = worker_name.removeprefix("atlas-worker-")
            workspace_log = self.workspace_for(short) / "worker.log"
            try:
                raw = workspace_log.read_text(encoding="utf-8", errors="replace")
            except OSError:
                raw = ""
        redacted = redact_secrets(raw, secrets)
        log_dir = Path(self.config.paths.log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        short = worker_name.removeprefix("atlas-worker-")
        (log_dir / f"{short}.log").write_text(redacted, encoding="utf-8")
        return redacted

    def _build_evidence(
        self,
        *,
        execution: dict,
        task_id: str,
        execution_id: str,
        worker_name: str,
        runner_name: str,
        definition: dict,
        workspace: Path,
        started_iso: str,
        terminal_status: str,
        exit_code: int | None,
        cleanup_status: str,
    ) -> dict:
        from controller import CONTROLLER_VERSION

        fragment = self._read_evidence_fragment(workspace)
        base_revision = self._resolve_revision(
            "base_revision",
            definition_value=definition.get("base_revision"),
            fragment_value=fragment.get("base_revision"),
        )
        result_revision = self._resolve_revision(
            "result_revision",
            definition_value=definition.get("result_revision"),
            fragment_value=fragment.get("result_revision"),
        )
        artifact_names = list(fragment.get("artifacts", []))
        artifact_hashes = evidence.collect_artifact_hashes(workspace, artifact_names)
        image_digest = self.docker.image_digest(self.config.worker.image)
        finished_iso = evidence.utc_now_iso()
        return evidence.build_evidence(
            task_id=task_id,
            execution_id=execution_id,
            github_run_id=int(
                execution.get("github_run_id") or definition.get("github_run_id") or 0
            ),
            github_run_attempt=int(execution.get("github_run_attempt") or 1),
            worker_id=worker_name,
            runner_name=runner_name,
            runner_labels=list(self.config.labels),
            runner_image_digest=image_digest,
            repository=f"{self.config.github.owner}/{self.config.github.repo}",
            base_revision=base_revision,
            result_revision=result_revision,
            started_at=started_iso,
            finished_at=finished_iso,
            terminal_status=terminal_status,
            exit_code=exit_code,
            tests=dict(fragment.get("tests", {})),
            artifacts=sorted(artifact_hashes),
            artifact_sha256=artifact_hashes,
            cleanup_status=cleanup_status,
            controller_version=CONTROLLER_VERSION,
            source_revision=self.source_revision,
        )

    def _read_evidence_fragment(self, workspace: Path) -> dict:
        path = Path(workspace) / "evidence" / "fragment.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _resolve_revision(
        field: str,
        *,
        definition_value: str | None,
        fragment_value: str | None,
    ) -> str | None:
        """Resolve revision provenance with explicit conflict semantics.

        Task definition and evidence fragment are both provenance signals.
        Conflicts fail closed; unknown stays unknown.
        """
        if (
            definition_value is not None
            and fragment_value is not None
            and definition_value != fragment_value
        ):
            raise WorkerFailure(f"evidence_{field}_conflict")
        if definition_value is not None:
            return definition_value
        if fragment_value is not None:
            return fragment_value
        return None

    def _write_evidence_checked(self, doc: dict, execution_id: str) -> Path | None:
        """Write evidence; a validation failure must fail closed to FAILED."""
        target = self.workspace_for(execution_id) / "evidence.json"
        try:
            return evidence.write_evidence(doc, target)
        except evidence.EvidenceError as exc:
            self.store.transition(
                execution_id, lifecycle.FAILED,
                terminal_status="failed", failure_reason="evidence_validation",
                cleanup_status="unknown",
            )
            raise WorkerFailure("evidence_validation") from exc

    def _deregister_and_destroy(
        self, worker_name: str, runner_name: str, secrets: list[str]
    ) -> str:
        """Verify ephemeral deregistration where queryable; destroy container."""
        cleanup = "ok"
        if self.github is not None and self.token_provider is not None:
            try:
                runners = self.github.list_runners()
                still = [r for r in runners if r.get("name") == runner_name]
                if still:
                    time.sleep(0)  # grace already covered by ephemeral semantics
                    for runner in still:
                        try:
                            self.github.delete_runner(int(runner["id"]))
                        except GitHubError:
                            cleanup = "failed"
            except GitHubError:
                cleanup = "unknown"
        try:
            if self.docker.container_exists(worker_name):
                self.docker.rm(worker_name)
        except Exception:
            cleanup = "failed"
        return cleanup

    def _cleanup_after_failure(self, worker_name: str, secrets: list[str]) -> None:
        try:
            if self.docker.container_exists(worker_name):
                self.docker.rm(worker_name)
        except Exception:
            pass

    @staticmethod
    def _safe_rm(path: str) -> None:
        with contextlib.suppress(OSError):
            os.unlink(path)
