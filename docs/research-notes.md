# Research notes, reminders and the research map

One command records one research fact. The program reads those facts, recognises a stuck state and asks the
agent one specific question. Answering takes a measurement. A map shows it all in one offline page.

Reminders are heuristics. The assurance is always `RECORDED_INPUT_NOT_SCIENTIFIC_VERIFICATION`: a closed
reminder is not evidence that anything is true. Reminder status is never stored; it is recomputed from notes.

## Commands

```powershell
python -B scripts/rds_cli.py --root <project> note goal "Recover the law" --metric psnr --scale 4k
python -B scripts/rds_cli.py --root <project> note expect "Should rise" --metric psnr --value 30 --tolerance 0.5
python -B scripts/rds_cli.py --root <project> note observe "Measured" --metric psnr --value 27 --scale 256
python -B scripts/rds_cli.py --root <project> note attempt "Gate v2" --route gate --outcome fail
python -B scripts/rds_cli.py --root <project> note claim "Attention causes it" --ref n3
python -B scripts/rds_cli.py --root <project> note constraint "Keep QKV" --source self
python -B scripts/rds_cli.py --root <project> nudge
python -B scripts/rds_cli.py --root <project> nudge answer <key> --ref n7
python -B scripts/rds_cli.py --root <project> nudge dismiss <key> --reason "Why it does not apply"
python -B scripts/rds_cli.py --root <project> map --output research-map.html
```

## Contract

`scripts/rds_notes.py` (L1). Notes are rows in the existing `events` table with `kind` `RESEARCH_NOTE`.
- Uses the root's existing ledger. `.rds/project.sqlite3` is created with events only when no ledger exists,
  and a later `project init` must still work. No new database, CAS record or lock. Writes use `BEGIN IMMEDIATE`.
- `TYPES = ('goal','expect','observe','attempt','claim','constraint','answer','dismiss','shown')`.
- `append(root, type, text='', **fields) -> dict` returns the note including `id`.
  Fields: `route, outcome, metric, value, tolerance, scale, ref, aggregate, endpoint, source, key, reason, auto`.
  `outcome` is `ok`, `fail` or `unknown`; `ref` is a list of note ids; `value` and `tolerance` are numbers.
  An unknown type or field, or text over 2000 characters, raises `ValueError`.
  `answer` requires `key` and `ref`, `dismiss` requires `key` and `reason`.
- `read(root, limit=None) -> list[dict]` in ledger order, each with `id` `n<row id>`. It returns `[]` when no ledger
  exists and never creates anything. Automatic hook delivery reads at most the latest 2000 notes.

`scripts/rds_nudge.py` (L3). Pure over notes, except `answer` and `dismiss`.
- `THRESHOLDS = {'repeat': 3, 'flat': 3, 'proxy': 2, 'endpoint': 3, 'reshow_after': 5}`, listed as defaults.
- `evaluate(notes) -> list[dict]`, each `{key, rule, question, anchors, status}`. Status is `OPEN`, `ANSWERED`
  or `DISMISSED`. `key` is stable: rule plus anchor note id. `question` quotes the anchor text and asks
  something answerable by a measurement.
- `status(notes) -> {'open', 'closed', 'unknown', 'assurance'}`. `unknown` lists expectations and flat-reading
  checks that could not run because a tolerance or value is missing. A missing tolerance never fires a rule.
- `next_for_host(notes) -> dict | None`: the highest-priority open reminder not shown within the last
  `reshow_after` non-`shown` notes. Rule order below is priority order.
- `answer(root, key, ref)` needs an open key and an `observe` note later than every anchor. Otherwise
  `ValueError`, nothing written. `dismiss(root, key, reason)` needs an open key and a reason.

Wording: a question names the evidence and asks for one specific thing. It never says the agent is wrong and
never asks it to reflect in general, because self-correction without an external signal often fails and
pushback invites capitulation. The delivered text ends by naming the two ways to close it.

Rules:

| Rule | Fires when | Asks |
|---|---|---|
| `expect_mismatch` | An `observe` with the same metric as an earlier `expect` differs by more than its tolerance | Which premise does this deviation shake? |
| `repeat_route` | At least `repeat` consecutive attempts on the same route fail; the key uses the first attempt in that failure run. Success or another route starts a new run | What have these attempts all kept fixed? |
| `unbound_claim` | A `claim` has no `ref` to an `observe` or `attempt` | Which measurement could refute it? |
| `proxy_metric` | A goal names a metric and the last `proxy` observes used a different one | Is this optimum still optimal on the goal metric? |
| `scale_gap` | A goal names a scale and an `observe` has a different scale | Does the ranking hold at the goal scale? |
| `flat_reading` | The last `flat` observes on one metric stay within the tolerance of their matching `expect`; overlapping flat windows share the first observation's key | What can this reading still distinguish? |
| `aggregate` | An `observe` has `aggregate` true | What did the aggregation cancel? |
| `endpoint_unchecked` | A goal exists, at least `endpoint` observes are recorded and none has `endpoint` true | What does the system finally output? |
| `unsourced_constraint` | A `constraint` has a `source` other than `user` or `doc` | Who required it, and what happens without it? |
| `missing_goal` | At least three attempts exist and no goal is recorded; lowest priority | What are the goal and expected result? |

`scripts/rds_conversation_hook.py`. Automatic delivery.
- Claude Code uses `PostToolUse`, `PostToolUseFailure` and `UserPromptSubmit`; Codex uses `PostToolUse` and `UserPromptSubmit`. OMP delivers on shell results and before agent start. The host receives additional context and a `shown` note records the key.
- Recognised experiment commands include script files, Python modules, pytest, uv/poetry run and npm/pnpm test. Automatic `attempt` notes store a route name and outcome, never arguments or command output.
- The hook never creates `.rds`, stays silent on errors, and uses a three-second timeout. Codex hooks require user trust in `/hooks`; real Codex delivery remains unverified.

`scripts/rds_research_map.py` (L4).
- `graph(notes, nudges) -> {'nodes', 'edges'}`. An edge is either `ref` (declared by a note) or `route`
  (consecutive attempts on one route). No other relation is invented.
- `render(notes, nudges) -> str`: one offline HTML page using the repo's bundled D3 asset, with no network
  request. Note text must be escaped so it cannot break out of the page.
