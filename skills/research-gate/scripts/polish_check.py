"""Detect polishing: a run of experiments that tunes one method instead of advancing the question (0 LLM tokens).

    python polish_check.py LEDGER.jsonl [--min-run 4] [--band 0.03] [--json]

Reads an experiment ledger (one JSON object per line, oldest first) and looks at the most recent
unbroken run of closed experiments that did not pass. It flags the run when it has at least
``--min-run`` entries and they share a method family, and reports which signals hold:

- ``no_pass``     every entry in the run ended FAIL / PARTIAL / HOLD;
- ``same_family`` one method name across the run (title prefix before a version tag such as " v7");
- ``same_bench``  some capitalised name (a benchmark such as Push-T) appears in every headline;
- ``narrow_band`` the best score of each headline (largest match of ``--metric-regex``, default: any
                  decimal) stays within ``--band`` (small deltas);
- ``tuning_only`` every entry with a ``change_kind`` field is "tune" or "add" (no new mechanism, nothing removed).

A flagged run is a prompt to stop and re-check the plan against the north star, not a verdict.
Field names are configurable for other ledgers (``--verdict-key``, ``--title-key``, ``--metric-key``).
Exit code 0 always; ``--fail-on-flag`` exits 1 when a run is flagged (for CI or a hook).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True
PASS = {"PASS", "SUPPORTED"}
VERSION_RE = re.compile(r"^(.*?)(?:\s+v\d+\b|\s*\(|\s+—|\s+-\s)")
NUM_RE = re.compile(r"(?<![\w.])(\d+\.\d+)")
BENCH_RE = re.compile(r"(?<![\w-])([A-Z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*)(?![\w=])")


def family(title: str) -> str:
    m = VERSION_RE.match(title or "")
    return (m.group(1) if m else (title or "").split(" ")[0]).strip().lower()


def check(entries: list[dict], min_run: int = 4, band: float = 0.03, verdict_key: str = "verdict",
          title_key: str = "title", metric_key: str = "headline_metric", metric_regex: str | None = None) -> dict:
    run: list[dict] = []
    for e in reversed(entries):
        if str(e.get(verdict_key, "")).upper() in PASS:
            break
        run.append(e)
    run.reverse()
    fams = [family(str(e.get(title_key, ""))) for e in run]
    # keep the trailing part of the run that shares the latest family
    if fams:
        last = fams[-1]
        k = len(fams)
        while k > 0 and fams[k - 1] == last:
            k -= 1
        run, fams = run[k:], fams[k:]
    out = {"flagged": False, "run": [str(e.get("card", e.get("id", "?"))) for e in run], "signals": {}}
    if len(run) < min_run:
        return out
    heads = [str(e.get(metric_key, "")) for e in run]
    rx = re.compile(metric_regex) if metric_regex else NUM_RE
    nums = [max(float(x) for x in found) for h in heads if (found := rx.findall(h))]
    common = set.intersection(*[set(BENCH_RE.findall(h)) for h in heads]) if heads else set()
    kinds = [str(e["change_kind"]).lower() for e in run if e.get("change_kind")]
    out["signals"] = {
        "no_pass": True,
        "same_family": len(set(fams)) == 1,
        "same_bench": bool(common),
        "narrow_band": len(nums) >= min_run and max(nums) - min(nums) <= band,
        "tuning_only": bool(kinds) and all(k in ("tune", "add") for k in kinds),
    }
    out["family"] = fams[-1]
    out["benchmarks"] = sorted(common)
    out["flagged"] = out["signals"]["same_family"]
    if nums:
        out["metric_range"] = [min(nums), max(nums)]
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ledger", type=Path)
    ap.add_argument("--min-run", type=int, default=4)
    ap.add_argument("--band", type=float, default=0.03)
    ap.add_argument("--verdict-key", default="verdict")
    ap.add_argument("--title-key", default="title")
    ap.add_argument("--metric-key", default="headline_metric")
    ap.add_argument("--metric-regex", default=None, help="one capture group; the largest match per entry is its score")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--fail-on-flag", action="store_true")
    a = ap.parse_args(argv)
    entries = [json.loads(l) for l in a.ledger.read_text(encoding="utf-8").splitlines() if l.strip()]
    res = check(entries, a.min_run, a.band, a.verdict_key, a.title_key, a.metric_key, a.metric_regex)
    if a.json:
        print(json.dumps(res, ensure_ascii=False))
    elif res["flagged"]:
        held = [k for k, v in res["signals"].items() if v]
        print(f"POLISHING {len(res['run'])} entries ({res['run'][0]}–{res['run'][-1]}) of '{res['family']}': {', '.join(held)}")
        print("  Re-check against the north star before the next run: does the next step test a core criterion,")
        print("  keep the method minimal, and say how the result would carry to another setting?")
    else:
        print(f"OK no polishing run (latest non-pass run: {len(res['run'])} entries)")
    return 1 if (a.fail_on_flag and res["flagged"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
