# Research loop integration acceptance (2026-11-13)

Prospective protocol, written before implementation and verification. This is
engineering acceptance, not an experiment measuring scientific benefit.

The ordinary Skill entry must restore one project, keep the goal and failed
route, describe a changed assumption/representation/decomposition, run an
existing checker, and return its effect on the next research action. It must
work without an external model or execution contract for local formal checks.
External experiments retain their existing project/structure/Compass admission.

Insertion points: `rds_notes.append/read` already share ProjectStore events;
`rds_cas.cas_json` preserves original bytes; `rds_verify.verify/checked_result`
and `rds_math_router.solve_and_check/check_certificate` already separate search
from replay. `rds_structure.feedback` owns empirical receipt rechecking.
`rds_cli.parser/main` and `SKILL.md` currently leave these paths disconnected.

Implementation scope: a composition facade and checker adapters, CLI wiring,
mapped tests, import classification, a runnable example, Skill and entry docs,
then release/version/package consistency. No verifier, budget or ledger schema
changes. Research annotations are existing RESEARCH_NOTE events with CAS-bound
payloads and note references; they are not another scheduler or truth store.

Acceptance and reverse checks:

- Same fixed-point task: iteration/contraction fails; a host-proposed algebraic
  representation checks the exact fixed point. Both routes and the failed
  original remain visible after process restart. The tool does not invent the
  new hypothesis or claim that a fixed point proves iterative convergence.
- Repeating a check reuses the recorded result; a status read replays only, never
  calls a generator or launches a job. Unsupported specs remain UNKNOWN.
- Alter original CAS bytes, substitute a certificate, cross-bind an obligation,
  or attach an observation claiming PASS: none may discharge an obligation.
- Parent/goal links must resolve in the same project and current goal scope.
  Child-directory entry discovers the canonical project instead of making a
  second ledger. Existing native objectives remain usable.
- Formal, exact-math and existing structure feedback adapters preserve their
  assurance and scope. Empirical observations remain INPUT_REPORTED; all
  scientific_support fields remain UNKNOWN.
- An isolated reviewer reproduces critical cases and recomputes CAS hashes using
  hashlib plus raw ledger/asset bytes, independent of the facade's helpers.
- Focused tests during implementation; full suite and package/import checks on
  the final release candidate. CI covers available OS/Python/native/Lean paths.

The release is authorized by the current user. Earlier integration-plan pauses
and publication restrictions are historical document instructions, superseded
by the user's current request. No efficacy claim or paid benchmark is required.
