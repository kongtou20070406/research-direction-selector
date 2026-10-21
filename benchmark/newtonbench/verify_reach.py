"""Verify benchmark/newtonbench/results/source-reach-20261021.json against protocol."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULT_PATH = ROOT / 'benchmark' / 'newtonbench' / 'results' / 'source-reach-20261021.json'


def verify(data_path=RESULT_PATH):
    if not data_path.exists():
        print(f"Error: {data_path} does not exist.")
        return False

    with open(data_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    rows = data.get('rows', [])
    by_eq = {}
    for r in rows:
        by_eq.setdefault(r['equation'], {})[r['variant']] = r

    print("================================================================================")
    print("SOURCE REACH PREREGISTERED VERIFICATION REPORT")
    print("================================================================================")
    print(f"Total equations recorded: {len(by_eq)}")
    print(f"Total rows recorded: {len(rows)}")

    recovered = []
    failed_rescue = []
    undetermined = []
    criterion_checks = []

    print("\n--- 10-Equation Table ---")
    print(f"{'Equation':<20} | {'no-source status':<18} | {'no-s exact':<10} | {'truth status':<18} | {'truth exact':<11} | {'Rescued?'}")
    print("-" * 100)

    for eq, variants in by_eq.items():
        ns = variants.get('no-source', {})
        ts = variants.get('truth-source', {})

        ns_status = ns.get('status') or ns.get('error', 'UNKNOWN')
        ns_exact = str(ns.get('claim_exact', False))
        ts_status = ts.get('status') or ts.get('error', 'UNKNOWN')
        ts_exact = str(ts.get('claim_exact', False))

        is_negative_control = (eq == 'feynman-I.27.6')

        # Check 6 criteria for truth-source
        c1 = (ts.get('status') == 'GOAL_IDENTIFIED')
        c2 = (ts.get('claim') == 'CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS')
        c3 = (ts.get('counterexamples') == 0)
        c4 = (ts.get('experiments') is not None and ts.get('experiments') <= 8)
        c_check = ts.get('certification_check') or {}
        c5 = (ts.get('false_certification') is False and c_check.get('points_checked') == 32)
        c6 = (str(c_check.get('max_abs_error')) == '0')

        all_6_pass = all([c1, c2, c3, c4, c5, c6])

        if is_negative_control:
            rescued_str = "CONTROL (PASS)" if (ns.get('claim_exact') and ts.get('claim_exact')) else "CONTROL (FAIL)"
        elif ns.get('claim_exact'):
            # Both arms reach the law, so the source adds nothing here and this cell is
            # not evidence about the source either way.
            rescued_str = "REACHED ANYWAY"
        elif not ts.get('claim_exact') or not all_6_pass:
            rescued_str = "NO"
            failed_rescue.append(eq)
        elif ns.get('status') == 'SEARCH_EXHAUSTED':
            rescued_str = "YES"
            recovered.append(eq)
        else:
            # The control arm did not finish, so this run does not show that the search
            # cannot reach the law.  A timeout is UNKNOWN, not a rescue.
            rescued_str = "UNKNOWN"
            undetermined.append(eq)

        print(f"{eq:<20} | {ns_status:<18} | {ns_exact:<10} | {ts_status:<18} | {ts_exact:<11} | {rescued_str}")

        criterion_checks.append({
            'equation': eq,
            'is_control': is_negative_control,
            'c1_status': c1,
            'c2_claim': c2,
            'c3_counterexamples': c3,
            'c4_experiments': c4,
            'c5_false_cert': c5,
            'c6_max_abs_err': c6,
            'all_pass': all_6_pass,
        })

    print("-" * 100)
    target_total = len(recovered) + len(undetermined) + len(failed_rescue)
    print(f"\nRescued count: {len(recovered)} / {target_total} target equations")
    print(f"Rescued equations: {recovered}")
    if undetermined:
        print(f"Undetermined (the control arm timed out, so reachability is unproven): {undetermined}")
    if failed_rescue:
        print(f"Failed rescue: {failed_rescue}")

    # Check Safety items
    print("\n--- Safety Item Checks ---")
    # Safety 1: Negative control I.27.6 matches
    i276 = by_eq.get('feynman-I.27.6', {})
    s1_pass = False
    if 'no-source' in i276 and 'truth-source' in i276:
        ns_exact = i276['no-source'].get('claim_exact')
        ts_exact = i276['truth-source'].get('claim_exact')
        s1_pass = (ns_exact == ts_exact == True)
    print(f"Safety 1 (I.27.6 negative control both arms match): {'PASS' if s1_pass else 'FAIL'}")

    # Safety 2: no-source arm claim_exact == true indicates naturally reachable, excluded from rescue
    natural_solves = [eq for eq, v in by_eq.items() if eq != 'feynman-I.27.6' and v.get('no-source', {}).get('claim_exact')]
    print(f"Safety 2 (no-source naturally solved, excluded from rescue): {natural_solves if natural_solves else 'None'}")

    # Safety 3: zero false_certification across all rows
    false_certs = [r for r in rows if r.get('false_certification') is True]
    s3_pass = (len(false_certs) == 0)
    print(f"Safety 3 (zero false_certification in any row): {'PASS' if s3_pass else f'FAIL: {false_certs}'}")

    # Summary
    all_safety = s1_pass and s3_pass
    print("\n--- Protocol Verdict ---")
    if all_safety and len(recovered) > 0:
        verdict = (
            f"VERDICT: the truth source rescued {len(recovered)} of {target_total} target "
            f"equations under B=8, with {len(undetermined)} undetermined because the control "
            f"arm did not finish ({', '.join(undetermined) or 'none'}) and "
            f"{len(failed_rescue)} failed ({', '.join(failed_rescue) or 'none'}). "
            f"False certifications across all {len(rows)} cells: {len(false_certs)}."
        )
        print(verdict)
    else:
        print(f"VERDICT: FAILED. Safety passed: {all_safety}, Recovered: {len(recovered)}")

    return all_safety


if __name__ == '__main__':
    verify(Path(sys.argv[1]) if len(sys.argv) > 1 else RESULT_PATH)
