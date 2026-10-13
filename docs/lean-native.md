# Native Lean library and statistical obligations

The optional `formal/` Lake project currently uses Lean **4.34.1** and mathlib **v4.34.1**.
RDS follows matching stable releases; `lean-toolchain` and `lake-manifest.json`
record the exact toolchain and dependency commits for each revision, rather than
imposing a permanent version ceiling. The [paired upgrade procedure](lean-toolchain-upgrades.md)
updates the runtime revision guard together with these files. Python-only installations still
run RDS. Runtime verification discovers already installed native binaries and
never installs a toolchain, resolves Lake dependencies, or downloads a cache.

For Agent-assisted provisioning, use the on-demand [Lean setup procedure](../references/skill-routes/lean-setup.md).
It reuses the project's pins and existing installation authority; it is guidance,
not a built-in installer or proof-acceptance bypass.

A deployment offering the complete native formal capability must provision this
matching Lean/mathlib pair and the built `Formal` library. A Python-only setup is
a reduced-capability installation. There is currently no `required-native`
installation mode or `doctor` command: acceptance must inspect
`assurance: LEAN_KERNEL_CHECKED`, independently replay the certificate, and retain
`application_status: UNKNOWN` when empirical premises are unresolved. Pin upgrades
require a fresh build and replay.

Select the checking method explicitly when it matters:

```text
python -B scripts/rds_cli.py formal verify --spec examples/formal/lean_obligation.json --tactics lean4 --no-cache
python -B scripts/rds_cli.py formal verify --spec examples/formal/lean_obligation.json --tactics rational --no-cache
```

`lean4` requires the native compiler and cannot return a Python fallback as PASS.
`rational` uses the exact Python checker for supported closed rational relations
without calling Lean; its assurance is `CERTIFICATE_CHECKED`. Default `rule`
selection can still use either available backend, and the result identifies the
actual backend and assurance. A generic successful exit is therefore not a native
environment check. Neither method establishes empirical applicability.

## Build and replay

Build the library explicitly, with the toolchain declared in `formal/lean-toolchain`:

```text
cd formal
lake update
lake exe cache get
lake build
```

Then run from the repository root:

```text
python -B scripts/rds_cli.py formal verify --spec examples/formal/statistical_hoeffding.json --output proof.json --no-cache
python -B scripts/rds_cli.py formal check --spec examples/formal/statistical_hoeffding.json --certificate proof.json
```

`RDS_LEAN_EXECUTABLE` can select an existing absolute native binary. Otherwise
discovery prefers the package's installed toolchain, then native binaries on
PATH or in installed elan toolchains; download shims are not run. Invalid explicit
configuration remains `UNKNOWN`. Without any native binary, supported closed
rational relations use an independently replayed exact Python certificate,
labelled `CERTIFICATE_CHECKED`, rather than `LEAN_KERNEL_CHECKED`. Unsupported
statistical checks remain `UNKNOWN`; SymPy or AST checks cannot certify a
probability theorem.

Each statistical kernel invocation has a 60-second deadline, a 4 GiB Lean
memory limit, one worker thread and a 64 KiB output limit. Resource exhaustion
returns `UNKNOWN`; it does not permit fallback or admission. Closed rational
checks retain their separate 3-second, 512 MiB limits.

The Python CI matrix needs no Lean installation. A separate native Lean job builds
the fixed package, audits axioms, and runs the native adapter tests.

## Theorems and assumptions

| Module | Formal mathematical claim | Required scope |
| --- | --- | --- |
| `Formal/Probability/Martingale.lean` | Ville's bound for the event that a nonnegative test supermartingale crosses a threshold | Adaptation, integrability, supermartingale inequalities, initial expectation bound, and positive significance level |
| `Formal/Probability/Concentration.lean` | Hoeffding tail bound for a finite independent bounded family | Probability measure, a.e. measurability and interval bounds, centering, nonnegative deviation |
| `Formal/Information/DPI.lean` | `I(X;Z) ≤ I(X;Y)` for a source and two composable Markov kernels | Joint law and marginal products, probability input measure, measurable Markov channels; infinite information values are permitted |
| `Formal/Gershgorin.lean` | Finite-matrix eigenvalue localization and a sufficient unit-disk bound | Declared matrix, normed coefficient field, exact row bound |
| `Formal/Contraction.lean` | Contraction of a residual update under a bound on its actual operator | Norm of `I+A` below one; a bound on `A` alone is insufficient |
| `Formal/ScaleInvariance.lean` | Linear homogeneity and invariance of normalized linear output under positive scaling | Bias-free real linear map; normalization at zero uses the defined zero value |

