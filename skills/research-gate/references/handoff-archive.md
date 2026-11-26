# Handoff and archive

Use this when work changes hands (another agent, machine or collaborator), when a stage ends, and before a long pause. The design follows math-research-solve's planned export: declare the handoff in a plan, freeze the plan with byte hashes, pack exactly that plan, and let the recipient verify the bundle. [handoff.py](../scripts/handoff.py) does all four steps with zero LLM tokens, so a receiving agent reads one compact plan instead of re-deriving the project.

## Rules

1. **The plan is the index; the bytes are the evidence.** A summary, chat log or status note is never evidence. Every claim names the files that support it, and those files are in the plan with their hashes.
2. **Complete or partial, never implied.** A partial handoff states `partial_reason`. A complete handoff leaves no job running.
3. **Refuted claims stay.** Mark a claim `refuted`, keep its original evidence and name the files that refute it in `refuted_by`. Do not delete the record; the next reader needs to know the route was tried.
4. **Generated results are never edited.** Keep outputs byte for byte, including failed and contradictory ones. A correction is a new file that names the original. A successor plan (`previous_plan_sha256`) fails if a `result` file's bytes changed or a claim disappeared.
5. **Failed approaches carry a reason.** One sentence on why it failed, plus evidence when there is any.
6. **Running jobs carry a status file and a stop condition**, so the recipient can tell a live job from a dead one without guessing.
7. **Verification is a readback, not a replay.** `verify` proves the bundle holds the declared bytes. Rerunning the experiments is a separate, authorized step.
8. **Machine-checked bundle, human-first handoff.** Keep JSON, hashes, and logs for receiving agents; when handing off to the human researcher, open with 2–3 warm, plain partner sentences covering where things stand, what succeeded, and what needs a decision. Keep internal task IDs, raw CLI flags, exit codes, and log dumps out of the top screen, avoid bureaucratic bullet-dumps or lecturing tone, and offer at most 1–2 concrete recommendations.
   - *Bad*: `Task WF-2 COMPLETED; exit=0; sha=ac3ec89; --pytest-target passed 228; check logs.`
   - *Good*: `这两项已经做完并推上远端，测试全绿。接下来只剩一个小决定需要你拍板……`

## Steps

```bash
python scripts/handoff.py check  PLAN.json [--previous OLD.frozen.json]
python scripts/handoff.py freeze PLAN.json --out PLAN.frozen.json        # prints PLAN_SHA256
python scripts/handoff.py pack   PLAN.frozen.json --expect-plan <PLAN_SHA256> --out handoff_<date>.zip
python scripts/handoff.py verify handoff_<date>.zip                      # recipient side
```

`freeze` and `pack` are create-only. `pack` rechecks every hash, writes to a temporary file, reads the bundle back and only then publishes it.

## Plan fields

| Field | Content |
|---|---|
| `schema`, `project`, `purpose` | `handoff-plan/v1`; project name; `handoff` or `archive` |
| `scope`, `partial_reason` | `complete` or `partial` (with the reason) |
| `objective` | The claim or weak proposition the work serves |
| `files` | `{relative/path: {role}}`; roles: code, configuration, input, result, ledger, document, checkpoint, optional |
| `claims` | `id`, `statement`, `status` (proposed, executed, verified, refuted, needs_review), `evidence`, `refuted_by` |
| `failed_approaches` | `id`, `what`, `why`, `evidence` |
| `running_jobs` | `name`, `where`, `status_file`, `stop_condition` |
| `next_actions`, `contributors` | First unresolved step; which agent or person produced which record |
| `previous_plan_sha256` | Digest of the previous frozen plan, for an archive series |

An `executed` or `verified` claim needs at least one evidence file with role result, ledger, checkpoint, input or document.
