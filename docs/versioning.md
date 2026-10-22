# Versioning and releases

RDS uses **MAJOR.MINOR.PATCH**. The latest published stable release is **5.8.0**. The 5.x series was the original prototype line; the next generation opened at **6.0.0-rc.1** and this checkout identifies itself as **6.0.0** (publication pending; see the [6.0.0 notes](releases/6.0.0.md) and the earlier [preview notes](releases/6.0.0-rc.1-preview.md)). `6.0.0-rc.1` state remains readable. See [Era transition](#era-transition) for why the major line moved. From 5.7.0 onward, releases are stable by default; use a release candidate only when a trial or staged acceptance is explicitly requested.

The public contract includes documented CLI options, JSON schemas for state/receipts/certificates, and supported ledger reading and execution behavior. Internal helper changes alone do not define a breaking release. The numbering follows [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).

## Choose the next version

Develop on `main`; do not change the release version for every PR. At release time, inspect all changes since the previous release and choose the highest applicable level:

| Level | Trigger | Example after 5.8.0 |
| --- | --- | --- |
| MAJOR | Incompatible public CLI, schema or ledger behavior. Provide a migration path and document affected readers/callers. | 6.0.0 |
| MINOR | Compatible new features or components, or deprecation of a public interface. | 6.1.0 |
| PATCH | Compatible bug fixes, performance improvements or dependency repairs. | 5.8.1 |

Routine documentation edits do not require a release each time. If a documentation-only release is explicitly requested, use PATCH. A dependency update that actually breaks a public contract follows MAJOR rather than PATCH.

Incrementing MINOR resets PATCH to zero; incrementing MAJOR resets both MINOR and PATCH. A release containing several kinds of changes takes the highest level.

For an explicitly requested trial, choose the intended final version first, then use `X.Y.Z-rc.1`, `X.Y.Z-rc.2`, and so on. The final release uses `X.Y.Z`. Never overwrite or move an existing tag or replace its published artifacts; corrections require a new version. A compatible feature increment is a MINOR release, not an automatic continuation of an older RC series.

## Release requirements

Before publishing, complete these checks for the exact release revision:

1. Record the exact commit SHA on `main`. The tag, source package and release notes must identify that revision.
2. Align CLI `VERSION`, Skill `metadata.version` and the tag to the same numerical release. Tags have a `v` prefix; CLI `VERSION` does not. For this release: CLI `6.0.0`, Skill `v6.0.0`, tag `v6.0.0`.
3. Preserve read compatibility with old ledgers for compatible releases. The reference-state reader explicitly supports `5.1.0`, `5.2.0`, `5.3.0`, `5.4.0`, `5.5.0-rc.1`, `5.5.0-rc.2`, `5.6.0-rc.1`, `5.6.0-rc.2`, `5.7.0`, `5.8.0`, `5.9.0-rc.1`, `5.9.0-rc.2`, `6.0.0-rc.1` and the current CLI version. Read-only status preserves the ledger's stored version and bytes. Unlisted versions remain rejected; readable old state still requires intact contract bindings and does not bypass engine checks for new execution. For a breaking release, provide and verify the migration path, describe the supported old formats, and retain recovery evidence.
4. Run verification appropriate to the accumulated changes. Retain local results and CI evidence for the release revision; distinguish earlier-revision evidence, actual passes, skips and unavailable coverage. Prepare release notes as described in step 5.
5. Write release notes describing delivered behavior, compatibility, validation and material limits. Engineering acceptance does not require demonstrated scientific gain. Unmeasured benchmarks, unresolved application premises and research hypotheses remain unknown or untested.

Skill `metadata.engine` is a descriptive value, **`rds-cli-vMAJOR.MINOR`**; for this preview it is `rds-cli-v6.0`. The actual `engine_id` binds source and environment hashes. A changed binding may require a new contract under the existing execution guard. That safety check alone is not a breaking version change: classify the release by changes to the public contract, not by every new source hash.

## Era transition

The 5.x series was the original prototype line. Version **6.0.0** opens a new
generation: the pipeline refactor changes public module boundaries, extracted
presentation assets and the workspace-to-ledger binding, which
[Choose the next version](#choose-the-next-version) classifies as a MAJOR change.
This is a deliberate, dated generation switch, not a correction of an earlier
number.

- The published tags `v5.8.0` and `v5.9.0-rc.1` keep their existing identity; no
  tag is moved, renamed or re-published.
- The working label `5.9.0-rc.2` was never tagged or published. It is superseded
  by `6.0.0-rc.1` and remains readable as a stored ledger version (see the
  readable-version list above).
- Mapping: the 5.x prototype line maps to the 6.0.0 generation as a whole, not
  version-by-version. There is no `5.Y.Z -> 6.0.Y` correspondence.

Changing the CLI `VERSION` also changes the source hash bound into `engine_id`,
so an otherwise unchanged project contract from an earlier checkout requires the
existing new-contract path before further execution. That is the normal
engine-binding guard, not a ledger read failure.
