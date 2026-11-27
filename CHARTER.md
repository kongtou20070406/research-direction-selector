# RDS v7 Design & Research Charter

This charter captures the design philosophy behind **Research Direction Selector (RDS)** and the complete CLI reference for maintainers and CI.

---

## 1. Workflow Design Charter (One-Person Fleet)

1. **One-Person Research Fleet:** One researcher directs a Lead model (planning, synthesis, prose, visual design, `reference/`, per-device operator optimization, and the `eval/` entry points plus hands-on probes of raw failure samples), parallel engineering workers (closed pipeline/baseline/plumbing tasks, taken only after evaluation semantics and test SHAs are locked), parallel independent reviewers (closed proofs and counterexamples at any time, literature, operator fault-finding, and open-context full-repo red-team passes at project start and before numbers enter a main table or freeze—decoupled from routine card closure), and deterministic 0-token verification scripts. For key positioning decisions and major-draft consultations, the Lead may bring in a cross-family model (e.g., Claude Opus) through `/swarm`.
2. **Zero-Command Conversation:** The researcher focuses on four irreplaceable tasks: setting the north star, contributing scientific intuition, owning final aesthetic taste, and making key tradeoffs. Scripts, flags, and verification details stay behind the scenes.
3. **Explore Freely, Verify Strictly:** Exploration and bug fixes run without cards or gates. Only numbers that will be cited in the ledger or a paper need a falsifiable hypothesis, baseline, and kill threshold sealed before launch; during drafting, track pending numbers with `% gate-sorry` / `TBD` and clear them at `--no-sorry` freeze. Accept deliverables only via real runs, compilers, SHA-256 digests, numerical parity, and counterexamples.
4. **Three Exploration Modes (`probe` / `paper` / `program`):**
   - `probe` (Idea Probe): zero-config, fast native PyTorch falsification in `reference/`.
   - `paper` (Focused Paper): single-manuscript `.agents/brief.json` + `paper_gate.json`, zero glue scripts.
   - `program` (Open-Ended Scientific Exploration): `program.json` proposition tree, dual-lens `focus` switching, knowledge base, and lightweight shared collaborator repos.
5. **Lead Intellectual Independence (Anti-Sycophancy):** The Lead model forms a mechanism-grounded prior before inspecting worker outputs or answering leading prompts; workers return raw verifiable evidence (not verdicts); conflicts with proven dead ends or simplicity are pushed back candidly with counterexamples before any logged override in `DECISIONS.md`.
6. **Three-Layer Anti-Amnesia:** (1) optional advisory `experience_brief.py` injecting mode, current focus, and recent decisions; (2) **[Obelisk](https://github.com/tommy0103/obelisk)** for cross-session and post-compaction raw evidence recall; (3) `handoff.py` SHA-256 stage bundles when freezing milestones.
7. **Companion Ecosystem:** RDS v7 orchestrates research direction and deterministic gates; **[Proofline](https://github.com/kongtou20070406/proofline)** supplies zero-GPU Roofline + Z3 + Lean 4 latency floors between `reference/` and `engine/`; **[Obelisk](https://github.com/tommy0103/obelisk)** supplies cross-session memory recall.

---

## 2. Research Manifesto

1. **North Star & Proposition Ladder:** Work in falsifiable scientific propositions, not blind parameter sweeps. Trigger a jump review when two consecutive main-line runs miss their bar.
2. **Code Understandability & Simplicity:** Keep core models in minimal native PyTorch under `reference/` (1:1 with paper equations, checked by AST purity). Isolate device-specific fused kernels (`Triton`/`CUDA`/`torch.compile`) in `engine/` behind forward+backward parity tests—only after `proofline` confirms meaningful headroom.
3. **Theory Understandability & Simplicity:** Main-text theorems start with a 2-sentence intuition, use `<=5` active symbols and `<=5` proof steps, and bind `code_ref` / `code_symbols` to `reference/`.
4. **What-not-How Innovation:** Frame claims around the bottleneck solved and invariant uncovered, matched against same-recipe baselines and zero-tuning cross-domain checks.

---

## 3. CLI Reference (For Maintainers & CI)

```bash
# 1. Initialize skills or scaffold a new project
python scripts/bootstrap.py --apply
python scripts/bootstrap.py --init-project <dir> --name <name> --mode paper --vault <notes_dir>

# 2. Proposition tree, prior-art gate, knowledge base & polish check
python skills/research-gate/scripts/program.py check  path/to/program.json
python skills/research-gate/scripts/program_board.py path/to/program.json --out board.md --knowledge <notes_dir>/data
python skills/research-gate/scripts/prior_art_check.py cards/001_example.md
python skills/research-gate/scripts/knowledge.py search <notes_dir>/data/*.jsonl -q "query"
python skills/research-gate/scripts/polish_check.py ledger/experiments.jsonl

# 3. Worker dispatch, AST purity check & handoff bundle
python scripts/dispatch_gpt.py --repo . --task-id T1 --allow src/x.py --protect tests \
    --prompt-file task.md --pytest-target tests --ledger ledger/worker_experience.jsonl
python skills/research-gate/scripts/accept_worker_deliverable.py --repo-root . --code-quality src/reference
python skills/research-gate/scripts/handoff.py freeze PLAN.json --out PLAN.frozen.json

# 4. Manuscript tables, layout/PDF gate, theory gate & freeze review
python skills/paper-write/scripts/verify_manuscript_formal.py --results-to-tex RESULTS.json --out-tex tables/auto_t1.tex
python skills/paper-write/scripts/paper_gate.py path/to/paper --fix --compile --layout-check --render-pages
python skills/paper-write/scripts/theory_gate.py path/to/paper --no-sorry
python skills/paper-write/scripts/freeze_review.py --draft-dir draft --digest <sha256> --out-dir freeze/ --prior-audit audit.json
```
