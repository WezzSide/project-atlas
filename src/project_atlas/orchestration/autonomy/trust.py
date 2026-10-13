"""Trusted-anchor load, verify, and fail-closed advancement.

BOOTSTRAP_MAIN/TREE are historical genesis only.
TRUSTED_RUNTIME_MAIN/TREE come from a verified record.
OBSERVED_MAIN/TREE are live facts and never become authority by themselves.

GOVERNOR_CAN_INVENT_OWNER_AUTHORITY = NO
GOVERNOR_CAN_ADVANCE_ANCHOR_FROM_OBSERVED_MAIN_ONLY = NO
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Protocol

from project_atlas.orchestration.autonomy.evidence import hash_payload
from project_atlas.orchestration.autonomy.models import (
    BOOTSTRAP_MAIN,
    BOOTSTRAP_TREE,
    CANONICAL_REPOSITORY_IDENTITY,
    INITIAL_RETARGET_CERTIFIED_HEAD,
    INITIAL_RETARGET_EVIDENCE_DIGEST,
    INITIAL_RETARGET_MAIN,
    INITIAL_RETARGET_SOURCE_DIRECTIVE,
    INITIAL_RETARGET_SOURCE_PR,
    INITIAL_RETARGET_TREE,
    LIVE_REPOSITORY_IDENTITY,
    MAX_FIRST_PARENT_CHECKPOINT_HOPS,
    PIN_RETARGET_PACKAGE_ID,
    AdvancementProof,
    AdvancementReason,
    CatchupHopProof,
    TrustCatchupProof,
    TrustCheckpointProof,
    TrustedAnchorRecord,
    TrustState,
)
from project_atlas.source_identity import IdentityLockError, ProjectIdentityLock

SHIPPED_ANCHOR_NAME = "autonomy-trusted-anchor-initial.json"
CURRENT_RECORD_NAME = "current.json"
HISTORY_DIR_NAME = "history"
LOCK_NAME = ".anchor.lock"
PLACEHOLDER_DIGEST = "0" * 64
_PIN_RE = re.compile(r"^[0-9a-f]{40}$")
_IDENTITY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,255}$")


class TrustError(ValueError):
    """Fail-closed trust-anchor error. Not an authority grant."""

    code = "TRUST_UNVERIFIABLE"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        if code is not None:
            self.code = code


class GitTopology(Protocol):
    """Observed git facts used to verify a proof. Not an authority source."""

    def observe_main(self) -> tuple[str, str]:
        """Return (commit, tree) for the configured main ref."""

    def commit_exists(self, sha: str) -> bool:
        """True only when ``sha`` is a full-name commit object."""

    def tree_of(self, sha: str) -> str:
        """Return the 40-char tree SHA of a commit."""

    def parents_of(self, sha: str) -> tuple[str, ...]:
        """Return parent commit SHAs in git order."""

    def is_descendant(self, child: str, ancestor: str) -> bool:
        """Topology helper. Never sufficient to advance trust."""


@dataclass(frozen=True)
class AdvancementChecks:
    """§10 checklist. All fields must be True before advancement."""

    owner_authorization_proven: bool
    expected_previous_main_match: bool
    authorized_candidate_head_match: bool
    authorized_candidate_tree_match: bool
    merge_parent_1_match: bool
    merge_parent_2_match: bool
    merge_tree_match: bool
    post_merge_seal: bool
    required_post_merge_ci: bool
    evidence_integrity: bool

    @property
    def all_required(self) -> bool:
        return all(
            (
                self.owner_authorization_proven,
                self.expected_previous_main_match,
                self.authorized_candidate_head_match,
                self.authorized_candidate_tree_match,
                self.merge_parent_1_match,
                self.merge_parent_2_match,
                self.merge_tree_match,
                self.post_merge_seal,
                self.required_post_merge_ci,
                self.evidence_integrity,
            )
        )


def require_full_pin(value: str, label: str) -> str:
    if not _PIN_RE.fullmatch(value):
        raise TrustError(f"{label} is not a 40-char lowercase git SHA", code="PIN_INVALID")
    return value


def normalize_repository_identity(remote_url: str) -> str:
    """Normalize a git remote to ``host/owner/name`` without a scheme."""
    raw = remote_url.strip()
    if not raw:
        raise TrustError("repository identity is empty", code="REPO_IDENTITY_UNVERIFIABLE")
    if raw.startswith("git@"):
        _, remainder = raw.split("@", 1)
        raw = remainder.replace(":", "/", 1)
    for prefix in ("https://", "http://", "ssh://", "git://"):
        if raw.lower().startswith(prefix):
            raw = raw[len(prefix) :]
    raw = raw.removesuffix(".git").strip("/")
    if ".." in raw.split("/") or "\\" in raw or ":" in raw:
        raise TrustError("repository identity is unsafe", code="REPO_IDENTITY_UNVERIFIABLE")
    identity = raw.lower()
    if not _IDENTITY_RE.fullmatch(identity):
        raise TrustError("repository identity is malformed", code="REPO_IDENTITY_UNVERIFIABLE")
    return identity


def repository_identities_match(left: str, right: str) -> bool:
    """True when both names are this repository, including the owner transfer.

    ``CANONICAL_REPOSITORY_IDENTITY`` is the sealed pin (pre-transfer
    owner). ``LIVE_REPOSITORY_IDENTITY`` is the current GitHub owner.
    They are the same repo; they are not interchangeable with any other
    host or owner.
    """
    a = left.casefold()
    b = right.casefold()
    if a == b:
        return True
    equivalent = {
        CANONICAL_REPOSITORY_IDENTITY,
        LIVE_REPOSITORY_IDENTITY,
        "github.com/bolkdev/project-atlas",
    }
    return a in equivalent and b in equivalent


def seal_anchor(record: TrustedAnchorRecord) -> TrustedAnchorRecord:
    digest = hash_payload(record.unsigned_payload())
    return record.model_copy(update={"record_digest": digest})


def verify_anchor_integrity(record: TrustedAnchorRecord) -> TrustedAnchorRecord:
    expected = hash_payload(record.unsigned_payload())
    if record.record_digest != expected:
        raise TrustError("trusted-anchor record digest mismatch", code="HASH_INVALID")
    return record


def build_initial_retarget_record() -> TrustedAnchorRecord:
    """Materialize the owner-specified #398 retarget. Does not read origin/main."""
    unsigned = TrustedAnchorRecord(
        repository_identity=CANONICAL_REPOSITORY_IDENTITY,
        trusted_main=INITIAL_RETARGET_MAIN,
        trusted_tree=INITIAL_RETARGET_TREE,
        predecessor_main=BOOTSTRAP_MAIN,
        predecessor_tree=BOOTSTRAP_TREE,
        advancement_reason=AdvancementReason.VERIFIED_OWNER_AUTHORIZED_MERGE,
        source_package=PIN_RETARGET_PACKAGE_ID,
        source_directive=INITIAL_RETARGET_SOURCE_DIRECTIVE,
        source_pr=INITIAL_RETARGET_SOURCE_PR,
        merge_commit=INITIAL_RETARGET_MAIN,
        merge_parent_1=BOOTSTRAP_MAIN,
        merge_parent_2=INITIAL_RETARGET_CERTIFIED_HEAD,
        merge_tree=INITIAL_RETARGET_TREE,
        certified_head=INITIAL_RETARGET_CERTIFIED_HEAD,
        certified_tree=INITIAL_RETARGET_TREE,
        certification_status="CERTIFIED",
        independent_verification_status="PASS",
        post_merge_seal="PASS",
        post_merge_ci="PASS",
        evidence_reference="as-orch-autonomy-001-merge-002/FINAL_REPORT.md",
        evidence_digest=INITIAL_RETARGET_EVIDENCE_DIGEST,
        sequence=1,
        record_digest=PLACEHOLDER_DIGEST,
    )
    return seal_anchor(unsigned)


def verify_initial_retarget_record(record: TrustedAnchorRecord) -> TrustedAnchorRecord:
    verify_anchor_integrity(record)
    expected = build_initial_retarget_record()
    if record.model_dump(mode="json") != expected.model_dump(mode="json"):
        raise TrustError(
            "shipped retarget record does not match sealed #398 pins",
            code="SHIPPED_MISMATCH",
        )
    if record.trusted_main == record.certified_head:
        raise TrustError("commit identity collapsed into certified HEAD", code="IDENTITY_COLLAPSE")
    if record.trusted_tree != record.certified_tree:
        raise TrustError("merge tree is not the certified content identity", code="TREE_MISMATCH")
    return record


