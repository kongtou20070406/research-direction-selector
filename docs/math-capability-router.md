# Bounded exact-math capability router

The theory-tools entrypoint can select a registered adapter from a structured mathematical request. It chooses by input kind, domain, requested claim, and local pinned-backend availability. It does not rank tools by speed. Performance is `NOT_BENCHMARKED`.

```powershell
python -B scripts/rds_theory_tools.py --math-capabilities
python -B scripts/rds_theory_tools.py --math-solve request.json
python -B scripts/rds_theory_tools.py --math-check request.json --certificate certificate.json
```

The command prints machine-readable JSON. `--math-solve` emits a certificate after the candidate backend returns an answer and the router's independent exact checker accepts it. `--math-check` replays that certificate using bounded standard-library rational arithmetic; replay does not call SymPy.

## Registered capabilities

The registry is intentionally finite and currently contains two SymPy 1.14.0 adapters. The SymPy dependency is already pinned by `requirements-formal.txt`.

| Capability | Request claim | Checked result | Scope limit |
| --- | --- | --- | --- |
| `sympy.qq.linear.unique.v1` | `A*x=b` has exactly one solution over `QQ` | Exact substitution plus full-column-rank elimination | 1–8 rows and columns, rows at least columns, exact rational inputs with at most 64 bits |
| `sympy.qq.univariate.distinct-real-roots.v1` | Complete set of distinct real roots of a polynomial over `QQ` | Pairwise-disjoint rational isolating intervals, checked with an independent Sturm count on the square-free polynomial | Degree at most 8, nonzero polynomial, exact rational coefficients with at most 64 bits; multiplicities are not reported |

Example requests:

```json
{"schema":"rds-math-request-v1","kind":"rational_linear_system","domain":"QQ","matrix":[[2,3],[4,-1],[6,2]],"rhs":[1,2,3],"claim":"unique_solution"}
```

```json
{"schema":"rds-math-request-v1","kind":"univariate_qq_polynomial_roots","domain":"QQ","coefficients_ascending":[-2,0,1],"claim":"complete_distinct_real_root_set","max_interval_width":"1/16"}
```

The linear request uses `schema: "rds-math-request-v1"`, `kind: "rational_linear_system"`, `domain: "QQ"`, a rectangular `matrix`, matching `rhs`, and `claim: "unique_solution"`. The polynomial request uses the same schema, `kind: "univariate_qq_polynomial_roots"`, `domain: "QQ"`, ascending `coefficients_ascending`, `claim: "complete_distinct_real_root_set"`, and a positive rational `max_interval_width` no greater than one. Rational values are JSON integers or strings such as `"-3/7"`; floats and expression strings are rejected.

Both routes cap requests at 64 KiB and expose deterministic structural work budgets. The polynomial budget includes degree cubed times an upper estimate of requested interval precision bits. These are conservative input bounds, not elapsed-time or memory guarantees.

## Status and proof meaning

- `PASS` means the returned certificate is bound to the exact request digest and independently checked within the capability's stated scope.
- `UNKNOWN` means no registered adapter supports the requested semantics, a backend is unavailable, or a supported finite bound is exceeded. The result carries an `rds-math-extension-task-v1` object with the typed gap and checker requirements.
- `INVALID_CERTIFICATE` means replay rejected malformed, incomplete, altered, or request-mismatched evidence.

This prototype does not issue `FAIL`, `UNSAT`, or a general no-solution claim. Inconsistent systems, underdetermined systems, parametric families, zero-polynomial root sets, unsupported domains, and unsupported problem kinds remain `UNKNOWN`. A checked rational witness would establish that a solution exists; it would not establish uniqueness or exhaustiveness. The registered linear route therefore checks full column rank in addition to substitution, while the root route checks both one root per reported interval and equality with the total Sturm count.

The polynomial capability returns distinct root locations, not multiplicity data. Repeated roots are represented once in the square-free root set. Exact rational endpoints are checked; no approximate decimal output is used as proof evidence.

## Extension tasks and tool policy

An unsupported request returns its original structured spec, a SHA-256 digest, the missing capability, proposed input/checker obligations, and possible tool candidates. Candidate tools are informational only. `python-flint` and `z3-solver` are not registered or installed by this router. No arbitrary plugin or generated adapter code runs, and capability discovery never installs dependencies. An adapter can enter the registry only with a reviewed typed contract, candidate generator, independent checker, focused regressions, and declared license/source.
