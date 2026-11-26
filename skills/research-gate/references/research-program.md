# Research program: one long question, many papers, many hands

A research program is a long scientific question pursued for years. It produces many papers, involves people and agents who come and go, and its north star may itself change. One file per program, `program.json` at the project root, records how the parts connect; [program.py](../scripts/program.py) checks the links with zero LLM tokens. Human-readable documents (GOAL, STATUS, ROADMAP, DECISIONS, cards, drafts) stay where they are and cite ids from this file.

## Four layers

| Layer | Unit | Who changes it | Rule that keeps it honest |
|---|---|---|---|
| North star | Versioned statement (`S@1`, `S@2`, …) with its conditions | Only the researcher, recorded in DECISIONS | An amendment adds a version; nothing is deleted |
| Proposition ladder | Weak propositions (W1–W7, lighthouse lines) | Main line, with the researcher's approval for new rungs | Each rung is a result on its own; `depends_on` stays acyclic; a supported rung needs a supported claim |
| Claims | One sentence + strength (observed, supported, proved) + evidence | Main line after the evidence exists | Supported needs evidence; proved needs a `.lean` proof; refuted claims stay with `refuted_by` |
| Papers | A slice of claims for one venue | Main line writes; the researcher decides scope | One primary owner per claim among active papers; submit only supported primary claims; never lean on a refuted claim; a merge carries every primary claim |

## Split until easy, then go back up

A rung that is too big is split into child propositions (`parent`), and each child is split again until it is **tractable**: one card, one proof, or one closed engineering task for an `engineering worker` settles it. Solved children are then composed back into their parent. A **route** is one way of splitting or attacking a node; when a split fails, open another route on the same node and keep the failed one as history.

`program.py review program.json --today <date> --md agenda.md` turns the tree into a work agenda, with no rereading of history:

| Agenda item | Meaning | Next action |
|---|---|---|
| `too_big` | Open, not split, not tractable | Split it: `route-new` plus child propositions |
| `ready_leaves` | Tractable and its prerequisites stand | Write the card or the GPT contract and run it |
| `ready_to_compose` | Every child of a live split is supported | Go back up: write the composition argument or experiment for the parent |
| `resplit` | A child of an active split was refuted | Retire that split, try another |
| `unattacked_rungs` | Open rung with no split and no live route | Decide whether to attack it now |
| `stalled`, `over_budget`, `past_deadline`, `retire_candidates` | Route health | Resume, stop or retire |
| `paper_candidates` | Supported claims no paper owns yet | Cut a paper or add them to one |
| `north_star_review_due`, `reanchor` | Time or evidence says look at the star again | Review with the researcher |

Every active route carries a falsifier and a GPU budget or deadline, so no line of attack stays open by default. New routes start as `proposed` and become `active` only after gate A.

## Focus: what is being pushed now

`program.json` may carry `focus` {`since`, `active` [node ids], `note`}. The board then puts only the focus nodes and their descendants on the to-do list, folds everything else into one "parked" line, and lists **what each active paper still needs** (primary claims not yet supported). Parked work keeps its records; nothing is deleted.

## Sharing progress through git

Collaborators and machines align through a small git repository that holds only knowledge worth exchanging, never raw runs:

```bash
python scripts/share.py export --program program.json --root . --to <shared-repo>   # regenerate the mirror
python scripts/share.py check <shared-repo>                                         # hashes, no strays
git -C <shared-repo> add -A && git -C <shared-repo> commit -m "sync <date>" && git -C <shared-repo> push
```

What goes in: `program.json`, `DECISIONS.md`, and the small text evidence (cards, result JSON, Lean proofs) behind supported or refuted claims. What stays out: checkpoints, PDFs, logs, anything binary or over the size cap, and evidence of claims that are not settled yet. Each export prepends a dated entry to `CHANGES.md` listing only what changed (new splits, status changes, north-star amendments), so the other side reads the delta in a minute. On the receiving side, `share.py check` confirms the mirror is intact before anyone relies on it.

## Room to change the north star

The ladder is what makes the north star replaceable. Every rung is a standalone result ("in a world with fixed rules, a weight-tied loop extrapolates 64× without tuning"), so it stays true when the north star moves. Only its edge to the north star needs review.

1. The researcher decides to amend, in words recorded in DECISIONS.
2. `program.py amend program.json --id S@3 --date … --statement … --quote "<原话>" --reason … --evidence <claim ids> --out program.json` adds the version and the amendment record. Statuses do not change.
3. `program.py check` now warns `re-anchor <id>` for every proposition, claim and active paper anchored to an older version. Work through the list: keep and re-anchor, restate, or retire. A merged or abandoned paper keeps its historical anchor.
4. Review the north star at milestones, not continuously: when a rung changes status, when a paper is submitted, or when a claim the star depends on is refuted. The question is whether the evidence still points the same way.

## Cutting papers from a long program

- **Publishable unit.** The smallest set of claims that settles one rung at one level of weakening, or carries the recipe to one new lighthouse line, with its evidence complete. A paper that changes no rung's status is salami.
- **Typical sequence.** A method paper (a rung in a controlled world), a theory paper (a conditional theorem such as W7), transfer papers (lighthouse lines), and a synthesis when several rungs stand. Papers may merge as the program grows; `merged_into` keeps the history, and the merged paper must carry every primary claim.
- **One owner per claim.** A claim is primary in one active paper and background elsewhere. This prevents double counting across a series and makes the contribution of each paper explicit.
- **Before submission.** Every primary claim is `supported`; paper-write's Tier 1 and Tier 2 gates then check the manuscript itself.

## Working with others

- **Results written back.** `program.py claim-propose program.json --id C-card043 --statement … --proposition W3 --evidence … --source card/043` adds a `status: proposed` claim for a person to confirm. If the named proposition is not in this program, it uses the focus only when exactly one node is active (and records `proposition_inferred_from: focus`); otherwise it writes nothing and says why. It refuses anything `program.py check` rejects, so every project gets the same rules.
- **Incoming contributions.** Collaborators add `contrib/<leaf>-<name>/CLAIM.json` in a pull request. `scripts/contrib_proposal.py --shared <repo> --program program.json --out PAGE.md --fetch` finds them in the working tree, branches and pull requests, checks each one, and writes a ready-to-paste claim with `status: proposed` and `source: contrib/<folder>`. It merges nothing. Once a claim with that `source` is in the project, the folder stops being listed.
- **Handing work over** uses [handoff and archive](handoff-archive.md): the plan names the claim ids it supports, so the recipient can tie the bundle back to the program.
- Authorship and contributor roles are not tracked here. `program.py` still accepts an optional `contributors` list if a scoped export for an outside collaborator is ever needed (`program.py export --for <id>`).

## Where the ids appear

| Place | What cites program ids |
|---|---|
| Cards | The rung (`W3`) a card tests and the claim ids it may produce |
| Experiment ledger rows | The claim ids they are evidence for |
| Paper drafts | Primary and background claim ids, in the draft's front matter |
| DECISIONS | North-star amendments and new rungs, with the researcher's words |

## Commands

```bash
python scripts/program.py check  program.json [--root <project>]   # errors and re-anchor warnings
python scripts/program.py report program.json                      # ladder and paper tables
python scripts/program.py export program.json --for <id> --out view.json
python scripts/program.py amend  program.json --id S@2 --date … --statement … --quote … --reason … --out program.json
```