These are conditional theorems. Ville controls an error probability; it does not
give zero false positives. Independent evaluation samples can support a Hoeffding
protocol; successive training losses generally are not independent samples.
DPI does not rule out improvements in optimization, numerical behavior or task
performance after a transformation.

The JSON language initially selects only a registered theorem:

```json
{"schema":1,"kind":"statistical_obligation","theorem":"ville"}
```

The other names are `hoeffding` and `dpi`. Extra parameters, user Lean code,
tactics and self-asserted assumptions are rejected. This interface audits the
quantified conditional law, rather than calculating a confidence bound for a
CSV, proving independence of a sampling process, or checking a deployed model.
A native result reports `status: PASS`, `assurance: LEAN_KERNEL_CHECKED`,
`conditional_statement: true`, `application_status: UNKNOWN` and
`assumptions_required`. The application remains blocked until a supported,
separate checker closes its premises. A finite theorem module preserves this
application status; conjunction cannot remove unresolved assumptions.

Certificates bind the JSON declaration, generated source, native executable,
Lean version, package/toolchain/manifest, Formal source and compiled modules.
Checking reconstructs the template and invokes the native kernel again. The
accepted foundational axioms are `propext`, `Classical.choice` and `Quot.sound`;
`sorryAx`, user axioms, `native_decide` and extra diagnostic output do not pass
the audit. A hash records identity, not scientific interpretation.

## Frontier and candidate admission

Frontier inputs may attach at most 16 `formal_records`, each with `id`, a defined
`node`, `source`, `statement`, and optional framework `certificate` and
`available_on`. The existing as-of cutoff also applies to these records. Stored
certificates are replayed; a missing, mismatched, fallback or conditional-only
certificate leaves an explicit `FORMAL_OBLIGATION` gap.

Proposal review accepts `formal_obligation` and optional `formal_certificate`.
For a formal gap it must preserve the exact declared statement; unrelated true
theorems cannot close that gap. The `candidate_pool` contains only proposals whose
declared side condition has a native kernel proof and no unresolved application
status. `FAIL` and `UNKNOWN` block admission. Pool membership grants no execution
authorization and adopts no scientific relations. Graph similarity or supplied
`SUPPORTED` labels never create Lean dependency edges.

Plan admission, cached proof reuse and execution also require a resolved
application status. Cached declarative results are reconstructed from their
certificates, so deleting a stored application flag cannot bypass the check.
Unresolved statistical premises block reservation and execution before model
evaluation.

## Source scope and further work

The revision's [mathlib sub-Gaussian library](https://github.com/leanprover-community/mathlib4/blob/v4.34.1/Mathlib/Probability/Moments/SubGaussian.lean)
supplies Hoeffding's MGF and independent-sum results. Its
[KL data-processing library](https://github.com/leanprover-community/mathlib4/blob/v4.34.1/Mathlib/InformationTheory/KullbackLeibler/DataProcessing.lean)
supplies common-channel contraction; the RDS mutual-information theorem also
proves the required joint and product-law mapping identities. Ville's proof is
adapted from [formal-martingales, fixed revision](https://github.com/Robby955/formal-martingales/blob/1e49307ce983fe472b35400a79052bb607298123/FormalMartingales/Martingale/Ville.lean),
with its source attribution and license retained in the package.

[The Ramanujan Library](https://arxiv.org/abs/2412.12361) (ICLR 2025, arXiv v2,
2025-01-19) contributes numerical conjecture discovery, not proof certificates.
[TheoremGraph](https://arxiv.org/abs/2606.25363) (v1, 2026-06-24) distinguishes
formal dependencies from semantic retrieval; cross-language matching is not
kernel equivalence. [AlphaProof Nexus](https://arxiv.org/abs/2605.22763)
(v2, 2026-06-08) motivates proof search followed by verification and statement
preservation. Their reported results are not RDS benchmark results.

General McDiarmid, conformal coverage, PAC-Bayes and confidence-sequence adapters
are not registered by this patch. Adding them requires a fixed proof dependency
closure, native compilation, an explicit sampling protocol and a bound replay
interface; a numerical test or a quoted theorem name is insufficient.
