#!/usr/bin/env python3
"""Query the experiment database. Enforces a hard per-workspace budget.

Usage: python -B scripts/query_db.py --arm A --batch 2
Each invocation costs 1 credit (server-side ledger) and takes the configured
server-side latency. When credits are exhausted the runner exits nonzero.
Every successful query is appended to scripts/query_log.jsonl.
"""
import argparse, json, os, sqlite3, time

BASE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(BASE, "credits.json")
QUERY_LOG = os.path.join(BASE, "query_log.jsonl")
QUOTA = 6          # server-side total credits
COST_SECONDS = 20  # per-query server-side latency

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--arm", choices=["A", "B"], required=True)
    p.add_argument("--batch", type=int, required=True)
    a = p.parse_args()
    st = json.load(open(LEDGER)) if os.path.exists(LEDGER) else {"used": 0}
    if st["used"] >= QUOTA:
        print(json.dumps({"error": "credit exhausted", "used": st["used"], "quota": QUOTA}))
        raise SystemExit(2)
    st["used"] += 1
    json.dump(st, open(LEDGER, "w"))
    time.sleep(COST_SECONDS)
    con = sqlite3.connect(os.path.join(BASE, "..", "data.db"))
    row = con.execute("SELECT AVG(score), COUNT(*) FROM scores WHERE arm=? AND batch=?",
                      (a.arm, a.batch)).fetchone()
    con.close()
    with open(QUERY_LOG, "a", encoding="utf-8") as log:
        log.write(json.dumps({"arm": a.arm, "batch": a.batch, "ts": round(time.time(), 3)}) + "\n")
    print(json.dumps({"arm": a.arm, "batch": a.batch,
                      "mean": round(row[0], 6), "n": row[1],
                      "credits_remaining": QUOTA - st["used"]}))

if __name__ == "__main__":
    main()
