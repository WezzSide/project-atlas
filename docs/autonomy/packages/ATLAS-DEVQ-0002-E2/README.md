# ATLAS-DEVQ-0002-E2: prepared package (not dispatched)

**Label:** `E2_PACKAGE_PREPARED_AWAITING_VALID_EXECUTION_GRANT`

Preparation is not authorization. This branch is storage for exact, hashable evidence. **Do not merge it to `main` before E2 is dispatched**: merging would move `main` off the sealed base, which triggers the package's own abort condition "main is not at the sealed base revision before dispatch".

| Item | Value |
|---|---|
| Task / execution | `ATLAS-DEVQ-0002` / `ATLAS-DEVQ-0002-E2` |
| Attempt | implementation attempt **2/3**, `attempt_kind=implementation`, bounded repairs used 0. E1 was implementation-bearing attempt 1 (envelope failure, product verdict UNOBSERVABLE). After E2, **one** further implementation-bearing attempt remains under the ceiling of 3 |
| Sealed base | `64195b71b14cdb072948c08bddc440b5343d28ce` (TREE `de1ef3bc700e28096b091c3cbc7aae59c3d87c62`), canonical `main` when prepared |
| Builder | `dev_package.py` as merged on `main` (blob `c2ca7995898d558e8a3a60477bee266816a0dc64`, id `dev_package/1`) |
| `work_seal` | `ac1cc81f985614f84a742f8a7ace4bcbad855748656d61c03c779d1bc3febfff` |
| `workflow_inputs_sha256` | `c1c913330e0143ac98ded649fcb25192165397104cd77ed77965f8e09e058e39` |
| `package_sha256` | `b8035f9b0cb24e6ff9423b466e117d0dc8c8577eac6835147df5ceddb1bf62fe` (sha256 of `ATLAS-DEVQ-0002-E2.package.json`) |
| Supersedes | package `54a81ccb…276565` (v1, commit `99689171`; never dispatched). It was revised after the package review: GET-only cross-origin tests, portability, `from None` redaction, base-format URLError messages, turn economy |
| Grant required | `ONE_WORKFLOW_DISPATCH_GRANT` (not issued) |

## Dispatch notes

- **How to dispatch.** Use the package's own `workflow_inputs` for `atlas-agent-execute.yml` on `main`. Do not dispatch through `FabricAdapter`: it currently treats any `attempt > 1` as a repair, looks up a prior result branch, and would refuse.
- **Re-check the base first.** Before dispatch, confirm that `main` still equals the sealed base. If `main` has moved (for example after #1047 or #1046 merges), rebuild the package from the new `main`: the build is deterministic. Then repeat the package review. All identities above will change.
- **If the run is red on the turn envelope (#1043 salvage).** The candidate is preserved on `atlas/agent-<run>-<attempt>`, but the workflow stays red. `ingest_report` requires `workflow_conclusion == "success"`, so the candidate cannot be ingested automatically. Instead:
  - verify the exact saved candidate;
  - run exact-head CI and an independent review;
  - route adoption through the owner.

  Never fabricate a workflow success.
