# Source of the writing exemplars

These cards and [exemplar-style-analysis.md](exemplar-style-analysis.md) are adapted from [CCFA-Skills](https://github.com/mikubaka88/CCFA-Skills) (`ccf-paper-writer/references/`, commit `4c17b3ec21c9`, 2026-11-14), copyright 2026 Chaoyue Li, MIT License (see [LICENSE](LICENSE)). [index.md](index.md) was rewritten for paper-write.

## What was kept

13 of the 27 upstream cards: the ones that serve ML/AI papers (ICML, NeurIPS, ICLR and the neighbouring CVPR, ACL, ECCV, AAAI theory examples listed in the index). Card text is unchanged except:

- Paths to upstream local full texts and PDFs now point to the public source on the card (arXiv, PMLR, NeurIPS, CVF, ACL Anthology, AAAI OJS), plus a `Local original` line for the copy in `papers/`.
- References to upstream-only files (custom format, shared source registry, Common preferences) were removed or replaced with `paper_search` and the paper-write Tier 1 checks.

## Original papers

The 20 papers behind these cards are listed in [papers.json](papers.json). `scripts/fetch_exemplar_papers.py` downloads them from the official sources rather than from the upstream repository, rejects a PDF whose first two pages miss the title, extracts page-marked text with PyMuPDF and records sha256 hashes in `papers/manifest.lock.json`. The papers are third-party works that the upstream MIT licence does not cover, so `papers/` stays out of git; each machine fetches its own copy.

## What was not taken
- 14 cards outside the ML/AI scope of paper-write: `llava-4d` (the upstream author's custom exemplar), `cvpr-2025-vggt-best-paper` (award note duplicated by `vggt`), `cvpr-2023-best-papers`, `cvpr-2024-best-papers`, `iccv-2023-passive-ultra-wideband`, `iccv-2025-brickgpt`, `eccv-2024-minimalist-vision`, `acmmm-2024-speaker-to-dubber`, `acmmm-2025-aff3dfunc`, `aaai-2024-gxvaes`, `sigmod-2024-polardb-mp`, `usenix-security-2024-inspectre-gadget`, `popl-2024-megalibm`, `chi-2024-constrained-highlighting`. Fetch one from the commit above if a paper needs it.