def load_shipped_initial_anchor() -> TrustedAnchorRecord:
    packaged = resources.files("project_atlas").joinpath("data", SHIPPED_ANCHOR_NAME)
    if not packaged.is_file():
        raise TrustError("shipped trusted-anchor record is missing", code="TRUST_UNVERIFIABLE")
    try:
        payload = json.loads(packaged.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TrustError(
            "shipped trusted-anchor record is unreadable",
            code="TRUST_UNVERIFIABLE",
        ) from exc
    try:
        record = TrustedAnchorRecord.model_validate(payload)
    except Exception as exc:
        raise TrustError(
            "shipped trusted-anchor record is schema-invalid",
            code="TRUST_UNVERIFIABLE",
        ) from exc
    return verify_initial_retarget_record(record)


def load_runtime_anchor(
    *,
    store: Path | None = None,
    explicit: TrustedAnchorRecord | None = None,
    allow_shipped: bool = False,
    expected_repository_identity: str | None = None,
) -> TrustedAnchorRecord:
    """Load trust. Never initializes from origin/main.

    Precedence: explicit record, then store (no shipped fallback), then shipped
    only when ``allow_shipped`` is True.
    """
    if explicit is not None:
        record = verify_anchor_integrity(explicit)
    elif store is not None:
        record = _load_store_current(store)
    elif allow_shipped:
        record = load_shipped_initial_anchor()
    else:
        raise TrustError(
            "no trust record and no explicit bootstrap authority",
            code="TRUST_UNVERIFIABLE",
        )
    if (
        expected_repository_identity is not None
        and not repository_identities_match(
            record.repository_identity, expected_repository_identity
        )
    ):
        raise TrustError(
            "trusted-anchor repository identity does not match this repository",
            code="REPO_IDENTITY_MISMATCH",
        )
    return record


def evaluate_target_moved(
    observed_main: str,
    observed_tree: str,
    trusted: TrustedAnchorRecord,
) -> bool:
    require_full_pin(observed_main, "observed_main")
    require_full_pin(observed_tree, "observed_tree")
    return observed_main != trusted.trusted_main or observed_tree != trusted.trusted_tree


def classify_observation(
    observed_main: str,
    observed_tree: str,
    trusted: TrustedAnchorRecord,
    *,
    descendant_of_trusted: bool = False,
) -> TrustState:
    """Classify live observation. Descendant-only is never sufficient authority."""
    del descendant_of_trusted
    if evaluate_target_moved(observed_main, observed_tree, trusted):
        return TrustState.TARGET_MOVED
    return TrustState.TRUSTED


def _advancement_evidence_binding(proof: AdvancementProof) -> dict[str, object]:
    """The exact structure an ordinary single-hop advancement proof's
    ``evidence_digest`` must hash over.

    Follow-up finding (same class of gap independently found and fixed for
    checkpoint recovery, PR #664/#667, and bounded catch-up, PR #666):
    previously ``verify_evidence_integrity()`` hashed ONLY
    ``evidence_payload`` in isolation, leaving ``evidence_digest`` self-
    referential -- a legitimately-authorized evidence payload/digest pair
    could be lifted, unchanged, onto an entirely different proof (different
    target, different candidate, different owner-claimed provenance) and
    still pass integrity, even though ``_record_from_verified_proof()``
    persists every one of those fields verbatim into the sealed
    ``TrustedAnchorRecord``. The topological fields here (``merge_commit``/
    ``merge_tree``/``merge_parent_1``/``merge_parent_2``/
    ``authorized_candidate_head``/``_tree``) are already independently
    re-verified against LIVE git topology by ``evaluate_advancement()``
    regardless of this binding (so this was never a trust-TARGET forgery
    path) -- but a swapped ``source_package``/``source_directive``/
    ``source_pr``/``evidence_reference`` (WHICH work item/directive/
    evidence document actually authorized this advance) was not
    independently checked anywhere and would have gone undetected. Every
    field a reader would need to know "what did the owner actually
    authorize" is included here, matching the pattern the checkpoint and
    catch-up proofs already used.
    """
    return {
        "evidence_payload": proof.evidence_payload,
        "repository_identity": proof.repository_identity,
        "owner_authorization": proof.owner_authorization,
        "expected_previous_main": proof.expected_previous_main,
        "expected_previous_tree": proof.expected_previous_tree,
        "authorized_candidate_head": proof.authorized_candidate_head,
        "authorized_candidate_tree": proof.authorized_candidate_tree,
        "merge_commit": proof.merge_commit,
        "merge_parent_1": proof.merge_parent_1,
        "merge_parent_2": proof.merge_parent_2,
        "merge_tree": proof.merge_tree,
        "post_merge_seal": proof.post_merge_seal,
        "post_merge_ci": proof.post_merge_ci,
        "source_package": proof.source_package,
        "source_directive": proof.source_directive,
        "source_pr": proof.source_pr,
        "evidence_reference": proof.evidence_reference,
    }


def verify_evidence_integrity(proof: AdvancementProof) -> bool:
    if proof.evidence_payload is None:
        return False
    return hash_payload(_advancement_evidence_binding(proof)) == proof.evidence_digest


def evaluate_advancement(
    current: TrustedAnchorRecord,
    proof: AdvancementProof,
    topology: GitTopology,
    *,
    observed_main: str,
    observed_tree: str,
) -> AdvancementChecks:
    """Evaluate §10. Does not invent owner authorization or mutate state."""
    parents = (
        topology.parents_of(proof.merge_commit)
        if topology.commit_exists(proof.merge_commit)
        else ()
    )
    merge_tree = (
        topology.tree_of(proof.merge_commit) if topology.commit_exists(proof.merge_commit) else ""
    )
    head_exists = topology.commit_exists(proof.authorized_candidate_head)
    head_tree = topology.tree_of(proof.authorized_candidate_head) if head_exists else ""
    # Exactly 2, not >= 2 (IV finding on this same PR: the merge_tree/
    # candidate_tree equality this PR removes had, as an accidental side
    # effect, been the only thing standing between an octopus merge (3+
    # parents) and a real content-injection path -- a third, uncredited
    # parent could smuggle its content into `trusted_tree` while
    # `parent_1_ok`/`parent_2_ok` only ever inspect parents[0]/parents[1].
    # Same octopus-merge rationale already applied to checkpoint recovery
    # and bounded catch-up (PR #664 IV finding) -- ordinary advancement
    # never had the equivalent protection on its own, and this PR is what
    # makes that gap exploitable rather than inert, so it closes here too.
    parent_1_ok = len(parents) == 2 and parents[0] == proof.merge_parent_1
    parent_2_ok = len(parents) == 2 and parents[1] == proof.merge_parent_2
    return AdvancementChecks(
        owner_authorization_proven=proof.owner_authorization == "OWNER_AUTHORIZED",
        expected_previous_main_match=(
            proof.expected_previous_main == current.trusted_main
            and proof.expected_previous_tree == current.trusted_tree
            and proof.merge_parent_1 == current.trusted_main
        ),
        authorized_candidate_head_match=(
            head_exists
            and proof.authorized_candidate_head == proof.merge_parent_2
            and proof.merge_parent_2 in parents
        ),
        authorized_candidate_tree_match=(
            head_exists and head_tree == proof.authorized_candidate_tree
        ),
        merge_parent_1_match=(
            parent_1_ok and proof.merge_parent_1 == current.trusted_main
        ),
        merge_parent_2_match=(
            parent_2_ok and proof.merge_parent_2 == proof.authorized_candidate_head
        ),
        # Deliberately does NOT require `proof.merge_tree ==
        # proof.authorized_candidate_tree` (removed; real incident, PR
        # #669: a genuine, honest GitHub 3-way merge commit -- the PR's
        # own branch predated other merges since landed on main, so its
        # tree necessarily differs from both the resulting merge tree and
        # its own branch tree once combined -- was structurally denied by
        # this equality even though every fact it would have added is
        # ALREADY independently verified below: `merge_tree` is checked
        # against the REAL tree of `merge_commit`
        # (`topology.tree_of(...)`, not the proof's own claim about
        # itself), and `authorized_candidate_tree_match` separately
        # checks `authorized_candidate_tree` against the REAL tree of
        # `authorized_candidate_head`. Forcing those two independently-
        # true facts to also equal EACH OTHER only holds for the special
        # case of a fast-forward-content merge (no divergent content on
        # either side to combine) -- it added no verification beyond what
        # the two checks below already provide, and silently rejected
        # every equally-legitimate ordinary 3-way merge, which is a
        # completely normal occurrence whenever two PRs land on main
        # without both being freshly rebased against each other first.
        merge_tree_match=(
            bool(merge_tree)
            and merge_tree == proof.merge_tree
            and observed_tree == proof.merge_tree
            and observed_main == proof.merge_commit
        ),
        post_merge_seal=proof.post_merge_seal == "PASS",
        required_post_merge_ci=proof.post_merge_ci == "PASS",
        evidence_integrity=verify_evidence_integrity(proof),
    )


def _record_from_verified_proof(
    current: TrustedAnchorRecord,
    proof: AdvancementProof,
) -> TrustedAnchorRecord:
    unsigned = TrustedAnchorRecord(
        repository_identity=proof.repository_identity,
        trusted_main=proof.merge_commit,
        trusted_tree=proof.merge_tree,
        predecessor_main=current.trusted_main,
        predecessor_tree=current.trusted_tree,
        advancement_reason=AdvancementReason.VERIFIED_OWNER_AUTHORIZED_MERGE,
        source_package=proof.source_package,
        source_directive=proof.source_directive,
        source_pr=proof.source_pr,
        merge_commit=proof.merge_commit,
        merge_parent_1=proof.merge_parent_1,
        merge_parent_2=proof.merge_parent_2,
        merge_tree=proof.merge_tree,
        certified_head=proof.authorized_candidate_head,
        certified_tree=proof.authorized_candidate_tree,
        certification_status="CERTIFIED",
        independent_verification_status="PASS",
        post_merge_seal="PASS",
        post_merge_ci="PASS",
        evidence_reference=proof.evidence_reference,
        evidence_digest=proof.evidence_digest,
        sequence=current.sequence + 1,
        record_digest=PLACEHOLDER_DIGEST,
    )
    return seal_anchor(unsigned)


def advance_trusted_anchor(
    current: TrustedAnchorRecord,
    proof: AdvancementProof,
    topology: GitTopology,
    *,
    store: Path | None = None,
    expected_repository_identity: str | None = None,
) -> TrustedAnchorRecord:
    """OBSERVE → VERIFY → REOBSERVE → COMPARE → ATOMIC_ADVANCE."""
    if expected_repository_identity is not None:
        if not repository_identities_match(
            proof.repository_identity, expected_repository_identity
        ):
            raise TrustError("proof repository identity mismatch", code="REPO_IDENTITY_MISMATCH")
        if not repository_identities_match(
            current.repository_identity, expected_repository_identity
        ):
            raise TrustError("current repository identity mismatch", code="REPO_IDENTITY_MISMATCH")
    if not repository_identities_match(
        proof.repository_identity, current.repository_identity
    ):
        raise TrustError(
            "cross-repository anchor reuse is forbidden",
            code="REPO_IDENTITY_MISMATCH",
        )
    if proof.expected_previous_main != current.trusted_main:
        raise TrustError("stale or concurrent predecessor", code="PREDECESSOR_MISMATCH")

    observed_main, observed_tree = topology.observe_main()
    require_full_pin(observed_main, "observed_main")
    require_full_pin(observed_tree, "observed_tree")
    if not topology.commit_exists(proof.merge_commit):
        raise TrustError("proof references a nonexistent merge commit", code="GIT_OBJECT_MISSING")
    if not topology.commit_exists(proof.authorized_candidate_head):
        raise TrustError("proof references a nonexistent certified HEAD", code="GIT_OBJECT_MISSING")

    checks = evaluate_advancement(
        current,
        proof,
        topology,
        observed_main=observed_main,
        observed_tree=observed_tree,
    )
    if not checks.all_required:
        raise TrustError("verified advancement proof is incomplete", code="ADVANCEMENT_DENIED")

    re_main, re_tree = topology.observe_main()
    if (re_main, re_tree) != (observed_main, observed_tree):
        raise TrustError(
            "live state changed during verification",
            code="TARGET_MOVED_DURING_VERIFICATION",
        )
    if re_main != proof.merge_commit or re_tree != proof.merge_tree:
        raise TrustError(
            "re-observed main does not match authorized merge",
            code="ADVANCEMENT_DENIED",
        )

    new_record = _record_from_verified_proof(current, proof)
    if new_record.predecessor_main != current.trusted_main:
        raise TrustError("history monotonicity violated", code="PREDECESSOR_MISMATCH")
    if store is None:
        return new_record
    return compare_and_advance(store, current, new_record)


def _walk_first_parent_chain(
    topology: GitTopology, target: str, ancestor: str
) -> tuple[int, str] | None:
    """Walk ONLY first-parent edges from ``target`` looking for ``ancestor``.

    Deliberately never uses ``git merge-base --is-ancestor`` (or any other
    "reachable via SOME path" check) -- a commit merged in from a side
    branch could make an unrelated commit reachable that way without it
    ever having been on the trunk (first-parent) history a stale runtime
    anchor needs to be recertified against. This function follows
    ``topology.parents_of(sha)[0]`` repeatedly starting at ``target`` and
    returns ``(hop_count, chain_digest)`` only if ``ancestor`` is found
    exactly that way. Returns ``None`` if ``target == ancestor`` (a
    checkpoint must strictly advance, never a same-commit no-op), if the
    walk reaches a root (no parents) without finding ``ancestor``, or if
    the bounded walk is exhausted first. ``chain_digest`` is a
    deterministic SHA-256 over the exact ordered chain of commit SHAs
    walked (``target`` first, ``ancestor`` last, newline-joined) so a
    proof cannot claim a hop count or digest that doesn't correspond to
    genuinely-observed topology.
    """
    if target == ancestor:
        return None
    chain = [target]
    current = target
    for _ in range(MAX_FIRST_PARENT_CHECKPOINT_HOPS):
        parents = topology.parents_of(current)
        if not parents:
            return None
        current = parents[0]
        chain.append(current)
        if current == ancestor:
            digest = hashlib.sha256("\n".join(chain).encode("utf-8")).hexdigest()
            return len(chain) - 1, digest
    return None


@dataclass(frozen=True)
class CheckpointChecks:
    """Checklist for a ``TrustCheckpointProof``. All fields must be True
    before a checkpoint recovery may proceed."""

    owner_authorization_proven: bool
    checkpoint_reason_valid: bool
    repository_identity_match: bool
    expected_previous_match: bool
    target_matches_observed: bool
    target_merge_parents_match: bool
    certified_candidate_match: bool
    first_parent_ancestry: bool
    first_parent_hop_count_match: bool
    first_parent_chain_digest_match: bool
    post_merge_seal: bool
    post_merge_ci: bool
    independent_verification: bool
    evidence_integrity: bool

    @property
    def all_required(self) -> bool:
        return all(
            (
                self.owner_authorization_proven,
                self.checkpoint_reason_valid,
                self.repository_identity_match,
                self.expected_previous_match,
                self.target_matches_observed,
                self.target_merge_parents_match,
                self.certified_candidate_match,
                self.first_parent_ancestry,
                self.first_parent_hop_count_match,
                self.first_parent_chain_digest_match,
                self.post_merge_seal,
                self.post_merge_ci,
                self.independent_verification,
                self.evidence_integrity,
            )
        )


def _checkpoint_evidence_binding(proof: TrustCheckpointProof) -> dict[str, object]:
    """The exact structure ``evidence_digest`` must hash over.

    BINDS the evidence to this specific checkpoint's own security-
    relevant claims (IV finding, PR #664: hashing ``evidence_payload``
    alone left ``evidence_digest`` self-referential -- a legitimately-
    authorized evidence payload/digest pair for one target could be
    lifted, unchanged, onto an entirely different topology-valid proof
    for a DIFFERENT target/ancestry/authorization and still pass, since
    nothing tied the evidence to WHICH checkpoint it was meant to
    certify). Every field a reader would need to know "what did the
    owner actually authorize" is included, so a digest computed for one
    proof can never validate a different one.

    ``source_package``/``source_directive``/``source_pr``/
    ``evidence_reference`` added (same class of gap independently found
    and fixed for ordinary advancement in PR #668, and for bounded
    catch-up in PR #666 -- these four fields are persisted verbatim into
    the sealed ``TrustedAnchorRecord`` by
    ``_record_from_verified_checkpoint()``, so a checkpoint's provenance
    -- WHICH work item/directive/evidence document actually authorized
    it -- must be exactly as tamper-evident as its topology/
    authorization fields already were). Safe to change without
    invalidating this repository's real, already-persisted checkpoint
    record: ``TrustedAnchorRecord.record_digest`` is a wholly separate
    hash over the RECORD's own fields (``verify_anchor_integrity``/
    ``seal_anchor``), never over this binding; this function is only
    ever consulted while VERIFYING an incoming ``TrustCheckpointProof``,
    which the one-time ``_checkpoint_already_used`` gate makes
    structurally unreachable again for this store regardless.
    """
    return {
        "evidence_payload": proof.evidence_payload,
        "repository_identity": proof.repository_identity,
        "owner_authorization": proof.owner_authorization,
        "checkpoint_reason": proof.checkpoint_reason,
        "expected_previous_main": proof.expected_previous_main,
        "expected_previous_tree": proof.expected_previous_tree,
        "target_main": proof.target_main,
        "target_tree": proof.target_tree,
        "target_merge_parent_1": proof.target_merge_parent_1,
        "target_merge_parent_2": proof.target_merge_parent_2,
        "certified_candidate_head": proof.certified_candidate_head,
        "certified_candidate_tree": proof.certified_candidate_tree,
        "first_parent_hop_count": proof.first_parent_hop_count,
        "first_parent_chain_digest": proof.first_parent_chain_digest,
        "post_merge_seal": proof.post_merge_seal,
        "post_merge_ci": proof.post_merge_ci,
        "independent_verification": proof.independent_verification,
        "source_package": proof.source_package,
        "source_directive": proof.source_directive,
        "source_pr": proof.source_pr,
        "evidence_reference": proof.evidence_reference,
    }


def verify_checkpoint_evidence_integrity(proof: TrustCheckpointProof) -> bool:
    return hash_payload(_checkpoint_evidence_binding(proof)) == proof.evidence_digest


def evaluate_checkpoint_recovery(
    current: TrustedAnchorRecord,
    proof: TrustCheckpointProof,
    topology: GitTopology,
    *,
    observed_main: str,
    observed_tree: str,
) -> CheckpointChecks:
    """Evaluate a ``TrustCheckpointProof`` against live git topology. Does
    not invent owner authorization, does not accept ANY-path ancestry
    (only a genuine first-parent walk, see ``_walk_first_parent_chain``),
    and does not mutate state."""
    target_exists = topology.commit_exists(proof.target_main)
    target_tree = topology.tree_of(proof.target_main) if target_exists else ""
    parents = topology.parents_of(proof.target_main) if target_exists else ()
    # Exactly 2, not >= 2 (IV finding, PR #664): an octopus merge (3+
    # parents) satisfying only the first two would let a proof describe
    # an incomplete parent set while still being accepted as though it
    # fully described the target commit -- contradicting this record's
    # own "ACTUAL git parents" truthfulness claim.
    parent_1_ok = len(parents) == 2 and parents[0] == proof.target_merge_parent_1
    parent_2_ok = len(parents) == 2 and parents[1] == proof.target_merge_parent_2
    candidate_exists = topology.commit_exists(proof.certified_candidate_head)
    candidate_tree = topology.tree_of(proof.certified_candidate_head) if candidate_exists else ""
    walk = (
        _walk_first_parent_chain(topology, proof.target_main, current.trusted_main)
        if target_exists
        else None
    )
    hop_count, chain_digest = walk if walk is not None else (0, "")
    return CheckpointChecks(
        owner_authorization_proven=proof.owner_authorization == "OWNER_AUTHORIZED",
        checkpoint_reason_valid=proof.checkpoint_reason == "STALE_RUNTIME_ANCHOR_RECOVERY",
        repository_identity_match=proof.repository_identity == current.repository_identity,
        expected_previous_match=(
            proof.expected_previous_main == current.trusted_main
            and proof.expected_previous_tree == current.trusted_tree
        ),
        target_matches_observed=(
            observed_main == proof.target_main and observed_tree == proof.target_tree
        ),
        target_merge_parents_match=(
            target_exists and target_tree == proof.target_tree and parent_1_ok and parent_2_ok
        ),
        certified_candidate_match=(
            candidate_exists
            and candidate_tree == proof.certified_candidate_tree
            and proof.certified_candidate_head == proof.target_merge_parent_2
        ),
        first_parent_ancestry=walk is not None,
        first_parent_hop_count_match=(
            walk is not None and hop_count == proof.first_parent_hop_count
        ),
        first_parent_chain_digest_match=(
            walk is not None and chain_digest == proof.first_parent_chain_digest
        ),
        post_merge_seal=proof.post_merge_seal == "PASS",
        post_merge_ci=proof.post_merge_ci == "PASS",
        independent_verification=proof.independent_verification == "PASS",
        evidence_integrity=verify_checkpoint_evidence_integrity(proof),
    )


def _record_from_verified_checkpoint(
    current: TrustedAnchorRecord,
    proof: TrustCheckpointProof,
) -> TrustedAnchorRecord:
    # predecessor_main/tree honestly record the OLD (stale) trusted anchor,
    # while merge_parent_1/2 remain the ACTUAL git parents of target_main
    # observed on live topology -- never forged to make the record LOOK
    # like an ordinary single-hop advancement. A reader of this record can
    # always tell a checkpoint recovery apart from ordinary advancement by
    # advancement_reason, and by predecessor_main != merge_parent_1.
    unsigned = TrustedAnchorRecord(
        repository_identity=proof.repository_identity,
        trusted_main=proof.target_main,
        trusted_tree=proof.target_tree,
        predecessor_main=current.trusted_main,
        predecessor_tree=current.trusted_tree,
        advancement_reason=AdvancementReason.VERIFIED_OWNER_AUTHORIZED_CHECKPOINT,
        source_package=proof.source_package,
        source_directive=proof.source_directive,
        source_pr=proof.source_pr,
        merge_commit=proof.target_main,
        merge_parent_1=proof.target_merge_parent_1,
        merge_parent_2=proof.target_merge_parent_2,
        merge_tree=proof.target_tree,
        certified_head=proof.certified_candidate_head,
        certified_tree=proof.certified_candidate_tree,
        certification_status="CERTIFIED",
        independent_verification_status="PASS",
        post_merge_seal="PASS",
        post_merge_ci="PASS",
        evidence_reference=proof.evidence_reference,
        evidence_digest=proof.evidence_digest,
        sequence=current.sequence + 1,
        record_digest=PLACEHOLDER_DIGEST,
    )
    return seal_anchor(unsigned)


def _checkpoint_already_used(store: Path) -> bool:
    """True if a checkpoint recovery has EVER been applied through this
    store -- current record OR anywhere in its retained history.

    Checkpoint recovery is a ONE-TIME capability per store (IV finding,
    PR #664: without this, nothing stopped an operator from repeating
    checkpoint recovery for every subsequent merge -- including ordinary
    ones -- permanently bypassing ``advance_trusted_anchor()`` instead of
    using it as intended, defeating the whole "recovery, not a routine
    path" premise).

    ``compare_and_advance()`` never REWRITES an existing history entry,
    but a second, independent IV round found that reading only "what is
    already there" is not itself durable: nothing stops a history entry
    from being *deleted* out from under this check (a bare filesystem
    delete of exactly one file, no digest/schema knowledge required) --
    silently resetting the gate while leaving every other file
    self-consistent. Closed by requiring the retained history to be
    GAP-FREE, not merely internally consistent: every sequence number
    from 1 up to (current record's own ``sequence`` minus 1) must exist
    and be readable, not just whichever files happen to still be
    present. A missing sequence number -- whether it once held a
    checkpoint record or not -- is treated exactly like a corrupt one:
    fails closed (denies) rather than silently proceeding as if that
    slice of history had simply never happened.

    Residual, DISCLOSED (not hidden) limitation, precisely stated (a
    fourth IV round found the earlier wording here understated it): the
    expected sequence range this scans is bounded by ``current.
    sequence`` as SELF-REPORTED by ``current.json`` -- ``_load_store_
    current()`` verifies that record's internal self-consistency
    (``record_digest`` matches its own payload), never anything
    independent of it. A single edit to ``current.json`` alone (set
    ``sequence`` back down, recompute the now-self-consistent
    ``record_digest`` the same way ``seal_anchor()`` does, leave
    ``history/`` completely untouched) shrinks the range this function
    checks and bypasses this gate -- no history tampering, no "fresh
    store from scratch" fabrication required, just one file. This is
    NOT a new privilege escalation: it requires exactly the same
    filesystem-write access to the store this module's design already
    treats as out of scope everywhere (``record_digest`` is a bare
    self-consistency hash, never a cryptographic signature) -- but it is
    real, and cheaper than forging a whole store, so state it precisely
    rather than only gesturing at the broader, harder case.
    """
    root = _require_store_root(store)
    current_path = _store_path(root, CURRENT_RECORD_NAME)
    if not current_path.is_file():
        return False
    try:
        current = _load_store_current(root)
    except TrustError:
        return True
    if current.advancement_reason == AdvancementReason.VERIFIED_OWNER_AUTHORIZED_CHECKPOINT:
        return True
    checkpoint_reason = AdvancementReason.VERIFIED_OWNER_AUTHORIZED_CHECKPOINT.value
    for sequence in range(1, current.sequence):
        entry = _store_path(root, f"{HISTORY_DIR_NAME}/{sequence:08d}.json")
        if not entry.is_file():
            return True
        try:
            payload = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return True
        if not isinstance(payload, dict):
            return True
        if payload.get("advancement_reason") == checkpoint_reason:
            return True
    return False


def advance_via_checkpoint_recovery(
    current: TrustedAnchorRecord,
    proof: TrustCheckpointProof,
    topology: GitTopology,
    *,
    store: Path,
    expected_repository_identity: str | None = None,
) -> TrustedAnchorRecord:
    """OBSERVE -> VERIFY -> REOBSERVE -> COMPARE -> ATOMIC_ADVANCE, the
    stale-runtime-anchor checkpoint-recovery variant.

    Distinct from ``advance_trusted_anchor()``: that function requires
    ``proof.merge_parent_1 == current.trusted_main`` (ordinary single-hop
    advancement). This function instead requires ``current.trusted_main``
    be reachable by walking ONLY first-parent edges from
    ``proof.target_main`` (see ``_walk_first_parent_chain``), with an
    exact hop-count and chain-digest match against what the proof claims
    -- auditable, non-fabricated lineage, without pretending every
    intervening merge was individually certified. Never invoked from
    governor observation alone; always requires this explicit,
    separately-authored ``TrustCheckpointProof``. ALWAYS persists to an
    explicit runtime ``store`` -- never silently mutates shipped package
    data; callers must ``initialize_store()`` first if no runtime store
    exists yet (this function does not create one implicitly).
    """
    if expected_repository_identity is not None:
        if not repository_identities_match(
            proof.repository_identity, expected_repository_identity
        ):
            raise TrustError("proof repository identity mismatch", code="REPO_IDENTITY_MISMATCH")
        if not repository_identities_match(
            current.repository_identity, expected_repository_identity
        ):
            raise TrustError(
                "current repository identity mismatch", code="REPO_IDENTITY_MISMATCH"
            )
    if not repository_identities_match(
        proof.repository_identity, current.repository_identity
    ):
        raise TrustError(
            "cross-repository anchor reuse is forbidden",
            code="REPO_IDENTITY_MISMATCH",
        )
    if proof.expected_previous_main != current.trusted_main:
        raise TrustError("stale or concurrent predecessor", code="PREDECESSOR_MISMATCH")
    if _checkpoint_already_used(store):
        raise TrustError(
            "a checkpoint recovery has already been applied through this "
            "trust store -- checkpoint recovery is a one-time capability; "
            "use ordinary single-hop advance_trusted_anchor() for all "
            "further advancement",
            code="CHECKPOINT_ALREADY_USED",
        )

    observed_main, observed_tree = topology.observe_main()
    require_full_pin(observed_main, "observed_main")
    require_full_pin(observed_tree, "observed_tree")
    if not topology.commit_exists(proof.target_main):
        raise TrustError(
            "proof references a nonexistent checkpoint target commit",
            code="GIT_OBJECT_MISSING",
        )
    if not topology.commit_exists(proof.certified_candidate_head):
        raise TrustError(
            "proof references a nonexistent certified candidate head",
            code="GIT_OBJECT_MISSING",
        )

    checks = evaluate_checkpoint_recovery(
        current,
        proof,
        topology,
        observed_main=observed_main,
        observed_tree=observed_tree,
    )
    if not checks.all_required:
        raise TrustError("verified checkpoint proof is incomplete", code="CHECKPOINT_DENIED")

    re_main, re_tree = topology.observe_main()
    if (re_main, re_tree) != (observed_main, observed_tree):
        raise TrustError(
            "live state changed during verification",
            code="TARGET_MOVED_DURING_VERIFICATION",
        )
    if re_main != proof.target_main or re_tree != proof.target_tree:
        raise TrustError(
            "re-observed main does not match authorized checkpoint target",
            code="CHECKPOINT_DENIED",
        )

    new_record = _record_from_verified_checkpoint(current, proof)
    if new_record.predecessor_main != current.trusted_main:
        raise TrustError("history monotonicity violated", code="PREDECESSOR_MISMATCH")
    return compare_and_advance(store, current, new_record)


@dataclass(frozen=True)
class CatchupChecks:
    """Checklist for a ``TrustCatchupProof``. All fields must be True before
    a bounded catch-up may proceed."""

    owner_authorization_proven: bool
    catchup_reason_valid: bool
    repository_identity_match: bool
    expected_previous_match: bool
    target_matches_observed: bool
    hop_chain_contiguous: bool
    hop_chain_matches_first_parent_walk: bool
    each_hop_authorization_valid: bool
    each_hop_status_pass: bool
    each_hop_evidence_integrity: bool
    evidence_integrity: bool

    @property
    def all_required(self) -> bool:
        return all(
            (
                self.owner_authorization_proven,
                self.catchup_reason_valid,
                self.repository_identity_match,
                self.expected_previous_match,
                self.target_matches_observed,
                self.hop_chain_contiguous,
                self.hop_chain_matches_first_parent_walk,
                self.each_hop_authorization_valid,
                self.each_hop_status_pass,
                self.each_hop_evidence_integrity,
                self.evidence_integrity,
            )
        )


def _catchup_hop_binding(hop: CatchupHopProof) -> dict[str, object]:
    """The exact structure a hop's ``evidence_digest`` must hash over --
    same evidence-target-binding rationale as ``_checkpoint_evidence_
    binding()`` (PR #664 IV finding): every security-relevant field of THIS
    hop, so a legitimately-authorized hop's evidence can never be lifted
    onto a different hop. Includes ``source_package``/``source_directive``/
    ``evidence_reference`` (reviewer finding, PR #666: without these, a
    hop's provenance -- WHICH work item/directive/evidence document
    authorized it -- could be substituted post hoc without invalidating
    this digest, even though the topology/authorization fields stayed
    intact; `_record_from_verified_catchup()` persists these straight into
    the sealed `TrustedAnchorRecord`, so they must be exactly as tamper-
    evident as everything else)."""
    return {
        "evidence_payload": hop.evidence_payload,
        "merge_commit": hop.merge_commit,
        "merge_tree": hop.merge_tree,
        "merge_parent_1": hop.merge_parent_1,
        "merge_parent_2": hop.merge_parent_2,
        "certified_candidate_head": hop.certified_candidate_head,
        "certified_candidate_tree": hop.certified_candidate_tree,
        "source_pr": hop.source_pr,
        "source_package": hop.source_package,
        "source_directive": hop.source_directive,
        "evidence_reference": hop.evidence_reference,
        "authorization_basis": hop.authorization_basis,
        "independent_verification": hop.independent_verification,
        "post_merge_ci": hop.post_merge_ci,
        "post_merge_seal": hop.post_merge_seal,
    }


def verify_catchup_hop_evidence_integrity(hop: CatchupHopProof) -> bool:
    return hash_payload(_catchup_hop_binding(hop)) == hop.evidence_digest


def _catchup_evidence_binding(proof: TrustCatchupProof) -> dict[str, object]:
    """The exact structure the overall ``evidence_digest`` must hash over.
    Includes every hop's own digest (not the hops' full content, which is
    already independently bound by ``_catchup_hop_binding``) so the whole
    chain -- not just its endpoints -- is what the overall digest commits
    to; substituting a different hop, or a different ORDER of hops, changes
    this binding even if every individual hop's own digest still verifies.
    Includes ``source_package``/``source_directive``/``source_pr``/
    ``evidence_reference`` for the same provenance-binding reason as
    ``_catchup_hop_binding`` (reviewer finding, PR #666) -- these are
    persisted verbatim into the sealed ``TrustedAnchorRecord`` by
    ``_record_from_verified_catchup()``, so a swap of "which PR/directive
    authorized this" must invalidate the digest exactly like a swap of the
    topology fields already does.
    """
    return {
        "evidence_payload": proof.evidence_payload,
        "repository_identity": proof.repository_identity,
        "owner_authorization": proof.owner_authorization,
        "catchup_reason": proof.catchup_reason,
        "expected_previous_main": proof.expected_previous_main,
        "expected_previous_tree": proof.expected_previous_tree,
        "target_main": proof.target_main,
        "target_tree": proof.target_tree,
        "hop_count": proof.hop_count,
        "first_parent_chain_digest": proof.first_parent_chain_digest,
        "hop_evidence_digests": [hop.evidence_digest for hop in proof.hops],
        "source_package": proof.source_package,
        "source_directive": proof.source_directive,
        "source_pr": proof.source_pr,
        "evidence_reference": proof.evidence_reference,
    }


def verify_catchup_evidence_integrity(proof: TrustCatchupProof) -> bool:
    return hash_payload(_catchup_evidence_binding(proof)) == proof.evidence_digest


def _evaluate_catchup_chain(
    topology: GitTopology,
    current: TrustedAnchorRecord,
    proof: TrustCatchupProof,
) -> tuple[bool, bool]:
    """Walk ``proof.hops`` in order, checking EACH hop against live git
    topology (never trusting the proof's own claims about itself), then
    cross-validate the reconstructed chain against an INDEPENDENT
    first-parent walk of the real topology (``_walk_first_parent_chain()``,
    the same function checkpoint recovery uses) so a hop list that doesn't
    correspond to genuine trunk history can never pass merely by being
    internally self-consistent.

    Returns ``(hops_contiguous_and_match_git, matches_independent_walk)``.
    """
    previous = current.trusted_main
    for hop in proof.hops:
        if hop.merge_parent_1 != previous:
            return False, False
        if not topology.commit_exists(hop.merge_commit):
            return False, False
        parents = topology.parents_of(hop.merge_commit)
        # Exactly 2, not >= 2 -- same octopus-merge rationale as checkpoint
        # recovery (PR #664 IV finding): a proof describing only the first
        # two of three-or-more real parents must never be accepted as if it
        # fully described the merge.
        parents_ok = (
            len(parents) == 2
            and parents[0] == hop.merge_parent_1
            and parents[1] == hop.merge_parent_2
        )
        if not parents_ok:
            return False, False
        if topology.tree_of(hop.merge_commit) != hop.merge_tree:
            return False, False
        # Redundant with CatchupHopProof's own `_candidate_is_second_parent`
        # model_validator -- checked again here anyway, same defense-in-
        # depth pattern `evaluate_advancement`/`evaluate_checkpoint_
        # recovery` already use for the identical relationship. A schema
        # validator alone is not load-bearing against every construction
        # path (e.g. `model_copy(update=...)` does not re-run validators),
        # so the runtime check that actually decides whether to trust this
        # hop must never rely on it exclusively (reviewer finding, PR #666).
        if hop.certified_candidate_head != hop.merge_parent_2:
            return False, False
        if not topology.commit_exists(hop.certified_candidate_head):
            return False, False
        if topology.tree_of(hop.certified_candidate_head) != hop.certified_candidate_tree:
            return False, False
        previous = hop.merge_commit
    if previous != proof.target_main:
        return False, False
    walk = _walk_first_parent_chain(topology, proof.target_main, current.trusted_main)
    walk_matches = (
        walk is not None
        and walk[0] == proof.hop_count
        and walk[1] == proof.first_parent_chain_digest
    )
    return True, walk_matches


def evaluate_catchup_recovery(
    current: TrustedAnchorRecord,
    proof: TrustCatchupProof,
    topology: GitTopology,
    *,
    observed_main: str,
    observed_tree: str,
) -> CatchupChecks:
    """Evaluate a ``TrustCatchupProof`` against live git topology and each
    hop's own evidence. Does not invent owner authorization, does not
    accept ANY-path ancestry, and does not mutate state."""
    hop_chain_contiguous, hop_chain_matches_walk = _evaluate_catchup_chain(topology, current, proof)
    each_hop_status_pass = all(
        hop.independent_verification == "PASS"
        and hop.post_merge_ci == "PASS"
        and hop.post_merge_seal == "PASS"
        for hop in proof.hops
    )
    each_hop_evidence_integrity = all(
        verify_catchup_hop_evidence_integrity(hop) for hop in proof.hops
    )
    # authorization_basis is already schema-constrained to the two allowed
    # literals; re-affirmed explicitly here (rather than assumed from
    # schema validation alone) so this checklist stays a complete, self-
    # contained audit trail of what was actually checked.
    each_hop_authorization_valid = all(
        hop.authorization_basis in ("OWNER_AUTHORIZED_AT_MERGE", "OWNER_RATIFIED_EXISTING_MERGE")
        for hop in proof.hops
    )
    return CatchupChecks(
        owner_authorization_proven=proof.owner_authorization == "OWNER_AUTHORIZED",
        catchup_reason_valid=proof.catchup_reason == "BOUNDED_VERIFIED_TRUST_CATCHUP",
        repository_identity_match=proof.repository_identity == current.repository_identity,
        expected_previous_match=(
            proof.expected_previous_main == current.trusted_main
            and proof.expected_previous_tree == current.trusted_tree
        ),
        target_matches_observed=(
            observed_main == proof.target_main and observed_tree == proof.target_tree
        ),
        hop_chain_contiguous=hop_chain_contiguous,
        hop_chain_matches_first_parent_walk=hop_chain_matches_walk,
        each_hop_authorization_valid=each_hop_authorization_valid,
        each_hop_status_pass=each_hop_status_pass,
        each_hop_evidence_integrity=each_hop_evidence_integrity,
        evidence_integrity=verify_catchup_evidence_integrity(proof),
    )


def _record_from_verified_catchup(
    current: TrustedAnchorRecord,
    proof: TrustCatchupProof,
) -> TrustedAnchorRecord:
    # predecessor_main/tree honestly record the anchor BEFORE this bounded
    # catch-up, while merge_parent_1/2 are the ACTUAL git parents of the
    # final hop's merge commit (== target_main) observed on live topology --
    # never forged to look like ordinary single-hop advancement. A reader
    # can always tell a catch-up apart from ordinary advancement or
    # checkpoint recovery by advancement_reason.
    last_hop = proof.hops[-1]
    unsigned = TrustedAnchorRecord(
        repository_identity=proof.repository_identity,
        trusted_main=proof.target_main,
        trusted_tree=proof.target_tree,
        predecessor_main=current.trusted_main,
        predecessor_tree=current.trusted_tree,
        advancement_reason=AdvancementReason.VERIFIED_OWNER_AUTHORIZED_CATCHUP,
        source_package=proof.source_package,
        source_directive=proof.source_directive,
        source_pr=proof.source_pr,
        merge_commit=proof.target_main,
        merge_parent_1=last_hop.merge_parent_1,
        merge_parent_2=last_hop.merge_parent_2,
        merge_tree=proof.target_tree,
        certified_head=last_hop.certified_candidate_head,
        certified_tree=last_hop.certified_candidate_tree,
        certification_status="CERTIFIED",
        independent_verification_status="PASS",
        post_merge_seal="PASS",
        post_merge_ci="PASS",
        evidence_reference=proof.evidence_reference,
        evidence_digest=proof.evidence_digest,
        sequence=current.sequence + 1,
        record_digest=PLACEHOLDER_DIGEST,
    )
    return seal_anchor(unsigned)


def advance_via_bounded_catchup(
    current: TrustedAnchorRecord,
    proof: TrustCatchupProof,
    topology: GitTopology,
    *,
    store: Path,
    expected_repository_identity: str | None = None,
) -> TrustedAnchorRecord:
    """OBSERVE -> VERIFY -> REOBSERVE -> COMPARE -> ATOMIC_ADVANCE, the
    bounded per-hop-evidenced catch-up variant.

    Distinct from BOTH ``advance_trusted_anchor()`` (exactly one ordinary
    hop) and ``advance_via_checkpoint_recovery()`` (a ONE-TIME, unbounded-
    span recertification that does not individually evidence intervening
    merges). This function requires a short, bounded chain of individually-
    evidenced ``CatchupHopProof`` entries whose reconstructed first-parent
    chain independently matches live topology (see
    ``_evaluate_catchup_chain()``). Deliberately NOT gated by
    ``_checkpoint_already_used()`` -- that gate protects checkpoint
    recovery's one-time property exclusively, and this function neither
    reads nor writes it, so using bounded catch-up any number of times
    across a store's lifetime can never reset or bypass checkpoint
    recovery's own one-time enforcement (owner directive D-ATLAS-BOUNDED-
    TRUST-CATCHUP-RECOVERY §1/§9/§14/§15 -- see the regression tests
    proving this alongside the catch-up test matrix). ALWAYS persists to an
    explicit runtime ``store`` -- never silently mutates shipped package
    data.
    """
    if expected_repository_identity is not None:
        if not repository_identities_match(
            proof.repository_identity, expected_repository_identity
        ):
            raise TrustError("proof repository identity mismatch", code="REPO_IDENTITY_MISMATCH")
        if not repository_identities_match(
            current.repository_identity, expected_repository_identity
        ):
            raise TrustError(
                "current repository identity mismatch", code="REPO_IDENTITY_MISMATCH"
            )
    if not repository_identities_match(
        proof.repository_identity, current.repository_identity
    ):
        raise TrustError(
            "cross-repository anchor reuse is forbidden",
            code="REPO_IDENTITY_MISMATCH",
        )
    if proof.expected_previous_main != current.trusted_main:
        raise TrustError("stale or concurrent predecessor", code="PREDECESSOR_MISMATCH")

    observed_main, observed_tree = topology.observe_main()
    require_full_pin(observed_main, "observed_main")
    require_full_pin(observed_tree, "observed_tree")
    if not topology.commit_exists(proof.target_main):
        raise TrustError(
            "proof references a nonexistent catch-up target commit",
            code="GIT_OBJECT_MISSING",
        )
    for hop in proof.hops:
        if not topology.commit_exists(hop.merge_commit):
            raise TrustError(
                "proof references a nonexistent hop merge commit",
                code="GIT_OBJECT_MISSING",
            )
        if not topology.commit_exists(hop.certified_candidate_head):
            raise TrustError(
                "proof references a nonexistent hop certified candidate head",
                code="GIT_OBJECT_MISSING",
            )

    checks = evaluate_catchup_recovery(
        current,
        proof,
        topology,
        observed_main=observed_main,
        observed_tree=observed_tree,
    )
    if not checks.all_required:
        raise TrustError("verified catch-up proof is incomplete", code="CATCHUP_DENIED")

    re_main, re_tree = topology.observe_main()
    if (re_main, re_tree) != (observed_main, observed_tree):
        raise TrustError(
            "live state changed during verification",
            code="TARGET_MOVED_DURING_VERIFICATION",
        )
    if re_main != proof.target_main or re_tree != proof.target_tree:
        raise TrustError(
            "re-observed main does not match authorized catch-up target",
            code="CATCHUP_DENIED",
        )

    new_record = _record_from_verified_catchup(current, proof)
    if new_record.predecessor_main != current.trusted_main:
        raise TrustError("history monotonicity violated", code="PREDECESSOR_MISMATCH")
    return compare_and_advance(store, current, new_record)


class MergeGuardError(TrustError):
    """Fail-closed: raised when an unrelated main integration is attempted
    while ``trusted_runtime_main != live main``. A distinct code from
    generic ``TrustError`` so callers can tell "trust is unverifiable"
    apart from "trust is verifiable but stale" -- the M2 trust/main
    interlock this session's real incidents (PR #653, PR #669) prove is
    required."""

    code = "TRUST_NOT_CURRENT_FOR_MERGE"


@dataclass(frozen=True)
class TrustRepairCarrier:
    """Explicit, narrow justification for the ONE documented exception to
    the merge/trust-sync interlock: a PR whose entire purpose is
    restoring trust synchronization itself (an ordinary-advancement
    repair, checkpoint, or bounded catch-up carrier) cannot itself wait
    for trust to already be current -- that would be circular.

    ``source_pr``/``reason`` are an AUDIT TRAIL, not a cryptographic
    authorization -- this class proves only that a caller took the
    deliberate extra step of constructing a real ``TrustRepairCarrier``
    instance (enforced by an exact ``type() is`` check in
    ``require_trust_current_for_merge()``, not merely a type hint a
    loosely-typed caller -- deserialized JSON, a CLI arg, an ``Any``-typed
    kwargs passthrough -- could satisfy with a plain dict or a bare
    ``True``; IV finding, PR #672) and gave it a non-empty reason string.
    It does not itself verify that the named PR is real, that it is
    genuinely a repair carrier, or that the reason is accurate -- callers
    remain responsible for supplying this only when actually true.

    Reviewer finding (Codex, PR #672): this is a deliberate design
    choice, not an oversight -- consistent with every other proof type
    in this file. ``AdvancementProof.owner_authorization``,
    ``TrustCheckpointProof.owner_authorization``, and
    ``TrustCatchupProof.owner_authorization`` are ALL bare self-asserted
    ``Literal["OWNER_AUTHORIZED"]`` claims with zero cryptographic
    verification -- this module's own header states
    ``GOVERNOR_CAN_INVENT_OWNER_AUTHORITY = NO``, meaning the CODE never
    invents authority, not that every self-asserted claim is
    independently re-verified by the code itself. The real verification
    that a given PR genuinely IS a trust-repair carrier happens the same
    way every other merge in this system is verified -- independent
    adversarial IV, hosted CI, and human review of that PR's actual
    diff -- BEFORE anyone constructs a ``TrustRepairCarrier`` for it, not
    inside this class. Unlike the other proof types, this one has no
    live-git-topology fact to bind to (this precondition check has no
    visibility into what commit is about to be merged), so there is no
    analogous cross-check available to add here without changing this
    function's contract to also accept and verify a candidate commit --
    a materially different, larger scope than the narrow precondition
    this class exists for today.

    Passing this to ``require_trust_current_for_merge()`` does not grant
    any trust-state mutation and does not widen who may call it or when
    -- it only lets that ONE precondition check pass for a caller that
    already independently satisfies every other merge gate."""

    source_pr: int
    reason: str

    def __post_init__(self) -> None:
        if self.source_pr < 1:
            raise TrustError(
                "trust repair carrier source_pr must be positive", code="INTERLOCK_MISUSE"
            )
        if not self.reason.strip():
            raise TrustError(
                "trust repair carrier reason must be non-empty", code="INTERLOCK_MISUSE"
            )


def require_trust_current_for_merge(
    *,
    store: Path,
    topology: GitTopology,
    expected_repository_identity: str | None = CANONICAL_REPOSITORY_IDENTITY,
    trust_repair_carrier: TrustRepairCarrier | None = None,
) -> TrustedAnchorRecord:
    """M2: fail-closed precondition for any main integration.

    ``trusted_runtime_main`` must equal live main (observed fresh here,
    never cached), or an explicit, narrow ``TrustRepairCarrier``
    justification must be supplied.

    ``expected_repository_identity`` defaults to ``CANONICAL_REPOSITORY_
    IDENTITY`` (reviewer finding, Copilot, PR #672: defaulting to
    ``None`` -- as every other ``store=``-taking function in this module
    does -- meant repository-identity mismatch silently went unchecked
    unless every caller remembered to pass it explicitly, contradicting
    the fail-closed behavior this function exists to provide; a caller
    genuinely working against a different repository's store may still
    pass ``None`` explicitly to opt out).

    Real incidents this formalizes (PR #653: a merge landed while the
    persisted trust anchor was already stale, with nothing machine-
    enforced to stop it or the next one from compounding the gap. PR
    #669: a genuine 3-way merge the ordinary advancement path of the time
    could not represent, leaving trust a further hop behind until
    repaired). This function does not, by itself, change WHO can call it
    or WHEN -- the governor already structurally forbids autonomous
    ``MERGED`` transitions regardless of any grant
    (``AutonomousGovernor.request_merge()``, owner gate A), and
    ``AutonomousLoop`` already fails closed on ``TARGET_MOVED`` for
    origination/lease/dispatch (``loop.py``). This exists so the owner-
    driven integration process itself has one real, reusable,
    adversarially-tested function to call before every merge, instead of
    an ad hoc manual check repeated by hand each time.

    Returns the loaded, verified anchor on success (current callers may
    want to log it) -- never mutates trust state either way.
    """
    anchor = load_runtime_anchor(
        store=store, expected_repository_identity=expected_repository_identity
    )
    observed_main, observed_tree = topology.observe_main()
    if not evaluate_target_moved(observed_main, observed_tree, anchor):
        return anchor
    if trust_repair_carrier is None:
        # Reviewer finding (Copilot, PR #672): this guard also fires on a
        # tree-only mismatch (same commit SHA claimed, different tree --
        # see test_target_tree_mismatch_alone_still_denied), so the
        # message includes both trusted/observed trees, not just the
        # main SHAs, to avoid misleading an operator into thinking only
        # HEAD moved.
        raise MergeGuardError(
            f"trust is not current for merge: trusted_main={anchor.trusted_main} "
            f"(tree {anchor.trusted_tree}), live_main={observed_main} "
            f"(tree {observed_tree}). No unrelated main integration may proceed "
            "until trust is synchronized (post-merge seal + trust advance/catch-up)."
        )
    # Runtime type check, not just the type hint (IV finding, PR #672):
    # mypy alone is not load-bearing here -- a carrier built from
    # loosely-typed input (deserialized JSON, a CLI arg, an `Any`-typed
    # kwargs passthrough) type-checks cleanly against `Any` and would
    # otherwise let a plain dict, a bare `True`, or any other truthy
    # value silently satisfy this "is not None" check, defeating the
    # entire interlock at exactly the moment it matters. `type(x) is not
    # TrustRepairCarrier` (exact type, not `isinstance`) also closes a
    # subclass that overrides `__post_init__` to skip the source_pr/
    # reason validation -- `TrustRepairCarrier` is frozen and not meant
    # to be subclassed for this purpose.
    if type(trust_repair_carrier) is not TrustRepairCarrier:
        raise TrustError(
            "trust_repair_carrier must be a genuine TrustRepairCarrier instance, "
            f"not {type(trust_repair_carrier).__name__}",
            code="INTERLOCK_MISUSE",
        )
    return anchor


def _inside(root: Path, target: Path) -> bool:
    try:
        target.relative_to(root)
    except ValueError:
        return False
    return True


def _require_store_root(store: Path) -> Path:
    resolved = store.resolve()
    if resolved.parent == resolved or resolved == Path.home().resolve():
        raise TrustError("trust store root is not a safe writable directory", code="PATH_UNSAFE")
    return resolved


def _store_path(store: Path, relative_name: str) -> Path:
    if ".." in Path(relative_name).parts or Path(relative_name).is_absolute():
        raise TrustError("trust store relative path is unsafe", code="PATH_UNSAFE")
    target = (store / relative_name).resolve()
    if not _inside(store, target):
        raise TrustError("trust store path escapes root", code="PATH_UNSAFE")
    return target


def _write_json_atomic(target: Path, payload: dict[str, object]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=True)
    tmp = target.with_name(f".{target.name}.tmp")
    tmp.write_text(encoded + "\n", encoding="utf-8")
    os.replace(tmp, target)


def initialize_store(store: Path, record: TrustedAnchorRecord) -> TrustedAnchorRecord:
    """Persist an explicit record. Does not read origin/main. No overwrite."""
    verify_anchor_integrity(record)
    root = _require_store_root(store)
    current_path = _store_path(root, CURRENT_RECORD_NAME)
    if current_path.exists():
        existing = _load_store_current(root)
        if existing.record_digest != record.record_digest:
            raise TrustError("existing trust record conflicts; refusing overwrite", code="BLOCKED")
        return existing
    _write_json_atomic(current_path, record.model_dump(mode="json"))
    return record


def _load_store_current(store: Path) -> TrustedAnchorRecord:
    root = _require_store_root(store)
    current_path = _store_path(root, CURRENT_RECORD_NAME)
    if not current_path.is_file():
        raise TrustError("trusted-anchor store record is missing", code="TRUST_UNVERIFIABLE")
    try:
        payload = json.loads(current_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TrustError(
            "trusted-anchor store record is unreadable",
            code="TRUST_UNVERIFIABLE",
        ) from exc
    if not isinstance(payload, dict):
        raise TrustError("trusted-anchor store record is schema-invalid", code="TRUST_UNVERIFIABLE")
    try:
        record = TrustedAnchorRecord.model_validate(payload)
    except Exception as exc:
        raise TrustError(
            "trusted-anchor store record is schema-invalid",
            code="TRUST_UNVERIFIABLE",
        ) from exc
    return verify_anchor_integrity(record)


def compare_and_advance(
    store: Path,
    expected_current: TrustedAnchorRecord,
    new_record: TrustedAnchorRecord,
) -> TrustedAnchorRecord:
    """Serialized compare-and-advance. Concurrent writers fail closed."""
    verify_anchor_integrity(expected_current)
    verify_anchor_integrity(new_record)
    if new_record.predecessor_main != expected_current.trusted_main:
        raise TrustError(
            "new predecessor_main must equal old trusted_main",
            code="PREDECESSOR_MISMATCH",
        )
    if new_record.sequence != expected_current.sequence + 1:
        raise TrustError("sequence must increase by one", code="DOWNGRADE_FORBIDDEN")
    if new_record.trusted_main == expected_current.predecessor_main:
        raise TrustError("arbitrary rollback is forbidden", code="ROLLBACK_FORBIDDEN")
    root = _require_store_root(store)
    lock_path = _store_path(root, LOCK_NAME)
    try:
        with ProjectIdentityLock(lock_path, wait_seconds=2.0, stale_seconds=30.0):
            current = _load_store_current(root)
            if current.record_digest != expected_current.record_digest:
                raise TrustError(
                    "concurrent or stale compare-and-advance",
                    code="ANCHOR_CAS_MISMATCH",
                )
            history_name = f"{HISTORY_DIR_NAME}/{current.sequence:08d}.json"
            history_path = _store_path(root, history_name)
            if history_path.exists():
                raise TrustError(
                    "refusing to rewrite anchor history",
                    code="TRUST_ANCHOR_HISTORY_REWRITTEN",
                )
            _write_json_atomic(history_path, current.model_dump(mode="json"))
            _write_json_atomic(
                _store_path(root, CURRENT_RECORD_NAME),
                new_record.model_dump(mode="json"),
            )
            return new_record
    except IdentityLockError as exc:
        raise TrustError(
            "anchor lock is held by another writer",
            code="CONCURRENT_ADVANCE",
        ) from exc


def observe_repository_identity(repo: Path, *, git_runner: GitRunner | None = None) -> str:
    runner = git_runner or LiveGitObserver(repo)
    return runner.repository_identity()


class GitRunner(Protocol):
    def repository_identity(self) -> str:
        """Normalized origin identity."""


class LiveGitObserver:
    """Fail-closed live git topology. Abbreviated SHAs are rejected."""

    def __init__(self, repo: Path, *, main_ref: str = "origin/main") -> None:
        # Branch names are never pins. origin/main is an observation ref only.
        if main_ref != "origin/main":
            raise TrustError(
                "main observation ref is not allowed",
                code="BRANCH_NAME_CONFUSION",
            )
        self._repo = repo.resolve()
        self._main_ref = main_ref
        if not (self._repo / ".git").exists():
            raise TrustError("root is not a git repository", code="GIT_UNOBSERVABLE")

    def _run(self, *args: str) -> str:
        result = subprocess.run(
            ["git", *args],
            cwd=self._repo,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise TrustError(f"git {' '.join(args)} failed", code="GIT_UNOBSERVABLE")
        return result.stdout.strip()

    def observe_main(self) -> tuple[str, str]:
        commit = require_full_pin(self._run("rev-parse", self._main_ref), "observed_main")
        tree = require_full_pin(
            self._run("rev-parse", f"{self._main_ref}^{{tree}}"),
            "observed_tree",
        )
        return commit, tree

    def commit_exists(self, sha: str) -> bool:
        if not _PIN_RE.fullmatch(sha):
            return False
        result = subprocess.run(
            ["git", "cat-file", "-t", sha],
            cwd=self._repo,
            check=False,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0 and result.stdout.strip() == "commit"

    def tree_of(self, sha: str) -> str:
        require_full_pin(sha, "tree_of")
        return require_full_pin(self._run("rev-parse", f"{sha}^{{tree}}"), "tree")

    def parents_of(self, sha: str) -> tuple[str, ...]:
        require_full_pin(sha, "parents_of")
        raw = self._run("rev-list", "--parents", "-n", "1", sha)
        parts = raw.split()
        parents = tuple(require_full_pin(item, "parent") for item in parts[1:])
        return parents

    def is_descendant(self, child: str, ancestor: str) -> bool:
        require_full_pin(child, "child")
        require_full_pin(ancestor, "ancestor")
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, child],
            cwd=self._repo,
            check=False,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0

    def repository_identity(self) -> str:
        return normalize_repository_identity(self._run("remote", "get-url", "origin"))


class FixtureGitObserver:
    """Disposable topology for tests. Not a live repository and not authority."""

    def __init__(
        self,
        *,
        observed_main: str,
        observed_tree: str,
        objects: dict[str, tuple[str, tuple[str, ...]]],
        identity: str = CANONICAL_REPOSITORY_IDENTITY,
        observe_sequence: tuple[tuple[str, str], ...] | None = None,
    ) -> None:
        self.observed_main = require_full_pin(observed_main, "observed_main")
        self.observed_tree = require_full_pin(observed_tree, "observed_tree")
        self._objects = objects
        self._identity = identity
        self._observe_sequence = list(observe_sequence or ())
        self._observe_calls = 0

    def observe_main(self) -> tuple[str, str]:
        if self._observe_sequence:
            index = min(self._observe_calls, len(self._observe_sequence) - 1)
            self._observe_calls += 1
            return self._observe_sequence[index]
        self._observe_calls += 1
        return self.observed_main, self.observed_tree

    def commit_exists(self, sha: str) -> bool:
        return _PIN_RE.fullmatch(sha) is not None and sha in self._objects

    def tree_of(self, sha: str) -> str:
        if sha not in self._objects:
            raise TrustError("fixture git object missing", code="GIT_OBJECT_MISSING")
        return self._objects[sha][0]

    def parents_of(self, sha: str) -> tuple[str, ...]:
        if sha not in self._objects:
            raise TrustError("fixture git object missing", code="GIT_OBJECT_MISSING")
        return self._objects[sha][1]

    def is_descendant(self, child: str, ancestor: str) -> bool:
        if child == ancestor:
            return True
        if child not in self._objects:
            return False
        for parent in self._objects[child][1]:
            if parent == ancestor or self.is_descendant(parent, ancestor):
                return True
        return False

    def repository_identity(self) -> str:
        return self._identity
