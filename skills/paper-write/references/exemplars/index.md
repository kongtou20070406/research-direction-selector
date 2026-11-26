# Exemplar Index

Use this index when a concrete writing question needs a model passage: defining what a model computes, explaining a bottleneck, narrating a theorem, or interpreting a comparison. Load one matching card, then read the original passage it points to. Do not load every card, and do not rank writing quality by venue or award.

The cards come from [CCFA-Skills](SOURCE.md). Each card separates **source observations** (what the paper actually does, with a location) from **adaptation advice** (what may transfer).

Each card's `Local original` line names the paper's text in `papers/`: page-marked text extracted from the official PDF, with the PDF beside it. Search the text for the cited section, then check the PDF when equations, figures or column order matter. `papers/` is git-ignored. If it is missing, run `python scripts/fetch_exemplar_papers.py` from the paper-write folder; the script downloads every paper in [papers.json](papers.json) from its official source and checks the title. If an original still cannot be read, say which observation is unverified and do not claim source-based learning.

## Selection rule

- Select by scientific object and writing function, not by venue prestige. Venue is a context clue only.
- Use as few passages as answer the writing question. Add a second card only when it answers a different question.
- Without a matching card, search once per writing question with `paper_search` for a well-written passage, not once per paragraph.
- Transfer sentence relationships and the order of explanation. Never transfer a source's claims, mechanisms, numbers, distinctive sentences or claim strength. Conventional phrasing and established terms may be reused.
- Follow [exemplar-style-analysis.md](exemplar-style-analysis.md) for how to read a passage and compose from your own material.

## Cards

| Card | Venue | Use when |
| --- | --- | --- |
| [transformer-writing-style](cards/transformer-writing-style.md) | NeurIPS 2017 | Explaining a computational dependency, structural bottleneck, or change of modeling primitive |
| [vggt](cards/vggt.md) | CVPR 2025 | Defining what a model predicts; direct prediction versus iterative optimization or refinement |
| [aaai-2025-every-bit-helps](cards/aaai-2025-every-bit-helps.md) | AAAI 2025 | Theory papers: making a bound consequential through prior results and lower bounds |
| [icml-2025-collabllm](cards/icml-2025-collabllm.md) | ICML 2025 | Objectives with a time horizon; separating model-judged results from human-study results |
| [icml-2024-videopoet](cards/icml-2024-videopoet.md) | ICML 2024 | Defining a model by its conditioning signals; pretraining versus task adaptation |
| [neurips-2024-var](cards/neurips-2024-var.md) | NeurIPS 2024 | Introducing a new prediction unit or generation paradigm; contrast figures |
| [neurips-2025-1000-layer-ssl-rl](cards/neurips-2025-1000-layer-ssl-rl.md) | NeurIPS 2025 | Scaling studies, goal-conditioned RL, qualitative capability changes |
| [acl-2024-mission-impossible](cards/acl-2024-mission-impossible.md) | ACL 2024 | Testing a published claim with controlled synthetic tasks |
| [acl-2025-minilongbench](cards/acl-2025-minilongbench.md) | ACL 2025 | Evaluation cost; pairing a cheaper protocol with ranking agreement |
| [eccv-2022-partial-distance-correlation](cards/eccv-2022-partial-distance-correlation.md) | ECCV 2022 | A statistical tool used as a diagnostic or regularizer across applications |

### Award discovery cards

These list award papers and give generic advice that the award source does not establish. Read an actual passage before attributing a writing move to any of them.

| Card | Use when |
| --- | --- |
| [iclr-2026-outstanding-papers](cards/iclr-2026-outstanding-papers.md) | Theory of transformers; evaluation papers built on a deployment mismatch |
| [iclr-2025-outstanding-papers](cards/iclr-2025-outstanding-papers.md) | Safety alignment, finetuning dynamics, model editing |
| [iclr-2024-outstanding-papers](cards/iclr-2024-outstanding-papers.md) | Diffusion generalization, interactive world simulators, long-sequence models, ViT analysis |

## Example selections

- Recurrent or iterative computation (why a loop is needed, what each step depends on): `transformer-writing-style`.
- A model that replaces an optimization or sampling loop with direct prediction: `vggt`.
- A theorem whose significance depends on earlier bounds: `aaai-2025-every-bit-helps`; for making a conceptual measure intuitive before the formalism, `iclr-2026-outstanding-papers` plus an inspected passage.
- A controlled probe that tests whether a capability exists: `acl-2024-mission-impossible`.

## Adding a card

Adapted from CCFA-Skills `ccf-paper-to-exemplar`.

1. Add the paper (id, card, title, official PDF url) to [papers.json](papers.json) and run `scripts/fetch_exemplar_papers.py --only <id>`. A PDF whose first two pages miss the title is rejected.
2. Read the full text in coherent sections, not only the abstract. Check the PDF where equations, figures or column order matter.
3. Write `cards/<card>.md` under the existing headings: Story Pattern, Abstract Moves, Introduction Moves, Method Moves, Evidence Moves, Reusable Techniques, Do-Not-Copy Boundary. Anchor each source observation to a page, section or paragraph, and label adaptation advice separately. Awards say nothing about writing quality.
4. Verify every statement about the source (paragraph counts, organization, quoted phrases) against the text, then add one row to the Cards table. The test suite fails if a card has no original or an original has no card.

## Output

For an explicit exemplar-analysis request, name the card, the source location and the transferable move. For ordinary drafting, apply the move silently and return only the requested prose, then run the Tier 1 checks in [SKILL.md](../../SKILL.md).
